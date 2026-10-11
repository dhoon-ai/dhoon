#!/usr/bin/env bash
# Show what happened during your recent chats, from every part of the system, and point
# out known problems (context too small, step limit reached, tool errors, crashes).
#
#   bash scripts/logs.sh            the last 60 minutes
#   bash scripts/logs.sh 15         the last 15 minutes
#   bash scripts/logs.sh --follow   watch live while you chat (Ctrl+C to stop)
#   bash scripts/logs.sh --save     also save everything to a file (check it before sharing:
#                                   it contains your questions and tool results)
#
# Where each log lives, if you want to look yourself:
#   tools the agent called   runtime/logs/toolbox.log   (or: journalctl -u ai-toolbox)
#   the AI model (Ollama)    journalctl -u ollama
#   chat app (LibreChat)     docker logs LibreChat, runtime/LibreChat/logs/error-*.log, debug-*.log
#   web search (SearXNG)     docker logs searxng
set -uo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

MINUTES=60
FOLLOW=false
SAVE=false
for arg in "$@"; do
  case "$arg" in
    --follow) FOLLOW=true ;;
    --save)   SAVE=true ;;
    ''|*[!0-9]*) echo "Usage: bash scripts/logs.sh [MINUTES] [--follow] [--save]"; exit 2 ;;
    *) MINUTES="$arg" ;;
  esac
done

TOOLBOX_LOG="${TOOLBOX_LOG:-$RUNTIME/logs/toolbox.log}"
LC_LOGS="$LIBRECHAT_DIR/logs"
SINCE_JOURNAL="$MINUTES min ago"
SINCE_DOCKER="${MINUTES}m"

if $FOLLOW; then
  step "Live logs: tool calls and model warnings (Ctrl+C to stop)"
  [ -f "$TOOLBOX_LOG" ] || warn "No toolbox log yet at $TOOLBOX_LOG (re-run scripts/04_toolbox.sh)"
  trap 'kill 0' INT TERM EXIT
  [ -f "$TOOLBOX_LOG" ] && tail -n 0 -F "$TOOLBOX_LOG" | sed 's/^/[tools]  /' &
  journalctl -u ollama -f -n 0 -o cat 2>/dev/null | grep --line-buffered -iE 'truncat|error|panic|out of memory|level=WARN' \
    | sed 's/^/[ollama] /' &
  docker logs -f --since 1s LibreChat 2>&1 | grep --line-buffered -iE 'error|recursion|limit|timeout' \
    | sed 's/^/[chat]   /' &
  wait
  exit 0
fi

# Last part of the toolbox log within the time window (lines start with "YYYY-mm-dd HH:MM:SS").
toolbox_recent() {
  [ -f "$TOOLBOX_LOG" ] || return 0
  local from
  from=$(date -d "$SINCE_JOURNAL" '+%Y-%m-%d %H:%M:%S')
  awk -v from="$from" 'substr($0,1,19) >= from' "$TOOLBOX_LOG"
}

report() {
  echo "Logs for the last $MINUTES minutes (generated $(date '+%Y-%m-%d %H:%M'))"

  step "1. Tool calls (toolbox)"
  if [ ! -f "$TOOLBOX_LOG" ]; then
    warn "No toolbox log at $TOOLBOX_LOG. Turn it on with: bash scripts/04_toolbox.sh"
    journalctl -u ai-toolbox --since "$SINCE_JOURNAL" -o cat --no-pager 2>/dev/null | tail -n 40
  else
    TOOLS=$(toolbox_recent)
    if [ -z "$TOOLS" ]; then echo "  (no tool calls)"; else echo "$TOOLS" | tail -n 80; fi
  fi

  step "2. AI model (Ollama) warnings and errors"
  OLLAMA=$(journalctl -u ollama --since "$SINCE_JOURNAL" -o cat --no-pager 2>/dev/null \
    | grep -iE 'truncat|error|panic|out of memory|level=WARN|signal' | tail -n 30)
  if [ -z "$OLLAMA" ]; then echo "  (nothing)"; else echo "$OLLAMA"; fi

  step "3. Chat app (LibreChat) errors"
  CHAT=$(docker logs --since "$SINCE_DOCKER" LibreChat 2>&1 | grep -iE 'error|recursion|limit|timeout|abort' | tail -n 30)
  if [ -z "$CHAT" ]; then echo "  (nothing)"; else echo "$CHAT"; fi
  local newest=""
  for f in "$LC_LOGS"/error-*.log; do
    [ -f "$f" ] && { [ -z "$newest" ] || [ "$f" -nt "$newest" ]; } && newest="$f"
  done
  if [ -n "$newest" ]; then
    echo "  --- newest lines of $newest ---"
    tail -n 15 "$newest"
  fi

  step "4. Web search (SearXNG) errors"
  SEARCH=$(docker logs --since "$SINCE_DOCKER" searxng 2>&1 | grep -iE 'error|exception|captcha|suspended|timeout' | tail -n 15)
  if [ -z "$SEARCH" ]; then echo "  (nothing)"; else echo "$SEARCH"; fi

  step "What this probably means"
  local found=false
  if echo "$OLLAMA" | grep -qi 'truncat'; then
    found=true
    fail "The chat got longer than the model's context window, so Ollama cut off the start"
    echo "         (including your request). The agent then loses track or stops."
    echo "         Fix: a bigger CONTEXT_LENGTH in config/models.env (now $CONTEXT_LENGTH),"
    echo "         then: bash scripts/ollama_settings.sh. Or start a new chat for each big task."
  fi
  if echo "$CHAT" | grep -qiE 'recursion|step limit|max.*steps'; then
    found=true
    fail "The agent hit LibreChat's step limit. Click 'Keep going' if it is shown, or raise"
    echo "         recursionLimit in config/librechat/librechat.yaml, then: bash scripts/05_librechat.sh"
  fi
  if echo "$OLLAMA" | grep -qiE 'out of memory|signal: killed|panic'; then
    found=true
    fail "The model crashed or ran out of memory. Close other programs, or use a smaller"
    echo "         CONTEXT_LENGTH or model (config/models.env)."
  fi
  if echo "${TOOLS:-}" | grep -q '^.\{20\}ERROR'; then
    found=true
    warn "Some tools failed (ERROR lines in section 1). The agent is told why and should"
    echo "         recover; if it stopped there, that tool is the place to look."
  fi
  if echo "$CHAT" | grep -qiE 'timeout|ETIMEDOUT|aborted'; then
    found=true
    warn "Something timed out (section 3). On a slow CPU, long tasks can take minutes per step."
  fi
  if [ -n "${TOOLS:-}" ]; then
    last=$(echo "$TOOLS" | tail -n 1)
    case "$last" in
      *" CALL  "*) found=true; warn "The last tool call never finished: ${last:20:120}" ;;
    esac
  fi
  $found || ok "No known problem found. If a chat still stopped, start a new chat and try again,"
  $found || echo "         or run: bash scripts/logs.sh --follow   while you repeat the question."
}

if $SAVE; then
  mkdir -p "$RUNTIME/logs"
  out="$RUNTIME/logs/report-$(date '+%Y%m%d-%H%M').txt"
  report | sed 's/\x1b\[[0-9;]*m//g' > "$out"
  report
  echo
  ok "Saved to $out (it contains your questions and tool results: check before sharing)"
else
  report
fi
