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
# make sure the service file has the current settings (e.g. PLANNER_MODEL)
if ! grep -qx "Environment=PLANNER_MODEL=$MODEL_PLANNER" /etc/systemd/system/ai-toolbox.service 2>/dev/null \
   || ! grep -q "^Environment=RESEARCH_ENGINES=" /etc/systemd/system/ai-toolbox.service 2>/dev/null \
   || ! grep -q "^Environment=TOOLBOX_LOG=" /etc/systemd/system/ai-toolbox.service 2>/dev/null \
   || ! grep -q "^EnvironmentFile=-$KIT/config/email.env" /etc/systemd/system/ai-toolbox.service 2>/dev/null \
   || [ ! -f /etc/systemd/system/ai-scheduler.timer ]; then
  warn "Toolbox service settings are outdated - re-running script 04"
  bash "$KIT/scripts/04_toolbox.sh"
fi
docker build --pull -t agent-sandbox "$KIT/sandbox"
sudo systemctl restart ai-toolbox

step "Updating SearXNG"
docker pull "$SEARXNG_IMAGE"   # stays on the tested build (SEARXNG_IMAGE in lib.sh)
bash "$KIT/scripts/03_searxng.sh"

step "Updating LibreChat"
librechat_checkout   # stays on the tested version (LIBRECHAT_REF in lib.sh)
render_librechat_config
(cd "$LIBRECHAT_DIR" && docker compose pull && docker compose up -d)

step "Updating Ollama"
curl -fsSL https://ollama.com/install.sh | sh

if ! grep -q "OLLAMA_CONTEXT_LENGTH=$CONTEXT_LENGTH\"" /etc/systemd/system/ollama.service.d/override.conf 2>/dev/null \
   || ! grep -q "OLLAMA_KEEP_ALIVE=$KEEP_ALIVE\"" /etc/systemd/system/ollama.service.d/override.conf 2>/dev/null \
   || [ ! -f /etc/systemd/system/ai-model-preload.service ] \
   || [ ! -f /etc/systemd/system/ai-warmup.timer ]; then
  bash "$KIT/scripts/ollama_settings.sh"
fi

step "Making sure the configured models are downloaded"
# The Ollama installer and the settings above restart Ollama: wait until it answers.
wait_for_ollama 60 || { fail "Ollama did not start: journalctl -u ollama -n 50"; exit 1; }
for m in "$MODEL_FAST" "$MODEL_MAIN" "$MODEL_EMBED" "$MODEL_PLANNER"; do ollama pull "$m"; done

step "Update done"
bash "$KIT/scripts/check.sh" || true
