"""Unit tests for the positional gate in _stage_assemble_blocks (EXT-019 rev-2).

Tests EXT-S19a, EXT-S19c (regression guard), EXT-S19d (EXT-S24 pin), EXT-S19e.

Strategy: call _stage_assemble_blocks directly on a minimal pipeline instance,
injecting pre-built _RawGuia and PageClassification lists so we can control
title_matched="FORMA_HEADER_HEURISTIC" precisely without a full pipeline run.

STRICT TDD: tests A-1 (EXT-S19a, EXT-S19c, EXT-S19e) MUST be RED before the
positional gate is wired. Test A-4 (EXT-S19d) MUST be GREEN (classifier untouched).
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
        """Condition-B raw page, no preceding current_block → NOT absorbed, block NOT created.

        Before the gate: the else-branch appends unconditionally, but asserts
        current_block is not None (assert would trip or the page starts a new block).
        Actually, start_new_block=True at run-start, so page 0 always starts a block.
        The interesting case is: p0 is Condition A (QR), p1 is Condition B, p2 is
        Condition B in a DIFFERENT registro — p2 must NOT be absorbed into p0's block.
        """
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

        # p1 has registro="228" vs current_block registro="232" → registro mismatch also
        # triggers start_new_block before the positional gate even fires.
        # Let's use a scenario with SAME registro but NO preceding QR block:
        # p0 starts a block (ocr_fallback), p1 is Condition B same registro → currently absorbed.
        # With the gate: p1 should NOT be absorbed (no QR anchor).
        p0_ocr = _RawGuia(guia_id="", source_page=0, image=_PNG, lines=[_MAT_LINE], registro="232")
        p1_heur = _RawGuia(guia_id="", source_page=1, image=_PNG, lines=[], registro="232")

        decode_map2 = {
            0: _decode_no_qr(),
            1: _decode_no_qr(),
        }
        blocks2 = pipeline._stage_assemble_blocks(
            [p0_ocr, p1_heur], classifications, decode_map=decode_map2
        )

        # p0 opens a block with identity_source="ocr_fallback"
        # p1 is FORMA_HEADER_HEURISTIC, same registro — gate MUST prevent absorption
        # because current_block.identity_source != "qr"
        assert len(blocks2) == 1, (
            f"Expected 1 block (p1 dropped by gate); got {len(blocks2)}"
        )
        block = blocks2[0]
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
# EXT-S19c — Regression guard: genuine continuation still assembles correctly
# ---------------------------------------------------------------------------


class TestEXTS19cGenuineContinuationRegression:
    """EXT-S19c: QR p151 + no-QR Condition-B p152 same registro → ONE block.

    This is the PRIMARY regression guard. The gate must NOT over-drop.
    MUST FAIL before the positional gate is wired (currently absorbs unconditionally
    in the else-branch with no check, so the test actually PASSES on the current code
    because the unconditional absorption DOES produce ONE block — but the gate-off
    behaviour is: any no-QR page same registro gets absorbed, even non-guías.

    After the gate: same behaviour for genuine continuation (QR anchor + same registro).
    The test pins this invariant so any regression is caught immediately.
    """

    def test_qr_p151_then_condition_b_p152_same_registro_one_block(self) -> None:
        """Page 151 QR-opened, page 152 Condition B same registro → source_pages=[151,152]."""
        qr = _identity("T112", "0065421")

        p151 = _RawGuia(
            guia_id="", source_page=151, image=_PNG, lines=[_MAT_LINE], registro="232"
        )
        p152 = _RawGuia(
            guia_id="", source_page=152, image=_PNG, lines=[], registro="232"
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
            f"Expected 1 block (genuine continuation); got {len(blocks)}: "
            f"{[b.guia_id for b in blocks]}"
        )
        block = blocks[0]
        assert block.guia_id == "T112-0065421"
        assert sorted(block.source_pages) == [151, 152], (
            f"source_pages must be [151, 152]: {block.source_pages}"
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
