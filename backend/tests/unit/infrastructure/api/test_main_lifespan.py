"""Tests for lifespan composition-root key injection.

Task 5.1 RED → GREEN (after 5.2 implementation).
Covers VKS-003-S01/S02/S03.
JD-fix MEDIUM-4: setdefault precedence for PROVIDER/BASE_URL/MODEL (explicit env wins);
  ENABLED still force-set; _clean_vision_env saves+restores pre-existing values.

Critical invariants:
- tmp key file present → after lifespan startup inside TestClient context:
    os.environ["RECONCILIATION__VISION__ENABLED"] == "true"
    os.environ["RECONCILIATION__VISION__PROVIDER"] == "ollama"  ← CRITICAL
    app.state.key_store and app.state.key_probe are set
- no key file → env untouched, no fail-fast.
- explicit BASE_URL/MODEL/PROVIDER in env before startup → NOT overwritten by lifespan.
- ENABLED is ALWAYS force-set true when key present (even if operator set it false).
- API key value in env EQUALS the file content (not just non-None).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

_VISION_ENV_KEYS = [
    "RECONCILIATION__VISION__ENABLED",
    "RECONCILIATION__VISION__PROVIDER",
    "RECONCILIATION__VISION__OLLAMA__API_KEY",
    "RECONCILIATION__VISION__OLLAMA__BASE_URL",
    "RECONCILIATION__VISION__OLLAMA__MODEL",
]


@pytest.fixture(autouse=True)
def _clean_vision_env() -> Generator[None, None, None]:
    """Save and restore pre-existing vision env values before/after each test.

    JD-fix LOW-8: the previous implementation unconditionally popped all 5 keys,
    which would destroy pre-existing operator-set values in cross-test pollution.
    Fix: snapshot before, restore after (pop keys that were absent, reset keys
    that were present with their original values).
    """
    # Snapshot current state
    snapshot: dict[str, str | None] = {k: os.environ.get(k) for k in _VISION_ENV_KEYS}
    # Remove any values set by a previous test
    for k in _VISION_ENV_KEYS:
        os.environ.pop(k, None)

    yield

    # Restore to pre-test state
    for k, v in snapshot.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


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
        """RECONCILIATION__VISION__OLLAMA__API_KEY equals the file content (VKS-003-S01).

        JD LOW-8: strengthened from 'is not None' to 'equals file content'.
        The env var must contain exactly what was written to the file, not just any value.
        """
        from unittest.mock import patch  # noqa: PLC0415
        from reconciliation.infrastructure.api.main import create_app  # noqa: PLC0415

        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        expected_key = "specific-key-value-abc"
        (secrets_dir / "vision_api_key").write_text(expected_key, encoding="utf-8")

        app = create_app()
        with patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.scan",
            return_value=[],
        ), patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.sweep_failed",
            return_value=[],
        ), patch.dict(os.environ, {"RECONCILIATION_SECRETS_DIR": str(secrets_dir)}, clear=False):
            with TestClient(app, raise_server_exceptions=True):
                actual = os.environ.get("RECONCILIATION__VISION__OLLAMA__API_KEY")
                assert actual == expected_key, (
                    f"Injected API key must equal file content. "
                    f"Expected: {expected_key!r}, got: {actual!r}"
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


class TestLifespanInjectionPrecedence:
    """Lifespan uses setdefault for PROVIDER/BASE_URL/MODEL (JD MEDIUM-4).

    Explicit operator env values must NOT be overwritten by the lifespan injection.
    ENABLED is still force-set (a present key file is the operator's explicit enable).
    API_KEY is still force-set (the file IS the key — no other source).
    """

    def _make_lifespan_context(
        self,
        tmp_path: Path,
        extra_env: dict[str, str],
    ):  # type: ignore[return]
        """Return a TestClient context with a key file and extra_env pre-set."""
        from unittest.mock import patch  # noqa: PLC0415
        from reconciliation.infrastructure.api.main import create_app  # noqa: PLC0415

        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir(parents=True, exist_ok=True)
        (secrets_dir / "vision_api_key").write_text("file-key-xyz", encoding="utf-8")

        app = create_app()
        env_patch = {
            "RECONCILIATION_SECRETS_DIR": str(secrets_dir),
            **extra_env,
        }
        return app, env_patch, patch

    def test_explicit_base_url_not_overwritten(self, tmp_path: Path) -> None:
        """Explicit OLLAMA__BASE_URL in env survives lifespan injection (setdefault).

        JD RED→GREEN test (MEDIUM-4): the bugged code does
          os.environ["RECONCILIATION__VISION__OLLAMA__BASE_URL"] = "https://ollama.com/v1"
        unconditionally, retargeting any explicit dev-compose BASE_URL to ollama.com.
        Fix: os.environ.setdefault(...) so explicit values are NOT overwritten.
        """
        from unittest.mock import patch  # noqa: PLC0415
        from reconciliation.infrastructure.api.main import create_app  # noqa: PLC0415

        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        (secrets_dir / "vision_api_key").write_text("file-key-xyz", encoding="utf-8")

        explicit_url = "http://localhost:11435/v1"  # dev Ollama compose URL

        app = create_app()
        with patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.scan",
            return_value=[],
        ), patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.sweep_failed",
            return_value=[],
        ), patch.dict(
            os.environ,
            {
                "RECONCILIATION_SECRETS_DIR": str(secrets_dir),
                "RECONCILIATION__VISION__OLLAMA__BASE_URL": explicit_url,
            },
            clear=False,
        ):
            with TestClient(app, raise_server_exceptions=True):
                actual = os.environ.get("RECONCILIATION__VISION__OLLAMA__BASE_URL")
                assert actual == explicit_url, (
                    f"Explicit BASE_URL must not be overwritten by lifespan injection. "
                    f"Expected: {explicit_url!r}, got: {actual!r}. "
                    "Fix: use os.environ.setdefault() instead of direct assignment."
                )

    def test_explicit_model_not_overwritten(self, tmp_path: Path) -> None:
        """Explicit OLLAMA__MODEL in env survives lifespan injection (setdefault)."""
        from unittest.mock import patch  # noqa: PLC0415
        from reconciliation.infrastructure.api.main import create_app  # noqa: PLC0415

        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        (secrets_dir / "vision_api_key").write_text("file-key-xyz", encoding="utf-8")

        explicit_model = "qwen2.5vl:72b"

        app = create_app()
        with patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.scan",
            return_value=[],
        ), patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.sweep_failed",
            return_value=[],
        ), patch.dict(
            os.environ,
            {
                "RECONCILIATION_SECRETS_DIR": str(secrets_dir),
                "RECONCILIATION__VISION__OLLAMA__MODEL": explicit_model,
            },
            clear=False,
        ):
            with TestClient(app, raise_server_exceptions=True):
                actual = os.environ.get("RECONCILIATION__VISION__OLLAMA__MODEL")
                assert actual == explicit_model, (
                    f"Explicit MODEL must not be overwritten. "
                    f"Expected: {explicit_model!r}, got: {actual!r}"
                )

    def test_explicit_provider_not_overwritten(self, tmp_path: Path) -> None:
        """Explicit PROVIDER in env survives lifespan injection (setdefault)."""
        from unittest.mock import patch  # noqa: PLC0415
        from reconciliation.infrastructure.api.main import create_app  # noqa: PLC0415

        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        (secrets_dir / "vision_api_key").write_text("file-key-xyz", encoding="utf-8")

        explicit_provider = "openai"

        app = create_app()
        with patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.scan",
            return_value=[],
        ), patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.sweep_failed",
            return_value=[],
        ), patch.dict(
            os.environ,
            {
                "RECONCILIATION_SECRETS_DIR": str(secrets_dir),
                "RECONCILIATION__VISION__PROVIDER": explicit_provider,
            },
            clear=False,
        ):
            with TestClient(app, raise_server_exceptions=True):
                actual = os.environ.get("RECONCILIATION__VISION__PROVIDER")
                assert actual == explicit_provider, (
                    f"Explicit PROVIDER must not be overwritten. "
                    f"Expected: {explicit_provider!r}, got: {actual!r}"
                )

    def test_enabled_is_force_set_even_when_explicitly_false(self, tmp_path: Path) -> None:
        """ENABLED is force-set true even when operator set it false explicitly.

        A present key file is the operator's intent to enable vision. The lifespan
        must override a false-valued ENABLED when the key file exists.
        This is the OPPOSITE of setdefault — ENABLED must ALWAYS be forced.
        """
        from unittest.mock import patch  # noqa: PLC0415
        from reconciliation.infrastructure.api.main import create_app  # noqa: PLC0415

        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        (secrets_dir / "vision_api_key").write_text("file-key-xyz", encoding="utf-8")

        app = create_app()
        with patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.scan",
            return_value=[],
        ), patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.sweep_failed",
            return_value=[],
        ), patch.dict(
            os.environ,
            {
                "RECONCILIATION_SECRETS_DIR": str(secrets_dir),
                "RECONCILIATION__VISION__ENABLED": "false",  # explicit false — must be overridden
            },
            clear=False,
        ):
            with TestClient(app, raise_server_exceptions=True):
                actual = os.environ.get("RECONCILIATION__VISION__ENABLED")
                assert actual == "true", (
                    f"ENABLED must be force-set 'true' when key file present, "
                    f"even if operator set it 'false'. Got: {actual!r}"
                )
