#!/usr/bin/env bash
# Remove the agent stack, in three levels:
#
#   bash scripts/uninstall.sh               stop and remove the services; KEEP chats, models, files
#   bash scripts/uninstall.sh --everything  also DELETE chats, uploads, models, ~/agent-workspace
#   bash scripts/uninstall.sh --purge       --everything, plus UNINSTALL Ollama and Docker
#                                           and undo every system change the install made
#
# Add --yes to skip the confirmation question, or set DRY_RUN=1 to only print what
# would happen. This repo folder itself is never deleted; remove it yourself afterwards.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

LEVEL="services"
ASSUME_YES=false
for arg in "$@"; do
  case "$arg" in
    --everything) LEVEL="everything" ;;
    --purge)      LEVEL="purge" ;;
    --yes)        ASSUME_YES=true ;;
    *) echo "Unknown option: $arg"; exit 2 ;;
  esac
done

# Run a command, or only print it when DRY_RUN=1. Failures are reported but don't stop
# the cleanup (something may already be gone).
run() {
  if [ "${DRY_RUN:-}" = "1" ]; then echo "  [dry-run] $*"; return 0; fi
  "$@" || warn "failed (continuing): $*"
}

# LibreChat folders: the repo layout (runtime/LibreChat) and the older kit layout.
LC_DIRS=()
for d in "$LIBRECHAT_DIR" "$KIT/LibreChat"; do [ -d "$d" ] && LC_DIRS+=("$d"); done

confirm() {
  $ASSUME_YES && return 0
  [ "${DRY_RUN:-}" = "1" ] && return 0
  echo
  echo "$1"
  read -r -p "Type YES to continue: " answer
  [ "$answer" = "YES" ]
}

case "$LEVEL" in
  everything) confirm "This permanently DELETES all chats, uploaded files, the AI models and $WORKSPACE_DIR." \
                || { warn "Cancelled - nothing changed"; exit 1; } ;;
  purge)      confirm "This permanently DELETES all chats, files and models, and UNINSTALLS Ollama and Docker.
Docker removal also deletes ANY other Docker containers and images on this computer." \
                || { warn "Cancelled - nothing changed"; exit 1; } ;;
esac

# ---------------------------------------------------------------- level 1: services ---
step "Stopping and removing services"
run sudo systemctl disable --now ai-toolbox ai-model-preload
run sudo rm -f /etc/systemd/system/ai-toolbox.service /etc/systemd/system/ai-model-preload.service
# any other ai-* service or timer, e.g. added by a private plugin
for unit in "$SYSTEMD_ROOT"/ai-*.service "$SYSTEMD_ROOT"/ai-*.timer; do
  [ -e "$unit" ] || continue
  run sudo systemctl disable --now "$(basename "$unit")"
  run sudo rm -f "$unit"
done
run sudo systemctl daemon-reload
if command -v docker >/dev/null 2>&1; then
  run docker rm -f searxng
  for d in "${LC_DIRS[@]}"; do
    if [ "$LEVEL" = "services" ]; then (cd "$d" && run docker compose down)
    else (cd "$d" && run docker compose down -v --rmi all); fi   # -v: also the database volumes
  done
  run docker rmi agent-sandbox
fi

if [ "$LEVEL" = "services" ]; then
  ok "Services removed. Chats, models and files are kept."
  echo "  Reinstall with scripts 03, 04 and 05. Delete everything with --everything or --purge."
  exit 0
fi

# ---------------------------------------------------------------- level 2: data ---
step "Deleting chats, uploads, settings and the workspace"
for d in "${LC_DIRS[@]}"; do run sudo rm -rf "$d"; done
run sudo rm -rf "$RUNTIME" "$KIT/toolbox/venv"   # toolbox/venv: older kit layout
run rm -rf "$WORKSPACE_DIR"
if command -v docker >/dev/null 2>&1; then
  run docker rmi searxng/searxng:latest
fi

if [ "$LEVEL" = "everything" ]; then
  step "Deleting this project's AI models"
  if command -v ollama >/dev/null 2>&1; then
    for m in "$MODEL_MAIN" "$MODEL_FAST" "$MODEL_EMBED" "$MODEL_PLANNER" qwen3:30b-a3b qwen3:4b qwen3:8b \
             qwen3:30b-a3b-thinking-2507-q4_K_M qwen3:4b-thinking-2507-q4_K_M; do
      ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$m" && run ollama rm "$m"
    done
  fi
  ok "Chats, files and models deleted. Docker and Ollama are still installed (remove them with --purge)."
  exit 0
fi

# ---------------------------------------------------------------- level 3: purge ---
step "Uninstalling Ollama (including ALL its models)"
run sudo systemctl disable --now ollama
run sudo rm -f /etc/systemd/system/ollama.service
run sudo rm -rf /etc/systemd/system/ollama.service.d
run sudo rm -f /usr/local/bin/ollama /usr/bin/ollama
run sudo rm -rf /usr/local/lib/ollama /usr/lib/ollama /usr/share/ollama
mountpoint -q /data 2>/dev/null && run sudo rm -rf /data/ollama
run rm -rf "$HOME/.ollama"
id ollama >/dev/null 2>&1 && run sudo userdel ollama
getent group ollama >/dev/null 2>&1 && run sudo groupdel ollama

step "Uninstalling Docker (including ALL containers, images and volumes)"
if command -v docker >/dev/null 2>&1; then
  run docker system prune -a -f --volumes
fi
run sudo systemctl disable --now docker.socket docker containerd
for p in docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
         docker-ce-rootless-extras docker-model-plugin docker.io docker-compose-v2; do
  dpkg -s "$p" >/dev/null 2>&1 && run sudo apt-get purge -y "$p"
done
run sudo apt-get autoremove -y
run sudo rm -rf /var/lib/docker /var/lib/containerd /etc/docker
run sudo rm -f /etc/apt/sources.list.d/docker.list /etc/apt/sources.list.d/docker.sources \
               /etc/apt/keyrings/docker.asc /etc/apt/keyrings/docker.gpg
getent group docker >/dev/null 2>&1 && run sudo groupdel docker
run sudo systemctl daemon-reload

ok "Purge complete. Ollama, Docker and all project data are gone."
echo
echo "Left in place on purpose (remove yourself if you want):"
echo "  - this repo folder:          rm -rf \"$KIT\""
echo "  - basic tools (git, curl, python3-venv): harmless, used by many programs"
if mountpoint -q /data 2>/dev/null; then
  echo "  - the /data disk and its /etc/fstab line (other files may be on it)."
  echo "    To stop mounting it: sudo umount /data, then delete the '/data' line in /etc/fstab"
fi
echo "Log out and back in (or reboot) to finish removing the docker group."
