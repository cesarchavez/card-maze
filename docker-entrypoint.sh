#!/bin/sh
# ── Card Maze — Docker entrypoint ─────────────────────────────────────────────
# Runs once each time the container starts.
#
# Strategy: all user-writable files live in /instance (a mounted host volume).
#   - decks.xml        → seeded from the image on first run, then user-editable
#   - appvalues.xml    → created by the app itself on first request
#   - results.db       → created by the app itself on first request
#   - prize_images/    → directory for uploaded prize artwork
#
# Symlinks from /app point to /instance so app.py needs no path changes.
# ─────────────────────────────────────────────────────────────────────────────
set -e

INSTANCE=/instance

# ── 1. Ensure instance directory structure exists ─────────────────────────────
mkdir -p "$INSTANCE/prize_images"

# ── 2. Seed defaults on first run ─────────────────────────────────────────────
if [ ! -f "$INSTANCE/decks.xml" ]; then
    cp /app/decks.xml "$INSTANCE/decks.xml"
    echo "[entrypoint] First run — seeded default decks.xml into $INSTANCE"
fi
# appvalues.xml and results.db are created automatically by the app on startup;
# no manual seeding needed.

# ── 3. Symlink instance files into /app ───────────────────────────────────────
# decks.xml
ln -sf "$INSTANCE/decks.xml" /app/decks.xml

# appvalues.xml (target may not exist yet; symlink is still valid — app creates it)
ln -sf "$INSTANCE/appvalues.xml" /app/appvalues.xml

# results.db (same: sqlite3 creates the file at the symlink target on first connect)
ln -sf "$INSTANCE/results.db" /app/results.db

# prize_images — replace the empty placeholder dir with a symlink to /instance
rm -rf /app/static/prize_images
ln -s  "$INSTANCE/prize_images" /app/static/prize_images

# ── 4. Start Gunicorn ─────────────────────────────────────────────────────────
echo "[entrypoint] Instance ready. Starting Gunicorn…"
exec gunicorn --config /app/gunicorn.conf.py app:app
