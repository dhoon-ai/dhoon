#!/usr/bin/env bash
# Shared settings and helpers for all scripts. Sourced, not run directly.
# shellcheck disable=SC2034  # variables are used by the scripts that source this file

# Repo folder = the folder above scripts/, wherever you cloned it.
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Generated files and downloaded apps live here (ignored by git).
RUNTIME="${RUNTIME_DIR:-$KIT/runtime}"
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

# CPU temperature in C, from the CPU package sensor when available. Some boards expose a
# broken first sensor (e.g. -263 C), so implausible readings are skipped. Empty if unknown.
cpu_temp() {
  local z type t best=""
  for z in /sys/class/thermal/thermal_zone*; do
    [ -r "$z/temp" ] || continue
    t=$(( $(cat "$z/temp" 2>/dev/null || echo -999999) / 1000 ))
    [ "$t" -gt 0 ] && [ "$t" -lt 125 ] || continue
    type=$(cat "$z/type" 2>/dev/null)
    if [ "$type" = "x86_pkg_temp" ] || [ "$type" = "cpu-thermal" ]; then echo "$t"; return; fi
    [ -n "$best" ] || best="$t"
  done
  echo "$best"
}

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

# ---------------------------------------------------------------- install status ---
# step_done N  -> exit 0 if install step N (1-6) is already complete, else 1.
# Each check looks at the real system, so it works whether the step was done by the
# installer, by hand, or by an older version of these scripts. STATUS_REASON says why not.
STATUS_REASON=""
SYSTEMD_ROOT="${SYSTEMD_ROOT:-/etc/systemd/system}"
STEP_NAMES=("" "Docker + Ollama" "AI models" "Private search (SearXNG)" "Agent toolbox" "LibreChat web app" "Assistant agent")

_model_present() {  # model name as in `ollama list` (with or without :latest)
  ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -qxE "$(printf '%s' "$1" | sed 's/[.[\*^$]/\\&/g')(:latest)?"
}

step_done() {
  STATUS_REASON=""
  case "$1" in
    1)
      command -v docker >/dev/null 2>&1 || { STATUS_REASON="Docker not installed"; return 1; }
      command -v ollama >/dev/null 2>&1 || { STATUS_REASON="Ollama not installed"; return 1; }
      [ -f "$SYSTEMD_ROOT/ollama.service.d/override.conf" ] || { STATUS_REASON="Ollama not configured"; return 1; }
      getent group docker | grep -qw "$USER" || { STATUS_REASON="$USER not in the docker group"; return 1; }
      ;;
    2)
      curl -fs http://127.0.0.1:11434/api/tags >/dev/null 2>&1 || { STATUS_REASON="Ollama not running"; return 1; }
      local m
      for m in "$MODEL_MAIN" "$MODEL_FAST" "$MODEL_EMBED" "$MODEL_PLANNER"; do
        _model_present "$m" || { STATUS_REASON="model $m not downloaded"; return 1; }
      done
      ;;
    3)
      curl -fs "http://127.0.0.1:8888/search?q=test&format=json" >/dev/null 2>&1 \
        || { STATUS_REASON="SearXNG not answering"; return 1; }
      ;;
    4)
      systemctl is-active --quiet ai-toolbox 2>/dev/null || { STATUS_REASON="toolbox service not running"; return 1; }
      grep -q "^ExecStart=.*$KIT/toolbox/toolbox_server.py" "$SYSTEMD_ROOT/ai-toolbox.service" 2>/dev/null \
        || { STATUS_REASON="toolbox service points to another folder"; return 1; }
      grep -q "PLANNER_MODEL=$MODEL_PLANNER" "$SYSTEMD_ROOT/ai-toolbox.service" 2>/dev/null \
        || { STATUS_REASON="toolbox settings out of date"; return 1; }
      "$RUNTIME/venv/bin/python" -c "import mcp, pypdf, docx, numpy" 2>/dev/null \
        || { STATUS_REASON="toolbox Python packages missing or outdated"; return 1; }
      ;;
    5)
      curl -fs http://localhost:3080 >/dev/null 2>&1 || { STATUS_REASON="LibreChat not answering"; return 1; }
      [ -f "$LIBRECHAT_DIR/librechat.yaml" ] || { STATUS_REASON="LibreChat not installed in $LIBRECHAT_DIR"; return 1; }
      if ! grep -q "allowedAddresses" "$LIBRECHAT_DIR/librechat.yaml" || ! grep -q "fileConfig" "$LIBRECHAT_DIR/librechat.yaml"; then
        STATUS_REASON="LibreChat settings out of date"; return 1
      fi
      ;;
    6)
      [ "$(cat "$RUNTIME/.agent-created" 2>/dev/null)" = "$MODEL_MAIN" ] \
        || { STATUS_REASON="agent not created yet (or uses another model)"; return 1; }
      ;;
    *) return 1 ;;
  esac
  return 0
}
