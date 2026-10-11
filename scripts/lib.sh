#!/usr/bin/env bash
# Shared settings and helpers for all scripts. Sourced, not run directly.
# shellcheck disable=SC2034  # variables are used by the scripts that source this file

# Repo folder = the folder above scripts/, wherever you cloned it.
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Generated files and downloaded apps live here (ignored by git).
RUNTIME="$KIT/runtime"
LIBRECHAT_DIR="$RUNTIME/LibreChat"
SEARXNG_DIR="$RUNTIME/searxng"
WORKSPACE_DIR="${WORKSPACE_DIR:-$HOME/agent-workspace}"
TOOLBOX_PORT="${TOOLBOX_PORT:-8001}"

mkdir -p "$RUNTIME"

# ---- Models ----
# Your own choices go in config/models.env (copy config/models.env.example).
# Anything not set there is picked automatically from the amount of RAM.
# shellcheck source=/dev/null
[ -f "$KIT/config/models.env" ] && source "$KIT/config/models.env"

# We use Qwen3 "Instruct 2507" models: they answer directly instead of "thinking"
# first. Thinking models often never emit the tool call (it gets stuck inside the
# hidden reasoning), and the long reasoning is very slow on a CPU.
RAM_GB=$(awk '/MemTotal/{printf "%d", $2/1024/1024}' /proc/meminfo)
if [ -z "${MODEL_MAIN:-}" ]; then
  if [ "$RAM_GB" -ge 28 ]; then MODEL_MAIN="qwen3:30b-a3b-instruct-2507-q4_K_M"   # MoE, ~19 GB, ~3B active: best
  else                          MODEL_MAIN="qwen3:4b-instruct-2507-q4_K_M"        # ~2.5 GB
  fi
fi
MODEL_FAST="${MODEL_FAST:-qwen3:4b-instruct-2507-q4_K_M}"
MODEL_EMBED="${MODEL_EMBED:-nomic-embed-text}"
# The planner "thinks" and writes plans for the agent (used by the ask_planner tool).
# A second 19 GB model needs RAM to hold both, so smaller machines get the 4b one.
if [ -z "${MODEL_PLANNER:-}" ]; then
  if [ "$RAM_GB" -ge 50 ]; then MODEL_PLANNER="qwen3:30b-a3b-thinking-2507-q4_K_M"
  else                          MODEL_PLANNER="qwen3:4b-thinking-2507-q4_K_M"
  fi
fi

step() { printf '\n\033[1;34m=== %s ===\033[0m\n' "$*"; }
ok()   { printf '  \033[1;32m[OK]\033[0m   %s\n' "$*"; }
warn() { printf '  \033[1;33m[WARN]\033[0m %s\n' "$*"; }
fail() { printf '  \033[1;31m[FAIL]\033[0m %s\n' "$*"; }
next() { printf '\n\033[1mNext:\033[0m  %s\n' "$*"; }

# IP address of Docker's internal bridge (containers reach the host here)
docker_bridge_ip() {
  ip -4 addr show docker0 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1
}

# Copy the LibreChat config template, filling in the chosen model names.
render_librechat_config() {
  sed -e "s|__MODEL_MAIN__|$MODEL_MAIN|g" -e "s|__MODEL_FAST__|$MODEL_FAST|g" \
    "$KIT/config/librechat/librechat.yaml" > "$LIBRECHAT_DIR/librechat.yaml"
  cp "$KIT/config/librechat/docker-compose.override.yml" "$LIBRECHAT_DIR/docker-compose.override.yml"
}
