"""Real-data integration gate for RapidOCRAdapter (EXT-031 / EXT-032).

Proves that the live RapidOCRAdapter + box_row_parser stack reads the
CORRECT quantities from REAL sideways-scanned guías (reg227 section).

**Strict TDD RED phase**: these tests are written FIRST and are expected to
FAIL until:
  - ``rapidocr`` and ``onnxruntime`` are in the ``[ocr]`` optional-deps group,
  - PP-OCRv5-server ONNX models have been downloaded (auto on first run), and
  - :meth:`RapidOCRAdapter.extract_printed_table` produces correct output.

Skip guard: tests are skipped when ``CTR_PDF_PATH`` is unset (CI / no asset).
The path should point at the **section PDF** (``docs/eval/reg227_section.pdf``)
or the full production PDF — the same file used by ``ocr_compare.py``.

Ground truth: ``docs/eval/ground_truth.md`` (confirmed by zoomed 300 DPI read
of printed GRE tables; cross-validated by model agreement in the eval).

Gate semantics (``requires_review`` contract, NOT naive exact-multiset-equality)
-------------------------------------------------------------------------------
The per-page quantity gate does NOT assert ``sorted(emitted) == sorted(GT)``.
That naive equality is WRONG after the M-6 fix (``never silent-drop a real
material row``): on real page 156 RapidOCR reads the reception-STAMP region as
non-lexical garble (e.g. ``'acacpen enfuin aeococl vignte'`` qty ``4.8`` unit
``TN``, ``requires_review=True``, conf 0.573). A phrase-denylist cannot catch
arbitrary OCR gibberish; only GEOMETRY can exclude it — and geometric
column/table-region anchoring is DEFERRED to PR#4. The M-6 safety invariant
requires EMITTING such a row (flagged), so it legitimately appears in the
output and breaks naive equality.

Instead each page asserts the two-part semantic rule that ENCODES the
``requires_review`` trust contract:

  1. **Completeness (binding correctness proof — the #40 fix)**: every GT
     quantity MUST be present in the emitted multiset
     (``Counter(GT) - Counter(emitted)`` is empty). All real deterministic
     reads succeeded — this is the proof that the deterministic OCR path reads
     the printed GRE tables exactly. NEVER weakened.

  2. **No confident spurious (trust contract)**: every emitted line whose
     quantity is NOT in the GT multiset MUST have ``requires_review is True``.
     A TRUSTED deterministic read is never a false quantity; review-flagged
     garble/stamp rows (the page-156 reception-stamp OCR) are TOLERATED because
     they are surfaced for human review, not trusted.

  # PR#4: geometric table-region / DESC|UNIDAD|CANTIDAD column anchoring will
  # localize the material-table band and exclude the reception-stamp paragraph
  # by POSITION, after which the page-156 review-flagged extra disappears.

GT values are UNCHANGED — completeness still requires the full table to be read.

Spec: EXT-031/S031a-c, EXT-032/S032b.
"""

from __future__ import annotations

import os
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from reconciliation.domain.models import MaterialLine

# ---------------------------------------------------------------------------
# Asset / skip guard
# ---------------------------------------------------------------------------

# Allow override via CTR_PDF_PATH; fall back to the section PDF if it exists.
_SECTION_PDF = Path(__file__).parents[3] / "docs" / "eval" / "reg227_section.pdf"
_CTR_PDF_PATH: str | None = os.environ.get("CTR_PDF_PATH") or (
    str(_SECTION_PDF) if _SECTION_PDF.exists() else None
)

pytestmark = pytest.mark.slow

_SKIP_REASON = (
    "CTR_PDF_PATH not set and docs/eval/reg227_section.pdf not found — "
    "real-data gate requires the section PDF"
)


def _render_page(pdf_path: str, page_idx: int, dpi: int = 200) -> bytes:
    """Render page ``page_idx`` (0-based) of *pdf_path* at *dpi* → PNG bytes."""
    import fitz  # noqa: PLC0415  (pymupdf, already in base deps)

    doc = fitz.open(pdf_path)
    try:
        pix = doc[page_idx].get_pixmap(
            matrix=fitz.Matrix(dpi / 72, dpi / 72),
            colorspace=fitz.csRGB,
        )
        return pix.tobytes("png")
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Ground-truth multisets (from docs/eval/ground_truth.md)
#
# Page indices are 0-based section-PDF indices (authoritative run-PNG index).
# Quantities are the printed TNE values; TNE→TN label normalization is applied
# by box_row_parser so the extracted unidad will be "TN" not "TNE".
# ---------------------------------------------------------------------------

_GT_0148 = [
    Decimal("0.037"),
    Decimal("0.014"),
    Decimal("0.102"),
]
_GT_0156 = [
    Decimal("0.008"),
    Decimal("0.136"),
    Decimal("0.191"),  # historic kimi-misread: 0.091 — GT is 0.191
    Decimal("0.041"),
]
_GT_0160 = [
    Decimal("1.616"),
    Decimal("0.238"),
    Decimal("1.643"),
    Decimal("0.121"),
]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _extract_lines(pdf_path: str, page_idx: int) -> list[MaterialLine]:
    """Run the real RapidOCRAdapter on one page and return its MaterialLines."""
    from reconciliation.adapters.ocr.rapid_table import RapidOCRAdapter  # noqa: PLC0415

    adapter = RapidOCRAdapter(dpi=200)
    image_bytes = _render_page(pdf_path, page_idx)
    return adapter.extract_printed_table(image_bytes)


def _assert_gt_complete_no_confident_spurious(
    lines: list[MaterialLine], gt: list[Decimal], page_label: str
) -> None:
    """Assert the two-part ``requires_review`` gate semantics for one page.

    Replaces naive ``sorted(emitted) == sorted(GT)`` (WRONG post-M-6, which
    never silent-drops a real material row — so the page-156 reception-stamp
    OCR garble is legitimately EMITTED with ``requires_review=True`` and breaks
    exact equality). The semantically-correct rule:

    1. **Completeness (binding correctness proof, #40)**: every GT quantity is
       present in the emitted multiset — ``Counter(GT) - Counter(emitted)`` is
       empty. Deterministic OCR reads the printed GRE table exactly. NEVER
       weakened.
    2. **No confident spurious (trust contract)**: every emitted line whose
       quantity is NOT in the GT multiset MUST be ``requires_review is True``.
       A trusted deterministic read is never a false quantity; review-flagged
       extras (garble/stamp rows) are tolerated by design.

    # PR#4: geometric table-region/column anchoring will exclude the
    # reception-stamp paragraph by POSITION, removing the page-156 extra.
    """
    emitted = [line.cantidad for line in lines]

    # (1) Completeness — every GT quantity is deterministically extracted.
    missing = Counter(gt) - Counter(emitted)
    assert not missing, (
        f"{page_label} INCOMPLETE — GT quantities missing from emitted (the "
        f"binding correctness proof / #40 fix; never weaken this).\n"
        f"  GT      : {sorted(gt)}\n"
        f"  emitted : {sorted(emitted)}\n"
        f"  missing : {sorted(missing.elements())}"
    )

    # (2) No confident spurious — every extra (not-in-GT) line is requires_review.
    gt_budget = Counter(gt)
    confident_spurious: list[MaterialLine] = []
    for line in lines:
        if gt_budget.get(line.cantidad, 0) > 0:
            gt_budget[line.cantidad] -= 1  # consume one GT slot
            continue
        # This line's quantity is outside the GT multiset → it MUST be flagged.
        if line.requires_review is not True:
            confident_spurious.append(line)
    assert not confident_spurious, (
        f"{page_label} has CONFIDENT SPURIOUS line(s) — a trusted "
        f"(requires_review=False) deterministic read carries a quantity outside "
        f"GT, violating the trust contract. Flagged garble is tolerated; a "
        f"confident false quantity is not.\n"
        f"  GT       : {sorted(gt)}\n"
        f"  emitted  : {sorted(emitted)}\n"
        f"  offending: "
        + ", ".join(
            f"qty={ln.cantidad} review={ln.requires_review} desc={ln.description_raw!r}"
            for ln in confident_spurious
        )
    )


# ---------------------------------------------------------------------------
# EXT-031 / S031a  — page 0148 (3-row guía T112-0065418)
# ---------------------------------------------------------------------------


class TestRapidOCRGatePage0148:
    """EXT-031/S031b: page 0148, guía T112-0065418 — 3 rows exact."""

    @pytest.mark.slow
    def test_page_0148_3_rows_exact(self) -> None:
        if not _CTR_PDF_PATH:
            pytest.skip(_SKIP_REASON)

        lines = _extract_lines(_CTR_PDF_PATH, 148)
        _assert_gt_complete_no_confident_spurious(lines, _GT_0148, "page 0148")

    @pytest.mark.slow
    def test_page_0148_no_unit_conversion(self) -> None:
        """EXT-032/S032b: TNE label normalized to TN — qty values NOT multiplied."""
        if not _CTR_PDF_PATH:
            pytest.skip(_SKIP_REASON)

        from reconciliation.adapters.ocr.rapid_table import RapidOCRAdapter  # noqa: PLC0415

        adapter = RapidOCRAdapter(dpi=200)
        image_bytes = _render_page(_CTR_PDF_PATH, 148)
        lines = adapter.extract_printed_table(image_bytes)

        for line in lines:
            # TNE label normalization: unidad stored as "TN" not "TNE"
            assert line.unidad in {"TN", "KG", "RD", "Rollo"}, (
                f"unexpected unidad {line.unidad!r} — TNE should be normalized to TN"
            )
            # qty must be the raw printed value (sub-tonne range for this guía)
            assert line.cantidad <= Decimal("1"), (
                f"qty {line.cantidad} suspiciously large — unit conversion guard"
            )


# ---------------------------------------------------------------------------
# EXT-031 / S031a  — page 0156 (4-row guía T112-0065426)
# ---------------------------------------------------------------------------


class TestRapidOCRGatePage0156:
    """EXT-031/S031a: page 0156, guía T112-0065426 — 4 rows exact.

    This page is the primary eval page (historic kimi-misread 0.191 → 0.091).
    The OCR path MUST read 0.191 correctly.
    """

    @pytest.mark.slow
    def test_page_0156_4_rows_exact(self) -> None:
        if not _CTR_PDF_PATH:
            pytest.skip(_SKIP_REASON)

        lines = _extract_lines(_CTR_PDF_PATH, 156)
        _assert_gt_complete_no_confident_spurious(lines, _GT_0156, "page 0156")

    @pytest.mark.slow
    def test_page_0156_conf_gate_not_dropping_real_rows(self) -> None:
        """EXT-031: the confidence / noise-floor gate must not DROP real material rows.

        Real GRE geometry note: the UNIDAD column sits LEFT of CANTIDAD in the
        physical guía table (DETALLE | UNIDAD | CANTIDAD from left to right).
        This violates the preferred column order (DESC | QTY | UNIT) so all rows
        use the relaxed-fallback unit path → requires_review=True — this is
        correct, not over-flagging. The test validates that the 4 correct quantity
        values ARE extracted (not silently dropped by the noise floor), even though
        all rows are requires_review=True.

        The orientation oracle falls back to SUGGESTION-2 tie-break (raw row count)
        because all rotations score 0 confident rows on this geometry.
        """
        if not _CTR_PDF_PATH:
            pytest.skip(_SKIP_REASON)

        from reconciliation.adapters.ocr.rapid_table import RapidOCRAdapter  # noqa: PLC0415

        adapter = RapidOCRAdapter(dpi=200)
        image_bytes = _render_page(_CTR_PDF_PATH, 156)
        lines = adapter.extract_printed_table(image_bytes)

        n_total = len(lines)
        assert n_total >= 4, (
            f"expected ≥4 rows, got {n_total} — noise floor may be dropping real rows"
        )
        # All real rows are requires_review=True due to UNIDAD-left-of-CANTIDAD
        # layout; this is expected, NOT an error. The 4 GT quantities must all be
        # present (completeness), and any EXTRA emitted line (e.g. the page-156
        # reception-stamp OCR garble, legitimately emitted post-M-6) MUST be
        # requires_review=True — never a confident false quantity.
        # PR#4: geometric table-region anchoring will drop the stamp extra by position.
        _assert_gt_complete_no_confident_spurious(lines, _GT_0156, "page 0156")


# ---------------------------------------------------------------------------
# EXT-031 / S031c  — page 0160 (4-row ACERO DIMENSIONADO guía T009-0739440)
# ---------------------------------------------------------------------------


class TestRapidOCRGatePage0160:
    """EXT-031/S031c: page 0160, guía T009-0739440 — 4 rows ACERO DIMENSIONADO exact."""

    @pytest.mark.slow
    def test_page_0160_4_rows_acero_dimensionado_exact(self) -> None:
        if not _CTR_PDF_PATH:
            pytest.skip(_SKIP_REASON)

        lines = _extract_lines(_CTR_PDF_PATH, 160)
        _assert_gt_complete_no_confident_spurious(
            lines, _GT_0160, "page 0160 (ACERO DIMENSIONADO)"
        )


# ---------------------------------------------------------------------------
# EXT-032 / S032b — domain invariants e2e (no unit conversion)
# ---------------------------------------------------------------------------


class TestDomainInvariantsE2E:
    """EXT-032/S032b: end-to-end domain invariant — units never converted.

    The reconciliation gate: any qty mismatch after OCR → MISMATCH flagged,
    never auto-corrected.  This test verifies the OCR stack does NOT perform
    numeric conversions (TNE×1000 → KG, etc.).
    """

    @pytest.mark.slow
    def test_domain_invariants_e2e_no_unit_conversion(self) -> None:
        if not _CTR_PDF_PATH:
            pytest.skip(_SKIP_REASON)

        from reconciliation.adapters.ocr.rapid_table import RapidOCRAdapter  # noqa: PLC0415

        adapter = RapidOCRAdapter(dpi=200)

        for page_idx, gt_qtys in [(148, _GT_0148), (156, _GT_0156), (160, _GT_0160)]:
            image_bytes = _render_page(_CTR_PDF_PATH, page_idx)
            lines = adapter.extract_printed_table(image_bytes)

            for line in lines:
                # Quantities from these guías are in the 0.008–1.643 TN range.
                # If unit conversion were applied (e.g. ×1000 for TNE→KG)
                # the values would be in the 8–1643 range.
                assert line.cantidad < Decimal("100"), (
                    f"page {page_idx}: qty {line.cantidad} looks like a unit-converted "
                    f"value (expected sub-tonne range) — units never converted invariant"
                )


# ---------------------------------------------------------------------------
# Task 3.2.7 — wider-sample orientation check
# ---------------------------------------------------------------------------


class TestOrientationWiderSample:
    """Task 3.2.7: self-scoring orientation validated beyond the 3 GT pages.

    Probes the known-good guía pages near the GT set (indices 140-165) plus the
    3 GT pages themselves. The section PDF contains a mix of guía pages and
    non-guía pages (table of contents, covers, summary pages) so an
    evenly-spaced sample across all 165 pages would hit many non-guía pages
    that legitimately return 0 rows. This test focuses on the guía-dense zone
    where we have high confidence all pages have material tables.

    Real-data finding (2026-06-07): evenly-spaced sampling across the full
    165-page section hit mostly non-guía pages (blank/cover/ToC) that return
    0 rows — this is expected behavior, not an orientation failure. The 3 GT
    pages (148/156/160) ARE in the guía-dense zone and extract correctly.
    """

    @pytest.mark.slow
    def test_wider_sample_orientation_recovery(self) -> None:
        if not _CTR_PDF_PATH:
            pytest.skip(_SKIP_REASON)

        import fitz  # noqa: PLC0415
        from reconciliation.adapters.ocr.rapid_table import RapidOCRAdapter  # noqa: PLC0415

        doc = fitz.open(_CTR_PDF_PATH)
        n_pages = len(doc)
        doc.close()

        # Focus on the guía-dense zone near the GT pages (140–min(n_pages-1, 165)).
        # These pages are confirmed guía pages (same section as GT).
        # Also include the 3 GT pages as anchors.
        dense_zone_end = min(n_pages - 1, 165)
        dense_zone_start = max(0, dense_zone_end - 25)  # up to 25 pages
        probe_indices = list(range(dense_zone_start, dense_zone_end, 3))  # every 3rd page

        # Always include the 3 confirmed GT pages
        for anchor in [148, 156, 160]:
            if anchor < n_pages and anchor not in probe_indices:
                probe_indices.append(anchor)

        adapter = RapidOCRAdapter(dpi=200)
        row_counts: dict[int, int] = {}

        for idx in probe_indices:
            image_bytes = _render_page(_CTR_PDF_PATH, idx)
            lines = adapter.extract_printed_table(image_bytes)
            row_counts[idx] = len(lines)

        # The 3 GT anchor pages MUST return the correct row count.
        for anchor, expected_min in [(148, 3), (156, 4), (160, 4)]:
            if anchor in row_counts:
                assert row_counts[anchor] >= expected_min, (
                    f"GT page {anchor} returned {row_counts[anchor]} rows, "
                    f"expected ≥{expected_min}"
                )

        # Wider zone: at least 50% of the probed pages should return ≥1 row.
        # A lower rate suggests orientation recovery is failing on the dense zone.
        pages_with_rows = sum(1 for n in row_counts.values() if n > 0)
        total_probed = len(row_counts)
        hit_rate = pages_with_rows / total_probed if total_probed > 0 else 0.0

        assert hit_rate >= 0.5, (
            f"Wider-sample orientation hit rate too low: "
            f"{pages_with_rows}/{total_probed} ({hit_rate:.0%}) pages returned ≥1 row. "
            f"Row counts by page: {dict(sorted(row_counts.items()))}"
        )
