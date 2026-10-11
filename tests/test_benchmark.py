"""Tests for scripts/benchmark.sh against a fake Ollama (no real Ollama, GPU or sudo needed)."""

import json
import os
import stat
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MODEL = "qwen3:4b-instruct-2507-q4_K_M"


def fake_ollama(gpu_speeds, cpu_speeds=(54.0, 11.2)):
    """HTTP server answering like Ollama; default runs use gpu_speeds, num_gpu=0 uses cpu_speeds."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"models":[]}')

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            pp, tg = cpu_speeds if body["options"].get("num_gpu") == 0 else gpu_speeds
            result = {
                "prompt_eval_count": 600,
                "prompt_eval_duration": 600 / pp * 1e9,
                "eval_count": 128,
                "eval_duration": 128 / tg * 1e9,
            }
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture()
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_cli = bin_dir / "ollama"
    fake_cli.write_text(
        "#!/bin/bash\n"
        'case "$1" in\n'
        f"  list) printf 'NAME ID\\n{MODEL} x\\n';;\n"
        f"  ps) printf 'NAME ID SIZE PROCESSOR\\n{MODEL} x 3 GB 100%% GPU\\n';;\n"
        "esac\n"
    )
    fake_cli.chmod(fake_cli.stat().st_mode | stat.S_IEXEC)
    dropin_dir = tmp_path / "dropins"
    dropin_dir.mkdir()
    return {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "SYSTEMD_DIR": str(dropin_dir),
        "NO_RESTART": "1",
        "MODEL_FAST": MODEL,
        "_DROPIN": str(dropin_dir / "cpu-only.conf"),
    }


def run(env, server, *args):
    env = {**env, "OLLAMA_URL": f"http://127.0.0.1:{server.server_port}"}
    r = subprocess.run(
        ["bash", str(REPO / "scripts/benchmark.sh"), *args], env=env, capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def test_slow_integrated_gpu_switches_to_cpu_only(env):
    server = fake_ollama(gpu_speeds=(25.6, 4.3))
    out = run(env, server)
    assert "CPU only is faster" in out
    dropin = Path(env["_DROPIN"]).read_text()
    assert "CUDA_VISIBLE_DEVICES=-1" in dropin and "GGML_VK_VISIBLE_DEVICES=" in dropin


def test_fast_gpu_keeps_default_and_removes_old_setting(env):
    Path(env["_DROPIN"]).write_text("old")
    server = fake_ollama(gpu_speeds=(900.0, 60.0))
    out = run(env, server)
    assert "default is as fast or faster" in out
    assert not Path(env["_DROPIN"]).exists()


def test_report_changes_nothing_and_reset_removes(env):
    server = fake_ollama(gpu_speeds=(25.6, 4.3))
    assert "nothing changed" in run(env, server, "--report")
    assert not Path(env["_DROPIN"]).exists()
    run(env, server)
    assert Path(env["_DROPIN"]).exists()
    assert "back to its default" in run(env, server, "--reset")
    assert not Path(env["_DROPIN"]).exists()
