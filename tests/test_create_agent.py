"""Tests for scripts/create_agent.py against a fake LibreChat API."""

import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import create_agent  # noqa: E402


class FakeLibreChat:
    def __init__(self, accounts=None, drop_tools=()):
        self.accounts = dict(accounts or {})
        self.agents = {}
        self.calls = []
        self.drop_tools = set(drop_tools)
        self.violations = 0
        self.avatar_uploads = 0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, status, body):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n)) if n else {}

            def browser_ok(self):
                # Like LibreChat's uaParser: non-browser User-Agents get an SSE error with status 200
                if "Firefox" in (self.headers.get("User-Agent") or ""):
                    return True
                fake.violations += 1
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'event: error\ndata: "Illegal request"\n\n')
                return False

            def authed(self):
                return self.headers.get("Authorization") == "Bearer tok-123"

            def do_GET(self):
                fake.calls.append(("GET", self.path))
                if self.path == "/api/config":
                    return self.reply(200, {})
                if self.path.startswith("/api/agents") and self.browser_ok() and self.authed():
                    return self.reply(200, {"data": list(fake.agents.values())})
                self.reply(401, {"message": "Unauthorized"})

            def do_POST(self):
                if self.path.startswith("/api/files/images/agents/"):
                    return self.avatar_upload()
                data = self.body()
                fake.calls.append(("POST", self.path, data))
                if self.path == "/api/auth/login":
                    if fake.accounts.get(data["email"]) == data["password"]:
                        return self.reply(200, {"token": "tok-123", "user": {}})
                    return self.reply(401, {"message": "Invalid credentials"})
                if self.path == "/api/auth/register":
                    assert data["password"] == data["confirm_password"] and len(data["name"]) >= 3
                    fake.accounts[data["email"]] = data["password"]
                    return self.reply(200, {"message": "ok"})
                if self.path == "/api/agents" and self.browser_ok() and self.authed():
                    agent = {**data, "id": f"agent_{len(fake.agents) + 1}"}
                    agent["tools"] = [t for t in data["tools"] if t not in fake.drop_tools]
                    fake.agents[agent["id"]] = agent
                    return self.reply(201, agent)
                self.reply(401, {"message": "Unauthorized"})

            def avatar_upload(self):
                agent_id = self.path.split("/")[-2]
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n)
                ctype = self.headers.get("Content-Type", "")
                ok = (
                    self.browser_ok()
                    and self.authed()
                    and ctype.startswith("multipart/form-data; boundary=")
                    and b'name="file"' in raw
                    and b"\x89PNG" in raw
                    and agent_id in fake.agents
                )
                if not ok:
                    return self.reply(400, {"message": "bad upload"})
                fake.avatar_uploads += 1
                fake.agents[agent_id]["avatar"] = {"filepath": "/images/a.png", "source": "local"}
                return self.reply(201, fake.agents[agent_id])

            def do_PATCH(self):
                data = self.body()
                fake.calls.append(("PATCH", self.path, data))
                agent_id = self.path.rsplit("/", 1)[-1]
                if self.browser_ok() and self.authed() and agent_id in fake.agents:
                    fake.agents[agent_id].update(data)
                    return self.reply(200, fake.agents[agent_id])
                self.reply(404, {"error": "Agent not found"})

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        import tempfile

        self.runtime = tempfile.mkdtemp()


def run(fake, *extra, email="me@example.com", password="secret123"):
    env = {**os.environ, "LIBRECHAT_EMAIL": email, "LIBRECHAT_PASSWORD": password, "RUNTIME_DIR": fake.runtime}
    return subprocess.run(
        [sys.executable, str(REPO / "scripts/create_agent.py"), "--url", fake.url, "--model", "m:instruct", *extra],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_payload_has_every_toolbox_tool_file_search_and_instructions():
    payload = create_agent.agent_payload("m:instruct")
    src = (REPO / "toolbox/toolbox_server.py").read_text()
    tools = re.findall(r"@mcp\.tool\(\)\s*\ndef (\w+)\(", src)
    assert len(tools) >= 13 and "ask_planner" in tools and "search_document" in tools
    for t in tools:
        assert f"{t}_mcp_toolbox" in payload["tools"]
    assert "file_search" in payload["tools"]
    assert payload["provider"] == "Ollama" and payload["model"] == "m:instruct"
    assert "ask_planner" in payload["instructions"]


def test_existing_account_creates_agent_then_updates_on_rerun():
    fake = FakeLibreChat(accounts={"me@example.com": "secret123"})
    r = run(fake)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Created the 'Assistant' agent" in r.stdout and len(fake.agents) == 1
    assert Path(fake.runtime, ".agent-created").read_text() == "m:instruct"
    r = run(fake)
    assert r.returncode == 0 and "Updated the 'Assistant' agent" in r.stdout
    assert len(fake.agents) == 1  # no duplicate
    assert fake.violations == 0  # always looked like a browser (LibreChat bans non-browsers)
    assert fake.avatar_uploads == 1  # picture added once, not again on the re-run
    assert "Added the Assistant's picture" in r.stdout or fake.agents["agent_1"]["avatar"]


def test_missing_account_is_created_with_yes():
    fake = FakeLibreChat()
    r = run(fake, "--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Created account me@example.com" in r.stdout and fake.accounts["me@example.com"] == "secret123"
    assert len(fake.agents) == 1


def test_warns_when_toolbox_tools_not_accepted():
    fake = FakeLibreChat(accounts={"me@example.com": "secret123"}, drop_tools={"ask_planner_mcp_toolbox"})
    r = run(fake)
    assert r.returncode == 1 and "ask_planner_mcp_toolbox" in r.stdout


def test_librechat_down():
    r = subprocess.run(
        [sys.executable, str(REPO / "scripts/create_agent.py"), "--url", "http://127.0.0.1:9", "--model", "m"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode != 0 and "not reachable" in r.stderr


@pytest.fixture(autouse=True)
def _no_tty(monkeypatch):
    monkeypatch.delenv("LIBRECHAT_EMAIL", raising=False)


def test_parse_handles_librechat_sse_error():
    parsed = create_agent.LibreChat._parse(b'event: error\ndata: "Illegal request"\n\n')
    assert parsed["error"] is True and "Illegal request" in parsed["message"]
