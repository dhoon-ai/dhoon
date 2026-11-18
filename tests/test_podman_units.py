"""The chat stack's Quadlets and ai-toolbox.service get this computer's folders at install time."""

import os
import subprocess
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
PODMAN_DIR = KIT / "config" / "librechat" / "podman"


def test_templates_name_no_folder_under_the_home():
    for f in PODMAN_DIR.iterdir():
        if f.suffix in (".container", ".pod", ".service"):
            text = f.read_text()
            assert "%h/" not in text and "/home/" not in text, f.name


def test_install_fills_in_the_folders(tmp_path):
    conf = tmp_path / "cfg"
    (conf / "environment.d").mkdir(parents=True)
    (conf / "environment.d" / "10-paths.conf").write_text("AGENT=/agent\nWORKSPACE=${AGENT}/agent-workspace\n")
    env = {**os.environ, "HOME": str(tmp_path), "XDG_CONFIG_HOME": str(conf)}
    env.pop("WORKSPACE", None)
    q, u = tmp_path / "q", tmp_path / "u"
    subprocess.run(
        ["bash", str(KIT / "scripts" / "install_podman_units.sh"), "--dest-quadlets", str(q), "--dest-units", str(u)],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    lc = (q / "LibreChat.container").read_text()
    assert f"Volume={KIT}/runtime/LibreChat/.env:/app/.env" in lc and "@" not in lc.replace("@sha256", "")
    tb = (u / "ai-toolbox.service").read_text()
    assert "Environment=WORKSPACE=/agent/agent-workspace" in tb and f"WorkingDirectory={KIT}/toolbox" in tb
    assert (q / "librechat.pod").exists()


def test_without_a_paths_file_install_stops_with_a_clear_error(tmp_path):
    """No home-folder default: the message names the variable and the paths file."""
    env = {**os.environ, "HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "cfg")}
    env.pop("WORKSPACE", None)
    r = subprocess.run(
        [
            "bash",
            str(KIT / "scripts" / "install_podman_units.sh"),
            "--dest-quadlets",
            str(tmp_path / "q"),
            "--dest-units",
            str(tmp_path / "u"),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 1
    assert "WORKSPACE_DIR is not set" in r.stderr and f"{tmp_path}/cfg/environment.d/10-paths.conf" in r.stderr


def test_only_some_units_into_the_xdg_folder(tmp_path):
    """--only installs just those; the default place is $XDG_CONFIG_HOME, where systemd looks."""
    conf = tmp_path / "cfg"
    (conf / "environment.d").mkdir(parents=True)
    (conf / "environment.d" / "10-paths.conf").write_text("WORKSPACE=/agent/workspace\n")
    env = {**os.environ, "HOME": str(tmp_path), "XDG_CONFIG_HOME": str(conf), "RUNTIME_DIR": str(tmp_path / "rt")}
    env.pop("WORKSPACE", None)
    subprocess.run(
        ["bash", str(KIT / "scripts" / "install_podman_units.sh"), "--only", "searxng,ai-toolbox"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    q, u = conf / "containers" / "systemd", conf / "systemd" / "user"
    assert sorted(p.name for p in q.iterdir()) == ["searxng.container"]
    assert (u / "ai-toolbox.service").exists() and not (tmp_path / ".config").exists()
    sx = (q / "searxng.container").read_text()
    assert (
        "Image=docker.io/searxng/searxng@sha256:" in sx
        and "PublishPort=127.0.0.1:8888:8080" in sx
        and "@" not in sx.replace("@sha256", "")
    )
    settings = (tmp_path / "rt" / "searxng" / "settings.yml").read_text()
    assert "CHANGE_ME" not in settings and "secret_key:" in settings
