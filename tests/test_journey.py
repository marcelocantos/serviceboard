"""Whole-product journey against an isolated real Supervisor daemon."""

import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from serviceboard.app import Handler, ServiceBoard


class ManifestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/.well-known/serviceboard.json":
            body = json.dumps({"version": 1, "title": "Journey service", "views": [{"title": "Overview", "path": "/dashboard"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Journey dashboard")

    def log_message(self, format, *args):
        pass


class Journey(unittest.TestCase):
    def test_real_supervisor_inventory_logs_controls_and_manifest(self):
        with tempfile.TemporaryDirectory(prefix="serviceboard-journey-") as directory:
            root = Path(directory)
            worker = root / "worker.py"
            worker.write_text("import sys, time\nprint('journey stdout', flush=True)\nprint('journey stderr', file=sys.stderr, flush=True)\nwhile True: time.sleep(1)\n")
            config = root / "supervisord.conf"
            config.write_text(f"""
[unix_http_server]
file={root}/supervisor.sock
chmod=0700
[supervisord]
nodaemon=true
pidfile={root}/supervisord.pid
logfile={root}/supervisord.log
childlogdir={root}
[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface
[supervisorctl]
serverurl=unix://{root}/supervisor.sock
[program:journey]
command={sys.executable} -u {worker}
autostart=true
autorestart=false
startsecs=0
stdout_logfile={root}/stdout.log
stderr_logfile={root}/stderr.log
""")
            daemon = subprocess.Popen(["supervisord", "-c", str(config)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            self.addCleanup(lambda: (daemon.terminate(), daemon.wait(timeout=5), daemon.stderr.close()) if daemon.poll() is None else daemon.stderr.close())
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                result = subprocess.run(["supervisorctl", "-c", str(config), "status"], capture_output=True, text=True)
                if result.returncode == 0 and "RUNNING" in result.stdout:
                    break
                time.sleep(0.1)
            else:
                log = (root / "supervisord.log").read_text() if (root / "supervisord.log").exists() else "no daemon log"
                stderr = daemon.stderr.read().decode() if daemon.poll() is not None else "daemon still running"
                self.fail(f"Isolated Supervisor did not start: {result.stdout} {result.stderr}; {stderr}; {log}")

            manifest_server = ThreadingHTTPServer(("127.0.0.1", 0), ManifestHandler)
            threading.Thread(target=manifest_server.serve_forever, daemon=True).start()
            self.addCleanup(lambda: (manifest_server.shutdown(), manifest_server.server_close()))
            registrations = root / "dashboards.json"
            registrations.write_text(json.dumps({"dashboards": {"supervisor:journey": f"http://127.0.0.1:{manifest_server.server_port}"}}))

            Handler.board = ServiceBoard(config, registrations, brew="/usr/bin/false")
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(lambda: (server.shutdown(), server.server_close()))
            base = f"http://127.0.0.1:{server.server_port}"

            def get(path):
                with urlopen(base + path) as response:
                    return json.load(response)

            self.assertIn(b"Services", urlopen(base + "/").read())
            inventory = get("/api/services")
            self.assertEqual(inventory["services"][0]["id"], "supervisor:journey")
            self.assertEqual(inventory["services"][0]["status"], "running")
            self.assertIn("journey stdout", get("/api/log?id=supervisor%3Ajourney&stream=stdout")["text"])
            self.assertIn("journey stderr", get("/api/log?id=supervisor%3Ajourney&stream=stderr")["text"])
            dashboard = get("/api/dashboard?id=supervisor%3Ajourney")["dashboard"]
            self.assertEqual(dashboard["views"][0]["url"], f"http://127.0.0.1:{manifest_server.server_port}/dashboard")

            payload = json.dumps({"id": "supervisor:journey", "action": "stop"}).encode()
            with self.assertRaises(HTTPError) as rejected:
                urlopen(Request(base + "/api/action", data=payload, headers={"Content-Type": "application/json", "Origin": base}, method="POST"))
            self.assertEqual(rejected.exception.code, 403)
            rejected.exception.close()

            token = get("/api/session")["token"]
            def action(name):
                request = Request(base + "/api/action", data=json.dumps({"id": "supervisor:journey", "action": name}).encode(), headers={"Content-Type": "application/json", "Origin": base, "X-Serviceboard-Token": token}, method="POST")
                with urlopen(request) as response:
                    return json.load(response)

            action("stop")
            self.assertEqual(get("/api/services")["services"][0]["status"], "stopped")
            action("start")
            self.assertEqual(get("/api/services")["services"][0]["status"], "running")
