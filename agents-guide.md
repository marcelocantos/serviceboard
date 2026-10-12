# Serviceboard

Local dashboard for services managed by Supervisor and Homebrew. It listens on `127.0.0.1` only. There is no package to install and no third-party Python dependency. Python 3.11+, `supervisorctl`, and `brew` must already be on the host.

## Run

```text
python3 -m serviceboard
```

Flags:

- `--port` (default `8767`)
- `--supervisor-config` (default `/opt/homebrew/etc/supervisord.conf`)
- `--registrations` (default `~/.config/serviceboard/dashboards.json`)
- `--event-socket` (default none; the Supervisor event listener connects here)

`python3 -m serviceboard.events --socket <path>` is the Supervisor event listener, not a second product.

The process refuses non-loopback `Host` headers. Control requests need the per-run token from `GET /api/session` and a matching `Origin`. Do not put Serviceboard behind a public reverse proxy.

## UI

The search field is focused on load. Up and Down move through visible services. Page Up and Page Down scroll the inventory by one page over 200ms. Enter clears a search and keeps the selection. Escape clears it and restores the selection from before the search. The inventory and service detail panels fill the page height; their contents scroll inside the panels.

While the page is visible and focused, Supervisor changes arrive on `GET /api/events` and Homebrew is checked every five seconds. A hidden or blurred page stops those updates. If the event listener is not connected, Supervisor status falls back to a five-second poll.

## HTTP

All routes require a loopback `Host`.

- `GET /api/session` returns the per-run token.
- `GET /api/services`, `GET /api/supervisor`, and `GET /api/homebrew` return inventory JSON.
- `GET /api/events?token=…` is the Supervisor server-sent event stream.
- `GET /api/log?id=…&stream=stdout|stderr` returns the latest 32,000 bytes.
- `GET /api/dashboard?id=…` fetches a registered service's dashboard manifest.
- `POST /api/action` with `Content-Type: application/json`, header `X-Serviceboard-Token`, and body `{"id","action"}` starts, stops, or restarts one service. `action` is `start`, `stop`, or `restart`.

Service ids are `supervisor:<name>` or `homebrew:<name>`.

## Dashboard protocol, version 1

`~/.config/serviceboard/dashboards.json` maps a service id to a loopback origin. Serviceboard fetches `/.well-known/serviceboard.json` from that origin when the Dashboard tab opens. It does not follow redirects and limits the body to 32 KiB. Views are paths on that same origin, shown in a sandboxed frame. Registration is explicit. The manifest cannot add control actions.

## Supervisor sample

`supervisor/serviceboard.ini` is a sample program and event listener for this checkout. Leave it in the repo and add its path to Supervisor's include list. `%(here)s` is the `supervisor/` directory. `directory=%(here)s/..` is the checkout. The commands use `%(ENV_HOME)s/.py/bin/python3`. The listener runs only while at least one focused page is connected. Supervisor restarts the dashboard if it crashes.

## Verify

`make bullseye` is the standing check. It runs the unit tests and a Chromium journey against an isolated Supervisor, then checks `app.js` syntax and formatting. The journey must not touch the daily Supervisor or the daily Serviceboard port.

## Gotchas

- Supervisor logs come from `supervisorctl tail`. If a program sets `redirect_stderr=true`, stderr is inside stdout and the stderr tab explains that.
- Homebrew logs exist only when the service plist sets `StandardOutPath` or `StandardErrorPath`.
- Static files are read from disk on each request. Python changes need a process restart.
- The page's Content-Security-Policy does not allow `unsafe-eval`.
