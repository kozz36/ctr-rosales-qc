"""Tests for GET /runs/{run_id}/pages/{page}/image (issue #27 — full-res page viewer).

Sibling of the thumbnail endpoint (test_thumbnail_fallback.py) but renders the
page at ~200 DPI and caches to a DISTINCT path (pages/full/{page:04d}.png) so it
never clobbers the 120-DPI thumbnail cache (pages/{page:04d}.png).

Verifies:
  (1) No cached full PNG + valid source PDF → 200 image/png from a 200-DPI fitz render.
  (2) Cached full PNG at pages/full/{page:04d}.png is served verbatim.
  (3) The full render does NOT write to the thumbnail cache path (pages/{page:04d}.png).
  (4) page index out of range → 404.
  (5) run has no ctx yet → 409.
  (6) unknown run_id → 404.

TDD: written BEFORE implementation (RED → GREEN).
"""

from __future__ import annotations

import uuid
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
    """Build a minimal valid PDF using fitz (PyMuPDF) — no real PDF required."""
    import fitz  # noqa: PLC0415

    doc = fitz.open()
    for _ in range(page_count):
        doc.new_page(width=100, height=100)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    app = create_app()
    config = AppConfig(output_dir=tmp_path / "runs")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    app.state.config = config
    app.state.run_registry = {}
    return TestClient(app, raise_server_exceptions=True)


def _seed_run_with_ctx(client: TestClient, run_id: str, ctx: Any) -> None:
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


class TestFullPageRender:
    """200-DPI render from the source PDF when no cached full PNG exists."""

    def test_returns_200_image_png_from_pdf(self, client: TestClient, tmp_path: Path) -> None:
        run_id = str(uuid.uuid4())
        run_dir = tmp_path / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = run_dir / f"{run_id}.pdf"
        _build_minimal_pdf(pdf_path, page_count=2)

        ctx = RunContext(pdf_path=pdf_path, output_base=tmp_path / "runs", run_id=run_id)
        _seed_run_with_ctx(client, run_id, ctx)

        resp = client.get(f"/api/v1/runs/{run_id}/pages/0/image")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/png")
        assert resp.content[:4] == b"\x89PNG"

    def test_full_render_does_not_clobber_thumbnail_cache(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """The full render caches to pages/full/, NEVER to the thumbnail path pages/."""
        run_id = str(uuid.uuid4())
        run_dir = tmp_path / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = run_dir / f"{run_id}.pdf"
        _build_minimal_pdf(pdf_path, page_count=2)

        ctx = RunContext(pdf_path=pdf_path, output_base=tmp_path / "runs", run_id=run_id)
        _seed_run_with_ctx(client, run_id, ctx)

        resp = client.get(f"/api/v1/runs/{run_id}/pages/0/image")
        assert resp.status_code == 200

        thumb_cache = run_dir / "pages" / "0000.png"
        full_cache = run_dir / "pages" / "full" / "0000.png"
        # Full render must NOT write the thumbnail cache file...
        assert not thumb_cache.exists(), "full-res render clobbered the 120-DPI thumbnail cache"
        # ...and SHOULD persist its own distinct cache.
        assert full_cache.exists(), "full-res render did not cache to pages/full/"

    def test_full_render_larger_than_thumbnail(self, client: TestClient, tmp_path: Path) -> None:
        """200 DPI must produce more bytes than the 120-DPI thumbnail of the same page."""
        run_id = str(uuid.uuid4())
        run_dir = tmp_path / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = run_dir / f"{run_id}.pdf"
        _build_minimal_pdf(pdf_path, page_count=2)

        ctx = RunContext(pdf_path=pdf_path, output_base=tmp_path / "runs", run_id=run_id)
        _seed_run_with_ctx(client, run_id, ctx)

        thumb = client.get(f"/api/v1/runs/{run_id}/pages/0/thumbnail")
        full = client.get(f"/api/v1/runs/{run_id}/pages/0/image")
        assert thumb.status_code == 200
        assert full.status_code == 200
        assert len(full.content) > len(thumb.content)


class TestFullPageCachePreferred:
    """A cached full PNG is served verbatim instead of re-rendering."""

    def test_cached_full_png_served(self, client: TestClient, tmp_path: Path) -> None:
        run_id = str(uuid.uuid4())
        run_dir = tmp_path / "runs" / run_id
        full_dir = run_dir / "pages" / "full"
        full_dir.mkdir(parents=True, exist_ok=True)

        _minimal_png = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde"
            b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xdc\xccY\xe7"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        (full_dir / "0000.png").write_bytes(_minimal_png)

        pdf_path = run_dir / f"{run_id}.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")  # must NOT be opened — cache wins.

        ctx = RunContext(pdf_path=pdf_path, output_base=tmp_path / "runs", run_id=run_id)
        _seed_run_with_ctx(client, run_id, ctx)

        resp = client.get(f"/api/v1/runs/{run_id}/pages/0/image")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/png")
        assert resp.content == _minimal_png


class TestFullPageOutOfRange:
    def test_out_of_range_page_returns_404(self, client: TestClient, tmp_path: Path) -> None:
        run_id = str(uuid.uuid4())
        run_dir = tmp_path / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = run_dir / f"{run_id}.pdf"
        _build_minimal_pdf(pdf_path, page_count=2)

        ctx = RunContext(pdf_path=pdf_path, output_base=tmp_path / "runs", run_id=run_id)
        _seed_run_with_ctx(client, run_id, ctx)

        resp = client.get(f"/api/v1/runs/{run_id}/pages/2/image")
        assert resp.status_code == 404


class TestFullPageNoCtx:
    def test_no_ctx_returns_409(self, client: TestClient, tmp_path: Path) -> None:
        run_id = str(uuid.uuid4())
        _seed_run_no_ctx(client, run_id)
        resp = client.get(f"/api/v1/runs/{run_id}/pages/0/image")
        assert resp.status_code == 409


class TestFullPageUnknownRun:
    def test_unknown_run_returns_404(self, client: TestClient) -> None:
        resp = client.get("/api/v1/runs/nonexistent/pages/0/image")
        assert resp.status_code == 404
