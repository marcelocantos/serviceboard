"""Local HTTP dashboard. No third-party Python dependencies are required."""

import argparse
import ipaddress
import json
import plistlib
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


STATUSES = frozenset(
    {"STOPPED", "STARTING", "RUNNING", "BACKOFF", "STOPPING", "EXITED", "FATAL", "UNKNOWN"}
)
MAX_LOG_BYTES = 32_000
STATIC = Path(__file__).parent / "static"
LOGO_CACHE_CONTROL = "public, max-age=86400"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/assets/supervisor.png": ("assets/supervisor.png", "image/png"),
    "/assets/homebrew.svg": ("assets/homebrew.svg", "image/svg+xml"),
}


class ServiceError(Exception):
    pass


def run(command, acceptable=(0,)):
    try:
        result = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ServiceError(str(exc)) from exc
    if result.returncode not in acceptable:
        raise ServiceError((result.stderr or result.stdout).strip() or f"Command exited {result.returncode}")
    return result.stdout


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ServiceError("Dashboard manifest redirected")


class ServiceBoard:
    def __init__(self, supervisor_config, registration_file, supervisorctl=None, brew=None):
        self.supervisor_config = str(supervisor_config)
        self.supervisorctl = supervisorctl or shutil.which("supervisorctl") or (
            "/opt/homebrew/bin/supervisorctl" if Path("/opt/homebrew/bin/supervisorctl").exists() else None
        )
        self.brew = brew or shutil.which("brew") or (
            "/opt/homebrew/bin/brew" if Path("/opt/homebrew/bin/brew").exists() else None
        )
        self.registration_file = Path(registration_file)
        self.token = secrets.token_urlsafe(32)
        self._brew_lock = threading.Lock()
        self._brew_cache = None
        self._brew_cache_at = 0.0

    def _supervisor(self, *args):
        if not self.supervisorctl:
            raise ServiceError("supervisorctl is unavailable")
        return run([self.supervisorctl, "-c", self.supervisor_config, *args], (0, 3) if args == ("status",) else (0,))

    def _brew(self, *args):
        if not self.brew:
            raise ServiceError("brew is unavailable")
        return run([self.brew, *args])

    def _brew_entries(self):
        with self._brew_lock:
            if self._brew_cache is not None and time.monotonic() - self._brew_cache_at < 10:
                return self._brew_cache
            try:
                entries = json.loads(self._brew("services", "list", "--json"))
            except ValueError as exc:
                raise ServiceError(f"Invalid Homebrew response: {exc}") from exc
            if not isinstance(entries, list):
                raise ServiceError("Unexpected Homebrew service list")
            self._brew_cache = entries
            self._brew_cache_at = time.monotonic()
            return entries

    def _supervisor_services(self):
        services = []
        for line in self._supervisor("status").splitlines():
            match = re.match(r"^(\S+)\s+([A-Z]+)(?:\s+(.*))?$", line)
            if not match or match.group(2) not in STATUSES:
                raise ServiceError(f"Unrecognized Supervisor status: {line}")
            name, status, detail = match.groups()
            services.append({"id": f"supervisor:{name}", "name": name, "source": "Supervisor", "status": status.lower(), "detail": detail or ""})
        return services

    def _homebrew_services(self):
        services = []
        for entry in self._brew_entries():
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                raise ServiceError("Unexpected Homebrew service entry")
            services.append({"id": f"homebrew:{entry['name']}", "name": entry["name"], "source": "Homebrew", "status": str(entry.get("status") or "none").lower(), "detail": str(entry.get("user") or "")})
        return services

    def services(self):
        services = []
        errors = {}
        for source, read in (("Supervisor", self._supervisor_services), ("Homebrew", self._homebrew_services)):
            try:
                services.extend(read())
            except ServiceError as exc:
                errors[source] = str(exc)
        return {"services": services, "errors": errors}

    def find(self, service_id):
        if service_id.startswith("supervisor:"):
            services = self._supervisor_services()
        elif service_id.startswith("homebrew:"):
            services = self._homebrew_services()
        else:
            raise ServiceError("Unknown service source")
        for service in services:
            if service["id"] == service_id:
                return service
        raise ServiceError("Service is not in the current inventory")

    def action(self, service_id, action):
        if action not in {"start", "stop", "restart"}:
            raise ServiceError("Unsupported action")
        service = self.find(service_id)
        if service["source"] == "Supervisor":
            return self._supervisor(action, service["name"])
        output = self._brew("services", action, service["name"])
        with self._brew_lock:
            self._brew_cache = None
        return output

    def log(self, service_id, stream):
        if stream not in {"stdout", "stderr"}:
            raise ServiceError("Unsupported log stream")
        service = self.find(service_id)
        if service["source"] == "Supervisor":
            try:
                return self._supervisor("tail", f"-{MAX_LOG_BYTES}", service["name"], stream)
            except ServiceError as exc:
                if stream == "stderr" and "redirect_stderr" in str(exc):
                    return "stderr is redirected into stdout for this service."
                if "NO_FILE" in str(exc) or "no log file" in str(exc).lower():
                    return "No log file is configured for this stream."
                raise
        entries = self._brew_entries()
        entry = next((item for item in entries if item.get("name") == service["name"]), None)
        if not entry or not entry.get("file"):
            return "No Homebrew service file is available."
        try:
            with open(entry["file"], "rb") as file:
                plist = plistlib.load(file)
            key = "StandardOutPath" if stream == "stdout" else "StandardErrorPath"
            path = plist.get(key)
            if not isinstance(path, str) or not path:
                return f"This Homebrew service does not declare a {stream} log path."
            with open(path, "rb") as file:
                file.seek(0, 2)
                file.seek(max(0, file.tell() - MAX_LOG_BYTES))
                return file.read().decode("utf-8", "replace")
        except (OSError, ValueError, TypeError) as exc:
            raise ServiceError(f"Unable to read {stream}: {exc}") from exc

    def dashboard(self, service_id):
        self.find(service_id)
        if not self.registration_file.exists():
            return None
        try:
            registrations = json.loads(self.registration_file.read_text())
            base = registrations.get("dashboards", {}).get(service_id)
        except (OSError, ValueError, AttributeError) as exc:
            raise ServiceError(f"Invalid dashboard registrations: {exc}") from exc
        if base is None:
            return None
        if not isinstance(base, str):
            raise ServiceError("Dashboard base URL must be a string")
        try:
            parsed = urlparse(base)
            parsed.port
        except ValueError as exc:
            raise ServiceError(f"Invalid dashboard base URL: {exc}") from exc
        try:
            is_loopback = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname).is_loopback
        except (ValueError, TypeError):
            is_loopback = False
        if parsed.scheme != "http" or not is_loopback or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ServiceError("Dashboard base URL must be a loopback HTTP origin")
        manifest_url = urljoin(base.rstrip("/") + "/", ".well-known/serviceboard.json")
        try:
            opener = build_opener(ProxyHandler({}), NoRedirect())
            with opener.open(Request(manifest_url, headers={"Accept": "application/json"}), timeout=2) as response:
                data = response.read(32_769)
            if len(data) > 32_768:
                raise ServiceError("Dashboard manifest is too large")
            manifest = json.loads(data)
        except (OSError, ValueError) as exc:
            raise ServiceError(f"Unable to load dashboard manifest: {exc}") from exc
        if not isinstance(manifest, dict) or manifest.get("version") != 1 or not isinstance(manifest.get("views"), list):
            raise ServiceError("Invalid dashboard manifest")
        views = []
        for view in manifest["views"][:8]:
            if not isinstance(view, dict):
                raise ServiceError("Invalid dashboard view")
            title, path = view.get("title"), view.get("path")
            if not isinstance(title, str) or not title or len(title) > 80 or not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
                raise ServiceError("Invalid dashboard view title or path")
            url = urljoin(base, path)
            if urlparse(url).netloc != parsed.netloc:
                raise ServiceError("Dashboard view must stay on its registered origin")
            views.append({"title": title, "url": url})
        return {"title": str(manifest.get("title") or service_id)[:80], "views": views}


class Handler(BaseHTTPRequestHandler):
    board: ServiceBoard

    def _headers(self, status, content_type, length):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", LOGO_CACHE_CONTROL if content_type.startswith("image/") else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; frame-src http://localhost:* http://127.0.0.1:* http://[::1]:*; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()

    def _json(self, status, value):
        body = json.dumps(value).encode()
        self._headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def _allowed_host(self):
        host = self.headers.get("Host", "")
        return host in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

    def do_GET(self):
        if not self._allowed_host():
            self._json(HTTPStatus.FORBIDDEN, {"error": "Local host required"})
            return
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/session":
                self._json(HTTPStatus.OK, {"token": self.board.token})
            elif parsed.path == "/api/services":
                self._json(HTTPStatus.OK, self.board.services())
            elif parsed.path == "/api/log":
                query = parse_qs(parsed.query)
                self._json(HTTPStatus.OK, {"text": self.board.log(query.get("id", [""])[0], query.get("stream", [""])[0])})
            elif parsed.path == "/api/dashboard":
                query = parse_qs(parsed.query)
                self._json(HTTPStatus.OK, {"dashboard": self.board.dashboard(query.get("id", [""])[0])})
            elif parsed.path in STATIC_FILES:
                filename, mime = STATIC_FILES[parsed.path]
                body = (STATIC / filename).read_bytes()
                self._headers(HTTPStatus.OK, mime, len(body))
                self.wfile.write(body)
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
        except ServiceError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def do_POST(self):
        if not self._allowed_host() or self.headers.get("Origin") not in {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"} or self.headers.get("X-Serviceboard-Token") != self.board.token:
            self._json(HTTPStatus.FORBIDDEN, {"error": "Local session required"})
            return
        if self.path != "/api/action" or self.headers.get("Content-Type") != "application/json":
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid request"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024:
                raise ValueError("Invalid body length")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict) or not isinstance(payload.get("id"), str):
                raise ValueError("Invalid action payload")
            output = self.board.action(payload["id"], payload.get("action"))
            self._json(HTTPStatus.OK, {"output": output.strip()})
        except (ValueError, ServiceError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})


def main():
    parser = argparse.ArgumentParser(description="Local Supervisor and Homebrew dashboard")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--supervisor-config", default="/opt/homebrew/etc/supervisord.conf")
    parser.add_argument("--registrations", default=str(Path.home() / ".config/serviceboard/dashboards.json"))
    args = parser.parse_args()
    board = ServiceBoard(args.supervisor_config, args.registrations)
    Handler.board = board
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Serviceboard: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
