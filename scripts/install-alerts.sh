#!/usr/bin/env bash
set -eu
KIT="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="${SUDO_USER:-$(id -un)}"
PYTHON="$KIT/runtime/venv/bin/python"
ROOT="${INSTALL_ALERTS_ROOT:-/}"
UNIT_DIR="$ROOT/etc/systemd/system"
TMPL_DIR="$KIT/scripts/systemd"

render() {
  sed -e "s#@KIT@#$KIT#g" -e "s#@USER@#$USER_NAME#g" -e "s#@PYTHON@#$PYTHON#g" "$1"
}

list_units() {
  for svc in "$UNIT_DIR"/ai-*.service; do
    [ -e "$svc" ] || continue
    base=$(basename "$svc")
    [ "$base" = "ai-notify-failure@.service" ] && continue
    echo "$base -> $svc.d/onfailure.conf"
  done
}

install_one() {
  mkdir -p "$UNIT_DIR"
  for f in "$TMPL_DIR"/*; do
    [ -f "$f" ] || continue
    base=$(basename "$f")
    render "$f" > "$UNIT_DIR/$base"
  done
}

add_dropins() {
  for svc in "$UNIT_DIR"/ai-*.service; do
    [ -e "$svc" ] || continue
    base=$(basename "$svc")
    [ "$base" = "ai-notify-failure@.service" ] && continue
    mkdir -p "$svc.d"
    cat > "$svc.d/onfailure.conf" <<EOF
[Unit]
OnFailure=ai-notify-failure@%n.service
EOF
  done
}

remove_dropins() {
  for svc in "$UNIT_DIR"/ai-*.service; do
    [ -e "$svc" ] || continue
    rm -f "$svc.d/onfailure.conf"
    rmdir "$svc.d" 2>/dev/null || true
  done
  for f in ai-notify-failure@.service ai-health-check.service ai-health-check.timer; do
    rm -f "$UNIT_DIR/$f"
  done
}

cmd=${1:-install}
case "$cmd" in
  --list) list_units ;;
  --remove) remove_dropins ;;
  install|"") install_one; add_dropins ;;
  *) echo "Usage: install-alerts.sh [--list|--remove]"; exit 2 ;;
esac
