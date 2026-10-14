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

    return {"get": get, "post": post, "download": download, "search": search}


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


# ---------------------------------------------------------------- fact check ---
def test_factcheck_numbers_dates_and_names():
    import factcheck

    src = "- The Bank of Canada cuts its rate to 2.75%.\n| TSX (Canada) | 24,567.89 | -5.0% |\n- Mark Carney visits Delhi in 2025."
    mon, sun = dt.date(2025, 3, 3), dt.date(2025, 3, 9)
    assert factcheck.check("- Mar 4: The Bank of Canada cut its rate to 2.75%.", src, mon, sun) == []
    assert factcheck.check("- The TSX closed at 24,568 after it fell 5.0%.", src, mon, sun) == []  # rounding, sign
    assert factcheck.check("- Canadian leader Mark Carney went to Delhi.", src, mon, sun) == []  # "Canad" stem
    assert factcheck.check("- Mar 5: 3 ministers met in 2025.", src, mon, sun) == []  # small counts, known year
    bad = factcheck.check("- Mar 12: The Bank of Canada cut to 2.5%; Justin Trudeau spoke in 2024.", src, mon, sun)
    assert "number 2.5% is not in the source" in bad and "number 2024 is not in the source" in bad
    assert "date Mar 12 is outside Mar 03 - Mar 09" in bad and "name Justin is not in the source" in bad
    text, n = factcheck.mark("## Top stories\n- Rates cut to 2.75%.\n- Rates cut to 3.1%.", src)
    assert n == 1 and text.splitlines()[1] == "- Rates cut to 2.75%." and text.endswith("⚠ not in source: number 3.1%")
    assert "passed" in factcheck.footer(0) and "2 items not found" in factcheck.footer(2)


def test_a_made_up_number_gets_one_rewrite_then_is_marked(kb):
    calls = {"wiki": [], "ai": [], "search": []}
    io = fake_io(calls)
    replies = iter(["## Top stories\n- Mar 4: The Bank of Canada cut its rate to 2.35%.",
                    "## Top stories\n- Mar 4: The Bank of Canada cut its rate to 2.75%."])  # fmt: skip

    def post(body):
        calls["ai"].append(body["messages"][1]["content"])
        return Resp({"message": {"content": next(replies)}})

    path = kb.write_week(dt.date(2025, 3, 3), dt.date(2025, 4, 8), io["get"], io["search"], post, io["download"])
    note = path.read_text()
    assert "2.75%" in note and "2.35%" not in note and "_Fact check: passed" in note
    assert "NOT in the text above: number 2.35% is not in the source" in calls["ai"][1]
    replies = iter(["## Top stories\n- Mar 4: Rates fell to 1.9%.\n- Mar 12: The Bank of Canada cuts to 2.75%."] * 2)
    path = kb.write_week(dt.date(2025, 3, 10), dt.date(2025, 4, 8), io["get"], io["search"], post, io["download"])
    note = path.read_text()
    assert "- Mar 4: Rates fell to 1.9%. ⚠ not in source: number 1.9%, date Mar 4" in note
    assert "_Fact check: 2 items not found in the source, marked ⚠._" in note
    assert "## World market moves this week" in note  # the computed table stays
    out = kb.check_one("2025-W11")
    assert "⚠  - Mar 4: Rates fell to 1.9%." in out and "✓  - Mar 12: The Bank of Canada" in out


def test_check_all_fixes_notes_written_before_the_fact_check(kb):
    calls = {"wiki": [], "ai": [], "search": []}
    io = fake_io(calls)
    kb.update(today=dt.date(2025, 3, 24), max_minutes=0, start="2025-03", **io)
    good, bad = kb.KB / "weeks" / "2025-W10.md", kb.KB / "weeks" / "2025-W11.md"
    for p, line in ((good, "- Mar 4: The Bank of Canada cuts its rate to 2.75%."), (bad, "- Mar 11: Rates hit 77.7%.")):
        head, _, tail = kb._split_note(p.read_text())
        p.write_text(kb._rebuild(head, "## Top stories\n" + line, tail))  # as an older version wrote it
    calls["ai"].clear()
    r = kb.check_all(post=io["post"])
    assert r["passed"] >= 1 and r["rewritten"] == 1
    assert calls["ai"] and all(c.startswith("Week 2025-W11") for c in calls["ai"])  # only the bad one goes to the AI
    assert "77.7%" not in bad.read_text() and "_Fact check:" in bad.read_text()  # rewritten from its source
    assert "_Fact check: passed" in good.read_text() and good.read_text().count("_Fact check") == 1
    calls["ai"].clear()
    again = kb.check_all(post=io["post"])
    assert again["rewritten"] == 0 and calls["ai"] == []  # marked notes are re-checked by code only, no AI loop
    assert "passed the fact check" in kb.status()


def test_quiz_scores_right_unknown_and_wrong(kb):
    calls = {"wiki": [], "ai": [], "search": []}
    io = fake_io(calls)
    kb.update(today=dt.date(2025, 4, 8), max_minutes=0, start="2025-03", **io)
    answers = iter(["2.75", "unknown", "3.1"] + ["2.75"] * 20)

    def post(body):
        return Resp({"message": {"content": next(answers)}})

    r = kb.quiz(5, post=post, retrieve=lambda q: "passages", today=dt.date(2025, 4, 8))
    assert r["asked"] == 5 and (r["right"], r["unknown"], r["wrong"]) == (3, 1, 1)
    assert r["wrong_examples"][0]["question"].count("___") == 1 and r["wrong_examples"][0]["answer"] == "2.75%"
    assert "quiz:     2025-04-08: 3 right, 1 not in the notes, 1 wrong (made up) of 5" in kb.status()
    assert not kb.quiz_due(dt.date(2025, 5, 2))  # needs 8+ weekly notes (there are 6)
    for i in range(3):
        (kb.KB / "weeks" / f"2024-W{40 + i}.md").write_text("x")
    assert not kb.quiz_due(dt.date(2025, 4, 20)) and kb.quiz_due(dt.date(2025, 5, 2))  # once a month


def test_every_bullet_dated_the_same_day_is_caught():
    import factcheck

    src = ("### Tue 4 Mar 2025\n- Germany holds an election.\n\n### Thu 6 Mar 2025\n"
           "- The European Central Bank cuts rates to 2.5%.\n### Headlines from a news search this week\n- x")  # fmt: skip
    mon, sun = dt.date(2025, 3, 3), dt.date(2025, 3, 9)
    assert set(factcheck.day_sections(src)) == {dt.date(2025, 3, 4), dt.date(2025, 3, 6)}
    assert factcheck.check("- Mar 4: Germany holds an election.", src, mon, sun) == []
    assert factcheck.check("- Mar 6: The European Central Bank cut rates to 2.5%.", src, mon, sun) == []
    wrong = factcheck.check("- Mar 4: The European Central Bank cut rates to 2.5%.", src, mon, sun)
    assert wrong == ["date Mar 4 doesn't match the source (that story isn't on that day)"]
    prompt = __import__("news_kb").WEEK_PROMPT
    assert not re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b", prompt)  # no example date to copy


def test_a_wrong_month_is_put_right_by_code():
    import factcheck

    # found on the NUC: the model wrote "Mar 15" for a story under "### Tue 15 Sep 2026"
    src = ("### Mon 14 Sep 2026\n- Japan holds an election.\n\n### Tue 15 Sep 2026\n 2026-2027 El Niño event\n"
           "  - The Panama Canal Authority reduces daily ship transits through the canal from 36 to 32 as "
           "below-average rainfall and a strengthening El Niño lower water levels. (DW)\n")  # fmt: skip
    mon, sun = dt.date(2026, 9, 14), dt.date(2026, 9, 20)
    line = "- **Mar 15:** The Panama Canal Authority reduces daily ship transits from 36 to 32 due to El Niño."
    assert "date Mar 15 is outside Sep 14 - Sep 20" in factcheck.check(line, src, mon, sun)
    fixed, n = factcheck.fix_dates(line, src, mon, sun)
    assert n == 1 and fixed.startswith("- **Sep 15:** The Panama Canal") and factcheck.check(fixed, src, mon, sun) == []
    wrong_day, n = factcheck.fix_dates("- Mon 14 Sep: The Panama Canal Authority cuts transits to 32.", src, mon, sun)
    assert n == 1 and wrong_day.startswith("- Tue 15 Sep: The Panama Canal")  # weekday fixed too
    same, n = factcheck.fix_dates("- Tue 15 Sep: The Panama Canal Authority cuts transits to 32.", src, mon, sun)
    assert n == 0  # already right
    assert factcheck.check("- Tue 15 Sep: The Panama Canal Authority cuts transits to 32.", src, mon, sun) == []
