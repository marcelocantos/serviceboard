# Serviceboard

A local dashboard for services managed by Supervisor and Homebrew. It shows current status, recent stdout and stderr where the service manager exposes them, and individual start, stop, and restart controls. The UI listens on `127.0.0.1` only.

## Run

Requires Python 3.11+, `supervisorctl`, and `brew` on the host. No Python packages are needed.

```text
python3 -m serviceboard
```

Open [http://127.0.0.1:8767](http://127.0.0.1:8767). Use `--port` to choose another local port, `--supervisor-config` for a different Supervisor config, and `--registrations` for a different dashboard registration file. Supervisor and Homebrew actions run as the user who started Serviceboard. The Supervisor connection uses the local `supervisorctl` configuration; Serviceboard does not store its credentials.

The server refuses non-loopback Host headers and requires a per-run token plus a matching Origin for control requests. Do not put it behind a public reverse proxy without adding authentication and a new access-control design.

On this Mac, `supervisor/serviceboard.ini` can be installed into `/opt/homebrew/etc/supervisor.d/` and loaded with `supervisorctl reread` followed by `supervisorctl update serviceboard`. Its working directory points at this checkout, and Supervisor restarts the dashboard if it crashes.

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

`make bullseye` runs the standing checks. Its live journey starts an isolated Supervisor daemon and Serviceboard on temporary local sockets/ports. It exercises live status, logs, control requests, and the dashboard protocol without touching daily services.
