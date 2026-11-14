# Sourced by lib.sh, restic_lib.sh and backup.sh: puts the paths file's values (PATHS_ENV, default
# ~/.config/dhoon/paths.env, KEY=/absolute/path lines) into the environment. A variable that is
# already set wins, so tests and one-off runs can point anywhere. Nothing in the file is run.
_dhoon_paths_load() {
  local f="${PATHS_ENV:-$HOME/.config/dhoon/paths.env}" k v
  [ -f "$f" ] || return 0
  while IFS='=' read -r k v || [ -n "$k" ]; do
    k="${k//[[:space:]]/}"
    [[ "$k" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    [ -n "${!k:-}" ] && continue
    v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
    [ -n "$v" ] && export "$k=$v"
  done < "$f"
}
_dhoon_paths_load
