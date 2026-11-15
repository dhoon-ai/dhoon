"""No folder under the home is written into the code, units, tests or examples: places come
from the paths file. Exceptions are listed by name, each with its reason."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMES = r"(dhoon|dhoon-dev|blocks|blocks-dev|agent-workspace|beam-queue)"
BAD = re.compile(r"(~|\$HOME|\$\{HOME\}|%h)/" + NAMES + r"(?![\w-])|home\(\)\s*/\s*[\"']" + NAMES + "|/home/" + "rahul")
ALLOWED = {
    # the age backup (scripts/backup.sh, restore.sh) is left as it is until its owner decides
    "scripts/backup.sh",
    "scripts/restore.sh",
}
NEW_INSTALL_DEFAULT = "# new-install default"  # install.sh proposes a folder once, then saves it
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "runtime", "plugins", "node_modules"}
DOCS = {".md"}  # docs are checked in the docs pass


def _files():
    """The repo's own files: what git tracks (caches, venvs and ignored files never count)."""
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, text=True, check=True).stdout
        return sorted(ROOT / x for x in out.split("\0") if x)
    except (OSError, subprocess.CalledProcessError):
        return sorted(ROOT.rglob("*"))


def test_no_folder_under_the_home():
    bad = []
    for f in _files():
        rel = f.relative_to(ROOT)
        if not f.is_file() or SKIP_DIRS.intersection(rel.parts) or f.suffix in DOCS or str(rel) in ALLOWED:
            continue
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(lines, 1):
            if BAD.search(line) and NEW_INSTALL_DEFAULT not in line:
                bad.append(f"{rel}:{n}: {line.strip()}")
    assert not bad, "\n".join(bad)
