"""scripts/unban.sh with a fake docker/mongosh (no real LibreChat needed)."""

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    # fake docker: logs every call; `exec ... countDocuments` prints $BANS
    (bin_dir / "docker").write_text(
        "#!/bin/bash\n"
        f'echo "$*" >> {log}\n'
        'case "$*" in\n'
        "  info*) exit 0;;\n"
        "  ps*) echo chat-mongodb;;\n"
        '  *countDocuments*) echo "${BANS:-0}";;\n'
        '  *deleteMany*) echo "${BANS:-0}";;\n'
        "esac\n"
    )
    (bin_dir / "curl").write_text("#!/bin/bash\nexit 0\n")
    for f in ("docker", "curl"):
        (bin_dir / f).chmod(0o755)
    lc = tmp_path / "LibreChat"
    lc.mkdir()
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "RUNTIME_DIR": str(tmp_path), "_LOG": str(log)}


def run(env, *args, bans="0"):
    return subprocess.run(
        ["bash", str(REPO / "scripts/unban.sh"), *args], env={**env, "BANS": bans}, capture_output=True, text=True
    )


def test_if_needed_does_nothing_without_bans(env):
    r = run(env, "--if-needed", bans="0")
    assert r.returncode == 0 and "Removing" not in r.stdout
    assert "restart" not in Path(env["_LOG"]).read_text()


def test_if_needed_removes_bans_and_restarts(env):
    r = run(env, "--if-needed", bans="2")
    assert r.returncode == 0 and "Removed 2 ban(s)" in r.stdout
    log = Path(env["_LOG"]).read_text()
    assert "deleteMany" in log and "compose restart api" in log


def test_plain_run_always_cleans(env):
    r = run(env, bans="0")
    assert r.returncode == 0 and "Removed 0 ban(s)" in r.stdout
    assert "compose restart api" in Path(env["_LOG"]).read_text()
