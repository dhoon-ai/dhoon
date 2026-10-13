#!/usr/bin/env python3
"""Create (or update) the "Assistant" agent in LibreChat, so nobody has to click through
the Agent Builder.

It signs in with your LibreChat account (creating it if it doesn't exist yet and you
agree), then creates the agent with: the main Instruct model, every toolbox tool,
File Search, the agent instructions from config/agent-instructions.md and a few
example conversation starters. Running it again updates the existing agent.

Usage (normally via scripts/06_create_agent.sh):
    python3 scripts/create_agent.py --model qwen3:30b-a3b-instruct-2507-q4_K_M
Credentials: --email/--password, or LIBRECHAT_EMAIL/LIBRECHAT_PASSWORD, or asked.
Only Python's standard library is used.
"""

import argparse
import getpass
import json
import os
import re
import sys
import urllib.error
import urllib.request
import uuid
from http.cookiejar import CookieJar
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
AGENT_NAME = "Assistant"
AVATAR = KIT / "assets" / "assistant-avatar.png"
QUICK_AVATAR = KIT / "assets" / "quick-assistant-avatar.png"  # amber, with a lightning bolt
# The custom endpoint is named "Ollama" in librechat.yaml, but LibreChat normalises that name
# to lowercase "ollama" for its model lists, so agents must use the lowercase id. Using
# "Ollama" makes chats fail with "Models for Ollama could not be loaded".
PROVIDER = "ollama"
MCP_SERVER = "toolbox"
MCP_DELIMITER = "_mcp_"  # LibreChat names MCP tools "<tool>_mcp_<server>"
# LibreChat rejects requests whose User-Agent isn't a recognised browser (and counts each one
# as a violation that can lead to a 2-hour ban), so identify as a browser.
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0 local-ai-agent-setup"
STARTERS = [
    "What's the weather today, and what's on my calendar this week?",
    "Search the web for the latest Ubuntu LTS release and summarise what's new, with sources.",
    "Research how to do this first, then plan it: compare three open-source backup tools for Linux, score them "
    "with run_python and save a comparison to reports/backup-plan.md.",
    "Analyse AAPL: 6-month price summary, indicators and fundamentals.",
]


PLUGINS = Path(os.environ.get("PLUGINS_DIR", KIT / "plugins"))  # private, git-ignored add-ons


def toolbox_tools() -> list[str]:
    """Names of all @mcp.tool() functions in the toolbox and in private plugins."""
    sources = [
        KIT / "toolbox" / "toolbox_server.py",
        *sorted((KIT / "toolbox" / "toolset").glob("*.py")),
        *sorted(PLUGINS.glob("*/tools.py")),
    ]
    return [t for f in sources for t in re.findall(r"@(?:tb\.)?mcp\.tool\(\)\s*\ndef (\w+)\(", f.read_text())]


def plugin_extras(name: str) -> list[str]:
    """Text from plugins/*/<name> (instructions.md, starters.txt)."""
    return [f.read_text().strip() for f in sorted(PLUGINS.glob(f"*/{name}")) if f.read_text().strip()]


# A second, lighter agent for everyday questions: the same model, but only these tools and
# short instructions, so it has far less to read before answering (faster on a CPU) and
# fewer tools to choose from.
QUICK_NAME = "Quick Assistant"
QUICK_TOOLS = [
    "current_datetime", "weather", "calendar_events", "calendar_add_event", "calendar_delete_event",
    "schedule_task", "scheduled_tasks", "cancel_scheduled_task", "web_search", "send_email",
]  # fmt: skip
QUICK_STARTERS = [
    "What's the weather today, and what's on my calendar?",
    "Every weekday at 7am, e-mail me the weather and my calendar for the day.",
    "Remind me on Friday at 5pm to pay the credit card.",
]


def quick_payload(model: str) -> dict:
    available = set(toolbox_tools())
    return {
        "name": QUICK_NAME,
        "description": "Fast everyday helper: weather, calendar, reminders and scheduled tasks, quick web answers.",
        "instructions": (KIT / "config" / "quick-instructions.md").read_text().strip(),
        "provider": PROVIDER,
        "model": model,
        "tools": [f"{t}{MCP_DELIMITER}{MCP_SERVER}" for t in QUICK_TOOLS if t in available]
        + [f"sys__server__sys{MCP_DELIMITER}{MCP_SERVER}"],
        "model_parameters": {"max_tokens": 2048},
        "recursion_limit": 20,
        "conversation_starters": QUICK_STARTERS,
    }


def agent_payload(model: str) -> dict:
    tools = [f"{t}{MCP_DELIMITER}{MCP_SERVER}" for t in toolbox_tools()]
    tools += [f"sys__server__sys{MCP_DELIMITER}{MCP_SERVER}", "file_search"]
    return {
        "name": AGENT_NAME,
        "description": "Private general-purpose agent: web search, files, Python, documents, stocks.",
        "instructions": "\n".join(
            [(KIT / "config" / "agent-instructions.md").read_text().strip(), *plugin_extras("instructions.md")]
        ),
        "provider": PROVIDER,
        "model": model,
        "tools": tools,
        "model_parameters": {"max_tokens": 4096},
        "recursion_limit": 50,  # max steps per run (librechat.yaml allows up to 100)
        "conversation_starters": (
            STARTERS + [line for text in plugin_extras("starters.txt") for line in text.splitlines() if line.strip()]
        )[:6],
    }


class LibreChat:
    def __init__(self, url: str):
        self.url = url.rstrip("/")
        self.token = None
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        req = urllib.request.Request(
            self.url + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT},
        )
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with self.opener.open(req, timeout=60) as r:
                return r.status, self._parse(r.read())
        except urllib.error.HTTPError as e:
            return e.code, self._parse(e.read())
        except (urllib.error.URLError, OSError) as e:
            return 0, {"message": str(e)}

    @staticmethod
    def _parse(raw: bytes) -> dict:
        """JSON body, or an error dict for anything else (e.g. LibreChat's 'event: error' replies)."""
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {"data": data}
        except ValueError:
            text = raw.decode(errors="replace")
            m = re.search(r"^data: (.*)$", text, re.M)
            return {"error": True, "message": (m.group(1) if m else text)[:300]}

    def upload_avatar(self, agent_id: str, image: Path) -> tuple[int, dict]:
        """multipart/form-data upload of the agent's picture (field name 'file')."""
        boundary = uuid.uuid4().hex
        body = (
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{image.name}"\r\n'
                "Content-Type: image/png\r\n\r\n"
            ).encode()
            + image.read_bytes()
            + f"\r\n--{boundary}--\r\n".encode()
        )
        req = urllib.request.Request(
            f"{self.url}/api/files/images/agents/{agent_id}/avatar",
            method="POST",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
                "Authorization": f"Bearer {self.token}",
            },
        )
        try:
            with self.opener.open(req, timeout=60) as r:
                return r.status, self._parse(r.read())
        except urllib.error.HTTPError as e:
            return e.code, self._parse(e.read())
        except (urllib.error.URLError, OSError) as e:
            return 0, {"message": str(e)}

    def login(self, email: str, password: str) -> bool:
        status, data = self.call("POST", "/api/auth/login", {"email": email, "password": password})
        if status == 200 and data.get("token"):
            self.token = data["token"]
            return True
        if data.get("twoFAPending"):
            sys.exit("This account uses two-factor login; create the agent in the Agent Builder instead.")
        return False

    def register(self, email: str, password: str) -> tuple[bool, str]:
        name = re.sub(r"[^A-Za-z0-9 ]", " ", email.split("@")[0]).strip().title() or "Admin"
        name = (name + " User")[:80] if len(name) < 3 else name[:80]
        status, data = self.call(
            "POST",
            "/api/auth/register",
            {"name": name, "email": email, "password": password, "confirm_password": password},
        )
        return status in (200, 201), str(data.get("message", data))[:300]


def ask(prompt: str, secret: bool = False) -> str:
    """Read from the terminal even when this runs inside `curl ... | bash`."""
    try:
        with open("/dev/tty") as tty:
            if secret:
                return getpass.getpass(prompt, stream=sys.stderr)
            sys.stderr.write(prompt)
            sys.stderr.flush()
            return tty.readline().strip()
    except OSError:
        sys.exit("No terminal available: set LIBRECHAT_EMAIL and LIBRECHAT_PASSWORD.")


def save_agent(lc: "LibreChat", payload: dict, agents: list[dict], model: str, avatar: Path = AVATAR) -> int:
    """Create or update one agent. Returns 1 if some tools were not accepted, else 0."""
    name = payload["name"]
    existing = [a for a in agents if a.get("name") == name]
    if existing:
        agent_id = existing[0]["id"]
        status, data = lc.call("PATCH", f"/api/agents/{agent_id}", payload)
        action = "Updated"
    else:
        status, data = lc.call("POST", "/api/agents", payload)
        agent_id = data.get("id")
        action = "Created"
    if status not in (200, 201) or not agent_id or data.get("error"):
        sys.exit(f"Could not save the agent '{name}' (status {status}): {data}")

    if avatar.exists() and not data.get("avatar"):
        a_status, a_data = lc.upload_avatar(agent_id, avatar)
        if a_status in (200, 201) and not a_data.get("error"):
            print(f"  Added the picture to '{name}'.")
        else:
            print(f"  (could not add the picture: {a_data.get('message', a_status)} - not important)")

    saved = set(data.get("tools") or [])
    missing = [t for t in payload["tools"] if t.endswith(f"{MCP_DELIMITER}{MCP_SERVER}") and t not in saved]
    missing = [t for t in missing if not t.startswith("sys__")]
    print(f"{action} the '{name}' agent ({agent_id}) with model {model}.")
    print(f"  Tools: {len(saved)} saved" + (f", including File Search: {'yes' if 'file_search' in saved else 'no'}"
                                             if "file_search" in payload["tools"] else ""))  # fmt: skip
    if missing:
        print(f"  WARNING: {len(missing)} toolbox tools were not accepted: {', '.join(missing)}")
        print("  Is the toolbox running? Check: bash scripts/check.sh, then re-run this script.")
        return 1
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=os.environ.get("LIBRECHAT_URL", "http://localhost:3080"))
    p.add_argument("--model", required=True, help="main Instruct model, e.g. qwen3:30b-a3b-instruct-2507-q4_K_M")
    p.add_argument("--email", default=os.environ.get("LIBRECHAT_EMAIL"))
    p.add_argument("--password", default=os.environ.get("LIBRECHAT_PASSWORD"))
    p.add_argument("--yes", action="store_true", help="create the account without asking if it doesn't exist")
    args = p.parse_args()

    lc = LibreChat(args.url)
    status, _ = lc.call("GET", "/api/config")
    if status != 200:
        sys.exit(f"LibreChat is not reachable at {args.url} (status {status}). Is it running? Try scripts/check.sh")

    print("Sign in with your LibreChat account (it is created if it doesn't exist yet).")
    email = args.email or ask("  Email: ")
    password = args.password or ask("  Password (min. 8 characters): ", secret=True)

    if not lc.login(email, password):
        create = args.yes or ask(f"  No account found for {email} (or wrong password). Create it? [y/N] ").lower() in (
            "y",
            "yes",
        )
        if not create:
            sys.exit("Stopped. Check the email/password, or sign up at " + args.url)
        ok, msg = lc.register(email, password)
        if not ok:
            sys.exit(
                f"Could not create the account: {msg}\n(Wrong password for an existing account? Or sign-up disabled?)"
            )
        if not lc.login(email, password):
            sys.exit("Account created but signing in failed. It may need email verification; sign in at " + args.url)
        print(f"  Created account {email} (the first account on this server is the admin).")
    else:
        print(f"  Signed in as {email}.")

    status, data = lc.call("GET", "/api/agents?limit=100")
    if status != 200 or data.get("error"):
        print(f"  (could not list existing agents: {data.get('message', status)} - creating new ones)")
        data = {}
    agents = data.get("data") or []
    problems = 0
    for payload, avatar in ((agent_payload(args.model), AVATAR), (quick_payload(args.model), QUICK_AVATAR)):
        problems += save_agent(lc, payload, agents, args.model, avatar)
    if problems:
        return 1
    marker = Path(os.environ.get("RUNTIME_DIR", KIT / "runtime")) / ".agent-created"  # read by status.sh
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(args.model)
    print(f"Open {args.url}, choose Agents -> {AGENT_NAME} (everything) or {QUICK_NAME} (faster, everyday).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
