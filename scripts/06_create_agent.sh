#!/usr/bin/env bash
# Script 06: create the "Assistant" agent in LibreChat automatically
# (main Instruct model, all toolbox tools, File Search, instructions, starters).
# Re-run any time: it updates the existing agent instead of making a new one.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

step "Script 06: creating the Assistant agent"
if ! curl -fs http://localhost:3080 >/dev/null 2>&1; then
  fail "LibreChat is not running. Run: bash scripts/05_librechat.sh"; exit 1
fi
# An earlier attempt may have triggered LibreChat's 2-hour ban; lift it first if so.
bash "$KIT/scripts/unban.sh" --if-needed || warn "Could not check for LibreChat bans (continuing)"
# The Assistant runs on the main model; the Quick Assistant on the small fast one, so everyday
# questions answer in seconds even while the main model is busy (a newsroom draft, say).
python3 "$KIT/scripts/create_agent.py" --model "$MODEL_MAIN" --fast-model "$MODEL_FAST" "$@"
echo "$MODEL_MAIN $MODEL_FAST" > "$KIT/.agent-model"   # update.sh says when the agents need updating
step "Script 06 done"
next "open http://localhost:3080 -> Agents -> Assistant (docs/GUIDE.md step 9 has things to try)"
