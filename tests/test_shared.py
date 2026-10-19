"""Shared pieces: heavy-job queue, one confidence scale, official numbers, the economic calendar,
and the knowledge-base inbox for plugins."""

import datetime as dt
import importlib
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "toolbox"))


@pytest.fixture()
def heavy(tmp_path, monkeypatch):
    monkeypatch.setenv("HEAVY_DIR", str(tmp_path))
    import heavy

    return importlib.reload(heavy)


def test_heavy_jobs_queue_and_chats_go_first(heavy):
    order = []
    started = threading.Event()

    def first():
        with heavy.heavy_job("knowledge base"):
            started.set()
            assert heavy.running()["name"] == "knowledge base"
            time.sleep(0.3)
            order.append("first done")

    t = threading.Thread(target=first)
    t.start()
    started.wait(2)
    with heavy.heavy_job("plugin", sleep=lambda s: time.sleep(0.05)):  # waits for the first one
        order.append("second ran")
    t.join()
    assert order == ["first done", "second ran"] and heavy.running() == {}
    with pytest.raises(TimeoutError):
        with heavy.heavy_job("a"):
            with heavy.heavy_job("b", wait_minutes=0, sleep=lambda s: None):
                pass
    assert not heavy.chatting()
    heavy.chat_seen()
    assert heavy.chatting()
    naps = []
    assert heavy.yield_to_chats(max_wait_seconds=45, sleep=naps.append) == 45 and naps == [15, 15, 15]


def test_one_confidence_scale():
    import sources

    assert sources.outlet_key("Reuters") == sources.outlet_key("https://www.reuters.com/world") == "reuters"
    assert list(sources.independent(["Reuters", "reuters.com", "BBC"])) == ["reuters", "bbc"]
    assert [sources.confidence(n) for n in (0, 1, 2, 3, 4)] == [2, 5, 7, 9, 10]
    assert sources.confidence(2, official=True) == 8 and sources.confidence(1, in_primary=False) == 3
    assert sources.confidence(0, in_primary=False) == 1


class Resp:
    def __init__(self, data=None, text=""):
        self.data, self.text = data, text

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def fake_get(url, params=None, headers=None, timeout=None):
    if "bankofcanada" in url:
        series = url.split("/observations/")[1].split("/")[0]
        return Resp(
            {"observations": [{"d": "2026-10-01", series: {"v": "2.75"}}, {"d": "2026-10-08", series: {"v": "2.50"}}]}
        )
    rows = "observation_date,X\n2025-09-01,320.0\n2026-08-01,325.0\n2026-09-01,329.6\n"
    return Resp(text=rows)


def fake_post(url, json=None, headers=None, timeout=None):
    points = [{"refPer": f"2025-{m:02d}-01", "value": 160.0} for m in range(9, 13)]
    points += [{"refPer": f"2026-{m:02d}-01", "value": 163.0} for m in range(1, 10)]
    return Resp([{"status": "SUCCESS", "object": {"vectorDataPoint": points}}])


def test_official_numbers():
    import econdata

    ca = {x["name"]: x for x in econdata.latest("canada", get=fake_get, post=fake_post)}
    rate = ca["Bank of Canada policy rate (%)"]
    assert rate["value"] == 2.5 and rate["change"] == -0.25 and rate["date"] == "2026-10-08"
    assert "bankofcanada.ca/valet/observations/V39079" in rate["source"]
    assert ca["Consumer prices (CPI, all-items)"]["change_12_months_pct"] == 1.9  # 163 vs 160 a year before
    us = {x["name"]: x for x in econdata.latest("usa", get=fake_get, post=fake_post)}
    assert us["Consumer prices (CPI, all items)"]["change_12_months_pct"] == 3.0
    with pytest.raises(ValueError):
        econdata.latest("mars")

    def broken(*a, **k):
        raise econdata.httpx.ConnectError("offline")

    assert all("not available now" in x["error"] for x in econdata.latest("usa", get=broken, post=broken))


def test_shared_economic_calendar(tmp_path, monkeypatch):
    monkeypatch.setenv("CALENDAR_DIR", str(tmp_path))
    import econ_calendar

    econ_calendar = importlib.reload(econ_calendar)
    results = [
        {
            "title": "Bank of Canada schedule",
            "snippet": "Next announcement: October 29, 2026",
            "url": "https://boc.example",
        }
    ]

    def ask(system, user):
        return json.dumps({"events": [{"date": "2026-10-29", "event": "BoC rate decision", "result": 1},
                                      {"date": "2026-11-20", "event": "invented", "result": 1}]})  # fmt: skip

    r = econ_calendar.refresh(dt.date(2026, 10, 19), regions=["canada"], search=lambda q: results, ask=ask)
    assert [e["event"] for e in r["events"]] == ["BoC rate decision"]  # the other date isn't in the text
    (tmp_path / "my-dates.json").write_text(json.dumps([{"date": "2026-10-22", "event": "My reminder"}]))
    events = econ_calendar.upcoming(dt.date(2026, 10, 19))
    assert [e["event"] for e in events] == ["My reminder", "BoC rate decision"]
    assert econ_calendar.upcoming(dt.date(2026, 10, 19), regions=["india"]) == [events[0]]  # own dates have no region
    assert not econ_calendar.due(dt.date(2026, 10, 20)) and econ_calendar.due(dt.date(2026, 10, 30))


def test_economy_tools_are_registered(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setenv("WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("CALENDAR_DIR", str(tmp_path / "cal"))
    import econ_calendar
    import toolbox_server

    importlib.reload(econ_calendar)
    tb = importlib.reload(toolbox_server)
    names = {t.name for t in asyncio.run(tb.mcp.list_tools())}
    assert {"economic_data", "economic_calendar"} <= names
    assert "No upcoming events known yet" in tb.economic_calendar()


def test_plugins_can_add_checked_public_stories_to_the_knowledge_base(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE", str(tmp_path))
    import news_kb

    kb = importlib.reload(news_kb)
    inbox = kb.KB / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "stories.jsonl").write_text("\n".join(json.dumps(x) for x in [
        {"date": "2025-03-05", "title": "Rent caps announced", "text": "Caps at 2.5%.", "source": "Example News", "confidence": 9},
        {"date": "2025-03-06", "title": "A rumour", "text": "?", "confidence": 4},
        {"date": "2025-04-01", "title": "Another week", "confidence": 9}, "not json"]))  # fmt: skip
    text = kb.inbox_items(dt.date(2025, 3, 3))
    assert text == "- [Wed 05 Mar] Rent caps announced: Caps at 2.5%. (Example News)"
