# ── Build stage: install Playwright + Chromium ────────────────────────────────
# python:3.12-slim is Debian-based; playwright install-deps works out of the box.
FROM python:3.12-slim AS base

# Install Playwright to a fixed world-readable path so the non-root runtime
# user can find the browser binaries regardless of home directory.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN pip install --no-cache-dir playwright \
    && playwright install-deps chromium \
    && playwright install chromium \
    && rm -rf /var/lib/apt/lists/* \
    && chmod -R o+rX /ms-playwright

# ── Runtime stage ─────────────────────────────────────────────────────────────
WORKDIR /app
COPY grafana_export.py .

# Output directory — mount a PVC here in Kubernetes
RUN mkdir -p /output

# Drop to a non-root user for security.
# Chromium sandbox is disabled at runtime via --no-sandbox (detected automatically
# by the script when KUBERNETES_SERVICE_HOST or /.dockerenv is present).
# Use a fixed numeric UID so Kubernetes runAsNonRoot can verify it without
# resolving the username (named users are rejected by the kubelet).
RUN useradd --uid 10001 --no-create-home --shell /bin/false exporter \
    && chown 10001:10001 /output /app
USER 10001

ENTRYPOINT ["python3", "grafana_export.py", "--out", "/output"]
