#!/usr/bin/env bash
# The toolbox's background jobs as systemd USER units (no sudo), for a Podman install:
# scheduled tasks, download links, the knowledge base, model preload/warm-up, the daily health
# e-mail and a failure e-mail for every ai-* user service. (scripts/04_toolbox.sh and
# ollama_settings.sh write the same jobs as system units for a Docker install.)
#   bash scripts/install_user_units.sh [--dest DIR]     default: $XDG_CONFIG_HOME/systemd/user
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
need_path WORKSPACE_DIR
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
[ "${1:-}" = "--dest" ] && UNIT_DIR="$2"
mkdir -p "$UNIT_DIR" "$RUNTIME/logs"
PY="$RUNTIME/venv/bin/python"

unit() { cat >"$UNIT_DIR/$1"; }   # unit NAME <<EOF ... EOF

unit ai-scheduler.service <<EOF
[Unit]
Description=Run the Assistant's scheduled tasks that are due
After=ollama.service

[Service]
Type=oneshot
WorkingDirectory=$KIT/toolbox
Environment=WORKSPACE=$WORKSPACE_DIR
Environment=SEARXNG_URL=http://127.0.0.1:8888
Environment=OLLAMA_URL=http://127.0.0.1:11434
Environment=PLANNER_MODEL=$MODEL_PLANNER
Environment=MODEL_FALLBACK=$MODEL_MAIN
Environment=TOOLBOX_LOG=$RUNTIME/logs/toolbox.log
Environment=RECEIPT_MODEL=${RECEIPT_MODEL:-}
Environment=RECEIPT_CURRENCY=${RECEIPT_CURRENCY:-}
EnvironmentFile=-$KIT/config/email.env
ExecStart=$PY $KIT/toolbox/scheduler.py --run-due
TimeoutStartSec=2h
Nice=5
EOF
unit ai-scheduler.timer <<EOF
[Unit]
Description=Check every minute for scheduled tasks that are due

[Timer]
OnCalendar=*-*-* *:*:00
AccuracySec=1s

[Install]
WantedBy=timers.target
EOF

# download links for files the Assistant makes (signed, workspace only, 7 days; home network only)
unit ai-files.service <<EOF
[Unit]
Description=Download links for files the Assistant makes

[Service]
WorkingDirectory=$KIT/toolbox
Environment=WORKSPACE=$WORKSPACE_DIR
Environment=FILES_PORT=$FILES_PORT
ExecStart=$PY $KIT/toolbox/fileshare.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

unit ai-knowledge.service <<EOF
[Unit]
Description=Add the missing weeks to the world news and markets knowledge base
After=ollama.service

[Service]
Type=oneshot
WorkingDirectory=$KIT/toolbox
Environment=WORKSPACE=$WORKSPACE_DIR
Environment=SEARXNG_URL=http://127.0.0.1:8888
Environment=OLLAMA_URL=http://127.0.0.1:11434
Environment=PLANNER_MODEL=$MODEL_PLANNER
Environment=MODEL_FALLBACK=$MODEL_MAIN
Environment=TOOLBOX_LOG=$RUNTIME/logs/toolbox.log
Environment=KNOWLEDGE_START=$KNOWLEDGE_START
Environment=KNOWLEDGE_MAX_MINUTES=$KNOWLEDGE_MAX_MINUTES
ExecStart=$PY $KIT/toolbox/news_kb.py --update
TimeoutStartSec=4h
Nice=10
EOF
unit ai-knowledge.timer <<EOF
[Unit]
Description=Every night: add the missing weeks to the world news and markets knowledge base

[Timer]
OnCalendar=*-*-* 23:15:00

[Install]
WantedBy=timers.target
EOF

# load the main model whenever Ollama (a user service here) starts, and again after midnight
unit ai-model-preload.service <<EOF
[Unit]
Description=Load the main AI model into memory after Ollama starts
After=ollama.service
PartOf=ollama.service

[Service]
Type=oneshot
ExecStart=/bin/bash $KIT/scripts/preload_model.sh
TimeoutStartSec=1800

[Install]
WantedBy=ollama.service
EOF
unit ai-warmup.timer <<EOF
[Unit]
Description=Have the model read the Assistant's instructions in advance (new date)

[Timer]
OnCalendar=*-*-* 00:35
Unit=ai-model-preload.service

[Install]
WantedBy=timers.target
EOF

# daily health e-mail (08:35) and one e-mail when an ai-* service fails
unit ai-health-check.service <<EOF
[Unit]
Description=Daily AI health check

[Service]
Type=oneshot
Environment=EMAIL_RETRY_ATTEMPTS=3
EnvironmentFile=-$KIT/config/email.env
ExecStart=$PY $KIT/scripts/health-check.py
EOF
unit ai-health-check.timer <<EOF
[Unit]
Description=Daily AI health check at 08:35

[Timer]
OnCalendar=*-*-* 08:35:00
Persistent=true

[Install]
WantedBy=timers.target
EOF
unit ai-notify-failure@.service <<EOF
[Unit]
Description=Failure alert for %i

[Service]
Type=oneshot
Environment=EMAIL_RETRY_ATTEMPTS=3
EnvironmentFile=-$KIT/config/email.env
ExecStart=$PY $KIT/scripts/notify-failure.py --user --unit %i
EOF
for svc in "$UNIT_DIR"/ai-*.service; do
  [ "$(basename "$svc")" = "ai-notify-failure@.service" ] && continue
  mkdir -p "$svc.d"
  printf '[Unit]\nOnFailure=ai-notify-failure@%%n.service\n' >"$svc.d/onfailure.conf"
done

# what the health e-mail watches (unit, hours since its last good run): new defaults are added,
# lines already there (your edits) are kept
health="${XDG_CONFIG_HOME:-$HOME/.config}/ai-alerts/health.env"
mkdir -p "$(dirname "$health")"
[ -f "$health" ] || echo "# ai-health-check: unit and maximum age (hours) since its last good run (written by install_user_units.sh)" >"$health"
added=0
while read -r u hours; do
  [ -f "$UNIT_DIR/$u.service" ] || continue
  grep -qE "^user $u\.service[[:space:]]" "$health" && continue
  echo "user $u.service $hours" >>"$health"; added=$((added + 1))
done <<'LIST'
ai-newsroom 26
ai-newsroom-west 26
ai-newsroom-usa 26
ai-newsroom-usa-west 26
ai-newsroom-australia 26
ai-newsroom-india 26
ai-newsroom-europe 26
ai-newsroom-europe-east 26
ai-newsroom-east-asia 26
ai-newsroom-southeast-asia 26
ai-newsroom-latin-america 26
ai-newsroom-middle-east-africa 26
ai-newsroom-middle-east-africa-gulf 26
ai-newsroom-recheck 26
ai-newsroom-publish 26
ai-newsroom-podcast 26
ai-newsroom-push 2
ai-newsroom-world 170
ai-newsroom-audit 170
ai-studio-site 26
ai-studio-guide 74
ai-knowledge 26
ai-mentor 50
ai-mentor-india 74
ai-mentor-morning 26
restore-backup 26
LIST
ok "health check list: $health ($added added)"

if [ -z "${1:-}" ]; then
  systemctl --user daemon-reload
  systemctl --user enable --now ai-scheduler.timer ai-warmup.timer ai-health-check.timer >/dev/null
  if [ "$KNOWLEDGE" = "off" ]; then systemctl --user disable --now ai-knowledge.timer >/dev/null 2>&1 || true
  else systemctl --user enable --now ai-knowledge.timer >/dev/null; fi
  systemctl --user enable ai-model-preload.service >/dev/null
  systemctl --user enable --now ai-files.service >/dev/null
  ok "Toolbox jobs installed as user units in $UNIT_DIR"
fi
