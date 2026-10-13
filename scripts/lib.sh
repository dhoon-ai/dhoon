#!/usr/bin/env bash
# Shared settings and helpers for all scripts. Sourced, not run directly.
# shellcheck disable=SC2034  # variables are used by the scripts that source this file

# Repo folder = the folder above scripts/, wherever you cloned it.
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Generated files and downloaded apps live here (ignored by git).
RUNTIME="${RUNTIME_DIR:-$KIT/runtime}"
LIBRECHAT_DIR="$RUNTIME/LibreChat"
SEARXNG_DIR="$RUNTIME/searxng"
# The LibreChat version Dhoon is tested with. Its images are pinned to match in
# config/librechat/docker-compose.override.yml. To move to a newer LibreChat: change both,
# run scripts/update.sh, test, then release.
LIBRECHAT_REF="${LIBRECHAT_REF:-e1dfc10449ff713faffacd60273fddcfe2c0a698}"
LIBRECHAT_GIT_URL="${LIBRECHAT_GIT_URL:-https://github.com/danny-avila/LibreChat.git}"
# The SearXNG build Dhoon is tested with (version 2026.10.11-ad46ebd7d).
SEARXNG_IMAGE="${SEARXNG_IMAGE:-searxng/searxng@sha256:ddd642cae6eac75412938fd846ddbf440a2df029cfeae8a562b8b205f4e35e25}"
WORKSPACE_DIR="${WORKSPACE_DIR:-$HOME/agent-workspace}"
TOOLBOX_PORT="${TOOLBOX_PORT:-8001}"
FILES_PORT="${FILES_PORT:-3081}"   # download links for created files (home network only)
# knowledge base of world news and market events since the model's training (news_kb.py)
KNOWLEDGE="${KNOWLEDGE:-on}"
KNOWLEDGE_START="${KNOWLEDGE_START:-2024-10}"
KNOWLEDGE_MAX_MINUTES="${KNOWLEDGE_MAX_MINUTES:-150}"

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
# The ask_planner tool uses the main Instruct model by default (fast, no separate download).
MODEL_PLANNER="${MODEL_PLANNER:-$MODEL_MAIN}"
# How much text the model can keep in mind at once (tokens). A big task (several web pages,
# stock data, a report) needs ~20k; when it doesn't fit, Ollama silently drops the start of
# the chat, including your request, and the agent loses track. 32k uses ~3 GB extra RAM.
if [ -z "${CONTEXT_LENGTH:-}" ]; then
  if [ "$RAM_GB" -ge 28 ]; then CONTEXT_LENGTH=32768; else CONTEXT_LENGTH=16384; fi
fi
# How long Ollama keeps a model in memory after its last use. -1 = forever (pinned), so the
# agent never waits for the 19 GB model to load again. Machines with less RAM free it after
# 30 minutes so other programs get the memory back.
if [ -z "${KEEP_ALIVE:-}" ]; then
  if [ "$RAM_GB" -ge 28 ]; then KEEP_ALIVE=-1; else KEEP_ALIVE=30m; fi
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
    if [ "$t" -le 0 ] || [ "$t" -ge 125 ]; then continue; fi
    type=$(cat "$z/type" 2>/dev/null)
    if [ "$type" = "x86_pkg_temp" ] || [ "$type" = "cpu-thermal" ]; then echo "$t"; return; fi
    [ -n "$best" ] || best="$t"
  done
  echo "$best"
}

# IP address of Docker's internal bridge (containers reach the host here)
# Ollama: install or update only when the version changes (its installer downloads ~1.5 GB
# and restarts Ollama every time). OLLAMA_VERSION (config/models.env) pins one, e.g. 0.12.6;
# empty = the newest release.
ollama_installed_version() { ollama --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | tail -1; }
ollama_latest_version() {
  curl -fsSLI -o /dev/null -w '%{url_effective}' https://github.com/ollama/ollama/releases/latest 2>/dev/null \
    | grep -oE '[0-9]+\.[0-9]+\.[0-9]+$'
}
install_ollama() {
  local want have
  want="${OLLAMA_VERSION:-$(ollama_latest_version)}"
  have="$(ollama_installed_version)"
  if [ -n "$have" ] && { [ -z "$want" ] || [ "$have" = "$want" ]; }; then
    ok "Ollama $have is already the right version (nothing to download)"
    return 0
  fi
  echo "Installing Ollama ${want:-(newest)}${have:+ (now $have)}"
  curl -fsSL https://ollama.com/install.sh | OLLAMA_VERSION="$want" sh
}

# Wait until Ollama answers (after an install or restart it takes a few seconds). 0 = ready.
wait_for_ollama() {
  local _
  for _ in $(seq 1 "${1:-60}"); do
    curl -fs http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}

docker_bridge_ip() {
  ip -4 addr show docker0 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1
}

# Copy the LibreChat config template, filling in the chosen model names.
# Get LibreChat's files at exactly LIBRECHAT_REF (new install, or an older install that
# followed LibreChat's main branch). Your .env and the files Dhoon writes there are kept.
librechat_checkout() {
  if [ ! -d "$LIBRECHAT_DIR/.git" ]; then
    git init -q "$LIBRECHAT_DIR"
    git -C "$LIBRECHAT_DIR" remote add origin "$LIBRECHAT_GIT_URL"
  fi
  [ "$(git -C "$LIBRECHAT_DIR" rev-parse -q --verify HEAD 2>/dev/null)" = "$LIBRECHAT_REF" ] && return 0
  git -C "$LIBRECHAT_DIR" fetch -q --depth 1 origin "$LIBRECHAT_REF"
  git -C "$LIBRECHAT_DIR" checkout -q --detach FETCH_HEAD
}

render_librechat_config() {
  local memory_off=true   # memory is off unless MEMORY=on (asked during install)
  [ "${MEMORY:-off}" = "on" ] && memory_off=false
  sed -e "s|__MODEL_MAIN__|$MODEL_MAIN|g" -e "s|__MODEL_FAST__|$MODEL_FAST|g" \
      -e "s|__MEMORY_DISABLED__|$memory_off|g" \
    "$KIT/config/librechat/librechat.yaml" > "$LIBRECHAT_DIR/librechat.yaml"
  cp "$KIT/config/librechat/docker-compose.override.yml" "$LIBRECHAT_DIR/docker-compose.override.yml"
  # Dhoon icons for the override above (copied as files, so Docker never makes folders)
  mkdir -p "$LIBRECHAT_DIR/branding"
  cp "$KIT"/config/branding/* "$LIBRECHAT_DIR/branding/"
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
      grep -q "OLLAMA_CONTEXT_LENGTH=$CONTEXT_LENGTH\"" "$SYSTEMD_ROOT/ollama.service.d/override.conf" \
        || { STATUS_REASON="Ollama settings out of date (context length)"; return 1; }
      grep -q "OLLAMA_KEEP_ALIVE=$KEEP_ALIVE\"" "$SYSTEMD_ROOT/ollama.service.d/override.conf" \
        || { STATUS_REASON="Ollama settings out of date (keep-alive)"; return 1; }
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
      if ! grep -qx "Environment=PLANNER_MODEL=$MODEL_PLANNER" "$SYSTEMD_ROOT/ai-toolbox.service" 2>/dev/null \
        || ! grep -q "^Environment=RESEARCH_ENGINES=" "$SYSTEMD_ROOT/ai-toolbox.service" 2>/dev/null \
        || ! grep -q "^Environment=TOOLBOX_LOG=" "$SYSTEMD_ROOT/ai-toolbox.service" 2>/dev/null \
        || ! grep -q "^EnvironmentFile=-$KIT/config/email.env" "$SYSTEMD_ROOT/ai-toolbox.service" 2>/dev/null; then
        STATUS_REASON="toolbox settings out of date"; return 1
      fi
      [ -f "$SYSTEMD_ROOT/ai-files.service" ] || { STATUS_REASON="download-link service missing"; return 1; }
      [ -f "$SYSTEMD_ROOT/ai-knowledge.timer" ] || { STATUS_REASON="knowledge-base timer missing"; return 1; }
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
