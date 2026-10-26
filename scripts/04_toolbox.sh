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
# read_receipts (optional receipt-reader package): its model and your usual currency, from config/models.env
Environment=RECEIPT_MODEL=${RECEIPT_MODEL:-}
Environment=RECEIPT_CURRENCY=${RECEIPT_CURRENCY:-}
Environment=RECEIPT_FAST=${RECEIPT_FAST:-}
# search_document(s): the optional reranker (pip install fastembed; see config/models.env.example)
Environment=DOC_RERANK=${DOC_RERANK:-}
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
Environment=RECEIPT_MODEL=${RECEIPT_MODEL:-}
Environment=RECEIPT_CURRENCY=${RECEIPT_CURRENCY:-}
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

# 4f. download links for files the Assistant makes (Excel, Word, charts): the file name in
# the chat opens the file. Links are signed, work only for files in the workspace and expire
# after 7 days; anything else gets "not found". Home network only: never forward this port.
sudo tee /etc/systemd/system/ai-files.service >/dev/null <<EOF
[Unit]
Description=Download links for files the Assistant makes
After=network-online.target

[Service]
User=$USER
WorkingDirectory=$KIT/toolbox
Environment=WORKSPACE=$WORKSPACE_DIR
Environment=FILES_PORT=$FILES_PORT
ExecStart=$RUNTIME/venv/bin/python $KIT/toolbox/fileshare.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 4g. knowledge base of world news and market events since the AI model's training: every
# night at 22:30 it adds the missing weeks (newest first, at most KNOWLEDGE_MAX_MINUTES), so
# the catch-up finishes over a few nights; after that it only adds last week. See news_kb.py.
sudo tee /etc/systemd/system/ai-knowledge.service >/dev/null <<EOF
[Unit]
Description=Add the missing weeks to the world news and markets knowledge base
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
Environment=KNOWLEDGE_START=$KNOWLEDGE_START
Environment=KNOWLEDGE_MAX_MINUTES=$KNOWLEDGE_MAX_MINUTES
ExecStart=$RUNTIME/venv/bin/python $KIT/toolbox/news_kb.py --update
TimeoutStartSec=4h
Nice=10
EOF
sudo tee /etc/systemd/system/ai-knowledge.timer >/dev/null <<EOF
[Unit]
Description=Every night: add the missing weeks to the world news and markets knowledge base

[Timer]
OnCalendar=*-*-* 22:30:00

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ai-toolbox
if [ "$KNOWLEDGE" = "off" ]; then
  sudo systemctl disable --now ai-knowledge.timer >/dev/null 2>&1 || true
else
  sudo systemctl enable --now ai-knowledge.timer >/dev/null 2>&1 || true
fi
sudo systemctl enable ai-files >/dev/null 2>&1
sudo systemctl restart ai-files
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
