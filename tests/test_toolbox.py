"""Unit tests for the toolbox. They need no internet, Docker or Ollama.

Run:  pip install -r toolbox/requirements.txt -r requirements-dev.txt && pytest
"""

import importlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "toolbox"))


@pytest.fixture()
def tb(tmp_path, monkeypatch):
    """Import toolbox_server with an isolated temporary workspace."""
    monkeypatch.setenv("WORKSPACE", str(tmp_path / "ws"))
    import toolbox_server

    return importlib.reload(toolbox_server)


# ---------------------------------------------------------------- general ---
def test_current_datetime_has_year(tb):
    assert "20" in tb.current_datetime()


def test_all_tools_registered(tb):
    import asyncio

    names = {t.name for t in asyncio.run(tb.mcp.list_tools())}
    assert {
        "current_datetime",
        "web_search",
        "read_webpage",
        "list_files",
        "read_file",
        "write_file",
        "run_python",
        "stock_price_summary",
        "stock_indicators",
        "stock_fundamentals",
    } <= names


# ---------------------------------------------------------------- files ---
def test_write_read_list(tb):
    assert "Saved" in tb.write_file("notes/a.md", "hello")
    assert tb.read_file("notes/a.md") == "hello"
    assert "notes/a.md" in tb.list_files("notes")


@pytest.mark.parametrize("bad", ["../secret.txt", "/etc/passwd", "notes/../../x"])
def test_paths_outside_workspace_are_blocked(tb, bad):
    with pytest.raises(ValueError):
        tb.write_file(bad, "x")


def test_read_file_truncates(tb):
    tb.write_file("big.txt", "x" * (tb.MAX_TEXT + 100))
    assert tb.read_file("big.txt").endswith("[truncated]")


# ---------------------------------------------------------------- web ---
class FakeResponse:
    def __init__(self, text="", data=None):
        self.text, self._data = text, data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def test_web_search_formats_results(tb, monkeypatch):
    data = {"results": [{"title": f"T{i}", "url": f"https://e.com/{i}", "content": "c" * 500} for i in range(12)]}
    monkeypatch.setattr(tb.httpx, "get", lambda *a, **k: FakeResponse(data=data))
    out = json.loads(tb.web_search("anything", max_results=3))
    assert len(out) == 3 and out[0]["url"] == "https://e.com/0"
    assert len(out[0]["snippet"]) == 300


def test_web_search_empty(tb, monkeypatch):
    monkeypatch.setattr(tb.httpx, "get", lambda *a, **k: FakeResponse(data={"results": []}))
    assert tb.web_search("nothing") == "No results."


def test_read_webpage_strips_scripts_and_marks_untrusted(tb, monkeypatch):
    html = b"<html><script>evil()</script><nav>menu</nav><p>Real text</p></html>"
    monkeypatch.setattr(tb, "_fetch", lambda url, headers=None: ("text/html; charset=utf-8", html))
    out = tb.read_webpage("https://example.com")
    assert out.startswith("[UNTRUSTED WEB CONTENT]")
    assert "Real text" in out and "evil" not in out and "menu" not in out


def test_read_webpage_reads_online_pdf_and_text(tb, monkeypatch):
    import io

    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(40, 800, "Quarterly report: revenue grew 12 percent.")
    c.showPage()
    c.save()
    monkeypatch.setattr(tb, "_fetch", lambda url, headers=None: ("application/pdf", buf.getvalue()))
    assert "revenue grew 12 percent" in tb.read_webpage("https://example.com/r.pdf")
    monkeypatch.setattr(tb, "_fetch", lambda url, headers=None: ("text/plain", b"plain words here"))
    assert "plain words here" in tb.read_webpage("https://example.com/a.txt")
    monkeypatch.setattr(tb, "_fetch", lambda url, headers=None: ("image/png", b"\x89PNG"))
    assert "not a text page" in tb.read_webpage("https://example.com/a.png")


@pytest.mark.parametrize(
    "url,ip",
    [("http://localhost:3080", "127.0.0.1"), ("http://router.home/", "192.168.1.1"), ("http://x.test/", "10.0.0.5")],
)
def test_read_webpage_blocks_local_network(tb, monkeypatch, url, ip):
    monkeypatch.setattr(tb.socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", (ip, 0))])
    with pytest.raises(ValueError, match="home network"):
        tb._check_public_url(url)


def test_read_webpage_rejects_non_web_schemes(tb):
    with pytest.raises(ValueError, match="http"):
        tb._check_public_url("file:///etc/passwd")


def test_mcp_returns_explanation_instead_of_generic_error(tb, monkeypatch):
    """The model must see WHY a tool failed, not just 'Error executing tool'."""
    import asyncio

    def refuse(url, headers=None):
        req = tb.httpx.Request("GET", url)
        raise tb.httpx.HTTPStatusError("403", request=req, response=tb.httpx.Response(403, request=req))

    monkeypatch.setattr(tb, "_fetch", refuse)
    result = asyncio.run(tb.mcp.call_tool("read_webpage", {"url": "https://example.com"}))
    text = str(result)
    assert "refuses automated readers" in text and "HTTP 403" in text
    assert "honest identity" in text and "different search result" in text and "tell the user" in text
    result = asyncio.run(tb.mcp.call_tool("write_file", {"path": "../../etc/x", "content": "x"}))
    assert "outside the workspace" in str(result)


# ---------------------------------------------------------------- sandbox ---
def test_run_python_uses_locked_down_docker(tb, monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["input"] = cmd, kw["input"]
        return subprocess.CompletedProcess(cmd, 0, stdout="42\n", stderr="")

    monkeypatch.setattr(tb.subprocess, "run", fake_run)
    assert tb.run_python("print(6*7)") == "42"
    cmd = " ".join(seen["cmd"])
    for flag in ("--network none", "--read-only", "--cap-drop ALL", "no-new-privileges"):
        assert flag in cmd
    assert seen["input"] == "print(6*7)"


def test_run_python_timeout(tb, monkeypatch):
    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(tb.subprocess, "run", boom)
    assert "too long" in tb.run_python("while True: pass")


# ---------------------------------------------------------------- stocks ---
def _fake_prices(days=260, start=100.0, step=0.5):
    idx = pd.date_range("2025-01-01", periods=days, freq="B")
    close = [start + i * step for i in range(days)]
    return pd.DataFrame({"Close": close, "Volume": [1000] * days}, index=idx)


def test_stock_price_summary(tb, monkeypatch):
    monkeypatch.setattr(tb, "_history", lambda t, p: _fake_prices(days=11, step=1.0))
    out = json.loads(tb.stock_price_summary("TEST", "1mo"))
    assert out["first_close"] == 100 and out["last_close"] == 110
    assert out["period_return_pct"] == 10.0
    assert out["max_drawdown_pct"] == 0.0


def test_stock_indicators_uptrend(tb, monkeypatch):
    monkeypatch.setattr(tb, "_history", lambda t, p: _fake_prices())
    out = json.loads(tb.stock_indicators("TEST"))
    assert out["sma50"] > out["sma200"]
    assert "uptrend" in out["trend"]
    assert out["rsi14"] == 100.0


def test_stock_indicators_not_enough_data(tb, monkeypatch):
    monkeypatch.setattr(tb, "_history", lambda t, p: _fake_prices(days=50))
    assert "Not enough history" in tb.stock_indicators("TEST")


def test_stock_unknown_symbol(tb, monkeypatch):
    monkeypatch.setattr(tb, "_history", lambda t, p: pd.DataFrame())
    assert "No data" in tb.stock_price_summary("NOPE")


# ---------------------------------------------------------------- planner ---
class FakePost:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class FakeStream:
    """Stands in for httpx.stream(): yields Ollama's streamed chat chunks (JSON lines)."""

    def __init__(self, chunks, on_chunk=None):
        self.chunks, self.on_chunk, self.closed_early = chunks, on_chunk, False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_lines(self):
        for i, c in enumerate(self.chunks):
            if self.on_chunk:
                self.on_chunk(i)
            yield json.dumps(c)
        self.closed_early = False


def _chunk(thinking="", content="", done=False):
    return {"message": {"thinking": thinking, "content": content}, "done": done}


def test_ask_planner_returns_plan_when_thinking_finishes(tb, monkeypatch):
    seen = {}

    def fake_stream(method, url, json=None, timeout=None):
        seen["body"] = json
        return FakeStream(
            [_chunk(thinking="hmm"), _chunk(content="<think>x</think>1. run_python: 2+2"), _chunk(done=True)]
        )

    monkeypatch.setattr(tb.httpx, "stream", fake_stream)
    monkeypatch.setattr(tb.httpx, "post", lambda *a, **k: pytest.fail("fallback must not run"))
    out = tb.ask_planner("add numbers", context="none")
    assert out.startswith("NEXT STEPS FROM PLANNER") and "1. run_python" in out and "<think>" not in out
    assert "call ask_planner again" in out and "progress" in out
    assert seen["body"]["think"] is True and seen["body"]["stream"] is True
    assert seen["body"]["model"] == tb.PLANNER_MODEL
    system = seen["body"]["messages"][0]["content"]
    assert tb.dt.date.today().isoformat() in system  # planner knows today's date
    assert "current data" in system and "Next steps" in system


def test_ask_planner_second_round_sends_progress_and_detects_finished(tb, monkeypatch):
    seen = {}

    def fake_stream(method, url, json=None, timeout=None):
        seen["user"] = json["messages"][1]["content"]
        return FakeStream([_chunk(content="FINISHED: a table comparing the three tools"), _chunk(done=True)])

    monkeypatch.setattr(tb.httpx, "stream", fake_stream)
    out = tb.ask_planner("compare backup tools", progress="found restic, borg, duplicati with features")
    assert "Task: compare backup tools" in seen["user"] and "found restic, borg" in seen["user"]
    assert "ready to answer" in out and "FINISHED: a table" in out and "write the final answer" in out


def test_ask_planner_out_of_thinking_time_is_finished_by_fast_model(tb, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(tb.time, "monotonic", lambda: clock["t"])

    def advance(i):
        clock["t"] += 100  # each chunk "takes" 100 s, so the 150 s thinking budget runs out

    chunks = [_chunk(thinking=f"idea {i}: search, then score with python. ") for i in range(10)]
    monkeypatch.setattr(tb.httpx, "stream", lambda *a, **k: FakeStream(chunks, on_chunk=advance))
    seen = {}

    def fake_post(url, json=None, timeout=None):
        seen["body"] = json
        return FakePost({"message": {"content": "1. web_search: backup tools\nFinal answer should: compare"}})

    monkeypatch.setattr(tb.httpx, "post", fake_post)
    out = tb.ask_planner("compare backup tools")
    assert out.startswith("NEXT STEPS FROM PLANNER (the planner ran out of thinking time") and "1. web_search" in out
    assert seen["body"]["model"] == tb.PLANNER_FALLBACK_MODEL
    notes = seen["body"]["messages"][1]["content"]
    assert "idea 0" in notes and "idea 2" not in notes  # stopped after the budget, notes handed over


def test_ask_planner_slow_load_timeout_still_gives_plan(tb, monkeypatch):
    def slow(*a, **k):
        raise tb.httpx.ReadTimeout("loading model")

    monkeypatch.setattr(tb.httpx, "stream", slow)
    monkeypatch.setattr(tb.httpx, "post", lambda *a, **k: FakePost({"message": {"content": "1. current_datetime"}}))
    assert "1. current_datetime" in tb.ask_planner("x")


def test_ask_planner_ollama_down_and_total_failure(tb, monkeypatch):
    def down(*a, **k):
        raise tb.httpx.ConnectError("down")

    monkeypatch.setattr(tb.httpx, "stream", down)
    assert "Planner unavailable" in tb.ask_planner("x")

    monkeypatch.setattr(tb.httpx, "stream", lambda *a, **k: FakeStream([_chunk(done=True)]))
    monkeypatch.setattr(tb.httpx, "post", down)
    assert "Make a simple plan yourself" in tb.ask_planner("x")


# ---------------------------------------------------------------- documents ---
def _bag_of_words(text, dims=64):
    """Stand-in for the embedding model: hashed word counts (similar text -> similar vector)."""
    import hashlib

    vec = [0.0] * dims
    for word in re.findall(r"[a-z]+", text.lower().split(":", 1)[-1]):
        vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % dims] += 1.0
    return vec


@pytest.fixture()
def fake_embed(tb, monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        assert url.endswith("/api/embed") and json["model"] == tb.EMBED_MODEL
        calls.append(len(json["input"]))
        return FakePost({"embeddings": [_bag_of_words(t) for t in json["input"]]})

    monkeypatch.setattr(tb.httpx, "post", fake_post)
    return calls


def _make_pdf(path, pages):
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path))
    for text in pages:
        y = 800
        for line in text.split("\n"):
            c.drawString(40, y, line)
            y -= 14
        c.showPage()
    c.save()


def test_search_document_pdf_finds_right_page_and_caches(tb, fake_embed):
    pages = [
        "Alice was beginning to get very tired of sitting by her sister on the bank.",
        "The Cheshire Cat grinned and slowly vanished, leaving only the grin behind.",
        "The Queen of Hearts shouted off with her head at the croquet game.",
    ]
    _make_pdf(tb.WORKSPACE / "alice.pdf", pages)
    out = tb.search_document("alice.pdf", "the cat that vanished with a grin", top_k=1)
    assert "[page 2" in out and "Cheshire" in out
    first_calls = len(fake_embed)
    tb.search_document("alice.pdf", "queen croquet", top_k=1)
    assert len(fake_embed) == first_calls + 1  # index cached: only the query is embedded
    assert ".index" not in tb.list_files(".")  # cache folder is hidden


def test_read_document_pages_and_total(tb):
    _make_pdf(tb.WORKSPACE / "book.pdf", ["first page text", "second page text", "third page text"])
    out = tb.read_document("book.pdf", start_page=2, max_pages=1)
    assert "3 pages in total" in out and "second page" in out and "third page" not in out


def test_search_document_docx_and_txt(tb, fake_embed):
    import docx

    d = docx.Document()
    d.add_paragraph("Invoices are due within thirty days of delivery.")
    d.add_paragraph("Refunds require the original receipt and the product packaging.")
    d.save(tb.WORKSPACE / "policy.docx")
    assert "Refunds" in tb.search_document("policy.docx", "what do I need for a refund receipt", top_k=1)
    (tb.WORKSPACE / "notes.txt").write_text("Backup the server every Sunday night.\n" * 3)
    assert "Sunday" in tb.search_document("notes.txt", "when is the backup", top_k=1)


def test_document_errors(tb, fake_embed):
    assert "File not found" in tb.search_document("missing.pdf", "x")
    (tb.WORKSPACE / "photo.png").write_bytes(b"\x89PNG")
    assert "Unsupported file type" in tb.search_document("photo.png", "x")
    _make_pdf(tb.WORKSPACE / "blank.pdf", [""])
    assert "No text found" in tb.search_document("blank.pdf", "x")
    with pytest.raises(ValueError):
        tb.read_document("../outside.pdf")


def test_wikipedia_links_use_the_official_api_with_honest_identity(tb, monkeypatch):
    seen = {}

    def fake_fetch(url, headers=None):
        seen["url"], seen["headers"] = url, headers
        body = {"query": {"pages": [{"title": "Rsync", "extract": "rsync is a utility for transferring files."}]}}
        return "application/json", json.dumps(body).encode()

    monkeypatch.setattr(tb, "_fetch", fake_fetch)
    out = tb.read_webpage("https://en.m.wikipedia.org/wiki/Rsync#History")
    assert "rsync is a utility" in out and out.startswith("[UNTRUSTED WEB CONTENT]")
    assert seen["url"].startswith("https://en.wikipedia.org/w/api.php?") and "titles=Rsync" in seen["url"]
    assert "local-ai-agent" in seen["headers"]["User-Agent"]


def test_wikipedia_missing_article(tb, monkeypatch):
    body = {"query": {"pages": [{"title": "Nope", "missing": True}]}}
    monkeypatch.setattr(tb, "_fetch", lambda url, headers=None: ("application/json", json.dumps(body).encode()))
    with pytest.raises(ValueError, match="no article"):
        tb.read_webpage("https://de.wikipedia.org/wiki/Nope")


def test_refused_page_is_retried_once_with_honest_identity(tb, monkeypatch):
    calls = []

    def fetch(url, headers=None):
        calls.append(headers)
        if headers is None:
            req = tb.httpx.Request("GET", url)
            raise tb.httpx.HTTPStatusError("403", request=req, response=tb.httpx.Response(403, request=req))
        return "text/html", b"<p>Welcome, honest bot</p>"

    monkeypatch.setattr(tb, "_fetch", fetch)
    assert "Welcome, honest bot" in tb.read_webpage("https://example.org/page")
    assert len(calls) == 2 and "local-ai-agent" in calls[1]["User-Agent"]
