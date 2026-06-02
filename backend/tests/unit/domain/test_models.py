"""Unit tests for domain value objects (task 1.1).

Covers: model instantiation, Decimal precision, None confidence allowed,
rev-2 new models (GuiaIdentity, GuiaContribution), GuiaDeRemision rev-2 fields,
ReconciliationRow.guias default.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from reconciliation.domain.models import (
    GuiaContribution,
    GuiaDeRemision,
    GuiaIdentity,
    MaterialLine,
    PageClassification,
    ReconciliationRow,
    Registro,
    VisionResult,
)


class TestMaterialLine:
    def test_basic_instantiation(self) -> None:
        line = MaterialLine(
            description_raw="BARRA CORRUGADA 1/2",
            description_canonical="barra corrugada 1/2",
            unidad="KG",
            cantidad=Decimal("1250.00"),
        )
        assert line.unidad == "KG"
        assert line.cantidad == Decimal("1250.00")

    def test_confidence_optional(self) -> None:
        line = MaterialLine(
            description_raw="X",
            description_canonical="x",
            unidad="TN",
            cantidad=Decimal("1.0"),
            confidence=None,
        )
        assert line.confidence is None

    def test_confidence_float(self) -> None:
        line = MaterialLine(
            description_raw="X",
            description_canonical="x",
            unidad="KG",
            cantidad=Decimal("5.0"),
            confidence=0.95,
        )
        assert line.confidence == pytest.approx(0.95)

    def test_decimal_precision_preserved(self) -> None:
        line = MaterialLine(
            description_raw="ALAMBRE",
            description_canonical="alambre",
            unidad="RD",
            cantidad=Decimal("123.456789"),
        )
        assert line.cantidad == Decimal("123.456789")

    def test_source_page_optional(self) -> None:
        line = MaterialLine(
            description_raw="X",
            description_canonical="x",
            unidad="Rollo",
            cantidad=Decimal("10"),
            source_page=None,
        )
        assert line.source_page is None

    def test_valid_units(self) -> None:
        for unit in ("KG", "TN", "RD", "Rollo"):
            line = MaterialLine(
                description_raw="X",
                description_canonical="x",
                unidad=unit,  # type: ignore[arg-type]
                cantidad=Decimal("1"),
            )
            assert line.unidad == unit

    def test_invalid_unit_raises(self) -> None:
        with pytest.raises(Exception):
            MaterialLine(
                description_raw="X",
                description_canonical="x",
                unidad="LB",  # type: ignore[arg-type]
                cantidad=Decimal("1"),
            )


class TestGuiaDeRemision:
    def test_instantiation(self) -> None:
        guia = GuiaDeRemision(
            guia_id="G-001",
            registro="4252",
            fecha=date(2025, 3, 15),
            lines=[],
            source_pages=[10, 11],
        )
        assert guia.guia_id == "G-001"
        assert guia.fecha == date(2025, 3, 15)

    def test_registro_optional(self) -> None:
        guia = GuiaDeRemision(
            guia_id="G-002",
            registro=None,
            fecha=None,
            lines=[],
            source_pages=[],
        )
        assert guia.registro is None
        assert guia.fecha is None

    def test_fecha_confidence_optional(self) -> None:
        guia = GuiaDeRemision(
            guia_id="G-003",
            registro="4252",
            fecha=date(2025, 1, 1),
            fecha_confidence=None,
            lines=[],
            source_pages=[1],
        )
        assert guia.fecha_confidence is None


class TestRegistro:
    def test_instantiation(self) -> None:
        reg = Registro(
            numero="4252",
            fecha_declarada=date(2025, 3, 15),
            declared_lines=[],
        )
        assert reg.numero == "4252"

    def test_fecha_optional(self) -> None:
        reg = Registro(
            numero="4252",
            fecha_declarada=None,
            declared_lines=[],
        )
        assert reg.fecha_declarada is None


class TestPageClassification:
    def test_instantiation_guia(self) -> None:
        pc = PageClassification(
            page=5,
            kind="GUIA",
            title_matched="GUÍA DE REMISIÓN",
            confidence=0.99,
        )
        assert pc.kind == "GUIA"
        assert pc.page == 5

    def test_unclassified(self) -> None:
        pc = PageClassification(
            page=0,
            kind="UNCLASSIFIED",
            title_matched=None,
            confidence=0.30,
        )
        assert pc.title_matched is None


class TestReconciliationRow:
    def test_match_row(self) -> None:
        row = ReconciliationRow(
            registro="4252",
            fecha=date(2025, 3, 15),
            material_canonical="barra corrugada 1/2",
            unidad="KG",
            declared_qty=Decimal("1250.00"),
            summed_qty=Decimal("1250.00"),
            delta=Decimal("0"),
            status="MATCH",
            source_pages=[10, 11],
            min_confidence=0.88,
        )
        assert row.status == "MATCH"
        assert row.delta == Decimal("0")

    def test_mismatch_row(self) -> None:
        row = ReconciliationRow(
            registro="4252",
            fecha=None,
            material_canonical="alambre n16",
            unidad="KG",
            declared_qty=Decimal("800.0"),
            summed_qty=Decimal("810.0"),
            delta=Decimal("10.0"),
            status="MISMATCH",
            source_pages=[],
            min_confidence=None,
        )
        assert row.status == "MISMATCH"
        assert row.min_confidence is None

    def test_declared_missing_status(self) -> None:
        row = ReconciliationRow(
            registro="4252",
            fecha=None,
            material_canonical="unknown material",
            unidad="KG",
            declared_qty=Decimal("0"),
            summed_qty=Decimal("500"),
            delta=Decimal("500"),
            status="DECLARED_MISSING",
            source_pages=[20],
        )
        assert row.status == "DECLARED_MISSING"

    def test_guia_missing_status(self) -> None:
        row = ReconciliationRow(
            registro="4252",
            fecha=None,
            material_canonical="barra 3/8",
            unidad="KG",
            declared_qty=Decimal("800"),
            summed_qty=Decimal("0"),
            delta=Decimal("-800"),
            status="GUIA_MISSING",
            source_pages=[],
        )
        assert row.status == "GUIA_MISSING"


class TestVisionResult:
    def test_with_date(self) -> None:
        vr = VisionResult(date=date(2025, 3, 15), confidence=0.92, raw="15/03/2025")
        assert vr.date == date(2025, 3, 15)

    def test_null_date(self) -> None:
        vr = VisionResult(date=None, confidence=0.0, raw="")
        assert vr.date is None
        assert vr.confidence == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Rev-2 new models (S1.1)
# ---------------------------------------------------------------------------


class TestGuiaIdentity:
    """Tests for GuiaIdentity (EXT-011)."""

    def test_instantiation_all_fields(self) -> None:
        gi = GuiaIdentity(
            serie="T009",
            numero="0741770",
            ruc_emisor="20370146994",
            ruc_receptor="20613231871",
            tipo="09",
            hashqr_url=None,
            confidence=1.0,
        )
        assert gi.serie == "T009"
        assert gi.numero == "0741770"
        assert gi.ruc_emisor == "20370146994"
        assert gi.confidence == pytest.approx(1.0)

    def test_guia_id_computed_from_serie_numero(self) -> None:
        gi = GuiaIdentity(
            serie="T009",
            numero="0741770",
            ruc_emisor="20370146994",
            ruc_receptor="20613231871",
            tipo="09",
            confidence=1.0,
        )
        assert gi.guia_id == "T009-0741770"

    def test_hashqr_url_optional_defaults_none(self) -> None:
        gi = GuiaIdentity(
            serie="T009",
            numero="0741770",
            ruc_emisor="20370146994",
            ruc_receptor="20613231871",
            tipo="09",
            confidence=1.0,
        )
        assert gi.hashqr_url is None

    def test_hashqr_url_populated(self) -> None:
        url = "https://e-consulta.sunat.gob.pe/descargaqr?hashqr=ABC123"
        gi = GuiaIdentity(
            serie="T009",
            numero="0741770",
            ruc_emisor="20370146994",
            ruc_receptor="20613231871",
            tipo="09",
            hashqr_url=url,
            confidence=1.0,
        )
        assert gi.hashqr_url == url

    def test_tipo_31_valid(self) -> None:
        gi = GuiaIdentity(
            serie="V001",
            numero="0000001",
            ruc_emisor="20370146994",
            ruc_receptor="20613231871",
            tipo="31",
            confidence=1.0,
        )
        assert gi.tipo == "31"


class TestGuiaContribution:
    """Tests for GuiaContribution (REC-C02 / design §D)."""

    def test_instantiation_all_fields(self) -> None:
        gc = GuiaContribution(
            guia_id="T009-0741770",
            source_pages=[47, 48],
            cantidad=Decimal("1250.00"),
            unidad="KG",
            confidence=1.0,
            identity_source="qr",
        )
        assert gc.guia_id == "T009-0741770"
        assert gc.source_pages == [47, 48]
        assert gc.cantidad == Decimal("1250.00")
        assert gc.unidad == "KG"
        assert gc.identity_source == "qr"

    def test_ocr_fallback_source(self) -> None:
        gc = GuiaContribution(
            guia_id="guia_fallback",
            source_pages=[10],
            cantidad=Decimal("500"),
            unidad="TN",
            confidence=0.80,
            identity_source="ocr_fallback",
        )
        assert gc.identity_source == "ocr_fallback"

    def test_unidad_preserved_exactly(self) -> None:
        """Units MUST be preserved as-is — never converted (domain invariant)."""
        for unit in ("KG", "TN", "RD", "Rollo"):
            gc = GuiaContribution(
                guia_id="X-1",
                source_pages=[1],
                cantidad=Decimal("1"),
                unidad=unit,
                confidence=1.0,
                identity_source="qr",
            )
            assert gc.unidad == unit


class TestGuiaDeRemisionRev2Fields:
    """GuiaDeRemision rev-2 identity fields default safely (EXT-015 / design §7)."""

    def test_existing_construction_still_works(self) -> None:
        """Rev-2 fields default so existing call sites are not broken."""
        guia = GuiaDeRemision(
            guia_id="G-001",
            registro="232",
            fecha=date(2025, 3, 15),
            lines=[],
            source_pages=[10, 11],
        )
        assert guia.ruc_emisor is None
        assert guia.ruc_receptor is None
        assert guia.tipo is None
        assert guia.gre_hashqr_url is None
        assert guia.identity_confidence == pytest.approx(0.0)
        assert guia.identity_source == "ocr_fallback"
        # Rev-3 D6: first_page default changed from 0 to None (sentinel = "unknown")
        assert guia.first_page is None

    def test_rev2_fields_populated(self) -> None:
        guia = GuiaDeRemision(
            guia_id="T009-0741770",
            registro="232",
            fecha=date(2025, 3, 15),
            lines=[],
            source_pages=[47, 48],
            ruc_emisor="20370146994",
            ruc_receptor="20613231871",
            tipo="09",
            gre_hashqr_url=None,
            identity_confidence=1.0,
            identity_source="qr",
            first_page=47,
        )
        assert guia.ruc_emisor == "20370146994"
        assert guia.identity_source == "qr"
        assert guia.first_page == 47


class TestReconciliationRowGuias:
    """ReconciliationRow.guias defaults to empty list (rev-2 / design §D)."""

    def test_guias_defaults_empty(self) -> None:
        row = ReconciliationRow(
            registro="232",
            fecha=date(2025, 3, 15),
            material_canonical="barra corrugada 1/2",
            unidad="KG",
            declared_qty=Decimal("1250.00"),
            summed_qty=Decimal("1250.00"),
            delta=Decimal("0"),
            status="MATCH",
            source_pages=[10, 11],
        )
        assert row.guias == []

    def test_guias_populated(self) -> None:
        gc = GuiaContribution(
            guia_id="T009-0741770",
            source_pages=[10],
            cantidad=Decimal("1250.00"),
            unidad="KG",
            confidence=1.0,
            identity_source="qr",
        )
        row = ReconciliationRow(
            registro="232",
            fecha=date(2025, 3, 15),
            material_canonical="barra corrugada 1/2",
            unidad="KG",
            declared_qty=Decimal("1250.00"),
            summed_qty=Decimal("1250.00"),
            delta=Decimal("0"),
            status="MATCH",
            source_pages=[10],
            guias=[gc],
        )
        assert len(row.guias) == 1
        assert row.guias[0].guia_id == "T009-0741770"


# ---------------------------------------------------------------------------
# R8.4: match_method field on MaterialLine and ReconciliationRow (MAT-008)
# ---------------------------------------------------------------------------


class TestMaterialLineMatchMethod:
    def test_default_match_method_is_deterministic(self) -> None:
        line = MaterialLine(
            description_raw="BARRA A615 G60 1/2\"",
            description_canonical="barra a615 g60 1/2\"",
            unidad="TN",
            cantidad=Decimal("1.0"),
        )
        assert line.match_method == "deterministic"

    def test_match_method_llm_inferred_stored(self) -> None:
        line = MaterialLine(
            description_raw="X",
            description_canonical="x",
            unidad="KG",
            cantidad=Decimal("1.0"),
            match_method="llm_inferred",
        )
        assert line.match_method == "llm_inferred"

    def test_backward_compat_model_validate_no_match_method(self) -> None:
        """Old serialised dict without match_method key → defaults to deterministic."""
        data = {
            "description_raw": "BARRA",
            "description_canonical": "barra",
            "unidad": "KG",
            "cantidad": "1.0",
        }
        line = MaterialLine.model_validate(data)
        assert line.match_method == "deterministic"


class TestReconciliationRowMatchMethod:
    def test_default_match_method_is_deterministic(self) -> None:
        row = ReconciliationRow(
            registro="232",
            fecha=None,
            material_canonical="BARRA A615 G60 1/2\" 9M",
            unidad="TN",
            declared_qty=Decimal("4.124"),
            delta=Decimal("0"),
            status="MATCH",
            source_pages=[5],
        )
        assert row.match_method == "deterministic"

    def test_match_method_llm_inferred_stored(self) -> None:
        row = ReconciliationRow(
            registro="232",
            fecha=None,
            material_canonical="some material",
            unidad="TN",
            declared_qty=Decimal("1.0"),
            delta=Decimal("0"),
            status="MATCH",
            source_pages=[],
            match_method="llm_inferred",
        )
        assert row.match_method == "llm_inferred"

    def test_backward_compat_model_validate_no_match_method(self) -> None:
        """Old serialised dict without match_method key → defaults to deterministic."""
        data = {
            "registro": "100",
            "fecha": None,
            "material_canonical": "barra",
            "unidad": "KG",
            "declared_qty": "1.0",
            "delta": "0",
            "status": "MATCH",
            "source_pages": [],
        }
        row = ReconciliationRow.model_validate(data)
        assert row.match_method == "deterministic"
