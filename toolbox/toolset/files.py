"""Workspace files and the locked-down Python sandbox."""

import os
import re
import subprocess
from pathlib import Path

import toolbox_server as tb


# ------------------------------------------------------------------ files ---
@tb.mcp.tool()
def list_files(folder: str = ".") -> str:
    """List files in the agent workspace (or a sub-folder of it)."""
    p = tb._safe_path(folder)
    if not p.is_dir():
        return f"Not a folder: {folder}"
    items = [
        f"{'[dir] ' if c.is_dir() else ''}{c.relative_to(tb.WORKSPACE)}"
        + ("" if c.is_dir() else f"  ({c.stat().st_size} bytes)")
        for c in sorted(p.iterdir())
        if not c.name.startswith(".")  # hide the search index folder
    ]
    return "\n".join(items) or "(empty)"


@tb.mcp.tool()
def read_file(path: str) -> str:
    """Read a text file from the agent workspace (truncated if long)."""
    p = tb._safe_path(path)
    text = p.read_text(errors="replace")
    return text[: tb.MAX_TEXT] + ("\n...[truncated]" if len(text) > tb.MAX_TEXT else "")


@tb.mcp.tool()
def write_file(path: str, content: str) -> str:
    """Create or overwrite a text file in the agent workspace (e.g. reports/today.md)."""
    p = tb._safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"Saved {p.relative_to(tb.WORKSPACE)} ({len(content)} characters).{_links([p])}"


@tb.mcp.tool()
def file_link(path: str) -> str:
    """A new download link for a file in the workspace, so the user can open it
    (e.g. when an older link has expired, or for a file they put there themselves)."""
    p = tb._safe_path(path)
    if not p.is_file():
        return f"No such file: {path}"
    return _links([p]).lstrip("\n") or "Could not make a link for that file."


def _links(files) -> str:
    """Download links for files the user may want to open (see fileshare.py)."""
    import fileshare

    out = []
    for p in list(files)[:5]:
        try:
            out.append(f"\n[{p.name}]({fileshare.make_link(p, tb.WORKSPACE)})")
        except (ValueError, OSError):
            continue
    if not out:
        return ""
    return "\nDownload link (show it to the user exactly like this):" + "".join(out)


def _snapshot() -> dict:
    """Modification times of the workspace files (hidden folders skipped)."""
    seen = {}
    for root, dirs, names in os.walk(tb.WORKSPACE):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for n in names:
            p = Path(root) / n
            try:
                seen[p] = p.stat().st_mtime_ns
            except OSError:
                pass
    return seen


# ------------------------------------------------------------------ code ---
# Imported for every run (one line, so error line numbers are off by just 1), because
# models often forget "import pandas as pd".
PY_PREAMBLE = "import pandas as pd, numpy as np, matplotlib.pyplot as plt\n"


@tb.mcp.tool()
def run_python(code: str, timeout_seconds: int = 60) -> str:
    """Run Python 3 code in an isolated sandbox and return what it prints.
    Installed: pandas, numpy, matplotlib, openpyxl (Excel: df.to_excel('x.xlsx'), read_excel)
    and python-docx (Word: import docx). The sandbox has NO internet: it
    cannot download anything (no yfinance, no requests). Get data with the other tools
    first (e.g. stock_save_history writes prices to a CSV file), then read it here.
    Never use made-up, random or "simulated" numbers in place of real data.
    pd (pandas), np (numpy) and plt (matplotlib.pyplot) are already imported.
    The workspace folder is available as the current directory, so files you
    read/write with relative paths (e.g. 'data.csv', 'chart.png') live there.
    Always print() the results you want to see."""
    cmd = [
        "docker",
        "run",
        "--rm",
        "-i",
        "--network",
        "none",  # no internet, no LAN
        "--memory",
        "2g",
        "--cpus",
        "2",
        "--pids-limit",
        "128",
        "--read-only",
        "--tmpfs",
        "/tmp:size=256m",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-e",
        "HOME=/tmp",
        "-e",
        "MPLCONFIGDIR=/tmp",
        "-e",
        "MPLBACKEND=Agg",
        "-v",
        f"{tb.WORKSPACE}:/work",
        "-w",
        "/work",
        tb.SANDBOX_IMAGE,
        "python",
        "-",
    ]
    before = _snapshot()
    try:
        r = subprocess.run(
            cmd, input=PY_PREAMBLE + code, capture_output=True, text=True, timeout=max(5, min(timeout_seconds, 300))
        )
    except subprocess.TimeoutExpired:
        return "Error: the code took too long and was stopped."
    out = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else "")
    out = out.strip() or "(no output - did you forget print()?)"
    if re.search(
        r"ModuleNotFoundError|No module named|NameResolutionError|Temporary failure in name resolution"
        r"|Network is unreachable|getaddrinfo failed",
        out,
    ):
        out += (
            "\n\nHINT: the sandbox has no internet and only pandas, numpy and matplotlib. Don't download "
            "here: get the data with another tool first (stock_save_history for stock prices, "
            "read_webpage for pages), save it with write_file if needed, then read the file here."
        )
    out = out[-tb.MAX_TEXT :] if len(out) > tb.MAX_TEXT else out
    made = [p for p, t in _snapshot().items() if before.get(p) != t]  # files made or changed by this run
    return out + _links(sorted(made, key=lambda p: p.stat().st_mtime_ns, reverse=True))
