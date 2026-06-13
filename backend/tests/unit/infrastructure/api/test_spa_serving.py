"""SPA serving tests — slice 1 of Windows native installer.

Covers RECONCILIATION_SPA_DIR env-gated static mount wired in create_app().
All six contracts from docs/WINDOWS-INSTALLER.md §2.2:

  S1: GET /          → 200, text/html, body contains fake index.html marker
  S2: GET /historial  → 200, index.html fallback (no such file on disk)
  S3: GET /assets/app.js → 200, asset content, js content-type
  S4: GET /api/v1/<unknown> → 404 JSON (fallback must NOT intercept API paths)
  S5: GET /api/v1/runs (existing route) → NOT swallowed by SPA fallback
  S6: SPA dir UNSET → GET / behaves as today (no fallback); existing API still works

TDD: tests were written RED first (spa.py did not exist); then GREEN after implementation.

Design note: RECONCILIATION_SPA_DIR is read at create_app() time (the mount must be
wired before the app starts accepting connections).  Tests therefore set the env var
BEFORE calling create_app(), then restore it afterwards via monkeypatch.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_SCAN_PATCH = "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.scan"
_SWEEP_PATCH = "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.sweep_failed"

INDEX_MARKER = "<!-- spa-test-index-marker -->"


def _build_spa_dir(tmp_path: Path) -> Path:
    """Create a minimal fake SPA directory with index.html + assets/app.js."""
    spa = tmp_path / "spa"
    spa.mkdir()
    (spa / "index.html").write_text(
        f"<html><body>{INDEX_MARKER}</body></html>", encoding="utf-8"
    )
    assets = spa / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('spa-app-js');", encoding="utf-8")
    return spa


def _make_spa_client(spa_dir: Path) -> TestClient:
    """Build an app with RECONCILIATION_SPA_DIR set and return a live TestClient.

    IMPORTANT: the env var must be set BEFORE create_app() because mount_spa()
    reads it at factory time (routes are wired at app construction, not at
    request time).  We set the env var, build the app, then clean up.
    """
    from reconciliation.infrastructure.api.main import create_app  # noqa: PLC0415

    # Set env BEFORE create_app so mount_spa() sees it.
    prev = os.environ.get("RECONCILIATION_SPA_DIR")
    os.environ["RECONCILIATION_SPA_DIR"] = str(spa_dir)
    try:
        app = create_app()
    finally:
        # Restore immediately after construction; the mounted routes persist on app.
        if prev is None:
            os.environ.pop("RECONCILIATION_SPA_DIR", None)
        else:
            os.environ["RECONCILIATION_SPA_DIR"] = prev

    return TestClient(app, raise_server_exceptions=True)


def _make_no_spa_client() -> TestClient:
    """Build an app WITHOUT SPA mount (env var absent)."""
    from reconciliation.infrastructure.api.main import create_app  # noqa: PLC0415

    os.environ.pop("RECONCILIATION_SPA_DIR", None)
    app = create_app()
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# S1 — GET / returns index.html when SPA dir is set
# ---------------------------------------------------------------------------


class TestSpaRootRoute:
    """S1: GET / → 200, text/html, body contains the index.html marker."""

    def test_root_returns_index_html_content(self, tmp_path: Path) -> None:
        spa_dir = _build_spa_dir(tmp_path)
        with (
            patch(_SCAN_PATCH, return_value=[]),
            patch(_SWEEP_PATCH, return_value=[]),
            _make_spa_client(spa_dir) as client,
        ):
            resp = client.get("/")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        assert "text/html" in resp.headers["content-type"]
        assert INDEX_MARKER in resp.text, "Response body must contain index.html marker"

    def test_root_returns_200_status(self, tmp_path: Path) -> None:
        spa_dir = _build_spa_dir(tmp_path)
        with (
            patch(_SCAN_PATCH, return_value=[]),
            patch(_SWEEP_PATCH, return_value=[]),
            _make_spa_client(spa_dir) as client,
        ):
            resp = client.get("/")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# S2 — GET /historial (no such file on disk) → 200 index.html fallback
# ---------------------------------------------------------------------------


class TestSpaHistoryFallback:
    """S2: deep SPA route returns index.html (no such file → fallback)."""

    def test_historial_returns_index_html(self, tmp_path: Path) -> None:
        spa_dir = _build_spa_dir(tmp_path)
        with (
            patch(_SCAN_PATCH, return_value=[]),
            patch(_SWEEP_PATCH, return_value=[]),
            _make_spa_client(spa_dir) as client,
        ):
            resp = client.get("/historial")
        assert resp.status_code == 200, f"Expected 200 fallback, got {resp.status_code}"
        assert "text/html" in resp.headers["content-type"]
        assert INDEX_MARKER in resp.text

    def test_deep_spa_route_fallback(self, tmp_path: Path) -> None:
        """An arbitrary deep SPA client route also returns index.html."""
        spa_dir = _build_spa_dir(tmp_path)
        with (
            patch(_SCAN_PATCH, return_value=[]),
            patch(_SWEEP_PATCH, return_value=[]),
            _make_spa_client(spa_dir) as client,
        ):
            resp = client.get("/registros/232/detalle")
        assert resp.status_code == 200
        assert INDEX_MARKER in resp.text


# ---------------------------------------------------------------------------
# S3 — GET /assets/app.js → 200, asset content, JS content-type
# ---------------------------------------------------------------------------


class TestSpaAssetServing:
    """S3: real asset files under /assets/* are served from disk."""

    def test_asset_js_returns_200(self, tmp_path: Path) -> None:
        spa_dir = _build_spa_dir(tmp_path)
        with (
            patch(_SCAN_PATCH, return_value=[]),
            patch(_SWEEP_PATCH, return_value=[]),
            _make_spa_client(spa_dir) as client,
        ):
            resp = client.get("/assets/app.js")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

    def test_asset_js_content_type_is_javascript(self, tmp_path: Path) -> None:
        spa_dir = _build_spa_dir(tmp_path)
        with (
            patch(_SCAN_PATCH, return_value=[]),
            patch(_SWEEP_PATCH, return_value=[]),
            _make_spa_client(spa_dir) as client,
        ):
            resp = client.get("/assets/app.js")
        ct = resp.headers.get("content-type", "")
        assert "javascript" in ct or "text/" in ct, (
            f"Expected JS-ish content-type for .js asset, got: {ct!r}"
        )

    def test_asset_js_body_contains_content(self, tmp_path: Path) -> None:
        spa_dir = _build_spa_dir(tmp_path)
        with (
            patch(_SCAN_PATCH, return_value=[]),
            patch(_SWEEP_PATCH, return_value=[]),
            _make_spa_client(spa_dir) as client,
        ):
            resp = client.get("/assets/app.js")
        assert "spa-app-js" in resp.text, "Asset body must contain the written content"


# ---------------------------------------------------------------------------
# S4 — GET /api/v1/<unknown> → 404 JSON (NOT intercepted by SPA fallback)
# ---------------------------------------------------------------------------


class TestSpaApiNotIntercepted:
    """S4: Unknown API paths return 404 JSON — not swallowed by the SPA fallback."""

    def test_unknown_api_path_returns_404_json(self, tmp_path: Path) -> None:
        spa_dir = _build_spa_dir(tmp_path)
        with (
            patch(_SCAN_PATCH, return_value=[]),
            patch(_SWEEP_PATCH, return_value=[]),
            _make_spa_client(spa_dir) as client,
        ):
            resp = client.get("/api/v1/nonexistent-endpoint-xyz")
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"
        ct = resp.headers.get("content-type", "")
        assert "application/json" in ct, (
            f"404 for unknown API path must be JSON, not HTML. content-type: {ct!r}"
        )
        assert INDEX_MARKER not in resp.text, (
            "SPA fallback must NOT intercept /api/v1/* paths"
        )

    def test_unknown_api_path_body_is_not_index_html(self, tmp_path: Path) -> None:
        spa_dir = _build_spa_dir(tmp_path)
        with (
            patch(_SCAN_PATCH, return_value=[]),
            patch(_SWEEP_PATCH, return_value=[]),
            _make_spa_client(spa_dir) as client,
        ):
            # A non-existent run_id under a real parametric route also must not return HTML
            resp = client.get("/api/v1/runs/00000000-0000-0000-0000-000000000000")
        # 404 from the API — must not be index.html
        assert INDEX_MARKER not in resp.text, (
            "SPA fallback must NOT intercept /api/v1/runs/<nonexistent-id>"
        )


# ---------------------------------------------------------------------------
# S5 — GET /api/v1/runs (existing route) → still routes to API, not SPA
# ---------------------------------------------------------------------------


class TestSpaExistingApiUnaffected:
    """S5: Existing /api/v1/runs route is not intercepted by the SPA mount."""

    def test_existing_runs_route_not_spa(self, tmp_path: Path) -> None:
        spa_dir = _build_spa_dir(tmp_path)
        with (
            patch(_SCAN_PATCH, return_value=[]),
            patch(_SWEEP_PATCH, return_value=[]),
            _make_spa_client(spa_dir) as client,
        ):
            resp = client.get("/api/v1/runs")
        # The route exists and returns 200 JSON (empty list is fine)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        ct = resp.headers.get("content-type", "")
        assert "application/json" in ct, (
            f"/api/v1/runs must return JSON, not HTML. content-type: {ct!r}"
        )
        assert INDEX_MARKER not in resp.text, (
            "SPA fallback must NOT intercept /api/v1/runs"
        )


# ---------------------------------------------------------------------------
# S6 — SPA dir UNSET → behaves as today (no fallback); existing API still works
# ---------------------------------------------------------------------------


class TestSpaDisabledWhenEnvUnset:
    """S6: When RECONCILIATION_SPA_DIR is unset, the SPA is not mounted."""

    def test_root_not_spa_when_env_unset(self) -> None:
        """GET / does not return SPA content when RECONCILIATION_SPA_DIR is not set."""
        with (
            patch(_SCAN_PATCH, return_value=[]),
            patch(_SWEEP_PATCH, return_value=[]),
            _make_no_spa_client() as client,
        ):
            resp = client.get("/")
        # Without SPA mount, / is not a defined route → 404 JSON (not index.html)
        assert resp.status_code != 200 or INDEX_MARKER not in resp.text, (
            "When RECONCILIATION_SPA_DIR is unset, / must NOT return SPA index.html"
        )

    def test_existing_api_route_still_works_when_spa_unset(self) -> None:
        """Existing /api/v1/runs still works when RECONCILIATION_SPA_DIR is unset."""
        with (
            patch(_SCAN_PATCH, return_value=[]),
            patch(_SWEEP_PATCH, return_value=[]),
            _make_no_spa_client() as client,
        ):
            resp = client.get("/api/v1/runs")
        assert resp.status_code == 200
        assert "application/json" in resp.headers.get("content-type", "")

    def test_spa_not_mounted_when_dir_missing(self, tmp_path: Path) -> None:
        """If RECONCILIATION_SPA_DIR points to non-existent dir, SPA is not mounted."""
        from reconciliation.infrastructure.api.main import create_app  # noqa: PLC0415

        nonexistent = str(tmp_path / "does-not-exist")
        prev = os.environ.get("RECONCILIATION_SPA_DIR")
        os.environ["RECONCILIATION_SPA_DIR"] = nonexistent
        try:
            app = create_app()
        finally:
            if prev is None:
                os.environ.pop("RECONCILIATION_SPA_DIR", None)
            else:
                os.environ["RECONCILIATION_SPA_DIR"] = prev

        with (
            patch(_SCAN_PATCH, return_value=[]),
            patch(_SWEEP_PATCH, return_value=[]),
            TestClient(app, raise_server_exceptions=True) as client,
        ):
            resp = client.get("/api/v1/runs")
        assert resp.status_code == 200, (
            "API must still work when SPA dir is set but missing"
        )
