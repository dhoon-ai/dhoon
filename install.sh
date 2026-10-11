#!/usr/bin/env bash
# One-command installer for local-ai-agent.
#
#   curl -fsSL https://raw.githubusercontent.com/rahulkthakar/local-ai-agent/main/install.sh | bash
#
# It downloads the project to ~/ai-kit and runs scripts 00-06 in order: hardware check,
# Docker + Ollama, models, private search, toolbox, LibreChat, and the Assistant agent.
# Safe to run again: finished steps are skipped or refreshed, so it also resumes.
#
# Options (as environment variables in front of `bash`):
#   INSTALL_DIR=~/ai-kit      where to put the project
#   REPO_URL=...              git URL to install from (e.g. your fork)
#   LIBRECHAT_EMAIL / LIBRECHAT_PASSWORD   account for the web app (otherwise asked)
#   SKIP_MODELS=1             skip downloading models (if you manage them yourself)
#   INSTALL_DRY_RUN=1         only print the steps
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

  say "2/8  Checking the hardware (changes nothing)"
  run bash scripts/00_check_hardware.sh

  say "3/8  Installing Docker and Ollama"
  run bash scripts/01_install_base.sh

  if [ -n "${SKIP_MODELS:-}" ]; then
    say "4/8  Skipping model downloads (SKIP_MODELS is set)"
  else
    say "4/8  Downloading the AI models (the long part)"
    as_docker scripts/02_download_models.sh
  fi

  say "5/8  Starting the private search engine"
  as_docker scripts/03_searxng.sh

  say "6/8  Starting the agent toolbox"
  as_docker scripts/04_toolbox.sh

  say "7/8  Installing the LibreChat web app"
  as_docker scripts/05_librechat.sh

  say "8/8  Creating the Assistant agent"
  if [ -n "$dry" ]; then echo "  [dry-run] bash scripts/06_create_agent.sh"
  else bash scripts/06_create_agent.sh </dev/tty || {
    echo "Agent creation failed. Fix the problem above, then run:  cd $dir && bash scripts/06_create_agent.sh"
    echo "(or create it by hand: docs/GUIDE.md step 8)"; }
  fi

  say "Finished"
  [ -n "$dry" ] || bash scripts/check.sh || true
  local ip
  ip=$(hostname -I 2>/dev/null | awk '{print $1}')
  echo
  echo "Open the web app:  http://localhost:3080   (other devices at home: http://${ip:-<this-pc>}:3080)"
  echo "Choose  Agents -> Assistant  and try one of the suggested starters."
  echo "Optional: bash $dir/scripts/benchmark.sh  (checks whether CPU-only is faster on this PC)"
  echo "Log out and back in once, so 'docker' works in new terminals without sudo."
  echo "Guide: $dir/docs/GUIDE.md"
}

main "$@"
