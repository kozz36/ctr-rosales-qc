"""Unit tests for rev-3 hybrid classifier + decode_identities pre-pass (R1.8).

Covers EXT-S23/24/25/29 and D2 regression scenarios:
  EXT-S23: scanned guía page (empty digital text, qr_is_guia=True) → GUIA (QR_IDENTITY)
  EXT-S24: scanned guía page (empty text, image_dominant=True) → GUIA (FORMA_HEADER_HEURISTIC)
  EXT-S25: declared page with >=200 chars MUST NOT be stolen by QR or heuristic
  EXT-S29: first_page sentinel — None default; 0 is valid concrete index
  D2 regression: dual-QR returns both compact identity AND hashqr_url
  decode_identities pre-pass: render cache populated; qr_is_guia propagated
  image_dominant: PdfStructureAdapter.image_coverage_ratio coverage threshold
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from reconciliation.application.config import AppConfig
from reconciliation.application.pipeline import (
    DecodeOutcome,
    ReconciliationPipeline,
    _get_image_dominant,
    _QR_DPI,
)
from reconciliation.domain.classifier import (
    IMAGE_DOMINANT_THRESHOLD,
    PageClassifier,
    _FORMA_HEADER_MAX_CHARS,
)
from reconciliation.domain.models import GuiaDeRemision, GuiaIdentity, MaterialLine, VisionResult
from reconciliation.application.run_context import RunContext


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_UNIVERSAL_HEADER = (
    "PTR001-TORRE ROSALES\n"
    "Informe de detalle del formulario\n"
    "Created by Sandra Sopla Pinedo with Autodesk® Forma® on May 31, 2026 at 11:56 AM UTC-05:00\n"
    "Page 3 of 493\n"
)

# This is < 200 chars after noise-stripping (the body is empty)
_SCANNED_GUIA_TEXT = _UNIVERSAL_HEADER

# Declared page with real text (> 200 chars)
_DECLARED_TEXT = (
    _UNIVERSAL_HEADER
    + "PROTOCOLO DE RECEPCION DE MATERIALES\n"
    + "Numero 232\n"
    + "Fecha de declaracion 2026-05-01\n"
    + "BARRA CORRUGADA 1/2 PULG × 9M\n"
    + "Cantidad: 1250.00 KG\n"
    + "Esta es informacion de detalle con mas de doscientos caracteres para asegurar\n"
    + "que la condicion de guarda contra falsos positivos del clasificador funcione.\n"
)

_VALID_IDENTITY = GuiaIdentity(
    serie="T009",
    numero="0741770",
    ruc_emisor="20370146994",
    ruc_receptor="20613231871",
    tipo="09",
    confidence=1.0,
    hashqr_url=None,
)

_HASHQR_URL = "https://e-factura.sunat.gob.pe/v1/contribuyente/gre/comprobantes/descargaqr?hashqr=XYZ"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tiny_png() -> bytes:
    """Return a minimal 10×10 white PNG (avoids large deps)."""
    from PIL import Image  # noqa: PLC0415

    img = Image.new("RGB", (10, 10), color=(255, 255, 255))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class FakeDocWithCoverage:
    """Fake DocumentSourcePort with configurable image_coverage_ratio."""

    def __init__(self, pages: list[dict[str, Any]], coverage: dict[int, float] | None = None) -> None:
        self._pages = pages
        self._coverage = coverage or {}

    def page_count(self) -> int:
        return len(self._pages)

    def render_page(self, idx: int, dpi: int = 200) -> bytes:
        return self._pages[idx].get("image", _make_tiny_png())

    def page_text(self, idx: int) -> str | None:
        return self._pages[idx].get("text")

    def image_coverage_ratio(self, idx: int) -> float:
        return self._coverage.get(idx, 0.0)


class FakeDocWithoutCoverage:
    """Fake DocumentSourcePort WITHOUT image_coverage_ratio (legacy/test fake)."""

    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self._pages = pages

    def page_count(self) -> int:
        return len(self._pages)

    def render_page(self, idx: int, dpi: int = 200) -> bytes:
        return self._pages[idx].get("image", _make_tiny_png())

    def page_text(self, idx: int) -> str | None:
        return self._pages[idx].get("text")


class FakeVision:
    supports_batch: bool = False

    def read_handwritten_date(self, image: bytes, hint: str | None = None) -> VisionResult:
        return VisionResult(date=date(2026, 5, 28), confidence=0.99, raw="28/05/2026")

    def read_handwritten_date_batch(self, images: list[bytes]) -> list[VisionResult]:  # pragma: no cover
        return [self.read_handwritten_date(img) for img in images]


class FakeOCR:
    def extract_declared(self, text: str) -> list[MaterialLine]:
        return []

    def extract_printed_table(self, image: bytes) -> list[MaterialLine]:
        return []


class FakeIdentity:
    """Returns a fixed identity for any page."""

    def __init__(self, result: GuiaIdentity | None) -> None:
        self._result = result

    def decode_identity(self, image: bytes, page_idx: int | None = None) -> GuiaIdentity | None:
        return self._result


def _build_pipeline(
    doc: Any,
    identity: Any | None = None,
    page_to_registro: dict[int, str | None] | None = None,
) -> ReconciliationPipeline:
    return ReconciliationPipeline(
        doc_source=doc,
        extractor=FakeOCR(),
        vision=FakeVision(),
        config=AppConfig(),
        page_to_registro=page_to_registro or {},
        identity=identity,
    )


# ---------------------------------------------------------------------------
# EXT-S23: scanned guía page + qr_is_guia=True → GUIA (QR_IDENTITY)
# ---------------------------------------------------------------------------


class TestEXTS23ScannedGuiaQrIdentity:
    """Scanned page (empty digital text) + QR decode succeeded → GUIA (QR_IDENTITY)."""

    def test_classify_page_with_qr_is_guia_returns_guia_qr_identity(self) -> None:
        clf = PageClassifier()
        result = clf.classify_page(
            page_index=5,
            page_text=_SCANNED_GUIA_TEXT,
            qr_is_guia=True,
            image_dominant=False,
        )
        assert result.kind == "GUIA"
        assert result.title_matched == "QR_IDENTITY"
        assert result.page == 5

    def test_classify_with_empty_text_and_qr_is_guia_returns_guia(self) -> None:
        clf = PageClassifier()
        result = clf.classify(
            page_text=None,
            qr_is_guia=True,
            image_dominant=False,
        )
        assert result.kind == "GUIA"
        assert result.title_matched == "QR_IDENTITY"

    def test_pipeline_produces_guia_for_qr_decoded_scanned_page(
        self, tmp_path: Path
    ) -> None:
        """End-to-end: pipeline classifies a scanned page as GUIA when identity returns non-None."""
        doc = FakeDocWithCoverage(
            pages=[
                {"text": _SCANNED_GUIA_TEXT},  # page 0: scanned guía
            ],
            coverage={0: 0.95},
        )
        identity_adapter = FakeIdentity(_VALID_IDENTITY)
        pipeline = _build_pipeline(doc, identity=identity_adapter, page_to_registro={0: "232"})
        ctx = RunContext(pdf_path=Path("fake.pdf"), output_base=tmp_path)
        result = pipeline.run(ctx)

        guia_pages = [c for c in result.classifications if c.kind == "GUIA"]
        assert len(guia_pages) == 1
        assert guia_pages[0].title_matched in ("QR_IDENTITY", "FORMA_HEADER_HEURISTIC")


# ---------------------------------------------------------------------------
# EXT-S24: scanned page + image_dominant=True (no QR) → GUIA (FORMA_HEADER_HEURISTIC)
# ---------------------------------------------------------------------------


class TestEXTS24ScannedGuiaHeuristic:
    """Scanned page, image_dominant=True, no QR → GUIA via Condition B heuristic."""

    def test_classify_page_image_dominant_no_qr_returns_guia_heuristic(self) -> None:
        clf = PageClassifier()
        result = clf.classify_page(
            page_index=7,
            page_text=_SCANNED_GUIA_TEXT,
            qr_is_guia=False,
            image_dominant=True,
        )
        assert result.kind == "GUIA"
        assert result.title_matched == "FORMA_HEADER_HEURISTIC"
        assert result.page == 7

    def test_classify_with_none_text_and_image_dominant_returns_guia_heuristic(self) -> None:
        clf = PageClassifier()
        result = clf.classify(
            page_text=None,
            qr_is_guia=False,
            image_dominant=True,
        )
        assert result.kind == "GUIA"
        assert result.title_matched == "FORMA_HEADER_HEURISTIC"

    def test_pipeline_with_image_dominant_no_identity_classifies_as_guia(
        self, tmp_path: Path
    ) -> None:
        """Pipeline uses image_coverage_ratio to derive image_dominant for Condition B."""
        doc = FakeDocWithCoverage(
            pages=[{"text": _SCANNED_GUIA_TEXT}],
            coverage={0: 0.95},  # above IMAGE_DOMINANT_THRESHOLD
        )
        pipeline = _build_pipeline(doc, identity=None, page_to_registro={0: "232"})
        ctx = RunContext(pdf_path=Path("fake.pdf"), output_base=tmp_path)
        result = pipeline.run(ctx)

        assert len(result.classifications) == 1
        assert result.classifications[0].kind == "GUIA"
        assert result.classifications[0].title_matched == "FORMA_HEADER_HEURISTIC"

    def test_below_threshold_not_image_dominant(self) -> None:
        """Coverage below IMAGE_DOMINANT_THRESHOLD → image_dominant=False."""
        doc = FakeDocWithCoverage(
            pages=[{"text": _SCANNED_GUIA_TEXT}],
            coverage={0: IMAGE_DOMINANT_THRESHOLD - 0.1},
        )
        result = _get_image_dominant(doc, 0)
        assert result is False

    def test_at_threshold_is_image_dominant(self) -> None:
        """Coverage at IMAGE_DOMINANT_THRESHOLD → image_dominant=True."""
        doc = FakeDocWithCoverage(
            pages=[{"text": _SCANNED_GUIA_TEXT}],
            coverage={0: IMAGE_DOMINANT_THRESHOLD},
        )
        result = _get_image_dominant(doc, 0)
        assert result is True

    def test_missing_method_returns_false(self) -> None:
        """FakeDoc without image_coverage_ratio → image_dominant=False (graceful)."""
        doc = FakeDocWithoutCoverage(pages=[{"text": _SCANNED_GUIA_TEXT}])
        result = _get_image_dominant(doc, 0)
        assert result is False


# ---------------------------------------------------------------------------
# EXT-S25: declared page MUST NOT be misclassified by QR or heuristic
# ---------------------------------------------------------------------------


class TestEXTS25DeclaredPageNeverStolenByHybrid:
    """Declared/protocolo pages with substantial text MUST win over QR and heuristic (EXT-S25).

    The guard is the declared-title-first ordering in classify(): protocolo check
    runs before Condition A (qr_is_guia) and Condition B (image_dominant).
    Additionally, a page with >= 200 cleaned chars NEVER reaches Condition B.
    """

    def test_protocolo_page_with_qr_is_guia_still_declared(self) -> None:
        clf = PageClassifier()
        # Even if qr_is_guia is True, a protocolo page must win.
        result = clf.classify(
            page_text=_DECLARED_TEXT,
            qr_is_guia=True,
            image_dominant=True,
        )
        assert result.kind == "DECLARED"
        assert result.title_matched == "PROTOCOLO DE RECEPCION"

    def test_declared_page_with_image_dominant_still_declared(self) -> None:
        clf = PageClassifier()
        # A Form Detail page with lots of text (>200 chars) + image_dominant → DECLARED.
        detail_text = (
            _UNIVERSAL_HEADER
            + "Form detail\n"
            + "#4252: CTR-PLC01-FR001_RECEPCION DE MATERIALES EN OBRA\n"
            + "DESCRIPTION: BARRA CORRUGADA 1/2 PULG × 9M\n"
            + "NOTES: Material de acero para construccion; proviene de Aceros Arequipa SA.\n"
            + "Cantidad declarada: 1250.00 KG; tolerance EXACT 0.\n"
            + "Additional detail that pushes the body well beyond 200 characters total.\n"
        )
        result = clf.classify(
            page_text=detail_text,
            qr_is_guia=False,
            image_dominant=True,
        )
        # Even with image_dominant, a Form Detail page → DECLARED
        assert result.kind == "DECLARED"

    def test_condition_b_does_not_fire_when_body_exceeds_threshold(self) -> None:
        """Condition B guard: cleaned body > _FORMA_HEADER_MAX_CHARS → NOT heuristic."""
        # Build a page with just enough real content to exceed the char threshold
        # but NO recognized title (no PROTOCOLO, GUIA, FORM DETAIL, etc.)
        # This simulates a page that is image-dominant but has substantial text.
        many_chars = "X" * (_FORMA_HEADER_MAX_CHARS + 1)
        result = PageClassifier().classify(
            page_text=_UNIVERSAL_HEADER + many_chars + "\n",
            qr_is_guia=False,
            image_dominant=True,
        )
        # Body exceeds threshold → Condition B does NOT fire → UNCLASSIFIED
        assert result.kind == "UNCLASSIFIED"
        assert result.title_matched is None


# ---------------------------------------------------------------------------
# EXT-S29: first_page sentinel — None is default; 0 is a valid page index
# ---------------------------------------------------------------------------


class TestEXTS29FirstPageSentinel:
    """Rev-3 D6: GuiaDeRemision.first_page is int|None; default is None."""

    def test_default_first_page_is_none(self) -> None:
        guia = GuiaDeRemision(
            guia_id="T009-0001",
            registro="232",
            fecha=date(2026, 5, 28),
            lines=[],
            source_pages=[5, 6],
        )
        assert guia.first_page is None

    def test_explicit_zero_is_valid_page_index(self) -> None:
        guia = GuiaDeRemision(
            guia_id="T009-0001",
            registro="232",
            fecha=date(2026, 5, 28),
            lines=[],
            source_pages=[0, 1],
            first_page=0,
        )
        assert guia.first_page == 0  # Not None — page 0 is real

    def test_pipeline_sets_first_page_from_block_first_page(
        self, tmp_path: Path
    ) -> None:
        """Pipeline propagates first_page from _GuiaBlock.first_page to GuiaDeRemision."""
        doc = FakeDocWithCoverage(
            pages=[{"text": _SCANNED_GUIA_TEXT}],
            coverage={0: 0.95},
        )
        pipeline = _build_pipeline(
            doc, identity=FakeIdentity(_VALID_IDENTITY), page_to_registro={0: "232"}
        )
        ctx = RunContext(pdf_path=Path("fake.pdf"), output_base=tmp_path)
        result = pipeline.run(ctx)

        assert len(result.guias) == 1
        assert result.guias[0].first_page == 0  # page 0 is the real first page

    def test_first_page_none_not_zero_for_unknown_origin(self) -> None:
        """GuiaDeRemision constructed without first_page has None, not 0."""
        guia = GuiaDeRemision(
            guia_id="unknown",
            registro=None,
            fecha=None,
            lines=[],
            source_pages=[],
        )
        assert guia.first_page is None
        # Confirm is-not-None check works correctly
        assert not (guia.first_page is not None)


# ---------------------------------------------------------------------------
# D2 regression: dual-QR returns both compact identity AND hashqr_url
# ---------------------------------------------------------------------------


class TestD2DualQRRegression:
    """Rev-3 D2: multi-resolution COLOR decode finds BOTH compact QR AND URL QR."""

    def test_decode_identity_returns_hashqr_url_alongside_identity(self) -> None:
        """When _decode_multi_res returns both payloads, hashqr_url is set on identity."""
        from reconciliation.adapters.identity.qr_barcode import QrBarcodeExtractionAdapter

        adapter = QrBarcodeExtractionAdapter()
        VALID_PAYLOAD = "20370146994|09|T009|0741770|6|20613231871"

        with patch.object(adapter, "_decode_multi_res", return_value=[VALID_PAYLOAD, _HASHQR_URL]):
            result = adapter.decode_identity(_make_tiny_png())

        assert result is not None
        assert result.guia_id == "T009-0741770"
        assert result.hashqr_url == _HASHQR_URL

    def test_decode_identity_url_only_returns_none_ocr_fallback(self) -> None:
        """URL-only decode (no compact QR) → None → OCR fallback path (Risk-3)."""
        from reconciliation.adapters.identity.qr_barcode import QrBarcodeExtractionAdapter

        adapter = QrBarcodeExtractionAdapter()
        with patch.object(adapter, "_decode_multi_res", return_value=[_HASHQR_URL]):
            result = adapter.decode_identity(_make_tiny_png())

        assert result is None

    def test_decode_hashqr_url_extracts_url_only(self) -> None:
        """decode_hashqr_url returns URL payload without requiring compact QR."""
        from reconciliation.adapters.identity.qr_barcode import QrBarcodeExtractionAdapter

        adapter = QrBarcodeExtractionAdapter()
        VALID_PAYLOAD = "20370146994|09|T009|0741770|6|20613231871"
        with patch.object(adapter, "_decode_multi_res", return_value=[VALID_PAYLOAD, _HASHQR_URL]):
            url = adapter.decode_hashqr_url(_make_tiny_png())

        assert url == _HASHQR_URL

    def test_decode_hashqr_url_returns_none_when_no_url_qr(self) -> None:
        """No URL QR decoded → decode_hashqr_url returns None."""
        from reconciliation.adapters.identity.qr_barcode import QrBarcodeExtractionAdapter

        VALID_PAYLOAD = "20370146994|09|T009|0741770|6|20613231871"
        adapter = QrBarcodeExtractionAdapter()
        with patch.object(adapter, "_decode_multi_res", return_value=[VALID_PAYLOAD]):
            url = adapter.decode_hashqr_url(_make_tiny_png())

        assert url is None


# ---------------------------------------------------------------------------
# decode_identities pre-pass: render cache populated; qr_is_guia propagated
# ---------------------------------------------------------------------------


class TestDecodeIdentitiesPrePass:
    """_stage_decode_identities builds the cache; classify consumes it without re-scanning."""

    def test_decode_outcome_qr_is_guia_property(self) -> None:
        """DecodeOutcome.qr_is_guia is True only when identity is set."""
        outcome_with_identity = DecodeOutcome(
            identity=_VALID_IDENTITY,
            hashqr_url=None,
            rendered=b"PNG",
            decoded=True,
        )
        outcome_without_identity = DecodeOutcome(
            identity=None,
            hashqr_url=None,
            rendered=b"PNG",
            decoded=False,
        )
        assert outcome_with_identity.qr_is_guia is True
        assert outcome_without_identity.qr_is_guia is False

    def test_decode_map_populated_for_every_page(self, tmp_path: Path) -> None:
        """_stage_decode_identities populates an entry for every page index."""
        n_pages = 3
        doc = FakeDocWithCoverage(
            pages=[{"text": _SCANNED_GUIA_TEXT}] * n_pages,
            coverage={i: 0.9 for i in range(n_pages)},
        )
        pipeline = _build_pipeline(doc, identity=FakeIdentity(_VALID_IDENTITY))
        decode_map = pipeline._stage_decode_identities(n_pages)

        assert len(decode_map) == n_pages
        for idx in range(n_pages):
            assert idx in decode_map
            assert decode_map[idx].rendered  # bytes populated

    def test_decode_map_qr_is_guia_true_when_identity_returns_non_none(
        self, tmp_path: Path
    ) -> None:
        doc = FakeDocWithCoverage(
            pages=[{"text": _SCANNED_GUIA_TEXT}],
            coverage={0: 0.9},
        )
        pipeline = _build_pipeline(doc, identity=FakeIdentity(_VALID_IDENTITY))
        decode_map = pipeline._stage_decode_identities(1)

        assert decode_map[0].qr_is_guia is True
        assert decode_map[0].identity is not None

    def test_decode_map_qr_is_guia_false_when_identity_returns_none(
        self, tmp_path: Path
    ) -> None:
        doc = FakeDocWithCoverage(
            pages=[{"text": _SCANNED_GUIA_TEXT}],
            coverage={0: 0.9},
        )
        pipeline = _build_pipeline(doc, identity=FakeIdentity(None))
        decode_map = pipeline._stage_decode_identities(1)

        assert decode_map[0].qr_is_guia is False

    def test_render_not_called_twice_for_guia_page(self, tmp_path: Path) -> None:
        """EXT-019 render-cache: extract_ocr reuses cached bytes, no second render."""
        doc = FakeDocWithCoverage(
            pages=[{"text": _SCANNED_GUIA_TEXT}],
            coverage={0: 0.95},
        )
        render_calls: list[int] = []
        original_render = doc.render_page

        def counting_render(idx: int, dpi: int = 200) -> bytes:
            render_calls.append(idx)
            return original_render(idx, dpi)

        doc.render_page = counting_render  # type: ignore[method-assign]

        pipeline = _build_pipeline(
            doc, identity=FakeIdentity(_VALID_IDENTITY), page_to_registro={0: "232"}
        )
        ctx = RunContext(pdf_path=Path("fake.pdf"), output_base=tmp_path)
        pipeline.run(ctx)

        # Page 0 should be rendered exactly once (during decode_identities).
        # extract_ocr must reuse the cached bytes.
        assert render_calls.count(0) == 1
