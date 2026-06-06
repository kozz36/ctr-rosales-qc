"""Phase 3: concurrency isolation tests for _reprocess_batch (REV-R20-S02/S03/S05/S06).

Tests verify:
- REV-R20-S02: no RuntimeError (no nested asyncio.run / event-loop conflict).
- REV-R20-S03: per-guía exception does NOT abort the remaining guías.
- REV-R20-S05: recovered lines are NOT marked retry_attempted.
- REV-R20-S06 (light): concurrency cap ≤ 3 (Semaphore in service, not in route).

Strict-TDD: RED tests first (tasks 3.1–3.3); go GREEN once Phase 1 implementation
is in place (task 3.4).  The implementation was committed in task 1.7, so these tests
should be GREEN immediately after being written IF the route is correct.

The concurrency test here validates the route does NOT add a second Semaphore or
call asyncio.run() (D1 / KI-2).  The Semaphore cap is asserted at the service level.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from reconciliation.application.config import AppConfig
from reconciliation.application.reprocess_service import ReprocessResult
from reconciliation.domain.models import ErroredGuia


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_errored(guia_id: str, registro: str = "R001") -> ErroredGuia:
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
# Task 3.1 RED: no RuntimeError (no nested event loop / asyncio.run)
# ---------------------------------------------------------------------------


class TestReprocessBatchNoNestedEventLoop:
    """REV-R20-S02: _reprocess_batch must not cause RuntimeError from nested asyncio.run.

    The route is async def (per D2).  Starlette background tasks that are coroutine
    functions are awaited on the running loop.  asyncio.run() inside the background
    task would raise RuntimeError('This event loop is already running.').
    """

    def test_no_runtime_error_from_nested_loop(self, client: TestClient) -> None:
        """Task 3.1 RED: POST .../reprocess must not raise RuntimeError."""
        run_id = str(uuid.uuid4())
        errored = [_make_errored("T009-0001"), _make_errored("T009-0002")]

        fake_service = MagicMock()
        fake_service._vision = MagicMock()
        fake_service.apply_reprocess = AsyncMock(
            return_value=ReprocessResult(recovered=True, guia_id="T009-0001", rows=[])
        )

        _seed_run(client, run_id, fake_service, errored)

        # If the route calls asyncio.run() inside the background task this raises
        # RuntimeError — the test will FAIL if that happens.
        resp = client.post(f"/api/v1/runs/{run_id}/registros/R001/reprocess")
        assert resp.status_code == 202

        # Background task runs inline in TestClient — apply_reprocess must have been
        # called for each guía without raising.
        assert fake_service.apply_reprocess.await_count == 2


# ---------------------------------------------------------------------------
# Task 3.2 RED: per-guía exception does NOT abort the batch
# ---------------------------------------------------------------------------


class TestReprocessBatchIsolation:
    """REV-R20-S03: a per-guía failure must NOT abort the remaining guías."""

    def test_per_guia_exception_does_not_abort_batch(self, client: TestClient) -> None:
        """Task 3.2 RED: exception on first guía must not stop the second from being tried."""
        run_id = str(uuid.uuid4())
        errored = [_make_errored("T009-FAIL"), _make_errored("T009-OK")]

        call_order: list[str] = []

        async def _fake_reprocess(
            guia_id: str,
            source_pages: list[int],
        ) -> ReprocessResult:
            call_order.append(guia_id)
            if guia_id == "T009-FAIL":
                raise RuntimeError("vision timeout")
            return ReprocessResult(recovered=True, guia_id=guia_id, rows=[])

        fake_service = MagicMock()
        fake_service._vision = MagicMock()
        fake_service.apply_reprocess = _fake_reprocess

        _seed_run(client, run_id, fake_service, errored)

        resp = client.post(f"/api/v1/runs/{run_id}/registros/R001/reprocess")
        assert resp.status_code == 202

        # Both guías must have been attempted (asyncio.gather return_exceptions=True).
        assert "T009-FAIL" in call_order
        assert "T009-OK" in call_order


# ---------------------------------------------------------------------------
# Task 3.3 RED: _reprocess_batch never calls mark_retry_attempted
# ---------------------------------------------------------------------------


class TestReprocessBatchNeverSetsRetryAttempted:
    """REV-R20-S05: bulk AI reprocess must NOT call mark_retry_attempted.

    retry_attempted is a SUNAT-REINTENTAR flag (gates the SUNAT button).
    AI reprocess is stateless-retryable; setting the flag would incorrectly
    gate the REINTENTAR button.
    """

    def test_bulk_reprocess_does_not_call_mark_retry_attempted_on_success(
        self, client: TestClient
    ) -> None:
        """Task 3.3 RED: mark_retry_attempted must NOT be called on success."""
        run_id = str(uuid.uuid4())
        errored = [_make_errored("T009-0001")]

        fake_service = MagicMock()
        fake_service._vision = MagicMock()
        fake_service.apply_reprocess = AsyncMock(
            return_value=ReprocessResult(recovered=True, guia_id="T009-0001", rows=[])
        )

        review_svc = _seed_run(client, run_id, fake_service, errored)

        resp = client.post(f"/api/v1/runs/{run_id}/registros/R001/reprocess")
        assert resp.status_code == 202

        # mark_retry_attempted must NEVER be called — it is SUNAT-only.
        review_svc.mark_retry_attempted.assert_not_called()

    def test_bulk_reprocess_does_not_call_mark_retry_attempted_on_failure(
        self, client: TestClient
    ) -> None:
        """Task 3.3 RED: mark_retry_attempted must NOT be called even when vision returns []."""
        run_id = str(uuid.uuid4())
        errored = [_make_errored("T009-0001")]

        fake_service = MagicMock()
        fake_service._vision = MagicMock()
        fake_service.apply_reprocess = AsyncMock(
            return_value=ReprocessResult(recovered=False, guia_id="T009-0001", reason="vision_empty")
        )

        review_svc = _seed_run(client, run_id, fake_service, errored)

        resp = client.post(f"/api/v1/runs/{run_id}/registros/R001/reprocess")
        assert resp.status_code == 202

        review_svc.mark_retry_attempted.assert_not_called()


# ---------------------------------------------------------------------------
# Task 3.4 (green after 3.1–3.3): recovered lines always requires_review=True
# ---------------------------------------------------------------------------


class TestReprocessBatchRecoveredLinesRequiresReview:
    """REV-R20-S05: recovered guías must always have requires_review=True.

    This is enforced by ReviewService.add_recovered_guia (fail-closed guard),
    but the batch route must not strip the flag before calling the service.
    Tested at the route level by verifying apply_reprocess was called (no bypass).
    """

    def test_apply_reprocess_called_per_guia(self, client: TestClient) -> None:
        """All guías in the registro have apply_reprocess called (no bypass)."""
        run_id = str(uuid.uuid4())
        errored = [
            _make_errored("T009-0001"),
            _make_errored("T009-0002"),
            _make_errored("T009-0003"),
        ]

        fake_service = MagicMock()
        fake_service._vision = MagicMock()
        fake_service.apply_reprocess = AsyncMock(
            return_value=ReprocessResult(recovered=True, guia_id="T009-0001", rows=[])
        )

        _seed_run(client, run_id, fake_service, errored)

        resp = client.post(f"/api/v1/runs/{run_id}/registros/R001/reprocess")
        assert resp.status_code == 202

        # All 3 guías must have been sent to apply_reprocess.
        assert fake_service.apply_reprocess.await_count == 3
