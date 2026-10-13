"""Everyday tools: date and time, weather, calendars and scheduled tasks."""

import datetime as dt

import toolbox_server as tb


# ------------------------------------------------------------------ general ---
@tb.mcp.tool()
def current_datetime() -> str:
    """Return the current local date and time. Call this whenever the answer depends on today's date."""
    return dt.datetime.now().astimezone().strftime("%A %Y-%m-%d %H:%M %Z")


@tb.mcp.tool()
def weather(place: str = "", days: int = 3, units: str = "", remember: bool = False) -> str:
    """Current weather and forecast (1-7 days) from Open-Meteo. place: a city, optionally with
    region or country ("Pune, India"); empty = the user's default place. If there is no default
    yet, ask the user which place (and Celsius or Fahrenheit), then call with remember=true.
    units: metric or imperial (empty = the user's default)."""
    import weather as wx

    saved = wx.load_settings()
    place = place.strip() or saved.get("weather_place", "")
    if not place:
        return "No default place yet. Ask the user which city to use for weather, then call weather(place=..., remember=true)."
    units = units or saved.get("weather_units", "metric")
    text = wx.forecast(place, days, units)
    if remember and not text.startswith("Place not found"):
        wx.save_setting("weather_place", place)
        wx.save_setting("weather_units", units)
        text += f"\n(Saved {place} as the default place.)"
    return text


@tb.mcp.tool()
def calendar_events(start: str = "", end: str = "", query: str = "") -> str:
    """The user's events from all their calendars, with ids. start/end: YYYY-MM-DD or
    YYYY-MM-DD HH:MM (default: today and the next 7 days). query: words to look for."""
    import calendars

    try:
        return calendars.list_events(start, end, query)
    except calendars.CalendarError as e:
        return str(e)


@tb.mcp.tool()
def calendar_add_event(
    title: str, start: str, end: str = "", location: str = "", notes: str = "", repeat: str = "", calendar: str = ""
) -> str:
    """Add an event to the user's calendar, only when they ask. start/end: YYYY-MM-DD HH:MM in
    local time, or YYYY-MM-DD for all day (no end = 1 hour). repeat: daily, weekly, monthly or
    yearly. calendar: local, caldav or google (empty = the default). Confirm what was added."""
    import calendars

    try:
        return calendars.add_event(title, start, end, location, notes, repeat, calendar)
    except calendars.CalendarError as e:
        return str(e)


@tb.mcp.tool()
def calendar_delete_event(event_id: str) -> str:
    """Delete an event by the id calendar_events shows. Only when the user asks for that event."""
    import calendars

    try:
        return calendars.delete_event(event_id)
    except calendars.CalendarError as e:
        return str(e)


# ------------------------------------------------------------------ scheduled tasks ---
# Run by the ai-scheduler timer (toolbox/scheduler.py --run-due); results are e-mailed to you.
@tb.mcp.tool()
def schedule_task(when: str, what: str, repeat: str = "", kind: str = "task") -> str:
    """Schedule something for later, only when the user asks. when: first time, YYYY-MM-DD HH:MM
    local (use current_datetime). what: the full instruction, e.g. "Weather for today and my
    calendar for today". repeat: hourly, daily, weekdays, weekly, monthly, or empty for once.
    kind: "task" (you do it with your tools then; the answer is e-mailed) or "reminder" (the text
    is e-mailed as it is)."""
    import scheduler

    try:
        return scheduler.add(when, what, repeat, kind)
    except ValueError as e:
        return str(e)


@tb.mcp.tool()
def scheduled_tasks() -> str:
    """The user's scheduled tasks and reminders, with ids, next run and last result."""
    import scheduler

    return scheduler.listing()


@tb.mcp.tool()
def cancel_scheduled_task(task_id: str) -> str:
    """Stop a scheduled task or reminder (id from scheduled_tasks), only when the user asks."""
    import scheduler

    return scheduler.cancel(task_id)
