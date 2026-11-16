#!/usr/bin/env bash
# Prepare runtime/LibreChat for the chat stack's Podman units (config/librechat/podman), and
# optionally load saved chats into it.
#   bash scripts/librechat_podman_setup.sh                       folders, librechat.yaml, branding, admin-panel.env
#   bash scripts/librechat_podman_setup.sh --load-chats DIR      also load DIR/mongo.archive.gz (+ postgres.sql.gz)
#   bash scripts/librechat_podman_setup.sh --dump-chats DIR      write the chats to DIR (for backups)
# .env holds the site's secrets (logins break without the same ones): it comes from your backup,
# or from scripts/05_librechat.sh on a new install. This script never makes a new one.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"

load="" dump=""
while [ $# -gt 0 ]; do
  case "$1" in
    --load-chats) load="$2"; shift 2 ;;
    --dump-chats) dump="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

if [ -n "$dump" ]; then
  mkdir -p "$dump"
  podman exec chat-mongodb mongodump --archive --gzip --quiet >"$dump/mongo.archive.gz"
  podman exec vectordb pg_dump -U myuser mydatabase | gzip >"$dump/postgres.sql.gz"
  date -Is >"$dump/made-at.txt"
  ok "chats saved in $dump ($(du -sh "$dump" | cut -f1))"
  exit 0
fi

[ -f "$LIBRECHAT_DIR/.env" ] || { fail "$LIBRECHAT_DIR/.env is missing: restore it from your backup (or run scripts/05_librechat.sh on a new install)"; exit 1; }
mkdir -p "$LIBRECHAT_DIR"/{images,uploads,logs,skill,data-node,meili_data_v1.35.1}
render_librechat_config   # librechat.yaml (models from config/models.env) + branding/
secret=$(sed -n 's/^ADMIN_PANEL_SESSION_SECRET=//p' "$LIBRECHAT_DIR/.env" | head -1)
[ -n "$secret" ] || { fail "ADMIN_PANEL_SESSION_SECRET is not in $LIBRECHAT_DIR/.env"; exit 1; }
( umask 077; printf 'SESSION_SECRET=%s\n' "$secret" >"$LIBRECHAT_DIR/admin-panel.env" )
chmod 600 "$LIBRECHAT_DIR/.env"
ok "runtime/LibreChat is ready for the Podman units"

if [ -n "$load" ]; then
  [ -f "$load/mongo.archive.gz" ] || { fail "no mongo.archive.gz in $load"; exit 1; }
  for _ in $(seq 1 60); do podman exec chat-mongodb mongosh --quiet --eval 'db.runCommand({ping:1}).ok' >/dev/null 2>&1 && break; sleep 2; done
  podman exec -i chat-mongodb mongorestore --archive --gzip --drop --quiet <"$load/mongo.archive.gz"
  ok "chats loaded from $load/mongo.archive.gz"
  if [ -f "$load/postgres.sql.gz" ]; then
    for _ in $(seq 1 60); do podman exec vectordb pg_isready -U myuser >/dev/null 2>&1 && break; sleep 2; done
    gunzip -c "$load/postgres.sql.gz" | podman exec -i vectordb psql -q -U myuser -d mydatabase >/dev/null
    ok "document search index loaded from $load/postgres.sql.gz"
  fi
fi
