#!/usr/bin/env bash
# Script 04: build the code sandbox and run the toolbox (the agent's tools) as a service.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

step "Script 04: setting up the agent toolbox"

# 4a. locked-down Python sandbox image
docker build -t agent-sandbox "$KIT/sandbox"

# 4b. Python environment for the toolbox server
python3 -m venv "$RUNTIME/venv"
"$RUNTIME/venv/bin/pip" install --upgrade pip
"$RUNTIME/venv/bin/pip" install -r "$KIT/toolbox/requirements.txt"

# 4c. the only folder the agent may read/write
mkdir -p "$WORKSPACE_DIR"
# e-mail settings hold a password: readable only by you
[ -f "$KIT/config/email.env" ] && chmod 600 "$KIT/config/email.env"

# 4d. listen only on Docker's internal address (containers can reach it, your LAN cannot)
BRIDGE_IP=$(docker_bridge_ip)
if [ -z "$BRIDGE_IP" ]; then fail "Docker bridge (docker0) not found. Is Docker running?"; exit 1; fi
echo "Docker bridge address: $BRIDGE_IP"

sudo tee /etc/systemd/system/ai-toolbox.service >/dev/null <<EOF
[Unit]
Description=AI agent toolbox (MCP server)
After=docker.service network-online.target
Requires=docker.service

[Service]
User=$USER
WorkingDirectory=$KIT/toolbox
Environment=BIND_HOST=$BRIDGE_IP
Environment=PORT=$TOOLBOX_PORT
Environment=WORKSPACE=$WORKSPACE_DIR
Environment=SEARXNG_URL=http://127.0.0.1:8888
Environment=OLLAMA_URL=http://127.0.0.1:11434
Environment=PLANNER_MODEL=$MODEL_PLANNER
# every tool call is logged here (see: bash scripts/logs.sh)
Environment=TOOLBOX_LOG=$RUNTIME/logs/toolbox.log
# e-mail settings for the send_email tool (optional; see config/email.env.example)
EnvironmentFile=-$KIT/config/email.env
# ask_planner can research on the web how a hard task is usually done (generic question
# only, personal details removed). Empty = your SearXNG's usual engines. Choose your own in
# config/models.env, e.g. RESEARCH_ENGINES="startpage,duckduckgo,brave"
Environment=RESEARCH_ENGINES=${RESEARCH_ENGINES:-}
ExecStart=$RUNTIME/venv/bin/python $KIT/toolbox/toolbox_server.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 4e. scheduled tasks and reminders the Assistant sets up for you (schedule_task tool):
# every minute, toolbox/scheduler.py does whatever is due (usually nothing, in a split second)
sudo tee /etc/systemd/system/ai-scheduler.service >/dev/null <<EOF
[Unit]
Description=Run the Assistant's scheduled tasks that are due
After=network-online.target ollama.service

[Service]
Type=oneshot
User=$USER
WorkingDirectory=$KIT/toolbox
Environment=WORKSPACE=$WORKSPACE_DIR
Environment=SEARXNG_URL=http://127.0.0.1:8888
Environment=OLLAMA_URL=http://127.0.0.1:11434
Environment=PLANNER_MODEL=$MODEL_PLANNER
Environment=TOOLBOX_LOG=$RUNTIME/logs/toolbox.log
EnvironmentFile=-$KIT/config/email.env
ExecStart=$RUNTIME/venv/bin/python $KIT/toolbox/scheduler.py --run-due
TimeoutStartSec=2h
Nice=5
EOF
sudo tee /etc/systemd/system/ai-scheduler.timer >/dev/null <<EOF
[Unit]
Description=Check every minute for scheduled tasks that are due

[Timer]
OnCalendar=*-*-* *:*:00
AccuracySec=1s

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ai-toolbox
sudo systemctl enable --now ai-scheduler.timer >/dev/null 2>&1 || true
sudo systemctl restart ai-toolbox
sleep 4
if systemctl is-active --quiet ai-toolbox; then
  ok "Toolbox is running on http://$BRIDGE_IP:$TOOLBOX_PORT/mcp"
  echo 'import pandas; print("sandbox OK, pandas", pandas.__version__)' | \
    docker run --rm -i --network none agent-sandbox python -
  step "Script 04 done"
  next "bash scripts/05_librechat.sh"
else
  fail "Toolbox failed to start. See why with:  journalctl -u ai-toolbox -n 50"
  exit 1
fi
