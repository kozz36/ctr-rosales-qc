"""Failing tests for GET /api/v1/capabilities endpoint.

Task 4.1 RED → GREEN (after 4.3 implementation).
Covers CAP-001-S01 through S04.

Pattern: set app.state directly before TestClient creation, bypassing lifespan.
Mirrors the existing test_api_routes.py fixture approach.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from reconciliation.infrastructure.api.main import create_app


def _make_config(vision_enabled: bool = False, sunat_enabled: bool = True) -> Any:
    """Build a minimal AppConfig stub."""
    cfg = MagicMock()
    cfg.vision.enabled = vision_enabled
    cfg.sunat.enabled = sunat_enabled
    return cfg


def _make_client(vision_enabled: bool = False, sunat_enabled: bool = True) -> TestClient:
    """Build a TestClient with stubbed app.state (no lifespan IO)."""
    from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
        JsonManifestRunHistoryAdapter,
    )

    app = create_app()
    app.state.config = _make_config(vision_enabled=vision_enabled, sunat_enabled=sunat_enabled)
    app.state.run_registry = {}
    app.state.run_history = JsonManifestRunHistoryAdapter()
    # key_store and key_probe not needed for GET /capabilities but set to avoid
    # AttributeError from _get_key_store / _get_key_probe if hit incidentally.
    app.state.key_store = MagicMock()
    app.state.key_probe = MagicMock()
    return TestClient(app, raise_server_exceptions=True)


class TestCapabilitiesRoute:
    """GET /api/v1/capabilities — CAP-001."""

    def test_capabilities_vision_off_sunat_on(self) -> None:
        """Vision off + SUNAT on → 200 {'vision_enabled': false, 'sunat_enabled': true} (CAP-001-S01)."""
        client = _make_client(vision_enabled=False, sunat_enabled=True)
        resp = client.get("/api/v1/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert data["vision_enabled"] is False
        assert data["sunat_enabled"] is True

    def test_capabilities_vision_on_sunat_on(self) -> None:
        """Vision on + SUNAT on → 200 {'vision_enabled': true, 'sunat_enabled': true} (CAP-001-S02)."""
        client = _make_client(vision_enabled=True, sunat_enabled=True)
        resp = client.get("/api/v1/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert data["vision_enabled"] is True
        assert data["sunat_enabled"] is True

    def test_capabilities_response_keys_exactly_two(self) -> None:
        """Response body keys == {'vision_enabled', 'sunat_enabled'} exactly (CAP-001-S03)."""
        client = _make_client()
        resp = client.get("/api/v1/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"vision_enabled", "sunat_enabled"}, (
            f"Response must have exactly 2 keys, got: {set(data.keys())}"
        )

    def test_capabilities_no_active_run_returns_200(self) -> None:
        """No active run → 200, no error (CAP-001-S04)."""
        client = _make_client()
        resp = client.get("/api/v1/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert "detail" not in data
