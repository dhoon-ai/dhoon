#!/usr/bin/env bash
# Install the chat stack's Quadlets and ai-toolbox.service (config/librechat/podman) for this user,
# with this computer's folders filled in (@DHOON_HOME@ = this checkout, @WORKSPACE@ = the workspace
# from the paths file). Quadlets can't read environment variables, so the paths are written in.
#   bash scripts/install_podman_units.sh [--dest-quadlets DIR] [--dest-units DIR]
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
need_path WORKSPACE_DIR
QDIR="$HOME/.config/containers/systemd"
UDIR="$HOME/.config/systemd/user"
while [ $# -gt 0 ]; do
  case "$1" in
    --dest-quadlets) QDIR="$2"; shift 2 ;;
    --dest-units) UDIR="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
mkdir -p "$QDIR" "$UDIR"
fill() { sed -e "s#@DHOON_HOME@#$KIT#g" -e "s#@WORKSPACE@#$WORKSPACE_DIR#g" "$1"; }
for f in "$KIT"/config/librechat/podman/*.container "$KIT"/config/librechat/podman/*.pod; do
  fill "$f" > "$QDIR/$(basename "$f")"
done
fill "$KIT/config/librechat/podman/ai-toolbox.service" > "$UDIR/ai-toolbox.service"
if grep -l '@[A-Z_]*@' "$QDIR"/*.container "$QDIR"/*.pod "$UDIR/ai-toolbox.service" 2>/dev/null; then
  echo "ERROR: a placeholder was left in the files above" >&2; exit 1
fi
echo "Installed into $QDIR and $UDIR (DHOON_HOME=$KIT, WORKSPACE=$WORKSPACE_DIR)."
echo "Then: systemctl --user daemon-reload"
