"""Make the Assistant's first answer fast.

Before answering, the model must read the Assistant's instructions and the description of
every tool (about 4,000 tokens: minutes on a CPU). Ollama remembers what it has read, so if
those exact words were read recently, the next request skips them and starts in seconds.

The toolbox passes LibreChat's requests through to Ollama (see /ollama/ in
toolbox_server.py) and keeps a copy of the latest instructions + tool list here (never the
chat itself). `python3 toolbox/warmup.py` sends that same text to Ollama again, so it is
already read when you open a chat: after Ollama starts (with the model preload), after the
date changes and after nightly jobs that used the model for something else.

Ollama answers WARM_PARALLEL requests at once (NUM_PARALLEL, 2 with 28+ GB RAM), each in its
own slot with its own memory of what it read. So the same text is sent that many times at
once: every slot has it read, and a chat that arrives while a long job uses one slot still
starts in seconds in the other. What a slot has read stays until it reads something else.
"""

import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import httpx

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
WARM_DIR = Path(os.environ.get("WARM_DIR", str(Path(__file__).resolve().parents[1] / "runtime" / "warm")))
# How LibreChat may write {{current_date}}: tried longest first to find it in the saved text.
DATE_FORMATS = [
    "%A, %B %d, %Y", "%A, %d %B %Y", "%Y-%m-%d (%A)", "%A, %Y-%m-%d", "%Y-%m-%d, %A", "%A %Y-%m-%d",
    "%B %d, %Y", "%d %B %Y", "%a %b %d %Y", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y",
]  # fmt: skip


def _file(model: str, tools: list) -> Path:
    """One copy per agent (model + its tool list), e.g. the Assistant and the Quick Assistant."""
    import hashlib

    names = sorted(str((t.get("function") or t).get("name", "")) for t in tools if isinstance(t, dict))
    tag = hashlib.sha1("|".join(names).encode()).hexdigest()[:8]
    return WARM_DIR / ("".join(c if c.isalnum() else "_" for c in model) + f"-{tag}.json")


def wanted_models() -> list[str]:
    """The models the agents use now (WARM_MODELS, set by preload_model.sh from MODEL_MAIN and
    MODEL_FAST): the main model last, so it is the one Ollama keeps in memory longest. Empty: all."""
    return [m for m in os.environ.get("WARM_MODELS", "").split() if m]


def latest_per_model(models: list[str] | None = None) -> list[Path]:
    """Ollama keeps one read-ahead per model, so warm only the agent used most recently. Copies
    for models the agents no longer use (after a model change) are left alone: loading an old
    model would take minutes and push the main model out of memory."""
    models = wanted_models() if models is None else models
    newest: dict[str, Path] = {}
    for f in WARM_DIR.glob("*.json") if WARM_DIR.exists() else []:
        try:
            model = json.loads(f.read_text())["model"]
        except (OSError, ValueError, KeyError):
            continue
        if models and model not in models:
            continue
        if model not in newest or f.stat().st_mtime > newest[model].stat().st_mtime:
            newest[model] = f
    if models:  # WARM_MODELS lists the main model first; it is warmed last
        return [newest[m] for m in reversed(models) if m in newest]
    return sorted(newest.values())


def remember(body: bytes) -> bool:
    """Keep the instructions + tools of a chat request that uses tools. Never raises."""
    try:
        data = json.loads(body)
        tools, messages, model = data.get("tools"), data.get("messages") or [], data.get("model")
        if not tools or not model or not messages or str(model).endswith("cloud"):
            return False  # a cloud model (MODEL_CLOUD) has nothing to load on this computer
        system = []
        for m in messages:  # the leading system message(s): the Assistant's instructions
            if m.get("role") not in ("system", "developer"):
                break
            system.append(m)
        if not system:
            return False
        saved = {"model": model, "system": system, "tools": tools, "date": dt.date.today().isoformat()}
        f = _file(model, tools)
        old = json.loads(f.read_text()) if f.exists() else None
        if old and {k: old.get(k) for k in ("system", "tools", "date")} == {
            k: saved[k] for k in ("system", "tools", "date")
        }:
            os.utime(f)  # mark this agent as the one used most recently
            return True
        WARM_DIR.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(saved, ensure_ascii=False))
        tmp.replace(f)
        return True
    except Exception:  # noqa: BLE001 - a copy for speed must never break a chat
        return False


def _redate(text: str, then: dt.date, today: dt.date) -> str:
    """Write today's date where the saved instructions had the date they were saved on."""
    if then == today:
        return text
    for fmt in sorted(DATE_FORMATS, key=lambda f: -len(then.strftime(f))):
        old = then.strftime(fmt)
        if old in text:
            return text.replace(old, today.strftime(fmt))
    return text


def _slots() -> int:
    try:
        return max(1, min(int(os.environ.get("WARM_PARALLEL", "1")), 8))
    except ValueError:
        return 1


def warm_up(transport: httpx.BaseTransport | None = None, today: dt.date | None = None) -> list[str]:
    """Send every saved instructions + tools to Ollama, once per slot, all at the same time
    (so each lands in its own slot). Returns one line per model."""
    from concurrent.futures import ThreadPoolExecutor

    today = today or dt.date.today()
    slots = _slots()
    out = []
    for f in latest_per_model():
        try:
            saved = json.loads(f.read_text())
            then = dt.date.fromisoformat(saved["date"])
            system = [
                {**m, "content": _redate(m["content"], then, today)} if isinstance(m.get("content"), str) else m
                for m in saved["system"]
            ]
            body = {
                "model": saved["model"],
                "messages": [*system, {"role": "user", "content": "Hello"}],
                "tools": saved["tools"],
                "max_tokens": 1,
                "stream": False,
            }
            start = time.monotonic()

            def send(_, body=body):
                with httpx.Client(transport=transport, timeout=httpx.Timeout(3600, connect=10)) as client:
                    client.post(f"{OLLAMA_URL}/v1/chat/completions", json=body).raise_for_status()

            with ThreadPoolExecutor(max_workers=slots) as pool:
                list(pool.map(send, range(slots)))  # raises the first error, if any
            where = f"in all {slots} slots, " if slots > 1 else ""
            out.append(
                f"{saved['model']}: instructions and {len(saved['tools'])} tools read in advance "
                f"({where}{time.monotonic() - start:.0f} s)"
            )
        except Exception as e:  # noqa: BLE001
            out.append(f"{f.stem}: not warmed up ({type(e).__name__}: {e})")
    return out


if __name__ == "__main__":
    lines = warm_up()
    print("\n".join(lines) or "Nothing to warm up yet: it starts after your first chat with the Assistant.")
    sys.exit(0)
