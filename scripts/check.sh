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

if docker image inspect agent-sandbox >/dev/null 2>&1; then ok "Code sandbox image exists"
else fail "Sandbox image missing  ->  bash scripts/04_toolbox.sh"; problems=$((problems+1)); fi

if curl -fs http://localhost:3080 >/dev/null; then ok "LibreChat web UI is up (http://localhost:3080)"
else fail "LibreChat  ->  cd $LIBRECHAT_DIR && docker compose up -d"; problems=$((problems+1)); fi

echo
echo "  Memory: $(free -h | awk '/Mem:/{print $3" used of "$2}')"
echo "  Disk /: $(df -h / | awk 'NR==2{print $4" free"}')"
mountpoint -q /data && echo "  Disk /data: $(df -h /data | awk 'NR==2{print $4" free"}')"
[ -r /sys/class/thermal/thermal_zone0/temp ] && \
  echo "  CPU temp: $(( $(cat /sys/class/thermal/thermal_zone0/temp) / 1000 )) C"

echo
if [ "$problems" -eq 0 ]; then ok "Everything looks healthy"; else fail "$problems problem(s) found - see above"; fi
exit "$problems"
