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
    assert text.index("ollama.com/install.sh") < wait < pull
