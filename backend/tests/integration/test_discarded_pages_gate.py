"""Real-data integration gate for discarded_pages (EXT-034/035, SDD#2 PR-1).

Proves that the pipeline correctly surfaces all 343 GUIA-classified pages that
were dropped by the rev-6 QR-evidence gate in the 2026-06-11 full run.

Evidence: 343 dropped pages in 11 contiguous runs (0-based page indices):
  (33–35), (57–81), (99–137), (152), (165–222), (239–276), (279),
  (293–347), (358–376), (379–452), (463–492)

Design:
  This gate does NOT require OCR (Tier-2), vision, or SUNAT:
  - The discard decision is QR-evidence-determined.
  - Pages with NO QR evidence have no page_hashqr_url by definition, so
    is_ocr_fallback_material can never rescue them regardless of OCR output.
  - We run classify → decode_identities → extract_ocr (NullOcr) → assemble
    via the full pipeline with OCR disabled, which gives deterministic,
    fast output (no heavy ML deps needed).
  - Vision is faked (required for date extraction stage, but irrelevant for
    discard count correctness).

Skip guard: test is skipped when the production PDF is absent.
Marker: @pytest.mark.slow (QR decode ~1.2 s/page × ~469 GUIA pages ≈ 10-15 min).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# PDF / skip guard
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PDF_NAME = "Informe de detalle del formulario-202606020255.pdf"
_PDF_PATH = _PROJECT_ROOT / _PDF_NAME

# Also accept an override via CTR_PDF_PATH env var
_env_path = os.environ.get("CTR_PDF_PATH")
if _env_path:
    _resolved = Path(_env_path)
    if not _resolved.is_absolute():
        _resolved = _PROJECT_ROOT / _resolved
    _PDF_PATH_EFFECTIVE = _resolved
else:
    _PDF_PATH_EFFECTIVE = _PDF_PATH

pytestmark = pytest.mark.slow

_SKIP_REASON = (
    f"Production PDF not present at {_PDF_PATH_EFFECTIVE}; "
    "skipping discarded-pages real-data gate"
)

# ---------------------------------------------------------------------------
# Expected discarded-page ranges (2026-06-11 evidence, 0-based page indices)
# ---------------------------------------------------------------------------

_EXPECTED_RANGES = [
    (33, 35),
    (57, 81),
    (99, 137),
    (152, 152),
    (165, 222),
    (239, 276),
    (279, 279),
    (293, 347),
    (358, 376),
    (379, 452),
    (463, 492),
]

_EXPECTED_DISCARDED_COUNT = 343
_EXPECTED_GUIA_PAGE_COUNT = 469   # assembled (126) + discarded (343) = 469
_EXPECTED_ASSEMBLED_COUNT = 126   # unique guía blocks (GUIA pages with QR evidence)


def _ranges_to_set(ranges: list[tuple[int, int]]) -> set[int]:
    """Expand a list of inclusive (start, end) ranges to a set of integers."""
    result: set[int] = set()
    for start, end in ranges:
        result.update(range(start, end + 1))
    return result


# ---------------------------------------------------------------------------
# Fake vision adapter (required for date stage — irrelevant for discard count)
# ---------------------------------------------------------------------------


class _NullVision:
    """Minimal VisionLLMPort fake: returns empty date for all pages."""

    supports_batch: bool = False

    def read_handwritten_date(self, image: bytes, hint: str | None = None):
        from reconciliation.domain.models import VisionResult  # noqa: PLC0415

        return VisionResult(date=None, confidence=0.0, raw="")

    def read_handwritten_date_batch(self, images: list[bytes]):
        from reconciliation.domain.models import VisionResult  # noqa: PLC0415

        return [VisionResult(date=None, confidence=0.0, raw="")] * len(images)


# ---------------------------------------------------------------------------
# Gate test
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _PDF_PATH_EFFECTIVE.exists(), reason=_SKIP_REASON)
class TestDiscardedPagesRealDataGate:
    """Real-data gate: 343 dropped pages surfaced in PipelineResult.discarded_pages."""

    def _build_and_run(self, tmp_path: Path):
        """Build the full pipeline (OCR disabled for speed) and run it.

        Uses build_pipeline with ocr.enabled=False so:
        - NullOcrExtractor is injected → no paddle import, no OCR calls
        - QrBarcodeExtractionAdapter is wired for QR decode (the discard gate signal)
        - Vision is capped at 0 calls (not relevant for discard count)
        - Total cost: classify + QR decode + assemble, ~1.2 s/page × ~469 GUIA pages
        """
        from reconciliation.application.config import AppConfig  # noqa: PLC0415
        from reconciliation.infrastructure.container import build_pipeline  # noqa: PLC0415

        cfg = AppConfig()
        object.__setattr__(cfg.ocr, "enabled", False)
        # Cap vision calls at 0 — we only care about discard count, not dates
        object.__setattr__(cfg.vision, "max_vision_calls", 0)
        # Use tmp_path for output so we don't pollute the real runs dir
        object.__setattr__(cfg, "output_dir", tmp_path / "runs")

        try:
            pipeline, ctx, _page_to_registro = build_pipeline(
                pdf_path=_PDF_PATH_EFFECTIVE,
                config=cfg,
            )
            # Override vision with null adapter to avoid any latent LLM env-var side effects
            pipeline._vision = _NullVision()  # type: ignore[attr-defined]
        except (ImportError, ModuleNotFoundError) as exc:
            # ONLY a genuinely-missing optional adapter dependency (e.g. pyzbar/zxing-cpp
            # for QrBarcodeExtractionAdapter) is a legitimate skip. Wiring regressions
            # (TypeError/AttributeError/etc.) must FAIL the gate, not silently skip.
            pytest.skip(
                f"build_pipeline failed ({exc}) — QrBarcodeExtractionAdapter or "
                "other required adapter dependency not installed. Cannot run discard gate."
            )

        return pipeline.run(ctx)

    def test_discarded_count_is_343(self, tmp_path: Path) -> None:
        """Assert exactly 343 discarded pages are collected from the full PDF run.

        Spec: EXT-034 / real-data evidence 2026-06-11.
        """
        result = self._build_and_run(tmp_path)

        discarded_pages = result.discarded_pages
        actual_count = len(discarded_pages)

        assert actual_count == _EXPECTED_DISCARDED_COUNT, (
            f"Expected {_EXPECTED_DISCARDED_COUNT} discarded pages, got {actual_count}. "
            f"Discarded page numbers: {sorted(d.page for d in discarded_pages)}"
        )

    def test_discarded_ranges_match_evidence(self, tmp_path: Path) -> None:
        """Assert the discarded pages form the 11 expected contiguous ranges.

        Spec: EXT-034 / design §A5.
        """
        result = self._build_and_run(tmp_path)

        actual_pages = {d.page for d in result.discarded_pages}
        expected_pages = _ranges_to_set(_EXPECTED_RANGES)

        missing = expected_pages - actual_pages
        extra = actual_pages - expected_pages

        assert not missing, (
            f"Missing expected discarded pages: {sorted(missing)}. "
            "These pages should have been dropped by the QR-evidence gate."
        )
        assert not extra, (
            f"Unexpected extra discarded pages: {sorted(extra)}. "
            "These pages were discarded but are not in the 2026-06-11 evidence."
        )

    def test_zero_silent_drop(self, tmp_path: Path) -> None:
        """Assert GUIA-classified == assembled + discarded (zero silent drops).

        The pipeline classifies GUIA pages; each must either become a guía block
        (assembled via _stage_assemble_blocks) or appear in discarded_pages.
        No GUIA page should be silently dropped.

        Invariant: total GUIA-classified = assembled source pages + discarded pages.
        Approximate assertion: discarded + assembled == _EXPECTED_GUIA_PAGE_COUNT.
        """
        result = self._build_and_run(tmp_path)

        # Count assembled source pages (pages that contributed to a guía block)
        assembled_source_pages: set[int] = set()
        for guia in result.guias:
            assembled_source_pages.update(guia.source_pages)

        discarded_page_set = {d.page for d in result.discarded_pages}

        # No overlap
        overlap = assembled_source_pages & discarded_page_set
        assert not overlap, (
            f"Pages appear in BOTH assembled and discarded: {sorted(overlap)}. "
            "A page must be assembled XOR discarded — never both."
        )

        total_accounted = len(assembled_source_pages) + len(discarded_page_set)
        assert total_accounted == _EXPECTED_GUIA_PAGE_COUNT, (
            f"Expected {_EXPECTED_GUIA_PAGE_COUNT} total GUIA-classified pages, "
            f"got {total_accounted} "
            f"({len(assembled_source_pages)} assembled + {len(discarded_page_set)} discarded). "
            "Zero-silent-drop invariant violated."
        )

    def test_a5_mapping_each_run_maps_to_one_registro(self, tmp_path: Path) -> None:
        """Check design §A5 claim: each contiguous run maps to exactly one registro.

        This is a DERIVED claim (not strictly observed in the 2026-06-11 evidence).
        The test does NOT fail on registro=None entries (graceful) but reports
        any run that spans multiple registros for human review.

        Per SA-2: if the derivation fails, this test logs the actual mapping
        without failing the gate (non-binding assertion; A5 is a structural claim).
        """
        result = self._build_and_run(tmp_path)

        page_to_registro = {d.page: d.registro for d in result.discarded_pages}

        violations = []
        for range_start, range_end in _EXPECTED_RANGES:
            pages_in_run = list(range(range_start, range_end + 1))
            # Filter to pages that are actually in discarded_pages (they all should be)
            run_registros = {
                page_to_registro[p]
                for p in pages_in_run
                if p in page_to_registro
            }
            # Remove None (unresolved) — these don't count as multi-registro violations
            non_none = run_registros - {None}
            if len(non_none) > 1:
                violations.append(
                    f"Run {range_start}–{range_end}: multiple registros {non_none}"
                )
            elif not non_none:
                # All None in this run — log but not a violation
                pass

        if violations:
            # Report the violations but do NOT fail — SA-2: this is a derived claim,
            # not a binding assertion. The A1 registro-break rule handles it structurally.
            pytest.xfail(
                "A5 design claim verification: some contiguous runs span registros. "
                "This is unexpected but handled by A1 registro-break grouping. "
                f"Details: {'; '.join(violations)}"
            )
