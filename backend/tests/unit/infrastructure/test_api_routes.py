"""FastAPI route tests — full coverage via TestClient with fake pipeline.

All pipeline/ML/SDK execution is bypassed via dependency_overrides and
direct registry manipulation.  No real PDF, no real OCR, no real vision.

Covers:
- POST /runs: upload validation (reject non-pdf, oversize, success)
- GET /runs/{run_id}: status polling (pending, review, error, 404)
- GET /runs/{run_id}/table: table fetch (200, 409, 404)
- PATCH /runs/{run_id}/rows/{row_id}: edit (200, 422, 409, 404)
- POST /runs/{run_id}/reassign: reassign (200, 422, 404)
- POST /runs/{run_id}/export: export (200, 409, 404)
- GET /runs/{run_id}/audit: audit trail (200, 404)
"""

from __future__ import annotations

import io
import json
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from reconciliation.application.config import AppConfig
from reconciliation.domain.models import (
    GuiaDeRemision,
    MaterialLine,
    ReconciliationRow,
    Registro,
)
from reconciliation.infrastructure.api.main import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_row(
    registro: str = "R001",
    material: str = "barra 3/8",
    status: str = "MATCH",
    declared: str = "10.0",
    summed: str = "10.0",
) -> ReconciliationRow:
    from reconciliation.domain.models import GuiaContribution  # noqa: PLC0415
    d = Decimal(declared)
    s = Decimal(summed)
    # summed_qty is a computed property (sum of guias[*].cantidad).
    # Build a dummy contribution so summed_qty reflects the expected value.
    contrib = GuiaContribution(
        guia_id="test-guia",
        source_pages=[1, 2],
        cantidad=s,
        unidad="TN",
        confidence=0.9,
        identity_source="ocr_fallback",
    )
    return ReconciliationRow(
        registro=registro,
        fecha=date(2024, 1, 15),
        material_canonical=material,
        unidad="TN",
        declared_qty=d,
        delta=d - s,
        status=status,  # type: ignore[arg-type]
        source_pages=[1, 2],
        min_confidence=0.9,
        guias=[contrib],
    )


def _make_guia(guia_id: str = "guia_page_5", registro: str = "R001") -> GuiaDeRemision:
    return GuiaDeRemision(
        guia_id=guia_id,
        registro=registro,
        fecha=date(2024, 1, 15),
        fecha_confidence=0.95,
        lines=[
            MaterialLine(
                description_raw="BARRA 3/8",
                description_canonical="barra 3/8",
                unidad="TN",
                cantidad=Decimal("10.0"),
            )
        ],
        source_pages=[5],
    )


def _make_review_service(
    rows: list[ReconciliationRow] | None = None,
    audit_events: list[dict] | None = None,
    edit_result: list[ReconciliationRow] | None = None,
    reassign_result: list[ReconciliationRow] | None = None,
) -> MagicMock:
    """Build a MagicMock ReviewService with pre-configured return values.

    Pass an explicit list (even empty []) to override the default single-row list.
    """
    default_rows = [_make_row()]
    svc = MagicMock()
    svc.rows = rows if rows is not None else default_rows
    svc.get_audit_trail.return_value = audit_events if audit_events is not None else []
    svc.apply_edit.return_value = edit_result if edit_result is not None else default_rows
    svc.apply_reassignment.return_value = (
        reassign_result if reassign_result is not None else default_rows
    )
    return svc


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    """TestClient with a fake config and empty run registry."""
    app = create_app()

    # Inject fake config and empty registry before first request
    config = AppConfig(output_dir=tmp_path / "runs")
    config.output_dir.mkdir(parents=True, exist_ok=True)

    app.state.config = config
    app.state.run_registry = {}

    return TestClient(app, raise_server_exceptions=True)


def _seed_run(
    client: TestClient,
    run_id: str,
    status: str = "review",
    review_service: MagicMock | None = None,
    error: str | None = None,
    ctx: Any = None,
) -> None:
    """Directly inject a run entry into the registry (bypasses pipeline)."""
    registry = client.app.state.run_registry  # type: ignore[attr-defined]
    registry[run_id] = {
        "status": status,
        "review_service": review_service,
        "ctx": ctx,
        "result": None,
        "vision_calls_made": 3,
        "warnings": [],
        "error": error,
    }


# ---------------------------------------------------------------------------
# POST /runs — upload validation
# ---------------------------------------------------------------------------


class TestPostRuns:
    def test_rejects_non_pdf_content_type(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/runs",
            files={"file": ("data.txt", b"hello", "text/plain")},
        )
        assert resp.status_code == 415
        assert "PDF" in resp.json()["detail"]

    def test_rejects_html_content_type(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/runs",
            files={"file": ("page.html", b"<html>", "text/html")},
        )
        assert resp.status_code == 415

    def test_rejects_oversized_file(self, client: TestClient) -> None:
        from reconciliation.infrastructure.api.routes import MAX_UPLOAD_BYTES  # noqa: PLC0415

        # Patch the cap to 10 bytes so our test is fast
        with patch("reconciliation.infrastructure.api.routes.MAX_UPLOAD_BYTES", 10):
            resp = client.post(
                "/api/v1/runs",
                files={"file": ("big.pdf", b"A" * 50, "application/pdf")},
            )
        assert resp.status_code == 413

    def test_accepts_valid_pdf_returns_202(self, client: TestClient, tmp_path: Path) -> None:
        # Patch the background task so we don't actually run the pipeline
        with patch("reconciliation.infrastructure.api.routes._run_pipeline_background"):
            resp = client.post(
                "/api/v1/runs",
                files={"file": ("test.pdf", b"%PDF-1.4 fake content", "application/pdf")},
            )
        assert resp.status_code == 202
        body = resp.json()
        assert "run_id" in body
        assert body["status"] == "pending"

    def test_client_filename_not_used_on_disk(self, client: TestClient, tmp_path: Path) -> None:
        """The stored filename must be the run_id, never the client-supplied name."""
        with patch("reconciliation.infrastructure.api.routes._run_pipeline_background"):
            resp = client.post(
                "/api/v1/runs",
                files={"file": ("../../evil.pdf", b"%PDF-1.4", "application/pdf")},
            )
        assert resp.status_code == 202
        run_id = resp.json()["run_id"]
        # The PDF on disk must be named after run_id
        registry = client.app.state.run_registry  # type: ignore[attr-defined]
        stored_path = registry[run_id]["pdf_path"]
        assert "evil" not in stored_path
        assert run_id in stored_path

    def test_missing_file_field_returns_422(self, client: TestClient) -> None:
        resp = client.post("/api/v1/runs")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /runs/{run_id}
# ---------------------------------------------------------------------------


class TestGetRunStatus:
    def test_returns_404_for_unknown_run(self, client: TestClient) -> None:
        resp = client.get("/api/v1/runs/nonexistent")
        assert resp.status_code == 404

    def test_returns_pending_status(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        _seed_run(client, run_id, status="pending")
        resp = client.get(f"/api/v1/runs/{run_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"

    def test_returns_review_status(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        svc = _make_review_service()
        _seed_run(client, run_id, status="review", review_service=svc)
        resp = client.get(f"/api/v1/runs/{run_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "review"

    def test_returns_error_status_with_message(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        _seed_run(client, run_id, status="error", error="fitz exploded")
        resp = client.get(f"/api/v1/runs/{run_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "error"
        assert "fitz exploded" in body["error"]


# ---------------------------------------------------------------------------
# GET /runs/{run_id}/table
# ---------------------------------------------------------------------------


class TestGetTable:
    def test_returns_404_for_unknown_run(self, client: TestClient) -> None:
        resp = client.get("/api/v1/runs/nope/table")
        assert resp.status_code == 404

    def test_returns_409_when_run_not_yet_in_review(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        _seed_run(client, run_id, status="processing")
        resp = client.get(f"/api/v1/runs/{run_id}/table")
        assert resp.status_code == 409

    def test_returns_rows_when_ready(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        rows = [_make_row("R001"), _make_row("R002", status="MISMATCH")]
        svc = _make_review_service(rows=rows)
        _seed_run(client, run_id, review_service=svc)
        resp = client.get(f"/api/v1/runs/{run_id}/table")
        assert resp.status_code == 200
        body = resp.json()
        assert body["run_id"] == run_id
        assert len(body["rows"]) == 2

    def test_row_contains_expected_fields(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        svc = _make_review_service(rows=[_make_row()])
        _seed_run(client, run_id, review_service=svc)
        resp = client.get(f"/api/v1/runs/{run_id}/table")
        row = resp.json()["rows"][0]
        assert "row_id" in row
        assert "registro" in row
        assert "material_canonical" in row
        assert "delta" in row
        assert "status" in row

    def test_table_returns_empty_when_no_rows(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        svc = _make_review_service(rows=[])
        _seed_run(client, run_id, review_service=svc)
        resp = client.get(f"/api/v1/runs/{run_id}/table")
        assert resp.status_code == 200
        assert resp.json()["rows"] == []

    def test_table_includes_unresolved_guias_field(self, client: TestClient) -> None:
        """GET /table always returns an ``unresolved_guias`` field (REV-C04 / REC-C05)."""
        run_id = str(uuid.uuid4())
        svc = _make_review_service(rows=[_make_row()])
        _seed_run(client, run_id, review_service=svc)
        resp = client.get(f"/api/v1/runs/{run_id}/table")
        assert resp.status_code == 200
        body = resp.json()
        assert "unresolved_guias" in body
        # Default mock guía has registro="R001" (not None) → unresolved_guias is empty
        assert body["unresolved_guias"] == []

    def test_table_unresolved_guias_populated_for_none_registro(self, client: TestClient) -> None:
        """A guía with ``registro=None`` appears in ``unresolved_guias``, NOT in ``rows``."""
        run_id = str(uuid.uuid4())

        # Build a review service mock that has one unresolved guía (registro=None)
        unresolved_guia = _make_guia(guia_id="T009-UNRESOLVED", registro=None)  # type: ignore[arg-type]
        rows: list[ReconciliationRow] = []  # no reconciled rows
        svc = MagicMock()
        svc.rows = rows
        svc.guias = [unresolved_guia]
        svc.get_audit_trail.return_value = []

        _seed_run(client, run_id, review_service=svc)
        resp = client.get(f"/api/v1/runs/{run_id}/table")
        assert resp.status_code == 200
        body = resp.json()

        # rows must be empty — unresolved guías MUST NOT appear in rows (REC-C05)
        assert body["rows"] == []

        # unresolved_guias must contain the guía
        assert len(body["unresolved_guias"]) == 1
        unresolved = body["unresolved_guias"][0]
        assert unresolved["guia_id"] == "T009-UNRESOLVED"
        assert unresolved["identity_source"] == "ocr_fallback"
        assert unresolved["source_pages"] == [5]

    def test_table_resolved_guias_not_in_unresolved(self, client: TestClient) -> None:
        """A guía with a non-None registro MUST NOT appear in ``unresolved_guias``."""
        run_id = str(uuid.uuid4())
        resolved_guia = _make_guia(guia_id="T009-RESOLVED", registro="R001")
        svc = MagicMock()
        svc.rows = [_make_row("R001")]
        svc.guias = [resolved_guia]
        svc.get_audit_trail.return_value = []

        _seed_run(client, run_id, review_service=svc)
        resp = client.get(f"/api/v1/runs/{run_id}/table")
        assert resp.status_code == 200
        body = resp.json()
        assert body["unresolved_guias"] == []


# ---------------------------------------------------------------------------
# PATCH /runs/{run_id}/rows/{row_id}
# ---------------------------------------------------------------------------


class TestPatchRow:
    def test_returns_404_for_unknown_run(self, client: TestClient) -> None:
        resp = client.patch(
            "/api/v1/runs/nope/rows/r1",
            json={"guia_id": "g1", "field": "fecha", "value": "2024-01-15"},
        )
        assert resp.status_code == 404

    def test_returns_409_when_not_in_review(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        _seed_run(client, run_id, status="processing")
        resp = client.patch(
            f"/api/v1/runs/{run_id}/rows/r1",
            json={"guia_id": "g1", "field": "fecha", "value": "2024-01-15"},
        )
        assert resp.status_code == 409

    def test_apply_edit_returns_updated_rows(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        updated = [_make_row()]
        svc = _make_review_service(edit_result=updated)
        _seed_run(client, run_id, review_service=svc)
        resp = client.patch(
            f"/api/v1/runs/{run_id}/rows/r1",
            json={"guia_id": "g1", "field": "fecha", "value": "2024-01-15"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["run_id"] == run_id
        assert len(body["rows"]) == 1
        svc.apply_edit.assert_called_once_with(
            guia_id="g1", field="fecha", new_value="2024-01-15"
        )

    def test_apply_edit_propagates_value_error_as_422(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        svc = _make_review_service()
        svc.apply_edit.side_effect = ValueError("guia not found")
        _seed_run(client, run_id, review_service=svc)
        resp = client.patch(
            f"/api/v1/runs/{run_id}/rows/r1",
            json={"guia_id": "missing", "field": "fecha", "value": "2024-01-15"},
        )
        assert resp.status_code == 422
        assert "guia not found" in resp.json()["detail"]

    def test_edit_with_null_value_accepted(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        svc = _make_review_service()
        _seed_run(client, run_id, review_service=svc)
        resp = client.patch(
            f"/api/v1/runs/{run_id}/rows/r1",
            json={"guia_id": "g1", "field": "fecha", "value": None},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /runs/{run_id}/reassign
# ---------------------------------------------------------------------------


class TestReassign:
    def test_returns_404_for_unknown_run(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/runs/nope/reassign",
            json={"guia_id": "g1", "new_registro": "R002"},
        )
        assert resp.status_code == 404

    def test_returns_409_when_not_in_review(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        _seed_run(client, run_id, status="pending")
        resp = client.post(
            f"/api/v1/runs/{run_id}/reassign",
            json={"guia_id": "g1", "new_registro": "R002"},
        )
        assert resp.status_code == 409

    def test_reassign_returns_updated_rows(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        updated = [_make_row("R002")]
        svc = _make_review_service(reassign_result=updated)
        _seed_run(client, run_id, review_service=svc)
        resp = client.post(
            f"/api/v1/runs/{run_id}/reassign",
            json={"guia_id": "g1", "new_registro": "R002", "new_fecha": "2024-01-20"},
        )
        assert resp.status_code == 200
        svc.apply_reassignment.assert_called_once_with(
            guia_id="g1",
            new_registro="R002",
            new_fecha="2024-01-20",
        )

    def test_reassign_propagates_value_error_as_422(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        svc = _make_review_service()
        svc.apply_reassignment.side_effect = ValueError("guia_id not found")
        _seed_run(client, run_id, review_service=svc)
        resp = client.post(
            f"/api/v1/runs/{run_id}/reassign",
            json={"guia_id": "bad", "new_registro": "R002"},
        )
        assert resp.status_code == 422

    def test_reassign_with_null_fecha_accepted(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        svc = _make_review_service()
        _seed_run(client, run_id, review_service=svc)
        resp = client.post(
            f"/api/v1/runs/{run_id}/reassign",
            json={"guia_id": "g1", "new_registro": "R002", "new_fecha": None},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /runs/{run_id}/export
# ---------------------------------------------------------------------------


class TestExport:
    def test_returns_404_for_unknown_run(self, client: TestClient) -> None:
        resp = client.post("/api/v1/runs/nope/export", json={"fmt": "xlsx"})
        assert resp.status_code == 404

    def test_returns_409_when_not_in_review(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        _seed_run(client, run_id, status="pending")
        resp = client.post(f"/api/v1/runs/{run_id}/export", json={"fmt": "xlsx"})
        assert resp.status_code == 409

    def test_export_xlsx_returns_file(self, client: TestClient, tmp_path: Path) -> None:
        run_id = str(uuid.uuid4())
        svc = _make_review_service()
        # Build a fake RunContext with a real tmp run_dir
        fake_ctx = MagicMock()
        run_dir = tmp_path / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        fake_ctx.run_dir = run_dir

        _seed_run(client, run_id, review_service=svc, ctx=fake_ctx)

        # Mock ExcelReportAdapter.export to write a minimal file
        fake_export_path = run_dir / "export.xlsx"
        fake_export_path.write_bytes(b"PK")  # minimal xlsx magic bytes placeholder

        with patch(
            "reconciliation.adapters.report.xlsx_report.ExcelReportAdapter"
        ) as MockExporter:
            instance = MockExporter.return_value
            instance.export.return_value = fake_export_path
            resp = client.post(f"/api/v1/runs/{run_id}/export", json={"fmt": "xlsx"})

        assert resp.status_code == 200
        # Content-Disposition must carry a filename (FileResponse header)
        cd = resp.headers.get("content-disposition", "")
        assert "filename" in cd

    def test_export_csv_accepted(self, client: TestClient, tmp_path: Path) -> None:
        run_id = str(uuid.uuid4())
        svc = _make_review_service()
        fake_ctx = MagicMock()
        run_dir = tmp_path / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        fake_ctx.run_dir = run_dir

        _seed_run(client, run_id, review_service=svc, ctx=fake_ctx)

        fake_export_path = run_dir / "export.csv"
        fake_export_path.write_text("header,data\n", encoding="utf-8")

        with patch(
            "reconciliation.adapters.report.xlsx_report.ExcelReportAdapter"
        ) as MockExporter:
            instance = MockExporter.return_value
            instance.export.return_value = fake_export_path
            resp = client.post(f"/api/v1/runs/{run_id}/export", json={"fmt": "csv"})

        assert resp.status_code == 200

    def test_export_invalid_fmt_returns_422(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        svc = _make_review_service()
        _seed_run(client, run_id, review_service=svc)
        resp = client.post(f"/api/v1/runs/{run_id}/export", json={"fmt": "pdf"})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /runs/{run_id}/audit
# ---------------------------------------------------------------------------


class TestGetAudit:
    def test_returns_404_for_unknown_run(self, client: TestClient) -> None:
        resp = client.get("/api/v1/runs/nope/audit")
        assert resp.status_code == 404

    def test_returns_409_when_not_in_review(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        _seed_run(client, run_id, status="processing")
        resp = client.get(f"/api/v1/runs/{run_id}/audit")
        assert resp.status_code == 409

    def test_returns_empty_audit_when_no_edits(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        svc = _make_review_service(audit_events=[])
        _seed_run(client, run_id, review_service=svc)
        resp = client.get(f"/api/v1/runs/{run_id}/audit")
        assert resp.status_code == 200
        body = resp.json()
        assert body["run_id"] == run_id
        assert body["events"] == []

    def test_returns_audit_events(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        events = [
            {
                "timestamp": "2024-01-15T10:00:00+00:00",
                "kind": "field_edit",
                "target": {"guia_id": "g1"},
                "field": "fecha",
                "old_value": "None",
                "new_value": "2024-01-15",
            }
        ]
        svc = _make_review_service(audit_events=events)
        _seed_run(client, run_id, review_service=svc)
        resp = client.get(f"/api/v1/runs/{run_id}/audit")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["events"]) == 1
        assert body["events"][0]["kind"] == "field_edit"
        assert body["events"][0]["field"] == "fecha"


# ---------------------------------------------------------------------------
# Security: path traversal guard
# ---------------------------------------------------------------------------


class TestUploadSecurity:
    """Verify security invariants on the upload endpoint."""

    def test_stored_pdf_path_does_not_contain_traversal(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Client filename containing '../' must not be reflected in stored path."""
        with patch("reconciliation.infrastructure.api.routes._run_pipeline_background"):
            resp = client.post(
                "/api/v1/runs",
                files={"file": ("../../../etc/passwd.pdf", b"%PDF-1.4", "application/pdf")},
            )
        assert resp.status_code == 202
        run_id = resp.json()["run_id"]
        registry = client.app.state.run_registry  # type: ignore[attr-defined]
        stored = registry[run_id]["pdf_path"]
        assert ".." not in stored
        assert "etc" not in stored
        assert "passwd" not in stored


# ---------------------------------------------------------------------------
# GET /runs/{run_id}/pages/{page}/thumbnail (S1.8)
# ---------------------------------------------------------------------------


class TestGetThumbnail:
    def _seed_with_pages_dir(
        self,
        client: TestClient,
        run_id: str,
        tmp_path: Path,
        pages: list[int] | None = None,
    ) -> Path:
        """Seed a run with a fake ctx that has a pages directory.

        ``ctx.pdf_path`` is set to a non-existent path so the fitz fallback
        branch returns 404 (not 500) when the deskewed PNG is also absent.
        """
        run_dir = tmp_path / "runs" / run_id
        pages_dir = run_dir / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)

        # Write minimal PNG bytes for requested page numbers
        for page_idx in (pages or [0]):
            (pages_dir / f"{page_idx:04d}.png").write_bytes(b"\x89PNG\r\n\x1a\n")

        fake_ctx = MagicMock()
        fake_ctx.run_dir = run_dir
        # Non-existent pdf_path: triggers the pdf_path.exists() → False → 404 branch
        # when no deskewed PNG exists (i.e. the page index is out of range in this test).
        fake_ctx.pdf_path = run_dir / f"{run_id}.pdf"

        registry = client.app.state.run_registry  # type: ignore[attr-defined]
        registry[run_id] = {
            "status": "review",
            "review_service": _make_review_service(),
            "ctx": fake_ctx,
            "result": None,
            "vision_calls_made": 0,
            "warnings": [],
            "error": None,
        }
        return pages_dir

    def test_returns_200_with_png_for_existing_page(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        run_id = str(uuid.uuid4())
        self._seed_with_pages_dir(client, run_id, tmp_path, pages=[0])

        resp = client.get(f"/api/v1/runs/{run_id}/pages/0/thumbnail")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/png")

    def test_returns_404_for_missing_page(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        run_id = str(uuid.uuid4())
        self._seed_with_pages_dir(client, run_id, tmp_path, pages=[0])

        resp = client.get(f"/api/v1/runs/{run_id}/pages/99/thumbnail")
        assert resp.status_code == 404

    def test_returns_404_for_unknown_run(self, client: TestClient) -> None:
        resp = client.get("/api/v1/runs/nope/pages/0/thumbnail")
        assert resp.status_code == 404

    def test_returns_409_when_ctx_not_ready(self, client: TestClient) -> None:
        """Run exists but ctx is None (still processing) → 409."""
        run_id = str(uuid.uuid4())
        _seed_run(client, run_id, status="processing")
        resp = client.get(f"/api/v1/runs/{run_id}/pages/0/thumbnail")
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# PATCH /runs/{run_id}/guias/{guia_id}/lines (S1.7 — rev-2 line edit)
# ---------------------------------------------------------------------------


class TestPatchGuiaLine:
    def test_returns_404_for_unknown_run(self, client: TestClient) -> None:
        resp = client.patch(
            "/api/v1/runs/nope/guias/T001-0001/lines",
            json={"line_index": 0, "cantidad": 100.0},
        )
        assert resp.status_code == 404

    def test_returns_409_when_not_in_review(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        _seed_run(client, run_id, status="processing")
        resp = client.patch(
            f"/api/v1/runs/{run_id}/guias/T001-0001/lines",
            json={"line_index": 0, "cantidad": 100.0},
        )
        assert resp.status_code == 409

    def test_apply_guia_line_edit_returns_updated_rows(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        updated = [_make_row()]
        svc = _make_review_service()
        svc.apply_guia_line_edit = MagicMock(return_value=updated)
        _seed_run(client, run_id, review_service=svc)

        resp = client.patch(
            f"/api/v1/runs/{run_id}/guias/T001-0001/lines",
            json={"line_index": 0, "cantidad": 200.0},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["run_id"] == run_id
        assert len(body["rows"]) == 1

    def test_negative_cantidad_returns_422(self, client: TestClient) -> None:
        """Pydantic ge=0 constraint: cantidad < 0 → 422 before route handler."""
        run_id = str(uuid.uuid4())
        svc = _make_review_service()
        _seed_run(client, run_id, review_service=svc)
        resp = client.patch(
            f"/api/v1/runs/{run_id}/guias/T001-0001/lines",
            json={"line_index": 0, "cantidad": -1.0},
        )
        assert resp.status_code == 422

    def test_unknown_guia_id_returns_404(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        svc = _make_review_service()
        svc.apply_guia_line_edit = MagicMock(
            side_effect=ValueError("guia_id='ghost' not found")
        )
        _seed_run(client, run_id, review_service=svc)
        resp = client.patch(
            f"/api/v1/runs/{run_id}/guias/ghost/lines",
            json={"line_index": 0, "cantidad": 100.0},
        )
        assert resp.status_code == 404

    def test_idempotent_same_request_same_result(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        updated = [_make_row()]
        svc = _make_review_service()
        svc.apply_guia_line_edit = MagicMock(return_value=updated)
        _seed_run(client, run_id, review_service=svc)

        resp1 = client.patch(
            f"/api/v1/runs/{run_id}/guias/T001-0001/lines",
            json={"line_index": 0, "cantidad": 100.0},
        )
        resp2 = client.patch(
            f"/api/v1/runs/{run_id}/guias/T001-0001/lines",
            json={"line_index": 0, "cantidad": 100.0},
        )
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        # Both return the same rows
        assert resp1.json()["rows"] == resp2.json()["rows"]


# ---------------------------------------------------------------------------
# PATCH /runs/{run_id}/rows/{row_id} — summed_qty field rejected (S1.7 / REC-C04)
# ---------------------------------------------------------------------------


class TestPatchRowSummedQtyRejected:
    def test_summed_qty_field_returns_422(self, client: TestClient) -> None:
        """PATCH with field='summed_qty' must return 422 (REC-C04 / S1.7).

        The RowEditRequest.field Literal rejects 'summed_qty' at Pydantic validation.
        """
        run_id = str(uuid.uuid4())
        svc = _make_review_service()
        _seed_run(client, run_id, review_service=svc)
        resp = client.patch(
            f"/api/v1/runs/{run_id}/rows/r1",
            json={"guia_id": "g1", "field": "summed_qty", "value": "999"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /runs/{run_id}/table — guias[] inline (S1.7 / REC-C02)
# ---------------------------------------------------------------------------


class TestTableGuiasInline:
    def test_row_response_contains_guias_field(self, client: TestClient) -> None:
        """ReconciliationRowResponse must include a 'guias' list (REC-C02 / S1.7)."""
        run_id = str(uuid.uuid4())
        svc = _make_review_service(rows=[_make_row()])
        _seed_run(client, run_id, review_service=svc)
        resp = client.get(f"/api/v1/runs/{run_id}/table")
        assert resp.status_code == 200
        row = resp.json()["rows"][0]
        assert "guias" in row
        assert isinstance(row["guias"], list)

    def test_guia_contribution_fields_present(self, client: TestClient) -> None:
        """Each GuiaContributionResponse has the required fields."""
        run_id = str(uuid.uuid4())
        # _make_row() already includes a dummy GuiaContribution
        svc = _make_review_service(rows=[_make_row()])
        _seed_run(client, run_id, review_service=svc)
        resp = client.get(f"/api/v1/runs/{run_id}/table")
        row = resp.json()["rows"][0]
        assert len(row["guias"]) == 1
        contrib = row["guias"][0]
        assert "guia_id" in contrib
        assert "source_pages" in contrib
        assert "cantidad" in contrib
        assert "unidad" in contrib
        assert "confidence" in contrib


# ---------------------------------------------------------------------------
# R8.12: ReconciliationRowResponse.match_method (MAT-008, ADR-5)
# ---------------------------------------------------------------------------


class TestMatchMethodInTableResponse:
    """GET /runs/{run_id}/table rows include match_method field (R8.12)."""

    def _make_row_with_method(self, match_method: str) -> ReconciliationRow:
        from decimal import Decimal
        from reconciliation.domain.models import GuiaContribution

        return ReconciliationRow(
            registro="232",
            fecha=date(2024, 1, 15),
            material_canonical="BARRA A615 G60 1/2\" 9M",
            unidad="TN",
            declared_qty=Decimal("4.124"),
            delta=Decimal("0"),
            status="MATCH",
            source_pages=[5, 6, 8],
            match_method=match_method,  # type: ignore[arg-type]
            guias=[
                GuiaContribution(
                    guia_id="T009-0001",
                    source_pages=[5],
                    cantidad=Decimal("4.124"),
                    unidad="TN",
                    confidence=1.0,
                    identity_source="qr",
                )
            ],
        )

    def _get_table_response_rows(self, rows, client: TestClient, run_id: str) -> list:
        """Helper to set up a review service with given rows and GET table."""
        svc = _make_review_service(rows=rows)
        svc.guias = []  # no unresolved guias for these tests
        _seed_run(client, run_id, review_service=svc)
        resp = client.get(f"/api/v1/runs/{run_id}/table")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        return resp.json()["rows"]

    def test_row_has_match_method_field(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        rows = [self._make_row_with_method("deterministic")]
        result_rows = self._get_table_response_rows(rows, client, run_id)
        assert len(result_rows) == 1
        assert "match_method" in result_rows[0]

    def test_deterministic_match_method_in_response(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        rows = [self._make_row_with_method("deterministic")]
        result_rows = self._get_table_response_rows(rows, client, run_id)
        assert result_rows[0]["match_method"] == "deterministic"

    def test_llm_inferred_match_method_in_response(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        rows = [self._make_row_with_method("llm_inferred")]
        result_rows = self._get_table_response_rows(rows, client, run_id)
        assert result_rows[0]["match_method"] == "llm_inferred"

    def test_backward_compat_model_validate_no_match_method(self) -> None:
        """Old response dict without match_method → defaults to deterministic."""
        from reconciliation.infrastructure.api.schemas import ReconciliationRowResponse
        data = {
            "row_id": "232|2024-01-15|BARRA|TN",
            "registro": "232",
            "fecha": None,
            "material_canonical": "BARRA",
            "unidad": "TN",
            "declared_qty": "1.0",
            "summed_qty": "1.0",
            "delta": "0",
            "status": "MATCH",
            "source_pages": [],
        }
        row = ReconciliationRowResponse.model_validate(data)
        assert row.match_method == "deterministic"


# ---------------------------------------------------------------------------
# R9.6: fecha_divergence DTO fields (FDR-008, ADR-5)
# ---------------------------------------------------------------------------


class TestFechaDivergenceInTableResponse:
    """GET /runs/{run_id}/table surfaces divergence fields (R9.6)."""

    def _make_row(self, *, diverges: bool) -> ReconciliationRow:
        from decimal import Decimal
        from reconciliation.domain.models import GuiaContribution

        return ReconciliationRow(
            registro="232",
            fecha=date(2026, 5, 28),
            material_canonical="BARRA A615 G60 1/2\"",
            unidad="KG",
            declared_qty=Decimal("1000"),
            delta=Decimal("0"),
            status="MATCH",
            source_pages=[10],
            guias=[
                GuiaContribution(
                    guia_id="T009-0001",
                    source_pages=[10],
                    cantidad=Decimal("1000"),
                    unidad="KG",
                    confidence=1.0,
                    identity_source="qr",
                    fecha=date(2026, 4, 15) if diverges else date(2026, 5, 28),
                    fecha_divergence=diverges,
                    divergence_reason="fecha_divergence" if diverges else None,
                )
            ],
        )

    def _rows(self, rows, client: TestClient, run_id: str) -> list:
        svc = _make_review_service(rows=rows)
        svc.guias = []
        _seed_run(client, run_id, review_service=svc)
        resp = client.get(f"/api/v1/runs/{run_id}/table")
        assert resp.status_code == 200, resp.text
        return resp.json()["rows"]

    def test_contribution_has_divergence_fields(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        rows = self._rows([self._make_row(diverges=False)], client, run_id)
        contrib = rows[0]["guias"][0]
        assert "fecha" in contrib
        assert "fecha_divergence" in contrib
        assert "divergence_reason" in contrib

    def test_diverging_contribution_maps_true(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        rows = self._rows([self._make_row(diverges=True)], client, run_id)
        contrib = rows[0]["guias"][0]
        assert contrib["fecha_divergence"] is True
        assert contrib["divergence_reason"] == "fecha_divergence"
        assert rows[0]["has_fecha_divergence"] is True

    def test_non_diverging_defaults(self, client: TestClient) -> None:
        run_id = str(uuid.uuid4())
        rows = self._rows([self._make_row(diverges=False)], client, run_id)
        contrib = rows[0]["guias"][0]
        assert contrib["fecha_divergence"] is False
        assert contrib["divergence_reason"] is None
        assert rows[0]["has_fecha_divergence"] is False

    def test_backward_compat_model_validate_no_divergence_keys(self) -> None:
        from reconciliation.infrastructure.api.schemas import (
            GuiaContributionResponse,
            ReconciliationRowResponse,
        )

        contrib = GuiaContributionResponse.model_validate(
            {
                "guia_id": "T009-0001",
                "source_pages": [10],
                "cantidad": "1000",
                "unidad": "KG",
                "confidence": 1.0,
                "identity_source": "qr",
            }
        )
        assert contrib.fecha is None
        assert contrib.fecha_divergence is False
        assert contrib.divergence_reason is None

        row = ReconciliationRowResponse.model_validate(
            {
                "row_id": "232|2026-05-28|BARRA|KG",
                "registro": "232",
                "fecha": None,
                "material_canonical": "BARRA",
                "unidad": "KG",
                "declared_qty": "1000",
                "summed_qty": "1000",
                "delta": "0",
                "status": "MATCH",
                "source_pages": [],
            }
        )
        assert row.has_fecha_divergence is False
