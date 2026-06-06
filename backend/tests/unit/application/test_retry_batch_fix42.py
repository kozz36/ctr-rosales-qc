"""Fix #42 — _retry_batch MUST call mark_retry_attempted per guía on FAILED retry.

REV-R26 / D5: the per-registro batch REINTENTAR path (POST .../registros/{registro}/retry)
was missing the mark_retry_attempted call, unlike the per-guía path which correctly
calls it on apply_retry failure.  This test asserts the invariant so the bug cannot
regress silently.

Strict-TDD: failing tests written FIRST (RED). The fix is in routes.py _retry_batch.
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest
from fastapi.testclient import TestClient

from reconciliation.application.config import AppConfig
from reconciliation.application.reprocess_service import RetryResult
from reconciliation.domain.models import ErroredGuia


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_errored(
    guia_id: str,
    registro: str = "R001",
) -> ErroredGuia:
    return ErroredGuia(registro=registro, guia_id=guia_id, source_pages=[1])


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
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
    reprocess_service: object,
    errored_guias: list[ErroredGuia],
) -> MagicMock:
    """Inject a run with real review_service mock (using real errored_guias list)."""
    review_svc = MagicMock()
    review_svc.errored_guias = errored_guias

    registry = client.app.state.run_registry  # type: ignore[attr-defined]
    registry[run_id] = {
        "status": "review",
        "review_service": review_svc,
        "reprocess_service": reprocess_service,
        "ctx": None,
        "result": None,
        "vision_calls_made": 0,
        "warnings": [],
        "errored_guias": errored_guias,
    }
    return review_svc


# ---------------------------------------------------------------------------
# Fix #42 tests (Task 1.3 RED)
# ---------------------------------------------------------------------------


class TestRetryBatchMarkRetryAttempted:
    """_retry_batch MUST call mark_retry_attempted on failed REINTENTAR (fix #42).

    REV-R26-S01: the per-registro batch retry path must mirror the per-guía
    retry path which calls mark_retry_attempted when apply_retry returns
    recovered=False.

    REV-R26-S02: mark_retry_attempted must NOT be called when apply_retry
    succeeds (recovered=True).
    """

    def test_retry_batch_calls_mark_retry_attempted_on_failure(
        self, client: TestClient
    ) -> None:
        """Task 1.3 RED: _retry_batch must call mark_retry_attempted when retry fails.

        This MUST fail before the fix because _retry_batch does not call
        mark_retry_attempted — it only calls apply_retry.
        """
        import uuid  # noqa: PLC0415

        run_id = str(uuid.uuid4())
        errored = [
            _make_errored("T009-0001"),
            _make_errored("T009-0002"),
        ]

        fake_reprocess = MagicMock()
        # Both retries fail
        fake_reprocess._sunat = MagicMock()  # SUNAT present (retry requires it)
        fake_reprocess.apply_retry.return_value = RetryResult(
            recovered=False, guia_id="T009-0001", reason="sunat_none"
        )

        review_svc = _seed_run(client, run_id, fake_reprocess, errored)

        resp = client.post(f"/api/v1/runs/{run_id}/registros/R001/retry")
        assert resp.status_code == 202

        # Let background task complete (TestClient runs sync background tasks inline)
        # mark_retry_attempted must have been called once per guía in the batch
        assert review_svc.mark_retry_attempted.call_count == 2
        review_svc.mark_retry_attempted.assert_any_call("T009-0001")
        review_svc.mark_retry_attempted.assert_any_call("T009-0002")

    def test_retry_batch_does_not_call_mark_retry_attempted_on_success(
        self, client: TestClient
    ) -> None:
        """REV-R26-S02: mark_retry_attempted must NOT be called on successful retry."""
        import uuid  # noqa: PLC0415

        run_id = str(uuid.uuid4())
        errored = [_make_errored("T009-0001")]

        fake_reprocess = MagicMock()
        fake_reprocess._sunat = MagicMock()
        # Retry succeeds
        fake_reprocess.apply_retry.return_value = RetryResult(
            recovered=True, guia_id="T009-0001", rows=[]
        )

        review_svc = _seed_run(client, run_id, fake_reprocess, errored)

        resp = client.post(f"/api/v1/runs/{run_id}/registros/R001/retry")
        assert resp.status_code == 202

        # mark_retry_attempted must NOT be called when recovered=True
        review_svc.mark_retry_attempted.assert_not_called()
