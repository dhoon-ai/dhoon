#!/usr/bin/env bash
# Write Ollama's settings and restart it (called by script 01; safe to re-run any time,
# e.g. after changing CONTEXT_LENGTH in config/models.env).
#  - listen on all interfaces so Docker containers can reach it
#    (never forward port 11434 on your router)
#  - context window from CONTEXT_LENGTH (scripts/lib.sh picks it from your RAM)
#  - keep models in memory: forever with 28+ GB RAM, else 30 minutes (KEEP_ALIVE)
#  - load the main model right after Ollama starts (ai-model-preload service), so the
#    first chat after a reboot doesn't wait for it
#  - store models on /data if that disk exists (docs/GUIDE.md step 1.2)
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

step "Ollama settings (context window: $CONTEXT_LENGTH tokens, keep models loaded: $KEEP_ALIVE)"
sudo mkdir -p /etc/systemd/system/ollama.service.d
{
  echo "[Service]"
  echo 'Environment="OLLAMA_HOST=0.0.0.0:11434"'
  echo "Environment=\"OLLAMA_CONTEXT_LENGTH=$CONTEXT_LENGTH\""
  echo "Environment=\"OLLAMA_KEEP_ALIVE=$KEEP_ALIVE\""
  echo 'Environment="OLLAMA_NUM_PARALLEL=1"'
  # at most the main and the small model at once: loading a third big model (another writer
  # being tried, say) unloads one instead of running out of memory
  echo 'Environment="OLLAMA_MAX_LOADED_MODELS=2"'
  if mountpoint -q /data; then
    sudo mkdir -p /data/ollama
    sudo chown -R ollama:ollama /data/ollama 2>/dev/null || true
    echo 'Environment="OLLAMA_MODELS=/data/ollama"'
  fi
} | sudo tee /etc/systemd/system/ollama.service.d/override.conf >/dev/null

# Load the main model whenever Ollama (re)starts, e.g. after a reboot.
sudo tee /etc/systemd/system/ai-model-preload.service >/dev/null <<UNIT
[Unit]
Description=Load the main AI model into memory after Ollama starts
After=ollama.service
PartOf=ollama.service

[Service]
Type=oneshot
User=$USER
ExecStart=/bin/bash $KIT/scripts/preload_model.sh
TimeoutStartSec=1800

[Install]
WantedBy=ollama.service
UNIT
# Run it again after midnight: the date in the Assistant's instructions changes then.
sudo tee /etc/systemd/system/ai-warmup.timer >/dev/null <<UNIT
[Unit]
Description=Have the model read the Assistant's instructions in advance (new date)

[Timer]
OnCalendar=*-*-* 00:05
Unit=ai-model-preload.service

[Install]
WantedBy=timers.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable ai-model-preload.service >/dev/null 2>&1 || true
sudo systemctl enable --now ai-warmup.timer >/dev/null 2>&1 || true
sudo systemctl restart ollama
ok "Ollama restarted with the new settings"
if [ "$KEEP_ALIVE" = "-1" ]; then
  echo "  The main model is loading in the background and will stay in memory (check: ollama ps)."
fi
