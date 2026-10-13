"""The website (site/) and its publishing workflow: the page offers the install command,
and the workflow publishes install.sh next to it."""

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def test_page_offers_the_install_command():
    page = (REPO / "site" / "index.html").read_text()
    assert "install.sh | bash" in page and 'new URL("install.sh", location.href)' in page
    assert "docs/GUIDE.md" in page and "<title>Dhoon" in page
    assert (REPO / "site" / ".nojekyll").exists()


def test_workflow_publishes_the_site_and_the_installer():
    wf = yaml.safe_load((REPO / ".github" / "workflows" / "pages.yml").read_text())
    run = "\n".join(s.get("run", "") for s in wf["jobs"]["deploy"]["steps"])
    assert "cp -r site/. _site/" in run and "install.sh" in run
    for f in run.split("cp install.sh", 1)[1].split("\n")[0].split()[:-1]:
        assert (REPO / f).exists(), f
