"""Weather from Open-Meteo (free, no account or key; only the place name or its coordinates
are sent). The default place is asked once and remembered in runtime/assistant.json."""

import json
import os
from pathlib import Path

import httpx

GEOCODE_URL = os.environ.get("OPEN_METEO_GEOCODE_URL", "https://geocoding-api.open-meteo.com/v1/search")
FORECAST_URL = os.environ.get("OPEN_METEO_URL", "https://api.open-meteo.com/v1/forecast")
SETTINGS = Path(
    os.environ.get("ASSISTANT_SETTINGS", str(Path(__file__).resolve().parents[1] / "runtime" / "assistant.json"))
)

# WMO weather codes used by Open-Meteo
CODES = {
    0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
    82: "violent showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorm",
    96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}  # fmt: skip


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        return {}


def save_setting(key: str, value) -> None:
    data = load_settings()
    data[key] = value
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(data, indent=1, ensure_ascii=False))


def find_place(place: str) -> dict | None:
    """'Toronto', 'Toronto, Canada' or 'Pune, Maharashtra' -> name, region, country, coordinates."""
    name, *rest = [p.strip() for p in place.split(",")]
    r = httpx.get(GEOCODE_URL, params={"name": name, "count": 10, "language": "en", "format": "json"}, timeout=20)
    r.raise_for_status()
    results = r.json().get("results") or []
    hints = [h.lower() for h in rest if h]
    for res in results:
        where = " ".join(str(res.get(k, "")) for k in ("admin1", "country", "country_code")).lower()
        if all(h in where for h in hints):
            return res
    return None


def forecast(place: str, days: int = 3, units: str = "metric") -> str:
    loc = find_place(place)
    if not loc:
        return f"Place not found: {place}. Ask the user for a nearby city (and country)."
    imperial = units.lower().startswith(("imp", "f", "us"))
    days = max(1, min(int(days or 3), 7))
    params = {
        "latitude": loc["latitude"],
        "longitude": loc["longitude"],
        "timezone": "auto",
        "forecast_days": days,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
        "precipitation_sum,wind_speed_10m_max,sunrise,sunset",
    }
    if imperial:
        params.update({"temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch"})
    r = httpx.get(FORECAST_URL, params=params, timeout=20)
    r.raise_for_status()
    d = r.json()
    t, w, p = ("°F", "mph", "in") if imperial else ("°C", "km/h", "mm")
    c = d.get("current", {})
    where = ", ".join(x for x in (loc.get("name"), loc.get("admin1"), loc.get("country")) if x)
    lines = [
        f"Weather for {where} (local time {c.get('time', '?').replace('T', ' ')}):",
        f"Now: {CODES.get(c.get('weather_code'), 'unknown')}, {c.get('temperature_2m')}{t} "
        f"(feels like {c.get('apparent_temperature')}{t}), humidity {c.get('relative_humidity_2m')}%, "
        f"wind {c.get('wind_speed_10m')} {w}, precipitation {c.get('precipitation')} {p}.",
    ]
    daily = d.get("daily", {})
    for i, day in enumerate(daily.get("time", [])):
        lines.append(
            f"{day}: {CODES.get(daily['weather_code'][i], 'unknown')}, {daily['temperature_2m_min'][i]} to "
            f"{daily['temperature_2m_max'][i]}{t}, chance of rain/snow {daily['precipitation_probability_max'][i]}% "
            f"({daily['precipitation_sum'][i]} {p}), wind up to {daily['wind_speed_10m_max'][i]} {w}, "
            f"sunrise {daily['sunrise'][i][-5:]}, sunset {daily['sunset'][i][-5:]}."
        )
    lines.append("Source: Open-Meteo.")
    return "\n".join(lines)
