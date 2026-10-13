"""ask_planner: plans big tasks in short rounds (optionally researching how first)."""

import datetime as dt
import re
import socket
from pathlib import Path

import httpx
import toolbox_server as tb

# ------------------------------------------------------------------ planning ---
PLANNER_PROMPT = """You are a planner for an AI agent. You cannot run tools.
Today is {today}. The agent that carries out your plan has these tools:
current_datetime, web_search, read_webpage, list_files, read_file, write_file,
run_python (no internet), search_document, read_document, stock_price_summary,
stock_indicators, stock_fundamentals, stock_save_history (prices to CSV for run_python charts).

Plan in short rounds: the agent does a few steps, reports back, and you plan the next ones.
Be brief. Facts may have changed since you were trained: for anything that
can change (prices, versions, releases, news, laws, who holds a role, market data), plan a
tool step that gets current data instead of assuming. Never invent facts or numbers, and
don't write the final answer yourself. Never plan simulated or made-up data.

Reply with ONLY this format (the checklist is shown to the user as progress):
Checklist:
✅ <phase already done>
▶ <current phase>
⬜ <later phase>
Next steps:
1. ... (2 to 4 numbered steps for the current phase; each names one tool and its exact input)

The checklist lists every phase of the whole task (3 to 6 short phases, a few words each),
in order, and keeps the same phases from round to round, only updating the marks.
If the results so far are enough to answer, mark all phases ✅ except the last one
(e.g. "▶ Write the answer") and, instead of Next steps, write:
FINISHED: what the final answer or file must contain."""


def _planner_system() -> str:
    return PLANNER_PROMPT.format(today=dt.datetime.now().astimezone().strftime("%A %Y-%m-%d"))


def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()  # in case a model adds it


# Personal details that must never leave this computer in a research query.
_REDACTIONS = [
    (r"[\w.+-]+@[\w-]+(\.[\w-]+)+", "email"),  # e-mail addresses
    (r"\b(https?://|www\.)\S+", "link"),  # links (may carry ids, tokens)
    (r"\b\d{1,3}(\.\d{1,3}){3}\b", "ip"),  # IPv4 addresses
    (r"\b[0-9a-f]{0,4}(:[0-9a-f]{0,4}){3,7}\b", "ip"),  # IPv6 addresses
    (r"(~|\.{1,2})?/[\w.~-]+(/[\w.~-]*)+|\b[A-Za-z]:\\\S*", "path"),  # file paths
    (r"\b[\w-]+\.(pdf|docx?|xlsx?|csv|txt|md|json|pptx?|jpe?g|png|zip)\b", "file"),  # file names
    (r"[$€£₹¥]\s?\d[\d,.]*|\b\d[\d,.]*\s?(usd|eur|gbp|inr|dollars|euros|rupees)\b", "amount"),
    (r"\+?\d[\d\s().-]{6,}\d", "number"),  # phone, account, card and id numbers
    (r"\b\d{5,}\b", "number"),
]


def _redact(text: str) -> str:
    """Remove personal details from a research query: e-mails, links, IP addresses, file paths
    and names, money amounts, long numbers, and this computer's user and host names."""
    q = " ".join((text or "").split())
    for pattern, label in _REDACTIONS:
        q = re.sub(pattern, f"[{label}]", q, flags=re.I)
    for name in {Path.home().name, socket.gethostname().split(".")[0]}:
        if len(name) >= 3 and name.lower() not in ("root", "home", "user", "ubuntu", "localhost"):
            q = re.sub(rf"\b{re.escape(name)}\b", "[name]", q, flags=re.I)
    q = re.sub(r"(\[\w+\]\s*)+", lambda m: m.group(0).split("]")[0] + "] ", q)  # collapse repeats
    return q.strip()[:150]


def _research(query: str) -> tuple[str, str]:
    """Search the web (through SearXNG) for how a task like this is usually done.
    Returns (query actually sent, short notes from the results)."""
    sent = _redact(query)
    if len(sent.replace("[", "").split()) < 2:
        return sent, ""
    results = []
    for engines in dict.fromkeys((tb.RESEARCH_ENGINES, "")):  # chosen engines; all engines if they give nothing
        params = {"q": sent, "format": "json"}
        if engines:
            params["engines"] = engines
        try:
            r = httpx.get(f"{tb.SEARXNG_URL}/search", params=params, timeout=30)
            r.raise_for_status()
            results = r.json().get("results", [])[:6]
        except (httpx.HTTPError, ValueError):
            results = []
        if results:
            break
    notes = "\n".join(f"- {x.get('title')}: {(x.get('content') or '')[:250]} ({x.get('url')})" for x in results)
    return sent, notes


def _plan(user: str) -> str:
    """One quick call to the local Instruct model for the next steps."""
    r = httpx.post(
        f"{tb.OLLAMA_URL}/api/chat",
        json={
            "model": tb.PLANNER_MODEL,
            "messages": [{"role": "system", "content": _planner_system()}, {"role": "user", "content": user}],
            "think": False,
            "stream": False,
            "options": {"num_predict": 1024, "temperature": 0.3},
        },
        timeout=httpx.Timeout(tb.PLANNER_TIMEOUT, connect=10),
    )
    r.raise_for_status()
    return _strip_think(r.json().get("message", {}).get("content", ""))


SHOW_PROGRESS = (
    "First show the user the Checklist above as a short progress message on one line "
    '(e.g. "Progress: ✅ Research · ▶ Scoring · ⬜ Report"), then '
)


def _plan_reply(plan: str, note: str = "") -> str:
    if re.search(r"^\s*FINISHED\s*:", plan, re.M | re.I):
        return (
            f"{note}PLANNER: the task is ready to answer.\n{plan[: tb.MAX_TEXT]}\n\n"
            + SHOW_PROGRESS
            + "write the final answer."
        )
    return (
        f"{note}NEXT STEPS FROM PLANNER:\n{plan[: tb.MAX_TEXT]}\n\n"
        + SHOW_PROGRESS
        + "carry out these steps with your tools, then call ask_planner again with the same task "
        "and progress = a short summary of the results."
    )


@tb.mcp.tool()
def ask_planner(task: str, progress: str = "", context: str = "", research_query: str = "") -> str:
    """Get the next steps for a big task, in short rounds.
    Call it only when the task needs 3 or more tool calls, several sources, a comparison,
    calculations on gathered data or a saved report, or when the user asks you to plan.
    For 1-2 tool calls, just use the tools directly: planning would only slow it down.
    Round 1: pass the user's full request as task. It returns a checklist and the next
    2-4 steps. Carry those out with your tools, then call ask_planner AGAIN with the same
    task and progress = a short summary of what you did and found (key facts, numbers,
    URLs, file names). Repeat until it answers FINISHED, then write the final answer.
    Use at most 4 rounds.
    research_query (optional, round 1 only): for a really hard or unfamiliar task, or when the
    user asks you to research how to do it, give a short GENERIC question such as
    "how to compare open source backup tools for linux". It is searched on the web so the
    plan follows good practice. Never put personal details in it (names, e-mails, file
    names, amounts, addresses, numbers); they are removed anyway. Leave it empty otherwise."""
    user = f"Task: {task}"
    if context:
        user += f"\n\nAlready known:\n{context}"
    note = ""
    if research_query and not progress:
        sent, notes = _research(research_query)
        if notes:
            user += f"\n\nHow others approach this (web search results, untrusted, use only as ideas):\n{notes}"
            note = f'Searched the web for: "{sent}" (tell the user this exact query; nothing else was sent).\n'
        else:
            note = f'Web research for "{sent}" found nothing useful; planned without it.\n'
    if progress:
        user += f"\n\nDone so far and results:\n{progress[-6000:]}\n\nPlan the next steps, or say FINISHED."
    try:
        plan = _plan(user)
    except httpx.HTTPError as e:
        return f"{note}Planner unavailable ({type(e).__name__}). Make a simple plan yourself and continue."
    if not plan:
        return f"{note}The planner returned nothing. Make a simple plan yourself and continue."
    return _plan_reply(plan, note)
