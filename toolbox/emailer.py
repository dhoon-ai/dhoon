"""
emailer.py - send e-mail (with attachments) through your own mail account over SMTP.

Settings come from config/email.env (the toolbox service loads it; never commit it):

  EMAIL_PROVIDER=gmail          gmail | zoho | zoho-in | zoho-eu | outlook | office365 | custom
  EMAIL_ADDRESS=you@gmail.com   the account that sends
  EMAIL_APP_PASSWORD="abcd efgh ijkl mnop"   an app password, not your normal password
  EMAIL_ALLOWED_TO=you@gmail.com,you@outlook.com   the ONLY addresses the agent may write to
  EMAIL_FROM_NAME="Local AI"    optional
  EMAIL_MAX_PER_DAY=20          optional safety limit
  EMAIL_SMTP_HOST / EMAIL_SMTP_PORT / EMAIL_SMTP_SECURITY (ssl|starttls)   only for "custom"

Gmail: turn on 2-Step Verification, then Google Account -> Security -> App passwords.
Zoho: Account -> Security -> App passwords (needed when 2-factor login is on). Accounts made
      in India use zoho-in, in Europe zoho-eu.
Outlook/Microsoft 365: Microsoft is retiring password (basic) sign-in for sending from apps in
      favour of OAuth2, so this usually fails. Send with Gmail or Zoho instead; you can still
      send TO an Outlook address.

Test:  bash scripts/email_test.sh
"""

import json
import mimetypes
import os
import smtplib
import ssl
from datetime import date
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

PROVIDERS = {
    "gmail": ("smtp.gmail.com", 587, "starttls"),
    "zoho": ("smtp.zoho.com", 465, "ssl"),
    "zoho-in": ("smtp.zoho.in", 465, "ssl"),
    "zoho-eu": ("smtp.zoho.eu", 465, "ssl"),
    "outlook": ("smtp-mail.outlook.com", 587, "starttls"),
    "office365": ("smtp.office365.com", 587, "starttls"),
}
AUTH_HELP = {
    "gmail": "Gmail needs an App Password (Google Account -> Security -> 2-Step Verification -> App passwords), "
    "not your normal password.",
    "zoho": "Zoho needs an app-specific password when 2-factor login is on (Account -> Security -> App "
    "passwords). Accounts created in India use EMAIL_PROVIDER=zoho-in, in Europe zoho-eu.",
    "outlook": "Microsoft no longer accepts passwords for sending mail from apps (OAuth2 only). Send with "
    "Gmail or Zoho instead; you can still send TO your Outlook address.",
}
AUTH_HELP["zoho-in"] = AUTH_HELP["zoho-eu"] = AUTH_HELP["zoho"]
AUTH_HELP["office365"] = AUTH_HELP["outlook"]
MAX_ATTACHMENTS_MB = 15


class EmailError(Exception):
    """A problem the user can fix (settings, recipients, attachments): the message says how."""


ENV_FILE = Path(os.environ.get("EMAIL_ENV_FILE", str(Path(__file__).resolve().parents[1] / "config" / "email.env")))


def _load_env_file() -> None:
    """Read config/email.env when the settings aren't in the environment yet (e.g. a script
    run from the terminal). Values already set in the environment win."""
    if os.environ.get("EMAIL_PROVIDER") or not ENV_FILE.is_file():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def settings() -> dict:
    _load_env_file()
    provider = os.environ.get("EMAIL_PROVIDER", "").strip().lower()
    address = os.environ.get("EMAIL_ADDRESS", "").strip()
    if not provider or not address:
        raise EmailError(
            "E-mail is not set up. Copy config/email.env.example to config/email.env, fill it in, "
            "then run: bash scripts/04_toolbox.sh"
        )
    if provider == "custom":
        host = os.environ.get("EMAIL_SMTP_HOST", "")
        port = int(os.environ.get("EMAIL_SMTP_PORT", "587"))
        security = os.environ.get("EMAIL_SMTP_SECURITY", "starttls").lower()
    elif provider in PROVIDERS:
        host, port, security = PROVIDERS[provider]
    else:
        raise EmailError(f"Unknown EMAIL_PROVIDER '{provider}'. Use one of: {', '.join(PROVIDERS)}, custom.")
    password = os.environ.get("EMAIL_APP_PASSWORD", "")
    if provider == "gmail":
        password = password.replace(" ", "")  # Google shows app passwords in groups of 4
    allowed = os.environ.get("EMAIL_ALLOWED_TO", "") or address
    return {
        "provider": provider,
        "address": address,
        "password": password,
        "host": host,
        "port": port,
        "security": security,
        "from_name": os.environ.get("EMAIL_FROM_NAME", "Local AI"),
        "allowed": {a.strip().lower() for a in allowed.split(",") if a.strip()},
        "allowed_list": [a.strip() for a in allowed.split(",") if a.strip()],  # in the order you wrote them
        "max_per_day": int(os.environ.get("EMAIL_MAX_PER_DAY", "20")),
    }


def _count_file() -> Path:
    return Path(os.environ.get("EMAIL_STATE", str(Path.home() / ".local/state/local-ai-agent/email-count.json")))


def _sent_today() -> int:
    try:
        data = json.loads(_count_file().read_text())
        return data["count"] if data.get("date") == date.today().isoformat() else 0
    except (OSError, ValueError, KeyError):
        return 0


def _count_one() -> None:
    f = _count_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"date": date.today().isoformat(), "count": _sent_today() + 1}))


def markdown_to_html(text: str) -> str:
    try:
        import markdown

        body = markdown.markdown(text, extensions=["tables", "sane_lists"])
    except ImportError:
        import html

        body = f"<pre>{html.escape(text)}</pre>"
    style = (
        "body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;line-height:1.5;color:#222;max-width:760px}"
        "table{border-collapse:collapse;margin:8px 0}td,th{border:1px solid #ccc;padding:4px 8px;font-size:14px}"
        "th{background:#f3f3f3}"
    )
    return f"<html><head><style>{style}</style></head><body>{body}</body></html>"


def send(
    to: list[str],
    subject: str,
    body_markdown: str,
    attachments: list[Path] = (),
    check_allowed: bool = True,
) -> str:
    """Send one e-mail (plain text + HTML version of the Markdown body, plus attachments)."""
    cfg = settings()
    recipients = [t.strip() for t in to if t and t.strip()]
    if not recipients:
        raise EmailError("No recipient given.")
    if check_allowed:
        blocked = [r for r in recipients if r.lower() not in cfg["allowed"]]
        if blocked:
            raise EmailError(
                f"Not allowed to send to {', '.join(blocked)}. Allowed: {', '.join(sorted(cfg['allowed']))}. "
                "Add addresses to EMAIL_ALLOWED_TO in config/email.env if you trust them."
            )
    if _sent_today() >= cfg["max_per_day"]:
        raise EmailError(f"Daily limit of {cfg['max_per_day']} e-mails reached (EMAIL_MAX_PER_DAY).")
    total = sum(Path(a).stat().st_size for a in attachments)
    if total > MAX_ATTACHMENTS_MB * 1024 * 1024:
        raise EmailError(f"Attachments are {total / 1e6:.1f} MB; the limit is {MAX_ATTACHMENTS_MB} MB.")

    msg = EmailMessage()
    msg["From"] = formataddr((cfg["from_name"], cfg["address"]))
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject[:200]
    msg.set_content(body_markdown)
    msg.add_alternative(markdown_to_html(body_markdown), subtype="html")
    for a in attachments:
        path = Path(a)
        ctype, _ = mimetypes.guess_type(path.name)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)

    context = ssl.create_default_context()
    try:
        if cfg["security"] == "ssl":
            server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], context=context, timeout=60)
        else:
            server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=60)
        with server:
            if cfg["security"] != "ssl":
                server.starttls(context=context)
            server.login(cfg["address"], cfg["password"])
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise EmailError(
            f"The mail server refused the login ({e.smtp_code}). {AUTH_HELP.get(cfg['provider'], '')}".strip()
        ) from None
    except (smtplib.SMTPException, OSError) as e:
        raise EmailError(f"Could not send through {cfg['host']}:{cfg['port']}: {type(e).__name__}: {e}") from None
    _count_one()
    names = f" with {len(attachments)} attachment(s)" if attachments else ""
    return f"E-mail '{subject[:80]}' sent to {', '.join(recipients)}{names}."


def send_test() -> str:
    """bash scripts/email_test.sh: send a test e-mail to the first address in EMAIL_ALLOWED_TO
    (your own inbox), from EMAIL_ADDRESS (the sending account)."""
    s = settings()
    to = s["allowed_list"][0]
    body = (
        f"It works! **E-mail is set up.**\n\nSent from {s['address']} through {s['host']}. "
        f"The agent may send to: {', '.join(s['allowed_list'])}."
    )
    return send([to], "Test from your local AI agent", body)


if __name__ == "__main__":
    import sys

    try:
        print(send_test())
    except EmailError as e:
        print(f"E-mail not sent: {e}")
        sys.exit(1)
