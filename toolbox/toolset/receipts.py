"""read_receipts: receipt photos and PDF invoices to a spreadsheet, with KnowReva's open-source
receipt-reader (https://github.com/knowreva/receipt-reader). Optional: if it isn't installed, the
tool says how to install it. It uses Dhoon's own model (it can read images) unless RECEIPT_MODEL
names another, e.g. qwen2.5vl:7b. RECEIPT_CURRENCY (e.g. CAD) is your usual currency, for receipts
that only print "$". RECEIPT_FAST=on reads photos' text with Tesseract first (receipt-reader's --fast;
needs tesseract-ocr and pytesseract): much faster on a CPU, with the picture read only when the text
doesn't add up. Updates to receipt-reader reach Dhoon when the package is updated (see below)."""

import csv
import os

import heavy
import toolbox_server as tb

MOST = 20  # receipts per call: each takes up to a minute on a CPU


@tb.mcp.tool()
def read_receipts(folder: str = "receipts", output: str = "receipts.csv", currency: str = "") -> str:
    """Read the receipt photos and PDF invoices in a workspace folder into a spreadsheet (CSV in
    the workspace): date, shop, category, currency, tax and total for each. Every row is checked
    by arithmetic (items, tax and total must add up); rows marked CHECK need the user to look.
    Use when the user asks to log, list, total or organise receipts or invoices. Up to 20
    receipts per call; call again for the next ones. currency: the user's usual currency as a
    3-letter code (CAD, USD, EUR...) if they said it, for receipts that only print "$"."""
    try:
        from receipt_reader import files, output as out, reader
    except ImportError:
        return ("receipt-reader isn't installed. On the Dhoon computer run (the same command updates it): "
                "~/dhoon/runtime/venv/bin/pip install git+https://github.com/knowreva/receipt-reader "
                "then: sudo systemctl restart ai-toolbox")  # fmt: skip
    src, dest = tb._safe_path(folder), tb._safe_path(output)
    if not src.exists():
        return f"There is no folder '{folder}' in the workspace. Put the receipts in one (see list_files)."
    previous = []  # receipts already in the spreadsheet are skipped, so a second call continues
    if dest.exists():
        with open(dest, newline="", encoding="utf-8") as fh:
            previous = list(csv.DictReader(fh))
    done = {row["file"] for row in previous}
    todo = [p for p in files.find([str(src)]) if str(p) not in done]
    if not todo:
        return f"No new receipts in '{folder}' ({len(done)} already in {output})."
    model = os.environ.get("RECEIPT_MODEL") or tb.PLANNER_MODEL
    usual = (currency or os.environ.get("RECEIPT_CURRENCY", "")).strip().upper()
    extra = {"currency": usual} if usual else {}  # only when set: older receipt-readers don't take it
    if os.environ.get("RECEIPT_FAST", "").lower() == "on":
        extra["fast"] = True
    busy = heavy.running()
    if busy:  # a newsroom or knowledge-base run is using the model: two at once can push it out of memory
        return (f"Not now: {busy.get('name', 'a long job')} is running (since {busy.get('since', '?')}) and uses the "
                "same model. Reading receipts at the same time would slow both, or push its model out of memory. "
                "Ask again when it has finished (python3 ~/dhoon/toolbox/heavy.py shows what is running).")  # fmt: skip
    results, lines = [], []
    try:
        with heavy.heavy_job("receipts", wait_minutes=0):  # in the queue: scheduled jobs wait for this one
            for path in todo[:MOST]:
                try:
                    r = reader.read(path, model=model, url=tb.OLLAMA_URL, **extra)
                except Exception as e:  # noqa: BLE001 - one unreadable file doesn't stop the others
                    r = {"file": str(path), "status": "check",
                         "problems": [f"couldn't read it: {type(e).__name__}: {e}"]}  # fmt: skip
                results.append(r)
                lines.append(f"| {path.name} | {r.get('date', '')} | {r.get('vendor', '')} | {r.get('total', '')} "
                             f"{r.get('currency', '')} | {'ok' if r['status'] == 'ok' else 'CHECK: ' + '; '.join(r['problems'])[:120]} |")  # fmt: skip
    except TimeoutError:
        return "Not now: another long job has just started and uses the same model. Ask again when it has finished."
    rows = previous + [out.row(r) for r in results]
    with open(dest, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=out.COLUMNS)
        w.writeheader()
        w.writerows(rows)
    left = len(todo) - len(results)
    return (f"Read {len(results)} receipt(s) into {output} (model {model}).\n\n| File | Date | Shop | Total | Check |\n"
            f"|---|---|---|---|---|\n" + "\n".join(lines)
            + (f"\n\n{left} more receipt(s) in '{folder}': call read_receipts again to continue." if left else ""))  # fmt: skip
