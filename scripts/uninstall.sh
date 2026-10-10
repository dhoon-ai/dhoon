#!/usr/bin/env bash
# Stop and remove the agent stack. Keeps your models, chats and workspace
# unless you pass --everything.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

EVERYTHING=false
[ "${1:-}" = "--everything" ] && EVERYTHING=true

step "Stopping services"
sudo systemctl disable --now ai-toolbox 2>/dev/null || true
sudo rm -f /etc/systemd/system/ai-toolbox.service
sudo systemctl daemon-reload
docker rm -f searxng 2>/dev/null || true
if [ -d "$LIBRECHAT_DIR" ]; then (cd "$LIBRECHAT_DIR" && docker compose down) || true; fi
docker rmi agent-sandbox 2>/dev/null || true

if $EVERYTHING; then
  read -r -p "Delete ALL chats, LibreChat data, models and $WORKSPACE_DIR? Type YES: " answer
  if [ "$answer" = "YES" ]; then
    (cd "$LIBRECHAT_DIR" 2>/dev/null && docker compose down -v) || true
    rm -rf "$RUNTIME" "$WORKSPACE_DIR"
    for m in "$MODEL_MAIN" "$MODEL_FAST" "$MODEL_EMBED"; do ollama rm "$m" 2>/dev/null || true; done
    ok "Everything removed (Docker and Ollama programs are still installed)"
  else
    warn "Cancelled - data kept"
  fi
else
  ok "Services removed. Models, chats and workspace kept. Re-install with scripts 03-05."
fi
