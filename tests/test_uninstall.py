"""Tests for scripts/uninstall.sh in DRY_RUN mode with fake commands (nothing is deleted)."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

FAKES = {
    "docker": "exit 0",
    "ollama": "[ \"$1\" = list ] && printf 'NAME ID\\nqwen3:30b-a3b x\\nnomic-embed-text:latest y\\n'; exit 0",
    "sudo": 'echo "REAL SUDO CALLED: $*" >&2; exit 1',
}


@pytest.fixture()
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in FAKES.items():
        f = bin_dir / name
        f.write_text(f"#!/bin/bash\n{body}\n")
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    workspace = tmp_path / "agent-workspace"
    workspace.mkdir()
    (workspace / "keep.txt").write_text("x")
    return {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "WORKSPACE_DIR": str(workspace),
        "RUNTIME_DIR": str(tmp_path / "runtime"),
        "MODEL_EMBED": "nomic-embed-text:latest",
        "_WS": str(workspace),
    }


def run(env, *args, dry=True, stdin=""):
    e = {**env, "DRY_RUN": "1"} if dry else env
    return subprocess.run(
        ["bash", str(REPO / "scripts/uninstall.sh"), *args],
        env=e,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_services_level_keeps_data(env):
    r = run(env)
    assert r.returncode == 0, r.stderr
    assert "systemctl disable --now ai-toolbox" in r.stdout
    assert "rm -rf" not in r.stdout and "ollama rm" not in r.stdout
    assert "REAL SUDO" not in r.stderr


def test_everything_deletes_data_and_models_but_not_programs(env):
    r = run(env, "--everything", "--yes")
    assert r.returncode == 0, r.stderr
    assert f"rm -rf {env['_WS']}" in r.stdout
    assert "ollama rm qwen3:30b-a3b" in r.stdout and "ollama rm nomic-embed-text:latest" in r.stdout
    assert "apt-get purge" not in r.stdout and "/usr/share/ollama" not in r.stdout
    assert Path(env["_WS"], "keep.txt").exists()  # dry run: nothing really deleted


def test_purge_uninstalls_ollama_and_docker(env):
    r = run(env, "--purge", "--yes")
    assert r.returncode == 0, r.stderr
    for expected in (
        "/usr/share/ollama",
        "systemctl disable --now ollama",
        "docker system prune -a -f --volumes",
        "rm -rf /var/lib/docker",
    ):
        assert expected in r.stdout


def test_confirmation_required(env):
    r = run(env, "--everything", dry=False, stdin="no\n")
    assert r.returncode == 1 and "Cancelled" in r.stdout
    assert Path(env["_WS"], "keep.txt").exists()
    assert "REAL SUDO" not in r.stderr
