#!/usr/bin/env bash
# Restart the whole system in the right order, then run the health check.
#
#   bash scripts/restart.sh
#
# You don't need this after a reboot: everything starts by itself (see "After a reboot" in
# docs/GUIDE.md). Use it when something is stuck and you don't want to reboot the computer.
# Your chats, files, models and settings are kept.
set -uo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

has_unit() { [ -n "$(systemctl list-unit-files "$1" --no-legend 2>/dev/null)" ]; }

step "Restarting the AI system"

sudo systemctl restart ollama
for _ in $(seq 1 60); do
  curl -fs http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
  sleep 2
done
if curl -fs http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then ok "Ollama (the AI models)"
else fail "Ollama did not come back: journalctl -u ollama -n 50"; fi

if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx searxng; then
  docker restart searxng >/dev/null && ok "SearXNG (private search)"
else
  warn "SearXNG is not installed: bash scripts/03_searxng.sh"
fi

if [ -d "$LIBRECHAT_DIR" ]; then
  if (cd "$LIBRECHAT_DIR" && docker compose restart >/dev/null 2>&1); then ok "LibreChat (the chat website)"
  else fail "LibreChat did not restart: cd $LIBRECHAT_DIR && docker compose up -d"; fi
else
  warn "LibreChat is not installed: bash scripts/05_librechat.sh"
fi

if has_unit ai-toolbox.service; then
  sudo systemctl restart ai-toolbox && ok "Toolbox (the agent's tools and plugins)"
else
  warn "The toolbox is not installed: bash scripts/04_toolbox.sh"
fi

# load the main model in the background (takes a minute or two; chats work meanwhile, slower)
if has_unit ai-model-preload.service; then
  sudo systemctl restart --no-block ai-model-preload && ok "Loading $MODEL_MAIN into memory in the background"
fi

echo
echo "Scheduled jobs (they keep their times; a run missed while the computer was off runs soon after start):"
systemctl list-timers 'ai-*' --no-pager 2>/dev/null | sed 's/^/  /' | head -10

echo
echo "Giving the chat website a few seconds to start..."
sleep "${RESTART_WAIT:-15}"
[ "${RESTART_SKIP_CHECK:-0}" = 1 ] || bash "$(dirname "$0")/check.sh"
