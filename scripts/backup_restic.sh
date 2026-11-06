#!/usr/bin/env bash
# The big backup: everything backup.sh can't fit in a git repo (the agent's workspace, Claude's
# settings, database dumps, the lists needed to rebuild), with restic, to every target you set:
# an external disk and/or S3-compatible storage off-site (e.g. Cloudflare R2). restic runs in a rootless
# Podman container pinned by digest: nothing to install but Podman.
#
#   bash scripts/backup_restic.sh             back up now (the daily user timer runs this)
#   bash scripts/backup_restic.sh --status    age of the last backup per target; exit 1 if stale or failed
#   bash scripts/backup_restic.sh --install   write and enable the daily user timer (no sudo)
#   bash scripts/restore_restic.sh --help     get data back, or --drill to prove it can be done
#
# Data is stored by ROLE, not by path: each role is mounted at /src/<role> in the container and saved
# as its own snapshot (tag role=<role>), so a restore can put it wherever the new machine wants it.
# Roles (RESTIC_ROLES) and their default places (override any with ROLE_PATH_<role>, e.g.
# ROLE_PATH_workspace=/srv/ws). Any other folder becomes a role too: ROLE_PATH_notes=~/notes and
# add notes to RESTIC_ROLES:
#   workspace  $WORKSPACE_DIR (agent-workspace)    claude   ~/.claude
#   config     <repo>/config (*.env: secrets)
#   backupconf ~/.config/ai-kit-backup             ssh      ~/.ssh
#   rebuild    made fresh each run: database dumps, runtime state files, user units, Quadlets,
#              model/image/package lists, crontab
# Settings: ~/.config/ai-kit-backup/restic.env (chmod 600):
#   RESTIC_IMAGE        docker.io/restic/restic@sha256:...   (pinned; required)
#   RESTIC_TARGETS      "usb r2" (any of them)
#   RESTIC_USB          /path/to/disk/restic                 (a folder on the external disk)
#   RESTIC_R2           s3:https://<account>.r2.cloudflarestorage.com/<bucket>
#   AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY             (the R2 API token; never on a command line)
#   RESTIC_PASSWORD_FILE  default ~/.config/ai-kit-backup/restic-password. Keep a copy OFF this
#                       computer and NOT on the backup disk (a password manager): without it nothing
#                       can be restored; with it next to the disk, anyone holding the disk can read it.
#   RESTIC_KEEP         "--keep-daily 7 --keep-weekly 4 --keep-monthly 6" (applied on Sundays)
#   RESTIC_TIME         03:10 (the daily timer)
set -euo pipefail

# shellcheck source=scripts/restic_lib.sh
source "$(cd "$(dirname "$0")" && pwd)/restic_lib.sh"

make_rebuild() {  # what a rebuild needs that isn't a folder: dumps, state files, units, lists
  rm -rf "$STAGE.tmp" && mkdir -p "$STAGE.tmp" && chmod 700 "$STAGE.tmp"
  local d="$STAGE.tmp"
  if command -v "$DOCKER" >/dev/null 2>&1; then
    local running; running="$("$DOCKER" ps --format '{{.Names}}' 2>/dev/null || true)"
    if grep -qx chat-mongodb <<<"$running"; then
      "$DOCKER" exec chat-mongodb mongodump --quiet --archive --gzip --db LibreChat > "$d/mongo.archive.gz" \
        || warn "mongo dump failed"
    else warn "chat-mongodb isn't running: no chat dump"; fi
    if grep -qx vectordb <<<"$running"; then
      "$DOCKER" exec vectordb pg_dumpall -U "${PG_USER:-myuser}" 2>/dev/null | gzip > "$d/postgres.sql.gz" \
        || warn "postgres dump failed"
    else warn "vectordb isn't running: no postgres dump"; fi
    "$DOCKER" images --digests --format '{{.Repository}}:{{.Tag}} {{.Digest}}' > "$d/images-docker.txt" 2>/dev/null || true
  fi
  "$PODMAN" images --digests --format '{{.Repository}}:{{.Tag}} {{.Digest}}' > "$d/images-podman.txt" 2>/dev/null || true
  command -v ollama >/dev/null 2>&1 && ollama list > "$d/models.txt" 2>/dev/null || true
  crontab -l > "$d/crontab.txt" 2>/dev/null || true
  [ -x "$KIT/runtime/venv/bin/pip" ] && "$KIT/runtime/venv/bin/pip" freeze > "$d/pip-freeze.txt" 2>/dev/null || true
  for f in schedule.json assistant.json google_token.json; do
    [ -f "$KIT/runtime/$f" ] && cp -p "$KIT/runtime/$f" "$d/" || true
  done
  for u in "$HOME/.config/systemd/user" "$HOME/.config/containers/systemd"; do
    [ -d "$u" ] && mkdir -p "$d/units" && cp -rp "$u" "$d/units/$(basename "$(dirname "$u")")-$(basename "$u")" || true
  done
  ls -l "$HOME/.local/bin" > "$d/local-bin.txt" 2>/dev/null || true
  date -Iseconds > "$d/made-at.txt"
  rm -rf "$STAGE" && mv "$STAGE.tmp" "$STAGE"
}

backup_target() {  # back up every role to one target; 0 = all saved
  local target="$1" failed=0
  if ! restic_run "$target" -- cat config >/dev/null 2>&1; then
    say "[$target] new repository: restic init"
    restic_run "$target" -- init || { warn "[$target] could not create the repository"; return 1; }
  fi
  for role in $RESTIC_ROLES; do
    local src; src="$(role_path "$role")" || { warn "unknown role $role"; failed=1; continue; }
    if [ ! -e "$src" ]; then warn "[$target] $role: $src doesn't exist, skipped"; continue; fi
    if restic_run "$target" -v "$src:/src/$role:ro" -- backup "/src/$role" --host dhoon --tag "role=$role" \
         "${EXCLUDES[@]}" --quiet; then
      say "[$target] $role saved"
    else
      warn "[$target] $role FAILED"; failed=1
    fi
  done
  if [ "$(date +%u)" = 7 ] || [ "${RESTIC_PRUNE:-}" = yes ]; then
    # shellcheck disable=SC2086  # RESTIC_KEEP is a list of flags
    restic_run "$target" -- forget --host dhoon --group-by host,tags $RESTIC_KEEP --prune --quiet \
      || warn "[$target] forget/prune failed (the backup itself is saved)"
  fi
  return "$failed"
}

status() {  # one line per target from the last run; exit 1 if any is stale or failed
  [ -f "$STATUS" ] || { say "No restic backup has run yet."; return 1; }
  local bad=0 now; now="$(date +%s)"
  while read -r target result when; do
    [ -n "$target" ] || continue
    local age=$(( (now - $(date -d "$when" +%s)) / 3600 ))
    say "$target: $result, ${age}h ago ($when)"
    { [ "$result" = ok ] && [ "$age" -lt "$STALE_HOURS" ]; } || bad=1
  done < "$STATUS"
  return "$bad"
}

install_timer() {
  local dir="$HOME/.config/systemd/user"
  mkdir -p "$dir"
  cat > "$dir/ai-backup-restic.service" <<UNIT
[Unit]
Description=Big backup with restic (disk + off-site)
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/env bash $SCRIPTS/backup_restic.sh
Nice=10
TimeoutStartSec=3h
UNIT
  cat > "$dir/ai-backup-restic.timer" <<UNIT
[Unit]
Description=Big backup with restic, daily

[Timer]
OnCalendar=*-*-* ${RESTIC_TIME:-03:10}:00
Persistent=true

[Install]
WantedBy=timers.target
UNIT
  systemctl --user daemon-reload && systemctl --user enable --now ai-backup-restic.timer
  say "Daily at ${RESTIC_TIME:-03:10}: systemctl --user list-timers ai-backup-restic.timer"
}

case "${1:-}" in
  --status)  status; exit $? ;;
  --install) install_timer; exit 0 ;;
  --help|-h) sed -n '2,32p' "$0"; exit 0 ;;
  "") ;;
  *) die "unknown option $1 (see --help)" ;;
esac

[ -n "${RESTIC_IMAGE:-}" ] || die "RESTIC_IMAGE is not set in $ENV_FILE (a pinned restic image)"
[ -f "$RESTIC_PASSWORD_FILE" ] || die "no password file $RESTIC_PASSWORD_FILE"
mkdir -p "$CACHE" "$CONF"
exec 9>"$CONF/.restic.lock"
flock -n 9 || die "another restic backup is running"
make_rebuild
overall=0
: > "$STATUS.tmp"
for t in $RESTIC_TARGETS; do
  if backup_target "$t"; then r=ok; else r=failed; overall=1; fi
  printf '%s %s %s\n' "$t" "$r" "$(date -Iseconds)" >> "$STATUS.tmp"
done
mv "$STATUS.tmp" "$STATUS"
status || true
exit "$overall"
