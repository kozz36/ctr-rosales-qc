"""T6 / REV-R16 — POST .../errored-guias/{guia_id}/reprocess async endpoint.

Strict-TDD: tests written FIRST (RED).

Covers:
- 200 with recovered=True when apply_reprocess succeeds.
- 200 with recovered=False + reason="vision_empty" when vision returns no lines.
- 404 when guia_id not in errored_guias.
- 503 when reprocess_service is None (both disabled).
- 404 when run_id not found.
- ReprocessGuiaResponse schema has the expected fields.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from reconciliation.application.config import AppConfig
from reconciliation.application.reprocess_service import ReprocessResult
from reconciliation.domain.models import ErroredGuia


def _make_errored(
    guia_id: str = "T227-0001",
    retry_attempted: bool = True,
    registro: str | None = "227",
) -> ErroredGuia:
    return ErroredGuia(
        guia_id=guia_id,
        registro=registro,
        source_pages=[10],
        retry_attempted=retry_attempted,
    )


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    """TestClient with fake config and empty run registry."""
    from reconciliation.infrastructure.api.main import create_app  # noqa: PLC0415

    app = create_app()
    config = AppConfig(output_dir=tmp_path / "runs")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    app.state.config = config
    app.state.run_registry = {}
    return TestClient(app, raise_server_exceptions=True)


def _seed_run(
    client: TestClient,
    run_id: str,
    reprocess_service: object | None,
    errored_guias: list[ErroredGuia] | None = None,
) -> MagicMock:
    """Inject a review run entry into the registry; returns the review_service mock."""
    review_service = MagicMock()
    review_service.errored_guias = errored_guias or []
    review_service.rows = []

    registry = client.app.state.run_registry  # type: ignore[attr-defined]
    registry[run_id] = {
        "status": "review",
        "review_service": review_service,
        "reprocess_service": reprocess_service,
        "ctx": None,
        "result": None,
        "vision_calls_made": 0,
        "warnings": [],
        "errored_guias": errored_guias or [],
    }
    return review_service


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestReprocessGuiaSchema:
    def test_schema_imported(self) -> None:
        from reconciliation.infrastructure.api.schemas import (  # noqa: PLC0415
            ReprocessGuiaResponse,
        )

        r = ReprocessGuiaResponse(run_id="r1", guia_id="g1", recovered=True)
        assert r.recovered is True
        assert r.run_id == "r1"
        assert r.rows == []
        assert r.errored_guias == []

    def test_recovered_false_has_reason(self) -> None:
        from reconciliation.infrastructure.api.schemas import (  # noqa: PLC0415
            ReprocessGuiaResponse,
        )

        r = ReprocessGuiaResponse(
            run_id="r1",
            guia_id="g1",
            recovered=False,
            reason="vision_empty",
        )
        assert r.reason == "vision_empty"


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------


class TestReprocessEndpoint:
    def test_returns_200_recovered_true(self, client: TestClient) -> None:
        """Successful vision recovery → 200 with recovered=True."""
        errored = [_make_errored()]
        fake_service = MagicMock()
        fake_service.apply_reprocess = AsyncMock(
            return_value=ReprocessResult(
                recovered=True,
                guia_id="T227-0001",
                rows=[],
            )
        )
        _seed_run(client, "test-run", fake_service, errored)

        resp = client.post("/api/v1/runs/test-run/errored-guias/T227-0001/reprocess")
        assert resp.status_code == 200
        data = resp.json()
        assert data["recovered"] is True
        assert data["guia_id"] == "T227-0001"

    def test_returns_200_vision_empty(self, client: TestClient) -> None:
        """Vision returns empty → 200 with recovered=False + reason=vision_empty."""
        errored = [_make_errored()]
        fake_service = MagicMock()
        fake_service.apply_reprocess = AsyncMock(
            return_value=ReprocessResult(
                recovered=False,
                guia_id="T227-0001",
                reason="vision_empty",
            )
        )
        _seed_run(client, "test-run", fake_service, errored)

        resp = client.post("/api/v1/runs/test-run/errored-guias/T227-0001/reprocess")
        assert resp.status_code == 200
        data = resp.json()
        assert data["recovered"] is False
        assert data["reason"] == "vision_empty"

    def test_returns_404_when_guia_not_found(self, client: TestClient) -> None:
        """guia_id not in errored_guias → 404."""
        fake_service = MagicMock()
        _seed_run(client, "test-run", fake_service, [])

        resp = client.post("/api/v1/runs/test-run/errored-guias/UNKNOWN-9999/reprocess")
        assert resp.status_code == 404

    def test_returns_503_when_service_none(self, client: TestClient) -> None:
        """reprocess_service is None → 503."""
        _seed_run(client, "test-run", None, [_make_errored()])

        resp = client.post("/api/v1/runs/test-run/errored-guias/T227-0001/reprocess")
        assert resp.status_code == 503

    def test_returns_503_vision_disabled_when_null_vision_adapter(
        self, client: TestClient
    ) -> None:
        """vision.enabled=False (NullVisionAdapter injected) → 503 vision_disabled.

        REV-R16-S03 / REV-R17-S03: a service whose vision port is a
        NullVisionAdapter MUST surface 503 vision_disabled, NOT 200 vision_empty —
        the disabled state must not be masked as an empty read.
        """
        from reconciliation.adapters.vision.null_vision import (  # noqa: PLC0415
            NullVisionAdapter,
        )

        fake_service = MagicMock()
        fake_service._vision = NullVisionAdapter()
        # apply_reprocess must NOT be awaited — the gate fires before it.
        fake_service.apply_reprocess = AsyncMock(
            return_value=ReprocessResult(
                recovered=False, guia_id="T227-0001", reason="vision_empty"
            )
        )
        _seed_run(client, "test-run", fake_service, [_make_errored()])

        resp = client.post("/api/v1/runs/test-run/errored-guias/T227-0001/reprocess")
        assert resp.status_code == 503
        assert "vision_disabled" in resp.json()["detail"]
        fake_service.apply_reprocess.assert_not_awaited()

    def test_vision_enabled_empty_still_200_vision_empty(
        self, client: TestClient
    ) -> None:
        """Regression guard: vision ENABLED but returns [] → STILL 200 vision_empty.

        REV-R16-S02: vision-disabled (503) and vision-enabled-empty-result
        (200 vision_empty) MUST stay distinct. A real (non-Null) vision adapter
        that yields no lines is a legitimate empty read, not a disabled state.
        """
        fake_service = MagicMock()
        fake_service._vision = MagicMock()  # a real (non-Null) vision port
        fake_service.apply_reprocess = AsyncMock(
            return_value=ReprocessResult(
                recovered=False, guia_id="T227-0001", reason="vision_empty"
            )
        )
        _seed_run(client, "test-run", fake_service, [_make_errored()])

        resp = client.post("/api/v1/runs/test-run/errored-guias/T227-0001/reprocess")
        assert resp.status_code == 200
        data = resp.json()
        assert data["recovered"] is False
        assert data["reason"] == "vision_empty"

    def test_returns_404_when_run_not_found(self, client: TestClient) -> None:
        """Unknown run_id → 404."""
        resp = client.post("/api/v1/runs/no-such-run/errored-guias/T227-0001/reprocess")
        assert resp.status_code == 404
