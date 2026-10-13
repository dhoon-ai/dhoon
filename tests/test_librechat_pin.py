"""LibreChat is pinned: its code to LIBRECHAT_REF and its images to exact builds, so a
change upstream can't break a new Dhoon install. No network: a local git repo plays GitHub."""

import os
import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def git(*args, cwd=None):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}  # fmt: skip
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


def test_librechat_images_are_pinned_to_exact_builds():
    services = yaml.safe_load((REPO / "config/librechat/docker-compose.override.yml").read_text())["services"]
    for name in ("api", "admin-panel", "rag_api"):  # the ones LibreChat ships as :latest
        image = services[name]["image"]
        assert "@sha256:" in image and len(image.split("@sha256:")[1]) == 64, name


def checkout(tmp_path, upstream, ref):
    env = {**os.environ, "RUNTIME_DIR": str(tmp_path / "runtime"), "LIBRECHAT_REF": ref,
           "LIBRECHAT_GIT_URL": f"file://{upstream}", "MODEL_MAIN": "a", "MODEL_FAST": "b"}  # fmt: skip
    cmd = f"source {REPO}/scripts/lib.sh && librechat_checkout"
    subprocess.run(["bash", "-c", cmd], env=env, check=True, capture_output=True, text=True)
    return tmp_path / "runtime" / "LibreChat"


def test_checkout_gets_exactly_the_pinned_commit_and_keeps_your_files(tmp_path):
    up = tmp_path / "upstream"
    up.mkdir()
    git("init", "-q", "-b", "main", cwd=up)
    git("config", "uploadpack.allowAnySHA1InWant", "true", cwd=up)  # GitHub allows fetching a commit
    (up / "docker-compose.yml").write_text("v1\n")
    git("add", "-A", cwd=up)
    git("commit", "-q", "-m", "one", cwd=up)
    first = git("rev-parse", "HEAD", cwd=up)
    (up / "docker-compose.yml").write_text("v2\n")
    git("commit", "-q", "-am", "two", cwd=up)
    second = git("rev-parse", "HEAD", cwd=up)

    lc = checkout(tmp_path, up, first)  # a new install
    assert git("rev-parse", "HEAD", cwd=lc) == first and (lc / "docker-compose.yml").read_text() == "v1\n"
    (lc / ".env").write_text("SECRET=1\n")  # made by script 05, never in LibreChat's repo
    lc = checkout(tmp_path, up, first)  # already there: nothing to do
    lc = checkout(tmp_path, up, second)  # a newer tested version
    assert git("rev-parse", "HEAD", cwd=lc) == second and (lc / "docker-compose.yml").read_text() == "v2\n"
    assert (lc / ".env").read_text() == "SECRET=1\n"


def test_an_install_that_followed_main_moves_to_the_pinned_commit(tmp_path):
    up = tmp_path / "upstream"
    up.mkdir()
    git("init", "-q", "-b", "main", cwd=up)
    git("config", "uploadpack.allowAnySHA1InWant", "true", cwd=up)
    for n in ("one", "two"):
        (up / "f").write_text(n)
        git("add", "-A", cwd=up)
        git("commit", "-q", "-m", n, cwd=up)
    pinned = git("rev-parse", "HEAD~1", cwd=up)
    (tmp_path / "runtime").mkdir()
    git("clone", "-q", f"file://{up}", str(tmp_path / "runtime" / "LibreChat"))  # how older installs did it
    lc = checkout(tmp_path, up, pinned)
    assert git("rev-parse", "HEAD", cwd=lc) == pinned


def test_every_container_dhoon_starts_has_a_fixed_version():
    """No ':latest' anywhere Dhoon starts or updates a container (LibreChat's own compose file
    is covered by the image pins in the override)."""
    import re

    lib = (REPO / "scripts/lib.sh").read_text()
    assert re.search(r'SEARXNG_IMAGE="\$\{SEARXNG_IMAGE:-searxng/searxng@sha256:[0-9a-f]{64}\}"', lib)
    for script in ("03_searxng.sh", "update.sh"):
        text = (REPO / "scripts" / script).read_text()
        assert "searxng/searxng:latest" not in text and '"$SEARXNG_IMAGE"' in text, script
