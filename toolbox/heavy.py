"""One heavy job at a time, and your chats first.

Long background jobs (the knowledge base, plugins' nightly runs) all use the same model on the
same CPU. Two at once make both slow and chats crawl. Every heavy job runs inside

    with heavy.heavy_job("knowledge base"):
        ...
        heavy.yield_to_chats()   # between steps: wait while someone is chatting

so jobs queue one after another (each waits for the one before, up to wait_minutes), and a
job pauses between its steps while a chat is going on (the toolbox marks every chat request).
`python3 toolbox/heavy.py` shows which job is running.
"""

import contextlib
import fcntl
import json
import os
import time
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]


def _dir() -> Path:
    d = Path(os.environ.get("HEAVY_DIR", os.environ.get("RUNTIME_DIR", str(KIT / "runtime"))))
    d.mkdir(parents=True, exist_ok=True)
    return d


def chat_seen() -> None:
    """Called by the toolbox for every chat request (never raises)."""
    try:
        (_dir() / "last-chat").touch()
    except OSError:
        pass


def chatting(within_seconds: float = 90) -> bool:
    f = _dir() / "last-chat"
    try:
        return time.time() - f.stat().st_mtime < within_seconds
    except OSError:
        return False


def yield_to_chats(max_wait_seconds: float = 600, quiet_seconds: float = 90, sleep=time.sleep) -> float:
    """Wait (at most max_wait_seconds) while someone is chatting. Returns the seconds waited."""
    waited = 0.0
    while chatting(quiet_seconds) and waited < max_wait_seconds:
        sleep(15)
        waited += 15
    return waited


def running() -> dict:
    """The heavy job running now, or {}."""
    f = _dir() / "heavy.json"
    try:
        info = json.loads(f.read_text())
    except (OSError, ValueError):
        return {}
    try:
        os.kill(int(info.get("pid", 0)), 0)
    except (OSError, ValueError):
        return {}
    return info


@contextlib.contextmanager
def heavy_job(name: str, wait_minutes: float = 240, sleep=time.sleep):
    """Run a heavy job when no other one runs (waits for it). Raises TimeoutError if it waited
    too long, so a stuck job never blocks the others forever."""
    lock = open(_dir() / "heavy.lock", "w")  # noqa: SIM115
    waited, told = 0.0, False
    try:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if waited >= wait_minutes * 60:
                    raise TimeoutError(f"{running().get('name', 'another heavy job')} is still running") from None
                if not told:
                    print(f"Waiting for {running().get('name', 'another heavy job')} to finish...", flush=True)
                    told = True
                sleep(30)
                waited += 30
        (_dir() / "heavy.json").write_text(
            json.dumps({"name": name, "pid": os.getpid(), "since": time.strftime("%H:%M")})
        )
        yield
    finally:
        with contextlib.suppress(OSError):
            if running().get("pid") == os.getpid():
                (_dir() / "heavy.json").unlink()
        lock.close()


if __name__ == "__main__":
    job = running()
    print(f"Running now: {job['name']} (since {job['since']})" if job else "No heavy job is running.")
