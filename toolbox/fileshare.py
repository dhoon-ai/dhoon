"""Download links for files the Assistant makes (Excel, Word, charts, reports).

When a tool writes a file in the workspace, it adds a link such as
    http://192.168.1.20:3081/f/1792350000/Xa2c.../reports/budget.xlsx
that opens the file from any device at home. Each link is signed with a key that only
this computer has, works only for that one file in the agent's workspace, and expires
after FILES_LINK_DAYS days (7). Without a valid link the server answers "not found" to
everything, so nobody can browse or guess files. Never forward port 3081 on your router.

Runs as the ai-files service:  python3 toolbox/fileshare.py
Settings: FILES_PORT (3081), FILES_HOST (this computer's address on your network; found
automatically), FILES_LINK_DAYS (7), WORKSPACE.
"""

import base64
import hashlib
import hmac
import mimetypes
import os
import secrets
import socket
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
INLINE = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf"}  # shown in the browser; the rest is downloaded


def _workspace() -> Path:
    return Path(os.environ.get("WORKSPACE", str(Path.home() / "agent-workspace"))).resolve()


def _port() -> int:
    return int(os.environ.get("FILES_PORT", "3081"))


def _key() -> bytes:
    """The signing key, made once (runtime/fileshare.key, readable only by you)."""
    f = Path(os.environ.get("FILES_KEY", str(KIT / "runtime" / "fileshare.key")))
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(secrets.token_bytes(32))
        f.chmod(0o600)
    return f.read_bytes()


def _sign(rel: str, expires: int) -> str:
    mac = hmac.new(_key(), f"{rel}|{expires}".encode(), hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()


def host() -> str:
    """This computer's address on the home network (no traffic is sent to find it)."""
    if os.environ.get("FILES_HOST"):
        return os.environ["FILES_HOST"]
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "localhost"


def make_link(path: Path, workspace: Path | None = None) -> str:
    """A signed download link for a file inside the workspace."""
    ws = (workspace or _workspace()).resolve()
    rel = Path(path).resolve().relative_to(ws).as_posix()
    expires = int(time.time()) + int(os.environ.get("FILES_LINK_DAYS", "7")) * 86400
    return f"http://{host()}:{_port()}/f/{expires}/{_sign(rel, expires)}/{urllib.parse.quote(rel)}"


def resolve(url_path: str, workspace: Path | None = None) -> Path | None:
    """The file a link points to, or None if the link is wrong, expired or unsafe."""
    parts = urllib.parse.urlsplit(url_path).path.split("/", 4)  # ['', 'f', expires, sig, rel]
    if len(parts) != 5 or parts[1] != "f" or not parts[2].isdigit():
        return None
    expires, sig, rel = int(parts[2]), parts[3], urllib.parse.unquote(parts[4])
    if expires < time.time() or not hmac.compare_digest(sig, _sign(rel, expires)):
        return None
    ws = (workspace or _workspace()).resolve()
    p = (ws / rel).resolve()
    if not p.is_relative_to(ws) or not p.is_file():
        return None
    return p


class _Handler(BaseHTTPRequestHandler):
    server_version = "Dhoon-files"
    sys_version = ""  # don't announce the Python version

    def log_message(self, *args):  # no access log: file names can be private
        pass

    def _send(self, head_only: bool):
        p = resolve(self.path)
        if p is None:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            if not head_only:
                self.wfile.write(b"Not found")
            return
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        disposition = "inline" if p.suffix.lower() in INLINE else "attachment"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(p.stat().st_size))
        self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{urllib.parse.quote(p.name)}")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "sandbox; default-src 'none'")  # no scripts, ever
        self.send_header("Cache-Control", "private, no-store")
        self.end_headers()
        if not head_only:
            with p.open("rb") as f:
                while chunk := f.read(65536):
                    self.wfile.write(chunk)

    def do_GET(self):
        self._send(head_only=False)

    def do_HEAD(self):
        self._send(head_only=True)


def serve(port: int | None = None, bind: str = "0.0.0.0") -> ThreadingHTTPServer:
    return ThreadingHTTPServer((bind, port if port is not None else _port()), _Handler)


if __name__ == "__main__":
    _key()  # make the key before the first link is needed
    srv = serve()
    print(f"File links on http://{host()}:{srv.server_address[1]}/f/... (workspace {_workspace()})", flush=True)
    srv.serve_forever()
