# backend/Dockerfile — paddle-free multi-stage image for ctr-rosales-qc (CONT-001)
#
# Stage 1 — builder: installs deps from the pinned uv.lock (--frozen).
#            Only core + identity + llm extras; ml (paddle) is EXCLUDED.
# Stage 2 — runtime: lean python:3.12-slim + libzbar0 (for pyzbar QR decoding).
#
# Build: docker build -t ctr-backend:latest ./backend
# Verify paddle-free: docker run --rm ctr-backend:latest python -c "import paddle"
#   → must exit non-zero (ModuleNotFoundError) — CONT-S02

# ─── Stage 1: builder ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Install uv from the official image (avoids a separate pip install step)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency manifests first — maximises layer caching
COPY pyproject.toml uv.lock ./

# Install project deps from the frozen lockfile (no ml/paddle, no dev extras)
# --no-install-project: only deps, not the project itself (added in COPY src below)
RUN uv sync --frozen --no-dev --extra identity --extra llm --no-install-project

# Copy source tree (needed for the project's own package)
COPY src/ ./src/

# Install the project itself (editable install into the venv)
RUN uv sync --frozen --no-dev --extra identity --extra llm

# ─── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Install system runtime dependency for pyzbar (libzbar0)
# zxing-cpp, Pillow, and numpy are pure Python wheels — no extra apt deps needed.
RUN apt-get update && \
    apt-get install -y --no-install-recommends libzbar0 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the populated venv from the builder stage
COPY --from=builder /app/.venv ./.venv

# Copy source tree
COPY --from=builder /app/src ./src

# Build-time sanity checks (executed as root before USER switch):
# 1. Verify pyzbar, zxing-cpp, and openai are importable (core deps present)
# 2. Assert paddle is NOT importable (CONT-S02 — paddle-free guarantee)
RUN /app/.venv/bin/python -c "import pyzbar.pyzbar; import zxing; print('identity ok')" && \
    /app/.venv/bin/python -c "from openai import OpenAI; print('llm ok')"

# paddle absence assertion — build FAILS if paddle is somehow present
RUN /app/.venv/bin/python -c "import paddle; raise RuntimeError('paddle must not be present')" 2>&1 | \
    grep -q "ModuleNotFoundError" && echo "paddle absent — CONT-S02 OK" || \
    (echo "ERROR: paddle is present — remove it from deps" && exit 1)

# Non-root user for container security
RUN adduser --disabled-password --gecos "" appuser && chown -R appuser:appuser /app

USER appuser

# PATH to the venv + PYTHONPATH to the src layout
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

EXPOSE 8000

CMD ["uvicorn", "reconciliation.infrastructure.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
