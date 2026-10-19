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
    (root / "ollama.service.d" / "override.conf").write_text(
        '[Service]\nEnvironment="OLLAMA_CONTEXT_LENGTH=32768"\nEnvironment="OLLAMA_KEEP_ALIVE=-1"\nEnvironment="OLLAMA_MAX_LOADED_MODELS=3"\nEnvironment="OLLAMA_NUM_PARALLEL=2"\n'
    )
    return {
        **os.environ,
        **MODELS,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "SYSTEMD_ROOT": str(root),
        "RUNTIME_DIR": str(tmp_path / "runtime"),
        "USER": "tester",
        "CONTEXT_LENGTH": "32768",
        "KEEP_ALIVE": "-1",
        "NUM_PARALLEL": "2",
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


def test_failed_step_stops_with_manual_instructions(half_installed, tmp_path):
    """Steps 1-2 done, step 3 fails: the installer must stop, explain, and not run step 4+."""
    import shutil

    kit = tmp_path / "kit"
    shutil.copytree(REPO, kit, ignore=shutil.ignore_patterns("runtime", "__pycache__", ".git"))
    subprocess.run(["git", "init", "-q", str(kit)], check=True)
    (kit / "scripts" / "03_searxng.sh").write_text("#!/bin/bash\necho 'boom: port 8888 in use' >&2\nexit 1\n")
    (kit / "scripts" / "04_toolbox.sh").write_text("#!/bin/bash\necho STEP4-RAN\n")
    bin_dir = Path(half_installed["PATH"].split(":")[0])
    (bin_dir / "id").write_text(
        '#!/bin/bash\ncase "$1" in -u) echo 1000;; -nG) echo "tester docker";; *) exec /usr/bin/id "$@";; esac\n'
    )
    env = {**half_installed, "INSTALL_DIR": str(kit)}
    r = subprocess.run(["bash", str(kit / "install.sh")], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 1
    assert "Step 5/8 failed: Starting the private search engine" in r.stderr
    assert "Open ANOTHER terminal" in r.stderr and f"cd {kit} && bash scripts/03_searxng.sh" in r.stderr
    assert "continues from this step" in r.stderr
    assert "STEP4-RAN" not in r.stdout and "Finished" not in r.stdout


def test_old_context_length_means_step_1_is_redone(half_installed, tmp_path):
    conf = Path(half_installed["SYSTEMD_ROOT"]) / "ollama.service.d" / "override.conf"
    conf.write_text('[Service]\nEnvironment="OLLAMA_CONTEXT_LENGTH=16384"\nEnvironment="OLLAMA_KEEP_ALIVE=-1"\n')
    r = subprocess.run(["bash", str(REPO / "scripts/status.sh")], env=half_installed, capture_output=True, text=True)
    assert "context length" in r.stdout and "continues from step 1" in r.stdout


def test_old_keep_alive_means_step_1_is_redone(half_installed):
    conf = Path(half_installed["SYSTEMD_ROOT"]) / "ollama.service.d" / "override.conf"
    conf.write_text('[Service]\nEnvironment="OLLAMA_CONTEXT_LENGTH=32768"\nEnvironment="OLLAMA_KEEP_ALIVE=30m"\n')
    r = subprocess.run(["bash", str(REPO / "scripts/status.sh")], env=half_installed, capture_output=True, text=True)
    assert "keep-alive" in r.stdout and "continues from step 1" in r.stdout
