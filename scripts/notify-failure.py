#!/usr/bin/env python3
"""Send one e-mail when an ai-* unit fails. Intended for systemd OnFailure=."""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "toolbox"))
import emailer

STATE_DIR = Path.home() / ".cache" / "ai-alerts"


def redact(text: str) -> str:
    import re

    text = re.sub(r"(?i)(key|token|password|secret|api_key)\s*[=:]\s*\S+", r"\1=[REDACTED]", text)
    text = re.sub(r"Bearer\s+\S+", "Bearer [REDACTED]", text)
    text = re.sub(r"\b[0-9a-fA-F]{24,}\b", "[REDACTED_HEX]", text)
    text = re.sub(r"\b[A-Za-z0-9+/]{32,}={0,2}\b", "[REDACTED_B64]", text)
    return text


def run_cmd(cmd):
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        return (e.output or "") + f"\n(exit {e.returncode})"


def load_state():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    p = STATE_DIR / "notify-state.json"
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        data = {}
    # keep per-unit timestamps forever; reset only the daily counter on a new day
    if data.get("day") != date.today().isoformat():
        data["day"] = date.today().isoformat()
        data["count"] = 0
    data.setdefault("units", {})
    data.setdefault("count", 0)
    return data, p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--unit", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    unit = args.unit
    host = socket.gethostname()
    status = run_cmd(["systemctl", "status", unit, "--no-pager"])
    journal = run_cmd(["journalctl", "-u", unit, "-n", "30", "--no-pager", "--output=cat"])
    status_lines = redact(status).splitlines()[:10]
    journal_lines = redact(journal).splitlines()[-30:]
    status_block = "\n".join("    " + line for line in status_lines)
    journal_block = "\n".join("    " + line for line in journal_lines)
    body = (
        f"{unit} failed on {host}.\n\n"
        "=== systemctl status (first 10 lines) ===\n\n" + status_block + "\n\n"
        "=== last 30 journal lines ===\n\n" + journal_block
    )
    # journal to 30 lines max (the command already limits, but be safe)
    lines = body.splitlines()
    if len(lines) > 200:
        body = "\n".join(lines[:200])
    subject = f"[{host}] {unit} failed"

    dry = args.dry_run
    data, p = load_state()
    now = time.time()
    units = data["units"]
    if not dry:
        if (now - units.get(unit, 0)) < 6 * 3600:
            print(f"rate limited: {unit} alerted within the last 6 h")
            return 0
        if data["count"] >= 5:
            print("daily alert limit (5) reached")
            return 0

    allowed = emailer.settings()["allowed_list"]
    if not allowed:
        print("no allowed address configured")
        return 1
    to = [allowed[0]]
    if dry:
        print(f"To: {to[0]}")
        print(f"Subject: {subject}")
        print("")
        print(body)
        return 0

    try:
        print(emailer.send(to, subject, body, check_allowed=True))
    except emailer.EmailError as e:
        print(f"e-mail not sent: {e}")
        return 1
    units[unit] = now
    data["count"] += 1
    p.write_text(json.dumps(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
