"""status.sh and the installer's resume logic on a simulated half-finished install."""

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MODELS = {"MODEL_MAIN": "m-main:i", "MODEL_FAST": "m-fast:i", "MODEL_EMBED": "m-embed", "MODEL_PLANNER": "m-plan:t"}


@pytest.fixture()
def half_installed(tmp_path):
    """Steps 1 and 2 done (Docker, Ollama, models); search, toolbox, LibreChat, agent not."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fakes = {
        "docker": "exit 0",
        "ollama": "[ \"$1\" = list ] && printf 'NAME ID\\nm-main:i a\\nm-fast:i b\\nm-embed:latest c\\nm-plan:t d\\n'; exit 0",
        "getent": 'echo "docker:x:999:$USER"',
        # only Ollama (port 11434) answers
        "curl": 'for a in "$@"; do case "$a" in *11434*) exit 0;; esac; done; exit 7',
        "systemctl": "exit 3",
        "id": '[ "$1" = "-u" ] && { echo 1000; exit 0; }; exec /usr/bin/id "$@"',
    }
    for name, body in fakes.items():
        f = bin_dir / name
        f.write_text(f"#!/bin/bash\n{body}\n")
        f.chmod(0o755)
    root = tmp_path / "systemd"
    (root / "ollama.service.d").mkdir(parents=True)
    (root / "ollama.service.d" / "override.conf").write_text("[Service]\n")
    return {
        **os.environ,
        **MODELS,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "SYSTEMD_ROOT": str(root),
        "RUNTIME_DIR": str(tmp_path / "runtime"),
        "USER": "tester",
    }


def test_status_shows_done_and_todo(half_installed):
    r = subprocess.run(["bash", str(REPO / "scripts/status.sh")], env=half_installed, capture_output=True, text=True)
    out = r.stdout
    assert "[OK]" in out and "1. Docker + Ollama" in out and "2. AI models" in out
    assert "[TODO]" in out and "3. Private search" in out and "SearXNG not answering" in out
    assert "continues from step 3" in out


def test_status_reports_missing_model(half_installed):
    env = {**half_installed, "MODEL_PLANNER": "not-downloaded:x"}
    r = subprocess.run(["bash", str(REPO / "scripts/status.sh")], env=env, capture_output=True, text=True)
    assert "model not-downloaded:x not downloaded" in r.stdout and "continues from step 2" in r.stdout


def test_installer_skips_done_steps(half_installed):
    env = {**half_installed, "INSTALL_DRY_RUN": "1", "INSTALL_DIR": str(REPO)}
    r = subprocess.run(["bash", str(REPO / "install.sh")], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert "Installing Docker and Ollama - already done, skipping" in out
    assert "Downloading the AI models (the long part) - already done, skipping" in out
    assert "01_install_base.sh" not in out and "02_download_models.sh" not in out
    for script in ("03_searxng.sh", "04_toolbox.sh", "05_librechat.sh", "06_create_agent.sh"):
        assert script in out
    assert "reason: SearXNG not answering" in out


def test_force_reruns_everything(half_installed):
    env = {**half_installed, "INSTALL_DRY_RUN": "1", "INSTALL_DIR": str(REPO), "FORCE": "1"}
    r = subprocess.run(["bash", str(REPO / "install.sh")], env=env, capture_output=True, text=True, timeout=60)
    assert "01_install_base.sh" in r.stdout and "02_download_models.sh" in r.stdout
