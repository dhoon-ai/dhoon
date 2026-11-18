"""scripts/paths.sh: the paths file 10-paths.conf into the environment, no defaults."""

import subprocess
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]


def _run(tmp_path, text, script, extra=None):
    conf = tmp_path / "cfg"
    (conf / "environment.d").mkdir(parents=True, exist_ok=True)
    (conf / "environment.d" / "10-paths.conf").write_text(text)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "XDG_CONFIG_HOME": str(conf), **(extra or {})}
    return subprocess.run(
        ["bash", "-c", f'. "{KIT}/scripts/paths.sh"; {script}'], env=env, capture_output=True, text=True
    )


def test_values_quotes_and_references(tmp_path):
    text = "# c\n\nPROJECT=/data/project\nDHOON_HOME=${PROJECT}/dhoon\nVENV=\"$DHOON_HOME/runtime/venv\"\nLIT='/a/$X'\n"
    r = _run(tmp_path, text, 'echo "$DHOON_HOME|$VENV|$LIT"')
    assert r.stdout.strip() == "/data/project/dhoon|/data/project/dhoon/runtime/venv|/a/$X"


def test_what_is_already_set_wins(tmp_path):
    r = _run(tmp_path, "WORKSPACE=/from/file\n", 'echo "$WORKSPACE"', {"WORKSPACE": "/mine"})
    assert r.stdout.strip() == "/mine"


def test_need_path_names_the_variable_and_the_file(tmp_path):
    r = _run(tmp_path, "WORKSPACE=/w\n", "need_path BEAM_QUEUE; echo reached")
    assert r.returncode == 1 and "reached" not in r.stdout
    assert "BEAM_QUEUE is not set" in r.stderr and f"{tmp_path}/cfg/environment.d/10-paths.conf" in r.stderr
