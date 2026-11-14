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
            assert "%h/dhoon" not in text and "%h/agent-workspace" not in text and "/home/" not in text, f.name


def test_install_fills_in_the_folders(tmp_path):
    paths = tmp_path / "paths.env"
    paths.write_text("WORKSPACE=/agent/agent-workspace\n")
    env = {**os.environ, "HOME": str(tmp_path), "PATHS_ENV": str(paths)}
    q, u = tmp_path / "q", tmp_path / "u"
    subprocess.run(["bash", str(KIT / "scripts" / "install_podman_units.sh"), "--dest-quadlets", str(q),
                    "--dest-units", str(u)], env=env, check=True, capture_output=True, text=True)
    lc = (q / "LibreChat.container").read_text()
    assert f"Volume={KIT}/runtime/LibreChat/.env:/app/.env" in lc and "@" not in lc.replace("@sha256", "")
    tb = (u / "ai-toolbox.service").read_text()
    assert "Environment=WORKSPACE=/agent/agent-workspace" in tb and f"WorkingDirectory={KIT}/toolbox" in tb
    assert (q / "librechat.pod").exists()


def test_workspace_without_a_paths_file_is_the_documented_default(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path), "PATHS_ENV": str(tmp_path / "none.env")}
    out = subprocess.run(["bash", "-c", f'source "{KIT}/scripts/lib.sh"; echo "$WORKSPACE_DIR"'], env=env,
                         check=True, capture_output=True, text=True).stdout.strip()
    assert out == f"{tmp_path}/agent-workspace"
