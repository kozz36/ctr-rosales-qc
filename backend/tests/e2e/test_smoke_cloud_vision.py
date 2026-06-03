"""Cloud-vision accuracy smoke test for r10-containerized-verification (R10.9 / CONT-S04/S07/S08).

Pass criteria (D6):
  - Cloud qwen3.5:397b-cloud reads Registro 232 Protocolo page as date "2026-05-28"
    with confidence >= 0.85.
  - Token consumption logged: meter.calls >= 1, meter.total_tokens > 0 (CONT-S08).
  - Crop coords (0.60,0.04,1.00,0.22) confirmed as the calibrated starting estimate.

Calibration result (R10.9 — real run 2026-06-02):
  - Initial crop (0.60,0.04,1.00,0.22) returned date=2025-09-08 (model read the printed
    template revision date "Rev:02 Fecha:08/09/2025" instead of the handwritten field).
  - Calibrated to (0.60,0.14,1.00,0.22): excludes the revision row; shows only
    "Registro N°: 232 | Fecha: 28-05-26" → model returns {"date": "2026-05-28", "confidence": 1.0}.
  - Token consumption (calibrated crop): prompt=197, completion=520 (thinking), total=717.
    Much lower than first attempt (4492) because the cleaner image shortens the think phase.

Run:
  # Outside container (direct):
  cd backend && uv run pytest tests/e2e/test_smoke_cloud_vision.py -v -s -m e2e

  # Inside container (via compose):
  docker compose run --rm backend \\
    python -m pytest tests/e2e/test_smoke_cloud_vision.py -v -s --tb=short
"""

from __future__ import annotations

import io
import os
from datetime import date
from pathlib import Path

import pytest

# Skip the entire module if Ollama cloud is not configured / not running.
# This prevents the e2e smoke from breaking the regular unit test suite.
pytestmark = pytest.mark.e2e

_PDF_PATH_ENV = os.environ.get(
    "RECONCILIATION__PDF_PATH",
    "/data/input.pdf",  # compose mount point
)

# Host-side path for running directly (outside container)
_PDF_HOST_PATH = (
    "/data/Projects/ctr-rosales-qc/"
    "Informe de detalle del formulario-202606020255.pdf"
)

_OLLAMA_BASE_URL = os.environ.get(
    "RECONCILIATION__VISION__OLLAMA__BASE_URL",
    "http://localhost:11434/v1",  # default for host-side runs
)

_CLOUD_MODEL = os.environ.get(
    "RECONCILIATION__VISION__OLLAMA__MODEL",
    "qwen3.5:397b-cloud",
)

# Protocolo page index for section #4252 / Registro 232 (0-based)
_PROTOCOLO_PAGE_IDX = 3

# Calibrated protocolo_crop box (R10.9 — 2026-06-02)
# Targets Registro N° + Fecha rows only; excludes printed "Rev:02 Fecha:08/09/2025" row.
_PROTO_CROP = (0.60, 0.14, 1.00, 0.22)
# R7 stamp crop for guía pages
_STAMP_CROP = (0.55, 0.05, 1.00, 0.45)


def _get_pdf_path() -> Path:
    """Return the PDF path, preferring the compose mount then the host path."""
    compose_path = Path(_PDF_PATH_ENV)
    if compose_path.exists():
        return compose_path
    host_path = Path(_PDF_HOST_PATH)
    if host_path.exists():
        return host_path
    pytest.skip(f"PDF not found at {compose_path} or {host_path}")


def _is_ollama_reachable(base_url: str) -> bool:
    """Quick HEAD/GET to check if the Ollama endpoint is reachable."""
    try:
        import httpx  # type: ignore[import]
        resp = httpx.get(base_url.rstrip("/v1").rstrip("/") + "/api/tags", timeout=5.0)
        return resp.status_code == 200
    except Exception:
        return False


def _render_crop(pdf_path: Path, page_idx: int, crop: tuple[float, float, float, float]) -> bytes:
    """Render a page crop as PNG bytes.

    Args:
        pdf_path:  Path to the PDF.
        page_idx:  0-based page index.
        crop:      (x0_frac, y0_frac, x1_frac, y1_frac) relative to page dimensions.

    Returns:
        PNG bytes of the cropped region.
    """
    import fitz  # noqa: PLC0415 — PyMuPDF; lazy import

    doc = fitz.open(str(pdf_path))
    page = doc[page_idx]
    mat = fitz.Matrix(150 / 72, 150 / 72)  # 150 DPI
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    img_bytes = pix.tobytes("png")
    doc.close()

    from PIL import Image  # noqa: PLC0415

    img = Image.open(io.BytesIO(img_bytes))
    w, h = img.size
    x0f, y0f, x1f, y1f = crop
    box = (int(w * x0f), int(h * y0f), int(w * x1f), int(h * y1f))
    cropped = img.crop(box)
    buf = io.BytesIO()
    cropped.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Smoke Test 1 — Protocolo page accuracy (Registro 232, date 2026-05-28)
# ---------------------------------------------------------------------------


class TestProtocoloSmoke:
    def test_cloud_reads_registro_232_protocolo_date(self) -> None:
        """CONT-S04/S07/S08: cloud model reads Protocolo 232 date as 2026-05-28 >= 0.85."""
        if not _is_ollama_reachable(_OLLAMA_BASE_URL):
            pytest.skip(f"Ollama not reachable at {_OLLAMA_BASE_URL}")

        pdf_path = _get_pdf_path()

        from reconciliation.adapters.vision.openai_compatible import (  # noqa: PLC0415
            OpenAICompatibleVisionAdapter,
        )

        crop_bytes = _render_crop(pdf_path, _PROTOCOLO_PAGE_IDX, _PROTO_CROP)
        adapter = OpenAICompatibleVisionAdapter(
            model=_CLOUD_MODEL,
            base_url=_OLLAMA_BASE_URL,
            api_key="ollama",
            max_tokens=640,
            supports_batch=False,
        )

        result = adapter.read_handwritten_date(
            crop_bytes,
            hint="Protocolo de Recepcion de Materiales — Fecha field in upper-right header table",
        )

        # CONT-S07: cloud model read the correct date
        assert result.date == date(2026, 5, 28), (
            f"Expected 2026-05-28, got {result.date!r} "
            f"(confidence={result.confidence:.2f}, raw={result.raw[:100]!r})"
        )

        # Confidence gate (0.85 per EXT-002 spirit)
        assert result.confidence >= 0.85, (
            f"Confidence too low: {result.confidence:.2f} (threshold 0.85)"
        )

        # CONT-S08: token meter must show at least one call
        m = adapter.meter
        assert m.calls >= 1, "Expected at least 1 metered call"
        assert m.total_tokens > 0, "Expected non-zero token consumption"

        # Log consumption for the orchestrator's review
        print(
            f"\n[SMOKE RESULT] date={result.date} confidence={result.confidence:.2f}\n"
            f"[TOKEN METER]  calls={m.calls} prompt={m.prompt_tokens} "
            f"completion={m.completion_tokens} total={m.total_tokens}"
        )

    def test_crop_coords_yield_non_empty_image(self) -> None:
        """Sanity: crop box (0.60,0.04,1.00,0.22) produces a non-empty PNG."""
        pdf_path = _get_pdf_path()
        crop_bytes = _render_crop(pdf_path, _PROTOCOLO_PAGE_IDX, _PROTO_CROP)
        assert len(crop_bytes) > 1024, (
            f"Expected crop > 1 KB, got {len(crop_bytes)} bytes"
        )


# ---------------------------------------------------------------------------
# Smoke Test 2 — Guía stamp accuracy (one R7-proven page)
# ---------------------------------------------------------------------------


class TestGuiaStampSmoke:
    def test_cloud_reads_guia_stamp_date(self) -> None:
        """CONT-S07: cloud model reads a guía stamp from a R7-proven page (page 4 or 5)."""
        if not _is_ollama_reachable(_OLLAMA_BASE_URL):
            pytest.skip(f"Ollama not reachable at {_OLLAMA_BASE_URL}")

        pdf_path = _get_pdf_path()

        # Page 4 is a guía page in the #4252 section (R7 proven)
        guia_page_idx = 4

        from reconciliation.adapters.vision.openai_compatible import (  # noqa: PLC0415
            OpenAICompatibleVisionAdapter,
        )

        crop_bytes = _render_crop(pdf_path, guia_page_idx, _STAMP_CROP)
        adapter = OpenAICompatibleVisionAdapter(
            model=_CLOUD_MODEL,
            base_url=_OLLAMA_BASE_URL,
            api_key="ollama",
            max_tokens=640,
            supports_batch=False,
        )

        result = adapter.read_handwritten_date(crop_bytes)

        # The guía stamp for Registro 232 pages should contain a date
        # (R7 proved page 4 has a readable stamp)
        # We assert: confidence >= 0.50 at minimum (not asserting exact date here —
        # the stamp date may differ from the Protocolo declared date per R9 design)
        assert result.confidence >= 0.50, (
            f"Low confidence on guía stamp: {result.confidence:.2f} "
            f"(raw={result.raw[:100]!r})"
        )

        m = adapter.meter
        print(
            f"\n[GUIA STAMP SMOKE] date={result.date} confidence={result.confidence:.2f}\n"
            f"[TOKEN METER]      calls={m.calls} prompt={m.prompt_tokens} "
            f"completion={m.completion_tokens} total={m.total_tokens}"
        )
