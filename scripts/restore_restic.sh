#!/usr/bin/env bash
# Get the big backup (backup_restic.sh) back, role by role, to wherever this computer wants it.
#
#   bash scripts/restore_restic.sh --list [--from usb|r2]
#   bash scripts/restore_restic.sh --role workspace --to ~/agent-workspace [--from r2] [--snapshot latest]
#   bash scripts/restore_restic.sh --drill [--from usb|r2]
#
# Where to: --to, else $RESTORE_DIR/<role> (RESTORE_DIR in restic.env or the environment, default
# ~/restore). The disk and off-site places are RESTIC_USB / RESTIC_R2 in restic.env: nothing is fixed.
# --to must be an empty or new folder: a restore never writes over live data. To replace live data,
# restore to a new folder, check it, then swap the folders yourself.
# --drill restores every role into a scratch folder (RESTIC_DRILL_DIR, default ~/.cache/restic-drill),
# counts the files, checks the database dumps aren't empty, prints a table and exits 1 if anything is
# missing. Nothing live is touched; run it monthly and on a new machine before trusting the backup.
# Settings: the same restic.env as backup_restic.sh.
set -euo pipefail

# shellcheck source=scripts/restic_lib.sh
source "$(cd "$(dirname "$0")" && pwd)/restic_lib.sh"

FROM="" ROLE="" TO="" SNAP="latest" MODE=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --list) MODE=list ;;
    --drill) MODE=drill ;;
    --role) ROLE="${2:?--role needs a name}"; MODE="${MODE:-role}"; shift ;;
    --to) TO="${2:?--to needs a folder}"; shift ;;
    --from) FROM="${2:?--from needs usb or r2}"; shift ;;
    --snapshot) SNAP="${2:?--snapshot needs an id}"; shift ;;
    --help|-h) sed -n '2,15p' "$0"; exit 0 ;;
    *) die "unknown option $1 (see --help)" ;;
  esac
  shift
done
[ -n "${RESTIC_IMAGE:-}" ] || die "RESTIC_IMAGE is not set in $ENV_FILE"
FROM="${FROM:-${RESTIC_TARGETS%% *}}"
mkdir -p "$CACHE"

restore_role() {  # restore_role <role> <empty folder>
  local role="$1" dest="$2"
  mkdir -p "$dest"
  [ -z "$(ls -A "$dest")" ] || die "$dest isn't empty: restore into a new folder, then swap"
  restic_run "$FROM" -v "$dest:/out" -- restore "$SNAP:/src/$role" --host dhoon --tag "role=$role" --target /out
}

case "$MODE" in
  list) restic_run "$FROM" -- snapshots --host dhoon ;;
  role) TO="${TO:-${RESTORE_DIR:-$HOME/restore}/$ROLE}"
        restore_role "$ROLE" "$TO" && say "$ROLE restored to $TO" ;;
  drill)
    base="${RESTIC_DRILL_DIR:-$HOME/.cache/restic-drill}/$(date +%Y%m%d-%H%M%S)"
    bad=0
    printf '%-11s %8s  %s\n' role files result
    for role in $RESTIC_ROLES; do
      if restore_role "$role" "$base/$role" >/dev/null 2>&1; then
        n="$(find "$base/$role" -type f | wc -l)"
        if [ "$n" -gt 0 ]; then r=ok; else r="EMPTY"; bad=1; fi
      else n=0; r="FAILED"; bad=1; fi
      printf '%-11s %8s  %s\n' "$role" "$n" "$r"
    done
    for dump in mongo.archive.gz postgres.sql.gz; do
      if [ -s "$base/rebuild/$dump" ]; then say "dump $dump: ok"; else say "dump $dump: MISSING"; bad=1; fi
    done
    say "Drill folder: $base (delete it when done)"
    exit "$bad" ;;
  *) die "nothing to do (see --help)" ;;
esac
