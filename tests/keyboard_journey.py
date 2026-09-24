"""Keyboard journey through the real browser, app, and isolated Supervisor."""

import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

from serviceboard.app import Handler, ServiceBoard

SUBPIXEL_TOLERANCE = 1


class TrackingHandler(Handler):
    requests = []
    requests_lock = threading.Lock()

    def do_GET(self):
        with self.requests_lock:
            self.requests.append(self.path.partition("?")[0])
        super().do_GET()

    @classmethod
    def count(cls, path):
        with cls.requests_lock:
            return cls.requests.count(path)


class KeyboardJourney(unittest.TestCase):
    def test_search_navigation_and_dismissal(self):
        with tempfile.TemporaryDirectory(prefix="serviceboard-keyboard-") as directory:
            root = Path(directory)
            worker = root / "worker.py"
            worker.write_text("import time\nwhile True: time.sleep(1)\n")
            config = root / "supervisord.conf"
            programs = "\n".join(
                f"[program:run-{number:02d}]\ncommand={sys.executable} -u {worker}\nstartsecs=0\nautorestart=false\nstdout_logfile=NONE\n"
                for number in range(12)
            )
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
{programs}
[program:zz-stopped]
command={sys.executable} -u {worker}
autostart=false
stdout_logfile=NONE
[eventlistener:serviceboard-events]
command={sys.executable} -u -m serviceboard.events --socket {root}/events.sock
directory={Path(__file__).resolve().parents[1]}
events=PROCESS_STATE
autostart=false
autorestart=true
startsecs=0
buffer_size=100
stderr_logfile={root}/events.log
""")
            brew = root / "brew"
            brew_reads = root / "brew-reads"
            brew.write_text(f"#!{sys.executable}\nimport json\nfrom pathlib import Path\nwith Path({str(brew_reads)!r}).open('a') as output: output.write('x')\nprint(json.dumps([{{'name': 'brew-sample', 'status': 'none'}}]))\n")
            brew.chmod(0o755)
            daemon = subprocess.Popen([shutil.which("supervisord"), "-c", str(config)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            server = None
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    status = subprocess.run([shutil.which("supervisorctl"), "-c", str(config), "status"], capture_output=True, text=True)
                    if status.stdout.count("RUNNING") == 12 and "zz-stopped" in status.stdout:
                        break
                    time.sleep(0.1)
                else:
                    self.fail(f"Isolated Supervisor did not start: {status.stdout} {status.stderr}")

                Handler.board = ServiceBoard(config, root / "none.json", brew=brew, event_socket=root / "events.sock")
                TrackingHandler.requests = []
                server = ThreadingHTTPServer(("127.0.0.1", 0), TrackingHandler)
                server.daemon_threads = True
                threading.Thread(target=server.serve_forever, daemon=True).start()
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    try:
                        context = browser.new_context(viewport={"width": 1280, "height": 800})
                        page = context.new_page()
                        page.goto(f"http://127.0.0.1:{server.server_port}")
                        page.locator('[data-service-id="supervisor:zz-stopped"]').wait_for(state="attached")
                        page.locator('[data-service-id="homebrew:brew-sample"]').wait_for(state="attached")
                        self.assertEqual(page.locator("#refresh").count(), 0)
                        artwork_heights = []
                        # The source artwork bounds are 416/460 and 269/271 of the image heights.
                        for service_id, filename, expected_size, artwork_fraction in (("supervisor:run-00", "supervisor.png", 34 * 0.7, 416 / 460), ("homebrew:brew-sample", "homebrew.svg", 21.7, 269 / 271)):
                            logo = page.locator(f'[data-service-id="{service_id}"] .service-icon img')
                            self.assertEqual(logo.get_attribute("src"), f"/assets/{filename}")
                            self.assertTrue(logo.evaluate("image => image.complete && image.naturalWidth > 0"))
                            bounds = logo.bounding_box()
                            self.assertAlmostEqual(bounds["width"], expected_size, delta=0.5)
                            self.assertAlmostEqual(bounds["height"], expected_size, delta=0.5)
                            artwork_heights.append(bounds["height"] * artwork_fraction)
                            box = page.locator(f'[data-service-id="{service_id}"] .service-icon')
                            box_bounds = box.bounding_box()
                            self.assertEqual(box_bounds["width"], 34)
                            self.assertEqual(box_bounds["height"], 34)
                            self.assertAlmostEqual(bounds["x"] + bounds["width"] / 2, box_bounds["x"] + box_bounds["width"] / 2, delta=0.5)
                            self.assertAlmostEqual(bounds["y"] + bounds["height"] / 2, box_bounds["y"] + box_bounds["height"] / 2, delta=0.5)
                            self.assertNotEqual(box.evaluate("element => getComputedStyle(element).backgroundColor"), "rgba(0, 0, 0, 0)")
                        self.assertAlmostEqual(artwork_heights[0], artwork_heights[1], delta=0.5)
                        search = page.locator("#search")
                        selected = page.locator(".service-item.selected")

                        self.assertEqual(page.evaluate("document.activeElement.id"), "search")
                        statuses = page.locator(".service-state").all_text_contents()
                        self.assertEqual(statuses[:12], ["RUNNING"] * 12)
                        self.assertEqual(set(statuses[12:]), {"NONE", "STOPPED"})

                        search.press("ArrowDown")
                        self.assertEqual(selected.get_attribute("data-service-id"), "supervisor:run-01")
                        self.assertEqual(page.evaluate("document.activeElement.id"), "search")
                        search.press("ArrowUp")
                        self.assertEqual(selected.get_attribute("data-service-id"), "supervisor:run-00")

                        search.press("ArrowDown")
                        self.assertEqual(selected.get_attribute("data-service-id"), "supervisor:run-01")
                        search.fill("run-0")
                        self.assertEqual(selected.get_attribute("data-service-id"), "supervisor:run-00")
                        self.assertEqual(page.locator("#detail-name").inner_text(), "run-00")
                        search.fill("run-03")
                        self.assertEqual(selected.get_attribute("data-service-id"), "supervisor:run-03")
                        self.assertEqual(page.locator("#detail-name").inner_text(), "run-03")
                        self.assertEqual(page.evaluate("document.activeElement.id"), "search")
                        search.press("Escape")
                        self.assertEqual(selected.get_attribute("data-service-id"), "supervisor:run-01")

                        search.fill("zz")
                        self.assertEqual(selected.get_attribute("data-service-id"), "supervisor:zz-stopped")
                        search.press("Enter")
                        self.assertEqual(search.input_value(), "")
                        self.assertEqual(page.evaluate("document.activeElement.id"), "search")
                        self.assertEqual(selected.get_attribute("data-service-id"), "supervisor:zz-stopped")
                        self.assertEqual(page.locator(".service-item").count(), 14)
                        position = selected.evaluate("row => { const a=document.querySelector('#service-list').getBoundingClientRect(), b=row.getBoundingClientRect(); return {listTop:a.top, listBottom:a.bottom, rowTop:b.top, rowBottom:b.bottom, viewportHeight:innerHeight}; }")
                        self.assertTrue(position["rowTop"] >= position["listTop"] - SUBPIXEL_TOLERANCE and position["rowBottom"] <= position["listBottom"] + SUBPIXEL_TOLERANCE and position["rowTop"] >= -SUBPIXEL_TOLERANCE and position["rowBottom"] <= position["viewportHeight"] + SUBPIXEL_TOLERANCE, position)

                        search.fill("run-03")
                        search.press("ArrowDown")
                        self.assertEqual(selected.get_attribute("data-service-id"), "supervisor:run-03")
                        search.press("Escape")
                        self.assertEqual(search.input_value(), "")
                        self.assertEqual(page.evaluate("document.activeElement.id"), "search")
                        self.assertEqual(selected.get_attribute("data-service-id"), "supervisor:zz-stopped")
                        page.set_viewport_size({"width": 390, "height": 844})
                        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)

                        deadline = time.monotonic() + 8
                        while time.monotonic() < deadline and Handler.board.events.clients != 1:
                            time.sleep(0.1)
                        self.assertEqual(Handler.board.events.clients, 1)
                        self.assertTrue(page.evaluate("document.hasFocus() && document.visibilityState === 'visible'"))
                        active_reads = len(brew_reads.read_text())
                        active_supervisor_reads = TrackingHandler.count("/api/supervisor")
                        time.sleep(5.3)
                        self.assertGreater(len(brew_reads.read_text()), active_reads)
                        self.assertEqual(TrackingHandler.count("/api/supervisor"), active_supervisor_reads)
                        subprocess.run([shutil.which("supervisorctl"), "-c", str(config), "stop", "run-00"], check=True, capture_output=True)
                        page.locator('[data-service-id="supervisor:run-00"] .service-state').filter(has_text="STOPPED").wait_for(timeout=4000)

                        foreground_reads = len(brew_reads.read_text())
                        page.evaluate("() => { Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'hidden'}); document.dispatchEvent(new Event('visibilitychange')); }")
                        self.assertEqual(page.evaluate("document.visibilityState"), "hidden")
                        deadline = time.monotonic() + 8
                        while time.monotonic() < deadline and Handler.board.events.clients:
                            time.sleep(0.1)
                        self.assertEqual(Handler.board.events.clients, 0)
                        hidden_reads = len(brew_reads.read_text())
                        time.sleep(5.3)
                        self.assertEqual(len(brew_reads.read_text()), hidden_reads)
                        page.evaluate("() => { delete document.visibilityState; document.dispatchEvent(new Event('visibilitychange')); }")
                        deadline = time.monotonic() + 8
                        while time.monotonic() < deadline and Handler.board.events.clients != 1:
                            time.sleep(0.1)
                        self.assertEqual(Handler.board.events.clients, 1)
                        self.assertGreater(len(brew_reads.read_text()), foreground_reads)
                        page.evaluate("() => { Object.defineProperty(document, 'hasFocus', {configurable: true, value: () => false}); window.dispatchEvent(new Event('blur')); }")
                        deadline = time.monotonic() + 8
                        while time.monotonic() < deadline and Handler.board.events.clients:
                            time.sleep(0.1)
                        self.assertEqual(Handler.board.events.clients, 0)
                        page.evaluate("() => { delete document.hasFocus; window.dispatchEvent(new Event('focus')); }")
                        deadline = time.monotonic() + 8
                        while time.monotonic() < deadline and Handler.board.events.clients != 1:
                            time.sleep(0.1)
                        self.assertEqual(Handler.board.events.clients, 1)
                    finally:
                        browser.close()
            finally:
                if server:
                    server.shutdown()
                    server.server_close()
                    Handler.board.events.close()
                if daemon.poll() is None:
                    daemon.terminate()
                    daemon.wait(timeout=5)
                daemon.stderr.close()
