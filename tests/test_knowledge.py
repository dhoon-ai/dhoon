"""Knowledge base of world news and market events (toolbox/news_kb.py, search_knowledge)."""

import datetime as dt
import importlib
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "toolbox"))

# Shaped like Wikipedia's rendered "Portal:Current events/<Month> <Year>" page
DAY = """
<div class="current-events-main vevent" id="{y}_{month}_{d}">
 <div class="current-events-heading"><div class="current-events-title">
  <span class="summary">{month} {d}, {y}<span style="display:none">
  (<span class="bday dtstart published updated">{iso}</span>)</span> ({wd})</span>
  <span class="mw-editsection">edit history watch</span></div></div>
 <div class="current-events-content description">
  <p><b>Business and economy</b></p>
  <ul><li>The <a href="/wiki/Bank_of_Canada">Bank of Canada</a> cuts its rate to 2.75%. <a href="#">(CBC)</a></li></ul>
  <p><b>Armed conflicts and attacks</b></p>
  <ul><li><a href="#">Some war</a><ul><li>A ceasefire is agreed on day {d}.</li></ul></li></ul>
  <p><b>Sports</b></p>
  <ul><li>A football club wins a cup.</li></ul>
 </div>
</div>"""


def month_html(y: int, m: int) -> str:
    import calendar

    out = []
    for d in range(1, calendar.monthrange(y, m)[1] + 1):
        day = dt.date(y, m, d)
        out.append(DAY.format(y=y, month=calendar.month_name[m], d=d, iso=day.isoformat(), wd=f"{day:%A}"))
    return "<div>" + "".join(out) + "</div>"


class Resp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


@pytest.fixture()
def kb(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE", str(tmp_path / "ws"))
    (tmp_path / "ws").mkdir()
    import news_kb

    news_kb = importlib.reload(news_kb)
    news_kb._PRICES.clear()
    return news_kb


def fake_io(calls):
    def get(url, params):
        calls["wiki"].append(params["page"])
        _, _, name = params["page"].partition("/")
        month, year = name.split("_")
        import calendar

        return Resp({"parse": {"text": month_html(int(year), list(calendar.month_name).index(month))}})

    def post(body):
        calls["ai"].append(body["messages"][1]["content"])
        return Resp({"message": {"content": "## Top stories\n- Mar 4: The Bank of Canada cut its rate to 2.75%."}})

    def download(symbol, since):
        import pandas as pd

        days = pd.date_range(since, dt.date(2025, 4, 30), freq="B")
        base = 100.0 if symbol != "^TNX" else 4.0
        return pd.Series([base + i * (0.5 if symbol != "^TNX" else 0.01) for i in range(len(days))], index=days)

    def search(q):
        calls["search"].append(q)
        return {"results": [{"title": f"{q} headline", "content": "snippet"}]}

    def confirm(q):  # the general search used to count independent sources
        calls.setdefault("confirm", []).append(q)
        return {"results": [
            {"url": "https://www.reuters.com/a", "title": "Bank of Canada cuts rate to 2.75%", "content": ""},
            {"url": "https://apnews.com/b", "title": "Canada central bank lowers rate", "content": "Bank of Canada 2.75%"},
            {"url": "https://en.wikipedia.org/wiki/x", "title": "Bank of Canada 2.75%", "content": ""},
            {"url": "https://example.org/c", "title": "Unrelated story", "content": "nothing"},
        ]}  # fmt: skip

    return {"get": get, "post": post, "download": download, "search": search, "confirm": confirm}


def test_parse_keeps_news_and_markets_but_not_sports(kb):
    days = kb.parse_days(month_html(2025, 3))
    assert len(days) == 31
    text = days[dt.date(2025, 3, 4)]
    assert "- The Bank of Canada cuts its rate to 2.75%. (CBC)" in text  # links kept inside the line
    assert "  - A ceasefire is agreed on day 4." in text  # nested bullet indented
    assert "[Business and economy]" in text and "football" not in text and "Sports" not in text
    assert "edit history" not in text


def test_weeks_and_labels(kb):
    assert kb.week_id(dt.date(2025, 3, 3)) == "2025-W10" and kb.monday_of("2025-W10") == dt.date(2025, 3, 3)
    weeks = kb.complete_weeks(dt.date(2025, 3, 17), dt.date(2025, 3, 1))
    assert weeks == [dt.date(2025, 2, 24), dt.date(2025, 3, 3), dt.date(2025, 3, 10)]  # the week of the 17th isn't over
    assert kb.week_label(dt.date(2025, 3, 31)) == "2025-W14 (31 Mar - 6 Apr 2025)"
    with pytest.raises(ValueError):
        kb.monday_of("March")


def test_a_week_across_two_months_reads_both_pages_and_caches_finished_months(kb):
    calls = {"wiki": [], "ai": [], "search": []}
    io = fake_io(calls)
    text, sources = kb.week_text(dt.date(2025, 3, 31), dt.date(2025, 6, 1), io["get"])
    assert "### Mon 31 Mar 2025" in text and "### Sun 6 Apr 2025" in text
    assert [s.rsplit("/", 1)[1] for s in sources] == ["March_2025", "April_2025"]
    assert len(calls["wiki"]) == 2
    kb.week_text(dt.date(2025, 3, 31), dt.date(2025, 6, 1), io["get"])  # finished months come from the cache
    assert len(calls["wiki"]) == 2


def test_market_table_from_prices(kb):
    data = {"S&P 500 (US)": {dt.date(2025, 3, 7): 100.0, dt.date(2025, 3, 14): 95.0},
            "US 10-year yield (%)": {dt.date(2025, 3, 7): 4.2, dt.date(2025, 3, 14): 4.35}}  # fmt: skip
    table = kb.market_table(dt.date(2025, 3, 10), dt.date(2025, 3, 16), data)
    assert "| S&P 500 (US) | 95.00 | -5.0% |" in table
    assert "| US 10-year yield (%) | 4.35 | +0.15 pts |" in table
    assert kb.market_table(dt.date(2025, 3, 17), dt.date(2025, 3, 23), data) == ""  # no prices that week


def test_update_writes_newest_weeks_first_then_months_and_overview(kb):
    calls = {"wiki": [], "ai": [], "search": []}
    r = kb.update(today=dt.date(2025, 4, 8), max_minutes=0, start="2025-03", **fake_io(calls))
    assert r["written"][0] == "2025-W14" and r["written"][-1] == "2025-W09" and r["left"] == 0
    assert r["months"] == ["2025-03"] and not r["failed"]
    note = (kb.KB / "weeks" / "2025-W12.md").read_text()
    assert note.startswith("# World news and markets, week 2025-W12 (17 Mar - 23 Mar 2025)")
    assert "untrusted" in note and "Bank of Canada" in note and "## World market moves this week" in note
    assert "| S&P 500 (US) |" in note and "Portal:Current_events/March_2025" in note
    assert (kb.KB / "raw" / "2025-W12.txt").exists() and (kb.KB / "overview.md").exists()
    assert "World markets this week (from prices)" in calls["ai"][0]
    assert calls["search"] == kb.NEWS_SEARCHES  # only for last week (W14); older weeks rely on Wikipedia
    # nothing left to do: a second run writes nothing and asks the AI nothing
    calls["ai"].clear()
    r = kb.update(today=dt.date(2025, 4, 8), max_minutes=0, start="2025-03", **fake_io(calls))
    assert r["written"] == [] and calls["ai"] == []


def test_last_week_also_gets_world_and_market_headlines(kb):
    calls = {"wiki": [], "ai": [], "search": []}
    kb.update(today=dt.date(2025, 3, 18), max_minutes=0, start="2025-03-10", **fake_io(calls))
    assert calls["search"] == kb.NEWS_SEARCHES
    assert "stock markets headline" in calls["ai"][0]


def test_time_limit_and_failures(kb, monkeypatch):
    calls = {"wiki": [], "ai": [], "search": []}
    io = fake_io(calls)
    clock = iter(range(0, 10**7, 3600))  # every week "takes" an hour
    monkeypatch.setattr(kb.time, "monotonic", lambda: next(clock))
    r = kb.update(today=dt.date(2025, 4, 8), max_minutes=90, start="2025-03", **io)
    assert len(r["written"]) == 1 and r["stopped_for_time"] and r["months"] == [] and r["left"] == 5

    def broken(body):
        raise kb.httpx.ConnectError("Ollama is not running")

    r = kb.update(today=dt.date(2025, 4, 8), max_minutes=0, start="2025-03", **{**io, "post": broken})
    assert r["written"] == [] and len(r["failed"]) == 5 and r["left"] == 5  # tried again next night


def test_status_and_switch_off(kb, monkeypatch, capsys):
    assert "0 of" in kb.status() and "world news events and market events" in kb.status()
    monkeypatch.setenv("KNOWLEDGE", "off")
    assert kb.main(["--update"]) == 0
    assert "switched off" in capsys.readouterr().out


@pytest.fixture()
def tb(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("FILES_KEY", str(tmp_path / "files.key"))
    import toolbox_server

    return importlib.reload(toolbox_server)


def test_search_knowledge_tool(tb, monkeypatch):
    import asyncio

    from toolset import documents

    assert "search_knowledge" in {t.name for t in asyncio.run(tb.mcp.list_tools())}
    assert "empty" in tb.search_knowledge("rates")
    weeks = tb.WORKSPACE / "knowledge" / "weeks"
    weeks.mkdir(parents=True)
    (weeks / "2025-W10.md").write_text(
        "# Week 2025-W10\n\nThe Bank of Canada cut its interest rate to 2.75% on March 12."
    )
    (weeks / "2025-W11.md").write_text(
        "# Week 2025-W11\n\nA large earthquake hit a coastal region far from any market."
    )

    def fake_embed(texts):
        return [[1.0, 0.0] if ("rate" in t.lower()) else [0.0, 1.0] for t in texts]

    monkeypatch.setattr(documents, "_embed", fake_embed)
    out = tb.search_knowledge("Bank of Canada interest rate", top_k=1)
    assert "[knowledge/weeks/2025-W10.md]" in out and "2.75%" in out and "earthquake" not in out
    assert "UNTRUSTED" in out and "weeks 2025-W10 to 2025-W11" in out
    from toolset import knowledge

    assert knowledge.index_all() == 2


def test_timer_and_settings_are_wired_up():
    repo = Path(__file__).resolve().parents[1]
    s04 = (repo / "scripts" / "04_toolbox.sh").read_text()
    assert "ai-knowledge.timer" in s04 and "news_kb.py --update" in s04 and "OnCalendar=*-*-* 22:30:00" in s04
    assert "ai-knowledge.timer" in (repo / "scripts" / "update.sh").read_text()
    assert "KNOWLEDGE=on" in (repo / "config" / "models.env.example").read_text()
    instructions = (repo / "config" / "agent-instructions.md").read_text()
    assert "search_knowledge" in instructions


# ---------------------------------------------------------------- confidence from independent sources ---
def test_items_get_a_confidence_from_independent_sources(kb):
    calls = {"wiki": [], "ai": [], "search": []}
    io = fake_io(calls)
    path = kb.write_week(dt.date(2025, 3, 3), dt.date(2025, 4, 8), io["get"], io["search"], io["post"], io["download"],
                         io["confirm"])  # fmt: skip
    note = path.read_text()
    # cited by Wikipedia (CBC) + reuters.com + apnews.com; Wikipedia itself and unrelated sites don't count
    assert (
        "- Mar 4: The Bank of Canada cut its rate to 2.75%. _(confidence 9/10: CBC, reuters.com, apnews.com)_" in note
    )
    assert "_Confidence: each item is rated 1-10" in note and "average 9.0/10" in note
    assert calls["confirm"] == ["Bank Canada 2.75% March 2025"]
    assert "## Top stories" in note and "confidence" not in note.split("## Top stories")[0]
    assert "confidence: 1 items, average 9.0/10; 1 confirmed by 2+ sources" in kb.status()


def test_confidence_scale():
    import news_kb as kb

    assert kb.confidence(False, 0) == 1 and kb.confidence(True, 0) == 2
    assert [kb.confidence(True, n) for n in (1, 2, 3, 4, 9)] == [5, 7, 9, 10, 10]
    assert kb.confidence(False, 1) == 3  # not in the week's Wikipedia text: 2 points less
    assert kb._terms("- **Tue 15 Sep:** The Panama Canal Authority cuts transits from 36 to 32.") == [
        "Panama", "Canal", "Authority", "36", "32"]  # fmt: skip
    assert kb._outlet_key("Reuters") == kb._outlet_key("www.reuters.com") == "reuters"


def test_items_nobody_else_reports_get_a_low_score(kb):
    raw = "### Tue 4 Mar 2025\n- Germany holds an election. (DW)\n"
    rated, avg = kb.rate("## Top stories\n- Tue 4 Mar: Germany holds an election.\n- Tue 4 Mar: Atlantis rises 77 metres.",
                         raw, dt.date(2025, 3, 3), search=lambda q: {"results": []})  # fmt: skip
    lines = rated.splitlines()
    assert lines[0] == "## Top stories"
    assert lines[1].endswith("_(confidence 5/10: DW)_") and lines[2].endswith("_(confidence 1/10: no source found)_")
    assert avg == 3.0
    broken = kb.rate("- Tue 4 Mar: Germany holds an election.", raw, dt.date(2025, 3, 3),
                     search=lambda q: (_ for _ in ()).throw(kb.httpx.ConnectError("SearXNG down")))  # fmt: skip
    assert broken[0].endswith("_(confidence 5/10: DW)_")  # search down: Wikipedia's citations still count


def test_rate_all_adds_confidence_and_removes_old_fact_check_marks(kb):
    calls = {"wiki": [], "ai": [], "search": []}
    io = fake_io(calls)
    kb.update(today=dt.date(2025, 3, 17), max_minutes=0, start="2025-03", **io)
    p = kb.KB / "weeks" / "2025-W10.md"
    head, summary, tail = kb._split_note(p.read_text())
    old = "## Top stories\n- Mar 4: The Bank of Canada cut its rate to 2.75%. ⚠ not in source: date Mar 4\n\n_Fact check: 1 item._"
    p.write_text(f"{head}\n\n{old}\n{tail}")  # as the removed fact check left it
    calls["ai"].clear()
    r = kb.rate_all(search=io["confirm"])
    text = p.read_text()
    assert r["rated"] == 1 and calls["ai"] == []  # searches only, no AI
    assert "⚠" not in text and "Fact check" not in text and "(confidence 9/10" in text
    assert "## World market moves this week" in text  # the computed table is kept
    assert kb.rate_all(search=io["confirm"])["rated"] == 0  # done once


def test_prompt_has_no_example_date_to_copy():
    import news_kb

    assert not re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b", news_kb.WEEK_PROMPT)
