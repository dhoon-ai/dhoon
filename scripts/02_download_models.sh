#!/usr/bin/env bash
# Script 02: download the AI models (3-22 GB depending on your RAM).
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

step "Script 02: downloading models"
if ! docker info >/dev/null 2>&1; then
  fail "Docker needs you to log out and back in first (see script 01)."; exit 1
fi

echo "  RAM: ${RAM_GB} GB -> main model: $MODEL_MAIN, fast model: $MODEL_FAST"
ollama pull "$MODEL_FAST"     # quick questions, chat titles
ollama pull "$MODEL_MAIN"     # the agent's main brain
ollama pull "$MODEL_EMBED"    # makes your documents searchable

step "Quick speed test (look for 'eval rate')"
ollama run "$MODEL_FAST" "In one sentence: what does a CPU do?" --verbose 2>&1 | tail -n 12

step "Script 02 done"
next "bash scripts/03_searxng.sh"
