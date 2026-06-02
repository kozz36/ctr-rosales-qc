"""Domain value objects.

All models are Pydantic v2 BaseModel subclasses.
No I/O, no framework, no adapter imports permitted in this module.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, computed_field


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


class GuiaIdentity(BaseModel):
    """Identity decoded from a Guía de Remisión QR code (rev-2, EXT-011).

    Fields populated from the compact SUNAT GRE QR payload.
    ``hashqr_url`` is set when a URL-variant QR is also decoded on the same page.
    ``confidence`` is 1.0 only when all gating conditions pass (EXT-012).
    """

    serie: str
    numero: str
    ruc_emisor: str
    ruc_receptor: str
    tipo: str
    hashqr_url: str | None = None
    confidence: float

    @computed_field  # type: ignore[misc]
    @property
    def guia_id(self) -> str:
        """Deterministic identifier: ``{serie}-{numero}`` (e.g. ``T009-0741770``)."""
        return f"{self.serie}-{self.numero}"


class GuiaContribution(BaseModel):
    """A single guía's contribution to a reconciliation group (rev-2, REC-C02).

    Carries the unit so contributions can be mapped to the correct
    ``(registro, fecha, material_canonical, unidad)`` group without cross-unit
    conversion (domain invariant: units are summed independently).
    """

    guia_id: str
    source_pages: list[int]
    cantidad: Decimal
    unidad: str
    confidence: float
    identity_source: Literal["qr", "ocr_fallback"]


class GuiaDeRemision(BaseModel):
    """A single Guía de Remisión document extracted from one or more pages.

    Rev-2 fields (defaulted to preserve backwards-compatible construction):
    ``ruc_emisor``, ``ruc_receptor``, ``tipo``, ``gre_hashqr_url``,
    ``identity_confidence``, ``identity_source``, ``first_page``.
    """

    guia_id: str
    registro: str | None
    fecha: date | None
    fecha_confidence: float | None = None
    lines: list[MaterialLine]
    source_pages: list[int]
    # Rev-2 identity fields (EXT-015 / design §7)
    ruc_emisor: str | None = None
    ruc_receptor: str | None = None
    tipo: str | None = None
    gre_hashqr_url: str | None = None
    identity_confidence: float = 0.0
    identity_source: Literal["qr", "ocr_fallback"] = "ocr_fallback"
    first_page: int = 0


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
    """Output row from ReconciliationService — one per (registro, fecha, material, unidad) group.

    Rev-2: ``guias`` carries per-guía contribution detail (REC-C02 / design §D).
    ``summed_qty`` MUST remain a stored field (not a computed property) because the
    reconciler derives it during grouping; ``guias`` will be populated in S1.6 (PR-7).
    """

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
    # Rev-2: inline guía contributions (populated in PR-7 S1.6; empty by default)
    guias: list[GuiaContribution] = []


class VisionResult(BaseModel):
    """Structured response from a VisionLLMPort date-extraction call."""

    date: date | None
    confidence: float
    raw: str
