"""Domain value objects.

All models are Pydantic v2 BaseModel subclasses.
No I/O, no framework, no adapter imports permitted in this module.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


class MaterialLine(BaseModel):
    """A single material row extracted from a guía or declared page."""

    description_raw: str
    description_canonical: str
    unidad: Literal["KG", "TN", "RD", "Rollo"]
    cantidad: Decimal
    confidence: float | None = None
    source_page: int | None = None
    # Flagging surface (task 7.3 completes all flags, but field defined here per spec)
    requires_review: bool = False


class GuiaDeRemision(BaseModel):
    """A single Guía de Remisión document extracted from one or more pages."""

    guia_id: str
    registro: str | None
    fecha: date | None
    fecha_confidence: float | None = None
    lines: list[MaterialLine]
    source_pages: list[int]


class Registro(BaseModel):
    """A declared-side registry entry sourced from digital text."""

    numero: str
    fecha_declarada: date | None
    declared_lines: list[MaterialLine]


class PageClassification(BaseModel):
    """Result of classifying a single PDF page by its document title."""

    page: int
    kind: Literal["GUIA", "DECLARED", "IGNORED", "UNCLASSIFIED"]
    title_matched: str | None
    confidence: float


class ReconciliationRow(BaseModel):
    """Output row from ReconciliationService — one per (registro, fecha, material, unidad) group."""

    registro: str
    fecha: date | None
    material_canonical: str
    unidad: str
    declared_qty: Decimal
    summed_qty: Decimal
    delta: Decimal
    status: Literal["MATCH", "MISMATCH", "DECLARED_MISSING", "GUIA_MISSING", "UNCLASSIFIED"]
    source_pages: list[int]
    min_confidence: float | None = None


class VisionResult(BaseModel):
    """Structured response from a VisionLLMPort date-extraction call."""

    date: date | None
    confidence: float
    raw: str
