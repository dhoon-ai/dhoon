"""scripts/restart.sh with fake system commands: restarts everything in order."""

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_restart_brings_everything_back_in_order(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    fakes = {
        "sudo": 'exec "$@"',
        "systemctl": f'echo "systemctl $*" >> {log}; case "$*" in list-unit-files*) echo "x.service enabled";; esac',
        "docker": f'echo "docker $*" >> {log}; case "$*" in ps*) echo searxng;; esac',
        "curl": "exit 0",
        "sleep": "exit 0",
        "ollama": "exit 0",
    }
    for name, body in fakes.items():
        (bin_dir / name).write_text(f"#!/bin/bash\n{body}\n")
        (bin_dir / name).chmod(0o755)
    (tmp_path / "LibreChat").mkdir()
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "RUNTIME_DIR": str(tmp_path),
           "RESTART_SKIP_CHECK": "1", "RESTART_WAIT": "0"}  # fmt: skip
    r = subprocess.run(["bash", str(REPO / "scripts/restart.sh")], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    order = [c for c in log.read_text().splitlines() if "restart" in c]
    assert order[0] == "systemctl restart ollama"
    assert "docker restart searxng" in order and "docker compose restart" in order
    assert order.index("systemctl restart ai-toolbox") > order.index("docker compose restart")
    assert "systemctl restart --no-block ai-model-preload" in order
    assert "Ollama (the AI models)" in r.stdout and "Toolbox" in r.stdout


def test_update_waits_for_ollama_before_downloading_models():
    """The Ollama installer restarts Ollama; `ollama pull` right after failed with
    'could not connect to ollama server' (found by the bare-metal release test)."""
    text = (REPO / "scripts/update.sh").read_text()
    wait, pull = text.index("wait_for_ollama"), text.index("ollama pull")
    assert text.index("install_ollama") < wait < pull


def test_ollama_is_downloaded_only_when_the_version_changes(tmp_path):
    """update.sh used to re-download Ollama (~1.5 GB) and restart it on every run."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ollama").write_text('#!/bin/bash\necho "ollama version is 0.12.6"\n')
    log = tmp_path / "curl.log"
    (bin_dir / "curl").write_text(
        f'#!/bin/bash\necho "$*" >> {log}\n'
        'case "$*" in *releases/latest*) echo -n "https://github.com/ollama/ollama/releases/tag/v$LATEST" ;;\n'
        "  *install.sh*) echo 'echo installing $OLLAMA_VERSION' ;; esac\n"
    )
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "RUNTIME_DIR": str(tmp_path / "rt")}

    def run(**extra):
        r = subprocess.run(["bash", "-c", f"source {REPO}/scripts/lib.sh && install_ollama"],
                           env={**env, **extra}, capture_output=True, text=True)  # fmt: skip
        return r.stdout

    assert "already the right version" in run(LATEST="0.12.6")
    assert "install.sh" not in log.read_text()
    assert "installing 0.12.7" in run(LATEST="0.12.7")  # a newer release
    assert "already the right version" in run(LATEST="0.12.7", OLLAMA_VERSION="0.12.6")  # pinned
    assert "installing 0.12.5" in run(LATEST="0.12.7", OLLAMA_VERSION="0.12.5")


def test_update_restarts_the_chat_website_so_it_reads_new_settings():
    """librechat.yaml is mounted as a file: `docker compose up -d` keeps the running
    container, so new settings (e.g. memory in v1.0.1) only load after a restart."""
    text = (REPO / "scripts/update.sh").read_text()
    assert text.index("render_librechat_config") < text.index("docker compose restart api")
