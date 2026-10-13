"""
emailer.py - send e-mail (with attachments) through your own mail account over SMTP.

Settings come from config/email.env (the toolbox service loads it; never commit it):

  EMAIL_PROVIDER=gmail          gmail | zoho | zoho-in | zoho-eu | outlook | office365 | custom
                                or, over HTTPS (when mail ports are blocked): resend | brevo
  EMAIL_API_KEY=...             only for resend / brevo
  EMAIL_ADDRESS=you@gmail.com   the account that sends
  EMAIL_APP_PASSWORD="abcd efgh ijkl mnop"   an app password, not your normal password
  EMAIL_ALLOWED_TO=you@gmail.com,you@outlook.com   the ONLY addresses the agent may write to
  EMAIL_FROM_NAME="Inglenook"   optional
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
# Providers that send through a web API over HTTPS (port 443): they work on networks where
# the mail ports 587/465 are blocked or cut off. Value: the API address.
API_PROVIDERS = {
    "resend": "https://api.resend.com/emails",
    "brevo": "https://api.brevo.com/v3/smtp/email",
}
# Second way to connect if the first one fails before login (blocked port, cut connection)
FALLBACK = {
    "gmail": ("smtp.gmail.com", 465, "ssl"),
    "zoho": ("smtp.zoho.com", 587, "starttls"),
    "zoho-in": ("smtp.zoho.in", 587, "starttls"),
    "zoho-eu": ("smtp.zoho.eu", 587, "starttls"),
}
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
    if provider == "resend" and not address:
        address = "onboarding@resend.dev"  # Resend's test sender: works without your own domain
    if not provider or not address:
        raise EmailError(
            "E-mail is not set up. Copy config/email.env.example to config/email.env, fill it in, "
            "then run: bash scripts/04_toolbox.sh"
        )
    if provider in API_PROVIDERS:
        host, port, security = API_PROVIDERS[provider], 443, "https"
        if not os.environ.get("EMAIL_API_KEY", "").strip():
            raise EmailError(f"EMAIL_API_KEY is missing in config/email.env (your {provider} API key).")
        if not os.environ.get("EMAIL_ALLOWED_TO", "").strip():
            raise EmailError("Set EMAIL_ALLOWED_TO in config/email.env to your own inbox (where mail may go).")
    elif provider == "custom":
        host = os.environ.get("EMAIL_SMTP_HOST", "")
        port = int(os.environ.get("EMAIL_SMTP_PORT", "587"))
        security = os.environ.get("EMAIL_SMTP_SECURITY", "starttls").lower()
    elif provider in PROVIDERS:
        host, port, security = PROVIDERS[provider]
    else:
        raise EmailError(
            f"Unknown EMAIL_PROVIDER '{provider}'. Use one of: {', '.join([*PROVIDERS, *API_PROVIDERS])}, custom."
        )
    password = os.environ.get("EMAIL_APP_PASSWORD", "")
    if provider == "gmail":
        password = password.replace(" ", "")  # Google shows app passwords in groups of 4
    allowed = os.environ.get("EMAIL_ALLOWED_TO", "") or address
    return {
        "provider": provider,
        "address": address,
        "password": password,
        "api_key": os.environ.get("EMAIL_API_KEY", "").strip(),
        "host": host,
        "port": port,
        "security": security,
        "from_name": os.environ.get("EMAIL_FROM_NAME", "Inglenook"),
        "allowed": {a.strip().lower() for a in allowed.split(",") if a.strip()},
        "allowed_list": [a.strip() for a in allowed.split(",") if a.strip()],  # in the order you wrote them
        "max_per_day": int(os.environ.get("EMAIL_MAX_PER_DAY", "20")),
    }


def _default_state() -> str:
    """Today's e-mail count. Installs from before the Inglenook name keep their old file."""
    old = Path.home() / ".local/state/local-ai-agent/email-count.json"
    return str(old if old.exists() else Path.home() / ".local/state/inglenook/email-count.json")


def _count_file() -> Path:
    return Path(os.environ.get("EMAIL_STATE", _default_state()))


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


def _send_api(cfg: dict, recipients: list[str], subject: str, body_markdown: str, attachments: list[Path]) -> None:
    """Send through Resend or Brevo's web API (HTTPS)."""
    import base64

    import httpx

    files = [(a.name, base64.b64encode(a.read_bytes()).decode()) for a in attachments]
    html = markdown_to_html(body_markdown)
    if cfg["provider"] == "resend":
        headers = {"Authorization": f"Bearer {cfg['api_key']}"}
        body = {
            "from": formataddr((cfg["from_name"], cfg["address"])),
            "to": recipients,
            "subject": subject,
            "html": html,
            "text": body_markdown,
            "attachments": [{"filename": n, "content": c} for n, c in files],
        }
    else:  # brevo
        headers = {"api-key": cfg["api_key"], "accept": "application/json"}
        body = {
            "sender": {"name": cfg["from_name"], "email": cfg["address"]},
            "to": [{"email": r} for r in recipients],
            "subject": subject,
            "htmlContent": html,
        }
        if files:
            body["attachment"] = [{"name": n, "content": c} for n, c in files]
    try:
        r = httpx.post(API_PROVIDERS[cfg["provider"]], json=body, headers=headers, timeout=60)
    except httpx.HTTPError as e:
        raise EmailError(f"Could not reach {cfg['provider']} ({type(e).__name__}: {e}).") from None
    if r.status_code in (200, 201, 202):
        return
    try:
        detail = r.json().get("message") or r.text
    except ValueError:
        detail = r.text
    detail = str(detail)[:300]
    if r.status_code in (401, 403):
        hint = f"Check EMAIL_API_KEY (the {cfg['provider']} API key) in config/email.env."
        if cfg["provider"] == "resend" and "own email" in detail:
            hint = (
                "Without your own domain, Resend only sends to the e-mail address you signed up with: "
                "put that address in EMAIL_ALLOWED_TO."
            )
        if cfg["provider"] == "brevo" and "sender" in detail.lower():
            hint = "EMAIL_ADDRESS must be a sender you verified in Brevo (Senders, Domains & Dedicated IPs)."
        raise EmailError(f"{cfg['provider']} refused it ({r.status_code}): {detail}. {hint}")
    raise EmailError(f"{cfg['provider']} could not send it ({r.status_code}): {detail}")


def _deliver(host: str, port: int, security: str, cfg: dict, msg: EmailMessage) -> None:
    context = ssl.create_default_context()
    if security == "ssl":
        server = smtplib.SMTP_SSL(host, port, context=context, timeout=60)
    else:
        server = smtplib.SMTP(host, port, timeout=60)
    try:
        if security != "ssl":
            server.starttls(context=context)
        server.login(cfg["address"], cfg["password"])
        server.send_message(msg)
    except BaseException:
        server.close()
        raise
    try:  # the message is accepted: a problem while saying goodbye must not cause a second send
        server.quit()
    except (smtplib.SMTPException, OSError):
        server.close()


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

    if cfg["provider"] in API_PROVIDERS:
        _send_api(cfg, recipients, subject[:200], body_markdown, [Path(a) for a in attachments])
        _count_one()
        names = f" with {len(attachments)} attachment(s)" if attachments else ""
        return f"E-mail '{subject[:80]}' sent to {', '.join(recipients)}{names} (via {cfg['provider']})."

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

    attempts = [(cfg["host"], cfg["port"], cfg["security"])]
    if cfg["provider"] in FALLBACK:  # e.g. Gmail on port 465 when port 587 is blocked or cut off
        attempts.append(FALLBACK[cfg["provider"]])
    problems = []
    for host, port, security in attempts:
        try:
            _deliver(host, port, security, cfg, msg)
            break
        except smtplib.SMTPAuthenticationError as e:
            raise EmailError(
                f"The mail server refused the login ({e.smtp_code}). {AUTH_HELP.get(cfg['provider'], '')}".strip()
            ) from None
        except (smtplib.SMTPException, OSError) as e:
            problems.append(f"{host}:{port} ({security}): {type(e).__name__}: {e}")
    else:
        raise EmailError(
            "Could not send. Tried " + "; ".join(problems) + ". Check the internet connection; some networks "
            "block mail ports 587/465 (try another network, or ask your provider)."
        )
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
    return send([to], "Test from Inglenook, your private AI assistant", body)


if __name__ == "__main__":
    import sys

    try:
        print(send_test())
    except EmailError as e:
        print(f"E-mail not sent: {e}")
        sys.exit(1)
