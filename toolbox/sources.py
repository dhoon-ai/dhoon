"""How sure can you be? One scale for everything that counts independent sources.

    confidence(n, official=False, in_primary=True)

n = how many independent outlets report it (an outlet counts once: "Reuters" and reuters.com are
the same). 1 source 5/10, 2 sources 7/10, 3 sources 9/10, 4 or more 10/10; an official source
(a statistics office, central bank or government) adds 1; not found in the main source text
(e.g. the week's Wikipedia page) takes 2 off; nothing at all is 1/10.
Used by the knowledge base and available to plugins, so every score means the same thing.
"""

import re


def outlet_key(name: str) -> str:
    """'Reuters', 'www.reuters.com' and 'reuters.com/world' all give 'reuters'."""
    name = re.sub(r"^(https?://)?(www\.|m\.|amp\.|edition\.|news\.)", "", (name or "").lower().strip())
    return re.sub(r"[^a-z0-9]", "", re.split(r"[./\s]", name)[0]) or name


def independent(names: list[str]) -> dict[str, str]:
    """{outlet key: first name seen} without duplicates."""
    out: dict[str, str] = {}
    for n in names:
        if n:
            out.setdefault(outlet_key(n), n)
    return out


def confidence(n: int, official: bool = False, in_primary: bool = True) -> int:
    if n <= 0 and not official:
        return 2 if in_primary else 1
    score = 3 + 2 * n + (1 if official else 0) - (0 if in_primary else 2)
    return max(1, min(10, score))
