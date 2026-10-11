"""scripts/logs.sh: collects the logs and explains known problems (with fake tools)."""

import datetime as dt
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def run_logs(tmp_path, journal="", docker=""):
    bin_dir, rt = tmp_path / "bin", tmp_path / "rt"
    (rt / "logs").mkdir(parents=True)
    bin_dir.mkdir()
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    old = (dt.datetime.now() - dt.timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
    (rt / "logs" / "toolbox.log").write_text(
        f"{old} CALL  old_tool {{}}\n"
        f'{now} CALL  web_search {{"query": "x"}}\n'
        f"{now} DONE  web_search in 1.0s, 20 chars: ok\n"
        f'{now} CALL  run_python {{"code": "1"}}\n'
    )
    for name, out in (("journalctl", journal), ("docker", docker)):
        (bin_dir / name).write_text(f"#!/bin/bash\nprintf '%s\\n' '{out}'\n")
        (bin_dir / name).chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "RUNTIME_DIR": str(rt), "CONTEXT_LENGTH": "32768"}
    r = subprocess.run(["bash", str(REPO / "scripts/logs.sh"), "30", "--save"], env=env, capture_output=True, text=True)
    return r, rt


def test_logs_explains_truncation_step_limit_and_unfinished_tool(tmp_path):
    r, rt = run_logs(
        tmp_path,
        journal='level=WARN msg="truncating input prompt" limit=32768 prompt=40000',
        docker="error: Recursion limit of 50 reached",
    )
    out = r.stdout
    assert r.returncode == 0, r.stderr
    assert "web_search" in out and "old_tool" not in out  # only the chosen time window
    assert "context window" in out and "CONTEXT_LENGTH" in out
    assert "step limit" in out
    assert "last tool call never finished" in out and "run_python" in out
    saved = list((rt / "logs").glob("report-*.txt"))
    assert saved and "\x1b[" not in saved[0].read_text()


def test_logs_all_clear(tmp_path):
    r, _ = run_logs(tmp_path)
    assert "No known problem" not in r.stdout  # an unfinished tool call is still reported
    assert "last tool call never finished" in r.stdout
