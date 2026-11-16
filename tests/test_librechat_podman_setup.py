"""scripts/librechat_podman_setup.sh: runtime/LibreChat for the Podman units, never a new .env."""
import os
import subprocess
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
SCRIPT = KIT / "scripts" / "librechat_podman_setup.sh"


def _run(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path), "RUNTIME_DIR": str(tmp_path / "rt")}
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)


def test_without_env_it_stops_and_says_where_it_comes_from(tmp_path):
    r = _run(tmp_path)
    assert r.returncode == 1 and ".env is missing" in r.stdout + r.stderr
    assert not (tmp_path / "rt" / "LibreChat" / "admin-panel.env").exists()


def test_with_env_it_makes_folders_config_and_admin_secret(tmp_path):
    lc = tmp_path / "rt" / "LibreChat"
    lc.mkdir(parents=True)
    (lc / ".env").write_text("CREDS_KEY=x\nADMIN_PANEL_SESSION_SECRET=s3cret\n")
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    for d in ("images", "uploads", "logs", "skill", "data-node", "meili_data_v1.35.1"):
        assert (lc / d).is_dir(), d
    assert (lc / "librechat.yaml").exists() and (lc / "branding" / "logo.svg").exists()
    assert (lc / "admin-panel.env").read_text() == "SESSION_SECRET=s3cret\n"
    assert oct((lc / "admin-panel.env").stat().st_mode & 0o777) == "0o600"
    assert (lc / ".env").read_text().startswith("CREDS_KEY=x")  # never replaced
