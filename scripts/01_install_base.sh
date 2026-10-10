#!/usr/bin/env bash
# Script 01: install Docker and Ollama, and configure Ollama for a home PC.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

step "Script 01: installing base software (you may be asked for your password)"

sudo apt-get update
sudo apt-get install -y git curl python3-venv python3-pip openssl iproute2 pciutils

# ---- Docker ----
if ! command -v docker >/dev/null 2>&1; then
  echo "--- Installing Docker ---"
  if ! curl -fsSL https://get.docker.com | sudo sh; then
    warn "Official Docker script failed; using Ubuntu's Docker packages instead."
    sudo apt-get install -y docker.io docker-compose-v2
  fi
fi
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"

# ---- Ollama ----
if ! command -v ollama >/dev/null 2>&1; then
  echo "--- Installing Ollama ---"
  curl -fsSL https://ollama.com/install.sh | sh
fi

# Ollama settings:
#  - listen on all interfaces so Docker containers can reach it
#    (never forward port 11434 on your router)
#  - 16k context window: enough for agent work, not too slow on CPU
#  - keep the model in memory for 30 minutes between requests
#  - store models on /data if that disk exists (docs/GUIDE.md step 1.2)
sudo mkdir -p /etc/systemd/system/ollama.service.d
{
  echo "[Service]"
  echo 'Environment="OLLAMA_HOST=0.0.0.0:11434"'
  echo 'Environment="OLLAMA_CONTEXT_LENGTH=16384"'
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

step "Script 01 done"
echo "IMPORTANT: log out and log back in (or reboot) now, so Docker works without sudo."
next "bash scripts/02_download_models.sh"
