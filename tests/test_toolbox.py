"""Unit tests for the toolbox. They need no internet, Docker or Ollama.

Run:  pip install -r toolbox/requirements.txt -r requirements-dev.txt && pytest
"""

import importlib
import json
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
    html = "<html><script>evil()</script><nav>menu</nav><p>Real text</p></html>"
    monkeypatch.setattr(tb.httpx, "get", lambda *a, **k: FakeResponse(text=html))
    out = tb.read_webpage("https://example.com")
    assert out.startswith("[UNTRUSTED WEB CONTENT]")
    assert "Real text" in out and "evil" not in out and "menu" not in out


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
