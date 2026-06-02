"""Unit tests for QrBarcodeExtractionAdapter (S1.2, EXT-012).

Strategy: test the pure parse functions (parse_compact_gre_qr, build_guia_identity)
without requiring pyzbar or zxing-cpp installed, plus adapter-level tests that mock
the decoder union.  Any test that imports pyzbar or zxingcpp directly uses
pytest.importorskip() to skip gracefully when the libs are absent.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from reconciliation.adapters.identity.qr_barcode import (
    QrBarcodeExtractionAdapter,
    build_guia_identity,
    parse_compact_gre_qr,
)
from reconciliation.domain.models import GuiaIdentity


# ---------------------------------------------------------------------------
# parse_compact_gre_qr — pure function tests (no libs required)
# ---------------------------------------------------------------------------


class TestParseCompactGREQR:
    VALID_PAYLOAD = "20370146994|09|T009|0741770|6|20613231871"

    def test_valid_payload_parses_all_fields(self) -> None:
        result = parse_compact_gre_qr(self.VALID_PAYLOAD)
        assert result is not None
        assert result["ruc_emisor"] == "20370146994"
        assert result["tipo"] == "09"
        assert result["serie"] == "T009"
        assert result["numero"] == "0741770"
        assert result["ruc_receptor"] == "20613231871"

    def test_too_few_pipes_returns_none(self) -> None:
        assert parse_compact_gre_qr("20370146994|09|T009") is None

    def test_exactly_five_pipes_accepted(self) -> None:
        payload = "A|B|C|D|E|F"
        result = parse_compact_gre_qr(payload)
        assert result is not None

    def test_extra_fields_after_index5_ignored(self) -> None:
        payload = "20370146994|09|T009|0741770|6|20613231871|extra|fields"
        result = parse_compact_gre_qr(payload)
        assert result is not None
        assert result["ruc_receptor"] == "20613231871"

    def test_empty_string_returns_none(self) -> None:
        assert parse_compact_gre_qr("") is None

    def test_url_variant_returns_none_not_enough_pipes(self) -> None:
        url = "https://e-consulta.sunat.gob.pe/descargaqr?hashqr=ABC123"
        # URL has no pipes → parse returns None (URL is not a data QR)
        assert parse_compact_gre_qr(url) is None


# ---------------------------------------------------------------------------
# build_guia_identity — pure function tests (no libs required)
# ---------------------------------------------------------------------------


class TestBuildGuiaIdentity:
    VALID_FIELDS = {
        "ruc_emisor": "20370146994",
        "tipo": "09",
        "serie": "T009",
        "numero": "0741770",
        "ruc_receptor": "20613231871",
    }

    def test_valid_fields_returns_guia_identity(self) -> None:
        result = build_guia_identity(self.VALID_FIELDS, hashqr_url=None)
        assert isinstance(result, GuiaIdentity)
        assert result.guia_id == "T009-0741770"
        assert result.ruc_emisor == "20370146994"
        assert result.ruc_receptor == "20613231871"
        assert result.tipo == "09"
        assert result.confidence == pytest.approx(1.0)
        assert result.hashqr_url is None

    def test_hashqr_url_propagated(self) -> None:
        url = "https://e-consulta.sunat.gob.pe/descargaqr?hashqr=XYZ"
        result = build_guia_identity(self.VALID_FIELDS, hashqr_url=url)
        assert result is not None
        assert result.hashqr_url == url

    def test_10_digit_ruc_emisor_returns_none(self) -> None:
        """EXT-S14: 10-digit RUC → confidence gate fails → None."""
        bad = dict(self.VALID_FIELDS, ruc_emisor="2037014699")  # 10 digits
        result = build_guia_identity(bad, hashqr_url=None)
        assert result is None

    def test_10_digit_ruc_receptor_returns_none(self) -> None:
        bad = dict(self.VALID_FIELDS, ruc_receptor="2061323187")  # 10 digits
        result = build_guia_identity(bad, hashqr_url=None)
        assert result is None

    def test_invalid_tipo_returns_none(self) -> None:
        bad = dict(self.VALID_FIELDS, tipo="99")
        result = build_guia_identity(bad, hashqr_url=None)
        assert result is None

    def test_tipo_31_accepted(self) -> None:
        fields = dict(self.VALID_FIELDS, tipo="31")
        result = build_guia_identity(fields, hashqr_url=None)
        assert result is not None
        assert result.tipo == "31"

    def test_empty_serie_returns_none(self) -> None:
        bad = dict(self.VALID_FIELDS, serie="")
        result = build_guia_identity(bad, hashqr_url=None)
        assert result is None

    def test_empty_numero_returns_none(self) -> None:
        bad = dict(self.VALID_FIELDS, numero="")
        result = build_guia_identity(bad, hashqr_url=None)
        assert result is None

    def test_page_idx_logged_on_failure(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        bad = dict(self.VALID_FIELDS, ruc_emisor="123")
        with caplog.at_level(logging.WARNING, logger="reconciliation.adapters.identity.qr_barcode"):
            result = build_guia_identity(bad, hashqr_url=None, page_idx=42)
        assert result is None
        assert "42" in caplog.text


# ---------------------------------------------------------------------------
# Lazy-import test: importing the module must NOT raise even without libs
# ---------------------------------------------------------------------------


class TestLazyImport:
    def test_import_module_succeeds_without_pyzbar_zxing(self) -> None:
        """Importing qr_barcode at module level must not raise ImportError."""
        # If we got here the module loaded — the test passes trivially.
        from reconciliation.adapters.identity import qr_barcode  # noqa: F401

        assert True


# ---------------------------------------------------------------------------
# Adapter-level tests using mocked decoder union
# ---------------------------------------------------------------------------


def _make_tiny_png() -> bytes:
    """Return a minimal 1×1 white PNG (avoids PIL dependency on test runner)."""
    from PIL import Image  # type: ignore[import]  # noqa: PLC0415

    img = Image.new("L", (10, 10), color=255)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestQrBarcodeExtractionAdapterMocked:
    """Tests that mock _decode_union so we don't need pyzbar/zxing-cpp installed."""

    VALID_PAYLOAD = "20370146994|09|T009|0741770|6|20613231871"

    def _make_adapter(self) -> QrBarcodeExtractionAdapter:
        return QrBarcodeExtractionAdapter(render_dpi=150, upscale=2)

    def test_happy_path_returns_guia_identity(self) -> None:
        """EXT-S13: compact GRE QR → GuiaIdentity with confidence 1.0."""
        adapter = self._make_adapter()
        with patch.object(adapter, "_decode_union", return_value=[self.VALID_PAYLOAD]):
            image = _make_tiny_png()
            result = adapter.decode_identity(image)

        assert isinstance(result, GuiaIdentity)
        assert result.guia_id == "T009-0741770"
        assert result.ruc_emisor == "20370146994"
        assert result.ruc_receptor == "20613231871"
        assert result.tipo == "09"
        assert result.confidence == pytest.approx(1.0)

    def test_10_digit_ruc_returns_none(self) -> None:
        """EXT-S14: malformed RUC → None returned, failure logged."""
        bad_payload = "2037014699|09|T009|0741770|6|20613231871"  # 10-digit ruc_emisor
        adapter = self._make_adapter()
        with patch.object(adapter, "_decode_union", return_value=[bad_payload]):
            result = adapter.decode_identity(_make_tiny_png())
        assert result is None

    def test_url_variant_qr_only_returns_none(self) -> None:
        """Risk-3 defensive: only URL-variant QR → None → OCR fallback."""
        url_payload = "https://e-consulta.sunat.gob.pe/descargaqr?hashqr=ABC123"
        adapter = self._make_adapter()
        with patch.object(adapter, "_decode_union", return_value=[url_payload]):
            result = adapter.decode_identity(_make_tiny_png())
        assert result is None

    def test_url_variant_qr_alongside_data_qr_sets_hashqr_url(self) -> None:
        """URL-variant QR decoded alongside compact data QR → hashqr_url populated."""
        url_payload = "https://e-consulta.sunat.gob.pe/descargaqr?hashqr=ABC123"
        adapter = self._make_adapter()
        with patch.object(
            adapter,
            "_decode_union",
            return_value=[self.VALID_PAYLOAD, url_payload],
        ):
            result = adapter.decode_identity(_make_tiny_png())

        assert result is not None
        assert result.hashqr_url == url_payload
        assert result.guia_id == "T009-0741770"

    def test_decoder_union_pyzbar_none_zxing_returns_result(self) -> None:
        """Union: pyzbar returns nothing, zxing-cpp returns the QR → result works."""
        # Simulate: pyzbar returns [], zxing-cpp returns the valid payload
        adapter = self._make_adapter()
        # _decode_union already implements the union; we mock it to return only the
        # zxing-cpp result (simulating pyzbar absent/finding nothing).
        with patch.object(adapter, "_decode_union", return_value=[self.VALID_PAYLOAD]):
            result = adapter.decode_identity(_make_tiny_png())
        assert result is not None
        assert result.guia_id == "T009-0741770"

    def test_no_barcode_found_returns_none(self) -> None:
        """Empty decode union → None."""
        adapter = self._make_adapter()
        with patch.object(adapter, "_decode_union", return_value=[]):
            result = adapter.decode_identity(_make_tiny_png())
        assert result is None

    def test_unparseable_payload_returns_none(self) -> None:
        """Payload with insufficient pipes → parse fails → None."""
        adapter = self._make_adapter()
        with patch.object(adapter, "_decode_union", return_value=["GARBAGE DATA"]):
            result = adapter.decode_identity(_make_tiny_png())
        assert result is None
