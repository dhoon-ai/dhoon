#!/usr/bin/env bash
# Get files back from an encrypted backup made by scripts/backup.sh.
#
#   bash scripts/restore.sh --test              does the newest backup open? (do it monthly)
#   bash scripts/restore.sh                     unpack the newest backup into ~/restore-<time>/
#   bash scripts/restore.sh --apply             put everything back in place
#   bash scripts/restore.sh --apply --only inglenook/plugins/NAME just one folder or file
#   bash scripts/restore.sh --list              list the backups and what's inside the newest
#
# Options:
#   --key FILE    open with this key file (your recovery-key.txt or yubikey.txt from the USB stick).
#                 Default: the security key (touch it when it blinks), else ask for --key.
#   --from URL    take the backups from this git repo (a new computer without backup settings).
#                 For a private GitHub repo: https://github.com/USER/REPO.git (asks you to log in).
#   FILE.age      a backup file you downloaded yourself.
# What is there now is moved aside to NAME.before-restore-<time>, never deleted.
set -euo pipefail

CONF="${BACKUP_CONF:-$HOME/.config/ai-kit-backup}"
export PATH="$HOME/.local/bin:$HOME/go/bin:$PATH"
REPO_DIR="${BACKUP_REPO_DIR:-$HOME/.local/share/ai-kit-backup/repo}"

ok()   { printf '  \033[1;32m[OK]\033[0m   %s\n' "$*"; }
warn() { printf '  \033[1;33m[!]\033[0m    %s\n' "$*"; }
fail() { printf '  \033[1;31m[FAIL]\033[0m %s\n' "$*" >&2; }

identity="" file="" from="" mode="extract" only=()
while [ $# -gt 0 ]; do
  case "$1" in
    --key|--recovery-key|--identity) identity="$2"; shift 2 ;;
    --from) from="$2"; shift 2 ;;
    --only) only+=("${2%/}"); mode="apply"; shift 2 ;;
    --test) mode="test"; shift ;;
    --apply) mode="apply"; shift ;;
    --list) mode="list"; shift ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    -*) fail "Unknown option $1 (see --help)"; exit 2 ;;
    *) file="$1"; shift ;;
  esac
done
for p in "${only[@]}"; do
  p="${p#"$HOME"/}"; p="${p#\~/}"
  case "/$p/" in */../*|//*) fail "--only takes a path inside your home folder, like inglenook/plugins/NAME"; exit 2 ;; esac
done

newest_first() {
  find "$1" -maxdepth 1 -name '*.age' 2>/dev/null \
    | awk '{ if (match($0, /[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]+/)) print substr($0, RSTART, RLENGTH) "\t" $0 }' \
    | sort -r | cut -f2
}

# ---- which backup ----
if [ -z "$file" ]; then
  if [ -n "$from" ]; then
    REPO_DIR=$(mktemp -d)/repo
    git clone -q --depth 1 "$from" "$REPO_DIR"
  elif [ -d "$REPO_DIR/.git" ]; then
    if git -C "$REPO_DIR" fetch -q origin 2>/dev/null; then git -C "$REPO_DIR" reset -q --hard origin/main; fi
  elif [ -f "$CONF/backup.env" ]; then
    # shellcheck source=/dev/null
    source "$CONF/backup.env"
    mkdir -p "$(dirname "$REPO_DIR")"
    git clone -q "$BACKUP_REPO" "$REPO_DIR"
  fi
  file=$(newest_first "$REPO_DIR/backups" | head -1 || true)
  [ -n "$file" ] || { fail "No backup found. Use --from URL, or give a downloaded FILE.age"; exit 1; }
  if [ "$mode" = "list" ]; then
    echo "Backups (newest first):"
    newest_first "$REPO_DIR/backups" | while read -r f; do printf '  %s  %s\n' "$(du -h "$f" | cut -f1)" "$(basename "$f")"; done
  fi
fi
[ -f "$file" ] || { fail "No such file: $file"; exit 1; }

# ---- which key ----
if [ -z "$identity" ]; then
  for c in "$CONF/yubikey.txt" "$(dirname "$(dirname "$file")")/yubikey-identity.txt"; do
    if [ -f "$c" ]; then identity="$c"; break; fi
  done
  [ -n "$identity" ] || { fail "No key found. Use --key /path/to/recovery-key.txt"; exit 1; }
fi
[ -f "$identity" ] || { fail "No such key file: $identity"; exit 1; }
if grep -q '^AGE-PLUGIN-FIDO2-HMAC-' "$identity"; then
  command -v age-plugin-fido2-hmac >/dev/null \
    || { fail "This key needs age-plugin-fido2-hmac (docs/GUIDE.md), or use --key recovery-key.txt"; exit 1; }
  echo "Opening $(basename "$file"): touch your security key when it blinks (and enter its PIN if asked)."
fi

# ---- open ----
out="$HOME/restore-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$out" && chmod 700 "$out"
if ! age -d -i "$identity" "$file" | tar -xzf - -C "$out"; then
  fail "Could not open the backup (wrong key, wrong PIN, or the key wasn't touched in time)."
  rm -rf "$out"
  exit 1
fi
B="$out/backup"
if [ -f "$B/files.tar" ]; then
  mkdir -p "$B/home"
  tar -xf "$B/files.tar" -C "$B/home"
  rm -f "$B/files.tar"
fi

case "$mode" in
  test)
    head -1 "$B/MANIFEST.txt"
    rm -rf "$out"
    ok "The backup opens correctly."
    exit 0 ;;
  list)
    echo; sed -n '1,/^$/p' "$B/MANIFEST.txt"
    rm -rf "$out"
    exit 0 ;;
  extract)
    ok "Unpacked into $B/home (paths as in your home folder)"
    sed -n '3,/^$/p' "$B/MANIFEST.txt"
    warn "These files are NOT encrypted. Delete $out when you're done."
    echo "To put them back in place: bash $0 --apply   (or --only <path> for one folder)"
    exit 0 ;;
esac

# ---- apply ----
[ -d "$B/home" ] || { fail "This backup has an older layout: copy files by hand from $B"; exit 1; }
if [ ${#only[@]} -eq 0 ]; then mapfile -t only < "$B/paths.txt"; fi
ts=$(date +%Y%m%d-%H%M%S)
for p in "${only[@]}"; do
  p="${p#"$HOME"/}"; p="${p#\~/}"
  [ -n "$p" ] || continue
  if [ ! -e "$B/home/$p" ]; then warn "$p is not in this backup, skipped"; continue; fi
  if [ -e "$HOME/$p" ]; then
    mv "$HOME/$p" "$HOME/$p.before-restore-$ts"
    echo "  moved the current ~/$p aside to ~/$p.before-restore-$ts"
  fi
  mkdir -p "$(dirname "$HOME/$p")"
  cp -a "$B/home/$p" "$HOME/$p"
  ok "Restored ~/$p"
done
rm -rf "$out"
echo
echo "Done. If you restored plugins or settings, restart the toolbox: sudo systemctl restart ai-toolbox"
echo "and run a plugin's own install.sh if it has one. Delete the *.before-restore-* copies once happy."
