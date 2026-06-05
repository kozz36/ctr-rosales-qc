"""Tests for ReconciliationTableResponse.errored_guias field + get_table route (REV-E04).

TDD RED phase — these tests MUST fail until T-4 adds errored_guias to the DTO
and wires the get_table route.

Covers:
  - ReconciliationTableResponse.errored_guias defaults to [] (backward-compat)
  - DTO carries errored entries when present
  - GET /table returns errored_guias from the ReviewService
  - Existing rows/unresolved_guias are NOT affected (additive-only gate)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from reconciliation.domain.models import ErroredGuia
from reconciliation.infrastructure.api.schemas import (
    ErroredGuiaResponse,
    ReconciliationTableResponse,
)


# ---------------------------------------------------------------------------
# DTO unit tests
# ---------------------------------------------------------------------------


class TestReconciliationTableResponseErroredGuias:
    def test_defaults_to_empty_list(self) -> None:
        """No errored_guias key in JSON → field defaults to []."""
        resp = ReconciliationTableResponse(run_id="r1", rows=[])
        assert resp.errored_guias == []

    def test_backward_compat_model_validate_no_key(self) -> None:
        """Old payload without errored_guias parses cleanly."""
        payload = {"run_id": "r2", "rows": [], "unresolved_guias": []}
        resp = ReconciliationTableResponse.model_validate(payload)
        assert resp.errored_guias == []

    def test_carries_errored_entries(self) -> None:
        """Payload with errored_guias → entries present on the DTO."""
        eg = ErroredGuiaResponse(
            registro="R001",
            guia_id="T009-0001",
            source_pages=[5],
        )
        resp = ReconciliationTableResponse(run_id="r3", rows=[], errored_guias=[eg])
        assert len(resp.errored_guias) == 1
        assert resp.errored_guias[0].guia_id == "T009-0001"

    def test_serialises_errored_guias_in_model_dump(self) -> None:
        """model_dump includes errored_guias list."""
        eg = ErroredGuiaResponse(
            registro=None,
            guia_id="T009-NULL",
            source_pages=[7, 8],
        )
        resp = ReconciliationTableResponse(run_id="r4", rows=[], errored_guias=[eg])
        dumped = resp.model_dump()
        assert "errored_guias" in dumped
        assert dumped["errored_guias"][0]["guia_id"] == "T009-NULL"


# ---------------------------------------------------------------------------
# Route integration tests
# ---------------------------------------------------------------------------


def _make_test_client(tmp_path) -> TestClient:
    from pathlib import Path  # noqa: PLC0415

    from reconciliation.application.config import AppConfig  # noqa: PLC0415
    from reconciliation.infrastructure.api.main import create_app  # noqa: PLC0415

    app = create_app()
    config = AppConfig(output_dir=Path(tmp_path) / "runs")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    app.state.config = config
    app.state.run_registry = {}
    return TestClient(app, raise_server_exceptions=True)


def _seed_run_with_errored(
    client: TestClient,
    run_id: str,
    errored_guias: list[ErroredGuia] | None = None,
) -> None:
    """Inject a run entry with a mock ReviewService carrying errored_guias."""
    svc = MagicMock()
    svc.rows = []
    svc.guias = []
    svc.errored_guias = list(errored_guias) if errored_guias else []
    registry = client.app.state.run_registry  # type: ignore[attr-defined]
    registry[run_id] = {
        "status": "review",
        "review_service": svc,
        "ctx": None,
        "result": None,
        "vision_calls_made": 0,
        "warnings": [],
        "errored_guias": [],
        "error": None,
    }


class TestGetTableErroredGuias:
    def test_returns_empty_errored_guias_when_none(self, tmp_path) -> None:
        """GET /table → errored_guias: [] when service has none."""
        client = _make_test_client(tmp_path)
        run_id = "tbl-no-errors"
        _seed_run_with_errored(client, run_id, errored_guias=[])

        resp = client.get(f"/api/v1/runs/{run_id}/table")
        assert resp.status_code == 200
        body = resp.json()
        assert "errored_guias" in body
        assert body["errored_guias"] == []

    def test_returns_errored_guias_from_service(self, tmp_path) -> None:
        """GET /table → errored_guias list populated from ReviewService."""
        client = _make_test_client(tmp_path)
        run_id = "tbl-with-errors"
        eg1 = ErroredGuia(registro="R001", guia_id="T009-0001", source_pages=[5])
        eg2 = ErroredGuia(registro=None, guia_id="T009-0002", source_pages=[8, 9])
        _seed_run_with_errored(client, run_id, errored_guias=[eg1, eg2])

        resp = client.get(f"/api/v1/runs/{run_id}/table")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["errored_guias"]) == 2
        ids = {eg["guia_id"] for eg in body["errored_guias"]}
        assert ids == {"T009-0001", "T009-0002"}

    def test_errored_guias_fields_correct(self, tmp_path) -> None:
        """errored_guia entry carries registro, guia_id, source_pages."""
        client = _make_test_client(tmp_path)
        run_id = "tbl-fields"
        eg = ErroredGuia(registro="R007", guia_id="T009-0007", source_pages=[11])
        _seed_run_with_errored(client, run_id, errored_guias=[eg])

        resp = client.get(f"/api/v1/runs/{run_id}/table")
        entry = resp.json()["errored_guias"][0]
        assert entry["registro"] == "R007"
        assert entry["guia_id"] == "T009-0007"
        assert entry["source_pages"] == [11]

    def test_additive_only_rows_unaffected(self, tmp_path) -> None:
        """errored_guias on response must NOT alter existing rows list."""
        from datetime import date  # noqa: PLC0415
        from decimal import Decimal  # noqa: PLC0415

        from reconciliation.domain.models import (  # noqa: PLC0415
            GuiaContribution,
            ReconciliationRow,
        )

        client = _make_test_client(tmp_path)
        run_id = "tbl-additive"

        contrib = GuiaContribution(
            guia_id="G001",
            source_pages=[1],
            cantidad=Decimal("5.0"),
            unidad="TN",
            confidence=0.9,
            identity_source="qr",
        )
        row = ReconciliationRow(
            registro="R001",
            fecha=date(2025, 5, 1),
            material_canonical="barra 3/8",
            unidad="TN",
            declared_qty=Decimal("5.0"),
            delta=Decimal("0"),
            status="MATCH",
            source_pages=[1],
            min_confidence=0.9,
            guias=[contrib],
        )

        svc = MagicMock()
        svc.rows = [row]
        svc.guias = []
        svc.errored_guias = [ErroredGuia(registro="R002", guia_id="EG001", source_pages=[99])]

        registry = client.app.state.run_registry  # type: ignore[attr-defined]
        registry[run_id] = {
            "status": "review",
            "review_service": svc,
            "ctx": None,
            "result": None,
            "vision_calls_made": 0,
            "warnings": [],
            "errored_guias": [],
            "error": None,
        }

        resp = client.get(f"/api/v1/runs/{run_id}/table")
        assert resp.status_code == 200
        body = resp.json()
        # Row count must be 1 (unchanged)
        assert len(body["rows"]) == 1
        assert body["rows"][0]["registro"] == "R001"
        # errored_guias populated additively
        assert len(body["errored_guias"]) == 1
