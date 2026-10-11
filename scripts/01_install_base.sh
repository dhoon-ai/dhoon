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

bash "$KIT/scripts/ollama_settings.sh"

step "Script 01 done"
if [ -z "${FROM_INSTALLER:-}" ]; then
  echo "IMPORTANT: log out and log back in (or reboot) now, so Docker works without sudo."
  next "bash scripts/02_download_models.sh"
fi
