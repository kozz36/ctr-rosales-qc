# Makefile — CTR Rosales QC (repo root)
#
# Targets:
#   dev   — run backend (uvicorn :8000) + frontend (vite :5173) concurrently
#   help  — show available targets
#
# Requires: backend/.venv activated (or uv run), node/npm for frontend.
# Both processes share the terminal; Ctrl-C stops both.

.PHONY: dev help build test-container smoke verify

help:
	@echo "Available targets:"
	@echo "  dev            — start backend (port 8000) + frontend (port 5173) concurrently"
	@echo "  build          — build the paddle-free backend Docker image (CONT-001)"
	@echo "  test-container — run full backend unit test suite inside the container (CONT-S03)"
	@echo "  smoke          — cloud-vision accuracy smoke: Registro 232 → qwen3.5:397b-cloud (CONT-S07/S08)"
	@echo "  verify         — faithful in-container full run: R8 MATCH + R9 fecha-divergence gates (CONT-S12/S13)"

dev:
	@echo "Starting backend on :8000 and frontend on :5173"
	@(cd backend && .venv/bin/uvicorn reconciliation.infrastructure.api.main:app --reload --port 8000) &
	@(cd frontend && npm run dev) &
	@wait

# ─── r10 Container verification targets ───────────────────────────────────────

## Build the backend image (paddle-free, uv --frozen) — CONT-001
build:
	docker compose build backend

## Run the full backend unit test suite INSIDE the container — CONT-S03
## Tests run per-directory to avoid the paddle-hang integration modules.
test-container:
	docker compose run --rm backend \
	  python -m pytest tests/unit/domain tests/unit/application tests/unit/adapters tests/unit/infrastructure \
	  -p no:cacheprovider -v --tb=short -q

## Cloud-vision accuracy smoke: one Protocolo page → qwen3.5:397b-cloud — CONT-S07/S08
## Run BEFORE the full verification to calibrate crop + measure token consumption.
## Requires: make build + Ollama host daemon running with qwen3.5:397b-cloud pulled.
smoke:
	docker compose run --rm backend \
	  python -m pytest tests/e2e/test_smoke_cloud_vision.py -v -s --tb=short

## Full faithful in-container verification run — CONT-S12/S13
## POST /runs → poll → assert R8 MATCH gate + R9 fecha-divergence gate.
## Requires: make build + make smoke passing + Ollama host daemon + SUNAT reachable.
verify:
	docker compose up -d backend
	@echo "Waiting for backend to be healthy..."
	@sleep 5
	docker compose run --rm backend \
	  python -m pytest tests/e2e/test_container_verification.py -v -s --tb=short
	docker compose down
