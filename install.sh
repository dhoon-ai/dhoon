#!/usr/bin/env bash
# One-command installer for local-ai-agent.
#
#   curl -fsSL https://raw.githubusercontent.com/rahulkthakar/local-ai-agent/main/install.sh | bash
#
# It downloads the project to ~/ai-kit and runs scripts 00-06 in order: hardware check,
# Docker + Ollama, models, private search, toolbox, LibreChat, and the Assistant agent.
# Safe to run again: it checks each step and skips the ones already done, so it resumes
# after a failure and continues a manual install (see scripts/status.sh). If a step fails,
# it stops and explains how to run and fix that step by hand in another terminal.
#
# Options (as environment variables in front of `bash`):
#   INSTALL_DIR=~/ai-kit      where to put the project
#   REPO_URL=...              git URL to install from (e.g. your fork)
#   LIBRECHAT_EMAIL / LIBRECHAT_PASSWORD   account for the web app (otherwise asked)
#   SKIP_MODELS=1             skip downloading models (if you manage them yourself)
#   RUN_BENCHMARK=yes|no      answer the speed-test question in advance
#   INSTALL_DRY_RUN=1         only print the steps
#   FORCE=1                   re-run every step even if it looks done
#
# Everything is wrapped in main(), so bash reads the whole file before running anything;
# that keeps `curl | bash` safe even if the download is cut off.

main() {
  set -euo pipefail
  local repo_url="${REPO_URL:-https://github.com/rahulkthakar/local-ai-agent.git}"
  local dir="${INSTALL_DIR:-$HOME/ai-kit}"
  local dry="${INSTALL_DRY_RUN:-}"
  export FROM_INSTALLER=1   # scripts skip "log out and back in" hints; sg handles the group

  say()  { printf '\n\033[1;34m>>> %s\033[0m\n' "$*"; }
  die()  { printf '\n\033[1;31mInstall stopped: %s\033[0m\n' "$*" >&2; exit 1; }
  run()  { if [ -n "$dry" ]; then echo "  [dry-run] $*"; else "$@"; fi; }
  # Run a script with the docker group active, without needing to log out and back in.
  as_docker() {
    if [ -n "$dry" ]; then echo "  [dry-run] (docker group) bash $*"; return; fi
    if id -nG | tr ' ' '\n' | grep -qx docker; then bash "$@"; else sg docker -c "$(printf '%q ' bash "$@")"; fi
  }

  say "local-ai-agent installer"
  echo "This installs a private AI agent on this computer. It takes 30-90 minutes, mostly"
  echo "downloads (up to ~41 GB). You'll be asked for your password (sudo) and, at the end,"
  echo "for an email and password for the web app. Re-run the same command to resume."

  [ "$(id -u)" -ne 0 ] || die "run this as your normal user, not as root (it uses sudo when needed)"
  command -v apt-get >/dev/null 2>&1 || die "this installer needs Ubuntu or Debian (apt)"
  command -v sudo >/dev/null 2>&1 || die "sudo is required"

  say "1/8  Getting the project into $dir"
  if ! command -v git >/dev/null 2>&1; then run sudo apt-get update; run sudo apt-get install -y git; fi
  if [ -d "$dir/.git" ]; then
    run git -C "$dir" pull --ff-only || echo "  (could not update - continuing with the existing copy)"
  elif [ -e "$dir" ] && [ -n "$(ls -A "$dir" 2>/dev/null)" ]; then
    die "$dir exists and is not this project. Move it away, or set INSTALL_DIR=/another/folder"
  else
    run git clone --depth 1 "$repo_url" "$dir"
  fi
  [ -n "$dry" ] || cd "$dir"

  # Load the per-step status checks (also used by scripts/status.sh).
  have_status=false
  if [ -f "$dir/scripts/lib.sh" ]; then
    # shellcheck source=scripts/lib.sh
    source "$dir/scripts/lib.sh"
    have_status=true
  fi
  # do_step N "title" command...: skip if step N is already done (unless FORCE=1).
  do_step() {
    local n="$1" title="$2"; shift 2
    if [ -z "${FORCE:-}" ] && $have_status && step_done "$n"; then
      say "$((n + 2))/8  $title - already done, skipping"
      return 0
    fi
    say "$((n + 2))/8  $title"
    if $have_status && [ -n "$STATUS_REASON" ]; then echo "  (reason: $STATUS_REASON)"; fi
    if ! "$@"; then
      stop_with_help "$n" "$title"
    fi
  }
  # A step failed: stop here (don't carry on with a half-working setup) and explain the
  # manual way to fix it. Re-running the installer continues from this same step.
  stop_with_help() {
    local n="$1" title="$2" script
    case "$n" in
      1) script=01_install_base.sh ;;   2) script=02_download_models.sh ;;
      3) script=03_searxng.sh ;;        4) script=04_toolbox.sh ;;
      5) script=05_librechat.sh ;;      6) script=06_create_agent.sh ;;
    esac
    printf '\n\033[1;31m=== Step %s/8 failed: %s ===\033[0m\n' "$((n + 2))" "$title" >&2
    cat >&2 <<EOT
The installer has stopped here so nothing is left half-done. Your earlier steps are kept.

What to do:
  1. Read the error just above this box.
  2. Open ANOTHER terminal (Ctrl+Alt+T) and run this step by hand to see it clearly:
       cd $dir && bash scripts/$script
     Fix the problem it reports. Help: $dir/docs/GUIDE.md (section "Troubleshooting").
     Useful checks:  bash scripts/status.sh   and   bash scripts/check.sh
  3. When that step works, come back here and run the installer again:
       bash $dir/install.sh
     It skips everything already done and continues from this step.
EOT
    exit 1
  }

  if $have_status && [ -z "$dry" ]; then bash scripts/status.sh || true; fi

  say "2/8  Checking the hardware (changes nothing)"
  run bash scripts/00_check_hardware.sh

  do_step 1 "Installing Docker and Ollama" run bash scripts/01_install_base.sh

  if [ -n "${SKIP_MODELS:-}" ]; then
    say "4/8  Skipping model downloads (SKIP_MODELS is set)"
  else
    do_step 2 "Downloading the AI models (the long part)" as_docker scripts/02_download_models.sh
  fi

  do_step 3 "Starting the private search engine" as_docker scripts/03_searxng.sh
  do_step 4 "Starting the agent toolbox" as_docker scripts/04_toolbox.sh
  do_step 5 "Installing the LibreChat web app" as_docker scripts/05_librechat.sh

  create_agent() {
    if [ -n "$dry" ]; then echo "  [dry-run] bash scripts/06_create_agent.sh"; return; fi
    as_docker scripts/06_create_agent.sh </dev/tty
  }
  do_step 6 "Creating the Assistant agent" create_agent

  # Optional speed test: Ollama's default (GPU if supported) vs CPU only; keeps the faster.
  say "Optional: speed test (about 5 minutes)"
  echo "  Checks whether running the AI on the CPU only is faster on this PC than Ollama's"
  echo "  default (e.g. on mini PCs with built-in graphics), and switches if it is."
  local answer="${RUN_BENCHMARK:-}"
  if [ -n "$dry" ]; then
    echo "  [dry-run] ask, then: bash scripts/benchmark.sh"
  else
    if [ -z "$answer" ]; then
      if [ -r /dev/tty ]; then read -r -p "  Run the speed test now? [y/N] " answer </dev/tty || answer=""
      else answer="no"; fi
    fi
    case "$answer" in
      y|Y|yes|YES|1)
        bash scripts/benchmark.sh || echo "  The speed test did not finish; run it later: bash $dir/scripts/benchmark.sh" ;;
      *) echo "  Skipped. Run it any time: bash $dir/scripts/benchmark.sh" ;;
    esac
  fi

  say "Finished"
  [ -n "$dry" ] || bash scripts/check.sh || true
  local ip
  ip=$(hostname -I 2>/dev/null | awk '{print $1}')
  echo
  echo "Open the web app:  http://localhost:3080   (other devices at home: http://${ip:-<this-pc>}:3080)"
  echo "Choose  Agents -> Assistant  and try one of the suggested starters."
  echo "Log out and back in once, so 'docker' works in new terminals without sudo."
  echo "Guide: $dir/docs/GUIDE.md"
}

main "$@"
