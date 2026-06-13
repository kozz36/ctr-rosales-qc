"""SPA static-file mount for the Windows native installer (slice 1).

Activates only when ``RECONCILIATION_SPA_DIR`` env var points at a built
Vue SPA directory (containing ``index.html`` + ``assets/``).  When unset or
the directory is missing the function is a no-op — Docker/dev behavior is
completely preserved.

Contract (docs/WINDOWS-INSTALLER.md §2.2):
- Real asset files under ``/assets/*`` (and ``/favicon.ico`` etc.) are served
  from disk with the correct content-type via Starlette ``StaticFiles``.
- Any OTHER non-API path (``/``, ``/historial``, arbitrary deep client routes
  that have no matching file on disk) returns ``index.html`` with 200 +
  ``text/html`` (history-mode fallback via a catch-all ``Route``).
- Paths beginning with ``/api/``, ``/docs``, ``/redoc``, ``/openapi.json``
  are NEVER intercepted — an unknown ``/api/v1/<x>`` still returns the normal
  FastAPI 404 JSON, not ``index.html``.

Architecture invariants:
- Infrastructure/API layer only.  Zero domain or application imports.
- No new heavy top-level imports; ``StaticFiles`` and ``FileResponse`` are
  already transitive Starlette deps (already present in the FastAPI install).
- The SPA mount is appended AFTER ``app.include_router(router, prefix="/api/v1")``,
  so the API router always takes precedence.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Prefixes that must NEVER be swallowed by the SPA catch-all fallback.
# The comparison is done on the raw path string, so ``/api/`` covers
# ``/api/v1/...`` etc.
_API_PREFIXES: tuple[str, ...] = (
    "/api/",
    "/docs",
    "/redoc",
    "/openapi.json",
)


def mount_spa(app: object) -> None:  # app: FastAPI — avoid import cycle
    """Conditionally wire SPA serving onto *app*.

    Reads ``RECONCILIATION_SPA_DIR`` from the environment at call time
    (inside ``create_app()``).  If the var is unset or the directory does not
    exist, this function is a no-op.

    Must be called AFTER ``app.include_router(router, prefix="/api/v1")`` so
    that all API routes are registered first and take priority.
    """
    raw = os.environ.get("RECONCILIATION_SPA_DIR", "")
    if not raw:
        logger.debug("RECONCILIATION_SPA_DIR unset — SPA mount skipped")
        return

    spa_dir = Path(raw)
    if not spa_dir.is_dir():
        logger.warning(
            "RECONCILIATION_SPA_DIR=%r is not a directory — SPA mount skipped", raw
        )
        return

    index_html = spa_dir / "index.html"
    if not index_html.is_file():
        logger.warning(
            "RECONCILIATION_SPA_DIR=%r has no index.html — SPA mount skipped", raw
        )
        return

    # -----------------------------------------------------------------------
    # Lazy imports (Starlette is already a FastAPI dep; these are fine at
    # module top but kept local to stay consistent with the lazy-import
    # convention used by other adapters in this codebase).
    # -----------------------------------------------------------------------
    from fastapi import FastAPI  # noqa: PLC0415
    from fastapi.responses import FileResponse  # noqa: PLC0415
    from fastapi.routing import APIRoute  # noqa: PLC0415
    from starlette.requests import Request  # noqa: PLC0415
    from starlette.routing import Mount  # noqa: PLC0415
    from starlette.staticfiles import StaticFiles  # noqa: PLC0415

    assert isinstance(app, FastAPI)  # type-narrowing; never fails in practice

    # ------------------------------------------------------------------
    # 1. Mount the assets directory as a StaticFiles sub-application so
    #    ``/assets/<file>`` is served with the correct content-type.
    #    The path under the SPA dir is ``assets/``; it may not exist in a
    #    minimal build — skip gracefully.
    # ------------------------------------------------------------------
    assets_dir = spa_dir / "assets"
    if assets_dir.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=str(assets_dir)),
            name="spa_assets",
        )
        logger.debug("SPA assets mounted at /assets from %s", assets_dir)

    # ------------------------------------------------------------------
    # 2. Mount favicon.ico if present (served as a real file, not fallback).
    # ------------------------------------------------------------------
    favicon = spa_dir / "favicon.ico"
    if favicon.is_file():
        from starlette.responses import FileResponse as _FR  # noqa: PLC0415

        @app.get("/favicon.ico", include_in_schema=False)
        async def _favicon() -> _FR:  # type: ignore[return]
            return _FR(str(favicon))

    # ------------------------------------------------------------------
    # 3. Catch-all fallback: any path that is NOT under a protected API
    #    prefix and has no real file on disk returns index.html.
    #    Registered with a low-priority catch-all path ``/{full_path:path}``.
    # ------------------------------------------------------------------
    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str) -> FileResponse:  # type: ignore[return]
        # Guard: never intercept API / docs / openapi paths.
        # The ``full_path`` param does NOT include the leading slash that was
        # already consumed by path routing — we prepend it for the check.
        request_path = "/" + full_path
        for prefix in _API_PREFIXES:
            if request_path.startswith(prefix):
                # Let FastAPI return its normal 404 JSON.
                from fastapi import HTTPException  # noqa: PLC0415

                raise HTTPException(status_code=404)

        # Serve a real file if it exists directly under spa_dir (e.g. robots.txt).
        candidate = spa_dir / full_path
        if candidate.is_file():
            return FileResponse(str(candidate))

        # Everything else → index.html (SPA history-mode fallback).
        return FileResponse(str(index_html), media_type="text/html")

    logger.info(
        "SPA mount active: assets=%s, fallback → %s",
        assets_dir if assets_dir.is_dir() else "(none)",
        index_html,
    )
