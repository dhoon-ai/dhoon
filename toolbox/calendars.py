"""Calendars for the Assistant: one list of events from every calendar you connect.

Sources (settings in config/calendar.env, see config/calendar.env.example):
  - your private calendar on this computer (always on): ~/agent-workspace/calendar/calendar.ics,
    a normal .ics file you can also import into Google Calendar or your phone;
  - read-only calendar links (CALENDAR_ICS_URLS), e.g. Google Calendar's "Secret address in
    iCal format" or a published Outlook calendar;
  - a CalDAV calendar with an app password (CALDAV_URL...): iCloud, Nextcloud, Fastmail...;
  - Google Calendar with read and write access (python3 toolbox/calendars.py --google-setup).
New events go to CALENDAR_ADD_TO (local, caldav or google; default: the first connected one
that can be written, else local).
"""

import base64
import datetime as dt
import hashlib
import json
import os
import secrets
import sys
import time
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

KIT = Path(__file__).resolve().parents[1]
ENV_FILE = Path(os.environ.get("CALENDAR_ENV_FILE", str(KIT / "config" / "calendar.env")))
WORKSPACE = Path(os.environ.get("WORKSPACE", str(Path.home() / "agent-workspace")))
GOOGLE_CLIENT = Path(os.environ.get("GOOGLE_CLIENT_FILE", str(KIT / "config" / "google_oauth_client.json")))
GOOGLE_TOKEN = Path(os.environ.get("GOOGLE_TOKEN_FILE", str(KIT / "runtime" / "google_token.json")))
GOOGLE_API = os.environ.get("GOOGLE_CALENDAR_API", "https://www.googleapis.com/calendar/v3")
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_SCOPE = "https://www.googleapis.com/auth/calendar.events"
REPEATS = {"daily": "DAILY", "weekly": "WEEKLY", "monthly": "MONTHLY", "yearly": "YEARLY"}
_ICS_CACHE: dict[str, tuple[float, bytes]] = {}


class CalendarError(Exception):
    pass


def setting(key: str, default: str = "") -> str:
    if key in os.environ:
        return os.environ[key].strip()
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                if k.strip() == key:
                    return v.strip().strip('"').strip("'")
    return default


def local_tz() -> ZoneInfo:
    name = setting("CALENDAR_TIMEZONE") or os.environ.get("TZ", "")
    if not name and Path("/etc/timezone").exists():
        name = Path("/etc/timezone").read_text().strip()
    if not name and Path("/etc/localtime").is_symlink():
        name = str(Path("/etc/localtime").resolve()).split("zoneinfo/")[-1]
    try:
        return ZoneInfo(name or "UTC")
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


def parse_when(text: str) -> tuple[dt.datetime | dt.date, bool]:
    """'2026-10-14 09:30' (local time) or '2026-10-14' (all day)."""
    text = text.strip().replace("T", " ")
    try:
        if len(text) <= 10:
            return dt.date.fromisoformat(text), True
        return dt.datetime.fromisoformat(text).replace(tzinfo=None).replace(tzinfo=local_tz()), False
    except ValueError:
        raise CalendarError(f"Can't read the time '{text}': use YYYY-MM-DD HH:MM or YYYY-MM-DD.") from None


def _as_local(value) -> dt.datetime | dt.date:
    if isinstance(value, dt.datetime):
        return (value if value.tzinfo else value.replace(tzinfo=local_tz())).astimezone(local_tz())
    return value


# ------------------------------------------------------------------ .ics (local file and links) ---
def local_file() -> Path:
    return WORKSPACE / "calendar" / "calendar.ics"


def _load_local():
    from icalendar import Calendar

    f = local_file()
    if f.exists():
        return Calendar.from_ical(f.read_bytes())
    cal = Calendar()
    cal.add("prodid", "-//local-ai-agent//calendar//EN")
    cal.add("version", "2.0")
    return cal


def _events_from_ics(cal, start: dt.datetime, end: dt.datetime, source: str, writable: bool) -> list[dict]:
    import recurring_ical_events

    out = []
    repeating = {str(c.get("UID")) for c in cal.walk("VEVENT") if c.get("RRULE") or c.get("RDATE")}
    for ev in recurring_ical_events.of(cal).between(start, end):
        s, e = ev.get("DTSTART").dt, ev.get("DTEND").dt if ev.get("DTEND") else None
        out.append(
            {
                "id": f"{source}:{ev.get('UID')}" if writable else "",
                "source": source,
                "title": str(ev.get("SUMMARY", "(no title)")),
                "start": _as_local(s),
                "end": _as_local(e) if e else None,
                "all_day": not isinstance(s, dt.datetime),
                "location": str(ev.get("LOCATION", "") or ""),
                "repeats": str(ev.get("UID")) in repeating,
            }
        )
    return out


def _vevent(title, start, end, all_day, location, notes, repeat, uid=None):
    from icalendar import Event

    ev = Event()
    ev.add("uid", uid or f"{uuid.uuid4()}@local-ai-agent")
    ev.add("dtstamp", dt.datetime.now(dt.timezone.utc))
    ev.add("summary", title)
    ev.add("dtstart", start)  # local time with its time zone, so a weekly 9:00 stays 9:00 after DST
    ev.add("dtend", end)
    if location:
        ev.add("location", location)
    if notes:
        ev.add("description", notes)
    if repeat:
        ev.add("rrule", {"freq": REPEATS[repeat]})
    return ev


def _with_timezones(cal) -> None:
    """Add the VTIMEZONE definitions other calendar apps need to read local times."""
    try:
        cal.add_missing_timezones()
    except Exception:  # noqa: BLE001 - older icalendar: times still carry their TZID
        pass


def _ics_url_events(start, end) -> tuple[list[dict], list[str]]:
    from icalendar import Calendar

    events, problems = [], []
    for n, item in enumerate([x.strip() for x in setting("CALENDAR_ICS_URLS").split(",") if x.strip()], 1):
        name, url = item.split("|", 1) if "|" in item else (f"link {n}", item)
        url = url.strip().replace("webcal://", "https://", 1)
        try:
            cached = _ICS_CACHE.get(url)
            if not cached or time.time() - cached[0] > 300:
                r = httpx.get(url, timeout=30, follow_redirects=True)
                r.raise_for_status()
                cached = _ICS_CACHE[url] = (time.time(), r.content)
            events += _events_from_ics(Calendar.from_ical(cached[1]), start, end, name.strip(), writable=False)
        except Exception as e:  # noqa: BLE001
            problems.append(f"{name.strip()}: could not be read ({type(e).__name__})")
    return events, problems


# ------------------------------------------------------------------ CalDAV ---
def _caldav_calendar():
    import caldav

    client = caldav.DAVClient(
        url=setting("CALDAV_URL"), username=setting("CALDAV_USERNAME"), password=setting("CALDAV_PASSWORD")
    )
    calendars = client.principal().calendars()
    wanted = setting("CALDAV_CALENDAR").lower()
    for c in calendars:
        if not wanted or (c.name or "").lower() == wanted:
            return c
    raise CalendarError(f"No CalDAV calendar named '{setting('CALDAV_CALENDAR')}'.")


def _caldav_events(start, end) -> list[dict]:
    from icalendar import Calendar

    out = []
    for obj in _caldav_calendar().search(start=start, end=end, event=True, expand=True):
        out += _events_from_ics(Calendar.from_ical(obj.data), start, end, "caldav", writable=True)
    return out


# ------------------------------------------------------------------ Google Calendar ---
def google_connected() -> bool:
    return GOOGLE_TOKEN.exists() and GOOGLE_CLIENT.exists()


def _google_client() -> dict:
    data = json.loads(GOOGLE_CLIENT.read_text())
    return data.get("installed") or data.get("web") or data


def _google_access_token() -> str:
    tok = json.loads(GOOGLE_TOKEN.read_text())
    if tok.get("access_token") and tok.get("expires_at", 0) > time.time() + 60:
        return tok["access_token"]
    c = _google_client()
    r = httpx.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": c["client_id"],
            "client_secret": c["client_secret"],
            "refresh_token": tok["refresh_token"],
            "grant_type": "refresh_token",
        },  # fmt: skip
        timeout=30,
    )
    if r.status_code >= 400:
        raise CalendarError("Google sign-in expired: run  python3 toolbox/calendars.py --google-setup  again.")
    new = r.json()
    tok.update({"access_token": new["access_token"], "expires_at": time.time() + int(new.get("expires_in", 3600))})
    GOOGLE_TOKEN.write_text(json.dumps(tok))
    os.chmod(GOOGLE_TOKEN, 0o600)
    return tok["access_token"]


def _google(method: str, path: str, **kw) -> httpx.Response:
    cal_id = setting("GOOGLE_CALENDAR_ID", "primary")
    r = httpx.request(
        method,
        f"{GOOGLE_API}/calendars/{cal_id}/events{path}",
        headers={"Authorization": f"Bearer {_google_access_token()}"},
        timeout=30,
        **kw,
    )
    if r.status_code >= 400:
        raise CalendarError(f"Google Calendar said {r.status_code}: {r.text[:200]}")
    return r


def _google_time(value: dict):
    if "date" in value:
        return dt.date.fromisoformat(value["date"])
    return _as_local(dt.datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00")))


def _google_events(start, end, query="") -> list[dict]:
    params = {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true",
              "orderBy": "startTime", "maxResults": 250}  # fmt: skip
    if query:
        params["q"] = query
    items = _google("GET", "", params=params).json().get("items", [])
    return [
        {
            "id": f"google:{it['id']}",
            "source": "Google",
            "title": it.get("summary", "(no title)"),
            "start": _google_time(it["start"]),
            "end": _google_time(it["end"]),
            "all_day": "date" in it["start"],
            "location": it.get("location", ""),
            "repeats": bool(it.get("recurringEventId")),
        }
        for it in items
        if it.get("status") != "cancelled"
    ]


def google_setup() -> None:
    """One-time sign-in on this computer (opens your browser); saves a token only for calendar events."""
    import http.server
    import urllib.parse
    import webbrowser

    if not GOOGLE_CLIENT.exists():
        print(f"First create a Google OAuth client (Desktop app) and save its JSON as {GOOGLE_CLIENT}.")
        print("Steps: docs/GUIDE.md, 'Calendar' -> 'Google Calendar with read and write'.")
        sys.exit(1)
    c = _google_client()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Done. You can close this tab and go back to the terminal.".encode())

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}"
    url = (
        GOOGLE_AUTH_URL
        + "?"
        + urllib.parse.urlencode(
            {
                "client_id": c["client_id"],
                "redirect_uri": redirect,
                "response_type": "code",
                "scope": GOOGLE_SCOPE,
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }  # fmt: skip
        )
    )
    print("Opening your browser to sign in to Google. If it doesn't open, paste this link into it:\n")
    print(url + "\n")
    webbrowser.open(url)
    while "code" not in got and "error" not in got:
        server.handle_request()
    if got.get("error") or got.get("state") != state:
        print(f"Sign-in was not completed ({got.get('error', 'wrong state')}).")
        sys.exit(1)
    r = httpx.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": c["client_id"],
            "client_secret": c["client_secret"],
            "code": got["code"],
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect,
        },  # fmt: skip
        timeout=30,
    )
    r.raise_for_status()
    tok = r.json()
    GOOGLE_TOKEN.parent.mkdir(parents=True, exist_ok=True)
    GOOGLE_TOKEN.write_text(json.dumps({"refresh_token": tok["refresh_token"], "access_token": tok["access_token"],
                                        "expires_at": time.time() + int(tok.get("expires_in", 3600))}))  # fmt: skip
    os.chmod(GOOGLE_TOKEN, 0o600)
    print(f"Google Calendar connected. Token saved in {GOOGLE_TOKEN} (only this computer can use it).")
    print("Restart the toolbox: sudo systemctl restart ai-toolbox")


# ------------------------------------------------------------------ the three tools ---
def _write_target(choice: str = "") -> str:
    choice = (choice or setting("CALENDAR_ADD_TO")).lower()
    if choice in ("google", "caldav", "local"):
        return choice
    if google_connected():
        return "google"
    if setting("CALDAV_URL"):
        return "caldav"
    return "local"


def _fmt(e: dict) -> str:
    if e["all_day"]:
        when = f"{e['start']:%a %Y-%m-%d} (all day)"
    else:
        end = f"-{e['end']:%H:%M}" if e.get("end") and e["end"].date() == e["start"].date() else ""
        when = f"{e['start']:%a %Y-%m-%d %H:%M}{end}"
    extra = f" at {e['location']}" if e.get("location") else ""
    rep = " (repeats)" if e.get("repeats") else ""
    ident = f" [id {e['id']}]" if e.get("id") else ""
    return f"{when}  {e['title']}{extra}{rep}  ({e['source']}){ident}"


def list_events(start: str = "", end: str = "", query: str = "") -> str:
    tz = local_tz()
    today = dt.datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    s = parse_when(start)[0] if start else today
    e = parse_when(end)[0] if end else (s if isinstance(s, dt.datetime) else today) + dt.timedelta(days=7)
    s = s if isinstance(s, dt.datetime) else dt.datetime.combine(s, dt.time(), tz)
    e = e if isinstance(e, dt.datetime) else dt.datetime.combine(e, dt.time(23, 59), tz)
    events, problems = _events_from_ics(_load_local(), s, e, "local", writable=True), []
    more, bad = _ics_url_events(s, e)
    events += more
    problems += bad
    for name, fn in (("CalDAV", _caldav_events if setting("CALDAV_URL") else None),
                     ("Google", (lambda a, b: _google_events(a, b, query)) if google_connected() else None)):  # fmt: skip
        if fn:
            try:
                events += fn(s, e)
            except Exception as err:  # noqa: BLE001
                problems.append(f"{name}: {err}")
    if query:
        q = query.lower()
        events = [x for x in events if q in x["title"].lower() or q in x.get("location", "").lower()]

    def key(x):
        v = x["start"]
        return v if isinstance(v, dt.datetime) else dt.datetime.combine(v, dt.time(), tz)

    events.sort(key=key)
    head = f"Events {s:%a %Y-%m-%d} to {e:%a %Y-%m-%d} ({tz.key}):"
    body = [_fmt(x) for x in events] or ["(nothing scheduled)"]
    tail = [f"Note: {p}" for p in problems]
    return "\n".join([head, *body, *tail])


def add_event(title, start, end="", location="", notes="", repeat="", calendar="") -> str:
    if not title.strip():
        raise CalendarError("The event needs a title.")
    repeat = repeat.lower().strip()
    if repeat and repeat not in REPEATS:
        raise CalendarError("repeat must be daily, weekly, monthly or yearly (or empty).")
    s, all_day = parse_when(start)
    if end:
        e, _ = parse_when(end)
    else:
        e = s + dt.timedelta(days=1) if all_day else s + dt.timedelta(hours=1)
    if all_day and isinstance(e, dt.date) and e <= s:
        e = s + dt.timedelta(days=1)  # all-day end is the day after (exclusive)
    target = _write_target(calendar)
    if target == "google":
        body = {"summary": title, "location": location, "description": notes}
        if all_day:
            body.update({"start": {"date": s.isoformat()}, "end": {"date": e.isoformat()}})
        else:
            body.update({"start": {"dateTime": s.isoformat(), "timeZone": local_tz().key},
                         "end": {"dateTime": e.isoformat(), "timeZone": local_tz().key}})  # fmt: skip
        if repeat:
            body["recurrence"] = [f"RRULE:FREQ={REPEATS[repeat]}"]
        ident = "google:" + _google("POST", "", json=body).json()["id"]
    else:
        from icalendar import Calendar

        ev = _vevent(title, s, e, all_day, location, notes, repeat)
        if target == "caldav":
            cal = Calendar()
            cal.add("prodid", "-//local-ai-agent//calendar//EN")
            cal.add("version", "2.0")
            cal.add_component(ev)
            _with_timezones(cal)
            _caldav_calendar().save_event(cal.to_ical().decode())
        else:
            cal = _load_local()
            cal.add_component(ev)
            _with_timezones(cal)
            local_file().parent.mkdir(parents=True, exist_ok=True)
            local_file().write_bytes(cal.to_ical())
        ident = f"{target}:{ev['UID']}"
    when = f"{s:%a %Y-%m-%d}" + ("" if all_day else f" {s:%H:%M}-{e:%H:%M}")
    rep = f", repeats {repeat}" if repeat else ""
    return f"Added '{title}' on {when}{rep} to the {target} calendar [id {ident}]."


def delete_event(event_id: str) -> str:
    source, _, uid = event_id.partition(":")
    if not uid:
        raise CalendarError("Give the event id shown by calendar_events, e.g. local:1234@local-ai-agent.")
    if source == "google":
        _google("DELETE", f"/{uid}")
    elif source == "caldav":
        _caldav_calendar().event_by_uid(uid).delete()
    elif source == "local":
        cal = _load_local()
        keep = [c for c in cal.subcomponents if not (c.name == "VEVENT" and str(c.get("UID")) == uid)]
        if len(keep) == len(cal.subcomponents):
            raise CalendarError(f"No event {event_id} in the local calendar.")
        cal.subcomponents[:] = keep
        local_file().write_bytes(cal.to_ical())
    else:
        raise CalendarError(f"'{source}' is a read-only calendar link: change it in that calendar's own app.")
    return f"Deleted event {event_id}."


def status() -> str:
    links = [x for x in setting("CALENDAR_ICS_URLS").split(",") if x.strip()]
    return (
        f"local calendar {local_file()}; {len(links)} read-only link(s); "
        f"CalDAV {'on' if setting('CALDAV_URL') else 'off'}; Google {'on' if google_connected() else 'off'}; "
        f"new events go to: {_write_target()}; time zone {local_tz().key}"
    )


if __name__ == "__main__":
    if "--google-setup" in sys.argv:
        google_setup()
    else:
        print(status())
        print(list_events())
