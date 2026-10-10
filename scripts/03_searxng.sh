#!/usr/bin/env bash
# Script 03: start SearXNG, your private web search engine.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

step "Script 03: starting SearXNG (private search)"

mkdir -p "$SEARXNG_DIR"
if [ ! -f "$SEARXNG_DIR/settings.yml" ]; then
  sed "s/CHANGE_ME/$(openssl rand -hex 32)/" "$KIT/config/searxng/settings.yml" > "$SEARXNG_DIR/settings.yml"
fi

docker rm -f searxng >/dev/null 2>&1 || true
docker run -d --name searxng --restart unless-stopped \
  -p 127.0.0.1:8888:8080 \
  -v "$SEARXNG_DIR:/etc/searxng" \
  searxng/searxng:latest

echo "Waiting for SearXNG to start..."
for _ in $(seq 1 30); do
  if curl -fs "http://127.0.0.1:8888/search?q=test&format=json" >/dev/null 2>&1; then
    ok "SearXNG is working"
    step "Script 03 done"
    next "bash scripts/04_toolbox.sh"
    exit 0
  fi
  sleep 2
done
fail "SearXNG did not answer. Show its log with:  docker logs searxng"
exit 1
