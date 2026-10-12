"""send_email: provider presets, recipient allow-list, attachments, limits, friendly errors."""

import asyncio
import importlib
import smtplib
import sys
from email import message_from_bytes
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "toolbox"))


class FakeSMTP:
    sent = []
    fail_login = False

    def __init__(self, host, port, context=None, timeout=None):
        self.host, self.port, self.tls = host, port, False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        self.tls = True

    def login(self, user, password):
        if FakeSMTP.fail_login:
            raise smtplib.SMTPAuthenticationError(535, b"5.7.30 Basic authentication is not supported")
        self.user, self.password = user, password

    def send_message(self, msg):
        FakeSMTP.sent.append((self, msg))


@pytest.fixture()
def tb(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("EMAIL_STATE", str(tmp_path / "count.json"))
    monkeypatch.setenv("EMAIL_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("EMAIL_PROVIDER", "gmail")
    monkeypatch.setenv("EMAIL_ADDRESS", "me@gmail.com")
    monkeypatch.setenv("EMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    monkeypatch.setenv("EMAIL_ALLOWED_TO", "me@gmail.com, Me@Outlook.com")
    FakeSMTP.sent, FakeSMTP.fail_login = [], False
    import emailer
    import toolbox_server

    emailer = importlib.reload(emailer)
    monkeypatch.setattr(emailer.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(emailer.smtplib, "SMTP_SSL", FakeSMTP)
    tb = importlib.reload(toolbox_server)
    tb.emailer = emailer
    return tb


def test_send_email_with_markdown_and_attachment(tb):
    (tb.WORKSPACE / "reports").mkdir(parents=True)
    (tb.WORKSPACE / "reports/plan.md").write_text("# Plan\n")
    out = tb.send_email("me@outlook.com", "Weekly plan", "| a | b |\n|---|---|\n| 1 | 2 |", "reports/plan.md")
    assert "sent to me@outlook.com with 1 attachment" in out
    server, msg = FakeSMTP.sent[0]
    assert (server.host, server.port, server.tls) == ("smtp.gmail.com", 587, True)
    assert server.password == "abcdefghijklmnop"  # Gmail app password without spaces
    raw = message_from_bytes(msg.as_bytes())
    parts = [p.get_content_type() for p in raw.walk()]
    assert "text/plain" in parts and "text/html" in parts and "text/markdown" in parts
    html = next(p for p in raw.walk() if p.get_content_type() == "text/html").get_payload(decode=True).decode()
    assert "<table>" in html


def test_only_allowed_recipients_and_workspace_files(tb):
    out = tb.send_email("attacker@evil.example", "x", "secrets")
    assert out.startswith("E-mail not sent: Not allowed to send to attacker@evil.example") and not FakeSMTP.sent
    out = tb.send_email("me@gmail.com", "x", "y", "../../etc/passwd")
    assert "outside the workspace" in out or "not found" in out
    assert not FakeSMTP.sent


def test_daily_limit(tb, monkeypatch):
    monkeypatch.setenv("EMAIL_MAX_PER_DAY", "2")
    assert "sent" in tb.send_email("me@gmail.com", "1", "x")
    assert "sent" in tb.send_email("me@gmail.com", "2", "x")
    assert "Daily limit of 2" in tb.send_email("me@gmail.com", "3", "x")


def test_providers_and_friendly_errors(tb, monkeypatch):
    monkeypatch.setenv("EMAIL_PROVIDER", "zoho-in")
    tb.send_email("me@gmail.com", "x", "y")
    server, _ = FakeSMTP.sent[-1]
    assert (server.host, server.port, server.tls) == ("smtp.zoho.in", 465, False)  # SSL from the start
    monkeypatch.setenv("EMAIL_PROVIDER", "outlook")
    FakeSMTP.fail_login = True
    out = tb.send_email("me@gmail.com", "x", "y")
    assert "refused the login (535)" in out and "OAuth2" in out and "Gmail or Zoho" in out
    monkeypatch.setenv("EMAIL_PROVIDER", "")
    assert "E-mail is not set up" in tb.send_email("me@gmail.com", "x", "y")


def test_tool_is_registered_for_the_agent(tb):
    names = {t.name for t in asyncio.run(tb.mcp.list_tools())}
    assert "send_email" in names


def test_settings_are_read_from_the_env_file(tmp_path, monkeypatch):
    import emailer

    for k in ("EMAIL_PROVIDER", "EMAIL_ADDRESS", "EMAIL_APP_PASSWORD", "EMAIL_ALLOWED_TO"):
        monkeypatch.delenv(k, raising=False)
    env = tmp_path / "email.env"
    env.write_text('# comment\nEMAIL_PROVIDER=zoho\nEMAIL_ADDRESS=me@zoho.com\nEMAIL_APP_PASSWORD="pw 123"\n')
    monkeypatch.setenv("EMAIL_ENV_FILE", str(env))
    emailer = importlib.reload(emailer)
    s = emailer.settings()
    assert (s["provider"], s["host"], s["password"]) == ("zoho", "smtp.zoho.com", "pw 123")
    assert s["allowed"] == {"me@zoho.com"}
    for k in ("EMAIL_PROVIDER", "EMAIL_ADDRESS", "EMAIL_APP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
