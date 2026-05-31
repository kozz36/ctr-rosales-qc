"""Unit tests for domain value objects (task 1.1).

Covers: model instantiation, Decimal precision, None confidence allowed.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from reconciliation.domain.models import (
    GuiaDeRemision,
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
