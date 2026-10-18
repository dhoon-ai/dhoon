"""Scheduled tasks for the Assistant.

In a chat you say e.g. "every weekday at 7 e-mail me the weather and my day" and the
Assistant calls schedule_task. A systemd timer (ai-scheduler, every minute) runs
`python3 toolbox/scheduler.py --run-due`, which does what is due:
  - reminder: e-mails you the text as it is (no AI needed);
  - task: the Assistant does the task with its tools, unattended, and e-mails you the answer.
Every result is also saved in ~/agent-workspace/scheduled/. The list lives in
runtime/schedule.json, outside the workspace, so nothing the agent reads or writes there can
add tasks. Unattended runs can't send other e-mails, record investments, change calendars or
schedule more tasks.
"""

import asyncio
import calendar
import datetime as dt
import fcntl
import json
import os
import re
import sys
import uuid
from pathlib import Path

import httpx

KIT = Path(__file__).resolve().parents[1]
SCHEDULE_FILE = Path(os.environ.get("SCHEDULE_FILE", str(KIT / "runtime" / "schedule.json")))
WORKSPACE = Path(os.environ.get("WORKSPACE", str(Path.home() / "agent-workspace")))
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
MODEL = os.environ.get("SCHEDULER_MODEL") or os.environ.get("PLANNER_MODEL", "gemma4:26b")
MAX_STEPS = int(os.environ.get("SCHEDULER_MAX_STEPS", "15"))
MAX_TASKS = 20
REPEATS = ("", "hourly", "daily", "weekdays", "weekly", "monthly")
# tools an unattended run must not use (it can't ask you, and a web page could try to trick it)
DENY = set(
    os.environ.get(
        "SCHEDULER_DENY_TOOLS",
        "send_email,schedule_task,scheduled_tasks,cancel_scheduled_task,calendar_add_event,"
        "calendar_delete_event,record_investment,investment_tax_rules",
    ).split(",")
)
UNATTENDED = (
    "\n\nYou are running a SCHEDULED task the user set up earlier; nobody is watching, so don't ask "
    "questions: do the task with your tools and write the complete result as your final answer. "
    "It will be e-mailed to the user and saved. Keep it short and clear."
)


def now() -> dt.datetime:
    return dt.datetime.now().astimezone().replace(second=0, microsecond=0)


def load() -> list[dict]:
    try:
        return json.loads(SCHEDULE_FILE.read_text())
    except (OSError, ValueError):
        return []


def save(jobs: list[dict]) -> None:
    SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SCHEDULE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(jobs, indent=1, ensure_ascii=False))
    os.chmod(tmp, 0o600)
    tmp.replace(SCHEDULE_FILE)


def parse_when(text: str) -> dt.datetime:
    text = text.strip().replace("T", " ")
    try:
        when = dt.datetime.fromisoformat(text if len(text) > 10 else text + " 09:00")
    except ValueError:
        raise ValueError(f"Can't read the time '{text}': use YYYY-MM-DD HH:MM (local time).") from None
    return when.replace(tzinfo=None).astimezone() if when.tzinfo is None else when


def next_after(when: dt.datetime, repeat: str) -> dt.datetime | None:
    """The next run after `when` for a repeating task (None = runs once)."""
    if repeat == "hourly":
        return when + dt.timedelta(hours=1)
    if repeat == "daily":
        return when + dt.timedelta(days=1)
    if repeat == "weekly":
        return when + dt.timedelta(weeks=1)
    if repeat == "weekdays":
        nxt = when + dt.timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += dt.timedelta(days=1)
        return nxt
    if repeat == "monthly":
        y, m = (when.year + 1, 1) if when.month == 12 else (when.year, when.month + 1)
        return when.replace(year=y, month=m, day=min(when.day, calendar.monthrange(y, m)[1]))
    return None


def _wall(t: dt.datetime) -> dt.datetime:
    """Keep the same clock time across daylight-saving changes."""
    return t.replace(tzinfo=None).astimezone()


def add(when: str, what: str, repeat: str = "", kind: str = "task") -> str:
    repeat = (repeat or "").strip().lower()
    kind = "reminder" if kind.strip().lower().startswith("rem") else "task"
    if repeat not in REPEATS:
        return "repeat must be one of: hourly, daily, weekdays, weekly, monthly (or empty for once)."
    if not what.strip():
        return "Say what should happen."
    jobs = load()
    if len([j for j in jobs if j["active"]]) >= MAX_TASKS:
        return f"There are already {MAX_TASKS} scheduled tasks: cancel one first."
    first = parse_when(when)
    if repeat == "weekdays":
        while first.weekday() >= 5:
            first = _wall(first + dt.timedelta(days=1))
    if first < now() and not repeat:
        return f"{first:%Y-%m-%d %H:%M} is in the past. Give a future time."
    while first < now():
        first = _wall(next_after(first, repeat))
    job = {
        "id": uuid.uuid4().hex[:6],
        "kind": kind,
        "text": what.strip()[:2000],
        "repeat": repeat,
        "next_run": first.isoformat(),
        "created": now().isoformat(),
        "active": True,
        "runs": 0,
        "last_run": "",
        "last_result": "",
    }
    save([*jobs, job])
    every = {"": "once", "hourly": "every hour", "daily": "every day", "weekdays": "every weekday",
             "weekly": "every week", "monthly": "every month"}[repeat]  # fmt: skip
    mail = "e-mailed to you" if _email_ready() else "saved in your workspace (set up e-mail to get it by e-mail)"
    what_happens = "reminder" if kind == "reminder" else "task (the Assistant does it and sends the result)"
    return (
        f"Scheduled {what_happens} [id {job['id']}], {every}, first on {first:%a %Y-%m-%d %H:%M}. "
        f"Results are {mail} and saved in scheduled/."
    )


def listing() -> str:
    jobs = [j for j in load() if j["active"]]
    if not jobs:
        return "No scheduled tasks."
    out = []
    for j in sorted(jobs, key=lambda j: j["next_run"]):
        nxt = dt.datetime.fromisoformat(j["next_run"])
        last = f", last run {j['last_run'][:16].replace('T', ' ')}: {j['last_result']}" if j["last_run"] else ""
        out.append(
            f"[{j['id']}] {j['kind']}, {j['repeat'] or 'once'}, next {nxt:%a %Y-%m-%d %H:%M}: {j['text'][:120]}{last}"
        )
    return "\n".join(out)


def cancel(task_id: str) -> str:
    jobs = load()
    for j in jobs:
        if j["id"] == task_id.strip() and j["active"]:
            j["active"] = False
            save(jobs)
            return f"Cancelled [{task_id}]: {j['text'][:80]}"
    return f"No active scheduled task with id {task_id}. Use scheduled_tasks to see them."


# ------------------------------------------------------------------ running ---
def _email_ready() -> bool:
    try:
        import emailer

        return bool(emailer.settings()["allowed_list"])
    except Exception:  # noqa: BLE001
        return False


def _instructions() -> str:
    parts = [(KIT / "config" / "agent-instructions.md").read_text()]
    for f in sorted((KIT / "plugins").glob("*/instructions.md")):
        parts.append(f.read_text())
    text = "\n".join(parts)
    today = dt.date.today()
    return text.replace("{{current_date}}", f"{today:%A, %B %d, %Y}") + UNATTENDED


def _text(result) -> str:
    content = getattr(result, "content", result)
    if isinstance(content, tuple):  # (content, structured) in some library versions
        content = content[0]
    return "\n".join(getattr(c, "text", str(c)) for c in content or [])[:8000]


def run_task(text: str, post=None) -> str:
    """Do a task with the Assistant's tools, unattended. Returns its final answer."""
    import toolbox_server as tb  # with your plugins' tools

    post = post or (
        lambda body: httpx.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=httpx.Timeout(1800, connect=10))
    )
    tools = [
        {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.input_schema}}
        for t in asyncio.run(tb.mcp.list_tools())
        if t.name not in DENY
    ]
    allowed = {t["function"]["name"] for t in tools}
    messages = [{"role": "system", "content": _instructions()}, {"role": "user", "content": text}]
    for _ in range(MAX_STEPS):
        r = post({"model": MODEL, "messages": messages, "tools": tools, "stream": False, "think": False})
        r.raise_for_status()
        msg = r.json().get("message", {})
        calls = msg.get("tool_calls") or []
        messages.append(msg)
        if not calls:
            return (msg.get("content") or "").strip() or "(the Assistant gave no answer)"
        for c in calls:
            fn = c.get("function", {})
            name, args = fn.get("name", ""), fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            if name not in allowed:
                out = f"{name} is not available in a scheduled task."
            else:
                try:
                    out = _text(asyncio.run(tb.mcp.call_tool(name, args)))
                except Exception as e:  # noqa: BLE001
                    out = f"{name} failed: {e}"
            messages.append({"role": "tool", "content": out, "tool_name": name})
    return "Stopped after too many steps. The task may be too big for an unattended run: try a smaller one."


def deliver(job: dict, body: str, when: dt.datetime) -> str:
    folder = WORKSPACE / "scheduled"
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / f"{when:%Y-%m-%d_%H%M}-{job['id']}.md"
    title = ("Reminder: " if job["kind"] == "reminder" else "Scheduled: ") + re.sub(r"\s+", " ", job["text"])[:70]
    f.write_text(f"# {title}\n\n{when:%A %Y-%m-%d %H:%M}\n\n{body}\n")
    if not _email_ready():
        return f"saved {f.name}"
    try:
        import emailer

        emailer.send([emailer.settings()["allowed_list"][0]], title, body + f"\n\n---\n(Scheduled task {job['id']}: "
                     "say 'cancel scheduled task " + job["id"] + "' to stop it.)", [])  # fmt: skip
        return f"e-mailed and saved {f.name}"
    except Exception as e:  # noqa: BLE001
        return f"saved {f.name}; e-mail failed: {e}"


def run_due(post=None) -> list[str]:
    """Run every task that is due. Only one copy runs at a time (a task can take minutes)."""
    SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock = open(SCHEDULE_FILE.with_suffix(".lock"), "w")  # noqa: SIM115 - held for the whole run
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return []
    done, used_model = [], False
    t = now()
    jobs = load()
    due = [j for j in jobs if j["active"] and dt.datetime.fromisoformat(j["next_run"]) <= t]
    for j in due:  # move each to its next time first, so a crash never repeats it
        nxt = dt.datetime.fromisoformat(j["next_run"])
        while nxt <= t and j["repeat"]:
            nxt = _wall(next_after(nxt, j["repeat"]))
        j["next_run"], j["active"] = nxt.isoformat(), bool(j["repeat"])
    save(jobs)
    for j in due:
        if j["kind"] == "reminder":
            body = j["text"]
        else:
            used_model = True
            try:
                body = run_task(j["text"], post)
            except Exception as e:  # noqa: BLE001
                body = f"The scheduled task could not run: {type(e).__name__}: {e}"
        result = deliver(j, body, t)
        fresh = load()
        for f in fresh:
            if f["id"] == j["id"]:
                f.update({"last_run": t.isoformat(), "last_result": result, "runs": f.get("runs", 0) + 1})
        save(fresh)
        done.append(f"[{j['id']}] {j['kind']}: {result}")
    if used_model:  # the run used the model for other text: read the Assistant's instructions again
        try:
            import warmup

            warmup.warm_up()
        except Exception:  # noqa: BLE001
            pass
    return done


if __name__ == "__main__":
    if "--run-due" in sys.argv:
        for line in run_due():
            print(line)
    else:
        print(listing())
