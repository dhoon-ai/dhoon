"""Fact check: is every number, date and name in an AI-written text found in its source?

No AI involved: plain text matching, so it can't be fooled the way a model can. Used for the
knowledge base notes (news_kb.py) and by plugins (e.g. to check the numbers in a decision).

    problems = check(summary, source, first=monday, last=sunday)

A number counts as found if the source has it (rounding allowed: 4,567.89 may be written
4,568; a fall of -5.0% may be written "fell 5.0%"). A date must fall between `first` and
`last`. A name (a capitalised word of 4+ letters) must appear in the source, at least its
first 5 letters ("Canadian" is found by "Canada").
"""

import datetime as dt
import re

_NUM = re.compile(r"(?<![\w.])[-+−]?[$₹€£]?\d[\d,]*(?:\.\d+)?(?:%| ?pts?\b| ?bp\b)?")
_MONTHS = {
    m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)
}
_MON = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
_WEEKDAY = r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\.?,? "
# "Mar 15", "15 Sep" and "Tue 15 Sep" (the weekday is part of the date, so a fix can change it)
_DATE = re.compile(
    rf"\b(?:(?P<m1>{_MON}) (?P<d1>\d{{1,2}})(?!\d)|(?P<wd>{_WEEKDAY})?(?P<d2>\d{{1,2}}) (?P<m2>{_MON}))(?![a-z])"
)
_NAME = re.compile(r"\b[A-Z][a-zA-Z'’\-]{3,}\b")
# capitalised words that are not claims: headings, months, days, sentence starters
_COMMON = set(
    """
    top stories markets market economy world news other disasters health and the this that these those
    there their they when where while with from after before over under into about also more most
    less some many much first second third last next week weeks month months year years today
    monday tuesday wednesday thursday friday saturday sunday january february march april june july
    august september october november december sept full text sources source untrusted summarised
    facts check fact note notes change close index indexes still ongoing however meanwhile overall
    elsewhere despite following during between against among amid across within without several
    government president prime minister officials police court report reports according says said
    north south east west central eastern western northern southern international national federal
    """.split()
)


def _value(tok: str) -> float | None:
    t = re.sub(r"[$₹€£,%+\s]|pts?|bp", "", tok.replace("−", "-"))
    try:
        return abs(float(t))
    except ValueError:
        return None


def _source_values(source: str) -> list[float]:
    return [v for v in (_value(t) for t in _NUM.findall(source)) if v is not None]


def _found(v: float, values: list[float]) -> bool:
    tol = max(0.051, 0.006 * v)  # rounding: 2.754 -> 2.75, 4,567.89 -> 4,568
    return any(abs(v - s) <= tol for s in values)


def _month_day(m) -> tuple[int, int]:
    mon = m.group("m1") or m.group("m2")
    return _MONTHS[mon[:3].lower()], int(m.group("d1") or m.group("d2"))


def _dates(text: str, first: dt.date, last: dt.date) -> list[tuple[str, dt.date | None]]:
    out = []
    for m in _DATE.finditer(text):
        month, day = _month_day(m)
        when = None
        for year in {first.year, last.year}:
            try:
                d = dt.date(year, month, day)
            except ValueError:
                continue
            if first <= d <= last:
                when = d
        out.append((m.group(0), when))
    return out


def unsupported_numbers(text: str, source: str, ignore: list[float] | None = None) -> list[str]:
    """Numbers in `text` that are not in `source` (dates and small counts are not checked)."""
    values = _source_values(source) + list(ignore or [])
    date_spans = [m.span() for m in _DATE.finditer(text)]
    out = []
    for m in _NUM.finditer(text):
        if any(a <= m.start() < b for a, b in date_spans):
            continue  # the day of a date ("Mar 4") is checked as a date
        tok = m.group(0).strip()
        v = _value(tok)
        if v is None:
            continue
        if v <= 12 and float(v).is_integer() and not tok.endswith("%"):
            continue  # small counts ("3 people", "2 rounds") are too common to check
        if 1900 <= v <= 2100 and float(v).is_integer():
            if not re.search(rf"\b{int(v)}\b", source):
                out.append(tok)
            continue
        if not _found(v, values):
            out.append(tok)
    return list(dict.fromkeys(out))


def _names(text: str) -> list[tuple[str, bool]]:
    """Capitalised words, each with whether it starts a sentence (then it may be an ordinary
    word like "Rates", so a shorter stem is enough)."""
    out = []
    for m in _NAME.finditer(text):
        before = text[: m.start()].rstrip()
        out.append((m.group(0), not before or before[-1] in "-:.*#(•"))
    return out


def _name_found(word: str, starts: bool, low: str) -> bool:
    k = word.lower().strip("'’-")
    return k in _COMMON or k in low or k[:5] in low or (starts and k[:4] in low)


def unsupported_names(text: str, source: str) -> list[str]:
    low = source.lower()
    return list(dict.fromkeys(w for w, starts in _names(text) if not _name_found(w, starts, low)))


_DAY_HEADING = re.compile(r"^### \w{3} (\d{1,2}) (\w{3}) (\d{4})\s*$", re.M)


def day_sections(source: str) -> dict[dt.date, str]:
    """{day: its text} from a week's source ("### Tue 4 Mar 2025" headings)."""
    marks = list(_DAY_HEADING.finditer(source))
    out = {}
    for i, m in enumerate(marks):
        try:
            d = dt.date(int(m.group(3)), _MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
        except (KeyError, ValueError):
            continue
        end = marks[i + 1].start() if i + 1 < len(marks) else len(source)
        nxt = source.find("\n### ", m.end())  # e.g. "### Headlines from a news search"
        out[d] = source[m.end() : min(end, nxt) if nxt > 0 else end]
    return out


def _supported_on(text: str, day_text: str) -> bool | None:
    """Does that day's source text have this story? None if the line has nothing to compare."""
    rest = _DATE.sub(" ", text)
    names = [w for w, _ in _names(rest) if w.lower() not in _COMMON]
    nums = [v for v in (_value(t) for t in _NUM.findall(rest)) if v is not None and v > 12]
    if not names and not nums:
        return None
    low, values = day_text.lower(), _source_values(day_text)
    return any(_name_found(w, False, low) for w in names) or any(_found(v, values) for v in nums)


def _wrong_days(text: str, first: dt.date, last: dt.date, days: dict) -> list[str]:
    """Dates whose day in the source doesn't have this story (e.g. every bullet dated Mar 4)."""
    if not days:
        return []
    out = []
    for s, d in _dates(text, first, last):
        if d and _supported_on(text, days.get(d, "")) is False:
            out.append(f"date {s} doesn't match the source (that story isn't on that day)")
    return out


def fix_dates(text: str, source: str, first: dt.date, last: dt.date) -> tuple[str, int]:
    """Put the right date on lines whose date is wrong, when the story is on exactly one day of
    the source (plain code, no AI). Returns (text, how many dates were fixed)."""
    days = day_sections(source)
    if not days:
        return text, 0
    fixed, out = 0, []
    for line in text.splitlines():
        for m in list(_DATE.finditer(line)):
            month, day = _month_day(m)
            here = [d for y in {first.year, last.year} for d in [_safe_date(y, month, day)] if d and first <= d <= last]
            if here and _supported_on(line, days.get(here[0], "")) is not False:
                continue  # right (or nothing to compare)
            options = [d for d, t in days.items() if _supported_on(line, t)]
            if len(options) != 1:
                continue
            c = options[0]
            if m.group("m1"):
                new = f"{c:%b} {c.day}"
            else:
                new = (f"{c:%a} " if m.group("wd") else "") + f"{c.day} {c:%b}"
            line = line[: m.start()] + new + line[m.end() :]
            fixed += 1
            break  # one date per line; positions changed
        out.append(line)
    return "\n".join(out), fixed


def _safe_date(y: int, m: int, d: int) -> dt.date | None:
    try:
        return dt.date(y, m, d)
    except ValueError:
        return None


def check(text: str, source: str, first: dt.date | None = None, last: dt.date | None = None) -> list[str]:
    """Everything in `text` that the source doesn't support, in plain words."""
    problems = [f"number {n} is not in the source" for n in unsupported_numbers(text, source)]
    if first and last:
        problems += [f"date {s} is outside {first:%b %d} - {last:%b %d}" for s, d in _dates(text, first, last) if not d]
        problems += _wrong_days(text, first, last, day_sections(source))
    problems += [f"name {n} is not in the source" for n in unsupported_names(text, source)]
    return problems


def mark(text: str, source: str, first: dt.date | None = None, last: dt.date | None = None) -> tuple[str, int]:
    """The text with ' ⚠ not in source: ...' after each line that has a problem; and how many."""
    lines, n = [], 0
    for line in text.splitlines():
        found = [] if line.startswith("#") else check(line, source, first, last)
        if found:
            n += len(found)
            items = ", ".join(p.split(" is ")[0] for p in found)
            line = f"{line} ⚠ not in source: {items}"
        lines.append(line)
    return "\n".join(lines), n


def footer(problems: int) -> str:
    if not problems:
        return "_Fact check: passed (every number, date and name was found in the source)._"
    return f"_Fact check: {problems} item{'s' if problems != 1 else ''} not found in the source, marked ⚠._"
