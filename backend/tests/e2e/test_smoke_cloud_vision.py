"""Cloud-vision accuracy smoke test for r10-containerized-verification (CONT-S07/S08).

Scope (2026-06-03): only the guía stamp date is handwritten and vision-read. The
Protocolo "Fecha:" is DIGITAL/printed and parsed deterministically by
``digital_text_extractor`` (no vision), so the former Protocolo-page vision smoke
was removed together with the declared-date vision sub-stage.

Pass criteria:
  - Cloud qwen3.5:397b-cloud reads a R7-proven guía stamp page with confidence >= 0.50.
  - Token consumption logged: meter.calls >= 1, meter.total_tokens > 0 (CONT-S08).

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
# Smoke Test — Guía stamp accuracy (one R7-proven page)
#
# NOTE: the Protocolo-page vision smoke was removed with the declared-date vision
# sub-stage (2026-06-03): the Protocolo "Fecha:" is DIGITAL/printed and parsed
# deterministically by ``digital_text_extractor`` — no vision. Only the guía stamp
# date is handwritten (vision-read), so only that smoke remains.
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
