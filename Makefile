# Makefile — CTR Rosales QC (repo root)
#
# Targets:
#   dev   — run backend (uvicorn :8000) + frontend (vite :5173) concurrently
#   help  — show available targets
#
# Requires: backend/.venv activated (or uv run), node/npm for frontend.
# Both processes share the terminal; Ctrl-C stops both.

.PHONY: dev help build test-container smoke verify verify-fast install app-up app-down app-logs

help:
	@echo "Available targets:"
	@echo "  install        — one-command install + launch (Docker, deterministic mode) → ./install.sh"
	@echo "  app-up         — start the v1.0.0 app (backend + frontend) via docker-compose.app.yml"
	@echo "  app-down       — stop the v1.0.0 app"
	@echo "  app-logs       — follow the v1.0.0 app logs"
	@echo "  dev            — start backend (port 8000) + frontend (port 5173) concurrently"
	@echo "  build          — build the paddle-free backend Docker image (CONT-001)"
	@echo "  test-container — run full backend unit test suite inside the container (CONT-S03)"
	@echo "  smoke          — cloud-vision accuracy smoke: Registro 232 → qwen3.5:397b-cloud (CONT-S07/S08)"
	@echo "  verify         — faithful in-container full run: R8 MATCH + R9 fecha-divergence gates (CONT-S12/S13)"
	@echo "  verify-fast    — same R8+R9 gate on a 3-section ~50-page subset (minutes, not ~90 min)"

# ─── v1.0.0 turnkey app (docker-compose.app.yml) ──────────────────────────────

## One-command install + launch for end users (deterministic vision-off + SUNAT).
install:
	./install.sh

## Start the app (backend + frontend) — builds fresh, then runs detached.
app-up:
	docker compose -f docker-compose.app.yml up -d --build

## Stop the app.
app-down:
	docker compose -f docker-compose.app.yml down

## Follow the app logs.
app-logs:
	docker compose -f docker-compose.app.yml logs -f

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
	docker compose run --rm -e CTR_VERIFY_STRICT=1 backend \
	  python -m pytest tests/e2e/test_container_verification.py -v -s --tb=short
	docker compose down

## Fast acceptance gate — SAME R8+R9 assertions on a 3-section front-subset (~50
## pages incl. registro 232) instead of all 493. Minutes vs ~90 min on CPU.
## Builds the subset on the host (backend venv PyMuPDF), then mounts it into the
## container via the verify-fast override. CONT-S12/S13 (reduced corpus).
COMPOSE_FAST = docker compose -f docker-compose.yml -f docker-compose.verify-fast.yml
verify-fast:
	uv run --project backend python backend/scripts/make_verify_subset.py
	$(COMPOSE_FAST) up -d backend
	@echo "Waiting for backend to be healthy..."
	@sleep 5
	$(COMPOSE_FAST) run --rm -e CTR_VERIFY_STRICT=1 backend \
	  python -m pytest tests/e2e/test_container_verification.py -v -s --tb=short
	$(COMPOSE_FAST) down
