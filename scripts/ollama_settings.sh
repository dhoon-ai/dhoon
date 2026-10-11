#!/usr/bin/env bash
# Write Ollama's settings and restart it (called by script 01; safe to re-run any time,
# e.g. after changing CONTEXT_LENGTH in config/models.env).
#  - listen on all interfaces so Docker containers can reach it
#    (never forward port 11434 on your router)
#  - context window from CONTEXT_LENGTH (scripts/lib.sh picks it from your RAM)
#  - keep the model in memory for 30 minutes between requests
#  - store models on /data if that disk exists (docs/GUIDE.md step 1.2)
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

step "Ollama settings (context window: $CONTEXT_LENGTH tokens)"
sudo mkdir -p /etc/systemd/system/ollama.service.d
{
  echo "[Service]"
  echo 'Environment="OLLAMA_HOST=0.0.0.0:11434"'
  echo "Environment=\"OLLAMA_CONTEXT_LENGTH=$CONTEXT_LENGTH\""
  echo 'Environment="OLLAMA_KEEP_ALIVE=30m"'
  echo 'Environment="OLLAMA_NUM_PARALLEL=1"'
  if mountpoint -q /data; then
    sudo mkdir -p /data/ollama
    sudo chown -R ollama:ollama /data/ollama 2>/dev/null || true
    echo 'Environment="OLLAMA_MODELS=/data/ollama"'
  fi
} | sudo tee /etc/systemd/system/ollama.service.d/override.conf >/dev/null
sudo systemctl daemon-reload
sudo systemctl restart ollama
ok "Ollama restarted with the new settings"
