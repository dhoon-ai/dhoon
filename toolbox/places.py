"""Where the toolbox keeps files: WORKSPACE from the environment.

It comes from the paths file ($XDG_CONFIG_HOME/environment.d/10-paths.conf, written by
install.sh), which systemd gives every user service; scripts/lib.sh loads it for scripts.
There is no home-folder default: a missing WORKSPACE stops with this message.
"""

import os
from pathlib import Path

MISSING = (
    "WORKSPACE is not set: add WORKSPACE=/absolute/path to "
    "$XDG_CONFIG_HOME/environment.d/10-paths.conf (install.sh writes it), then log in again"
)


def workspace() -> Path:
    value = os.environ.get("WORKSPACE", "")
    if not value:
        raise RuntimeError(MISSING)
    return Path(value)
