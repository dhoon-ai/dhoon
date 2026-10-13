#!/usr/bin/env bash
# Health check. Run any time something seems broken:  bash scripts/check.sh
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

step "AI system health check"
problems=0

if curl -fs http://127.0.0.1:11434/api/tags >/dev/null; then ok "Ollama is running"
else fail "Ollama  ->  sudo systemctl restart ollama"; problems=$((problems+1)); fi
models=$(ollama list 2>/dev/null | awk 'NR>1{print $1}' | tr '\n' ' ')
echo "         models: ${models:-none}"
for m in "$MODEL_MAIN" "$MODEL_FAST" "$MODEL_EMBED" "$MODEL_PLANNER"; do
  echo " $models " | grep -q " ${m}\(:latest\)\? " || { warn "model $m missing  ->  ollama pull $m"; }
done
case "$MODEL_MAIN" in
  qwen3:*instruct*|qwen3-*instruct*) ;;
  qwen3*) warn "$MODEL_MAIN is a Qwen3 thinking model; it often fails to call tools. Use an instruct-2507 model (docs/GUIDE.md)";;
esac

loaded=$(ollama ps 2>/dev/null | awk 'NR>1{u="?"; if ($0 ~ /Forever/) u="Forever"; else if (match($0, /[^ ]+ [^ ]+ from now/)) u=substr($0, RSTART, RLENGTH); print $1" (until: "u")"}' \
  | paste -sd ',' - | sed 's/,/, /g')
echo "         loaded in memory now: ${loaded:-none}   (keep-alive setting: $KEEP_ALIVE)"
if [ -z "$loaded" ] && [ "$KEEP_ALIVE" = "-1" ]; then
  warn "main model not loaded  ->  bash scripts/preload_model.sh"
fi
if [ -f /etc/systemd/system/ollama.service.d/cpu-only.conf ]; then
  echo "         Ollama mode: CPU only (set by scripts/benchmark.sh; undo with --reset)"
else
  echo "         Ollama mode: default (uses a supported GPU if present; test with scripts/benchmark.sh)"
fi

if curl -fs "http://127.0.0.1:8888/search?q=test&format=json" >/dev/null; then ok "SearXNG search works"
else fail "SearXNG  ->  docker restart searxng ; docker logs searxng"; problems=$((problems+1)); fi

BRIDGE_IP=$(docker_bridge_ip)
code=$(curl -s -o /dev/null -w "%{http_code}" "http://${BRIDGE_IP:-127.0.0.1}:$TOOLBOX_PORT/mcp")
if [ "$code" != "000" ]; then ok "Toolbox answers on ${BRIDGE_IP}:$TOOLBOX_PORT"
else fail "Toolbox  ->  sudo systemctl restart ai-toolbox ; journalctl -u ai-toolbox -n 50"; problems=$((problems+1)); fi
if curl -fs "http://${BRIDGE_IP:-127.0.0.1}:$TOOLBOX_PORT/ollama/api/tags" >/dev/null 2>&1; then
  ok "Chat website -> toolbox -> Ollama connection works"
else fail "The toolbox can't pass chats to Ollama  ->  bash scripts/restart.sh"; problems=$((problems+1)); fi
# a made-up link must get "not found" (404): the service runs and refuses unsigned links
code=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$FILES_PORT/f/0/x/x")
if [ "$code" = "404" ]; then ok "Download links for created files work (port $FILES_PORT, home network only)"
else warn "Download links for created files are off  ->  bash scripts/04_toolbox.sh"; fi
weeks=$(find "$WORKSPACE_DIR/knowledge/weeks" -name '*.md' 2>/dev/null | wc -l)
if [ "$KNOWLEDGE" = "off" ]; then echo "         knowledge base of world news and markets: off (KNOWLEDGE=off)"
elif systemctl is-active --quiet ai-knowledge.timer 2>/dev/null; then
  ok "Knowledge base of world news and markets: $weeks weeks so far, updated every night (bash scripts/knowledge.sh)"
else warn "Knowledge base of world news and markets is not scheduled  ->  bash scripts/04_toolbox.sh"; fi
if systemctl is-active --quiet ai-scheduler.timer 2>/dev/null; then
  n=$(grep -o '"active": true' "$RUNTIME/schedule.json" 2>/dev/null | wc -l)
  ok "Scheduled tasks: checked every minute ($n active)"
else warn "Scheduled tasks are off  ->  bash scripts/04_toolbox.sh"; fi
if ls "$RUNTIME"/warm/*.json >/dev/null 2>&1; then
  echo "         fast first answer: the Assistant's instructions are read in advance (bash scripts/preload_model.sh)"
else
  echo "         fast first answer: starts after your first chat with the Assistant"
fi

if docker image inspect agent-sandbox >/dev/null 2>&1; then ok "Code sandbox image exists"
else fail "Sandbox image missing  ->  bash scripts/04_toolbox.sh"; problems=$((problems+1)); fi

if curl -fs http://localhost:3080 >/dev/null; then ok "LibreChat web UI is up (http://localhost:3080)"
else fail "LibreChat  ->  cd $LIBRECHAT_DIR && docker compose up -d"; problems=$((problems+1)); fi

# Optional settings (docs/GUIDE.md "After installing: your settings"); never counted as problems.
setting() { sed -n "s/^$1=//p" "$2" 2>/dev/null | tail -1 | tr -d '"'"'"; }
echo
echo "  Your settings:"
if [ -f "$KIT/config/email.env" ]; then
  echo "    e-mail:     $(setting EMAIL_PROVIDER "$KIT/config/email.env"), only to: $(setting EMAIL_ALLOWED_TO "$KIT/config/email.env")   (test: bash scripts/email_test.sh)"
else echo "    e-mail:     not set up (config/email.env)"; fi
cal="private calendar"
if [ -f "$KIT/config/calendar.env" ]; then
  [ -n "$(setting CALENDAR_ICS_URLS "$KIT/config/calendar.env")" ] && cal+=", calendar links"
  [ -n "$(setting CALDAV_URL "$KIT/config/calendar.env")" ] && cal+=", CalDAV"
fi
[ -f "$RUNTIME/google_token.json" ] && cal+=", Google (read and write)"
echo "    calendars:  $cal"
if [ -f "$KIT/config/models.env" ] && grep -q '^[A-Z]' "$KIT/config/models.env"; then
  echo "    models:     your choices in config/models.env"
else echo "    models:     chosen from your RAM (change in config/models.env)"; fi
if [ -f "$HOME/.config/ai-kit-backup/backup.env" ]; then
  if systemctl is-active --quiet ai-backup.timer 2>/dev/null; then
    echo "    backup:     set up, daily$( [ -f "$KIT/runtime/chat-backup/librechat.archive.gz" ] && echo ", chats included")"
  else echo "    backup:     set up, but not scheduled  ->  bash scripts/backup.sh --setup"; fi
else echo "    backup:     not set up (bash scripts/backup.sh --setup)"; fi
if [ "${MEMORY:-off}" = "on" ]; then echo "    memory:     on (each person can switch it off in Settings -> Personalization)"
else echo "    memory:     off (switch on: MEMORY=on in config/models.env, then bash scripts/05_librechat.sh)"; fi
reg=$(setting ALLOW_REGISTRATION "$LIBRECHAT_DIR/.env")
if [ "$reg" = "false" ]; then echo "    sign-up:    locked"
else echo "    sign-up:    open to anyone on your network (lock it: docs/GUIDE.md \"Safety\")"; fi
plugins=$(find "$KIT/plugins" -mindepth 1 -maxdepth 1 -type d ! -name '__pycache__' -printf '%f ' 2>/dev/null)
echo "    plugins:    ${plugins:-none}"

echo
echo "  Memory: $(free -h | awk '/Mem:/{print $3" used of "$2}')"
echo "  Disk /: $(df -h / | awk 'NR==2{print $4" free"}')"
mountpoint -q /data && echo "  Disk /data: $(df -h /data | awk 'NR==2{print $4" free"}')"
t=$(cpu_temp); [ -n "$t" ] && echo "  CPU temp: ${t} C"

echo
if [ "$problems" -eq 0 ]; then ok "Everything looks healthy"; else fail "$problems problem(s) found - see above"; fi
exit "$problems"
