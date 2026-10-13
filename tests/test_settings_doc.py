"""Optional settings are all explained in one place, and check.sh shows them without secrets."""

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_every_settings_file_is_in_the_guides_overview():
    guide = (REPO / "docs/GUIDE.md").read_text()
    overview = guide.split("## After installing: your settings", 1)[1].split("\n## ", 1)[0]
    for example in (REPO / "config").glob("*.env.example"):
        assert example.name.removesuffix(".example") in overview, example.name
    for anchor in re.findall(r"\]\(#([a-z0-9-]+)\)", overview):  # links inside the guide work
        titles = {
            re.sub(r"[^a-z0-9 -]", "", h.lower()).replace(" ", "-") for h in re.findall(r"^#+ (.+)$", guide, re.M)
        }
        assert anchor in titles, anchor


def test_check_shows_your_settings_but_never_secrets(tmp_path):
    kit, rt = tmp_path / "kit", tmp_path / "rt"
    (kit / "config").mkdir(parents=True)
    (kit / "plugins" / "mytool").mkdir(parents=True)
    (rt / "LibreChat").mkdir(parents=True)
    (kit / "config/email.env").write_text(
        'EMAIL_PROVIDER=gmail\nEMAIL_APP_PASSWORD="top secret"\nEMAIL_ALLOWED_TO=me@x.com\n'
    )
    (kit / "config/calendar.env").write_text("CALDAV_URL=https://dav\nCALDAV_PASSWORD=hunter2\n")
    (rt / "LibreChat/.env").write_text("ALLOW_REGISTRATION=false\nJWT_SECRET=abc123\n")
    check = (REPO / "scripts/check.sh").read_text()
    block = check[check.index("setting() {") : check.index('echo\necho "  Memory')]
    script = f"KIT={kit}; RUNTIME={rt}; LIBRECHAT_DIR={rt}/LibreChat; HOME={tmp_path}\n{block}"
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True).stdout
    assert "gmail, only to: me@x.com" in out and "CalDAV" in out and "sign-up:    locked" in out and "mytool" in out
    for secret in ("top secret", "hunter2", "abc123"):
        assert secret not in out
