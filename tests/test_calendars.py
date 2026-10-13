"""Calendar tools: local calendar, read-only links, CalDAV and Google, all faked (no internet)."""

import importlib
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "toolbox"))
pytest.importorskip("icalendar")
pytest.importorskip("recurring_ical_events")

LINK_ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Google Inc//Google Calendar 70.9054//EN
BEGIN:VEVENT
UID:work-1@google.com
DTSTART:20261014T130000Z
DTEND:20261014T140000Z
SUMMARY:Team meeting
LOCATION:Office
END:VEVENT
END:VCALENDAR
"""


@pytest.fixture()
def cal(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("CALENDAR_ENV_FILE", str(tmp_path / "calendar.env"))
    monkeypatch.setenv("CALENDAR_TIMEZONE", "America/Toronto")
    monkeypatch.setenv("GOOGLE_CLIENT_FILE", str(tmp_path / "client.json"))
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", str(tmp_path / "token.json"))
    for k in ("CALENDAR_ICS_URLS", "CALDAV_URL", "CALENDAR_ADD_TO"):
        monkeypatch.delenv(k, raising=False)
    import calendars

    return importlib.reload(calendars)


def test_private_local_calendar_add_list_delete(cal):
    out = cal.add_event("Dentist", "2026-10-14 09:30", location="Main St")
    assert "Added 'Dentist' on Wed 2026-10-14 09:30-10:30" in out and "local calendar" in out
    cal.add_event("Mom's birthday", "2026-10-16")
    listing = cal.list_events("2026-10-13", "2026-10-20")
    assert "Wed 2026-10-14 09:30-10:30  Dentist at Main St  (local)" in listing
    assert "Fri 2026-10-16 (all day)  Mom's birthday" in listing
    ident = listing.split("[id ")[1].split("]")[0]
    assert "Deleted" in cal.delete_event(ident)
    assert "Dentist" not in cal.list_events("2026-10-13", "2026-10-20")
    assert cal.local_file().read_bytes().startswith(b"BEGIN:VCALENDAR")  # a normal .ics file


def test_weekly_event_keeps_its_local_time_across_daylight_saving(cal):
    cal.add_event("Gym", "2026-10-27 18:00", repeat="weekly")  # Canada leaves DST on 1 Nov
    listing = cal.list_events("2026-10-26", "2026-11-12")
    assert listing.count("18:00-19:00  Gym (repeats)  (local)") == 3  # 18:00 before and after the change
    assert "Tue 2026-11-03 18:00" in listing


def test_read_only_link_shows_events_but_cannot_delete_them(cal, monkeypatch):
    monkeypatch.setenv("CALENDAR_ICS_URLS", "Work|webcal://calendar.google.com/secret/basic.ics")
    seen = []
    monkeypatch.setattr(
        cal.httpx,
        "get",
        lambda url, **k: seen.append(url) or httpx.Response(200, content=LINK_ICS, request=httpx.Request("GET", url)),
    )
    listing = cal.list_events("2026-10-13", "2026-10-20")
    assert "Wed 2026-10-14 09:00-10:00  Team meeting at Office  (Work)" in listing  # 13:00 UTC = 9:00 Toronto
    assert seen == ["https://calendar.google.com/secret/basic.ics"]
    with pytest.raises(cal.CalendarError, match="read-only"):
        cal.delete_event("Work:work-1@google.com")


def test_a_broken_link_is_reported_not_fatal(cal, monkeypatch):
    monkeypatch.setenv("CALENDAR_ICS_URLS", "https://example.invalid/x.ics")

    def boom(url, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(cal.httpx, "get", boom)
    out = cal.list_events("2026-10-13", "2026-10-20")
    assert "(nothing scheduled)" in out and "Note: link 1: could not be read (ConnectError)" in out


def test_google_calendar_read_and_write(cal, monkeypatch, tmp_path):
    (tmp_path / "client.json").write_text(json.dumps({"installed": {"client_id": "id", "client_secret": "s"}}))
    (tmp_path / "token.json").write_text(json.dumps({"refresh_token": "r", "access_token": "old", "expires_at": 0}))
    calls = []

    def fake_post(url, data=None, timeout=None):
        calls.append(("POST", url, data))
        return httpx.Response(
            200, json={"access_token": "fresh", "expires_in": 3600}, request=httpx.Request("POST", url)
        )

    def fake_request(method, url, headers=None, timeout=None, **kw):
        calls.append((method, url, kw, headers["Authorization"]))
        req = httpx.Request(method, url)
        if method == "GET":
            return httpx.Response(200, request=req, json={"items": [
                {"id": "g1", "summary": "Flight to Delhi", "start": {"dateTime": "2026-10-15T20:00:00-04:00"},
                 "end": {"dateTime": "2026-10-15T23:00:00-04:00"}},
                {"id": "g2", "summary": "Holiday", "start": {"date": "2026-10-17"}, "end": {"date": "2026-10-18"}}]})  # fmt: skip
        if method == "POST":
            return httpx.Response(200, request=req, json={"id": "new1"})
        return httpx.Response(204, request=req)

    monkeypatch.setattr(cal.httpx, "post", fake_post)
    monkeypatch.setattr(cal.httpx, "request", fake_request)
    listing = cal.list_events("2026-10-13", "2026-10-20")
    assert "Thu 2026-10-15 20:00-23:00  Flight to Delhi  (Google) [id google:g1]" in listing
    assert "Sat 2026-10-17 (all day)  Holiday" in listing
    assert json.loads((tmp_path / "token.json").read_text())["access_token"] == "fresh"  # refreshed once
    out = cal.add_event("Call bank", "2026-10-16 10:00", repeat="monthly")
    assert "google calendar [id google:new1]" in out  # Google connected: new events go there
    body = [c for c in calls if c[0] == "POST" and "events" in c[1]][0][2]["json"]
    assert body["start"] == {"dateTime": "2026-10-16T10:00:00-04:00", "timeZone": "America/Toronto"}
    assert body["recurrence"] == ["RRULE:FREQ=MONTHLY"]
    assert cal.delete_event("google:g1") == "Deleted event google:g1."
    assert all(c[3] == "Bearer fresh" for c in calls if c[0] in ("GET", "DELETE"))
    assert "local" in cal.add_event("Note", "2026-10-18", calendar="local")


def test_caldav_calendar(cal, monkeypatch):
    monkeypatch.setenv("CALDAV_URL", "https://caldav.icloud.com")
    store = {}

    class FakeObj:
        def __init__(self, data):
            self.data = data

        def delete(self):
            store.clear()

    class FakeCal:
        def save_event(self, ics):
            store["ev"] = ics

        def search(self, start, end, event, expand):
            return [FakeObj(store["ev"])] if store else []

        def event_by_uid(self, uid):
            return FakeObj(store["ev"])

    monkeypatch.setattr(cal, "_caldav_calendar", lambda: FakeCal())
    out = cal.add_event("Parent meeting", "2026-10-15 17:00")
    assert "caldav calendar" in out and "TZID=America/Toronto" in store["ev"]
    assert "Thu 2026-10-15 17:00-18:00  Parent meeting  (caldav)" in cal.list_events("2026-10-13", "2026-10-20")
    assert "Deleted" in cal.delete_event(out.split("[id ")[1].rstrip("].")) and not store


def test_bad_input_is_explained(cal):
    with pytest.raises(cal.CalendarError, match="YYYY-MM-DD"):
        cal.add_event("x", "next tuesday")
    with pytest.raises(cal.CalendarError, match="repeat must be"):
        cal.add_event("x", "2026-10-14", repeat="fortnightly")


def test_google_sign_in_saves_a_private_token(cal, monkeypatch, tmp_path):
    import threading
    import urllib.parse
    import urllib.request
    import webbrowser

    (tmp_path / "client.json").write_text(json.dumps({"installed": {"client_id": "id", "client_secret": "s"}}))

    def browser(url):  # the user signs in; Google sends the browser back to the local address
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert q["scope"] == ["https://www.googleapis.com/auth/calendar.events"] and q["code_challenge_method"] == [
            "S256"
        ]
        back = f"{q['redirect_uri'][0]}/?code=abc&state={q['state'][0]}"
        threading.Thread(target=lambda: urllib.request.urlopen(back).read(), daemon=True).start()

    sent = {}

    def token(url, data=None, timeout=None):
        sent.update(data)
        return httpx.Response(200, json={"refresh_token": "R", "access_token": "A", "expires_in": 3600},
                              request=httpx.Request("POST", url))  # fmt: skip

    monkeypatch.setattr(webbrowser, "open", browser)
    monkeypatch.setattr(cal.httpx, "post", token)
    cal.google_setup()
    saved = json.loads((tmp_path / "token.json").read_text())
    assert saved["refresh_token"] == "R" and sent["code"] == "abc" and sent["code_verifier"]
    assert oct((tmp_path / "token.json").stat().st_mode)[-3:] == "600" and cal.google_connected()
