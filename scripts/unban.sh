#!/usr/bin/env bash
# Lift LibreChat's temporary bans (default: 2 hours) on this server, e.g. after a script or
# tool was flagged as a "non-browser" request. Then restarts LibreChat to reset the counters.
#   bash scripts/unban.sh
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

step "Removing LibreChat bans"
DOCKER=(docker)
docker info >/dev/null 2>&1 || DOCKER=(sudo docker)
n=$("${DOCKER[@]}" exec chat-mongodb mongosh LibreChat --quiet \
  --eval 'print(db.logs.deleteMany({ key: /^BANS:/ }).deletedCount)' 2>/dev/null | tail -1)
ok "Removed ${n:-0} ban(s)"
(cd "$LIBRECHAT_DIR" && "${DOCKER[@]}" compose restart api >/dev/null)
ok "LibreChat restarted - sign in again at http://localhost:3080 (wait ~30 seconds)"
