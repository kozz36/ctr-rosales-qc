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

# Build-time RapidOCR warm-up: construct the PP-OCRv5-server engine AND run ONE
# inference on a synthetic text image so ALL THREE lazy-loaded ONNX models
# (det_server ~84 MB, rec_server ~81 MB, cls_mobile ~0.6 MB) are downloaded into
# the venv-relative site-packages/rapidocr/models/ path while the network is
# available.  This makes the runtime image air-gap-capable — the COPY --from=builder
# below carries the models into the final stage.
#
# WHY inference is required (not just construction):
#   RapidOCR lazy-loads Cls + Rec only when Det detects text boxes.
#   Constructing the engine alone never triggers Cls/Rec downloads.
#   Random noise → Det finds zero boxes → Cls/Rec never load → NOT downloaded.
#   Synthetic text via cv2.putText → Det finds boxes → Cls + Rec lazy-load →
#   all three models downloaded while network is available.
#
# NOTE: Cls stays PP-OCRv4/mobile even when Det/Rec are PP-OCRv5/server (default
#   config.yaml behaviour) → the cls file is ch_ppocr_mobile_v2.0_cls_mobile.onnx,
#   NOT the package-bundled ch_ppocr_mobile_v2.0_cls_infer.onnx (different name!).
#   The disk-existence guard below is the proof that all three are present.
#
# NOTE on Docker legacy-builder heredoc exit-code bug:
#   `RUN python - <<'EOF' ... sys.exit(1) ... EOF` silently swallows exit codes
#   in the legacy builder (the heredoc stdin is consumed before the RUN layer
#   starts, so Python exits 0 regardless).  Scripts are COPY-ed from the build
#   context and executed directly so the real exit code propagates.
COPY docker/ocr_warmup.py /tmp/ocr_warmup.py
RUN /app/.venv/bin/python /tmp/ocr_warmup.py

# ─── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Install system runtime dependency for pyzbar (libzbar0).
# CRITICAL-B (JD): NO libGL/libglib needed. rapidocr 3.8.1 transitively pins the
# FULL opencv-python (links libGL.so.1 / libglib-2.0.so.0), but the
# `[tool.uv] override-dependencies` in pyproject.toml forces that requirement to a
# never-resolving marker, so ONLY opencv-python-headless is installed — and
# headless links NO libGL/libglib (verified via `ldd cv2.abi3.so`). This keeps a
# truly libGL-free air-gap image. The build-time real-inference assertion below
# is the guard: if a future change ever lets the FULL opencv back into the
# closure, the build FAILS here instead of production.
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
#
# CRITICAL-B (JD): the prior assertion only CONSTRUCTED RapidOCR (no inference),
# so it did NOT exercise the cv2 native-lib path. The `cv2` namespace is provided
# by opencv-python-headless (the uv override removes the FULL opencv that rapidocr
# transitively pins). This assertion now (1) explicitly imports cv2 (exercises
# the native-lib load in THIS slim stage — proves headless needs no libGL/libglib),
# (2) runs a real cv2 op, and (3) runs ONE real OCR inference on a tiny synthetic
# image — so if the FULL opencv ever sneaks back into the closure (it WOULD need
# libGL absent from this slim runtime), the build FAILS HERE instead of in prod.
COPY docker/ocr_assert.py /tmp/ocr_assert.py
RUN /app/.venv/bin/python /tmp/ocr_assert.py

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

# pyzbar needs libzbar0. NO libGL/libglib: the uv override keeps only the
# headless opencv (no libGL) — same CRITICAL-B air-gap guarantee as runtime.
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
