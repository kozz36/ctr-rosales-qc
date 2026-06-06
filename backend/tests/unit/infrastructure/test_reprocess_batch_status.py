"""SA-5 fix: backend batch-status signal for bulk AI reprocess (REV-R20).

The PR-B frontend settlement was a TIME-HEURISTIC that finalized the
"N recuperadas / M fallaron" summary prematurely on a real-latency batch
(SA-5 run c8a6f97d: UI 2/22 while the backend recovered 17/24). The fix
adds a REAL backend completion signal:

  - ``_reprocess_batch`` records per-batch state {total, recovered, failed, done}
    into the run-registry entry under ``reprocess_batches[registro]``.
  - GET .../reprocess-status exposes it so the frontend polls a real ``done``
    flag + real counts instead of guessing via timing.

Strict-TDD: these tests are written RED FIRST (helper + endpoint absent), then
GREEN once the implementation lands. They exercise:

  - the race-free batch helper: mid-batch ``done=False``, final ``done=True``
    with correct recovered/failed split;
  - per-guía failure increments ``failed`` NOT ``recovered``;
  - the GET endpoint shape (done flag + counts), 404 for unknown run, and the
    "no batch yet" shape.

asyncio is single-threaded between awaits: the helper increments the shared
status dict immediately after each per-guía ``await`` resolves, so there is no
data race — the test drives real coroutine resolution to prove ordering.
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
) -> dict[str, object]:
    review_svc = MagicMock()
    review_svc.errored_guias = errored_guias

    registry = client.app.state.run_registry  # type: ignore[attr-defined]
    entry = {
        "status": "review",
        "review_service": review_svc,
        "reprocess_service": reprocess_service,
        "ctx": None,
        "result": None,
        "vision_calls_made": 0,
        "warnings": [],
        "errored_guias": errored_guias,
    }
    registry[run_id] = entry
    return entry


# ---------------------------------------------------------------------------
# RED 1: the race-free batch helper records mid-batch + final status
# ---------------------------------------------------------------------------


class TestReprocessBatchStatusHelper:
    """The batch coroutine maintains a live {total, recovered, failed, done} record."""

    def test_status_record_done_false_midbatch_true_after(self) -> None:
        """RED: while a guía is still awaiting, done=False; after gather, done=True
        with the correct recovered/failed split."""
        from reconciliation.infrastructure.api.routes import (  # noqa: PLC0415
            _run_reprocess_batch,
        )

        gate = asyncio.Event()

        async def _fake_reprocess(guia_id: str, source_pages: list[int]) -> ReprocessResult:
            if guia_id == "G-SLOW":
                await gate.wait()
                return ReprocessResult(recovered=True, guia_id=guia_id, rows=[])
            return ReprocessResult(recovered=True, guia_id=guia_id, rows=[])

        service = MagicMock()
        service.apply_reprocess = _fake_reprocess

        target = [_make_errored("G-FAST"), _make_errored("G-SLOW")]
        status: dict[str, object] = {}

        async def _drive() -> None:
            task = asyncio.create_task(
                _run_reprocess_batch(service, target, status, run_id="run-x")
            )
            # Let the fast guía resolve while the slow one blocks on the gate.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            # Mid-batch: not done yet; the slow guía is still in flight.
            assert status["done"] is False
            assert status["total"] == 2
            assert status["failed"] == 0
            # The fast guía already counted as recovered (>=1, < total).
            assert 1 <= status["recovered"] < 2

            gate.set()
            await task

            assert status["done"] is True
            assert status["total"] == 2
            assert status["recovered"] == 2
            assert status["failed"] == 0

        asyncio.run(_drive())

    def test_failure_increments_failed_not_recovered(self) -> None:
        """RED: a per-guía exception / non-recovered result increments failed."""
        from reconciliation.infrastructure.api.routes import (  # noqa: PLC0415
            _run_reprocess_batch,
        )

        async def _fake_reprocess(guia_id: str, source_pages: list[int]) -> ReprocessResult:
            if guia_id == "G-RAISE":
                raise RuntimeError("vision timeout")
            if guia_id == "G-EMPTY":
                return ReprocessResult(recovered=False, guia_id=guia_id, reason="vision_empty")
            return ReprocessResult(recovered=True, guia_id=guia_id, rows=[])

        service = MagicMock()
        service.apply_reprocess = _fake_reprocess

        target = [
            _make_errored("G-OK"),
            _make_errored("G-EMPTY"),
            _make_errored("G-RAISE"),
        ]
        status: dict[str, object] = {}

        asyncio.run(_run_reprocess_batch(service, target, status, run_id="run-x"))

        assert status["done"] is True
        assert status["total"] == 3
        assert status["recovered"] == 1
        assert status["failed"] == 2


# ---------------------------------------------------------------------------
# RED 2: GET .../reprocess-status endpoint shape
# ---------------------------------------------------------------------------


class TestReprocessStatusEndpoint:
    """GET /runs/{run_id}/registros/{registro}/reprocess-status."""

    def test_status_done_true_after_inline_batch(self, client: TestClient) -> None:
        """RED: after the POST (background task runs inline in TestClient), the
        status endpoint reports done=true with the correct counts."""
        run_id = str(uuid.uuid4())
        errored = [_make_errored("T009-0001"), _make_errored("T009-0002")]

        service = MagicMock()
        service._vision = MagicMock()
        service.apply_reprocess = AsyncMock(
            return_value=ReprocessResult(recovered=True, guia_id="T009-0001", rows=[])
        )
        _seed_run(client, run_id, service, errored)

        post = client.post(f"/api/v1/runs/{run_id}/registros/R001/reprocess")
        assert post.status_code == 202

        resp = client.get(f"/api/v1/runs/{run_id}/registros/R001/reprocess-status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["registro"] == "R001"
        assert body["total"] == 2
        assert body["recovered"] == 2
        assert body["failed"] == 0
        assert body["done"] is True

    def test_status_failure_counts(self, client: TestClient) -> None:
        """RED: a failed guía surfaces as failed, not recovered, in the endpoint."""
        run_id = str(uuid.uuid4())
        errored = [_make_errored("T009-OK"), _make_errored("T009-BAD")]

        async def _fake(guia_id: str, source_pages: list[int]) -> ReprocessResult:
            if guia_id == "T009-BAD":
                return ReprocessResult(recovered=False, guia_id=guia_id, reason="vision_empty")
            return ReprocessResult(recovered=True, guia_id=guia_id, rows=[])

        service = MagicMock()
        service._vision = MagicMock()
        service.apply_reprocess = _fake
        _seed_run(client, run_id, service, errored)

        post = client.post(f"/api/v1/runs/{run_id}/registros/R001/reprocess")
        assert post.status_code == 202

        resp = client.get(f"/api/v1/runs/{run_id}/registros/R001/reprocess-status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert body["recovered"] == 1
        assert body["failed"] == 1
        assert body["done"] is True

    def test_status_unknown_run_404(self, client: TestClient) -> None:
        """RED: unknown run_id → 404."""
        resp = client.get("/api/v1/runs/no-such-run/registros/R001/reprocess-status")
        assert resp.status_code == 404

    def test_status_no_batch_yet_done_true_total_zero(self, client: TestClient) -> None:
        """RED: a known run with no batch fired for the registro → a sane
        terminal shape (done=true, total=0) so the frontend never hangs."""
        run_id = str(uuid.uuid4())
        service = MagicMock()
        service._vision = MagicMock()
        _seed_run(client, run_id, service, [])

        resp = client.get(f"/api/v1/runs/{run_id}/registros/R999/reprocess-status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["done"] is True
