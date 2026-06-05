"""Unit tests for the QR-identity absorb gate in _stage_assemble_blocks (EXT-019 rev-3).

Tests EXT-S19a, EXT-S19b (real-data reg228 model), EXT-S19c (INVERTED — p152 photo
dropped), EXT-S19d (EXT-S24 pin), EXT-S19e, EXT-S19f (true multi-QR-page guia).
TestConditionCContinuationAbsorbed INVERTED — text-title non-QR continuation dropped.

Strategy: call _stage_assemble_blocks directly on a minimal pipeline instance,
injecting pre-built _RawGuia and PageClassification lists so we can control
classification precisely without a full pipeline run.

Rev-3 gate: absorb = identity is not None.
A non-QR page (identity is None) is DROPPED regardless of title_matched or anchor type.
A same-guia_id QR page (identity is not None) is absorbed into the open block.

STRICT TDD: EXT-S19c and TestConditionCContinuationAbsorbed were INVERTED (RED-first)
before the rev-3 gate replacement. EXT-S19b and EXT-S19f were added RED-first.
EXT-S19a, EXT-S19d, EXT-S19e remain correct under the new predicate and stay GREEN.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from reconciliation.application.config import AppConfig
from reconciliation.application.pipeline import (
    DecodeOutcome,
    ReconciliationPipeline,
    _GuiaBlock,
    _RawGuia,
)
from reconciliation.domain.classifier import PageClassifier
from reconciliation.domain.models import (
    GuiaIdentity,
    MaterialLine,
    PageClassification,
    VisionResult,
)


# ---------------------------------------------------------------------------
# Minimal fake adapters (no external deps)
# ---------------------------------------------------------------------------


class _FakeDoc:
    def page_count(self) -> int:
        return 0

    def render_page(self, idx: int, dpi: int = 200) -> bytes:
        return b"\x89PNG"

    def page_text(self, idx: int) -> str | None:
        return None


class _FakeExtractor:
    def extract_declared(self, text: str) -> list[MaterialLine]:
        return []

    def extract_printed_table(self, image: bytes) -> list[MaterialLine]:
        return []


class _FakeVision:
    supports_batch: bool = False

    def read_handwritten_date(
        self, image: bytes, hint: str | None = None
    ) -> VisionResult:
        from datetime import date

        return VisionResult(date=date(2026, 5, 1), confidence=0.99, raw="01/05/2026")


def _make_pipeline() -> ReconciliationPipeline:
    """Construct a minimal pipeline suitable for calling _stage_assemble_blocks."""
    return ReconciliationPipeline(
        doc_source=_FakeDoc(),
        extractor=_FakeExtractor(),
        vision=_FakeVision(),
        config=AppConfig(),
    )


def _identity(serie: str, numero: str) -> GuiaIdentity:
    return GuiaIdentity(
        serie=serie,
        numero=numero,
        ruc_emisor="12345678901",
        ruc_receptor="10987654321",
        tipo="09",
        hashqr_url=None,
        confidence=1.0,
    )


_MAT_LINE = MaterialLine(
    description_raw="BARRA 3/8",
    description_canonical="barra 3/8",
    unidad="KG",
    cantidad=Decimal("100"),
    confidence=0.95,
)

_PNG = b"\x89PNG"


def _cls(page: int, title_matched: str | None, kind: str = "GUIA") -> PageClassification:
    return PageClassification(
        page=page,
        kind=kind,  # type: ignore[arg-type]
        title_matched=title_matched,
        confidence=1.0,
    )


def _decode_qr(identity: GuiaIdentity) -> DecodeOutcome:
    return DecodeOutcome(
        identity=identity,
        hashqr_url=None,
        rendered=_PNG,
        decoded=True,
    )


def _decode_no_qr() -> DecodeOutcome:
    return DecodeOutcome(
        identity=None,
        hashqr_url=None,
        rendered=_PNG,
        decoded=False,
    )


# ---------------------------------------------------------------------------
# EXT-S19a — Condition-B page NOT adjacent to a QR-opened block → NOT absorbed
# ---------------------------------------------------------------------------


class TestEXTS19aConditionBNoQrBlockNotAbsorbed:
    """EXT-S19a: a FORMA_HEADER_HEURISTIC page with no preceding QR block must be dropped."""

    def test_no_open_block_condition_b_produces_no_guia(self) -> None:
        """Condition-B continuation, ocr_fallback anchor, same registro → NOT absorbed.

        p0 opens a block with identity_source="ocr_fallback" (no QR). p1 is a
        FORMA_HEADER_HEURISTIC page in the SAME registro. The positional gate
        MUST prevent absorption because the anchor's identity_source != "qr".
        """
        classifications = [
            _cls(0, "QR_IDENTITY"),
            _cls(1, "FORMA_HEADER_HEURISTIC"),
        ]

        p0_ocr = _RawGuia(guia_id="", source_page=0, image=_PNG, lines=[_MAT_LINE], registro="232")
        p1_heur = _RawGuia(guia_id="", source_page=1, image=_PNG, lines=[], registro="232")

        decode_map = {
            0: _decode_no_qr(),
            1: _decode_no_qr(),
        }
        pipeline = _make_pipeline()
        blocks = pipeline._stage_assemble_blocks(
            [p0_ocr, p1_heur], classifications, decode_map=decode_map
        )

        assert len(blocks) == 1, (
            f"Expected 1 block (p1 dropped by gate); got {len(blocks)}"
        )
        block = blocks[0]
        assert 1 not in block.source_pages, (
            f"p1 (FORMA_HEADER_HEURISTIC, no QR anchor) must NOT appear in source_pages: {block.source_pages}"
        )

    def test_condition_b_same_registro_ocr_fallback_anchor_not_absorbed(self) -> None:
        """Condition-B page same registro, preceding block is ocr_fallback → gate drops it."""
        p0 = _RawGuia(guia_id="", source_page=0, image=_PNG, lines=[_MAT_LINE], registro="232")
        p1 = _RawGuia(guia_id="", source_page=1, image=_PNG, lines=[], registro="232")

        classifications = [
            _cls(0, "GUIA DE REMISION"),   # Condition C — text title
            _cls(1, "FORMA_HEADER_HEURISTIC"),  # Condition B — no identity
        ]
        decode_map = {
            0: _decode_no_qr(),
            1: _decode_no_qr(),
        }
        pipeline = _make_pipeline()
        blocks = pipeline._stage_assemble_blocks(
            [p0, p1], classifications, decode_map=decode_map
        )

        assert len(blocks) == 1
        # p1 dropped by positional gate (no QR anchor)
        assert 1 not in blocks[0].source_pages, (
            f"Condition-B page must NOT be absorbed into an ocr_fallback block: {blocks[0].source_pages}"
        )


# ---------------------------------------------------------------------------
# Condition-C continuation — text-title GUIA page, identity None → DROPPED.
# Rev-3 gate: absorb = identity is not None.  A non-QR continuation page is
# dropped regardless of its title_matched value (FHH or text-title "GUIA DE
# REMISION").  INVERTED from rev-2 which absorbed text-title non-QR pages.
# ---------------------------------------------------------------------------


class TestConditionCContinuationAbsorbed:
    """A text-title continuation page (`title_matched == "GUIA DE REMISION"`,
    `identity is None`) following an ocr_fallback-opened block in the SAME
    registro must be DROPPED under rev-3.

    Rev-3 gate: `absorb = identity is not None`.  Since identity is None for
    the continuation page, absorb=False regardless of title_matched.  The
    open block closes with only the first page in source_pages.

    INVERTED from rev-2 (which absorbed text-title non-QR pages via
    `is_heuristic_only`).  RED against the rev-2 gate; GREEN after replacement.
    """

    def test_text_title_continuation_ocr_anchor_same_registro_dropped(self) -> None:
        """ocr_fallback block + text-title GUIA continuation, same registro → DROPPED (source_pages=[0])."""
        p0 = _RawGuia(
            guia_id="", source_page=0, image=_PNG, lines=[_MAT_LINE], registro="232"
        )
        # Continuation: text title "GUIA DE REMISION", no QR identity, same registro.
        p1 = _RawGuia(
            guia_id="", source_page=1, image=_PNG, lines=[], registro="232"
        )

        classifications = [
            _cls(0, "GUIA DE REMISION"),  # Condition C — text title opens an ocr_fallback block
            _cls(1, "GUIA DE REMISION"),  # Condition C — text-title continuation (identity None)
        ]
        decode_map = {
            0: _decode_no_qr(),
            1: _decode_no_qr(),
        }

        pipeline = _make_pipeline()
        blocks = pipeline._stage_assemble_blocks(
            [p0, p1], classifications, decode_map=decode_map
        )

        # Rev-3: identity is None → absorb=False; p1 NOT appended.
        # The open block for p0 gets finalized with source_pages=[0] only.
        block = next((b for b in blocks if b.registro == "232"), None)
        assert block is not None, "Block for registro 232 must exist (opened by p0)"
        assert block.source_pages == [0], (
            f"text-title non-QR continuation must be DROPPED under rev-3; "
            f"source_pages={block.source_pages}"
        )


# ---------------------------------------------------------------------------
# EXT-S19e — Condition-B page, registro mismatch → NOT absorbed
# ---------------------------------------------------------------------------


class TestEXTS19eRegistroMismatchNotAbsorbed:
    """EXT-S19e: Condition-B page with different registro must not pollute the open block."""

    def test_registro_mismatch_condition_b_not_absorbed(self) -> None:
        """Condition-B page registro='228', current block registro='232' (QR) → NOT absorbed."""
        qr = _identity("T112", "0065421")

        p0 = _RawGuia(guia_id="", source_page=0, image=_PNG, lines=[_MAT_LINE], registro="232")
        p1 = _RawGuia(guia_id="", source_page=1, image=_PNG, lines=[], registro="228")

        classifications = [
            _cls(0, "QR_IDENTITY"),
            _cls(1, "FORMA_HEADER_HEURISTIC"),
        ]
        decode_map = {
            0: _decode_qr(qr),
            1: _decode_no_qr(),
        }

        pipeline = _make_pipeline()
        blocks = pipeline._stage_assemble_blocks(
            [p0, p1], classifications, decode_map=decode_map
        )

        # Registro mismatch triggers start_new_block (section boundary) regardless of gate.
        # So p1 becomes its own block — check that the registro 232 block has ONLY p0.
        block_232 = next((b for b in blocks if b.registro == "232"), None)
        assert block_232 is not None, "Block for registro 232 must exist"
        assert block_232.source_pages == [0], (
            f"registro 232 block must have only p0, not p1 (registro 228): {block_232.source_pages}"
        )

    def test_condition_b_different_registro_does_not_inflate_source_pages(self) -> None:
        """The QR block's source_pages must not include a page from a different registro."""
        qr = _identity("T112", "0065421")

        p0 = _RawGuia(guia_id="", source_page=151, image=_PNG, lines=[_MAT_LINE], registro="232")
        p1 = _RawGuia(guia_id="", source_page=152, image=_PNG, lines=[], registro="228")

        classifications = [
            _cls(151, "QR_IDENTITY"),
            _cls(152, "FORMA_HEADER_HEURISTIC"),
        ]
        decode_map = {
            151: _decode_qr(qr),
            152: _decode_no_qr(),
        }

        pipeline = _make_pipeline()
        blocks = pipeline._stage_assemble_blocks(
            [p0, p1], classifications, decode_map=decode_map
        )

        qr_block = next(b for b in blocks if b.guia_id == "T112-0065421")
        assert 152 not in qr_block.source_pages, (
            f"page 152 (registro 228) must not appear in T112-0065421 source_pages: {qr_block.source_pages}"
        )


# ---------------------------------------------------------------------------
# EXT-S19c — INVERTED (rev-3): T112-0065421 p151 + FHH photo p152 same
# registro → p152 DROPPED; source_pages=[151] only.
#
# Real-data basis (run 67e4e7a1): reg227 QR p151 + 1 FHH photo p152 — p152
# carries 0 material lines.  Under rev-3 absorb = identity is not None → p152
# (identity None) is dropped.  INVERTED from rev-2 which asserted [151, 152].
# ---------------------------------------------------------------------------


class TestEXTS19cGenuineContinuationRegression:
    """EXT-S19c (INVERTED, rev-3): QR p151 + FHH photo p152 same registro.

    Real data shows p152 is a photo (0 lines).  Under the rev-3 gate
    (absorb = identity is not None), p152 is DROPPED → source_pages=[151].

    RED against the rev-2 gate (which kept [151, 152]);
    GREEN after the `absorb = identity is not None` replacement.
    """

    def test_qr_p151_then_fhh_photo_p152_same_registro_photo_dropped(self) -> None:
        """Page 151 QR-opened (T112-0065421), page 152 FHH photo → source_pages=[151] only."""
        qr = _identity("T112", "0065421")

        p151 = _RawGuia(
            guia_id="", source_page=151, image=_PNG, lines=[_MAT_LINE], registro="227"
        )
        # Real data: p152 is a photo / annex — identity None, 0 material lines.
        p152 = _RawGuia(
            guia_id="", source_page=152, image=_PNG, lines=[], registro="227"
        )

        classifications = [
            _cls(151, "QR_IDENTITY"),
            _cls(152, "FORMA_HEADER_HEURISTIC"),
        ]
        decode_map = {
            151: _decode_qr(qr),
            152: _decode_no_qr(),
        }

        pipeline = _make_pipeline()
        blocks = pipeline._stage_assemble_blocks(
            [p151, p152], classifications, decode_map=decode_map
        )

        assert len(blocks) == 1, (
            f"Expected 1 block for T112-0065421; got {len(blocks)}: "
            f"{[b.guia_id for b in blocks]}"
        )
        block = blocks[0]
        assert block.guia_id == "T112-0065421"
        assert block.source_pages == [151], (
            f"FHH photo p152 must be DROPPED under rev-3; source_pages={block.source_pages}"
        )

    def test_qr_block_identity_source_is_qr(self) -> None:
        """The assembled block started by a QR must have identity_source='qr'."""
        qr = _identity("T112", "0065421")

        p0 = _RawGuia(guia_id="", source_page=0, image=_PNG, lines=[_MAT_LINE], registro="232")
        p1 = _RawGuia(guia_id="", source_page=1, image=_PNG, lines=[], registro="232")

        classifications = [_cls(0, "QR_IDENTITY"), _cls(1, "FORMA_HEADER_HEURISTIC")]
        decode_map = {0: _decode_qr(qr), 1: _decode_no_qr()}

        pipeline = _make_pipeline()
        blocks = pipeline._stage_assemble_blocks(
            [p0, p1], classifications, decode_map=decode_map
        )

        assert blocks[0].identity_source == "qr"


# ---------------------------------------------------------------------------
# EXT-S19d — EXT-S24 pin: classifier verdict UNCHANGED (must be GREEN pre-impl)
# ---------------------------------------------------------------------------

_SCANNED_GUIA_TEXT = ""  # empty / noise: Condition B heuristic fires on image_dominant


class TestEXTS19dClassifierVerdictUnchanged:
    """EXT-S19d: PageClassifier must still return GUIA/FORMA_HEADER_HEURISTIC for Condition B.

    This test MUST be GREEN even before the positional gate is implemented —
    it pins that the classifier is NOT changed by this change.
    """

    def test_image_dominant_no_qr_returns_guia_heuristic(self) -> None:
        """Condition B: image_dominant=True, qr_is_guia=False → GUIA / FORMA_HEADER_HEURISTIC."""
        clf = PageClassifier()
        result = clf.classify_page(
            page_index=5,
            page_text=_SCANNED_GUIA_TEXT,
            qr_is_guia=False,
            image_dominant=True,
        )
        assert result.kind == "GUIA"
        assert result.title_matched == "FORMA_HEADER_HEURISTIC"

    def test_no_new_enum_value_introduced(self) -> None:
        """Condition B verdict must be 'GUIA', never 'IGNORED' or any new enum value."""
        clf = PageClassifier()
        result = clf.classify_page(
            page_index=0,
            page_text=None,
            qr_is_guia=False,
            image_dominant=True,
        )
        assert result.kind == "GUIA", (
            f"Condition B must still classify as GUIA, not {result.kind!r}. "
            "No new enum value should be introduced at the classifier level (EXT-S19d)."
        )
        assert result.title_matched == "FORMA_HEADER_HEURISTIC"


# ---------------------------------------------------------------------------
# EXT-S19b — Real-data reg228: QR p98 + several FHH photo pages → photos
# NOT absorbed; source_pages=[98] only (model from run 67e4e7a1).
#
# Real data: reg228 QR p98 + 39 FHH photos pp99-137 (0 lines each).
# Here we use a compact 3-photo model (pp99, p100, p101) sufficient to
# exercise the gate against multiple consecutive non-QR pages.
# ---------------------------------------------------------------------------


class TestEXTS19bRealDataReg228PhotosNotAbsorbed:
    """EXT-S19b: QR p98 (registro 228) + multiple FHH photo pages → photos DROPPED.

    Models run 67e4e7a1 reg228: identity QR p98, followed by 39 FHH photo pages
    pp99-137.  Under rev-3 (absorb = identity is not None), every photo page
    (identity None) is dropped → source_pages=[98] only.

    RED against the rev-2 gate (which absorbed same-registro photos);
    GREEN after `absorb = identity is not None` replacement.
    """

    def test_qr_p98_then_fhh_photos_same_registro_photos_dropped(self) -> None:
        """QR p98 (reg228) + FHH photos pp99-101 → source_pages=[98] only."""
        qr = _identity("T112", "0065900")

        p98 = _RawGuia(
            guia_id="", source_page=98, image=_PNG, lines=[_MAT_LINE], registro="228"
        )
        # FHH photo pages — identity None, 0 material lines each.
        photos = [
            _RawGuia(guia_id="", source_page=p, image=_PNG, lines=[], registro="228")
            for p in (99, 100, 101)
        ]

        classifications = [
            _cls(98, "QR_IDENTITY"),
            *[_cls(p, "FORMA_HEADER_HEURISTIC") for p in (99, 100, 101)],
        ]
        decode_map = {
            98: _decode_qr(qr),
            **{p: _decode_no_qr() for p in (99, 100, 101)},
        }

        pipeline = _make_pipeline()
        blocks = pipeline._stage_assemble_blocks(
            [p98, *photos], classifications, decode_map=decode_map
        )

        assert len(blocks) == 1, (
            f"Expected 1 block for T112-0065900; got {len(blocks)}: "
            f"{[b.guia_id for b in blocks]}"
        )
        block = blocks[0]
        assert block.guia_id == "T112-0065900"
        assert block.source_pages == [98], (
            f"FHH photo pages (pp99-101) must be DROPPED; source_pages={block.source_pages}"
        )


# ---------------------------------------------------------------------------
# EXT-S19f — True multi-QR-page guia: same guia_id on a 2nd QR page → absorbed
# into ONE block.  Domain authority: multi-page guías carry the same QR identity
# on each page; the gate keeps them together.
# ---------------------------------------------------------------------------


class TestEXTS19fTrueMultiQRPageGuiaAbsorbed:
    """EXT-S19f: same guia_id on consecutive QR pages → ONE block (absorbed).

    Under rev-3 (absorb = identity is not None): a 2nd QR page carrying the
    same guia_id falls inside the else-branch (same registro, same guia_id →
    NOT start_new_block) with identity is not None → absorb=True → appended.

    This must be GREEN under both rev-2 and rev-3 (no regression).
    """

    def test_same_guia_id_two_qr_pages_assemble_into_one_block(self) -> None:
        """Two QR pages with the same guia_id → one block, source_pages=[10, 11]."""
        qr = _identity("T112", "0065900")

        p10 = _RawGuia(
            guia_id="", source_page=10, image=_PNG, lines=[_MAT_LINE], registro="230"
        )
        p11 = _RawGuia(
            guia_id="", source_page=11, image=_PNG, lines=[_MAT_LINE], registro="230"
        )

        classifications = [
            _cls(10, "QR_IDENTITY"),
            _cls(11, "QR_IDENTITY"),
        ]
        decode_map = {
            10: _decode_qr(qr),
            11: _decode_qr(qr),  # same identity → same guia_id
        }

        pipeline = _make_pipeline()
        blocks = pipeline._stage_assemble_blocks(
            [p10, p11], classifications, decode_map=decode_map
        )

        assert len(blocks) == 1, (
            f"Same guia_id on 2 QR pages must produce ONE block; got {len(blocks)}: "
            f"{[b.guia_id for b in blocks]}"
        )
        block = blocks[0]
        assert block.guia_id == "T112-0065900"
        assert sorted(block.source_pages) == [10, 11], (
            f"Both QR pages must be in source_pages; got {block.source_pages}"
        )


# ---------------------------------------------------------------------------
# C1 (rev-4) — ocr_fallback material page → own block + requires_review.
#
# Bug (rev-3): a genuine guía page whose QR failed to decode but which carries
# OCR material (identity None, len(lines) > 0, identity_source="ocr_fallback")
# reaches the else-branch when same-registro as an open block and is SILENTLY
# DROPPED — material lost, no block, no requires_review.  Violates the
# validation-gate invariant ("never silently drop; flag requires_review").
#
# Fix (case 3, condition d): a non-QR page WITH material opens its OWN block
# (a distinct ocr_fallback guía), is counted in the registro total, and is
# flagged requires_review (uncertain identity) on its lines.  Case 2 (non-QR
# 0-line FHH photo) is still dropped (Bug-1 fix, unchanged).
# ---------------------------------------------------------------------------


class TestC1OcrFallbackMaterialPageStartsOwnBlock:
    """C1 (rev-4): a non-QR page with material starts its own ocr_fallback block.

    RED against the rev-3 gate (where the same-registro non-QR material page is
    dropped via the else-branch); GREEN after condition (d) is added.
    """

    def test_qr_then_ocr_fallback_material_then_qr_same_registro(self) -> None:
        """[QR A (material), ocr_fallback B (identity None, material, same reg), QR C (material)]
        → B is NOT dropped: it becomes its OWN block, flagged requires_review, and the
        registro total includes A + B + C.
        """
        qr_a = _identity("T112", "0001")
        qr_c = _identity("T112", "0003")

        line_a = _MAT_LINE.model_copy(update={"cantidad": Decimal("100")})
        line_b = _MAT_LINE.model_copy(update={"cantidad": Decimal("150")})
        line_c = _MAT_LINE.model_copy(update={"cantidad": Decimal("150")})

        p_a = _RawGuia(guia_id="", source_page=0, image=_PNG, lines=[line_a], registro="232")
        # B: QR-decode failed (identity None) but OCR read material lines.
        p_b = _RawGuia(guia_id="", source_page=1, image=_PNG, lines=[line_b], registro="232")
        p_c = _RawGuia(guia_id="", source_page=2, image=_PNG, lines=[line_c], registro="232")

        classifications = [
            _cls(0, "QR_IDENTITY"),
            _cls(1, "FORMA_HEADER_HEURISTIC"),  # no QR but carries material
            _cls(2, "QR_IDENTITY"),
        ]
        decode_map = {
            0: _decode_qr(qr_a),
            1: _decode_no_qr(),  # identity None → ocr_fallback
            2: _decode_qr(qr_c),
        }

        pipeline = _make_pipeline()
        blocks = pipeline._stage_assemble_blocks(
            [p_a, p_b, p_c], classifications, decode_map=decode_map
        )

        # Three distinct blocks: A (QR), B (ocr_fallback), C (QR).
        assert len(blocks) == 3, (
            f"Expected 3 blocks (B not dropped); got {len(blocks)}: "
            f"{[(b.guia_id, b.source_pages) for b in blocks]}"
        )

        block_b = next((b for b in blocks if 1 in b.source_pages), None)
        assert block_b is not None, "ocr_fallback page B (material) must start its OWN block"
        assert block_b.identity_source == "ocr_fallback"
        assert block_b.guia_id == "ocr_1"
        assert block_b.source_pages == [1]

        # B's material is NOT lost.
        assert sum(line.cantidad for line in block_b.lines) == Decimal("150"), (
            "B's OCR material must be retained on its own block"
        )

        # B is flagged requires_review (uncertain identity) on its lines.
        assert all(line.requires_review for line in block_b.lines), (
            "ocr_fallback material block lines MUST be flagged requires_review"
        )

        # Registro total includes A + B + C material (no silent loss of B's 150 KG).
        total = sum(line.cantidad for b in blocks for line in b.lines)
        assert total == Decimal("400"), (
            f"Registro total must include A+B+C (400 KG); got {total} — B's material lost"
        )

    def test_qr_pages_do_not_get_requires_review(self) -> None:
        """A QR-identified block's lines are NOT flagged requires_review by this change
        (only ocr_fallback material blocks carry the uncertain-identity flag)."""
        qr_a = _identity("T112", "0001")
        line_a = _MAT_LINE.model_copy(update={"cantidad": Decimal("100"), "requires_review": False})

        p_a = _RawGuia(guia_id="", source_page=0, image=_PNG, lines=[line_a], registro="232")
        classifications = [_cls(0, "QR_IDENTITY")]
        decode_map = {0: _decode_qr(qr_a)}

        pipeline = _make_pipeline()
        blocks = pipeline._stage_assemble_blocks([p_a], classifications, decode_map=decode_map)

        assert len(blocks) == 1
        assert blocks[0].identity_source == "qr"
        assert not any(line.requires_review for line in blocks[0].lines), (
            "QR block lines must NOT be flagged requires_review by the ocr_fallback rule"
        )

    def test_zero_line_photo_still_dropped(self) -> None:
        """Case 2 invariant (Bug-1, unchanged): a non-QR 0-line photo same registro is
        STILL dropped (condition d requires len(lines) > 0)."""
        qr = _identity("T112", "0065900")

        p98 = _RawGuia(guia_id="", source_page=98, image=_PNG, lines=[_MAT_LINE], registro="228")
        # 0-line FHH photo (run 67e4e7a1 reg228 model).
        photo = _RawGuia(guia_id="", source_page=99, image=_PNG, lines=[], registro="228")

        classifications = [
            _cls(98, "QR_IDENTITY"),
            _cls(99, "FORMA_HEADER_HEURISTIC"),
        ]
        decode_map = {98: _decode_qr(qr), 99: _decode_no_qr()}

        pipeline = _make_pipeline()
        blocks = pipeline._stage_assemble_blocks(
            [p98, photo], classifications, decode_map=decode_map
        )

        assert len(blocks) == 1, (
            f"0-line photo must NOT create a block; got {len(blocks)}: "
            f"{[(b.guia_id, b.source_pages) for b in blocks]}"
        )
        assert blocks[0].source_pages == [98], (
            f"0-line FHH photo must still be DROPPED; source_pages={blocks[0].source_pages}"
        )
