"""Dry-run test for install.sh, fed through a pipe like `curl ... | bash`."""

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_installer_runs_all_steps_in_order(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_id = bin_dir / "id"
    fake_id.write_text('#!/bin/bash\n[ "$1" = "-u" ] && { echo 1000; exit 0; }\nexec /usr/bin/id "$@"\n')
    fake_id.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "INSTALL_DRY_RUN": "1",
        "INSTALL_DIR": str(tmp_path / "kit"),
    }
    r = subprocess.run(
        ["bash"], input=(REPO / "install.sh").read_text(), env=env, capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 0, r.stdout + r.stderr
    steps = [
        "git clone",
        "00_check_hardware.sh",
        "01_install_base.sh",
        "02_download_models.sh",
        "03_searxng.sh",
        "04_toolbox.sh",
        "05_librechat.sh",
        "06_create_agent.sh",
        "bash scripts/benchmark.sh",
    ]
    positions = [r.stdout.index(s) for s in steps]
    assert positions == sorted(positions)
    assert "(docker group) bash scripts/02_download_models.sh" in r.stdout


def test_installer_refuses_root(tmp_path):
    env = {**os.environ, "INSTALL_DRY_RUN": "1", "INSTALL_DIR": str(tmp_path / "kit")}
    if os.getuid() != 0:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "id").write_text('#!/bin/bash\n[ "$1" = "-u" ] && { echo 0; exit 0; }\nexec /usr/bin/id "$@"\n')
        (bin_dir / "id").chmod(0o755)
        env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    r = subprocess.run(["bash", str(REPO / "install.sh")], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode != 0 and "not as root" in r.stderr
