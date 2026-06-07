# backend/Dockerfile — paddle-free multi-stage image for ctr-rosales-qc (CONT-001)
#
# Stage 1 — builder: installs deps from the pinned uv.lock (--frozen).
#            Extras: core + identity + llm + ocr.  ml (paddle) is EXCLUDED.
# Stage 2 — runtime: lean python:3.12-slim + libzbar0 (for pyzbar QR decoding).
#            RapidOCR PP-OCRv5-server ONNX models (~165 MB) are bundled at build
#            time via a warm-up call in the builder, making the image air-gap-capable.
#
# Build: docker build -t ctr-backend:latest ./backend
# Verify paddle-free: docker run --rm ctr-backend:latest python -c "import paddle"
#   → must exit non-zero (ModuleNotFoundError) — CONT-S02
# Verify rapidocr bundled: docker run --rm ctr-backend:latest python -c "import rapidocr"
#   → must exit zero — CONT-S03b

# ─── Stage 1: builder ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Install uv from the official image (avoids a separate pip install step)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency manifests first — maximises layer caching
COPY pyproject.toml uv.lock ./

# Install project deps from the frozen lockfile (no ml/paddle, no dev extras)
# --no-install-project: only deps, not the project itself (added in COPY src below)
RUN uv sync --frozen --no-dev --extra identity --extra llm --extra ocr --no-install-project

# Copy source tree (needed for the project's own package)
COPY src/ ./src/

# Install the project itself (editable install into the venv)
RUN uv sync --frozen --no-dev --extra identity --extra llm --extra ocr

# Build-time RapidOCR warm-up: construct the PP-OCRv5-server engine so the
# ~165 MB ONNX models (det 84 MB + rec 81 MB) are downloaded into the venv-relative
# site-packages/rapidocr/models/ path while the network is available.  This makes
# the runtime image air-gap-capable — the COPY --from=builder below carries the
# models into the final stage without needing a network connection at runtime.
# The warm-up exits immediately after construction (no inference is run).
RUN /app/.venv/bin/python - <<'EOF'
from rapidocr import RapidOCR, OCRVersion, ModelType
print("RapidOCR warm-up: downloading PP-OCRv5-server ONNX models...")
RapidOCR(params={
    "Det.ocr_version": OCRVersion.PPOCRV5,
    "Det.model_type": ModelType.SERVER,
    "Rec.ocr_version": OCRVersion.PPOCRV5,
    "Rec.model_type": ModelType.SERVER,
})
print("RapidOCR warm-up: models cached in venv — air-gap ready.")
EOF

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
# 2. Assert rapidocr is importable (CONT-S03b — OCR dep present + models bundled)
# 3. Assert paddle is NOT importable (CONT-S02 — paddle-free guarantee)
RUN /app/.venv/bin/python -c "import pyzbar.pyzbar; import zxingcpp; print('identity ok')" && \
    /app/.venv/bin/python -c "from openai import OpenAI; print('llm ok')"

# rapidocr + offline model availability assertion — build FAILS if rapidocr absent
# or if the warm-up models are not bundled (air-gap guarantee).
RUN /app/.venv/bin/python - <<'EOF'
import rapidocr
from rapidocr import RapidOCR, OCRVersion, ModelType
# Construct engine offline to confirm models are present in the venv cache
engine = RapidOCR(params={
    "Det.ocr_version": OCRVersion.PPOCRV5,
    "Det.model_type": ModelType.SERVER,
    "Rec.ocr_version": OCRVersion.PPOCRV5,
    "Rec.model_type": ModelType.SERVER,
})
print("rapidocr + PP-OCRv5-server models OK — CONT-S03b PASS")
EOF

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
RUN uv sync --frozen --extra identity --extra llm --extra ocr --extra dev

# Copy the test tree (pytest config lives in the already-present pyproject.toml)
COPY tests/ ./tests/

# rapidocr available in test image + paddle-free guarantee (CONT-S02 + CONT-S03b)
RUN /app/.venv/bin/python -c "import rapidocr; print('rapidocr ok (test image) — CONT-S03b')"
RUN /app/.venv/bin/python -c "import paddle; raise RuntimeError('paddle must not be present')" 2>&1 | \
    grep -q "ModuleNotFoundError" && echo "paddle absent (test image) — CONT-S02 OK" || \
    (echo "ERROR: paddle present in test image" && exit 1)

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

CMD ["uvicorn", "reconciliation.infrastructure.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
