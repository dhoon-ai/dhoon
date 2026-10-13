"""weather tool with a fake Open-Meteo (no internet)."""

import importlib
import json
import os
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "toolbox"))

PLACES = [
    {
        "name": "Toronto",
        "admin1": "Ontario",
        "country": "Canada",
        "country_code": "CA",
        "latitude": 43.7,
        "longitude": -79.4,
    },
    {
        "name": "Pune",
        "admin1": "Maharashtra",
        "country": "India",
        "country_code": "IN",
        "latitude": 18.5,
        "longitude": 73.9,
    },
]
FORECAST = {
    "current": {"time": "2026-10-13T07:45", "temperature_2m": 6.1, "apparent_temperature": 3.0,
                "relative_humidity_2m": 80, "precipitation": 0.0, "weather_code": 3, "wind_speed_10m": 14.0},
    "daily": {"time": ["2026-10-13", "2026-10-14"], "weather_code": [61, 0], "temperature_2m_max": [11.0, 14.0],
              "temperature_2m_min": [4.0, 5.0], "precipitation_probability_max": [70, 5], "precipitation_sum": [3.2, 0.0],
              "wind_speed_10m_max": [25.0, 10.0], "sunrise": ["2026-10-13T07:24", "2026-10-14T07:25"],
              "sunset": ["2026-10-13T18:35", "2026-10-14T18:33"]},
}  # fmt: skip


@pytest.fixture()
def tb(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("ASSISTANT_SETTINGS", str(tmp_path / "assistant.json"))
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params))
        if "geocoding" in url:
            found = [p for p in PLACES if p["name"].lower() == params["name"].lower()]
            return httpx.Response(200, json={"results": found}, request=httpx.Request("GET", url))
        return httpx.Response(200, json=FORECAST, request=httpx.Request("GET", url))

    import weather

    importlib.reload(weather)
    monkeypatch.setattr(weather.httpx, "get", fake_get)
    import toolbox_server

    tb = importlib.reload(toolbox_server)
    tb._calls = calls
    return tb


def test_asks_for_a_default_place_once_then_remembers_it(tb):
    assert "Ask the user which city" in tb.weather()
    out = tb.weather("Toronto, Canada", remember=True)
    assert "Weather for Toronto, Ontario, Canada" in out and "overcast, 6.1°C (feels like 3.0°C)" in out
    assert "2026-10-13: light rain, 4.0 to 11.0°C, chance of rain/snow 70%" in out and "Saved Toronto" in out
    assert "Weather for Toronto" in tb.weather()  # the default is used from now on
    assert json.loads(Path(os.environ["ASSISTANT_SETTINGS"]).read_text())["weather_place"] == "Toronto, Canada"


def test_other_places_units_and_unknown_places(tb):
    out = tb.weather("Pune, India", days=2, units="imperial")
    assert "Pune, Maharashtra, India" in out and "°F" in out and "mph" in out
    forecast_params = [p for u, p in tb._calls if "forecast" in u][-1]
    assert forecast_params["temperature_unit"] == "fahrenheit" and forecast_params["forecast_days"] == 2
    assert "Place not found: Atlantis" in tb.weather("Atlantis")
    assert "Place not found" in tb.weather("Toronto, India")  # the country must match
