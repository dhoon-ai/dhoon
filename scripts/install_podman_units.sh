#!/usr/bin/env bash
# Install the chat stack's Quadlets and ai-toolbox.service (config/librechat/podman) for this user,
# with this computer's folders filled in (@DHOON_HOME@ = this checkout, @WORKSPACE@ = the workspace
# from the paths file). Quadlets can't read environment variables, so the paths are written in.
# They go where systemd looks for this user: $XDG_CONFIG_HOME (default ~/.config).
#   bash scripts/install_podman_units.sh [--only searxng,ai-toolbox,...] [--dest-quadlets DIR] [--dest-units DIR]
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
need_path WORKSPACE_DIR
QDIR="${XDG_CONFIG_HOME:-$HOME/.config}/containers/systemd"
UDIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
ONLY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --only) ONLY=",$2,"; shift 2 ;;
    --dest-quadlets) QDIR="$2"; shift 2 ;;
    --dest-units) UDIR="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
mkdir -p "$QDIR" "$UDIR"
# Podman needs the registry in an image name (Docker assumed docker.io)
full_image() { case ${1%%/*} in *.*|*:*|localhost) printf '%s' "$1" ;; *) printf 'docker.io/%s' "$1" ;; esac; }
fill() { sed -e "s#@DHOON_HOME@#$KIT#g" -e "s#@WORKSPACE@#$WORKSPACE_DIR#g" -e "s#@SEARXNG_IMAGE@#$(full_image "$SEARXNG_IMAGE")#g" "$1"; }
wanted() { local n; n=$(basename "$1"); n=${n%.*}; [ -z "$ONLY" ] || [[ $ONLY == *",$n,"* ]]; }
written=()
for f in "$KIT"/config/librechat/podman/*.container "$KIT"/config/librechat/podman/*.pod; do
  wanted "$f" || continue
  fill "$f" > "$QDIR/$(basename "$f")"; written+=("$QDIR/$(basename "$f")")
done
if wanted ai-toolbox.service; then
  fill "$KIT/config/librechat/podman/ai-toolbox.service" > "$UDIR/ai-toolbox.service"; written+=("$UDIR/ai-toolbox.service")
fi
# SearXNG reads its settings from runtime/searxng (made once, with its own secret key)
if wanted searxng.container && [ ! -f "$SEARXNG_DIR/settings.yml" ]; then
  mkdir -p "$SEARXNG_DIR"
  sed "s/CHANGE_ME/$(od -An -tx1 -N32 /dev/urandom | tr -d ' \n')/" "$KIT/config/searxng/settings.yml" > "$SEARXNG_DIR/settings.yml"
fi
if [ ${#written[@]} -gt 0 ] && grep -l '@[A-Z_]*@' "${written[@]}"; then
  echo "ERROR: a placeholder was left in the files above" >&2; exit 1
fi
echo "Installed into $QDIR and $UDIR (DHOON_HOME=$KIT, WORKSPACE=$WORKSPACE_DIR)."
echo "Then: systemctl --user daemon-reload"
