#!/usr/bin/env python3
import argparse
import shutil
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "toolbox"))
import emailer

DEFAULT_ENV = Path.home() / ".config" / "ai-alerts" / "health.env"


def read_units(path):
    units = []
    p = Path(path)
    if not p.is_file():
        return units
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.replace(":", " ").split()
        if len(parts) < 2:
            continue
        try:
            units.append((parts[0], float(parts[1])))
        except ValueError:
            continue
    return units


def parse_failed(text):
    bad = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        unit = parts[0]
        if unit in ("●", "*") and len(parts) > 1:
            unit = parts[1]
        if unit.startswith("ai-") and unit.endswith(".service"):
            bad.append(unit)
    return bad


def failed_ai_units():
    out = subprocess.run(["systemctl", "list-units", "--type=service", "--state=failed", "--no-legend", "--plain"],
                         capture_output=True, text=True)
    return parse_failed(out.stdout)


def last_success_age_hours(unit):
    # A unit that never succeeded (or whose last run failed) counts as stale.
    out = subprocess.run(["systemctl", "show", "--timestamp=unix", unit, "-p", "Result", "-p",
                          "ExecMainExitTimestamp"], capture_output=True, text=True)
    result = None
    ts = None
    for line in out.stdout.splitlines():
        if line.startswith("Result="):
            result = line.split("=", 1)[1].strip()
        elif line.startswith("ExecMainExitTimestamp="):
            val = line.split("=", 1)[1].strip()
            if val.startswith("@"):
                val = val[1:]
            if val and val != "0":
                ts = val
    if not ts:
        return "never"  # caller decides message
    if result != "success":
        return ("failed", result)
    try:
        return (datetime.now().timestamp() - float(ts)) / 3600.0
    except ValueError:
        return "never"


def disk_bad():
    total, used, free = shutil.disk_usage("/")
    percent_free = free / total * 100 if total else 100
    free_gb = free / (1024 ** 3)
    return percent_free < 15 or free_gb < 20, percent_free, free_gb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--env", default=str(DEFAULT_ENV))
    args = ap.parse_args()

    problems = []
    bad_disk, pct, free_gb = disk_bad()
    if bad_disk:
        problems.append(f"disk low: {pct:.1f}% free ({free_gb:.1f} GB)")

    for u in failed_ai_units():
        problems.append(f"failed unit: {u}")

    for unit, max_age in read_units(args.env):
        age = last_success_age_hours(unit)
        if age == "never":
            problems.append(f"stale: {unit} never succeeded")
        elif isinstance(age, tuple) and age and age[0] == "failed":
            problems.append(f"stale: {unit} last run failed (Result={age[1]})")
        elif age is not None and age > max_age:
            problems.append(f"stale: {unit} last success {age:.1f}h ago (limit {max_age}h)")

    if not problems:
        print("health check: OK")
        return 0

    body = "Health check problems:\n" + "\n".join(f"- {p}" for p in problems)
    if args.dry_run:
        allowed = emailer.settings().get("allowed_list", [])
        to = allowed[0] if allowed else "(none)"
        print(f"To: {to}")
        print(f"Subject: [health] {socket.gethostname()} problems")
        print("")
        print(body)
        return 0

    allowed = emailer.settings()["allowed_list"]
    if not allowed:
        print(body)
        return 1
    try:
        print(emailer.send([allowed[0]], f"[health] {socket.gethostname()} problems", body, check_allowed=True))
    except emailer.EmailError as e:
        print(f"e-mail not sent: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
