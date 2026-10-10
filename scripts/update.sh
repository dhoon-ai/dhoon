#!/usr/bin/env bash
# Update everything: this repo, the toolbox, LibreChat, SearXNG and Ollama.
# Your chats, files and models are kept.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

step "Updating this repo"
git -C "$KIT" pull --ff-only || warn "Could not git pull (local changes?). Continuing."

step "Updating toolbox"
"$RUNTIME/venv/bin/pip" install --upgrade -r "$KIT/toolbox/requirements.txt"
docker build --pull -t agent-sandbox "$KIT/sandbox"
sudo systemctl restart ai-toolbox

step "Updating SearXNG"
docker pull searxng/searxng:latest
bash "$KIT/scripts/03_searxng.sh"

step "Updating LibreChat"
git -C "$LIBRECHAT_DIR" pull --ff-only
render_librechat_config
(cd "$LIBRECHAT_DIR" && docker compose pull && docker compose up -d)

step "Updating Ollama"
curl -fsSL https://ollama.com/install.sh | sh

step "Update done"
bash "$KIT/scripts/check.sh" || true
