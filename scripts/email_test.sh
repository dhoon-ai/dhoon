#!/usr/bin/env bash
# Send a test e-mail to yourself with the settings in config/email.env.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

step "E-mail test"
if [ ! -f "$KIT/config/email.env" ]; then
  fail "No config/email.env yet. Run: cp config/email.env.example config/email.env  and fill it in."
  exit 1
fi
chmod 600 "$KIT/config/email.env"   # it holds a password: readable only by you
set -a
# shellcheck source=/dev/null
source "$KIT/config/email.env"
set +a
if "$RUNTIME/venv/bin/python" "$KIT/toolbox/emailer.py"; then
  ok "Check your inbox (and the spam folder)."
  if ! grep -q "^EnvironmentFile=-$KIT/config/email.env" /etc/systemd/system/ai-toolbox.service 2>/dev/null; then
    next "bash scripts/04_toolbox.sh   (so the agent's send_email tool uses these settings)"
  else
    sudo systemctl restart ai-toolbox && ok "Toolbox restarted with the new e-mail settings."
  fi
else
  exit 1
fi
