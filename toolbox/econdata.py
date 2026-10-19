"""Official economic numbers, straight from the source (no AI, no news articles).

  Canada  Bank of Canada Valet API: policy rate, USD/CAD, 10-year bond yield
          Statistics Canada (Web Data Service): consumer prices (all-items) and unemployment rate
  USA     FRED (Federal Reserve Bank of St. Louis), CSV without a key: CPI, unemployment rate,
          federal funds rate, 10-year Treasury yield

Each value comes with its date, the one before, the change (for prices: over 12 months) and
the source address. Values are kept for 6 hours. Only public statistics are downloaded.
"""

import csv
import io
import time

import httpx

HEADERS = {"User-Agent": "Dhoon/1.1 (+https://dhoon.knowreva.com; personal self-hosted assistant)"}
BOC = "https://www.bankofcanada.ca/valet/observations/{series}/json"
STATCAN = "https://www150.statcan.gc.ca/t1/wds/rest/getDataFromVectorsAndLatestNPeriods"
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv"

SERIES = {
    "canada": [
        ("Bank of Canada policy rate (%)", "boc", "V39079", "level"),
        ("US dollar in Canadian dollars", "boc", "FXUSDCAD", "level"),
        ("Canada 10-year bond yield (%)", "boc", "BD.CDN.10YR.DQ.YLD", "level"),
        ("Consumer prices (CPI, all-items)", "statcan", 41690973, "yearly"),
        ("Unemployment rate (%)", "statcan", 2062815, "level"),
    ],
    "usa": [
        ("Consumer prices (CPI, all items)", "fred", "CPIAUCSL", "yearly"),
        ("Unemployment rate (%)", "fred", "UNRATE", "level"),
        ("Federal funds rate (%)", "fred", "FEDFUNDS", "level"),
        ("10-year Treasury yield (%)", "fred", "DGS10", "level"),
    ],
}
_CACHE: dict = {}
KEEP_SECONDS = 6 * 3600


def _boc(series: str, get) -> list[tuple[str, float]]:
    r = get(BOC.format(series=series), params={"recent": 15}, headers=HEADERS, timeout=30)
    r.raise_for_status()
    out = []
    for o in r.json().get("observations", []):
        v = (o.get(series) or {}).get("v")
        if v not in (None, ""):
            out.append((o["d"], float(v)))
    return out


def _statcan(vector: int, post) -> list[tuple[str, float]]:
    r = post(STATCAN, json=[{"vectorId": vector, "latestN": 13}], headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()[0]
    if data.get("status") != "SUCCESS":
        raise ValueError(f"Statistics Canada: {data.get('status')}")
    return [(p["refPer"], float(p["value"])) for p in data["object"]["vectorDataPoint"] if p.get("value") is not None]


def _fred(series: str, get) -> list[tuple[str, float]]:
    r = get(FRED, params={"id": series}, headers=HEADERS, timeout=30)
    r.raise_for_status()
    rows = list(csv.reader(io.StringIO(r.text)))
    out = []
    for row in rows[1:]:
        if len(row) >= 2 and row[1] not in ("", "."):
            try:
                out.append((row[0], float(row[1])))
            except ValueError:
                continue
    return out[-400:]


def _source(kind: str, series) -> str:
    return {"boc": f"https://www.bankofcanada.ca/valet/observations/{series}",
            "statcan": f"https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?vector=v{series}",
            "fred": f"https://fred.stlouisfed.org/series/{series}"}[kind]  # fmt: skip


def indicator(name: str, kind: str, series, how: str, get=None, post=None) -> dict:
    get = get or httpx.get
    post = post or httpx.post
    points = {"boc": lambda: _boc(series, get), "statcan": lambda: _statcan(series, post),
              "fred": lambda: _fred(series, get)}[kind]()  # fmt: skip
    points = sorted(points)
    if not points:
        raise ValueError("no data")
    date, value = points[-1]
    out = {"name": name, "value": value, "date": date, "source": _source(kind, series)}
    if how == "yearly":  # an index: what matters is the change over 12 months
        year_ago = [v for d, v in points if d[:7] == f"{int(date[:4]) - 1}{date[4:7]}"]
        if year_ago:
            out["change_12_months_pct"] = round((value / year_ago[0] - 1) * 100, 1)
    elif len(points) >= 2:
        out["previous"], out["previous_date"] = points[-2][1], points[-2][0]
        out["change"] = round(value - points[-2][1], 4)
    return out


def latest(country: str = "canada", get=None, post=None) -> list[dict]:
    """The latest official numbers for canada or usa (cached 6 hours)."""
    country = country.lower().strip()
    if country not in SERIES:
        raise ValueError(f"country must be one of: {', '.join(SERIES)}")
    hit = _CACHE.get(country)
    if hit and time.time() - hit[0] < KEEP_SECONDS and get is None and post is None:
        return hit[1]
    out = []
    for name, kind, series, how in SERIES[country]:
        try:
            out.append(indicator(name, kind, series, how, get, post))
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as e:
            out.append(
                {"name": name, "error": f"not available now ({type(e).__name__})", "source": _source(kind, series)}
            )
    if get is None and post is None:
        _CACHE[country] = (time.time(), out)
    return out
