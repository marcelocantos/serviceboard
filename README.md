# Serviceboard

A local dashboard for services managed by Supervisor and Homebrew. It shows current status, recent stdout and stderr where the service manager exposes them, and individual start, stop, and restart controls. The UI listens on `127.0.0.1` only.

## Run

Requires Python 3.11+, `supervisorctl`, and `brew` on the host. No Python packages are needed.

```text
python3 -m serviceboard
```

Open [http://127.0.0.1:8767](http://127.0.0.1:8767). Use `--port` to choose another local port, `--supervisor-config` for a different Supervisor config, and `--registrations` for a different dashboard registration file. Supervisor and Homebrew actions run as the user who started Serviceboard. The Supervisor connection uses the local `supervisorctl` configuration; Serviceboard does not store its credentials.

The server refuses non-loopback Host headers and requires a per-run token plus a matching Origin for control requests. Do not put it behind a public reverse proxy without adding authentication and a new access-control design.

The service search is focused on load. Use Up and Down to move through visible results. Enter clears a search and keeps the selected service; Escape clears it and restores the selection held before the search. Running services appear before stopped services.

While the page is visible and focused, Serviceboard listens for Supervisor process-state events and checks Homebrew every five seconds. Switching tabs, minimizing, or moving focus away stops those updates; returning reconnects and refreshes the inventory. If the Supervisor event listener is not installed or cannot connect, the page falls back to five-second Supervisor checks while active. The initial inventory and post-action refreshes always fetch fresh status.

`supervisor/serviceboard.ini` is a sample program and event listener for this checkout. Leave the file in the repository and add its path to Supervisor's include list. Supervisor expands `%(here)s` to the `supervisor/` directory, and `directory=%(here)s/..` is the checkout. The commands use `%(ENV_HOME)s/.py/bin/python3`; change `command` if Python lives elsewhere. On this Mac, Homebrew's include directory is `/opt/homebrew/etc/supervisor.d/`; after the include path is in place, load the program with `supervisorctl reread` followed by `supervisorctl update serviceboard`. The listener starts only while at least one focused page has a live connection, and Supervisor restarts the dashboard if it crashes.

## Service dashboard protocol, version 1

Register a service's **local origin** in `~/.config/serviceboard/dashboards.json`:

```json
{
  "dashboards": {
    "supervisor:my-api": "http://127.0.0.1:8080",
    "homebrew:my-worker": "http://127.0.0.1:9090"
  }
}
```

The key is the source (`supervisor` or `homebrew`), a colon, and the exact service name. Serviceboard fetches `/.well-known/serviceboard.json` from that origin when the Dashboard tab opens. It accepts loopback HTTP origins only, does not follow redirects, and limits the response to 32 KiB. A service serves:

```json
{
  "version": 1,
  "title": "My API",
  "views": [
    {"title": "Overview", "path": "/dashboard"},
    {"title": "Metrics", "path": "/metrics/view"}
  ]
}
```

Views must be paths on the registered origin. Serviceboard shows each view in a sandboxed frame and offers an **Open separately** link for pages that disallow embedding. Registration is explicit so a service cannot silently add browser destinations to the control UI. The manifest describes only display views; service control remains with Supervisor or Homebrew.

## Logs

Supervisor logs come from `supervisorctl tail` and show the latest 32 KiB. If a program sets `redirect_stderr=true`, its stderr appears in stdout. Homebrew logs are available when its service plist declares `StandardOutPath` or `StandardErrorPath`; otherwise the tab explains why no stream is available.

## Verify

`make bullseye` runs the standing checks. Its live journeys start isolated Supervisor daemons and Serviceboard on temporary local sockets/ports. They exercise live status, logs, control requests, the dashboard protocol, and keyboard navigation in Chromium without touching daily services. The browser journey uses `uv` to supply Playwright.
