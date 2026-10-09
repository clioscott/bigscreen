# BigScreen v2

448×192 LED wall (21× 64×64 panels: 3 parallel chains of 7) driven by a Pi 4. All fetching
happens on a separate server; the Pi only renders. Research and history: `RESEARCH.md`.

## Machines

| | Host | SSH | Role |
|---|---|---|---|
| Server | `MAGI-server` | `scott@192.168.1.111` | hub: fetch jobs, config, control panel on **:8800** |
| Pi 4 | `bigscreen` | `scott@192.168.1.100` | display (passwordless sudo) |

## Where things live

- **Canonical repo: `~/bigscreen` on the server.** It accepts pushes (`receive.denyCurrentBranch=updateInstead`),
  so a push updates the working tree directly. This Windows folder is a clone with remote `server`.
- **Runtime state: `~/bigscreen-state/` on the server** (not in git): `config.json` (the single config),
  `config-backups/`, `status.json`, `data/` (synced to the Pi), `secrets/` (Google token), `cache/`.
- Pi reads data from `/dev/shm/bigscreen` (tmpfs), rsynced by `hub/sync.py` after every successful job.

## Hub (server)

- `hub/main.py` starts the scheduler + FastAPI app (`hub/app.py`, UI in `hub/web/`, plain JS, no build).
- Jobs in `hub/jobs/` run as **one subprocess per fetch** (`python -m hub.jobs.<name>`) with a timeout;
  never make a job a long-lived loop (v1's finance worker leaked to 8.7 GB that way).
- Job health → `status.json` → Status tab. A job is "stale" after 3 missed intervals.
- Service: `bigscreen-hub` (system unit, user scott). `sudo systemctl restart bigscreen-hub` is passwordless.
- Logs: `journalctl -u bigscreen-hub -f`.

## Deploying a change

    git push server main                          # from Windows, updates ~/bigscreen on the server
    ssh scott@192.168.1.111 'sudo systemctl restart bigscreen-hub'
    # if requirements changed: ssh ... 'cd ~/bigscreen && .venv/bin/pip install -r requirements-hub.txt'

## Display (Pi) — currently v1 in bridge mode

The v1 renderer (`~/bigscreen-calendar`, service `bigscreen-client`) still drives the panels. It reads its
own `config/app-config.json` only at startup, so the panel's **Apply to display** writes that file from the hub
config (`hub/pi.py:v1_config`), syncs ticker logos, and restarts the service. Headline categories are passed
as v1's `subreddits` list. The v2 renderer replaces this (see RESEARCH.md roadmap).

## Rules

- Display tuning values in `system.display` came from exhaustive hardware testing — don't change them casually.
- Never run anything heavy on the Pi; it has no CPU headroom.
- Reddit is not a usable source (RSS ends 2026-11-13; `.json` 403s). Headlines come from publisher RSS.
