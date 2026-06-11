"""Real-data integration gate for discarded-page recovery (EXT-036/037, SDD#2 PR-2).

Proves the 3-tier OCR-first recovery chain works against genuinely-produced
pipeline output on the reg227 section PDF (docs/eval/reg227_section.pdf).

Evidence: the section PDF (165 pages, Registro 227 only) produces exactly 1
discarded page — page 152, registro='227' — with 3 cached MaterialLines from
the rapidocr OCR pass.  The full production PDF produces 343 discarded pages
(proven by PR-1 gate); the section PDF is used here because it runs in <4 min
(vs. ~7-8 min for the full PDF).

Tasks verified:
  2.3.1 — apply_page_recovery Tier-1 path:
    a. OCR extractor NOT called (Tier-1 cache hit)
    b. recovered=True
    c. ALL recovered lines have requires_review=True
    d. entry REMOVED from discarded_pages after recovery
  2.3.2 — Sidecar restart round-trip:
    e. recovered_discarded_page event present in sidecar
    f. event target matches {guia_id, page}
    g. event new_value is a dict (GuiaDeRemision model_dump)
    h. restore_from_sidecar: recovered guía present in fresh service
    i. restore_from_sidecar: discarded entry absent in fresh service

Skip guard: test is skipped when the section PDF is absent.
Marker: @pytest.mark.slow (rapidocr OCR ~2-4 min on 165 pages).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# PDF / skip guard
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SECTION_PDF = _REPO_ROOT / "docs" / "eval" / "reg227_section.pdf"

# Accept override via CTR_PDF_PATH (relative paths resolved against repo root)
_env_path = os.environ.get("CTR_PDF_PATH")
if _env_path:
    _resolved = Path(_env_path)
    if not _resolved.is_absolute():
        _resolved = _REPO_ROOT / _resolved
    _PDF_PATH_EFFECTIVE = _resolved
else:
    _PDF_PATH_EFFECTIVE = _SECTION_PDF

pytestmark = pytest.mark.slow

_SKIP_REASON = (
    f"Section PDF not present at {_PDF_PATH_EFFECTIVE}; "
    "skipping discarded-page recovery real-data gate"
)

# ---------------------------------------------------------------------------
# Fake vision (no LLM calls; required to satisfy AppConfig date-source guard)
# ---------------------------------------------------------------------------


class _NullVision:
    """Minimal VisionLLMPort stub: no LLM calls, no env-var requirements."""

    supports_batch: bool = False

    def read_handwritten_date(self, image: bytes, hint: str | None = None):
        from reconciliation.domain.models import VisionResult  # noqa: PLC0415

        return VisionResult(date=None, confidence=0.0, raw="")

    def read_handwritten_date_batch(self, images: list[bytes]):
        from reconciliation.domain.models import VisionResult  # noqa: PLC0415

        return [VisionResult(date=None, confidence=0.0, raw="")] * len(images)

    def read_material_table(self, image: bytes, hint: str | None = None) -> list:
        return []


# ---------------------------------------------------------------------------
# OCR spy — wraps any ExtractionPort; counts extract_printed_table calls
# ---------------------------------------------------------------------------


class _OcrSpy:
    """Transparent proxy over an ExtractionPort that counts OCR calls."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.call_count: int = 0

    def extract_printed_table(self, image: bytes) -> list:
        self.call_count += 1
        return self._inner.extract_printed_table(image)

    def __getattr__(self, name: str) -> Any:  # forward everything else
        return getattr(self._inner, name)


# ---------------------------------------------------------------------------
# Gate fixture: run pipeline once, reuse across both test methods
# ---------------------------------------------------------------------------


@pytest.fixture(scope="class")
def pipeline_run(tmp_path_factory):
    """Build and run the pipeline on the section PDF with OCR=rapidocr.

    Returns a dict with:
      result   — PipelineResult
      ctx      — RunContext (needed to read sidecar + rebuild ReviewService)
      cfg      — AppConfig (needed to rebuild ReprocessService)
    """
    tmp_out = tmp_path_factory.mktemp("ctr_gate_2_3")

    from reconciliation.application.config import AppConfig  # noqa: PLC0415
    from reconciliation.infrastructure.container import build_pipeline  # noqa: PLC0415

    cfg = AppConfig()
    object.__setattr__(cfg.ocr, "enabled", True)
    object.__setattr__(cfg.ocr, "engine", "rapidocr")
    # Cap vision at 0 — we need OCR lines for cached_lines; no LLM cost.
    object.__setattr__(cfg.vision, "max_vision_calls", 0)
    object.__setattr__(cfg, "output_dir", tmp_out / "runs")

    try:
        pipeline, ctx, _ = build_pipeline(
            pdf_path=_PDF_PATH_EFFECTIVE,
            config=cfg,
        )
    except (ImportError, ModuleNotFoundError) as exc:
        pytest.skip(f"build_pipeline failed ({exc}) — required adapter not installed")

    # Override vision with null stub (no LLM env vars required)
    pipeline._vision = _NullVision()  # type: ignore[attr-defined]

    result = pipeline.run(ctx)
    return {"result": result, "ctx": ctx, "cfg": cfg}


# ---------------------------------------------------------------------------
# Gate class
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _PDF_PATH_EFFECTIVE.exists(), reason=_SKIP_REASON)
class TestDiscardedRecoveryRealDataGate:
    """Real-data gate: apply_page_recovery Tier-1 + sidecar restart round-trip."""

    # ------------------------------------------------------------------
    # 2.3.1 — Recovery chain
    # ------------------------------------------------------------------

    def test_2_3_1_recovery_chain(self, pipeline_run: dict) -> None:
        """Verify the 3-tier recovery chain assertions a–d (task 2.3.1).

        Selects the FIRST discarded entry that has cached lines (Tier-1 path).
        If none has cached lines, falls back to the first empty-lines entry
        (Tier-2) and verifies assertions b–d only (notes the fallback in
        output).  Tier-1 is the preferred proof because it asserts that OCR
        is NOT called when the cache is warm.
        """
        result = pipeline_run["result"]
        ctx = pipeline_run["ctx"]
        cfg = pipeline_run["cfg"]

        # Select target entry
        with_lines = [dp for dp in result.discarded_pages if dp.lines]
        without_lines = [dp for dp in result.discarded_pages if not dp.lines]

        assert result.discarded_pages, (
            "Pipeline produced 0 discarded_pages on the section PDF — "
            "expected at least 1 (page 152 for Registro 227). "
            "Check that the QR-evidence gate emits DiscardedPage correctly."
        )

        if with_lines:
            target_entry = with_lines[0]
            expected_tier = 1
        else:
            # Tier-2 fallback
            target_entry = without_lines[0]
            expected_tier = 2
            # We still run recovery, but skip assertion (a) which is Tier-1-specific

        from reconciliation.adapters.inference.factory import (  # noqa: PLC0415
            build_inference_adapter,
        )
        from reconciliation.adapters.pdf.pymupdf_source import (  # noqa: PLC0415
            PdfStructureAdapter,
        )
        from reconciliation.adapters.vision.null_vision import (  # noqa: PLC0415
            NullVisionAdapter,
        )
        from reconciliation.application.reprocess_service import (  # noqa: PLC0415
            ReprocessService,
        )
        from reconciliation.domain.material_key_normalizer import (  # noqa: PLC0415
            MaterialKeyNormalizer,
        )
        from reconciliation.domain.material_key_resolver import (  # noqa: PLC0415
            MaterialKeyResolver,
        )
        from reconciliation.infrastructure.container import (  # noqa: PLC0415
            _build_ocr_extractor_for_config,
            build_review_service,
        )

        review_service = build_review_service(ctx, pipeline_result=None)

        real_extractor = _build_ocr_extractor_for_config(cfg)
        spy = _OcrSpy(real_extractor)

        doc_source = PdfStructureAdapter(_PDF_PATH_EFFECTIVE)
        inference = build_inference_adapter(cfg)
        key_resolver = MaterialKeyResolver(MaterialKeyNormalizer(), inference)

        reprocess_svc = ReprocessService(
            doc_source=doc_source,
            identity=None,
            sunat=None,
            key_resolver=key_resolver,
            review_service=review_service,
            vision=NullVisionAdapter(),  # Tier-3 disabled — Tier-1 or Tier-2 only
            extractor=spy,
        )

        page = target_entry.page
        initial_count = len(review_service.discarded_pages)
        spy_before = spy.call_count

        recovery_result = asyncio.run(
            reprocess_svc.apply_page_recovery(page)
        )

        spy_after = spy.call_count
        ocr_called = spy_after > spy_before

        # (a) Tier-1: OCR NOT called when cached lines are available
        if expected_tier == 1:
            assert not ocr_called, (
                f"Tier-1: OCR extractor was called {spy_after - spy_before} time(s) "
                f"for page={page} which has {len(target_entry.lines)} cached lines. "
                "Tier-1 must use cached lines directly — no render/OCR call."
            )
        # else: Tier-2 — OCR IS expected; no (a) assertion

        # (b) recovered=True
        assert recovery_result.recovered is True, (
            f"apply_page_recovery(page={page}) returned recovered=False "
            f"(reason={recovery_result.reason!r}). "
            f"Entry had {len(target_entry.lines)} cached lines (tier={expected_tier}). "
            "Expected successful recovery."
        )

        # (c) ALL recovered lines have requires_review=True (absolute invariant)
        recovered_guia = next(
            (g for g in review_service._guias if g.guia_id == recovery_result.guia_id),
            None,
        )
        assert recovered_guia is not None, (
            f"Recovered guía {recovery_result.guia_id!r} not found in "
            "review_service._guias after recovery."
        )
        bad_lines = [ln for ln in recovered_guia.lines if ln.requires_review is not True]
        assert not bad_lines, (
            f"Recovered guía {recovery_result.guia_id!r} has {len(bad_lines)} "
            "line(s) with requires_review != True. "
            "Invariant: ALL recovered lines MUST have requires_review=True "
            "(reconciliation validation gate — no auto-accept)."
        )

        # (d) entry REMOVED from discarded_pages
        final_discarded = review_service.discarded_pages
        entry_still_present = any(dp.page == page for dp in final_discarded)
        assert not entry_still_present, (
            f"DiscardedPage entry for page={page} is still present in "
            f"review_service.discarded_pages after successful recovery. "
            f"Expected removal. Remaining count: {len(final_discarded)} "
            f"(was {initial_count})."
        )

        # Store recovered guia_id for use in 2.3.2 (accessed via pipeline_run dict)
        pipeline_run["recovered_page"] = page
        pipeline_run["recovered_guia_id"] = recovery_result.guia_id
        pipeline_run["tier_proven"] = expected_tier

    # ------------------------------------------------------------------
    # 2.3.2 — Sidecar restart round-trip
    # ------------------------------------------------------------------

    def test_2_3_2_sidecar_restart_roundtrip(self, pipeline_run: dict) -> None:
        """Verify the sidecar event + restore_from_sidecar round-trip (task 2.3.2).

        Depends on test_2_3_1_recovery_chain having completed successfully.
        Reads the sidecar written by recover_discarded_page and reconstructs a
        fresh ReviewService via restore_from_sidecar, then asserts:
          e. recovered_discarded_page event present in sidecar JSON
          f. event target has correct guia_id + page
          g. event new_value is a dict (GuiaDeRemision model_dump)
          h. fresh service contains recovered guía
          i. fresh service discarded_pages does NOT contain the recovered page
        """
        ctx = pipeline_run["ctx"]
        page = pipeline_run.get("recovered_page")
        guia_id = pipeline_run.get("recovered_guia_id")

        if page is None or guia_id is None:
            pytest.skip(
                "test_2_3_1 did not complete recovery — cannot verify sidecar"
            )

        # Read sidecar from disk
        sidecar = ctx.read_review_sidecar()
        edits = sidecar.get("edits", [])

        # (e) recovered_discarded_page event present
        rdp_for_page = [
            e for e in edits
            if e.get("kind") == "recovered_discarded_page"
            and e.get("target", {}).get("page") == page
        ]
        assert rdp_for_page, (
            f"No 'recovered_discarded_page' sidecar event found for page={page}. "
            f"Total edits in sidecar: {len(edits)}. "
            "recover_discarded_page must persist an audit event kind="
            "'recovered_discarded_page' (Design §5 / §11.1 restart-correctness risk)."
        )

        ev = rdp_for_page[0]

        # (f) target shape
        expected_target = {"guia_id": guia_id, "page": page}
        assert ev.get("target") == expected_target, (
            f"Sidecar event target {ev.get('target')!r} != {expected_target!r}. "
            "The target must carry both guia_id and page for restore_from_sidecar "
            "to replay correctly."
        )

        # (g) new_value is a dict (GuiaDeRemision model_dump)
        assert isinstance(ev.get("new_value"), dict), (
            f"Sidecar event new_value is {type(ev.get('new_value')).__name__!r}, "
            "expected dict (GuiaDeRemision.model_dump(mode='json'))."
        )

        # Rebuild ReviewService from sidecar on a FRESH instance
        from reconciliation.infrastructure.container import build_review_service  # noqa: PLC0415

        fresh_svc = build_review_service(ctx, pipeline_result=None)

        # (h) recovered guía present in fresh service
        recovered_guia = next(
            (g for g in fresh_svc._guias if g.guia_id == guia_id),
            None,
        )
        assert recovered_guia is not None, (
            f"restore_from_sidecar did not restore guía {guia_id!r}. "
            f"Fresh service has {len(fresh_svc._guias)} guías. "
            "The recovered_discarded_page sidecar event must be replayed by "
            "restore_from_sidecar so the recovery survives a server restart."
        )

        # (i) discarded entry absent in fresh service
        discarded_still_present = any(
            dp.page == page for dp in fresh_svc.discarded_pages
        )
        assert not discarded_still_present, (
            f"DiscardedPage entry for page={page} is still in fresh service's "
            "discarded_pages after sidecar replay. "
            "recover_discarded_page must drop the entry AND persist it so the "
            "sidecar replay also removes it."
        )
