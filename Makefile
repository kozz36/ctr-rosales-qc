# Makefile — CTR Rosales QC (repo root)
#
# Targets:
#   dev   — run backend (uvicorn :8000) + frontend (vite :5173) concurrently
#   help  — show available targets
#
# Requires: backend/.venv activated (or uv run), node/npm for frontend.
# Both processes share the terminal; Ctrl-C stops both.

.PHONY: dev help

help:
	@echo "Available targets:"
	@echo "  dev   — start backend (port 8000) + frontend (port 5173) concurrently"

dev:
	@echo "Starting backend on :8000 and frontend on :5173"
	@(cd backend && .venv/bin/uvicorn reconciliation.infrastructure.api.main:app --reload --port 8000) &
	@(cd frontend && npm run dev) &
	@wait
