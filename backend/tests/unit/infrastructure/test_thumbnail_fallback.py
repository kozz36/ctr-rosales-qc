"""Tests for GET /runs/{run_id}/pages/{page}/thumbnail fallback (Fix A — issue #17).

Verifies the fallback chain:
  (1) Deskewed PNG exists → serve it (preserves OCR-on behaviour).
  (2) Deskewed PNG absent + source PDF present → render from PDF via fitz → 200 image/png.
  (3) page index out of range → 404.
  (4) run has no ctx yet → 409.

Uses a tiny 2-page blank fixture PDF built with fitz inside the test — no real PDF required.

TDD: tests written BEFORE implementation (RED → GREEN).
"""

from __future__ import annotations

import struct
import uuid
import zlib
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from reconciliation.application.config import AppConfig
from reconciliation.application.run_context import RunContext
from reconciliation.infrastructure.api.main import create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_minimal_pdf(path: Path, page_count: int = 2) -> Path:
    """Build a minimal valid 2-page PDF using fitz (PyMuPDF).

    This avoids any dependency on the real 493-page production PDF.
    """
    import fitz  # noqa: PLC0415

    doc = fitz.open()
    for _ in range(page_count):
        doc.new_page(width=100, height=100)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    """TestClient with fake config and empty run registry."""
    app = create_app()
    config = AppConfig(output_dir=tmp_path / "runs")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    app.state.config = config
    app.state.run_registry = {}
    return TestClient(app, raise_server_exceptions=True)


def _seed_run_with_ctx(
    client: TestClient,
    run_id: str,
    ctx: Any,
) -> None:
    """Inject a completed run entry (status=review) with a real RunContext."""
    registry = client.app.state.run_registry  # type: ignore[attr-defined]
    svc = MagicMock()
    svc.rows = []
    svc.guias = []
    svc.get_audit_trail.return_value = []
    registry[run_id] = {
        "status": "review",
        "ctx": ctx,
        "review_service": svc,
        "result": None,
        "vision_calls_made": 0,
        "warnings": [],
        "error": None,
    }


def _seed_run_no_ctx(client: TestClient, run_id: str) -> None:
    """Inject a run still processing (ctx=None)."""
    registry = client.app.state.run_registry  # type: ignore[attr-defined]
    registry[run_id] = {
        "status": "processing",
        "ctx": None,
        "review_service": None,
        "result": None,
        "vision_calls_made": 0,
        "warnings": [],
        "error": None,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestThumbnailFallbackPDF:
    """Thumbnail returns 200 image/png from the source PDF when deskewed PNG is absent."""

    def test_returns_200_image_png_when_no_deskewed_png(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Core contract: no deskewed PNG + valid source PDF → 200 image/png from fitz render."""
        run_id = str(uuid.uuid4())
        run_dir = tmp_path / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        # Build a real 2-page PDF fixture and place it as the source PDF.
        pdf_path = run_dir / f"{run_id}.pdf"
        _build_minimal_pdf(pdf_path, page_count=2)

        # pages/ dir does NOT exist → no deskewed PNG.
        ctx = RunContext(
            pdf_path=pdf_path,
            output_base=tmp_path / "runs",
            run_id=run_id,
        )
        _seed_run_with_ctx(client, run_id, ctx)

        resp = client.get(f"/api/v1/runs/{run_id}/pages/0/thumbnail")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/png")
        # Response body must be a non-trivial PNG (starts with PNG magic bytes)
        assert resp.content[:4] == b"\x89PNG"

    def test_second_page_also_renderable(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Page index 1 of a 2-page PDF is also served correctly."""
        run_id = str(uuid.uuid4())
        run_dir = tmp_path / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        pdf_path = run_dir / f"{run_id}.pdf"
        _build_minimal_pdf(pdf_path, page_count=2)

        ctx = RunContext(
            pdf_path=pdf_path,
            output_base=tmp_path / "runs",
            run_id=run_id,
        )
        _seed_run_with_ctx(client, run_id, ctx)

        resp = client.get(f"/api/v1/runs/{run_id}/pages/1/thumbnail")
        assert resp.status_code == 200
        assert resp.content[:4] == b"\x89PNG"


class TestThumbnailDeskewedPngPreferred:
    """When the deskewed PNG exists, it is served instead of rendering from the PDF."""

    def test_deskewed_png_served_when_present(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        run_id = str(uuid.uuid4())
        run_dir = tmp_path / "runs" / run_id
        pages_dir = run_dir / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)

        # Write a tiny but valid PNG as the deskewed render.
        # Minimal 1x1 white PNG bytes (hardcoded valid PNG).
        _minimal_png = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde"
            b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xdc\xccY\xe7"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        deskewed_file = pages_dir / "0000.png"
        deskewed_file.write_bytes(_minimal_png)

        pdf_path = run_dir / f"{run_id}.pdf"
        # No real PDF needed — the deskewed PNG should be served first.
        # Write a dummy file so ctx.pdf_path exists (won't be opened).
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        ctx = RunContext(
            pdf_path=pdf_path,
            output_base=tmp_path / "runs",
            run_id=run_id,
        )
        _seed_run_with_ctx(client, run_id, ctx)

        resp = client.get(f"/api/v1/runs/{run_id}/pages/0/thumbnail")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/png")
        # Body must be exactly the deskewed PNG we planted (not a fitz render).
        assert resp.content == _minimal_png


class TestThumbnailOutOfRange:
    """page index >= page_count → 404."""

    def test_out_of_range_page_returns_404(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        run_id = str(uuid.uuid4())
        run_dir = tmp_path / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        pdf_path = run_dir / f"{run_id}.pdf"
        _build_minimal_pdf(pdf_path, page_count=2)

        ctx = RunContext(
            pdf_path=pdf_path,
            output_base=tmp_path / "runs",
            run_id=run_id,
        )
        _seed_run_with_ctx(client, run_id, ctx)

        # Page 2 is out of range for a 2-page PDF (0-indexed).
        resp = client.get(f"/api/v1/runs/{run_id}/pages/2/thumbnail")
        assert resp.status_code == 404

    def test_negative_page_returns_422_or_404(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Negative page index is invalid — FastAPI path int constraint or 404."""
        run_id = str(uuid.uuid4())
        run_dir = tmp_path / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        pdf_path = run_dir / f"{run_id}.pdf"
        _build_minimal_pdf(pdf_path, page_count=2)

        ctx = RunContext(
            pdf_path=pdf_path,
            output_base=tmp_path / "runs",
            run_id=run_id,
        )
        _seed_run_with_ctx(client, run_id, ctx)

        resp = client.get(f"/api/v1/runs/{run_id}/pages/-1/thumbnail")
        # FastAPI treats negative int in path as 422 (invalid path param) or 404.
        assert resp.status_code in (404, 422)


class TestThumbnailNoCtx:
    """run exists but ctx=None → 409 (run not yet processed)."""

    def test_no_ctx_returns_409(self, client: TestClient, tmp_path: Path) -> None:
        run_id = str(uuid.uuid4())
        _seed_run_no_ctx(client, run_id)

        resp = client.get(f"/api/v1/runs/{run_id}/pages/0/thumbnail")
        assert resp.status_code == 409


class TestThumbnailUnknownRun:
    """Unknown run_id → 404."""

    def test_unknown_run_returns_404(self, client: TestClient) -> None:
        resp = client.get("/api/v1/runs/nonexistent/pages/0/thumbnail")
        assert resp.status_code == 404
