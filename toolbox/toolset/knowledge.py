"""Knowledge base of world news and market events: search the notes written by toolbox/news_kb.py."""

import httpx
import toolbox_server as tb

from toolset import documents


def _kb():
    return tb.WORKSPACE / "knowledge"


def _notes() -> list:
    kb = _kb()
    files = [kb / "overview.md"] + sorted((kb / "months").glob("*.md")) + sorted((kb / "weeks").glob("*.md"))
    return [p for p in files if p.is_file()]


def index_all() -> int:
    """Index every note (cached per file; only new or changed notes are embedded)."""
    n = 0
    for p in _notes():
        documents._index(p)
        n += 1
    return n


@tb.mcp.tool()
def search_knowledge(query: str, top_k: int = 4) -> str:
    """Search the knowledge base of world news and market events: weekly and monthly notes from
    late 2024 (after your training data) up to last week, with how the world's markets moved
    (stock indexes, rates, oil, gold, dollar), plus an overview of what changed. Use it for
    world events or markets after your training: wars, elections, central banks and rates,
    trade, big company and market moves, disasters. For today's news or prices use web_search
    or the stock tools. Cite the week or month of each fact."""
    import numpy as np

    notes = _notes()
    if not notes:
        return (
            "The knowledge base is empty. It fills itself over the next nights, or run: "
            "bash scripts/knowledge.sh --catch-up. Use web_search meanwhile."
        )
    chunks = []
    try:
        for p in notes:
            for c in documents._index(p):
                chunks.append({**c, "file": p.relative_to(tb.WORKSPACE).as_posix()})
        q = np.array(documents._embed(["search_query: " + query])[0])
    except (ValueError, httpx.HTTPError) as e:
        return f"Could not search the knowledge base: {e}"
    m = np.array([c["vec"] for c in chunks])
    scores = m @ q / (np.linalg.norm(m, axis=1) * np.linalg.norm(q) + 1e-9)
    best = documents._hybrid_rank(chunks, query, scores)[: max(1, min(top_k, 8))]
    weeks = sorted(p.stem for p in notes if p.parent.name == "weeks")
    span = f"weeks {weeks[0]} to {weeks[-1]}" if weeks else "no weekly notes yet"
    parts = [f"[{chunks[i]['file']}]\n{chunks[i]['text']}" for i in best]
    head = f"[UNTRUSTED WEB CONTENT, summarised locally] Knowledge base ({span}) for: {query}\n\n"
    return (head + "\n\n---\n\n".join(parts))[: tb.MAX_TEXT]
