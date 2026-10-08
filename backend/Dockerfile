# ONE image, TWO Cloud Run services (scripts/deploy-gcp.sh, DEPLOYMENT.md):
#   blussit-api     RUN_MODE=api     HTTP API, webhooks, websockets
#   blussit-worker  RUN_MODE=worker  background loops, index builds, backfills
# RUN_MODE is deliberately NOT set here: each service sets it, and the app
# refuses to start in production without an explicit value (app/core/config.py).
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY requirements-runtime.txt .
RUN pip install --upgrade pip && pip install -r requirements-runtime.txt

COPY app ./app

RUN mkdir -p /tmp/blussit-uploads/photos && chown -R app:app /app /tmp/blussit-uploads

USER app

EXPOSE 8080

# Liveness only (process up; 503 if a background loop died). Cloud Run's
# startup probe uses /api/ready (database + critical indexes) and the
# worker's liveness probe /api/health — see scripts/deploy-gcp.sh. Both
# paths exist in every RUN_MODE.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8080\")}/api/health', timeout=3).read()"

# Per-message-deflate keeps zlib buffers per open WebSocket (tens to hundreds of KB
# each); with hundreds of sockets per instance that memory matters more than
# the bandwidth saved on small JSON events.
# --timeout-graceful-shutdown: Cloud Run SIGKILLs 10 s after SIGTERM. Uvicorn
# stops waiting for slow in-flight requests after 4 s so the app's own
# shutdown (up to 5 s: finish background WhatsApp sends, stop the reminder
# loop cleanly — app/main.py on_shutdown) still runs before the kill. The
# same command serves both services; RUN_MODE decides what starts.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --no-access-log --ws-per-message-deflate false --timeout-graceful-shutdown 4"]
