# ── Card Maze — Gunicorn configuration ───────────────────────────────────────
# Used by the Docker entrypoint: gunicorn --config gunicorn.conf.py app:app
# ─────────────────────────────────────────────────────────────────────────────

import os

# Bind to all interfaces on port 8000 (mapped by docker-compose)
bind = "0.0.0.0:8000"

# Single worker — keeps SQLite writes and XML edits safely serialised.
# Increase to 2 only if you add proper DB-level locking.
workers = 1

# Four threads give concurrency without multiple processes
threads = 4

# Worker timeout in seconds (generous for slow XML writes)
timeout = 60

# Log to stdout/stderr so Docker captures them with `docker logs`
accesslog = "-"
errorlog  = "-"
loglevel  = os.getenv("LOG_LEVEL", "info")

# Forward real client IPs when sitting behind a reverse proxy (nginx, Caddy…)
forwarded_allow_ips = "*"
proxy_fix_middleware = True
