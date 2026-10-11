#!/usr/bin/env bash
# Script 05: install LibreChat (the web chat UI) and connect it to Ollama and the toolbox.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

step "Script 05: installing LibreChat"

if [ ! -d "$LIBRECHAT_DIR/.git" ]; then
  git clone --depth 1 https://github.com/danny-avila/LibreChat.git "$LIBRECHAT_DIR"
fi
cd "$LIBRECHAT_DIR"
[ -f .env ] || cp .env.example .env

# set KEY=VALUE in .env (replaces an existing or commented-out line, else appends)
set_env() {
  local key="$1" val="$2"
  if grep -qE "^#? *${key}=" .env; then
    sed -i -E "s|^#? *${key}=.*|${key}=${val}|" .env
  else
    echo "${key}=${val}" >> .env
  fi
}
# set only if empty, so secrets survive re-runs (otherwise logins break)
set_secret() { grep -qE "^$1=.+" .env || set_env "$1" "$2"; }

set_env UID "$(id -u)"
set_env GID "$(id -g)"
set_secret CREDS_KEY "$(openssl rand -hex 32)"
set_secret CREDS_IV "$(openssl rand -hex 16)"
set_secret JWT_SECRET "$(openssl rand -hex 32)"
set_secret JWT_REFRESH_SECRET "$(openssl rand -hex 32)"
set_secret MEILI_MASTER_KEY "$(openssl rand -hex 16)"
set_secret ADMIN_PANEL_SESSION_SECRET "$(openssl rand -hex 32)"
# document search uses your local embedding model instead of OpenAI
set_env EMBEDDINGS_PROVIDER ollama
set_env EMBEDDINGS_MODEL "$MODEL_EMBED"
set_env OLLAMA_BASE_URL http://host.docker.internal:11434
# single-machine install: lets LibreChat's scheduled tasks feature start
set_env SCHEDULES_SINGLE_PROCESS true

render_librechat_config

# LibreChat's containers run as your user (UID/GID above). If Docker creates the
# folders they write to, they end up owned by root and LibreChat crashes with
# "EACCES: permission denied". So create them ourselves and make sure you own them.
mapfile -t BIND_DIRS < <(grep -oE '^\s*- \./[A-Za-z0-9._-]+:' docker-compose.yml | sed -E 's/^\s*- \.\/(.*):$/\1/' | sort -u)
for d in "${BIND_DIRS[@]}"; do
  [ "$d" = ".env" ] || [ -f "$d" ] || mkdir -p "$d"
done
sudo chown -R "$(id -u):$(id -g)" "${BIND_DIRS[@]}" 2>/dev/null || true

docker compose pull
docker compose up -d

IP=$(hostname -I | awk '{print $1}')
echo "Waiting for LibreChat (first start can take 1-2 minutes)..."
for _ in $(seq 1 60); do
  if curl -fs http://localhost:3080 >/dev/null 2>&1; then
    step "Script 05 done!"
    echo "Open on this computer:          http://localhost:3080"
    echo "Or from another device at home: http://$IP:3080"
    echo "Click 'Sign up' to create your account (the first account becomes admin)."
    next "bash scripts/check.sh   then follow docs/GUIDE.md step 7"
    exit 0
  fi
  sleep 3
done
fail "LibreChat is not answering yet. Check:  cd $LIBRECHAT_DIR && docker compose logs api --tail 50"
exit 1
