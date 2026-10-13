"""Download links for created files: signed, expiring, workspace-only, never run scripts."""

import importlib
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "toolbox"))
import fileshare  # noqa: E402


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("FILES_KEY", str(tmp_path / "key"))
    monkeypatch.setenv("FILES_HOST", "127.0.0.1")
    monkeypatch.setenv("WORKSPACE", str(tmp_path / "ws"))
    (tmp_path / "ws" / "reports").mkdir(parents=True)


def _ws(tmp_path):
    return tmp_path / "ws"


def _path(url):
    return urllib.parse.urlsplit(url).path


def test_link_roundtrip_and_key_is_private(tmp_path):
    f = _ws(tmp_path) / "reports" / "my budget.xlsx"
    f.write_bytes(b"x")
    url = fileshare.make_link(f, _ws(tmp_path))
    assert url.startswith("http://127.0.0.1:3081/f/") and url.endswith("/reports/my%20budget.xlsx")
    assert fileshare.resolve(_path(url), _ws(tmp_path)) == f.resolve()
    assert (tmp_path / "key").stat().st_mode & 0o077 == 0


def test_expired_tampered_and_other_files_are_refused(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    (ws / "a.txt").write_text("a")
    (ws / "b.txt").write_text("b")
    (tmp_path / "secret.txt").write_text("s")
    url = _path(fileshare.make_link(ws / "a.txt", ws))
    _, f, exp, sig, rel = url.split("/", 4)
    assert fileshare.resolve(f"/f/{exp}/{sig}/b.txt", ws) is None  # signature is for a.txt only
    assert fileshare.resolve(f"/f/{int(exp) + 1}/{sig}/a.txt", ws) is None  # longer expiry
    assert fileshare.resolve(f"/f/{exp}/AAAA{sig[4:]}/a.txt", ws) is None
    assert fileshare.resolve("/f/x/y/a.txt", ws) is None and fileshare.resolve("/", ws) is None
    # a correctly signed path that escapes the workspace is still refused
    bad = f"/f/{exp}/{fileshare._sign('../secret.txt', int(exp))}/../secret.txt"
    assert fileshare.resolve(bad, ws) is None
    with pytest.raises(ValueError):
        fileshare.make_link(tmp_path / "secret.txt", ws)
    later = time.time() + 8 * 86400
    monkeypatch.setattr(fileshare.time, "time", lambda: later)
    assert fileshare.resolve(url, ws) is None  # expired after 7 days


def test_server_sends_file_with_safe_headers_and_404_otherwise(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    (ws / "page.html").write_text("<script>alert(1)</script>")
    (ws / "chart.png").write_bytes(b"\x89PNG")
    srv = fileshare.serve(port=0, bind="127.0.0.1")
    monkeypatch.setenv("FILES_PORT", str(srv.server_address[1]))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(fileshare.make_link(ws / "page.html", ws)) as r:
            assert r.read() == b"<script>alert(1)</script>"
            assert r.headers["Content-Disposition"].startswith("attachment")  # never shown as a page
            assert "sandbox" in r.headers["Content-Security-Policy"]
            assert r.headers["X-Content-Type-Options"] == "nosniff"
        with urllib.request.urlopen(fileshare.make_link(ws / "chart.png", ws)) as r:
            assert r.headers["Content-Disposition"].startswith("inline")
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        for p in ("/", "/f/0/x/page.html", "/../etc/passwd", "/f/9999999999/abc/page.html"):
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(base + p)
            assert e.value.code == 404
    finally:
        srv.shutdown()


@pytest.fixture()
def tb(tmp_path, monkeypatch):
    import toolbox_server

    return importlib.reload(toolbox_server)


def test_write_file_and_file_link_give_a_markdown_link(tb):
    out = tb.write_file("reports/budget.csv", "rent,1800\n")
    assert "Download link" in out and "[budget.csv](http://127.0.0.1:3081/f/" in out
    assert "[budget.csv](http://" in tb.file_link("reports/budget.csv")
    assert "No such file" in tb.file_link("nope.xlsx")
    with pytest.raises(ValueError):
        tb.file_link("../key")


def test_run_python_links_only_the_files_it_made(tb, tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    (ws / "old.csv").write_text("old")
    (ws / ".cache").mkdir()

    def fake_run(cmd, **kw):  # the sandbox writes budget.xlsx (and a hidden cache file)
        (ws / "budget.xlsx").write_bytes(b"PK")
        (ws / ".cache" / "x").write_text("x")
        return subprocess.CompletedProcess(cmd, 0, stdout="saved\n", stderr="")

    monkeypatch.setattr(tb.subprocess, "run", fake_run)
    out = tb.run_python("df.to_excel('budget.xlsx')")
    assert out.startswith("saved") and "[budget.xlsx](http://" in out
    assert "old.csv" not in out and ".cache" not in out

    monkeypatch.setattr(tb.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "42\n", ""))
    assert tb.run_python("print(42)") == "42"  # no new files, no link
