"""Official economic numbers and the calendar of upcoming economic events."""

import json

import econ_calendar
import econdata
import toolbox_server as tb


@tb.mcp.tool()
def economic_data(country: str = "canada") -> str:
    """The latest official economic numbers, straight from the statistics office and central
    bank (not the news): for canada the Bank of Canada policy rate, USD/CAD, the 10-year bond
    yield, inflation (CPI, change over 12 months) and the unemployment rate; for usa CPI,
    unemployment, the federal funds rate and the 10-year Treasury yield (FRED). Each with its
    date and source. country: canada or usa."""
    try:
        return json.dumps(econdata.latest(country), indent=1)
    except ValueError as e:
        return str(e)


@tb.mcp.tool()
def economic_calendar(days: int = 14, region: str = "") -> str:
    """Upcoming central-bank decisions and data releases (inflation, jobs, GDP) for canada, usa,
    india, europe or australia (empty: all), from a weekly search (each with its source link:
    say it should be checked) and the user's own dates."""
    events = econ_calendar.upcoming(days=max(1, min(days, 60)), regions=[region.lower()] if region else None)
    if not events:
        return "No upcoming events known yet (the calendar is refreshed weekly with the knowledge base)."
    return json.dumps(events, indent=1)
