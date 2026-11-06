#!/usr/bin/env bash
# Shared by backup_restic.sh and restore_restic.sh (settings, roles, running restic in Podman).
# Sourced, not run directly. See backup_restic.sh for the settings.
# shellcheck disable=SC2034  # variables are used by the scripts that source this file
SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIT="$(cd "$SCRIPTS/.." && pwd)"
CONF="${BACKUP_CONF:-$HOME/.config/ai-kit-backup}"
ENV_FILE="${RESTIC_ENV:-$CONF/restic.env}"
# shellcheck source=/dev/null
[ -f "$ENV_FILE" ] && { set -a; source "$ENV_FILE"; set +a; }
WORKSPACE_DIR="${WORKSPACE_DIR:-$HOME/agent-workspace}"
PODMAN="${PODMAN:-podman}"            # tests use a stand-in
DOCKER="${DOCKER:-docker}"            # the chat stack still runs on Docker today
CURL="${CURL:-curl}"                  # tests use a stand-in
RESTIC_TARGETS="${RESTIC_TARGETS:-usb}"
RESTIC_ROLES="${RESTIC_ROLES:-workspace claude config backupconf ssh rebuild}"
RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE:-$CONF/restic-password}"
RESTIC_KEEP="${RESTIC_KEEP:---keep-daily 7 --keep-weekly 4 --keep-monthly 6}"
STAGE="${RESTIC_STAGE:-$HOME/.local/share/ai-kit-backup/rebuild}"
CACHE="${RESTIC_CACHE:-$HOME/.cache/restic}"
STATUS="$CONF/last-restic.txt"
STALE_HOURS="${RESTIC_STALE_HOURS:-36}"
EXCLUDES=(--exclude __pycache__ --exclude '*.pyc' --exclude .venv --exclude venv --exclude node_modules
          --exclude '.cache' --exclude '*.lock')

say()  { printf '%s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

role_path() {  # the place a role lives on this computer
  local over="ROLE_PATH_$1"
  if [ -n "${!over:-}" ]; then printf '%s' "${!over}"; return; fi
  case "$1" in
    workspace)  printf '%s' "$WORKSPACE_DIR" ;;
    claude)     printf '%s' "$HOME/.claude" ;;
    config)     printf '%s' "$KIT/config" ;;
    backupconf) printf '%s' "$CONF" ;;
    ssh)        printf '%s' "$HOME/.ssh" ;;
    rebuild)    printf '%s' "$STAGE" ;;
    *)          return 1 ;;
  esac
}

restic_run() {  # restic_run <target> [podman -v mounts...] -- <restic args...>
  local target="$1"; shift
  local mounts=()
  while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do mounts+=("$1"); shift; done
  [ "${1:-}" = "--" ] && shift
  local args=(run --rm --userns=keep-id --hostname dhoon
              -e RESTIC_PASSWORD_FILE=/secret/password -v "$RESTIC_PASSWORD_FILE:/secret/password:ro"
              -e RESTIC_CACHE_DIR=/cache -v "$CACHE:/cache")
  case "$target" in
    usb) [ -n "${RESTIC_USB:-}" ] || { warn "RESTIC_USB is not set"; return 1; }
         [ -d "$RESTIC_USB" ] || mkdir -p "$RESTIC_USB" 2>/dev/null || { warn "can't reach $RESTIC_USB (disk plugged in?)"; return 1; }
         args+=(-v "$RESTIC_USB:/repo" -e RESTIC_REPOSITORY=/repo) ;;
    r2)  [ -n "${RESTIC_R2:-}" ] || { warn "RESTIC_R2 is not set"; return 1; }
         args+=(-e "RESTIC_REPOSITORY=$RESTIC_R2" -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY) ;;
    *)   warn "unknown target $target"; return 1 ;;
  esac
  "$PODMAN" "${args[@]}" "${mounts[@]}" "$RESTIC_IMAGE" "$@"
}

