"""FastAPI application factory — lifespan, CORS, router mount.

Entry point:
    uvicorn reconciliation.infrastructure.api.main:app --host 127.0.0.1 --port 8000 --reload

The ``app`` instance is created at module load and is the WSGI/ASGI entry
point for uvicorn.

Architecture decisions:
- Bind to localhost only (127.0.0.1) — local-first MVP; not exposed to the network.
- No auth (out of scope for MVP) — add before any network exposure.
- In-memory run registry (dict): simple and sufficient for single-process local use.
  If multi-process or persistence is needed, swap for Redis/DB in a later phase.
- CORS allows localhost:5173 (Vite dev server) and localhost:4173 (Vite preview).
  Add further origins via CORS_ORIGINS env var (comma-separated).
- Config loaded from config.yaml at startup; falls back to coded defaults.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from reconciliation.application.config import AppConfig
from reconciliation.infrastructure.api.routes import router

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan (replaces deprecated on_event handlers in FastAPI)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialise shared state at startup; clean up on shutdown."""
    # Load config (env > yaml > defaults)
    config_path = os.environ.get("RECONCILIATION_CONFIG", "config.yaml")
    config = AppConfig.from_yaml(config_path)
    logger.info("config loaded: vision.provider=%s", config.vision.provider)

    # Ensure the base output directory exists
    config.output_dir.mkdir(parents=True, exist_ok=True)

    # In-memory run registry: {run_id → {status, ctx, review_service, ...}}
    run_registry: dict[str, Any] = {}

    app.state.config = config
    app.state.run_registry = run_registry

    logger.info("reconciliation API ready — output_dir=%s", config.output_dir)
    yield

    # Shutdown: nothing to clean up for the in-memory registry.
    logger.info("reconciliation API shutting down.")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """Build and return the configured FastAPI application."""
    app = FastAPI(
        title="CTR Rosales QC — Material Reconciliation API",
        description=(
            "Local-first API for reconciling declared material lists against "
            "guías de remisión extracted from construction site PDFs."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    # --- CORS (security-ops: explicit allowlist, no wildcard) ---
    raw_origins = os.environ.get("CORS_ORIGINS", "")
    extra_origins = [o.strip() for o in raw_origins.split(",") if o.strip()]
    allowed_origins = [
        "http://localhost:5173",  # Vite dev server
        "http://localhost:4173",  # Vite preview
        "http://127.0.0.1:5173",
        "http://127.0.0.1:4173",
        *extra_origins,
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,  # no cookies used in MVP
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    # --- Routes ---
    app.include_router(router, prefix="/api/v1")

    return app


# ---------------------------------------------------------------------------
# Module-level instance (uvicorn entry point)
# ---------------------------------------------------------------------------

app = create_app()
