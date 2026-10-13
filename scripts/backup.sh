#!/usr/bin/env bash
# Encrypted backup of your private files to a git repo you own (e.g. a private GitHub repo).
#
#   bash scripts/backup.sh --setup   once: keys, what to back up, where to, daily schedule
#   bash scripts/backup.sh           make a backup now (also runs every day by itself)
#   bash scripts/restore.sh --help   get files back
#
# What: by default your private plugins (plugins/, with their own git history), your settings
# (config/, including secrets such as email.env) and the agent's workspace. Change the list
# with --setup or BACKUP_PATHS in the settings file. Everything is packed into one file and
# encrypted with age (https://age-encryption.org) to:
#   - a recovery key: a small text file you keep OFFLINE (USB stick, paper), and optionally
#   - a YubiKey or other FIDO2 security key (needs age-plugin-fido2-hmac),
# so either one opens it. Only the encrypted file leaves your computer: the git host can't read it.
# Making a backup needs only the PUBLIC keys, so the daily run works with the key unplugged.
#
# Settings: ~/.config/ai-kit-backup/ (never inside a repo)
#   recipients.txt      public keys the backup is encrypted to
#   recovery-key.txt    recovery SECRET key: copy it offline, then you may delete it here
#   yubikey.txt         security-key identity (useless without the physical key)
#   backup.env          BACKUP_REPO, BACKUP_KEEP, BACKUP_PATHS, BACKUP_EXCLUDE, BACKUP_TIME
set -euo pipefail

SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
KIT="$(cd "$SCRIPTS/.." && pwd)"
CONF="${BACKUP_CONF:-$HOME/.config/ai-kit-backup}"
REPO_DIR="${BACKUP_REPO_DIR:-$HOME/.local/share/ai-kit-backup/repo}"
WORKSPACE_DIR="${WORKSPACE_DIR:-$HOME/agent-workspace}"
SSH_KEY="$HOME/.ssh/ai_kit_backup"
SSH_HOST="github-ai-kit-backup"
MAX_MB="${BACKUP_MAX_MB:-95}"   # GitHub refuses files over 100 MB
stage=""
# age plugins are often installed in ~/.local/bin, which the daily (systemd) run doesn't have
export PATH="$HOME/.local/bin:$HOME/go/bin:$PATH"

say()  { printf '  %s\n' "$*"; }
ok()   { printf '  \033[1;32m[OK]\033[0m   %s\n' "$*"; }
warn() { printf '  \033[1;33m[!]\033[0m    %s\n' "$*"; }
fail() { printf '  \033[1;31m[FAIL]\033[0m %s\n' "$*" >&2; }
ask()  { local a; read -r -p "  $1 " a </dev/tty; printf '%s' "$a"; }

# A path relative to your home folder (backups store paths that way, so they restore on a
# new computer even with a different user name).
home_rel() {
  local p="$1"
  case "$p" in
    "$HOME"/*) printf '%s' "${p#"$HOME"/}" ;;
    /*) return 1 ;;
    *) printf '%s' "$p" ;;
  esac
}

default_paths() {
  local out=() p
  for p in "$KIT/plugins" "$KIT/config" "$WORKSPACE_DIR"; do
    [ -e "$p" ] && out+=("$(home_rel "$p" || true)")
  done
  printf '%s ' "${out[@]}"
}

save_env() {
  {
    echo "# Encrypted backup settings (scripts/backup.sh). Paths are relative to your home folder."
    printf 'BACKUP_REPO=%q\n' "$BACKUP_REPO"
    printf 'BACKUP_KEEP=%q\n' "${BACKUP_KEEP:-30}"
    printf 'BACKUP_PATHS=%q\n' "$BACKUP_PATHS"
    printf 'BACKUP_EXCLUDE=%q\n' "${BACKUP_EXCLUDE:-}"
    printf 'BACKUP_TIME=%q\n' "${BACKUP_TIME:-07:15}"
  } > "$CONF/backup.env"
  chmod 600 "$CONF/backup.env"
}

# ---------------------------------------------------------------- setup ---
setup() {
  mkdir -p "$CONF" && chmod 700 "$CONF"
  echo "=== Encrypted backup setup ==="
  command -v age >/dev/null || { fail "age is not installed. Run: sudo apt install age"; exit 1; }
  BACKUP_REPO="" BACKUP_PATHS=""
  # shellcheck source=/dev/null
  [ -f "$CONF/backup.env" ] && source "$CONF/backup.env"

  # 1. keys -------------------------------------------------------------
  echo; echo "1) Keys"
  if [ ! -f "$CONF/recovery-key.txt" ] && [ ! -f "$CONF/recovery-public.txt" ]; then
    (umask 077 && age-keygen -o "$CONF/recovery-key.txt" 2>/dev/null)
    ok "Made a recovery key: $CONF/recovery-key.txt"
  fi
  local r y=""
  r=$(grep -ho 'age1[0-9a-z]*' "$CONF/recovery-key.txt" "$CONF/recovery-public.txt" 2>/dev/null | head -1 || true)
  [ -n "$r" ] || { fail "Could not read the recovery key's public part."; exit 1; }
  echo "# public key: $r" > "$CONF/recovery-public.txt"

  if [ ! -f "$CONF/yubikey.txt" ]; then
    if command -v age-plugin-fido2-hmac >/dev/null; then
      if [[ ! "$(ask "Also lock backups to a YubiKey / FIDO2 security key? [Y/n]")" =~ ^[Nn] ]]; then
        say "Plug the key in. Answer 'y' to the PIN question (safer) and 'y' to 'separate identity'"
        say "(then backups can be made while the key is unplugged)."
        age-plugin-fido2-hmac -g > "$CONF/yubikey.txt" </dev/tty
        chmod 600 "$CONF/yubikey.txt"
      fi
    else
      say "Optional: to also open backups with a YubiKey, install age-plugin-fido2-hmac"
      say "(see docs/GUIDE.md, 'Encrypted backup') and run --setup again."
    fi
  fi
  [ -f "$CONF/yubikey.txt" ] && y=$(grep -o 'age1[0-9a-z-]*' "$CONF/yubikey.txt" | head -1 || true)
  {
    [ -n "$y" ] && printf '# security key (fido2-hmac)\n%s\n' "$y"
    printf '# recovery key (keep the secret part offline)\n%s\n' "$r"
  } > "$CONF/recipients.txt"
  if [ -n "$y" ]; then ok "Backups open with your security key OR your recovery key."
  else ok "Backups open with your recovery key."; fi
  if [ -f "$CONF/recovery-key.txt" ]; then
    warn "Copy $CONF/recovery-key.txt${y:+ and yubikey.txt} to a USB stick (or print it)"
    say "and keep it safe. Without it (or the security key) the backups can never be opened."
    say "Then you may delete recovery-key.txt here: backups only need its public part."
  fi

  # 2. what ------------------------------------------------------------
  echo; echo "2) What to back up (folders in your home folder)"
  local p list="${BACKUP_PATHS:-$(default_paths)}"
  for p in $list; do
    if [ -e "$HOME/$p" ]; then say "$(du -sh "$HOME/$p" 2>/dev/null | cut -f1)	~/$p"; else say "(missing)	~/$p"; fi
  done
  local answer
  answer=$(ask "Press Enter to keep this list, or type your own (space-separated):")
  if [ -n "$answer" ]; then
    list=""
    for p in $answer; do
      p="${p/#\~\//$HOME/}"
      list+="$(home_rel "$p" || { fail "$p is outside your home folder"; exit 1; }) "
    done
  fi
  BACKUP_PATHS="${list% }"

  # 3. where -----------------------------------------------------------
  echo; echo "3) Where to keep the encrypted backups"
  if [ -n "$BACKUP_REPO" ] && git ls-remote "$BACKUP_REPO" >/dev/null 2>&1; then
    ok "Using $BACKUP_REPO (access works)."
  else
    say "Create an EMPTY private repo first (on github.com: New repository, Private, no README)."
    local gh_user repo_name
    gh_user=$(ask "Your GitHub user name (or a full git URL for another host):")
    if [[ "$gh_user" == *:* || "$gh_user" == /* ]]; then
      BACKUP_REPO="$gh_user"
    else
      repo_name=$(ask "Name of the private repo [dhoon-backup]:")
      repo_name=${repo_name:-dhoon-backup}
      BACKUP_REPO="git@$SSH_HOST:$gh_user/$repo_name.git"
      if [ ! -f "$SSH_KEY" ]; then
        mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
        ssh-keygen -q -t ed25519 -N "" -C "dhoon-backup@$(hostname)" -f "$SSH_KEY"
      fi
      if ! grep -q "Host $SSH_HOST" "$HOME/.ssh/config" 2>/dev/null; then
        cat >> "$HOME/.ssh/config" <<EOF

# Encrypted backups: a key that can only push to the backup repo. Port 443 works on
# networks that block SSH's usual port 22.
Host $SSH_HOST
  HostName ssh.github.com
  Port 443
  User git
  IdentityFile $SSH_KEY
  IdentitiesOnly yes
EOF
        chmod 600 "$HOME/.ssh/config"
      fi
      ssh-keygen -F "[ssh.github.com]:443" >/dev/null 2>&1 \
        || ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20 -T "$SSH_HOST" >/dev/null 2>&1 || true
      echo
      echo "  In your browser: the repo -> Settings -> Deploy keys -> Add deploy key"
      echo "  Title: backup   Key: the line below   Tick 'Allow write access'   Add key"
      echo
      cat "$SSH_KEY.pub"
      echo
      ask "Press Enter when the deploy key is added..." >/dev/null
    fi
    local said
    if said=$(git ls-remote "$BACKUP_REPO" 2>&1); then
      ok "The repo accepts this computer."
    else
      fail "Can't reach $BACKUP_REPO yet. What git said:"
      printf '         %s\n' "$said" >&2
      save_env
      fail "Fix the key or URL, then run: bash $0 --setup"
      exit 1
    fi
  fi
  save_env

  # 4. when ------------------------------------------------------------
  echo; echo "4) Daily backup at ${BACKUP_TIME:-07:15}"
  if command -v systemctl >/dev/null && sudo -v 2>/dev/null; then
    sudo tee /etc/systemd/system/ai-backup.service >/dev/null <<UNIT
[Unit]
Description=Encrypted backup of private files (scripts/backup.sh)
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=$USER
Environment=WORKSPACE_DIR=$WORKSPACE_DIR
ExecStart=/bin/bash $KIT/scripts/backup.sh --if-configured
Nice=15
UNIT
    sudo tee /etc/systemd/system/ai-backup.timer >/dev/null <<UNIT
[Unit]
Description=Daily encrypted backup

[Timer]
OnCalendar=*-*-* ${BACKUP_TIME:-07:15}
Persistent=true

[Install]
WantedBy=timers.target
UNIT
    sudo systemctl daemon-reload
    sudo systemctl enable --now ai-backup.timer >/dev/null 2>&1
    ok "Scheduled (ai-backup.timer). Log: journalctl -u ai-backup"
  else
    warn "Could not schedule it (no sudo). Run 'bash scripts/backup.sh' yourself now and then."
  fi

  echo; echo "Making the first backup..."
  backup
  echo
  echo "Check it opens:  bash scripts/restore.sh --test"
}

# ---------------------------------------------------------------- backup ---
backup() {
  if [ ! -f "$CONF/recipients.txt" ] || [ ! -f "$CONF/backup.env" ]; then
    [ "${1:-}" = "--if-configured" ] && exit 0
    fail "Backups are not set up yet. Run: bash $0 --setup"
    exit 1
  fi
  BACKUP_PATHS="" BACKUP_EXCLUDE="" BACKUP_KEEP=30
  # shellcheck source=/dev/null
  source "$CONF/backup.env"
  local keep="${BACKUP_KEEP:-30}" stamp out p items=() excludes=()
  stamp=$(date +%Y-%m-%d_%H%M%S)
  for p in ${BACKUP_PATHS:-$(default_paths)}; do
    if [ -e "$HOME/$p" ]; then items+=("$p"); else warn "Skipping ~/$p (not found)"; fi
  done
  [ ${#items[@]} -gt 0 ] || { fail "Nothing to back up (check BACKUP_PATHS in $CONF/backup.env)"; exit 1; }
  for p in __pycache__ '*.pyc' .venv venv node_modules .pytest_cache '*.before-restore-*' ${BACKUP_EXCLUDE:-}; do
    excludes+=("--exclude=$p")
  done

  stage=$(mktemp -d)   # global, so the EXIT trap can still see it
  trap 'rm -rf "$stage"' EXIT
  mkdir -p "$stage/backup"
  printf '%s\n' "${items[@]}" > "$stage/backup/paths.txt"
  tar -cf "$stage/backup/files.tar" -C "$HOME" "${excludes[@]}" "${items[@]}"
  {
    echo "Backup $stamp from $(hostname)"
    echo "Restore with: bash scripts/restore.sh   (paths are relative to the home folder)"
    for p in "${items[@]}"; do echo "  ~/$p"; done
    echo
    tar -tf "$stage/backup/files.tar" | head -1000
  } > "$stage/backup/MANIFEST.txt"
  out="backup-$stamp.tar.gz.age"
  tar -czf - -C "$stage" backup | age -R "$CONF/recipients.txt" -o "$stage/$out"
  local mb
  mb=$(( $(stat -c %s "$stage/$out") / 1024 / 1024 ))
  if [ "$mb" -ge "$MAX_MB" ]; then
    fail "The backup is ${mb} MB; git hosts refuse files this big (limit ${MAX_MB} MB)."
    fail "Leave big folders out: BACKUP_PATHS or BACKUP_EXCLUDE in $CONF/backup.env"
    exit 1
  fi

  # the backup repo: ONE commit holding the newest $keep backups, so it never grows forever
  if [ ! -d "$REPO_DIR/.git" ]; then
    mkdir -p "$(dirname "$REPO_DIR")"
    git clone -q "$BACKUP_REPO" "$REPO_DIR" 2>/dev/null || { mkdir -p "$REPO_DIR" && git -C "$REPO_DIR" init -q; }
    git -C "$REPO_DIR" remote get-url origin >/dev/null 2>&1 || git -C "$REPO_DIR" remote add origin "$BACKUP_REPO"
  elif git -C "$REPO_DIR" fetch -q origin 2>/dev/null && git -C "$REPO_DIR" rev-parse -q --verify origin/main >/dev/null; then
    git -C "$REPO_DIR" checkout -q -B main origin/main 2>/dev/null || true
  fi
  git -C "$REPO_DIR" remote set-url origin "$BACKUP_REPO"
  mkdir -p "$REPO_DIR/backups"
  cp "$stage/$out" "$REPO_DIR/backups/"
  if [ -f "$CONF/yubikey.txt" ]; then cp "$CONF/yubikey.txt" "$REPO_DIR/yubikey-identity.txt"; fi
  cp "$CONF/recipients.txt" "$REPO_DIR/recipients.txt"
  cat > "$REPO_DIR/README.md" <<'EOF'
# Encrypted backups

Each file in `backups/` is encrypted with age to a recovery key (kept offline) and, if one was
set up, a YubiKey / FIDO2 security key. Without one of them the files can't be read, not by the
git host either. `yubikey-identity.txt` is not a secret: it is useless without the physical key.

Restore with Dhoon (https://github.com/dhoon-ai/dhoon):

    bash scripts/restore.sh                        # newest backup, security key (touch it)
    bash scripts/restore.sh --key recovery-key.txt # with the recovery key instead
    bash scripts/restore.sh --apply                # also put the files back in place

By hand, with only `age` (plus age-plugin-fido2-hmac for the security key):

    age -d -i recovery-key.txt backups/<file>.age | tar xzf -     # or -i yubikey-identity.txt
    tar -xf backup/files.tar -C restored/          # paths are relative to the home folder
EOF
  # newest first by the date in the name; keep $keep
  find "$REPO_DIR/backups" -maxdepth 1 -name '*.age' \
    | awk '{ if (match($0, /[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]+/)) print substr($0, RSTART, RLENGTH) "\t" $0 }' \
    | sort -r | tail -n +$((keep + 1)) | cut -f2 | xargs -r rm -f
  (
    cd "$REPO_DIR"
    git checkout -q --orphan new-main
    git add -A
    git -c user.name="dhoon-backup" -c user.email="backup@localhost" commit -q -m "Encrypted backups (latest $stamp)"
    git branch -D main >/dev/null 2>&1 || true
    git branch -m main
    git push -q -f origin main
  )
  local count size
  count=$(find "$REPO_DIR/backups" -maxdepth 1 -name '*.age' | wc -l)
  size=$(du -h "$stage/$out" | cut -f1)
  echo "$stamp $out $size" > "$CONF/last-backup.txt"
  ok "Backup $out ($size, encrypted) pushed; $count kept."
}

case "${1:-}" in
  --setup) setup ;;
  --if-configured) backup --if-configured ;;
  ""|--now) backup ;;
  -h|--help) sed -n '2,22p' "$0" ;;
  *) echo "Usage: bash $0 [--setup | --help]"; exit 2 ;;
esac
