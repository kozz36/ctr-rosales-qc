"""Tests for recovery API endpoints (PR-2).

Strict TDD: written before implementation (RED).

Endpoints:
  POST /runs/{run_id}/discarded-pages/{page}/recover
  POST /runs/{run_id}/discarded-pages/recover-batch
  GET  /runs/{run_id}/discarded-pages/recover-status

Design §3 contracts tested:
  - 404 when page not in discarded list.
  - 409 when run is not in READY state.
  - 202 lifecycle: total + recovered+failed == total + done=True after gather.
  - 409 on concurrent batch (one active batch per run).
  - Terminal shape {total:0, done:true} when no batch has been fired.
  - identity_source="operator" round-trips through DTO without ValidationError.

Spec: REV-R31, REV-R30 (SA-5 settle-only-on-done contract), Design §3.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers — minimal FastAPI app + registry setup
# ---------------------------------------------------------------------------


def _make_line(requires_review: bool = True, source_page: int = 152):
    from reconciliation.domain.models import MaterialLine

    return MaterialLine(
        description_raw="BARRA A615 G60 1/2\"",
        description_canonical="BARRA A615 G60 1/2\" 9M",
        cantidad=Decimal("2.500"),
        unidad="TN",
        source_page=source_page,
        requires_review=requires_review,
        confidence=0.92,
        match_method="deterministic",
    )


def _make_discarded(page: int = 152, registro: str | None = "232", with_lines: bool = True):
    from reconciliation.domain.models import DiscardedPage

    return DiscardedPage(
        page=page,
        registro=registro,
        lines=[_make_line(source_page=page)] if with_lines else [],
    )


def _build_test_app(
    run_id: str = "test-run-01",
    discarded_pages=None,
    run_ready: bool = True,
    reprocess_service=None,
):
    """Build a minimal FastAPI app with a fake registry for endpoint testing."""
    from unittest.mock import AsyncMock, MagicMock

    from fastapi import FastAPI

    from reconciliation.domain.models import GuiaDeRemision
    from reconciliation.infrastructure.api.routes import router
    from reconciliation.infrastructure.api.schemas import (
        ReconciliationTableResponse,
    )

    app = FastAPI()
    app.include_router(router)

    # Fake review service
    review_svc = MagicMock()
    review_svc.discarded_pages = list(discarded_pages or [])
    review_svc.rows = []
    review_svc.errored_guias = []
    review_svc.unresolved_guias = []

    def _fake_recover(page, guia):
        # Remove entry from mock state
        review_svc.discarded_pages = [
            dp for dp in review_svc.discarded_pages if dp.page != page
        ]
        return []

    review_svc.recover_discarded_page.side_effect = _fake_recover

    # Fake reprocess service with apply_page_recovery
    if reprocess_service is None:
        rps = MagicMock()

        async def _fake_apply(page: int):
            from reconciliation.application.reprocess_service import PageRecoveryResult

            return PageRecoveryResult(
                recovered=True,
                page=page,
                guia_id=f"recovered_{page}",
                reason=None,
                rows=[],
            )

        rps.apply_page_recovery = _fake_apply
    else:
        rps = reprocess_service

    # Build registry entry — review_service absent when not ready (simulates "processing" state)
    registry_entry: dict[str, Any] = {
        "status": "ready" if run_ready else "processing",
        "reprocess_service": rps,
    }
    if run_ready:
        registry_entry["review_service"] = review_svc

    @app.on_event("startup")
    async def _setup():
        app.state.run_registry = {run_id: registry_entry}
        app.state.config = MagicMock()
        app.state.config.ocr.enabled = False
        app.state.config.vision.enabled = False

    return app, registry_entry


# ---------------------------------------------------------------------------
# 2.1.11 — 404 when page not in discarded list
# ---------------------------------------------------------------------------


def test_single_recover_endpoint_404_unknown_page():
    """REV-R31 / Design §3 — 404 when page 9999 not in discarded list.

    FAILS today: endpoint does not exist.
    """
    dp = _make_discarded(page=152)
    app, _ = _build_test_app(run_id="run-404", discarded_pages=[dp])

    with TestClient(app) as client:
        resp = client.post("/runs/run-404/discarded-pages/9999/recover")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 2.1.12 — 409 when run is not in READY state
# ---------------------------------------------------------------------------


def test_single_recover_endpoint_409_run_not_ready():
    """Design §3 — 409 when run is still processing (not READY).

    FAILS today: endpoint does not exist.
    """
    dp = _make_discarded(page=152)
    app, _ = _build_test_app(run_id="run-409", discarded_pages=[dp], run_ready=False)

    with TestClient(app) as client:
        resp = client.post("/runs/run-409/discarded-pages/152/recover")

    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# 2.1.13 — Batch 202 lifecycle: total=2, recovered+failed==2, done=True
# ---------------------------------------------------------------------------


def test_batch_recover_endpoint_202_lifecycle():
    """REV-R30 (progress lifecycle) / Design §3 — 202 + status settles done=True.

    FAILS today: batch endpoint does not exist.
    """
    dp152 = _make_discarded(page=152)
    dp175 = _make_discarded(page=175)
    app, _ = _build_test_app(run_id="run-batch", discarded_pages=[dp152, dp175])

    with TestClient(app) as client:
        # Fire batch
        resp = client.post(
            "/runs/run-batch/discarded-pages/recover-batch",
            json={"pages": [152, 175]},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["count"] == 2

        # Poll until done
        for _ in range(30):  # max 30 polls
            status_resp = client.get("/runs/run-batch/discarded-pages/recover-status")
            assert status_resp.status_code == 200
            status = status_resp.json()
            if status.get("done"):
                break
        else:
            pytest.fail("Batch never settled done=True")

        assert status["total"] == 2
        assert status["recovered"] + status["failed"] == 2
        assert status["done"] is True


# ---------------------------------------------------------------------------
# 2.1.14 — 409 when a batch is already in-flight
# ---------------------------------------------------------------------------


def test_batch_409_when_batch_in_flight():
    """Design §3 — one active batch per run; second POST recover-batch returns 409.

    FAILS today: batch endpoint does not exist.
    """
    import asyncio

    dp152 = _make_discarded(page=152)
    dp175 = _make_discarded(page=175)

    # Slow reprocess service so the first batch stays in-flight
    from unittest.mock import MagicMock

    slow_rps = MagicMock()

    async def _slow_apply(page: int):
        await asyncio.sleep(60)  # intentionally long; TestClient will timeout before this
        from reconciliation.application.reprocess_service import PageRecoveryResult

        return PageRecoveryResult(recovered=True, page=page, guia_id=f"recovered_{page}")

    slow_rps.apply_page_recovery = _slow_apply

    app, entry = _build_test_app(
        run_id="run-inflight",
        discarded_pages=[dp152, dp175],
        reprocess_service=slow_rps,
    )

    # Pre-seed the status as in-flight (done=False)
    entry.setdefault("discarded_batches", {})["discarded"] = {
        "total": 2, "recovered": 0, "failed": 0, "done": False
    }

    with TestClient(app) as client:
        resp = client.post(
            "/runs/run-inflight/discarded-pages/recover-batch",
            json={"pages": [152, 175]},
        )

    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# 2.1.15 — Terminal shape when no batch fired: total=0, done=true
# ---------------------------------------------------------------------------


def test_recover_status_terminal_shape_when_no_batch_fired():
    """Design §3 — terminal shape {total:0, done:true} when no batch submitted.

    PR-3b re-attach on mount depends on this: safe to call on every mount.
    LOCKED by this test.
    FAILS today: status endpoint does not exist.
    """
    app, _ = _build_test_app(run_id="run-fresh")

    with TestClient(app) as client:
        resp = client.get("/runs/run-fresh/discarded-pages/recover-status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["done"] is True


# ---------------------------------------------------------------------------
# 2.1.16 — identity_source="operator" round-trips through DTO
# ---------------------------------------------------------------------------


def test_identity_source_operator_roundtrips_dto():
    """Design §2 (4-site Literal lockstep) — 'operator' accepted by GuiaContributionResponse.

    This is the complete-enum 500-lock: model_validate must NOT raise ValidationError.
    FAILS today: "operator" is not in the Literal at schemas.py:35.
    """
    from pydantic import ValidationError

    from reconciliation.infrastructure.api.schemas import GuiaContributionResponse

    payload = {
        "guia_id": "recovered_152",
        "source_pages": [152],
        "cantidad": "2.500",
        "unidad": "TN",
        "confidence": 0.92,
        "identity_source": "operator",  # the NEW value — must not raise
    }
    try:
        obj = GuiaContributionResponse.model_validate(payload)
    except ValidationError as exc:
        pytest.fail(
            f"GuiaContributionResponse.model_validate raised ValidationError for "
            f"identity_source='operator': {exc}"
        )

    assert obj.identity_source == "operator"


# ---------------------------------------------------------------------------
# CRITICAL (JD ×2) — batch handler de-duplicates pages before scheduling
# ---------------------------------------------------------------------------


def test_batch_dedups_repeated_pages():
    """CRITICAL — POST recover-batch {"pages":[88,88]} schedules page 88 ONCE.

    Deterministic double-count with zero concurrency: a duplicated page in the
    batch body invokes apply_page_recovery(88) twice → two recovered_88 guías.
    The route must de-duplicate (order-preserving) before scheduling.

    RED today: routes.py:1447 does NOT dedupe → apply_page_recovery called twice.
    """
    dp88 = _make_discarded(page=88)

    from unittest.mock import MagicMock

    rps = MagicMock()
    calls: list[int] = []

    async def _record_apply(page: int):
        from reconciliation.application.reprocess_service import PageRecoveryResult

        calls.append(page)
        return PageRecoveryResult(
            recovered=True, page=page, guia_id=f"recovered_{page}", rows=[]
        )

    rps.apply_page_recovery = _record_apply

    app, _ = _build_test_app(
        run_id="run-dedup", discarded_pages=[dp88], reprocess_service=rps
    )

    with TestClient(app) as client:
        resp = client.post(
            "/runs/run-dedup/discarded-pages/recover-batch",
            json={"pages": [88, 88]},
        )
        assert resp.status_code == 202
        # count reflects the de-duplicated page list.
        assert resp.json()["count"] == 1

        for _ in range(30):
            status = client.get(
                "/runs/run-dedup/discarded-pages/recover-status"
            ).json()
            if status.get("done"):
                break
        else:
            pytest.fail("Batch never settled")

    assert calls == [88], (
        f"Page 88 must be scheduled exactly once; apply_page_recovery calls: {calls}"
    )


# ---------------------------------------------------------------------------
# MEDIUM (REV-R30 item 3 / S08) — batch bounds concurrency to max 3 in-flight
# ---------------------------------------------------------------------------


def test_batch_bounds_concurrency_to_three():
    """MEDIUM — a batch of 6 pages never runs more than 3 recoveries in-flight.

    Spec REV-R30 item 3 + S08: max 3 simultaneous recovery calls. Without a
    Semaphore around _one, asyncio.gather spawns ALL pages concurrently → A4
    no-cap selections (343 pages) would launch ~32 parallel 300-DPI renders.

    RED today: gather is unbounded → max in-flight == 6.
    """
    from unittest.mock import MagicMock

    pages = [10, 11, 12, 13, 14, 15]
    discarded = [_make_discarded(page=p) for p in pages]

    rps = MagicMock()
    in_flight = 0
    max_in_flight = 0

    async def _instrumented_apply(page: int):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        try:
            await asyncio.sleep(0.02)  # hold the slot so overlap is observable
        finally:
            in_flight -= 1
        from reconciliation.application.reprocess_service import PageRecoveryResult

        return PageRecoveryResult(
            recovered=True, page=page, guia_id=f"recovered_{page}", rows=[]
        )

    rps.apply_page_recovery = _instrumented_apply

    app, _ = _build_test_app(
        run_id="run-bound", discarded_pages=discarded, reprocess_service=rps
    )

    with TestClient(app) as client:
        resp = client.post(
            "/runs/run-bound/discarded-pages/recover-batch",
            json={"pages": pages},
        )
        assert resp.status_code == 202

        for _ in range(60):
            status = client.get(
                "/runs/run-bound/discarded-pages/recover-status"
            ).json()
            if status.get("done"):
                break
        else:
            pytest.fail("Batch never settled")

    assert max_in_flight <= 3, (
        f"Concurrency unbounded: max in-flight {max_in_flight} exceeds limit 3"
    )


# ---------------------------------------------------------------------------
# LOW (test gap) — single-page 200 success path drives the route end-to-end
# ---------------------------------------------------------------------------


def test_single_recover_endpoint_200_success_assembles_response():
    """LOW — happy-path 200: rows + remaining discarded_pages assembled (routes.py:1395-1412).

    Locks the success-path response assembly: recovered=True, the recovered
    page is dropped from the returned discarded_pages list, rows surfaced.
    """
    from unittest.mock import MagicMock

    dp152 = _make_discarded(page=152)
    dp175 = _make_discarded(page=175)

    rps = MagicMock()

    app, entry = _build_test_app(
        run_id="run-200", discarded_pages=[dp152, dp175], reprocess_service=rps
    )

    review_svc = entry["review_service"]

    async def _apply(page: int):
        from reconciliation.application.reprocess_service import PageRecoveryResult

        # Mirror production: recovery drops the page from the discarded list.
        review_svc.discarded_pages = [
            d for d in review_svc.discarded_pages if d.page != page
        ]
        return PageRecoveryResult(
            recovered=True, page=page, guia_id=f"recovered_{page}", rows=[]
        )

    rps.apply_page_recovery = _apply

    with TestClient(app) as client:
        resp = client.post("/runs/run-200/discarded-pages/152/recover")

    assert resp.status_code == 200
    data = resp.json()
    assert data["recovered"] is True
    assert data["page"] == 152
    assert data["guia_id"] == "recovered_152"
    # The recovered page is dropped; page 175 remains.
    remaining = {d["page"] for d in data["discarded_pages"]}
    assert remaining == {175}
