"""In-container acceptance gate — CONT-S12/S13.

Full faithful pipeline run over the RUNNING backend (HTTP):

  POST /api/v1/runs   → upload the real PDF, capture run_id
  GET  /api/v1/runs/{run_id}         → poll until status == "review"
  GET  /api/v1/runs/{run_id}/table   → reconciliation rows

R8 MATCH gate (CONT-S12):
  - At least one MATCH row exists (regression guard: was zero before r8).
  - Registro 232, material 1/2" TN: status=MATCH, summed_qty=4.124,
    match_method=deterministic, requires_review=False.

R9 fecha-divergence gate (CONT-S13):
  - Divergence is an ADDITIVE side-channel only: rows with has_fecha_divergence=True
    must also have requires_review=True and their status must be qty-driven (MATCH/
    MISMATCH/etc.) — divergence must NEVER by itself force MISMATCH.
  - Registro 232 R8 MATCH row must NOT be downgraded by any divergence flag.

Real-data contract (SA-1 / anti-mock-theatre):
  - This test hits the LIVE backend via httpx over HTTP.
  - It uploads the real 493-page PDF.
  - Assertions mirror the integration-gate values from test_pipeline_r8_gate.py
    and test_pipeline_r9_gate.py so this gate is equally discriminating.
  - A mock anywhere in the pipeline chain would defeat the gate entirely.

Run:
  # Inside container (via compose — make verify):
  docker compose up -d backend
  docker compose run --rm backend \\
    python -m pytest tests/e2e/test_container_verification.py -v -s --tb=short

  # Outside container (direct — backend must already be running on :8000):
  cd backend && uv run pytest tests/e2e/test_container_verification.py -v -s -m e2e

Prerequisites (make verify):
  make build + make smoke passing + Ollama host daemon + SUNAT reachable (optional).
  The PDF must be present at the container mount-point (/data/input.pdf).

SA-2 deviation protocol:
  If the backend returns an unexpected HTTP status or a pipeline error the test
  reports the exact response body and fails rather than silently skipping.
"""

from __future__ import annotations

import os
import time
from decimal import Decimal
from pathlib import Path
from typing import NoReturn

import pytest

pytestmark = pytest.mark.e2e

# ---------------------------------------------------------------------------
# Environment / paths
# ---------------------------------------------------------------------------

# Backend URL: in-container the backend binds host networking → localhost:8000.
# Override via CTR_BACKEND_URL for out-of-container runs if needed.
_BACKEND_BASE_URL = os.environ.get("CTR_BACKEND_URL", "http://localhost:8000")
_API_PREFIX = "/api/v1"

# PDF path inside the container (compose bind-mount; see docker-compose.yml).
_PDF_PATH_CONTAINER = "/data/input.pdf"
# Host-side path for running outside the container (mirrors test_smoke_cloud_vision.py).
_PDF_PATH_HOST = (
    "/data/Projects/ctr-rosales-qc/"
    "Informe de detalle del formulario-202606020255.pdf"
)
# Also honour the same env var pattern as the integration gates.
_PDF_PATH_ENV = os.environ.get("CTR_PDF_PATH", "")

# Polling settings: the full pipeline on the real 493-page PDF takes several minutes.
_POLL_INTERVAL_S = 10          # seconds between status polls
_TIMEOUT_S = int(os.environ.get("CTR_VERIFY_TIMEOUT", str(15 * 60)))  # 15 min default

# Strict mode: set by `make verify` (the in-container acceptance gate). When ON, a
# missing precondition (backend unreachable, PDF absent) is a GATE FAILURE, not a
# skip — otherwise `make verify` would exit GREEN having verified nothing (vacuous
# green; the exact mock-theatre failure class this project has been burned by). When
# OFF (ad-hoc local runs alongside the unit suite), skip so a missing backend/PDF
# does not break unrelated test runs.
_STRICT = os.environ.get("CTR_VERIFY_STRICT", "") not in ("", "0", "false", "False")
# How long to wait for the backend to become reachable before declaring it down.
# Covers the cold-start race: `make verify` sleeps only 5s but the compose
# healthcheck start_period is 15s.
_BACKEND_WAIT_S = int(os.environ.get("CTR_VERIFY_BACKEND_WAIT", "60"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _skip_or_fail(reason: str) -> NoReturn:
    """Fail in strict mode (the in-container gate), skip otherwise.

    In strict mode (`CTR_VERIFY_STRICT=1`, set by `make verify`) a missing
    precondition is a real gate failure — making it visible instead of letting
    `make verify` exit green having verified nothing. Out of strict mode it skips.
    """
    if _STRICT:
        pytest.fail(reason)
    pytest.skip(reason)


def _get_pdf_path() -> Path:
    """Return the PDF path.

    Priority: CTR_PDF_PATH env > container mount > host path.
    In strict mode, FAILS if none are found (the gate cannot run without the real
    PDF); otherwise skips.
    """
    if _PDF_PATH_ENV:
        p = Path(_PDF_PATH_ENV)
        if p.exists():
            return p
    for candidate in (_PDF_PATH_CONTAINER, _PDF_PATH_HOST):
        p = Path(candidate)
        if p.exists():
            return p
    _skip_or_fail(
        f"Real PDF not found. Checked: {_PDF_PATH_ENV!r}, "
        f"{_PDF_PATH_CONTAINER!r}, {_PDF_PATH_HOST!r}. "
        "Set CTR_PDF_PATH or run via make verify (docker compose)."
    )


def _wait_for_backend(base_url: str, timeout_s: float = _BACKEND_WAIT_S) -> bool:
    """Poll GET /api/v1/runs until the REAL ctr backend answers 200, or timeout.

    Retries to absorb the cold-start race (`make verify` sleeps 5s, healthcheck
    start_period is 15s). The list endpoint returns 200 with a JSON array; we
    require BOTH so a foreign service squatting on the host port (e.g. another
    `network_mode: host` container returning its own 404/JSON) is rejected with a
    clear "not reachable" gate failure instead of a cryptic POST 404 downstream.
    Returns True only when the ctr runs-list contract is observed, else False.
    """
    import httpx  # noqa: PLC0415 — lazy import; never at module top

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base_url}{_API_PREFIX}/runs", timeout=10.0)
            if r.status_code == 200 and isinstance(r.json(), list):
                return True
        except Exception:
            pass
        time.sleep(2.0)
    return False


# ---------------------------------------------------------------------------
# Shared run fixture — uploads PDF once, caches result for all gate tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pipeline_result_via_api():
    """Upload the real PDF, poll until complete, return the /table response dict.

    This fixture is module-scoped so the expensive full pipeline run happens
    only once for both the R8 and R9 gate test classes.

    SA-2: in strict mode (`make verify`), if the backend is unreachable, the PDF
    is missing, or the pipeline ends in error, the test FAILS with a descriptive
    message (never silently skips) so the gate failure is visible. Out of strict
    mode it skips, so a missing backend/PDF does not block the unit suite.
    """
    import httpx  # noqa: PLC0415

    if not _wait_for_backend(_BACKEND_BASE_URL):
        _skip_or_fail(
            f"Backend not reachable at {_BACKEND_BASE_URL} after {_BACKEND_WAIT_S}s. "
            "Run 'docker compose up -d backend' before make verify, "
            "or set CTR_BACKEND_URL to the correct base URL."
        )

    pdf_path = _get_pdf_path()

    # --- POST /api/v1/runs — upload the real PDF ---
    with pdf_path.open("rb") as fh:
        resp = httpx.post(
            f"{_BACKEND_BASE_URL}{_API_PREFIX}/runs",
            files={"file": (pdf_path.name, fh, "application/pdf")},
            timeout=120.0,  # upload of 493-page PDF may take a moment
        )

    assert resp.status_code == 202, (
        f"POST /runs failed: HTTP {resp.status_code}\n{resp.text[:2000]}"
    )

    run_id: str = resp.json()["run_id"]
    print(f"\n[VERIFY] Uploaded PDF → run_id={run_id}")

    # --- GET /api/v1/runs/{run_id} — poll until status == "review" or "error" ---
    deadline = time.monotonic() + _TIMEOUT_S
    status_url = f"{_BACKEND_BASE_URL}{_API_PREFIX}/runs/{run_id}"

    while True:
        if time.monotonic() > deadline:
            pytest.fail(
                f"Pipeline run {run_id} timed out after {_TIMEOUT_S}s. "
                "Increase CTR_VERIFY_TIMEOUT or check Ollama + SUNAT availability."
            )

        status_resp = httpx.get(status_url, timeout=30.0)
        assert status_resp.status_code == 200, (
            f"GET /runs/{run_id} failed: HTTP {status_resp.status_code}\n"
            f"{status_resp.text[:2000]}"
        )

        body = status_resp.json()
        run_status: str = body["status"]
        progress = body.get("progress")
        if progress:
            print(
                f"\r[VERIFY] {run_status} — "
                f"{progress.get('stage_label','?')} "
                f"({progress.get('item_done','?')}/{progress.get('item_total','?')})",
                end="",
                flush=True,
            )

        if run_status == "review":
            print(f"\n[VERIFY] Pipeline complete. Fetching table for run {run_id}...")
            break

        if run_status == "error":
            error_detail = body.get("error") or "(no error detail)"
            pytest.fail(
                f"Pipeline run {run_id} ended in error:\n{error_detail}\n"
                f"warnings={body.get('warnings', [])}"
            )

        # Still pending / processing — wait and re-poll.
        time.sleep(_POLL_INTERVAL_S)

    # --- GET /api/v1/runs/{run_id}/table ---
    table_resp = httpx.get(
        f"{_BACKEND_BASE_URL}{_API_PREFIX}/runs/{run_id}/table",
        timeout=60.0,
    )
    assert table_resp.status_code == 200, (
        f"GET /runs/{run_id}/table failed: HTTP {table_resp.status_code}\n"
        f"{table_resp.text[:2000]}"
    )

    table_body = table_resp.json()
    rows = table_body["rows"]
    print(
        f"[VERIFY] Table fetched — {len(rows)} row(s), "
        f"run_id={run_id}"
    )
    return table_body


# ---------------------------------------------------------------------------
# CONT-S12 — R8 MATCH gate
# ---------------------------------------------------------------------------


class TestR8MatchGateCONTS12:
    """R8 canonical-key MATCH gate (CONT-S12).

    Assertions mirror test_pipeline_r8_gate.py::TestRealPDFGate at the API level.
    Every assertion here would FAIL if canonical-key matching were broken.
    """

    def test_at_least_one_match_row(self, pipeline_result_via_api: dict) -> None:
        """At least one MATCH row must exist.

        Was zero before r8. A zero count means canonical-key matching is broken.
        """
        rows = pipeline_result_via_api["rows"]
        match_rows = [r for r in rows if r["status"] == "MATCH"]
        assert len(match_rows) > 0, (
            "Expected at least one MATCH row. r8 canonical-key matching is broken. "
            f"Statuses present: {sorted({r['status'] for r in rows})}"
        )

    def test_registro_232_half_inch_tn_match(self, pipeline_result_via_api: dict) -> None:
        """Registro 232 — 'BARRA AG615/A706 G60 1/2\" x 9M = 4.124 TN' must MATCH.

        Three guías (pages 5, 6, 8) carry variant texts that sum to 4.124 TN.
        Match method must be deterministic (no vision inference needed).
        Mirrors: TestRealPDFGate.test_4252_family_row_match.
        """
        rows = pipeline_result_via_api["rows"]
        target_rows = [
            r for r in rows
            if r["registro"] == "232"
            and '1/2"' in r["material_canonical"]
            and r["unidad"] == "TN"
            and r["status"] == "MATCH"
        ]
        assert len(target_rows) >= 1, (
            "Expected at least one MATCH row for registro=232, 1/2\" TN. "
            "r8 canonical-key matching is broken or the guía triple was not extracted. "
            f"Rows for registro=232: {[(r['material_canonical'], r['status'], r['unidad']) for r in rows if r['registro'] == '232']}"
        )
        row = target_rows[0]

        # Exact quantity assertion: 4.124 TN (sum of pages 5+6+8).
        # MATCH tolerance is EXACT(0) — any discrepancy → MISMATCH.
        summed = Decimal(str(row["summed_qty"]))
        assert summed == Decimal("4.124"), (
            f"Expected summed_qty=4.124 TN, got {summed}. "
            "The three guías did not sum correctly or a line was lost."
        )

        assert row["match_method"] == "deterministic", (
            f"Expected match_method=deterministic, got {row['match_method']!r}. "
            "This description family must not require LLM inference."
        )
        assert row["requires_review"] is False, (
            f"Expected requires_review=False on a clean MATCH, got True. "
            f"Divergence or vision flags were incorrectly set: {row}"
        )

    def test_total_row_count_nonzero(self, pipeline_result_via_api: dict) -> None:
        """Pipeline must produce at least one row (regression: empty → pipeline failure).

        Mirrors: TestRealPDFGate.test_rev3_regression_guard.
        """
        rows = pipeline_result_via_api["rows"]
        assert len(rows) > 0, (
            "No rows produced — the pipeline likely failed silently or produced no output."
        )

    def test_multiple_status_types_present(self, pipeline_result_via_api: dict) -> None:
        """MATCH rows must be additive, not replacing MISMATCH/DECLARED_MISSING/GUIA_MISSING.

        Rev-3 regression guard: new MATCHes are additive. If only MATCH exists the
        pipeline has collapsed or misclassified rows.

        Mirrors: TestRealPDFGate.test_rev3_regression_guard — 'status types exist'.
        """
        rows = pipeline_result_via_api["rows"]
        statuses = {r["status"] for r in rows}
        # We know at minimum MATCH and at least one non-MATCH type exist from rev-3.
        assert "MATCH" in statuses, "MATCH status missing — r8 matching is broken."
        # The 493-page PDF has mismatches — at least one non-MATCH type must still exist.
        non_match = statuses - {"MATCH"}
        assert len(non_match) > 0, (
            "Only MATCH rows found — MISMATCH/DECLARED_MISSING/GUIA_MISSING have "
            "vanished. Rows were likely collapsed incorrectly or the PDF was misread."
        )


# ---------------------------------------------------------------------------
# CONT-S13 — R9 fecha-divergence gate
# ---------------------------------------------------------------------------


class TestR9DivergenceGateCONTS13:
    """R9 fecha-divergence acceptance gate (CONT-S13).

    Assertions mirror test_pipeline_r9_gate.py::TestR9RealPDFGate at the API level.

    Divergence is an ADDITIVE side-channel:
    - It sets has_fecha_divergence=True and requires_review=True on the row.
    - It MUST NOT change the status (qty-driven logic only).
    - The R8 MATCH for registro 232 must not be downgraded by any divergence.
    """

    def test_r8_match_not_downgraded_by_divergence(
        self, pipeline_result_via_api: dict
    ) -> None:
        """R8 regression guard: registro 232 1/2\" TN MATCH must survive R9.

        Divergence is an additive side-channel — it MUST NOT turn MATCH → MISMATCH.
        Mirrors: TestR9RealPDFGate.test_4252_family_row_still_match.
        """
        rows = pipeline_result_via_api["rows"]
        target = [
            r for r in rows
            if r["registro"] == "232"
            and '1/2"' in r["material_canonical"]
            and r["unidad"] == "TN"
            and r["status"] == "MATCH"
        ]
        assert len(target) >= 1, (
            "R8 MATCH row for registro=232 1/2\" TN is missing after R9 application. "
            "R9 divergence side-channel has incorrectly downgraded the status. "
            f"Rows for registro=232: {[(r['material_canonical'], r['status']) for r in rows if r['registro'] == '232']}"
        )
        row = target[0]
        assert Decimal(str(row["summed_qty"])) == Decimal("4.124"), (
            f"summed_qty changed after R9: expected 4.124, got {row['summed_qty']}"
        )
        assert row["match_method"] == "deterministic"

    def test_divergence_rows_require_review(
        self, pipeline_result_via_api: dict
    ) -> None:
        """FDR-S09: every row with has_fecha_divergence=True must also have requires_review=True.

        Divergence without requires_review is a silent data-quality gap.
        Mirrors: TestR9RealPDFGate.test_divergence_flags_imply_requires_review.
        """
        rows = pipeline_result_via_api["rows"]
        violations = [
            r for r in rows
            if r.get("has_fecha_divergence") is True and r.get("requires_review") is not True
        ]
        assert len(violations) == 0, (
            f"{len(violations)} row(s) have has_fecha_divergence=True but "
            "requires_review!=True. FDR-S09 violated:\n"
            + "\n".join(
                f"  registro={v['registro']} material={v['material_canonical']} "
                f"status={v['status']} requires_review={v['requires_review']}"
                for v in violations
            )
        )

    def test_divergence_does_not_override_qty_status(
        self, pipeline_result_via_api: dict
    ) -> None:
        """Divergent rows retain their qty-driven status (MATCH, MISMATCH, etc.).

        A row with has_fecha_divergence=True and summed_qty == declared_qty must
        still be MATCH. Divergence is a side-channel, not a status driver.
        Mirrors: TestR9ReconcilerDivergenceGate.test_divergent_guia_flagged_status_unchanged.
        """
        rows = pipeline_result_via_api["rows"]
        qty_status_overridden = []
        for r in rows:
            if not r.get("has_fecha_divergence"):
                continue
            # If quantities match exactly (delta == 0), status should be MATCH regardless.
            declared = Decimal(str(r.get("declared_qty") or "0"))
            summed = Decimal(str(r.get("summed_qty") or "0"))
            delta = Decimal(str(r.get("delta") or "0"))
            if declared > 0 and delta == Decimal("0") and r["status"] != "MATCH":
                qty_status_overridden.append(r)

        assert len(qty_status_overridden) == 0, (
            f"{len(qty_status_overridden)} row(s) have has_fecha_divergence=True, "
            "delta=0 (quantities match) but status != MATCH. "
            "Divergence is incorrectly overriding the qty-driven status:\n"
            + "\n".join(
                f"  registro={v['registro']} status={v['status']} "
                f"declared_qty={v['declared_qty']} summed_qty={v['summed_qty']}"
                for v in qty_status_overridden
            )
        )

    def test_pipeline_response_includes_required_api_fields(
        self, pipeline_result_via_api: dict
    ) -> None:
        """Smoke-check that the API contract fields are present on every row.

        Guards against future schema drift where a field is renamed or dropped
        on the API side without a corresponding test update.
        """
        rows = pipeline_result_via_api["rows"]
        required_fields = {
            "registro",
            "material_canonical",
            "unidad",
            "declared_qty",
            "summed_qty",
            "delta",
            "status",
            "match_method",
            "has_fecha_divergence",
            "requires_review",
        }
        for row in rows[:5]:  # check a sample — full schema is static
            missing = required_fields - set(row.keys())
            assert not missing, (
                f"API row is missing required fields: {missing}\nrow={row}"
            )
