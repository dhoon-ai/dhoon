#!/usr/bin/env bash
# The knowledge base of world news and market events since the AI model's training
# (weekly notes in ~/agent-workspace/knowledge, written by toolbox/news_kb.py).
#
#   bash scripts/knowledge.sh                   what it has so far
#   bash scripts/knowledge.sh --catch-up        write every missing week now (a few hours;
#                                               stop with Ctrl+C, it continues where it stopped)
#   bash scripts/knowledge.sh --preview 2025-W10   what Wikipedia has for one week (no AI, saves nothing)
#   bash scripts/knowledge.sh --week 2025-W10      write one week again
#   bash scripts/knowledge.sh --check-all          fact-check notes not checked yet (rewrites failing ones)
#   bash scripts/knowledge.sh --check 2025-W10     your spot check: each summary line against its source
#   bash scripts/knowledge.sh --quiz               20 masked facts, scored by code (also monthly by itself)
#
# Every night at 22:30 the ai-knowledge timer adds missing weeks by itself.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

export WORKSPACE="$WORKSPACE_DIR" PLANNER_MODEL="$MODEL_PLANNER" KNOWLEDGE KNOWLEDGE_START KNOWLEDGE_MAX_MINUTES
export OLLAMA_URL="http://127.0.0.1:11434" SEARXNG_URL="http://127.0.0.1:8888"
[ $# -gt 0 ] || set -- --status
if [ "$1" = "--catch-up" ]; then
  echo "Writing every missing week since $KNOWLEDGE_START: about 2-4 minutes a week on a CPU."
  echo "Chats are slower meanwhile. Stop any time with Ctrl+C; run this again to continue."
fi
exec "$RUNTIME/venv/bin/python" "$KIT/toolbox/news_kb.py" "$@"
