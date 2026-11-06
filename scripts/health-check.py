#!/usr/bin/env python3
import argparse
import os
import re
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


def read_units(path, scope="system"):
    """(unit, max_age_hours) lines of health.env. A line starting with "user " is a systemd user unit
    (systemctl --user), read with scope="user"; "check " lines are commands (read_checks)."""
    units = []
    p = Path(path)
    if not p.is_file():
        return units
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("check "):
            continue
        is_user = line.startswith("user ")
        if is_user != (scope == "user"):
            continue
        if is_user:
            line = line[5:].strip()
        parts = line.replace(":", " ").split()
        if len(parts) < 2:
            continue
        try:
            units.append((parts[0], float(parts[1])))
        except ValueError:
            continue
    return units


def read_checks(path):
    """Commands from "check <command>" lines: each must exit 0, or its last output line is a problem."""
    p = Path(path)
    if not p.is_file():
        return []
    return [line.strip()[6:].strip() for line in p.read_text().splitlines()
            if line.strip().startswith("check ") and line.strip()[6:].strip()]


def run_check(cmd, timeout=600):
    """None when the command exits 0, else a short reason."""
    try:
        out = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=timeout,
                             env=_user_env())
    except subprocess.TimeoutExpired:
        return f"timed out after {timeout}s"
    except OSError as e:
        return f"could not run ({e})"
    if out.returncode == 0:
        return None
    lines = [x for x in (out.stdout + "\n" + out.stderr).splitlines() if x.strip()]
    return f"exit {out.returncode}: {lines[-1][:200] if lines else 'no output'}"


def _user_env():
    """systemctl/journalctl --user from a system service (User=...) need the user's runtime dir."""
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


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


def failed_ai_units(user=False):
    cmd = ["systemctl"] + (["--user"] if user else []) + ["list-units", "--type=service", "--state=failed",
                                                         "--no-legend", "--plain"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, env=_user_env() if user else None)
    except OSError:
        return []
    return parse_failed(out.stdout)


def journal_last_success_timestamp(text, unit, user=False):
    """Return epoch seconds of the latest real systemd success line in journal text.

    Only systemd's own lines count:
      " systemd[1]: Finished ..." and
      " systemd[1]: <unit>: Deactivated successfully."
    A job's own output containing "Finished" does not count.
    """
    latest = None
    for line in (text or "").splitlines():
        parts = line.split()
        if not parts:
            continue
        try:
            ts = float(parts[0])
        except ValueError:
            continue
        mgr = r" systemd\[\d+\]: " if user else r" systemd\[1\]: "  # a user manager isn't pid 1
        if re.search(mgr + "Finished ", line) or re.search(mgr + re.escape(f"{unit}: Deactivated successfully."), line):
            if latest is None or ts > latest:
                latest = ts
    return latest


def has_journal_entries(text):
    text = text or ""
    if not text.strip():
        return False
    if "-- No entries --" in text:
        return False
    return True


def run_journalctl(unit, user=False):
    try:
        out = subprocess.run(["journalctl"] + (["--user"] if user else []) + ["-u", unit, "--since", "-7 days",
                              "-o", "short-unix", "--no-pager"],
                             capture_output=True, text=True, env=_user_env() if user else None)
    except Exception:
        return ""
    return out.stdout or ""


def run_systemctl_show(unit, user=False):
    try:
        out = subprocess.run(["systemctl"] + (["--user"] if user else []) + ["show", "--timestamp=unix", unit,
                              "-p", "Result", "-p", "ExecMainExitTimestamp"], capture_output=True, text=True,
                             env=_user_env() if user else None)
    except Exception:
        return ""
    return out.stdout or ""


def last_success_age_hours(unit, user=False):
    # Prefer the journal: it survives a reboot, when ExecMainExitTimestamp is empty.
    journal_text = run_journalctl(unit, user=True) if user else run_journalctl(unit)
    ts = journal_last_success_timestamp(journal_text, unit, user=user)
    if ts is not None:
        return (datetime.now().timestamp() - ts) / 3600.0
    if has_journal_entries(journal_text):
        # The journal ran this unit but never recorded a successful completion.
        return "never"

    # Fall back to systemctl's recorded timestamp only when the journal is empty.
    show_text = run_systemctl_show(unit, user=True) if user else run_systemctl_show(unit)
    result = None
    ts = None
    for line in show_text.splitlines():
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
    user_units = read_units(args.env, scope="user")
    if user_units:
        for u in failed_ai_units(user=True):
            problems.append(f"failed user unit: {u}")

    for scope, units in (("system", read_units(args.env)), ("user", user_units)):
        for unit, max_age in units:
            age = last_success_age_hours(unit, user=True) if scope == "user" else last_success_age_hours(unit)
            label = f"user {unit}" if scope == "user" else unit
            if age == "never":
                problems.append(f"stale: {label} never succeeded")
            elif isinstance(age, tuple) and age and age[0] == "failed":
                problems.append(f"stale: {label} last run failed (Result={age[1]})")
            elif age is not None and age > max_age:
                problems.append(f"stale: {label} last success {age:.1f}h ago (limit {max_age}h)")

    for cmd in read_checks(args.env):
        why = run_check(cmd)
        if why:
            problems.append(f"check failed: {cmd[:80]}: {why}")

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
