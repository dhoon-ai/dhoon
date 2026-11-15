# shellcheck shell=bash
# Sourced by lib.sh and backup.sh: puts the paths file's values into the environment.
# The file is $XDG_CONFIG_HOME/environment.d/10-paths.conf (XDG default: ~/.config), the same
# file systemd gives every user service, so scripts and services see the same folders.
# Lines are NAME=value; ${NAME} / $NAME use earlier lines or the environment; nothing is run.
# A variable that is already set wins, so tests and one-off runs can point anywhere.
# No defaults here: a script that needs a folder checks it with need_path NAME.
_dhoon_paths_load() {
  local f="${XDG_CONFIG_HOME:-$HOME/.config}/environment.d/10-paths.conf" k v name
  [ -f "$f" ] || return 0
  while IFS='=' read -r k v || [ -n "$k" ]; do
    k="${k//[[:space:]]/}"
    [[ "$k" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    [ -n "${!k:-}" ] && continue
    v="${v#"${v%%[![:space:]]*}"}"; v="${v%"${v##*[![:space:]]}"}"
    if [[ "$v" == \'*\' ]]; then
      v="${v:1:${#v}-2}"
    else
      [[ "$v" == \"*\" ]] && v="${v:1:${#v}-2}"
      local n=0
      while [ $((n++)) -lt 20 ] && [[ "$v" =~ \$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*) ]]; do
        name="${BASH_REMATCH[1]:-${BASH_REMATCH[2]}}"
        v="${v/"${BASH_REMATCH[0]}"/${!name:-}}"
      done
    fi
    [ -n "$v" ] && export "$k=$v"
  done < "$f"
}
_dhoon_paths_load

# need_path NAME: stop with a clear message when the paths file doesn't set NAME.
need_path() {
  [ -n "${!1:-}" ] && return 0
  echo "ERROR: $1 is not set: add $1=/absolute/path to ${XDG_CONFIG_HOME:-$HOME/.config}/environment.d/10-paths.conf (install.sh writes it)" >&2
  exit 1
}
