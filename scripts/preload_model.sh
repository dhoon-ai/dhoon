#!/usr/bin/env bash
# Load the main model into memory now and have it read the Assistant's instructions in
# advance (toolbox/warmup.py). Runs automatically after Ollama starts and every night after
# midnight (ai-model-preload service and ai-warmup timer); safe to run by hand. The model
# stays loaded for KEEP_ALIVE (-1 = until Ollama stops). Check with: ollama ps
set -uo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
for _ in $(seq 1 60); do
  curl -fs "$OLLAMA_URL/api/tags" >/dev/null 2>&1 && break
  sleep 2
done
case "$KEEP_ALIVE" in
  ''|*[!0-9-]*) keep="\"$KEEP_ALIVE\"" ;;   # e.g. "30m"
  *)            keep="$KEEP_ALIVE" ;;       # a number of seconds, or -1 = forever
esac
echo "Loading $MODEL_MAIN into memory (keep-alive: $KEEP_ALIVE)..."
# An empty prompt only loads the model.
if curl -fsS --max-time 900 "$OLLAMA_URL/api/generate" \
     -d "{\"model\": \"$MODEL_MAIN\", \"keep_alive\": $keep}" >/dev/null; then
  ok "$MODEL_MAIN is loaded"
  ollama ps 2>/dev/null || true
  # Also have it read the Assistant's instructions and tool list in advance (saved by the
  # toolbox from your last chat), so the first answer starts in seconds, not minutes.
  py="$RUNTIME/venv/bin/python"; [ -x "$py" ] || py=python3
  "$py" "$KIT/toolbox/warmup.py" || true
else
  fail "Could not load $MODEL_MAIN (is it downloaded? ollama list)"
  exit 1
fi
