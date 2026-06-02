"""Domain value objects.

All models are Pydantic v2 BaseModel subclasses.
No I/O, no framework, no adapter imports permitted in this module.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, computed_field

from reconciliation.domain.material_key import MatchMethod


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
    # R8.4 (MAT-008): how the canonical key was derived.  Backward-compatible default.
    match_method: MatchMethod = "deterministic"


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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def guia_id(self) -> str:
        """Deterministic identifier: ``{serie}-{numero}`` (e.g. ``T009-0741770``)."""
        return f"{self.serie}-{self.numero}"


class GuiaContribution(BaseModel):
    """A single guía's contribution to a reconciliation group (rev-2, REC-C02).

    Carries the unit so contributions can be mapped to the correct
    ``(registro, fecha, material_canonical, unidad)`` group without cross-unit
    conversion (domain invariant: units are summed independently).

    Rev-3 (D5 / REC-C07): ``year_inferred`` propagates from the source
    ``GuiaDeRemision.year_inferred`` flag.  ``False`` by default (backward compat).
    """

    guia_id: str
    source_pages: list[int]
    cantidad: Decimal
    unidad: str
    confidence: float
    identity_source: Literal["qr", "ocr_fallback"]
    # Rev-3 D5: provenance flag — True when the year component of fecha was
    # reconstructed via bounded inference (EXT-021), not read directly from vision.
    year_inferred: bool = False


class GuiaDeRemision(BaseModel):
    """A single Guía de Remisión document extracted from one or more pages.

    Rev-2 fields (defaulted to preserve backwards-compatible construction):
    ``ruc_emisor``, ``ruc_receptor``, ``tipo``, ``gre_hashqr_url``,
    ``identity_confidence``, ``identity_source``, ``first_page``.

    Rev-3 (D6): ``first_page`` is now ``int | None`` (default ``None``).
    ``None`` means "first page unknown"; ``0`` is the valid concrete page-0 index.
    Fix all ``!= 0`` sentinel idioms: use ``is not None`` instead.

    Rev-3 (D5 / EXT-021): ``year_inferred`` records whether the year component
    of ``fecha`` was reconstructed via bounded inference rather than read directly
    from vision output.  ``False`` by default (backward compat, explicit vision read).
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
    first_page: int | None = None
    # Rev-3 D5: True when the year was inferred via bounded inference (EXT-021).
    year_inferred: bool = False
    # Rev-3 D5: Raw string from VisionResult.raw; needed by _stage_normalize_dates
    # to extract day/month when fecha is None (year missing in model output).
    fecha_raw: str = ""


class Registro(BaseModel):
    """A declared-side registry entry sourced from digital text.

    R9.1 (ADR-2): ``protocolo_page`` is the 0-based PDF page index of the source
    Protocolo de Recepción (``None`` for detail-page-only registros — they carry
    no Protocolo "Fecha:" field).  The pipeline's declared-date vision sub-stage
    needs it to know which PDF page to render and crop.
    """

    numero: str
    fecha_declarada: date | None
    declared_lines: list[MaterialLine]
    # R9.1: source page of the Protocolo de Recepción (0-based PDF page index).
    # None when the Registro originates from a detail page, not a Protocolo.
    # 0 is a VALID concrete page index — never treat as falsy.
    protocolo_page: int | None = None


class PageClassification(BaseModel):
    """Result of classifying a single PDF page by its document title."""

    page: int
    kind: Literal["GUIA", "DECLARED", "IGNORED", "UNCLASSIFIED"]
    title_matched: str | None
    confidence: float
    # Flagging surface (task 7.3 / INJ-007 / INJ-S04, INJ-S05)
    orientation_fallback_failed: bool = False
    """True when the deskew adapter attempted correction but returned a failure result."""
    orientation_low_confidence: bool = False
    """True when deskew orientation confidence is below the threshold (adapter-level)."""
    ocr_empty_after_deskew: bool = False
    """True when OCR returned no text after a deskew+title-extract pass."""


class ReconciliationRow(BaseModel):
    """Output row from ReconciliationService — one per (registro, fecha, material, unidad) group.

    Rev-2: ``guias`` carries per-guía contribution detail (REC-C02 / design §D).
    ``summed_qty`` is a DERIVED computed property: sum of ``guias[*].cantidad``.
    It MUST NOT be written directly; the reconciler populates ``guias`` and
    ``summed_qty`` is derived automatically (REC-C04, S1.6).

    GUIA_MISSING rows have an empty ``guias`` list → ``summed_qty == 0``.
    """

    registro: str
    fecha: date | None
    material_canonical: str
    unidad: str
    declared_qty: Decimal
    delta: Decimal
    status: Literal["MATCH", "MISMATCH", "DECLARED_MISSING", "GUIA_MISSING", "UNCLASSIFIED"]
    source_pages: list[int]
    min_confidence: float | None = None
    # Flagging surface (task 7.3 / REV-004, EXT-S08, EXT-S08b)
    requires_review: bool = False
    """True when any contributing line or guia date has low confidence or null date."""
    # R8.4 (MAT-008): worst-wins match_method aggregated from contributing lines.
    # Backward-compatible default.
    match_method: MatchMethod = "deterministic"
    # Rev-2: inline guía contributions (populated by ReconciliationService.reconcile)
    guias: list[GuiaContribution] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def summed_qty(self) -> Decimal:
        """Derived from guias — MUST NOT be written directly (REC-C04, S1.6 invariant)."""
        return sum((g.cantidad for g in self.guias), start=Decimal(0))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def any_year_inferred(self) -> bool:
        """True when at least one contributing GuiaContribution has year_inferred=True.

        Advisory flag (REC-C07 / D5): surfaces in the review UI and export as a
        transparency signal for the engineer.  Does NOT affect MATCH/MISMATCH logic.
        """
        return any(g.year_inferred for g in self.guias)


class VisionResult(BaseModel):
    """Structured response from a VisionLLMPort date-extraction call.

    Rev-3 (D5 / EXT-021): ``year_inferred`` is set to ``True`` by
    ``_stage_normalize_dates`` AFTER vision returns, when the year component
    was absent or low-confidence and was reconstructed via bounded inference.
    Adapters always produce ``year_inferred=False`` (they read raw output only).
    """

    date: date | None
    confidence: float
    raw: str
    # Rev-3 D5: set to True by _stage_normalize_dates when the year was reconstructed.
    year_inferred: bool = False


class GreLineItem(BaseModel):
    """A single line item from an official SUNAT GRE PDF (rev-3, EXT-023 / D3).

    Carries the authoritative quantities as printed in the official GRE representation.
    Units come from SUNAT (e.g. "TONELADAS", "KILOGRAMOS") and are normalised
    downstream by MaterialNormalizer like any other extraction source.
    """

    cantidad: Decimal
    unidad: str  # raw SUNAT unit string (e.g. "TONELADAS", "KILOGRAMOS")
    descripcion: str  # raw material description
    codigo_producto: str | None = None  # SUNAT product code (e.g. "407797")


class OfficialGre(BaseModel):
    """Structured data from an official SUNAT GRE PDF (rev-3, EXT-023 / D3).

    Promoted from a bare Protocol seam (rev-2 EXT-016) to a PURE Pydantic domain
    model.  No I/O; no adapter imports.  All fields come from the text layer of
    the official SUNAT GRE representation PDF (``get_text()``).

    ``guia_id`` mirrors ``GuiaIdentity.guia_id`` (``{serie}-{numero}``).
    ``fecha_entrega`` is the date the goods were handed to the carrier (the
    deterministic lower bound for bounded year inference — D5).
    ``fecha_emision`` is the electronic issue date (cross-check only).
    """

    guia_id: str  # e.g. "T073-00680258"
    serie: str
    numero: str
    ruc_emisor: str
    ruc_receptor: str
    tipo: str | None = None
    fecha_emision: date | None = None
    fecha_entrega: date | None = None  # lower bound for year inference (D5)
    lines: list[GreLineItem] = []

    @classmethod
    def from_identity(cls, guia_id: str) -> OfficialGre:
        """Create a minimal OfficialGre from a guia_id (testing helper)."""
        parts = guia_id.split("-", 1)
        serie = parts[0] if parts else guia_id
        numero = parts[1] if len(parts) > 1 else ""
        return cls(guia_id=guia_id, serie=serie, numero=numero, ruc_emisor="", ruc_receptor="")


class MaterialKeyInference(BaseModel):
    """Return value from MaterialInferencePort.infer() (R8.6, MAT-006, ADR-2).

    Carries the LLM-inferred canonical tuple for a material description.
    The resolver (MaterialKeyResolver) wraps this into a CanonicalKey with
    method="llm_inferred" after applying the hallucination guard.

    All fields except ``familia`` are optional: the LLM may not always be
    able to infer every dimension.  The resolver handles None values by
    falling through to the unresolved sentinel.
    """

    familia: str
    grado: str | None = None
    diametro: str | None = None
    presentacion: str | None = None
    confidence: float = 0.0


class ReconciliationResult(BaseModel):
    """Output of ReconciliationService.reconcile() — rev-2 (REC-C05 / design §E).

    Wraps the reconciliation rows with a dedicated bucket for unresolved guías
    (those whose ``registro`` is ``None`` or could not be derived from the Contents
    map without emitting a section ID).

    Unresolved guías surface in the review UI under the "unresolved guías" bucket
    for human assignment; they MUST NOT be silently dropped.
    """

    rows: list[ReconciliationRow]
    unresolved_guias: list[GuiaDeRemision] = []
