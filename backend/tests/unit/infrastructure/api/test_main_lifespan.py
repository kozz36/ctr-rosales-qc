"""Tests for lifespan composition-root key injection.

Task 5.1 RED → GREEN (after 5.2 implementation).
Covers VKS-003-S01/S02/S03.

Critical invariants:
- tmp key file present → after lifespan startup inside TestClient context:
    os.environ["RECONCILIATION__VISION__ENABLED"] == "true"
    os.environ["RECONCILIATION__VISION__PROVIDER"] == "ollama"  ← CRITICAL
    app.state.key_store and app.state.key_probe are set
- no key file → env untouched, no fail-fast.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient


def _strip_injected_env() -> None:
    """Remove the 5 env vars the lifespan may inject, to restore a clean state."""
    for key in [
        "RECONCILIATION__VISION__ENABLED",
        "RECONCILIATION__VISION__PROVIDER",
        "RECONCILIATION__VISION__OLLAMA__API_KEY",
        "RECONCILIATION__VISION__OLLAMA__BASE_URL",
        "RECONCILIATION__VISION__OLLAMA__MODEL",
    ]:
        os.environ.pop(key, None)


@pytest.fixture(autouse=True)
def _clean_vision_env() -> Generator[None, None, None]:
    """Remove any vision env vars injected by lifespan before/after each test."""
    _strip_injected_env()
    yield
    _strip_injected_env()


class TestLifespanWithKeyFile:
    """Lifespan sets os.environ when key file is present (VKS-003-S01)."""

    def test_env_vision_enabled_set_to_true(self, tmp_path: Path) -> None:
        """RECONCILIATION__VISION__ENABLED='true' when key file present."""
        from unittest.mock import patch  # noqa: PLC0415
        from reconciliation.infrastructure.api.main import create_app  # noqa: PLC0415

        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        (secrets_dir / "vision_api_key").write_text("test-key-xyz", encoding="utf-8")

        app = create_app()
        with patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.scan",
            return_value=[],
        ), patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.sweep_failed",
            return_value=[],
        ), patch.dict(os.environ, {"RECONCILIATION_SECRETS_DIR": str(secrets_dir)}, clear=False):
            with TestClient(app, raise_server_exceptions=True):
                assert os.environ.get("RECONCILIATION__VISION__ENABLED") == "true", (
                    "RECONCILIATION__VISION__ENABLED must be 'true' when key file present"
                )

    def test_env_vision_provider_set_to_ollama(self, tmp_path: Path) -> None:
        """RECONCILIATION__VISION__PROVIDER='ollama' (CRITICAL — default is anthropic)."""
        from unittest.mock import patch  # noqa: PLC0415
        from reconciliation.infrastructure.api.main import create_app  # noqa: PLC0415

        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        (secrets_dir / "vision_api_key").write_text("test-key-xyz", encoding="utf-8")

        app = create_app()
        with patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.scan",
            return_value=[],
        ), patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.sweep_failed",
            return_value=[],
        ), patch.dict(os.environ, {"RECONCILIATION_SECRETS_DIR": str(secrets_dir)}, clear=False):
            with TestClient(app, raise_server_exceptions=True):
                assert os.environ.get("RECONCILIATION__VISION__PROVIDER") == "ollama", (
                    "RECONCILIATION__VISION__PROVIDER must be 'ollama' — not 'anthropic'"
                )

    def test_key_store_and_probe_on_app_state(self, tmp_path: Path) -> None:
        """app.state.key_store and app.state.key_probe are set after startup."""
        from unittest.mock import patch  # noqa: PLC0415
        from reconciliation.infrastructure.api.main import create_app  # noqa: PLC0415

        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        (secrets_dir / "vision_api_key").write_text("test-key-xyz", encoding="utf-8")

        app = create_app()
        with patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.scan",
            return_value=[],
        ), patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.sweep_failed",
            return_value=[],
        ), patch.dict(os.environ, {"RECONCILIATION_SECRETS_DIR": str(secrets_dir)}, clear=False):
            with TestClient(app, raise_server_exceptions=True):
                assert hasattr(app.state, "key_store"), "app.state.key_store must be set"
                assert hasattr(app.state, "key_probe"), "app.state.key_probe must be set"

    def test_env_ollama_api_key_set(self, tmp_path: Path) -> None:
        """RECONCILIATION__VISION__OLLAMA__API_KEY is set when key present (VKS-003-S01)."""
        from unittest.mock import patch  # noqa: PLC0415
        from reconciliation.infrastructure.api.main import create_app  # noqa: PLC0415

        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        (secrets_dir / "vision_api_key").write_text("specific-key-value-abc", encoding="utf-8")

        app = create_app()
        with patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.scan",
            return_value=[],
        ), patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.sweep_failed",
            return_value=[],
        ), patch.dict(os.environ, {"RECONCILIATION_SECRETS_DIR": str(secrets_dir)}, clear=False):
            with TestClient(app, raise_server_exceptions=True):
                assert os.environ.get("RECONCILIATION__VISION__OLLAMA__API_KEY") is not None, (
                    "Ollama API key env var must be set"
                )


class TestLifespanWithoutKeyFile:
    """Lifespan does NOT mutate env when key file is absent (VKS-003-S02)."""

    def test_no_env_mutation_when_no_key_file(self, tmp_path: Path) -> None:
        """RECONCILIATION__VISION__ENABLED is NOT set to 'true' when no key file."""
        from unittest.mock import patch  # noqa: PLC0415
        from reconciliation.infrastructure.api.main import create_app  # noqa: PLC0415

        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        # No key file written

        app = create_app()
        with patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.scan",
            return_value=[],
        ), patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.sweep_failed",
            return_value=[],
        ), patch.dict(os.environ, {"RECONCILIATION_SECRETS_DIR": str(secrets_dir)}, clear=False):
            with TestClient(app, raise_server_exceptions=True):
                actual = os.environ.get("RECONCILIATION__VISION__ENABLED")
                assert actual != "true", (
                    f"Vision env must NOT be 'true' without key file, got: {actual!r}"
                )

    def test_no_fail_fast_when_no_key_file(self, tmp_path: Path) -> None:
        """Keyless startup succeeds — no fail-fast triggered (VKS-003-S02)."""
        from unittest.mock import patch  # noqa: PLC0415
        from reconciliation.infrastructure.api.main import create_app  # noqa: PLC0415

        secrets_dir = tmp_path / "no-such-dir"  # dir doesn't even exist → graceful

        app = create_app()
        # Should not raise any exception — use `with` to trigger the lifespan
        with patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.scan",
            return_value=[],
        ), patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.sweep_failed",
            return_value=[],
        ), patch.dict(os.environ, {"RECONCILIATION_SECRETS_DIR": str(secrets_dir)}, clear=False):
            with TestClient(app, raise_server_exceptions=True) as client:
                # GET /capabilities must succeed with the default vision-off config
                resp = client.get("/api/v1/capabilities")
                assert resp.status_code == 200
