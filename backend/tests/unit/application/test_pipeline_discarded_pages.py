"""Unit tests for the discarded_pages side-channel on PipelineResult (EXT-034/035).

STRICT TDD: these tests MUST be RED before any implementation change is made.
Tests fail because:
  - DiscardedPage does not exist in domain/models.py yet.
  - PipelineResult.discarded_pages attribute is absent.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from reconciliation.application.config import AppConfig
from reconciliation.application.pipeline import ReconciliationPipeline
from reconciliation.application.run_context import RunContext
from reconciliation.domain.models import GuiaIdentity, MaterialLine, VisionResult


# ---------------------------------------------------------------------------
# Fake adapters
# ---------------------------------------------------------------------------


class _FakeDoc:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self._pages = pages

    def page_count(self) -> int:
        return len(self._pages)

    def render_page(self, idx: int, dpi: int = 200) -> bytes:
        return self._pages[idx].get("image", b"\x89PNG")

    def page_text(self, idx: int) -> str | None:
        return self._pages[idx].get("text")


class _FakeExtractor:
    def __init__(self, per_page_lines: list[list[MaterialLine]] | None = None) -> None:
        self._queue = list(per_page_lines or [])

    def extract_declared(self, text: str) -> list[MaterialLine]:
        return []

    def extract_printed_table(self, image: bytes) -> list[MaterialLine]:
        if self._queue:
            return self._queue.pop(0)
        return []


class _FakeVision:
    supports_batch: bool = False

    def read_handwritten_date(
        self, image: bytes, hint: str | None = None
    ) -> VisionResult:
        return VisionResult(date=date(2026, 5, 1), confidence=0.99, raw="01/05/2026")


class _StatefulIdentity:
    def __init__(self, sequence: list[GuiaIdentity | None]) -> None:
        self._seq = list(sequence)
        self._idx = 0

    def decode_identity(
        self, image: bytes, page_idx: int | None = None
    ) -> GuiaIdentity | None:
        if self._idx < len(self._seq):
            result = self._seq[self._idx]
        else:
            result = None
        self._idx += 1
        return result


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_GUIA_TEXT = (
    "PTR001-TORRE ROSALES\n"
    "Informe de detalle del formulario\n"
    "GUIA DE REMISION\n"
)

# A GUIA-classified page (has GUIA text) but NO QR evidence (identity=None, no hashqr_url)
_GUIA_PAGE_NO_QR = {"text": _GUIA_TEXT, "image": b"\x89PNG"}

_MAT_LINE = MaterialLine(
    description_raw="BARRA 3/8",
    description_canonical="barra 3/8",
    unidad="KG",
    cantidad=Decimal("100"),
    confidence=0.95,
)


def _identity(serie: str = "T001", numero: str = "0001") -> GuiaIdentity:
    return GuiaIdentity(
        serie=serie,
        numero=numero,
        ruc_emisor="12345678901",
        ruc_receptor="10987654321",
        tipo="09",
        hashqr_url=None,
        confidence=1.0,
    )


def _run_pipeline(
    pages: list[dict[str, Any]],
    identity_seq: list[GuiaIdentity | None] | None = None,
    per_page_lines: list[list[MaterialLine]] | None = None,
    page_to_registro: dict[int, str | None] | None = None,
    tmp_path: Path | None = None,
):
    cfg = AppConfig()
    doc = _FakeDoc(pages)
    extractor = _FakeExtractor(per_page_lines=per_page_lines)
    vision = _FakeVision()
    identity_adapter = _StatefulIdentity(identity_seq) if identity_seq is not None else None

    pipeline = ReconciliationPipeline(
        doc_source=doc,
        extractor=extractor,
        vision=vision,
        config=cfg,
        page_to_registro=page_to_registro or {},
        identity=identity_adapter,
    )
    base = tmp_path or Path(".")
    ctx = RunContext(pdf_path=base / "in.pdf", output_base=base / "runs")
    return pipeline.run(ctx)


# ---------------------------------------------------------------------------
# 1.1.1 — GUIA page with no QR evidence produces a DiscardedPage entry
# ---------------------------------------------------------------------------


class TestNoQrEvidenceEmitsDiscardedEntry:
    def test_no_qr_evidence_page_emits_discarded_entry(self, tmp_path: Path) -> None:
        """A GUIA-classified page with no QR identity and no hashqr_url must appear
        in PipelineResult.discarded_pages with the correct page, registro and lines.

        Spec: EXT-034 / EXT-S034a.
        FAILS (RED): PipelineResult.discarded_pages does not exist yet.
        """
        from reconciliation.domain.models import DiscardedPage  # type: ignore[attr-defined]

        result = _run_pipeline(
            pages=[_GUIA_PAGE_NO_QR],
            identity_seq=[None],          # No QR decode
            per_page_lines=[[_MAT_LINE]],
            page_to_registro={0: "232"},
            tmp_path=tmp_path,
        )

        assert hasattr(result, "discarded_pages"), (
            "PipelineResult must have discarded_pages attribute (EXT-035)"
        )
        assert len(result.discarded_pages) == 1
        entry = result.discarded_pages[0]
        assert isinstance(entry, DiscardedPage)
        assert entry.page == 0
        assert entry.registro == "232"
        assert len(entry.lines) == 1

        # The dropped page must NOT produce a GuiaDeRemision block
        assert all(g.guia_id != "ocr_0" or g.lines != [] for g in result.guias), (
            "No block must be assembled for a no-evidence page"
        )
        # No guia assembled for this page
        assert not any(0 in g.source_pages for g in result.guias), (
            "No guía should reference the discarded page"
        )

    # 1.1.2 — empty lines still produces a DiscardedPage entry
    def test_no_qr_evidence_empty_lines_still_discarded(self, tmp_path: Path) -> None:
        """A no-evidence page with zero OCR lines must still land in discarded_pages.

        Spec: EXT-034 / EXT-S034b.
        """
        from reconciliation.domain.models import DiscardedPage  # type: ignore[attr-defined]

        result = _run_pipeline(
            pages=[_GUIA_PAGE_NO_QR],
            identity_seq=[None],
            per_page_lines=[[]],           # no OCR lines
            page_to_registro={0: "229"},
            tmp_path=tmp_path,
        )

        assert len(result.discarded_pages) == 1
        entry = result.discarded_pages[0]
        assert isinstance(entry, DiscardedPage)
        assert entry.lines == []
        assert entry.registro == "229"


# ---------------------------------------------------------------------------
# 1.1.3 — Valid QR evidence page must NOT be discarded
# ---------------------------------------------------------------------------


class TestValidQrNotDiscarded:
    def test_valid_qr_evidence_not_discarded(self, tmp_path: Path) -> None:
        """A page with a successfully decoded QR identity must NOT appear in
        discarded_pages and must produce a normal guía block.

        Spec: EXT-034 / EXT-S034c. Regression lock on blocking semantics.
        """
        result = _run_pipeline(
            pages=[_GUIA_PAGE_NO_QR],
            identity_seq=[_identity("T001", "9999")],   # valid QR
            per_page_lines=[[_MAT_LINE]],
            page_to_registro={0: "232"},
            tmp_path=tmp_path,
        )

        assert result.discarded_pages == [], (
            "A valid-QR page must not land in discarded_pages"
        )
        # A block should be assembled
        assert any(0 in g.source_pages for g in result.guias), (
            "Valid-QR page must produce a guía block"
        )


# ---------------------------------------------------------------------------
# 1.1.4 — No-QR-evidence discriminant: identity=None AND no hashqr_url → DISCARDED.
#
# This is the NEGATIVE arm of the EXT-S034d discriminant at the pipeline level:
# a material-bearing GUIA page with NO QR evidence at all is dropped AND surfaced
# as a DiscardedPage (never silently lost, never assembled).
#
# The POSITIVE ocr_fallback arm (identity=None BUT hashqr_url present → assembled
# into its own block, NOT discarded) is exercised at the _stage_assemble_blocks
# boundary in test_positional_gate.py (which now asserts `discarded == []` on those
# positive paths).  It cannot be driven from this pipeline-level test because the
# _StatefulIdentity fake injects at the GuiaIdentity level, not DecodeOutcome, so it
# cannot supply a DecodeOutcome.hashqr_url without the real IdentityExtractionPort.
# ---------------------------------------------------------------------------


class TestNoQrEvidenceDiscriminantDiscarded:
    def test_no_hashqr_material_page_is_discarded_not_assembled(self, tmp_path: Path) -> None:
        """A material page with identity=None AND no hashqr_url must be DISCARDED
        (negative arm of the EXT-S034d discriminant), not assembled into a block.

        Spec: EXT-034 / EXT-S034d (negative arm). The positive arm (hashqr_url present
        → assembled, discarded == []) is locked in test_positional_gate.py.
        """
        # identity=None, no hashqr_url → page IS discarded (and NOT assembled).
        result = _run_pipeline(
            pages=[_GUIA_PAGE_NO_QR],
            identity_seq=[None],
            per_page_lines=[[_MAT_LINE]],
            page_to_registro={0: "232"},
            tmp_path=tmp_path,
        )
        # Page with no QR evidence at all → must be discarded.
        assert len(result.discarded_pages) == 1, (
            "No-QR-evidence material page must be discarded (negative discriminant arm)"
        )
        # And it must NOT have been assembled into any guía block.
        assert not any(0 in g.source_pages for g in result.guias), (
            "No-QR-evidence page must NOT be assembled into a guía block"
        )


# ---------------------------------------------------------------------------
# 1.1.5 — registro=None is valid for a DiscardedPage entry
# ---------------------------------------------------------------------------


class TestDiscardedRegistroNoneIsValid:
    def test_discarded_entry_registro_none_is_valid(self, tmp_path: Path) -> None:
        """A no-evidence GUIA page where page_to_registro returns None must produce
        a DiscardedPage with registro=None (unresolved section).

        Spec: EXT-034 / EXT-S034e.
        """
        from reconciliation.domain.models import DiscardedPage  # type: ignore[attr-defined]

        result = _run_pipeline(
            pages=[_GUIA_PAGE_NO_QR],
            identity_seq=[None],
            per_page_lines=[[_MAT_LINE]],
            page_to_registro={},   # page 0 not in map → raw.registro is None
            tmp_path=tmp_path,
        )

        assert len(result.discarded_pages) == 1
        entry = result.discarded_pages[0]
        assert isinstance(entry, DiscardedPage)
        assert entry.registro is None


# ---------------------------------------------------------------------------
# 1.1.6 — errored_guias and discarded_pages are separate collections
# ---------------------------------------------------------------------------


class TestErroredAndDiscardedAreSepaarte:
    def test_errored_and_discarded_collections_are_separate(self, tmp_path: Path) -> None:
        """A pipeline with one valid-QR zero-lines guía AND one no-evidence page must
        produce exactly one errored_guia entry (the valid-QR block) and one discarded
        entry (the no-evidence page) — they MUST NOT bleed into each other.

        Spec: EXT-035 / EXT-S035a.
        """
        pages = [
            _GUIA_PAGE_NO_QR,   # page 0: valid QR, zero OCR lines → errored_guia
            _GUIA_PAGE_NO_QR,   # page 1: no QR evidence → discarded_pages
        ]
        # page 0 gets a valid QR identity; page 1 gets None
        result = _run_pipeline(
            pages=pages,
            identity_seq=[_identity("T001", "0001"), None],
            per_page_lines=[[], [_MAT_LINE]],   # page 0 zero lines; page 1 has lines but no QR
            page_to_registro={0: "232", 1: "232"},
            tmp_path=tmp_path,
        )

        # page 0: valid-QR but zero-lines → errored_guia
        assert len(result.errored_guias) == 1, (
            "Valid-QR zero-lines page must land in errored_guias"
        )
        assert result.errored_guias[0].guia_id == "T001-0001"

        # page 1: no-evidence → discarded_pages
        assert len(result.discarded_pages) == 1, (
            "No-evidence page must land in discarded_pages"
        )
        discarded_pages_pages = [d.page for d in result.discarded_pages]
        errored_source_pages = [p for eg in result.errored_guias for p in eg.source_pages]

        # No overlap between the two collections
        assert not set(discarded_pages_pages).intersection(errored_source_pages), (
            "discarded_pages and errored_guias must be disjoint"
        )


# ---------------------------------------------------------------------------
# 1.1.7 — Old PipelineResult cache (no discarded_pages key) hydrates without error
# ---------------------------------------------------------------------------


class TestOldCacheHydratesWithoutError:
    def test_old_pipeline_result_cache_hydrates_without_error(self) -> None:
        """An old extraction cache dict without 'discarded_pages' key must deserialize
        without ValidationError, defaulting to [].

        Spec: EXT-035 / EXT-S035b. Backward-compat gate.
        """
        from reconciliation.domain.models import (  # type: ignore[attr-defined]
            GuiaDeRemision,
            ReconciliationRow,
            Registro,
        )

        # Build a minimal cache that looks like an old PipelineResult serialization
        # (no discarded_pages key) and verify ReviewService hydration path.
        # We test via build_review_service which calls cache.get("discarded_pages", [])
        # after our implementation.  For now just verify the domain model defaults.
        from reconciliation.application.pipeline import PipelineResult  # type: ignore[attr-defined]

        # Simulate an old Pydantic round-trip by constructing with default factory
        result = PipelineResult(
            run_id="test-run",
            classifications=[],
            declared=[],
            guias=[],
            rows=[],
        )
        # discarded_pages must default to [] on old-style construction
        assert result.discarded_pages == [], (
            "PipelineResult.discarded_pages must default to [] (backward compat)"
        )
