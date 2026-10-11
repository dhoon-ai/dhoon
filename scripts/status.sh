#!/usr/bin/env bash
# Show which install steps are already done, and what to run next. Changes nothing.
#   bash scripts/status.sh
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

step "Install status"
first_todo=""
for n in 1 2 3 4 5 6; do
  if step_done "$n"; then
    ok "$n. ${STEP_NAMES[$n]}"
  else
    printf '  \033[1;33m[TODO]\033[0m %s. %s  (%s)\n' "$n" "${STEP_NAMES[$n]}" "$STATUS_REASON"
    [ -n "$first_todo" ] || first_todo="$n"
  fi
done
if [ -z "$first_todo" ]; then
  echo; ok "Everything is installed. Open http://localhost:3080"
else
  next "bash $KIT/install.sh   (skips the finished steps and continues from step $first_todo)"
fi
