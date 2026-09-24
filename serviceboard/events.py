"""Supervisor event listener and its local bridge to active dashboard pages."""

import argparse
import os
import socket
import stat
import sys
import threading
from pathlib import Path


LISTENER_NAME = "serviceboard-events"
MAX_EVENT_BYTES = 1_048_576


class SupervisorEvents:
    def __init__(self, path, control):
        self.path = Path(path)
        self.control = control
        self.condition = threading.Condition()
        self.clients = 0
        self.version = 0
        self.socket = None
        self.listener_running = False
        self.closed = False

    def _bind(self):
        if self.socket is not None:
            return
        if self.path.exists():
            info = self.path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                raise OSError(f"Refusing to replace {self.path}")
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as probe:
                try:
                    probe.connect(str(self.path))
                except ConnectionRefusedError:
                    self.path.unlink()
                else:
                    raise OSError(f"Event socket is already in use: {self.path}")
        receiver = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            receiver.bind(str(self.path))
            os.chmod(self.path, 0o600)
            receiver.settimeout(1)
        except OSError:
            receiver.close()
            raise
        self.socket = receiver
        threading.Thread(target=self._receive, args=(receiver,), daemon=True).start()

    def _receive(self, receiver):
        while True:
            try:
                message = receiver.recv(64)
            except socket.timeout:
                if self.closed:
                    return
                continue
            except OSError:
                return
            if message == b"changed":
                with self.condition:
                    self.version += 1
                    self.condition.notify_all()

    def reset(self):
        try:
            self.control("stop", LISTENER_NAME)
        except Exception:
            # A fresh install or an already-stopped listener has nothing to reset.
            pass

    def subscribe(self):
        with self.condition:
            if self.closed:
                raise OSError("Event bridge is closed")
            if self.clients == 0:
                self._bind()
                self.control("start", LISTENER_NAME)
                self.listener_running = True
            self.clients += 1
            return self.version

    def wait(self, version, timeout):
        with self.condition:
            self.condition.wait_for(lambda: self.version != version or self.closed, timeout)
            return self.version

    def unsubscribe(self):
        with self.condition:
            self.clients -= 1
            if self.clients == 0 and self.listener_running:
                try:
                    self.control("stop", LISTENER_NAME)
                except Exception as exc:
                    print(f"Unable to stop Supervisor event listener: {exc}", file=sys.stderr)
                self.listener_running = False

    def close(self):
        with self.condition:
            self.closed = True
            if self.listener_running:
                try:
                    self.control("stop", LISTENER_NAME)
                except Exception as exc:
                    print(f"Unable to stop Supervisor event listener: {exc}", file=sys.stderr)
                self.listener_running = False
            self.condition.notify_all()
            if self.socket is not None:
                self.socket.close()
                self.socket = None
                self.path.unlink(missing_ok=True)


def listen(path):
    """Read Supervisor's stdin protocol and notify Serviceboard over a Unix socket."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sender:
        while True:
            sys.stdout.buffer.write(b"READY\n")
            sys.stdout.buffer.flush()
            header = sys.stdin.buffer.readline()
            if not header:
                return
            try:
                fields = dict(part.split(b":", 1) for part in header.split())
                length = int(fields[b"len"])
                if not 0 <= length <= MAX_EVENT_BYTES:
                    raise ValueError("Invalid event length")
                payload = sys.stdin.buffer.read(length)
                if len(payload) != length:
                    raise ValueError("Incomplete event payload")
                if fields.get(b"eventname", b"").startswith(b"PROCESS_STATE_"):
                    sender.sendto(b"changed", str(path))
            except (OSError, ValueError) as exc:
                print(f"Supervisor event bridge failed: {exc}", file=sys.stderr)
                return
            sys.stdout.buffer.write(b"RESULT 2\nOK")
            sys.stdout.buffer.flush()


def main():
    parser = argparse.ArgumentParser(description="Forward Supervisor process changes to Serviceboard")
    parser.add_argument("--socket", required=True)
    args = parser.parse_args()
    listen(args.socket)


if __name__ == "__main__":
    main()
