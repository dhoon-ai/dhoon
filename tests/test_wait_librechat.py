"""check.sh waits for LibreChat after a restart instead of failing at once (update.sh false alarm)."""

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _run(tmp_path, curl_ok_after: int, container_running: bool):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    count = tmp_path / "count"
    count.write_text("0")
    (bin_dir / "curl").write_text(
        f"#!/bin/bash\nn=$(( $(cat {count}) + 1 )); echo $n > {count}\n[ $n -gt {curl_ok_after} ]\n"
    )
    (bin_dir / "docker").write_text(f"#!/bin/bash\n{'echo LibreChat' if container_running else 'true'}\n")
    (bin_dir / "sleep").write_text("#!/bin/bash\ntrue\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "RUNTIME_DIR": str(tmp_path)}
    r = subprocess.run(["bash", "-c", f"source {REPO}/scripts/lib.sh && wait_for_librechat 40"], env=env)
    return r.returncode, int(count.read_text())


def test_waits_while_librechat_is_starting(tmp_path):
    code, tries = _run(tmp_path, curl_ok_after=5, container_running=True)
    assert code == 0 and tries == 6


def test_gives_up_at_once_when_the_container_is_not_running(tmp_path):
    code, tries = _run(tmp_path, curl_ok_after=99, container_running=False)
    assert code == 1 and tries == 1


def test_check_uses_the_wait():
    assert "wait_for_librechat 40" in (REPO / "scripts" / "check.sh").read_text()
