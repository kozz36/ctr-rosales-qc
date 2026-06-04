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
RUN /app/.venv/bin/python -c "import pyzbar.pyzbar; import zxingcpp; print('identity ok')" && \
    /app/.venv/bin/python -c "from openai import OpenAI; print('llm ok')"

# paddle absence assertion — build FAILS if paddle is somehow present
RUN /app/.venv/bin/python -c "import paddle; raise RuntimeError('paddle must not be present')" 2>&1 | \
    grep -q "ModuleNotFoundError" && echo "paddle absent — CONT-S02 OK" || \
    (echo "ERROR: paddle is present — remove it from deps" && exit 1)

# Non-root user for container security. Pre-create the data dirs and hand them to
# appuser BEFORE the USER switch so a FRESH named volume mounted on /data/runs or
# /data/sunat-cache inherits appuser ownership (Docker seeds an empty volume from
# the mount point) — otherwise the non-root process cannot write run output.
RUN adduser --disabled-password --gecos "" appuser && \
    mkdir -p /data/runs /data/sunat-cache && \
    chown -R appuser:appuser /app /data

USER appuser

# PATH to the venv + PYTHONPATH to the src layout
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

EXPOSE 8000

CMD ["uvicorn", "reconciliation.infrastructure.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ─── Stage 3: test ────────────────────────────────────────────────────────────
# Superset of runtime for IN-CONTAINER verification (CONT-S03/S12): adds the dev
# extra (pytest) + the tests/ tree.  Still paddle-free (no ml extra).  Runs as
# root so the named-volume mounts (/data/runs, /data/sunat-cache) stay writable.
# Build:  docker build --target test -t ctr-backend:test ./backend
FROM builder AS test

# pyzbar needs libzbar0 at runtime (same as the runtime stage)
RUN apt-get update && \
    apt-get install -y --no-install-recommends libzbar0 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Add the dev extra (pytest etc.) on top of the builder venv — still no ml/paddle
RUN uv sync --frozen --extra identity --extra llm --extra dev

# Copy the test tree (pytest config lives in the already-present pyproject.toml)
COPY tests/ ./tests/

# paddle-free guarantee holds in the test image too (CONT-S02)
RUN /app/.venv/bin/python -c "import paddle; raise RuntimeError('paddle must not be present')" 2>&1 | \
    grep -q "ModuleNotFoundError" && echo "paddle absent (test image) — CONT-S02 OK" || \
    (echo "ERROR: paddle present in test image" && exit 1)

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

CMD ["uvicorn", "reconciliation.infrastructure.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
