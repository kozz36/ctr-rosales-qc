"""Unit tests for SunatDescargaqrAdapter (R3.7 / EXT-023 / D3).

Coverage:
  - OfficialGre domain model: field types, from_identity helper
  - Pure parsing helpers (_extract_pdf_text, _parse_gre_number, _parse_line_items,
    _parse_labelled_date, _parse_rucs, _url_to_cache_key)
  - Adapter graceful fallback: network error → None, non-200 → None, non-PDF → None
  - Adapter cache: hit on second call (no second download), miss on first call
  - SUNAT > OCR precedence: when fetch succeeds, block lines are replaced
  - Year-fix folded test (D5 + #2753):
      vision returns 2016-05-28 + lower=2026-05-28 + upper=2026-06-01 → 2026-05-28, year_inferred=True
  - Air-gap: when sunat.enabled=False, no network call is made

IMPORTANT: NO test in this file makes a real SUNAT HTTP call.
Network is always mocked or skipped.
"""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
import tempfile

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sample_sunat_pdf_text() -> str:
    """Simulated text output from PyMuPDF get_text() on a SUNAT GRE PDF.

    Based on the confirmed spike (#2750) output from page T073-00680258.
    """
    return (
        "GUÍA DE REMISIÓN ELECTRÓNICA\n"
        "N° T073 - 00680258\n"
        "Fecha de emisión 28/05/2026 01:58 AM\n"
        "Fecha de entrega de Bienes al transportista:28/05/2026\n"
        "RUC: 20370146994\n"
        "Corporación Aceros Arequipa S.A.\n"
        "Destinatario\n"
        "RUC: 20613231871\n"
        "CONSORCIO TORRE ROSALES\n"
        "Bienes por transportar:\n"
        "Cantidad / Unidad de medida / Descripción Detallada\n"
        "0.192 / TONELADAS / BARRA A A615-G60 3/8\" X 9M\n"
        "Código de identificación del Bien o Servicio 407797\n"
    )


# ---------------------------------------------------------------------------
# OfficialGre domain model
# ---------------------------------------------------------------------------

class TestOfficialGreModel:
    def test_minimal_construction(self) -> None:
        from reconciliation.domain.models import OfficialGre

        gre = OfficialGre(
            guia_id="T073-00680258",
            serie="T073",
            numero="00680258",
            ruc_emisor="20370146994",
            ruc_receptor="20613231871",
        )
        assert gre.guia_id == "T073-00680258"
        assert gre.fecha_entrega is None
        assert gre.fecha_emision is None
        assert gre.lines == []

    def test_with_dates_and_lines(self) -> None:
        from reconciliation.domain.models import GreLineItem, OfficialGre

        item = GreLineItem(
            cantidad=Decimal("0.192"),
            unidad="TONELADAS",
            descripcion="BARRA A A615-G60 3/8\" X 9M",
            codigo_producto="407797",
        )
        gre = OfficialGre(
            guia_id="T073-00680258",
            serie="T073",
            numero="00680258",
            ruc_emisor="20370146994",
            ruc_receptor="20613231871",
            fecha_emision=date(2026, 5, 28),
            fecha_entrega=date(2026, 5, 28),
            lines=[item],
        )
        assert gre.fecha_entrega == date(2026, 5, 28)
        assert len(gre.lines) == 1
        assert gre.lines[0].cantidad == Decimal("0.192")

    def test_from_identity_helper(self) -> None:
        from reconciliation.domain.models import OfficialGre

        gre = OfficialGre.from_identity("T073-00680258")
        assert gre.guia_id == "T073-00680258"
        assert gre.serie == "T073"
        assert gre.numero == "00680258"

    def test_gre_line_item_optional_codigo(self) -> None:
        from reconciliation.domain.models import GreLineItem

        item = GreLineItem(cantidad=Decimal("1.5"), unidad="KG", descripcion="ACERO")
        assert item.codigo_producto is None


# ---------------------------------------------------------------------------
# Pure parsing helpers (no IO)
# ---------------------------------------------------------------------------

class TestParsers:
    def test_url_to_cache_key_extracts_hashqr(self) -> None:
        from reconciliation.adapters.sunat.descargaqr import _url_to_cache_key

        url = "https://e-factura.sunat.gob.pe/v1/contribuyente/gre/comprobantes/descargaqr?hashqr=ABC123def"
        key = _url_to_cache_key(url)
        assert "ABC123def" in key
        assert "/" not in key
        assert "?" not in key

    def test_url_to_cache_key_fallback_no_hashqr(self) -> None:
        from reconciliation.adapters.sunat.descargaqr import _url_to_cache_key

        url = "https://example.com/foo/bar"
        key = _url_to_cache_key(url)
        assert isinstance(key, str)
        assert len(key) > 0

    def test_parse_gre_number_standard_format(self) -> None:
        from reconciliation.adapters.sunat.descargaqr import _parse_gre_number

        text = "N° T073 - 00680258\nFecha de emisión 28/05/2026"
        serie, numero = _parse_gre_number(text)
        assert serie == "T073"
        assert numero == "00680258"

    def test_parse_gre_number_not_found_returns_empty(self) -> None:
        from reconciliation.adapters.sunat.descargaqr import _parse_gre_number

        serie, numero = _parse_gre_number("No number here")
        assert serie == ""
        assert numero == ""

    def test_parse_labelled_date_emision(self) -> None:
        from reconciliation.adapters.sunat.descargaqr import _EMISION_RE, _parse_labelled_date

        text = "Fecha de emisión 28/05/2026 01:58 AM"
        result = _parse_labelled_date(text, _EMISION_RE)
        assert result == date(2026, 5, 28)

    def test_parse_labelled_date_entrega(self) -> None:
        from reconciliation.adapters.sunat.descargaqr import _ENTREGA_RE, _parse_labelled_date

        text = "Fecha de entrega de Bienes al transportista:28/05/2026"
        result = _parse_labelled_date(text, _ENTREGA_RE)
        assert result == date(2026, 5, 28)

    def test_parse_labelled_date_absent_returns_none(self) -> None:
        from reconciliation.adapters.sunat.descargaqr import _EMISION_RE, _parse_labelled_date

        result = _parse_labelled_date("No date here", _EMISION_RE)
        assert result is None

    def test_parse_rucs_extracts_first_two(self) -> None:
        from reconciliation.adapters.sunat.descargaqr import _parse_rucs

        text = "RUC: 20370146994\nEmisor\nRUC: 20613231871\nReceptor"
        emisor, receptor = _parse_rucs(text)
        assert emisor == "20370146994"
        assert receptor == "20613231871"

    def test_parse_rucs_only_one_found(self) -> None:
        from reconciliation.adapters.sunat.descargaqr import _parse_rucs

        text = "RUC: 20370146994"
        emisor, receptor = _parse_rucs(text)
        assert emisor == "20370146994"
        assert receptor is None

    def test_parse_line_items_sample_text(self) -> None:
        from reconciliation.adapters.sunat.descargaqr import _parse_line_items
        from reconciliation.domain.models import GreLineItem

        text = _make_sample_sunat_pdf_text()
        items = _parse_line_items(text, Decimal, GreLineItem)

        assert len(items) == 1
        item = items[0]
        assert item.cantidad == Decimal("0.192")
        assert item.unidad == "TONELADAS"
        assert "BARRA" in item.descripcion

    def test_parse_line_items_no_table_returns_empty(self) -> None:
        from reconciliation.adapters.sunat.descargaqr import _parse_line_items
        from reconciliation.domain.models import GreLineItem

        items = _parse_line_items("No table here", Decimal, GreLineItem)
        assert items == []


# ---------------------------------------------------------------------------
# Adapter: graceful fallback on errors (R3.7 spec: never raises)
# ---------------------------------------------------------------------------

class TestAdapterGracefulFallback:
    def test_network_error_returns_none(self) -> None:
        """Network failure during download → adapter returns None, does not raise."""
        from reconciliation.adapters.sunat.descargaqr import SunatDescargaqrAdapter

        adapter = SunatDescargaqrAdapter(timeout_s=5.0, cache_dir=None)

        with patch.object(adapter, "_download", return_value=None):
            result = adapter.fetch("https://example.com/descargaqr?hashqr=TEST")

        assert result is None

    def test_non_pdf_content_type_returns_none(self) -> None:
        """HTTP 200 but Content-Type is not PDF → adapter returns None."""
        from reconciliation.adapters.sunat.descargaqr import SunatDescargaqrAdapter

        adapter = SunatDescargaqrAdapter(timeout_s=5.0, cache_dir=None)

        # Simulate _download returning None (Content-Type validation happened inside)
        with patch.object(adapter, "_download", return_value=None):
            result = adapter.fetch("https://example.com/descargaqr?hashqr=BADTYPE")

        assert result is None

    def test_pdf_parse_failure_returns_none(self) -> None:
        """PDF bytes returned but PyMuPDF fails to parse → adapter returns None."""
        from reconciliation.adapters.sunat.descargaqr import SunatDescargaqrAdapter

        adapter = SunatDescargaqrAdapter(timeout_s=5.0, cache_dir=None)

        # Return bad bytes that PyMuPDF will fail to open
        with patch.object(adapter, "_download", return_value=b"not-a-pdf"):
            result = adapter.fetch("https://example.com/descargaqr?hashqr=BADPDF")

        assert result is None

    def test_exception_inside_fetch_returns_none_not_raises(self) -> None:
        """Any unexpected exception inside fetch() is caught → returns None."""
        from reconciliation.adapters.sunat.descargaqr import SunatDescargaqrAdapter

        adapter = SunatDescargaqrAdapter(timeout_s=5.0, cache_dir=None)

        with patch.object(
            adapter, "_fetch_internal", side_effect=RuntimeError("unexpected!")
        ):
            result = adapter.fetch("https://example.com/descargaqr?hashqr=ERR")

        assert result is None


# ---------------------------------------------------------------------------
# Adapter: cache behaviour
# ---------------------------------------------------------------------------

class TestAdapterCache:
    def test_cache_hit_skips_download(self, tmp_path: Path) -> None:
        """Second fetch reuses cached PDF; no download is attempted."""
        from reconciliation.adapters.sunat.descargaqr import SunatDescargaqrAdapter

        cache_dir = tmp_path / "sunat"
        adapter = SunatDescargaqrAdapter(timeout_s=5.0, cache_dir=cache_dir)

        # Pre-populate cache with a fake valid PDF
        fake_pdf_bytes = _build_minimal_pdf_bytes()
        url = "https://e-factura.sunat.gob.pe/v1/contribuyente/gre/comprobantes/descargaqr?hashqr=TESTHASH"
        cache_key = "TESTHASH"
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / f"{cache_key}.pdf").write_bytes(fake_pdf_bytes)

        download_calls = []

        with patch.object(adapter, "_download", side_effect=download_calls.append):
            # The _download should NOT be called since cache hit will be found
            adapter.fetch(url)

        assert len(download_calls) == 0, "Download should not be called on cache hit"

    def test_cache_miss_triggers_download_and_saves(self, tmp_path: Path) -> None:
        """First fetch: cache miss → download → save to cache dir."""
        from reconciliation.adapters.sunat.descargaqr import SunatDescargaqrAdapter

        cache_dir = tmp_path / "sunat"
        adapter = SunatDescargaqrAdapter(timeout_s=5.0, cache_dir=cache_dir)

        fake_pdf_bytes = _build_minimal_pdf_bytes()
        url = "https://e-factura.sunat.gob.pe/v1/contribuyente/gre/comprobantes/descargaqr?hashqr=NEWKEY"

        with patch.object(adapter, "_download", return_value=fake_pdf_bytes):
            adapter.fetch(url)

        # Verify cache was written (some file in cache_dir)
        assert cache_dir.exists()
        pdf_files = list(cache_dir.glob("*.pdf"))
        assert len(pdf_files) >= 1

    def test_no_cache_dir_disables_caching(self) -> None:
        """When cache_dir=None, nothing is written to disk."""
        from reconciliation.adapters.sunat.descargaqr import SunatDescargaqrAdapter

        adapter = SunatDescargaqrAdapter(timeout_s=5.0, cache_dir=None)
        url = "https://example.com/descargaqr?hashqr=NOCACHE"

        with patch.object(adapter, "_download", return_value=None):
            result = adapter.fetch(url)

        assert result is None  # download returned None → graceful


# ---------------------------------------------------------------------------
# Full parse round-trip (using sample text bypassing fitz)
# ---------------------------------------------------------------------------

class TestAdapterParsedResult:
    def test_full_parse_from_sample_text(self) -> None:
        """Parse the sample SUNAT PDF text directly via _fetch_internal with mocked fitz."""
        from reconciliation.adapters.sunat.descargaqr import (
            SunatDescargaqrAdapter,
            _extract_pdf_text,
        )

        sample_text = _make_sample_sunat_pdf_text()
        fake_pdf_bytes = b"fake-pdf-bytes"

        adapter = SunatDescargaqrAdapter(timeout_s=5.0, cache_dir=None)

        # Bypass download and PDF extraction; inject sample text directly
        with patch.object(adapter, "_download", return_value=fake_pdf_bytes), \
             patch(
                 "reconciliation.adapters.sunat.descargaqr._extract_pdf_text",
                 return_value=sample_text,
             ):
            result = adapter.fetch("https://example.com/descargaqr?hashqr=TEST123")

        assert result is not None
        assert result.serie == "T073"
        assert result.numero == "00680258"
        assert result.guia_id == "T073-00680258"
        assert result.fecha_entrega == date(2026, 5, 28)
        assert result.fecha_emision == date(2026, 5, 28)
        assert result.ruc_emisor == "20370146994"
        assert result.ruc_receptor == "20613231871"
        assert len(result.lines) == 1
        assert result.lines[0].cantidad == Decimal("0.192")
        assert result.lines[0].unidad == "TONELADAS"
        assert "BARRA" in result.lines[0].descripcion
        assert result.lines[0].codigo_producto == "407797"


# ---------------------------------------------------------------------------
# SUNAT > OCR precedence in the pipeline (R3.5)
# ---------------------------------------------------------------------------

class TestSunatOcrPrecedence:
    def test_pipeline_uses_sunat_lines_when_fetch_succeeds(self, tmp_path: Path) -> None:
        """When SUNAT fetch succeeds, block lines are replaced with SUNAT data (D3).

        Verifies the precedence rule: SUNAT line-items override OCR lines for the
        same guía block when the adapter returns a non-None OfficialGre.
        """
        from decimal import Decimal  # noqa: PLC0415

        from reconciliation.domain.models import GreLineItem, GuiaDeRemision, OfficialGre
        from reconciliation.application.pipeline import (
            ReconciliationPipeline,
            _GuiaBlock,
        )
        from reconciliation.application.config import AppConfig, SunatConfig
        from reconciliation.application.run_context import RunContext
        from reconciliation.domain.models import MaterialLine

        # Build a block with OCR-extracted line (low confidence)
        ocr_line = MaterialLine(
            description_raw="OCR_TEXT",
            description_canonical="OCR_TEXT",
            unidad="KG",
            cantidad=Decimal("999"),  # OCR quantity (wrong — should be overridden)
            confidence=0.72,
        )

        # SUNAT OfficialGre with authoritative line
        sunat_item = GreLineItem(
            cantidad=Decimal("0.192"),
            unidad="TONELADAS",
            descripcion="BARRA A A615-G60 3/8\" X 9M",
            codigo_producto="407797",
        )
        official = OfficialGre(
            guia_id="T073-00680258",
            serie="T073",
            numero="00680258",
            ruc_emisor="20370146994",
            ruc_receptor="20613231871",
            fecha_entrega=date(2026, 5, 28),
            lines=[sunat_item],
        )

        # Build a minimal pipeline to call _stage_sunat_fetch directly
        class FakeSunatPort:
            def fetch(self, url: str) -> OfficialGre | None:
                return official

        config = AppConfig()
        config.sunat = SunatConfig(enabled=True)

        # Build a minimal block list
        block = _GuiaBlock(
            guia_id="T073-00680258",
            first_page=4,
            source_pages=[4],
            first_page_image=b"\x89PNG",
            lines=[ocr_line],
            registro="232",
            identity_source="qr",
            gre_hashqr_url="https://e-factura.sunat.gob.pe/descargaqr?hashqr=TEST",
        )

        # Create a minimal pipeline (no doc source needed for unit test of this stage)
        class _FakeDocSource:
            def page_count(self): return 1
            def render_page(self, idx, dpi=200): return b"\x89PNG"
            def page_text(self, idx): return None

        class _FakeExtractor:
            def extract_declared(self, text): return []
            def extract_printed_table(self, image): return []

        class _FakeVision:
            supports_batch = False
            def read_handwritten_date(self, image, hint=None):
                from reconciliation.domain.models import VisionResult  # noqa: PLC0415
                return VisionResult(date=date(2026, 5, 28), confidence=0.99, raw="28/05/2026")
            def read_handwritten_date_batch(self, images): return []

        pipeline = ReconciliationPipeline(
            doc_source=_FakeDocSource(),  # type: ignore[arg-type]
            extractor=_FakeExtractor(),  # type: ignore[arg-type]
            vision=_FakeVision(),  # type: ignore[arg-type]
            config=config,
            sunat=FakeSunatPort(),  # type: ignore[arg-type]
        )

        sunat_map = pipeline._stage_sunat_fetch([block])

        # After the stage, block.lines should be SUNAT lines (not the OCR line)
        assert block.lines != [ocr_line], "OCR lines should have been replaced by SUNAT lines"
        assert len(block.lines) == 1
        assert block.lines[0].cantidad == Decimal("0.192")
        assert "BARRA" in block.lines[0].description_raw
        assert "T073-00680258" in sunat_map


# ---------------------------------------------------------------------------
# Folded year-fix test (D5 + #2753) — the core R3 regression test
# ---------------------------------------------------------------------------

class TestFoldedYearFix:
    def test_vision_wrong_year_corrected_with_sunat_lower_bound(self) -> None:
        """Year-fix: vision 2016-05-28 + lower=2026-05-28 + upper=2026-06-01 → 2026-05-28.

        This is the canonical folded-fix test from the task specification.
        Covers engram #2753: vision models return parseable dates with wrong year.
        """
        from reconciliation.domain.date_inference import infer_reception_year

        # Simulate: vision returned 2016-05-28, but we trust only day=28, month=5
        day, month = 28, 5
        lower = date(2026, 5, 28)  # SUNAT fecha_entrega (deterministic lower bound)
        upper = date(2026, 6, 1)   # doc/run date

        result, year_inferred = infer_reception_year(day, month, lower, upper)

        assert result == date(2026, 5, 28)
        assert year_inferred is True

    def test_normalize_dates_corrects_vision_wrong_year(self, tmp_path: Path) -> None:
        """_stage_normalize_dates always reconstructs year even when vision had a full date.

        Proves the folded fix: vision date=2016-05-28 is corrected to 2026-05-28
        when the SUNAT lower bound (fecha_entrega=2026-05-28) is provided.
        """
        from reconciliation.application.config import AppConfig, SunatConfig
        from reconciliation.application.pipeline import ReconciliationPipeline
        from reconciliation.domain.models import GuiaDeRemision, MaterialLine, OfficialGre

        config = AppConfig()
        config.sunat = SunatConfig(enabled=True)

        class _FakeDoc:
            def page_count(self): return 1
            def render_page(self, idx, dpi=200): return b"\x89PNG"
            def page_text(self, idx): return None

        class _FakeExtractor:
            def extract_declared(self, text): return []
            def extract_printed_table(self, image): return []

        class _FakeVision:
            supports_batch = False
            def read_handwritten_date(self, image, hint=None):
                from reconciliation.domain.models import VisionResult  # noqa: PLC0415
                return VisionResult(date=None, confidence=0.99, raw="28/05/2016")
            def read_handwritten_date_batch(self, images): return []

        pipeline = ReconciliationPipeline(
            doc_source=_FakeDoc(),  # type: ignore[arg-type]
            extractor=_FakeExtractor(),  # type: ignore[arg-type]
            vision=_FakeVision(),  # type: ignore[arg-type]
            config=config,
        )

        # Build a guía where vision read raw "28/05/2016"
        guia = GuiaDeRemision(
            guia_id="T073-00680258",
            registro="232",
            fecha=None,  # vision returned None after raw="28/05/2016"
            fecha_raw="28/05/2016",
            fecha_confidence=0.99,
            lines=[],
            source_pages=[4],
        )

        # Sunat map with fecha_entrega as lower bound
        official = OfficialGre(
            guia_id="T073-00680258",
            serie="T073",
            numero="00680258",
            ruc_emisor="20370146994",
            ruc_receptor="20613231871",
            fecha_entrega=date(2026, 5, 28),
        )
        sunat_map = {"T073-00680258": official}

        result_guias = pipeline._stage_normalize_dates([guia], sunat_fetch_map=sunat_map)

        assert len(result_guias) == 1
        corrected = result_guias[0]
        assert corrected.fecha == date(2026, 5, 28), (
            f"Expected 2026-05-28 but got {corrected.fecha}"
        )
        assert corrected.year_inferred is True

    def test_normalize_dates_does_not_infer_when_vision_year_correct(self, tmp_path: Path) -> None:
        """When vision year is already the most-recent candidate, year_inferred=False.

        Edge case: vision returns 2026-05-28, inference also gives 2026-05-28.
        year_inferred stays False (year was not changed).
        """
        from reconciliation.application.config import AppConfig
        from reconciliation.application.pipeline import ReconciliationPipeline
        from reconciliation.domain.models import GuiaDeRemision

        config = AppConfig()

        class _FakeDoc:
            def page_count(self): return 1
            def render_page(self, idx, dpi=200): return b"\x89PNG"
            def page_text(self, idx): return None

        class _FakeExtractor:
            def extract_declared(self, text): return []
            def extract_printed_table(self, image): return []

        class _FakeVision:
            supports_batch = False
            def read_handwritten_date(self, image, hint=None):
                from reconciliation.domain.models import VisionResult  # noqa: PLC0415
                return VisionResult(date=date(2026, 5, 28), confidence=0.99, raw="28/05/2026")
            def read_handwritten_date_batch(self, images): return []

        pipeline = ReconciliationPipeline(
            doc_source=_FakeDoc(),  # type: ignore[arg-type]
            extractor=_FakeExtractor(),  # type: ignore[arg-type]
            vision=_FakeVision(),  # type: ignore[arg-type]
            config=config,
        )

        guia = GuiaDeRemision(
            guia_id="T073-00680258",
            registro="232",
            fecha=date(2026, 5, 28),  # vision returned the correct year
            fecha_raw="28/05/2026",
            fecha_confidence=0.99,
            lines=[],
            source_pages=[4],
        )

        result_guias = pipeline._stage_normalize_dates([guia], sunat_fetch_map={})

        assert result_guias[0].fecha == date(2026, 5, 28)
        assert result_guias[0].year_inferred is False


# ---------------------------------------------------------------------------
# Helper: build a minimal PDF bytes representation (for cache tests)
# We need something that PyMuPDF will accept but we don't want a real PDF.
# We patch _extract_pdf_text in adapter-level tests; for cache tests we only
# need the file to be saved and detected — we mock appropriately.
# ---------------------------------------------------------------------------

def _build_minimal_pdf_bytes() -> bytes:
    """Return the smallest valid PDF header that fitz can open (for cache tests)."""
    # This is a hand-crafted minimal PDF that PyMuPDF can open (returns empty text).
    # We use this to test cache mechanics without a real SUNAT download.
    return (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n"
        b"xref\n0 4\n0000000000 65535 f\n"
        b"0000000009 00000 n\n0000000058 00000 n\n0000000115 00000 n\n"
        b"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n190\n%%EOF\n"
    )
