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
    # --- Vision key store + probe (D2/D3): constructed BEFORE AppConfig.from_yaml ---
    # The key file is read first so that os.environ can be patched BEFORE pydantic-
    # settings reads it (env > yaml > defaults priority; a post-construction mutation
    # has no effect on the already-built AppConfig).
    from reconciliation.infrastructure.vision_key_file_store import (  # noqa: PLC0415
        VisionKeyFileStore,
    )
    from reconciliation.adapters.vision.key_probe import (  # noqa: PLC0415
        VisionKeyProbeAdapter,
    )

    key_store = VisionKeyFileStore()
    key_probe = VisionKeyProbeAdapter()

    vision_key = key_store.read()
    if vision_key:
        # Composition-Root env injection (D4): set env BEFORE AppConfig.from_yaml so
        # pydantic-settings reads the injected values at construction time.
        #
        # Precedence rules (JD MEDIUM-4):
        #   ENABLED    → force-set true (a present key file IS the operator's "enable").
        #   API_KEY    → force-set (the file IS the key; no other source for this value).
        #   PROVIDER   → setdefault (explicit operator/compose value wins; default=ollama).
        #   BASE_URL   → setdefault (explicit dev-compose URL must not be retargeted).
        #   MODEL      → setdefault (explicit override must not be discarded).
        os.environ["RECONCILIATION__VISION__ENABLED"] = "true"
        os.environ["RECONCILIATION__VISION__OLLAMA__API_KEY"] = vision_key
        os.environ.setdefault("RECONCILIATION__VISION__PROVIDER", "ollama")
        os.environ.setdefault("RECONCILIATION__VISION__OLLAMA__BASE_URL", "https://ollama.com/v1")
        os.environ.setdefault("RECONCILIATION__VISION__OLLAMA__MODEL", "kimi-k2.5")
        logger.info(
            "vision key injected into env (ENABLED=true, API_KEY set) — "
            "AppConfig will see vision-on at startup"
        )
    else:
        logger.info("vision key absent — vision stays off; no env mutation")

    # Load config (env > yaml > defaults)
    config_path = os.environ.get("RECONCILIATION_CONFIG", "config.yaml")
    config = AppConfig.from_yaml(config_path)
    logger.info("config loaded: vision.provider=%s", config.vision.provider)

    # Ensure the base output directory exists
    config.output_dir.mkdir(parents=True, exist_ok=True)

    # In-memory run registry: {run_id → {status, ctx, review_service, ...}}
    run_registry: dict[str, Any] = {}

    # --- Run history: ONE shared adapter on app.state (D1) ---
    # Constructed unconditionally so routes._get_run_history always resolves a
    # single instance (no inline construction in routes.py). The adapter ctor
    # takes no args and cannot fail; only the scan/sweep IO below is guarded.
    import datetime  # noqa: PLC0415

    from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
        JsonManifestRunHistoryAdapter,
    )

    adapter = JsonManifestRunHistoryAdapter()
    app.state.run_history = adapter

    # --- Scan existing run dirs and merge into registry (RH-002, D4) ---
    try:
        entries = adapter.scan(config.output_dir)
        for entry in entries:
            # Registry is empty at startup (no active runs), so this assignment
            # is unconditional — every scanned entry seeds the registry.
            rid = entry["run_id"]
            run_registry[rid] = entry

        logger.info(
            "run_history: startup scan merged %d run entries (output_dir=%s)",
            len(entries), config.output_dir,
        )

        # Lazy 48 h sweep of old error-status runs at startup
        try:
            cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=48)
            deleted_ids = adapter.sweep_failed(config.output_dir, cutoff)
            for rid in deleted_ids:
                run_registry.pop(rid, None)
            if deleted_ids:
                logger.info("run_history: startup sweep removed %d old failed runs", len(deleted_ids))
        except Exception as _sweep_exc:  # noqa: BLE001
            logger.warning("run_history: startup sweep failed (non-fatal): %s", _sweep_exc)

    except Exception as _scan_exc:  # noqa: BLE001
        logger.warning("run_history: startup scan failed (non-fatal): %s", _scan_exc)

    app.state.config = config
    app.state.run_registry = run_registry
    # D2/D3: expose key_store + key_probe so route Depends resolve the same instances.
    app.state.key_store = key_store
    app.state.key_probe = key_probe

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
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    # --- Routes ---
    app.include_router(router, prefix="/api/v1")

    return app


# ---------------------------------------------------------------------------
# Module-level instance (uvicorn entry point)
# ---------------------------------------------------------------------------

app = create_app()
