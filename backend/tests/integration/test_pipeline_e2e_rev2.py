"""Rev-2 e2e integration tests — QR identity tier, block grouping, guia contributions.

S1.9 deliverables (tasks.md lines 464-476):
  1. QR identity          — identity_source=="qr" and guia_id matches serie-numero pattern
  2. Block grouping       — no guia_id matches "guia_page_N"
  3. UNRESOLVED bucket    — guias with registro=None surface in pipeline result
  4. Section-ID guard     — is_section_id(guia.registro) is False for all resolved guias
  5. Guia-contribution    — ReconciliationRow.guias non-empty for MATCH/MISMATCH rows
  6. Line-edit e2e        — PATCH /guias/{id}/lines on MISMATCH → changes summed_qty
  7. Thumbnail e2e        — GET /pages/{n}/thumbnail → 200 + PNG content-type

Guards:
  All tests skip when the real PDF is absent (_SKIP_NO_PDF).

Design:
  - Only the QR/identity tier uses real adapters (QrBarcodeExtractionAdapter).
  - Vision (handwritten dates) uses FakeVision — no API call.
  - OCR (material tables) uses FakeOCR for most tests; a parameterised fake
    for tests that need non-empty guia lines.
  - Page classification uses a HybridDocSource that injects "GUIA DE REMISION"
    text for known guia pages so the PageClassifier identifies them correctly.
    This is the same technique as the existing e2e tests.
  - Tests 6 and 7 use a FastAPI TestClient (no real HTTP server needed).

Real-data evidence captured during development (smoke check):
  Pages 4-23 (0-based) in section 4252 (registro 232) are real guia pages.
  The smoke check confirmed:
    - 20/20 of those pages QR-decoded successfully with pyzbar+zxing-cpp
    - All decoded guia_ids match ^[A-Z]\\d+-\\d+$ (e.g. T009-0741770, T073-0680256)
    - identity_source="qr" for all 20; 0 guia_page_N IDs produced
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from reconciliation.application.config import AppConfig
from reconciliation.application.pipeline import ReconciliationPipeline
from reconciliation.application.run_context import RunContext
from reconciliation.domain.models import MaterialLine, VisionResult
from reconciliation.domain.section_id_guard import is_section_id
from reconciliation.infrastructure.container import (
    CompositeExtractionAdapter,
    build_page_to_registro_map,
)

# ---------------------------------------------------------------------------
# PDF guard (identical to existing e2e test)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PDF_NAME = "Informe de detalle del formulario-202605311657.pdf"
_PDF_PATH = _PROJECT_ROOT / _PDF_NAME

_SKIP_NO_PDF = pytest.mark.skipif(
    not _PDF_PATH.exists(),
    reason=f"Real PDF not present at {_PDF_PATH}; skipping rev-2 e2e integration tests",
)

# ---------------------------------------------------------------------------
# Constants validated by smoke check against the real PDF
# ---------------------------------------------------------------------------

# 0-based page range for section 4252 (registro "232")
# Pages 2-3 are DECLARED (FORM DETAIL + PROTOCOLO), pages 4-23 are guia pages
_SECTION_4252_GUIA_PAGES: set[int] = set(range(4, 24))

# Pattern: serie-numero form required by EXT-015
_GUIA_ID_PATTERN = re.compile(r"^[A-Z]\d+-\d+$")

# Pattern that MUST NEVER appear (forbidden since S1.5)
_FORBIDDEN_GUIA_PAGE_PATTERN = re.compile(r"guia_page_\d+")


# ---------------------------------------------------------------------------
# Fake port implementations (no ML/SDK required)
# ---------------------------------------------------------------------------


class FakeVision:
    """Returns a fixed date for all vision calls — no API needed."""

    supports_batch: bool = False

    def __init__(self, fixed_date: date | None = None) -> None:
        self._date = fixed_date or date(2026, 5, 28)

    def read_handwritten_date(self, image: bytes, hint: str | None = None) -> VisionResult:
        return VisionResult(date=self._date, confidence=0.99, raw=str(self._date))

    def read_handwritten_date_batch(self, images: list[bytes]) -> list[VisionResult]:
        return [self.read_handwritten_date(img) for img in images]


class FakeOCR:
    """Returns no material lines — for tests that only need structural assertions."""

    def extract_printed_table(self, image: bytes) -> list[MaterialLine]:
        return []

    def extract_declared(self, text: str) -> list[MaterialLine]:  # pragma: no cover
        return []


class FakeOCRWithLine:
    """Returns one configurable MaterialLine per call — for MATCH/MISMATCH tests."""

    def __init__(self, line: MaterialLine) -> None:
        self._line = line

    def extract_printed_table(self, image: bytes) -> list[MaterialLine]:
        return [self._line]

    def extract_declared(self, text: str) -> list[MaterialLine]:  # pragma: no cover
        return []


class HybridDocSource:
    """Wraps a real PDF source; overrides text for guia pages with GUIA marker.

    The PageClassifier requires "GUIA DE REMISION" text to classify a page as
    GUIA.  The real guia pages in this PDF are embedded PDFs (Autodesk Forma
    attachments) whose digital text layer carries only a universal header, not
    the GUIA title.  Injecting the marker lets the classifier work correctly
    while leaving the rendered image unchanged — the QR adapter reads the real
    image independently.
    """

    _GUIA_TEXT = "PTR001-TORRE ROSALES\nInforme de detalle del formulario\nGUIA DE REMISION\n"

    def __init__(self, real_src: Any, guia_page_set: set[int]) -> None:
        self._src = real_src
        self._guia_pages = guia_page_set

    def page_count(self) -> int:
        return self._src.page_count()

    def page_text(self, idx: int) -> str | None:
        if idx in self._guia_pages:
            return self._GUIA_TEXT
        return self._src.page_text(idx)

    def render_page(self, idx: int, dpi: int = 200) -> bytes:
        return self._src.render_page(idx, dpi)

    def contents_offsets(self) -> dict[str, int]:
        return self._src.contents_offsets()


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pdf_src():
    """Open PdfStructureAdapter once for the whole module."""
    from reconciliation.adapters.pdf.pymupdf_source import PdfStructureAdapter

    with PdfStructureAdapter(_PDF_PATH) as src:
        yield src


@pytest.fixture(scope="module")
def digital_extractor():
    from reconciliation.adapters.pdf.digital_text_extractor import DigitalTextExtractionAdapter

    return DigitalTextExtractionAdapter()


@pytest.fixture(scope="module")
def page_to_registro(pdf_src, digital_extractor):
    offsets = pdf_src.contents_offsets()
    total = pdf_src.page_count()
    return build_page_to_registro_map(
        offsets, total, doc_source=pdf_src, declared_extractor=digital_extractor
    )


@pytest.fixture(scope="module")
def qr_adapter():
    from reconciliation.adapters.identity.qr_barcode import QrBarcodeExtractionAdapter

    return QrBarcodeExtractionAdapter(render_dpi=150, upscale=2)


def _make_pipeline(
    pdf_src: Any,
    digital_extractor: Any,
    page_to_registro: dict[int, str | None],
    ocr_adapter: Any,
    identity_adapter: Any | None,
    guia_pages: set[int] | None = None,
) -> ReconciliationPipeline:
    """Build a ReconciliationPipeline with the given adapters."""
    pages = guia_pages if guia_pages is not None else _SECTION_4252_GUIA_PAGES
    hybrid_src = HybridDocSource(pdf_src, pages)

    extractor = CompositeExtractionAdapter.__new__(CompositeExtractionAdapter)
    extractor._declared_adapter = digital_extractor
    extractor._ocr_adapter = ocr_adapter

    config = AppConfig()
    return ReconciliationPipeline(
        doc_source=hybrid_src,
        extractor=extractor,
        vision=FakeVision(),
        config=config,
        page_to_registro=page_to_registro,
        identity=identity_adapter,
    )


# ---------------------------------------------------------------------------
# S1.9 Test 1 — QR identity: at least one guia with identity_source="qr"
# ---------------------------------------------------------------------------


@_SKIP_NO_PDF
class TestQrIdentityE2E:
    """Real QR decode produces GuiaDeRemision with identity_source=="qr"."""

    def test_at_least_one_qr_identity(
        self, pdf_src, digital_extractor, page_to_registro, qr_adapter, tmp_path
    ) -> None:
        """Pipeline with QrBarcodeExtractionAdapter produces >=1 guia with QR identity."""
        pipeline = _make_pipeline(
            pdf_src, digital_extractor, page_to_registro, FakeOCR(), qr_adapter
        )
        ctx = RunContext(pdf_path=_PDF_PATH, output_base=tmp_path / "qr_identity")
        result = pipeline.run(ctx)

        qr_guias = [g for g in result.guias if g.identity_source == "qr"]
        assert len(qr_guias) >= 1, (
            f"Expected >=1 GuiaDeRemision with identity_source='qr'; "
            f"got {len(result.guias)} total guias, all with sources: "
            f"{[g.identity_source for g in result.guias]}"
        )

    def test_qr_guia_id_matches_serie_numero_pattern(
        self, pdf_src, digital_extractor, page_to_registro, qr_adapter, tmp_path
    ) -> None:
        """QR-decoded guia_ids match serie-numero format, NOT guia_page_N."""
        pipeline = _make_pipeline(
            pdf_src, digital_extractor, page_to_registro, FakeOCR(), qr_adapter
        )
        ctx = RunContext(pdf_path=_PDF_PATH, output_base=tmp_path / "qr_pattern")
        result = pipeline.run(ctx)

        qr_guias = [g for g in result.guias if g.identity_source == "qr"]
        assert qr_guias, "No QR guias found — cannot validate guia_id pattern"

        for guia in qr_guias:
            assert _GUIA_ID_PATTERN.match(guia.guia_id), (
                f"guia_id {guia.guia_id!r} does not match ^[A-Z]\\d+-\\d+$ "
                f"(serie-numero format). EXT-015 violated."
            )
            assert not _FORBIDDEN_GUIA_PAGE_PATTERN.match(guia.guia_id), (
                f"guia_id {guia.guia_id!r} matches forbidden 'guia_page_N' pattern. "
                f"S1.5 invariant violated."
            )


# ---------------------------------------------------------------------------
# S1.9 Test 2 — Block grouping: no guia_page_N guia_ids produced
# ---------------------------------------------------------------------------


@_SKIP_NO_PDF
class TestBlockGroupingE2E:
    """Block assembly never produces guia_page_N identifiers (EXT-S18 at integration level)."""

    def test_no_guia_page_n_ids_with_qr(
        self, pdf_src, digital_extractor, page_to_registro, qr_adapter, tmp_path
    ) -> None:
        """With QR adapter wired: no guia_page_N ids in output."""
        pipeline = _make_pipeline(
            pdf_src, digital_extractor, page_to_registro, FakeOCR(), qr_adapter
        )
        ctx = RunContext(pdf_path=_PDF_PATH, output_base=tmp_path / "block_qr")
        result = pipeline.run(ctx)

        bad_ids = [
            g.guia_id for g in result.guias if _FORBIDDEN_GUIA_PAGE_PATTERN.match(g.guia_id)
        ]
        assert not bad_ids, (
            f"Found guia_page_N IDs in output (MUST be 0 after S1.5): {bad_ids}"
        )

    def test_no_guia_page_n_ids_without_qr(
        self, pdf_src, digital_extractor, page_to_registro, tmp_path
    ) -> None:
        """Without QR adapter: OCR fallback uses ocr_N pattern, never guia_page_N."""
        pipeline = _make_pipeline(
            pdf_src, digital_extractor, page_to_registro, FakeOCR(), identity_adapter=None
        )
        ctx = RunContext(pdf_path=_PDF_PATH, output_base=tmp_path / "block_ocr")
        result = pipeline.run(ctx)

        bad_ids = [
            g.guia_id for g in result.guias if _FORBIDDEN_GUIA_PAGE_PATTERN.match(g.guia_id)
        ]
        assert not bad_ids, (
            f"OCR fallback path still produced guia_page_N IDs: {bad_ids}"
        )


# ---------------------------------------------------------------------------
# S1.9 Test 3 — UNRESOLVED: guias with registro=None surface (EXT-S19, REC-C06)
# ---------------------------------------------------------------------------


@_SKIP_NO_PDF
class TestUnresolvedGuiasE2E:
    """Guia pages outside all known section ranges surface with registro=None (UNRESOLVED)."""

    def test_guias_outside_section_range_have_none_registro(
        self, pdf_src, digital_extractor, page_to_registro, qr_adapter, tmp_path
    ) -> None:
        """A guia page not in page_to_registro gets registro=None in the output.

        We inject a GUIA text marker on page 0 (the Contents page, which maps to
        no known section) to force an UNRESOLVED guia to appear.  This validates
        that the pipeline never silently drops these pages — they surface as
        registro=None.
        """
        # Page 0 is the Contents page — it won't be in page_to_registro
        guia_pages_with_orphan = _SECTION_4252_GUIA_PAGES | {0}

        hybrid_src = HybridDocSource(pdf_src, guia_pages_with_orphan)
        extractor = CompositeExtractionAdapter.__new__(CompositeExtractionAdapter)
        extractor._declared_adapter = digital_extractor
        extractor._ocr_adapter = FakeOCR()

        config = AppConfig()
        pipeline = ReconciliationPipeline(
            doc_source=hybrid_src,
            extractor=extractor,
            vision=FakeVision(),
            config=config,
            page_to_registro=page_to_registro,
            identity=qr_adapter,
        )
        ctx = RunContext(pdf_path=_PDF_PATH, output_base=tmp_path / "unresolved")
        result = pipeline.run(ctx)

        unresolved = [g for g in result.guias if g.registro is None]
        assert unresolved, (
            "Expected >=1 GuiaDeRemision with registro=None for the orphaned guia page; "
            "got none. UNRESOLVED guias must not be silently dropped (EXT-S19)."
        )
        # Verify the orphaned page appears in unresolved guias
        orphan_guias = [g for g in unresolved if 0 in g.source_pages]
        assert orphan_guias, (
            "Page 0 was injected as a GUIA page outside all section ranges; "
            "expected it to appear as an unresolved guia (registro=None). "
            f"All unresolved guias: {[(g.guia_id, g.source_pages) for g in unresolved]}"
        )


# ---------------------------------------------------------------------------
# S1.9 Test 4 — Section-ID guard: is_section_id(guia.registro) always False
# ---------------------------------------------------------------------------


@_SKIP_NO_PDF
class TestSectionIdGuardE2E:
    """No GuiaDeRemision in output should have a section ID as its registro (EXT-S20)."""

    def test_no_section_id_in_guia_registro(
        self, pdf_src, digital_extractor, page_to_registro, qr_adapter, tmp_path
    ) -> None:
        """For all guias with non-None registro, is_section_id(registro) must be False."""
        pipeline = _make_pipeline(
            pdf_src, digital_extractor, page_to_registro, FakeOCR(), qr_adapter
        )
        ctx = RunContext(pdf_path=_PDF_PATH, output_base=tmp_path / "section_guard")
        result = pipeline.run(ctx)

        violated = [
            (g.guia_id, g.registro)
            for g in result.guias
            if g.registro is not None and is_section_id(g.registro)
        ]
        assert not violated, (
            f"Guías with section-ID registro found (MUST be 0 after S1.4 / EXT-S20): {violated}"
        )


# ---------------------------------------------------------------------------
# S1.9 Test 5 — Guia-contribution inline: rows.guias non-empty (REC-C02)
# ---------------------------------------------------------------------------


@_SKIP_NO_PDF
class TestGuiaContributionInlineE2E:
    """ReconciliationRow.guias is non-empty for MATCH and MISMATCH rows.

    Strategy: inject only ONE guia page (page 4) with the exact declared_qty.
    With one guia page and QR decode active, a single GuiaDeRemision block is
    produced; its contribution to the reconciliation group equals declared_qty,
    producing a MATCH.  Using declared_qty + 1.0 produces a MISMATCH.

    Using one page avoids the 20×declared_qty summing artefact that occurs when
    all 20 guia pages in section 4252 each contribute the same material line.
    """

    # Single guia page chosen for contribution tests — must carry a QR code
    # (confirmed by smoke check: page 4, 0-based, QR decodes to T073-0680256)
    _SINGLE_GUIA_PAGE: set[int] = {4}

    def _run_single_guia_with_qty(
        self,
        pdf_src,
        digital_extractor,
        page_to_registro,
        qr_adapter,
        tmp_path: Path,
        fake_qty: Decimal,
        run_label: str,
    ):
        """Run with ONE guia page returning the given qty; return (result, first_line)."""
        from reconciliation.adapters.pdf.digital_text_extractor import (
            DigitalTextExtractionAdapter,
        )

        digital = DigitalTextExtractionAdapter()
        proto_text = pdf_src.page_text(3)
        assert proto_text is not None
        proto_reg = digital.extract_registro_from_proto_page(proto_text, 3)
        assert proto_reg is not None and proto_reg.declared_lines

        first_line = proto_reg.declared_lines[0]
        fake_line = MaterialLine(
            description_raw=first_line.description_raw,
            description_canonical=first_line.description_canonical,
            unidad=first_line.unidad,
            cantidad=fake_qty,
            confidence=0.95,
        )

        hybrid_src = HybridDocSource(pdf_src, self._SINGLE_GUIA_PAGE)
        extractor = CompositeExtractionAdapter.__new__(CompositeExtractionAdapter)
        extractor._declared_adapter = digital
        extractor._ocr_adapter = FakeOCRWithLine(fake_line)

        config = AppConfig()
        pipeline = ReconciliationPipeline(
            doc_source=hybrid_src,
            extractor=extractor,
            vision=FakeVision(),
            config=config,
            page_to_registro=page_to_registro,
            identity=qr_adapter,
        )
        ctx = RunContext(pdf_path=_PDF_PATH, output_base=tmp_path / f"contrib_{run_label}")
        return pipeline.run(ctx), first_line

    def test_match_row_has_non_empty_guias(
        self, pdf_src, digital_extractor, page_to_registro, qr_adapter, tmp_path
    ) -> None:
        """MATCH rows have non-empty guias[] with at least one GuiaContribution."""
        from reconciliation.adapters.pdf.digital_text_extractor import (
            DigitalTextExtractionAdapter,
        )

        digital = DigitalTextExtractionAdapter()
        proto_text = pdf_src.page_text(3)
        assert proto_text is not None
        proto_reg = digital.extract_registro_from_proto_page(proto_text, 3)
        assert proto_reg is not None and proto_reg.declared_lines

        declared_qty = proto_reg.declared_lines[0].cantidad

        # One guia page contributes exactly declared_qty → MATCH
        result, _ = self._run_single_guia_with_qty(
            pdf_src,
            digital_extractor,
            page_to_registro,
            qr_adapter,
            tmp_path,
            fake_qty=declared_qty,
            run_label="match",
        )

        match_rows = [r for r in result.rows if r.status == "MATCH"]
        assert match_rows, (
            f"Expected >=1 MATCH row after injecting declared qty from one guia page; "
            f"statuses for registro 232: {[r.status for r in result.rows if r.registro == '232']}"
        )
        for row in match_rows:
            assert row.guias, (
                f"MATCH row registro={row.registro!r} material={row.material_canonical!r} "
                f"has empty guias[]. REC-C02 violated."
            )

    def test_mismatch_row_has_non_empty_guias(
        self, pdf_src, digital_extractor, page_to_registro, qr_adapter, tmp_path
    ) -> None:
        """MISMATCH rows have non-empty guias[] with at least one GuiaContribution."""
        from reconciliation.adapters.pdf.digital_text_extractor import (
            DigitalTextExtractionAdapter,
        )

        digital = DigitalTextExtractionAdapter()
        proto_text = pdf_src.page_text(3)
        assert proto_text is not None
        proto_reg = digital.extract_registro_from_proto_page(proto_text, 3)
        assert proto_reg is not None and proto_reg.declared_lines

        declared_qty = proto_reg.declared_lines[0].cantidad
        perturbed_qty = declared_qty + Decimal("1.000")

        result, _ = self._run_single_guia_with_qty(
            pdf_src,
            digital_extractor,
            page_to_registro,
            qr_adapter,
            tmp_path,
            fake_qty=perturbed_qty,
            run_label="mismatch",
        )

        mismatch_rows = [r for r in result.rows if r.status == "MISMATCH"]
        assert mismatch_rows, (
            f"Expected >=1 MISMATCH row after injecting perturbed qty; "
            f"statuses for registro 232: {[r.status for r in result.rows if r.registro == '232']}"
        )
        for row in mismatch_rows:
            assert row.guias, (
                f"MISMATCH row registro={row.registro!r} material={row.material_canonical!r} "
                f"has empty guias[]. REC-C02 violated."
            )

    def test_summed_qty_derived_from_guias(
        self, pdf_src, digital_extractor, page_to_registro, qr_adapter, tmp_path
    ) -> None:
        """summed_qty is derived correctly from guias[] (REC-C04 computed invariant)."""
        from reconciliation.adapters.pdf.digital_text_extractor import (
            DigitalTextExtractionAdapter,
        )

        digital = DigitalTextExtractionAdapter()
        proto_text = pdf_src.page_text(3)
        proto_reg = digital.extract_registro_from_proto_page(proto_text, 3)
        assert proto_reg is not None and proto_reg.declared_lines

        declared_qty = proto_reg.declared_lines[0].cantidad
        perturbed_qty = declared_qty + Decimal("2.000")

        result, _ = self._run_single_guia_with_qty(
            pdf_src,
            digital_extractor,
            page_to_registro,
            qr_adapter,
            tmp_path,
            fake_qty=perturbed_qty,
            run_label="derived_qty",
        )

        rows_232 = [r for r in result.rows if r.registro == "232"]
        rows_with_guias = [r for r in rows_232 if r.guias]
        assert rows_with_guias, "No rows with guias[] found for registro 232"

        for row in rows_with_guias:
            expected = sum(g.cantidad for g in row.guias)
            assert row.summed_qty == expected, (
                f"summed_qty {row.summed_qty} != sum(guias.cantidad) {expected} "
                f"for row {row.registro}/{row.material_canonical}. REC-C04 violated."
            )


# ---------------------------------------------------------------------------
# S1.9 Test 6 — Line-edit e2e via ReviewService (REC-C04)
# ---------------------------------------------------------------------------


@_SKIP_NO_PDF
class TestLineEditE2E:
    """PATCH /guias/{guia_id}/lines on a MISMATCH row changes summed_qty."""

    # Single guia page for line-edit tests — page 4 carries QR code T073-0680256
    _SINGLE_GUIA_PAGE: set[int] = {4}

    def _build_mismatch_run(
        self, pdf_src, digital_extractor, page_to_registro, qr_adapter, tmp_path, run_label: str
    ):
        """Build a pipeline run with ONE guia page contributing declared_qty+5.

        Returns (result, declared_qty, target_guia_id, ctx).
        """
        from reconciliation.adapters.pdf.digital_text_extractor import (
            DigitalTextExtractionAdapter,
        )

        digital = DigitalTextExtractionAdapter()
        proto_text = pdf_src.page_text(3)
        proto_reg = digital.extract_registro_from_proto_page(proto_text, 3)
        assert proto_reg is not None and proto_reg.declared_lines

        first_line = proto_reg.declared_lines[0]
        declared_qty = first_line.cantidad
        perturbed_qty = declared_qty + Decimal("5.000")

        fake_line = MaterialLine(
            description_raw=first_line.description_raw,
            description_canonical=first_line.description_canonical,
            unidad=first_line.unidad,
            cantidad=perturbed_qty,
            confidence=0.95,
        )

        hybrid_src = HybridDocSource(pdf_src, self._SINGLE_GUIA_PAGE)
        extractor = CompositeExtractionAdapter.__new__(CompositeExtractionAdapter)
        extractor._declared_adapter = digital
        extractor._ocr_adapter = FakeOCRWithLine(fake_line)

        config = AppConfig()
        pipeline = ReconciliationPipeline(
            doc_source=hybrid_src,
            extractor=extractor,
            vision=FakeVision(),
            config=config,
            page_to_registro=page_to_registro,
            identity=qr_adapter,
        )
        ctx = RunContext(pdf_path=_PDF_PATH, output_base=tmp_path / run_label)
        result = pipeline.run(ctx)

        mismatch_rows = [r for r in result.rows if r.status == "MISMATCH" and r.registro == "232"]
        assert mismatch_rows, (
            "Expected a MISMATCH row for registro 232; "
            f"all 232 rows: "
            f"{[(r.status, r.summed_qty) for r in result.rows if r.registro == '232']}"
        )
        assert mismatch_rows[0].guias, "MISMATCH row has no guias[]"
        target_guia_id = mismatch_rows[0].guias[0].guia_id

        return result, declared_qty, target_guia_id, ctx

    def test_line_edit_changes_summed_qty(
        self, pdf_src, digital_extractor, page_to_registro, qr_adapter, tmp_path
    ) -> None:
        """Apply guia line edit on a MISMATCH row → summed_qty changes as expected."""
        from reconciliation.application.review_service import ReviewService

        result, declared_qty, target_guia_id, ctx = self._build_mismatch_run(
            pdf_src, digital_extractor, page_to_registro, qr_adapter, tmp_path, "line_edit"
        )

        # Build ReviewService from the sidecar
        review_service = ReviewService.restore_from_sidecar(
            declared=result.declared,
            guias=result.guias,
            rows=result.rows,
            ctx=ctx,
        )

        # Record summed_qty before edit
        before_rows = [
            r for r in review_service.rows if r.status == "MISMATCH" and r.registro == "232"
        ]
        assert before_rows
        before_summed = before_rows[0].summed_qty

        # Apply line edit: change quantity to declared_qty (resolves the mismatch)
        updated_rows = review_service.apply_guia_line_edit(
            guia_id=target_guia_id,
            line_index=0,
            material_canonical=None,
            new_cantidad=declared_qty,
        )

        # Verify summed_qty changed
        after_rows_232 = [r for r in updated_rows if r.registro == "232"]
        assert after_rows_232, "No rows for registro 232 after line edit"

        after_summed_values = [r.summed_qty for r in after_rows_232]
        assert any(s != before_summed for s in after_summed_values), (
            f"summed_qty did not change after line edit: before={before_summed}, "
            f"after values={after_summed_values}"
        )

    def test_line_edit_via_api_route_changes_summed_qty(
        self, pdf_src, digital_extractor, page_to_registro, qr_adapter, tmp_path
    ) -> None:
        """PATCH /runs/{id}/guias/{guia_id}/lines returns updated rows with changed summed_qty."""
        from fastapi.testclient import TestClient

        from reconciliation.application.review_service import ReviewService
        from reconciliation.infrastructure.api.main import create_app

        result, declared_qty, target_guia_id, ctx = self._build_mismatch_run(
            pdf_src, digital_extractor, page_to_registro, qr_adapter, tmp_path, "api_line_edit"
        )
        before_summed = float(next(
            r.summed_qty for r in result.rows if r.status == "MISMATCH" and r.registro == "232"
        ))

        review_service = ReviewService.restore_from_sidecar(
            declared=result.declared,
            guias=result.guias,
            rows=result.rows,
            ctx=ctx,
        )

        run_id = ctx.run_id
        app = create_app()

        with TestClient(app) as client:
            # Seed after lifespan starts — the lifespan initialises the registry dict
            # and we inject our run entry directly into that dict (same pattern as
            # test_api_routes._seed_run).
            client.app.state.run_registry[run_id] = {  # type: ignore[attr-defined]
                "status": "review",
                "ctx": ctx,
                "result": result,
                "review_service": review_service,
                "page_to_registro": page_to_registro,
                "vision_calls_made": 0,
                "warnings": [],
                "error": None,
            }
            resp = client.patch(
                f"/api/v1/runs/{run_id}/guias/{target_guia_id}/lines",
                json={"line_index": 0, "cantidad": float(declared_qty)},
            )

        assert resp.status_code == 200, f"Expected 200; got {resp.status_code}: {resp.text}"

        rows_data = resp.json()["rows"]
        assert isinstance(rows_data, list) and rows_data, "Response rows list is empty"

        rows_232 = [r for r in rows_data if r["registro"] == "232"]
        assert rows_232, "No rows for registro 232 in response"

        after_summed_values = [r["summed_qty"] for r in rows_232]
        assert any(s != before_summed for s in after_summed_values), (
            f"summed_qty did not change after API line edit: "
            f"before={before_summed}, after={after_summed_values}"
        )


# ---------------------------------------------------------------------------
# S1.9 Test 7 — Thumbnail e2e: GET /pages/{page}/thumbnail → 200 + PNG
# ---------------------------------------------------------------------------


@_SKIP_NO_PDF
class TestThumbnailE2E:
    """GET /runs/{id}/pages/{page}/thumbnail returns 200 + image/png."""

    def test_thumbnail_endpoint_returns_png(self, tmp_path) -> None:
        """Create a run with a synthetic page PNG and assert the endpoint returns it."""
        import uuid

        from fastapi.testclient import TestClient

        from reconciliation.application.run_context import RunContext
        from reconciliation.infrastructure.api.main import create_app

        run_id = str(uuid.uuid4())
        ctx = RunContext(pdf_path=_PDF_PATH, output_base=tmp_path / "thumb_runs", run_id=run_id)

        # Create the pages directory and write a synthetic PNG file
        pages_dir = ctx.run_dir / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)
        page_file = pages_dir / "0000.png"

        # Minimal valid 1x1 white PNG (89 bytes)
        _MINIMAL_PNG = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
            b"\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
            b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        page_file.write_bytes(_MINIMAL_PNG)

        app = create_app()

        with TestClient(app) as client:
            # Seed after lifespan starts (same pattern as test_api_routes._seed_run)
            client.app.state.run_registry[run_id] = {  # type: ignore[attr-defined]
                "status": "review",
                "ctx": ctx,
                "result": None,
                "review_service": None,
                "page_to_registro": {},
                "vision_calls_made": 0,
                "warnings": [],
                "error": None,
            }
            resp = client.get(f"/api/v1/runs/{run_id}/pages/0/thumbnail")

        assert resp.status_code == 200, (
            f"Expected 200 for thumbnail; got {resp.status_code}: {resp.text}"
        )
        content_type = resp.headers.get("content-type", "")
        assert "image/png" in content_type, (
            f"Expected image/png content-type; got {content_type!r}"
        )

    def test_thumbnail_returns_404_for_missing_page(self, tmp_path) -> None:
        """Thumbnail endpoint returns 404 when the page file does not exist."""
        import uuid

        from fastapi.testclient import TestClient

        from reconciliation.application.run_context import RunContext
        from reconciliation.infrastructure.api.main import create_app

        run_id = str(uuid.uuid4())
        ctx = RunContext(pdf_path=_PDF_PATH, output_base=tmp_path / "thumb_404", run_id=run_id)

        # Do NOT create any page file
        app = create_app()

        with TestClient(app) as client:
            client.app.state.run_registry[run_id] = {  # type: ignore[attr-defined]
                "status": "review",
                "ctx": ctx,
                "result": None,
                "review_service": None,
                "page_to_registro": {},
                "vision_calls_made": 0,
                "warnings": [],
                "error": None,
            }
            resp = client.get(f"/api/v1/runs/{run_id}/pages/99/thumbnail")

        assert resp.status_code == 404, (
            f"Expected 404 for missing page; got {resp.status_code}"
        )
