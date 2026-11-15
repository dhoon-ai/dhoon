# Rebuilding a computer

Dhoon doesn't rebuild computers. Its folders come from the paths file
(`$XDG_CONFIG_HOME/environment.d/10-paths.conf`), so any backup tool that restores that file and
those folders works; `scripts/backup.sh` keeps an encrypted copy of Dhoon's own files.
The maintainers use a separate tool for backup and full restore (KnowReva/restore, private).
