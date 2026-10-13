"""Dhoon branding in LibreChat: every icon the override mounts exists and gets copied."""

import json
import os
import re
import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
OVERRIDE = REPO / "config" / "librechat" / "docker-compose.override.yml"


def test_every_mounted_icon_exists():
    api = yaml.safe_load(OVERRIDE.read_text())["services"]["api"]
    mounts = [v.split(":") for v in api["volumes"] if v.startswith("./branding/")]
    assert len(mounts) == 7
    for src, dst, mode in mounts:
        assert (REPO / "config" / src.removeprefix("./")).is_file(), src  # else Docker makes a folder
        assert dst.startswith("/app/client/dist/") and mode == "ro"
    env = dict(e.split("=", 1) for e in api["environment"])
    assert env["APP_TITLE"] == "Dhoon" and "|" not in env["CUSTOM_FOOTER"]  # | splits the footer


def test_manifest_names_dhoon_and_lists_real_icons():
    m = json.loads((REPO / "config" / "branding" / "manifest.webmanifest").read_text())
    assert m["name"] == m["short_name"] == "Dhoon"
    for icon in m["icons"]:
        f = REPO / "config" / "branding" / Path(icon["src"]).name
        w, h = (int(x) for x in icon["sizes"].split("x"))
        data = f.read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n" and (
            int.from_bytes(data[16:20], "big"),
            int.from_bytes(data[20:24], "big"),
        ) == (w, h)


def test_welcome_text_in_librechat_yaml():
    cfg = yaml.safe_load((REPO / "config" / "librechat" / "librechat.yaml").read_text())
    assert "Dhoon" in cfg["interface"]["customWelcome"]


def test_render_copies_the_icons(tmp_path):
    env = {**os.environ, "RUNTIME_DIR": str(tmp_path), "MODEL_MAIN": "a", "MODEL_FAST": "b"}
    lc = tmp_path / "LibreChat"
    lc.mkdir()
    subprocess.run(["bash", "-c", f"source {REPO}/scripts/lib.sh && render_librechat_config"], env=env, check=True)
    assert (lc / "docker-compose.override.yml").read_text() == OVERRIDE.read_text()
    copied = {f.name for f in (lc / "branding").iterdir()}
    assert copied == {f.name for f in (REPO / "config" / "branding").iterdir()}
    assert re.search(r"default: \[\"a\", \"b\"\]", (lc / "librechat.yaml").read_text())
