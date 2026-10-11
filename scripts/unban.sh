#!/usr/bin/env bash
# Lift LibreChat's temporary bans (default: 2 hours) on this server, e.g. after a script or
# tool was flagged as a "non-browser" request. Then restarts LibreChat to reset the counters.
#   bash scripts/unban.sh               remove all bans and restart LibreChat
#   bash scripts/unban.sh --if-needed   only act if there is a ban (used by script 06)
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

IF_NEEDED=false
[ "${1:-}" = "--if-needed" ] && IF_NEEDED=true

DOCKER=(docker)
docker info >/dev/null 2>&1 || DOCKER=(sudo docker)
mongo() { "${DOCKER[@]}" exec chat-mongodb mongosh LibreChat --quiet --eval "$1" 2>/dev/null | tail -1; }

if ! "${DOCKER[@]}" ps --format '{{.Names}}' 2>/dev/null | grep -qx chat-mongodb; then
  $IF_NEEDED && exit 0
  fail "LibreChat's database (chat-mongodb) is not running. Start LibreChat: bash scripts/05_librechat.sh"
  exit 1
fi

count=$(mongo 'print(db.logs.countDocuments({ key: /^BANS:/ }))')
count=${count//[^0-9]/}
if $IF_NEEDED && [ "${count:-0}" -eq 0 ]; then
  exit 0   # nothing to do
fi

step "Removing LibreChat bans"
n=$(mongo 'print(db.logs.deleteMany({ key: /^BANS:/ }).deletedCount)')
ok "Removed ${n:-0} ban(s)"
(cd "$LIBRECHAT_DIR" && "${DOCKER[@]}" compose restart api >/dev/null)
echo "  Restarting LibreChat..."
for _ in $(seq 1 60); do
  curl -fs http://localhost:3080 >/dev/null 2>&1 && { ok "LibreChat is back - you can sign in again"; exit 0; }
  sleep 2
done
warn "LibreChat is taking long to restart; check: cd $LIBRECHAT_DIR && docker compose logs api --tail 30"
