# ── Card Maze — Dockerfile ────────────────────────────────────────────────────
# Builds a lean production image served by Gunicorn.
# Persistent files (results.db, appvalues.xml, decks.xml, prize_images/) are
# stored in /instance and should be mounted as a volume at runtime so they
# survive container restarts and image rebuilds.
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim

# ── System deps ───────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        tini \
    && rm -rf /var/lib/apt/lists/*

# ── Python deps ───────────────────────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application code ──────────────────────────────────────────────────────────
COPY . .

# Remove local-dev artefacts that will be redirected to /instance at runtime
RUN rm -f results.db appvalues.xml \
    && rm -rf static/prize_images \
    && mkdir -p static/prize_images

# ── Entrypoint ────────────────────────────────────────────────────────────────
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# ── Runtime ───────────────────────────────────────────────────────────────────
EXPOSE 8000

# tini reaps zombie processes and forwards signals cleanly to gunicorn
ENTRYPOINT ["tini", "--", "/docker-entrypoint.sh"]
