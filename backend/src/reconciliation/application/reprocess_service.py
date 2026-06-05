"""ReprocessService — deterministic REINTENTAR recovery for errored guías (PR #2).

Orchestrates: render source_pages → decode_hashqr_url → SUNAT fetch → normalize
→ ReviewService.add_recovered_guia (the SOLE mutation hook).

Architecture invariants (auto-reject if violated):
  - Ports-only constructor: DocumentSourcePort, IdentityExtractionPort,
    SunatGreFetchPort, MaterialKeyResolver, ReviewService, AppConfig.
  - ZERO concrete adapter imports at module level (container.py does the wiring).
  - Heavy deps (fitz/pyzbar/requests/etc.) stay INSIDE adapter methods — never
    imported here.
  - No vision calls: guía fecha = SUNAT fecha_entrega via apply_delivery_floor(None, ...)
    (R9b Rule-2 floor).  Deterministic + air-gap-safe.
  - Recovered lines: requires_review=True (invariant — reconciliation gate).
  - PDF read-only (render_page reads ctx.pdf_path via DocumentSourcePort; no writes).
  - fecha is NEVER a grouping axis; normalization produces the same group_token
    (description_canonical) as the pipeline's _norm_line for identical inputs.

Design ref: design-pr2.md §Architecture Decision 3 (CRUX normalization parity).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from reconciliation.domain.date_floor import apply_delivery_floor
from reconciliation.domain.models import (
    GreLineItem,
    GuiaDeRemision,
    MaterialLine,
    OfficialGre,
)

if TYPE_CHECKING:
    from reconciliation.application.review_service import ReviewService
    from reconciliation.domain.material_key_resolver import MaterialKeyResolver
    from reconciliation.domain.ports import (
        DocumentSourcePort,
        IdentityExtractionPort,
        SunatGreFetchPort,
    )

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domain constants — mirrors pipeline._VALID_UNITS (kept in sync manually;
# both should reference the same Literal["KG","TN","RD","Rollo"] domain set).
# ---------------------------------------------------------------------------

_VALID_UNITS: frozenset[str] = frozenset({"KG", "TN", "RD", "Rollo"})

# SUNAT long-form → domain code mapping (mirrors pipeline._normalize_sunat_unit).
_SUNAT_UNIT_MAP: dict[str, str] = {
    "TONELADAS": "TN",
    "TNE": "TN",
    "TN": "TN",
    "KILOGRAMOS": "KG",
    "KGM": "KG",
    "KG": "KG",
    "ROLLO": "Rollo",
    "ROL": "Rollo",
    "VARILLA": "RD",
    "RD": "RD",
}


# ---------------------------------------------------------------------------
# Public module-level helpers (shared with tests and future callers)
# ---------------------------------------------------------------------------


def _normalize_sunat_unit_for_recovery(sunat_unit: str) -> str:
    """Normalize a SUNAT unit code to the domain unit enum.

    Mirrors ``pipeline._normalize_sunat_unit``.  Extracted as a module-level
    function so tests can import and call it for parity verification.
    """
    return _SUNAT_UNIT_MAP.get(sunat_unit.upper(), sunat_unit)


def _build_recovered_guia_lines(
    official: OfficialGre,
    source_page: int,
    key_resolver: MaterialKeyResolver,
) -> list[MaterialLine]:
    """Build normalized MaterialLines from a SUNAT OfficialGre for a recovered guía.

    This is the CRUX T-2 helper.  It mirrors the pipeline's ``_apply_sunat_result``
    (pipeline.py L1216-1226) followed by ``_norm_line`` (pipeline.py L1553) exactly,
    so the recovered guía's group_token / match_method / unidad / cantidad are
    IDENTICAL to what a first-pass pipeline would have produced.

    Key invariants:
      - ``requires_review=True`` on ALL recovered lines (reconciliation validation
        gate — recovered guías are never auto-accepted).
      - Units that cannot be normalized to the domain set are SKIPPED (same filter
        as the pipeline).
      - ``confidence=1.0`` (SUNAT data is authoritative — no OCR confidence applies).
      - ``description_canonical`` = ``key.group_token`` (from key_resolver.resolve)
        AFTER the initial placeholder (``item.descripcion``), exactly as _norm_line
        overwrites it.

    Args:
        official:     Parsed SUNAT GRE data.
        source_page:  0-based page index for ``MaterialLine.source_page``.
        key_resolver: MaterialKeyResolver used by the pipeline (shared instance).

    Returns:
        List of normalized MaterialLines with requires_review=True.
    """
    lines: list[MaterialLine] = []
    for item in official.lines:
        normalized_unit = _normalize_sunat_unit_for_recovery(item.unidad)
        if normalized_unit not in _VALID_UNITS:
            logger.warning(
                "_build_recovered_guia_lines: SUNAT unit %r → %r not in domain set; "
                "skipping line (descripcion=%r)",
                item.unidad,
                normalized_unit,
                item.descripcion,
            )
            continue

        # Step 1: build MaterialLine exactly like pipeline._apply_sunat_result
        # (L1216-1226): description_canonical is a placeholder (item.descripcion);
        # normalizer runs in Step 2 below (mirrors pipeline Stage 8).
        raw_line = MaterialLine(
            description_raw=item.descripcion,
            description_canonical=item.descripcion,  # placeholder; overwritten in Step 2
            unidad=normalized_unit,  # type: ignore[arg-type]
            cantidad=item.cantidad,
            confidence=1.0,  # SUNAT authoritative
            source_page=source_page,
            requires_review=True,  # ALWAYS True for recovered guías
        )

        # Step 2: resolve canonical key via MaterialKeyResolver.resolve (mirrors
        # pipeline._norm_line L1553).  This sets group_token → description_canonical
        # and match_method so the recovered guía participates in the SAME
        # (registro, group_token, unidad) group as a normally-processed guía.
        key = key_resolver.resolve(raw_line.description_raw, raw_line.unidad)
        normalized_line = raw_line.model_copy(update={
            "description_canonical": key.group_token,
            "match_method": key.method,
            # requires_review: keep True (OR with key.requires_review, but always True)
            "requires_review": True,
        })
        lines.append(normalized_line)

    return lines


# ---------------------------------------------------------------------------
# RetryResult
# ---------------------------------------------------------------------------


@dataclass
class RetryResult:
    """Outcome of a single ReprocessService.apply_retry call.

    Attributes:
        recovered:   True when the guía was successfully recovered from SUNAT.
        guia_id:     The guía that was retried.
        reason:      Failure reason when recovered=False (None on success).
                     Values: ``"no_hashqr_url"`` | ``"sunat_empty"`` | ``"sunat_none"``.
        rows:        Updated ReconciliationRow list from ReviewService.
    """

    recovered: bool
    guia_id: str
    reason: str | None = None
    rows: list[Any] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ReprocessService
# ---------------------------------------------------------------------------


class ReprocessService:
    """Orchestrates REINTENTAR deterministic recovery for errored guías.

    Constructor ports (all Protocols — ZERO concrete adapter imports here):
      - ``doc_source``:     DocumentSourcePort  — render source pages at DPI.
      - ``identity``:       IdentityExtractionPort — decode_identity + decode_hashqr_url.
      - ``sunat``:          SunatGreFetchPort — fetch official GRE by hashqr_url.
      - ``key_resolver``:   MaterialKeyResolver — normalize descriptions to canonical key.
      - ``review_service``: ReviewService — add_recovered_guia (SOLE mutation hook).

    ``build_reprocess_service`` in container.py wires the concrete adapters (T-6).

    Design: Approach B (locked) — ReprocessService = adapter orchestrator (ports-only);
    ReviewService keeps SRP over in-memory guía list / re-reconcile / persistence.

    Render DPI=300: higher than the pipeline's 200 DPI first-pass to increase
    QR decode success on errored pages (design-pr2.md §Decision 2).
    """

    _RENDER_DPI: int = 300

    def __init__(
        self,
        doc_source: DocumentSourcePort,
        identity: IdentityExtractionPort,
        sunat: SunatGreFetchPort,
        key_resolver: MaterialKeyResolver,
        review_service: ReviewService,
    ) -> None:
        self._doc_source = doc_source
        self._identity = identity
        self._sunat = sunat
        self._key_resolver = key_resolver
        self._review_service = review_service

    def apply_retry(
        self,
        guia_id: str,
        source_pages: list[int],
    ) -> RetryResult:
        """Attempt to recover a single errored guía via REINTENTAR deterministic flow.

        Sequence (design-pr2.md §Decision 1):
          1. Render first source_page at DPI=300.
          2. decode_hashqr_url → if absent: result(recovered=False, reason="no_hashqr_url").
          3. SunatGreFetchPort.fetch(hashqr_url) → if None: result(recovered=False, reason="sunat_none").
          4. If SUNAT returns 0 lines: result(recovered=False, reason="sunat_empty").
          5. Build normalized lines via _build_recovered_guia_lines (T-2 parity crux).
          6. Apply R9b delivery floor: fecha = apply_delivery_floor(None, fecha_entrega).
          7. Build GuiaDeRemision with requires_review lines + fecha.
          8. Call review_service.add_recovered_guia(guia) (SOLE mutation hook, T-3).
          9. Return RetryResult(recovered=True, rows=...).

        Args:
            guia_id:      Deterministic guía identifier (serie-numero).
            source_pages: List of 0-based PDF page indices for this errored guía.

        Returns:
            RetryResult with recovered=True on success, or recovered=False + reason on failure.
        """
        first_page = source_pages[0] if source_pages else 0

        # Step 1: render source page at DPI=300 for improved QR decode.
        rendered: bytes | None = None
        try:
            rendered = self._doc_source.render_page(first_page, dpi=self._RENDER_DPI)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ReprocessService.apply_retry: render_page(%d, dpi=%d) failed for %r: %s",
                first_page,
                self._RENDER_DPI,
                guia_id,
                exc,
            )

        # Step 2: decode hashqr URL.
        hashqr_url: str | None = None
        if rendered is not None:
            try:
                hashqr_url = self._identity.decode_hashqr_url(rendered, page_idx=first_page)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ReprocessService.apply_retry: decode_hashqr_url failed for %r: %s",
                    guia_id,
                    exc,
                )

        if not hashqr_url:
            logger.info(
                "ReprocessService.apply_retry: no hashqr_url for %r → stays errored", guia_id
            )
            return RetryResult(recovered=False, guia_id=guia_id, reason="no_hashqr_url")

        # Step 3: SUNAT fetch.
        official: OfficialGre | None = None
        try:
            official = self._sunat.fetch(hashqr_url)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ReprocessService.apply_retry: SUNAT fetch failed for %r: %s", guia_id, exc
            )

        if official is None:
            logger.info(
                "ReprocessService.apply_retry: SUNAT returned None for %r → stays errored",
                guia_id,
            )
            return RetryResult(recovered=False, guia_id=guia_id, reason="sunat_none")

        # Step 4: build normalized lines (T-2 crux).
        lines = _build_recovered_guia_lines(
            official=official,
            source_page=first_page,
            key_resolver=self._key_resolver,
        )

        if not lines:
            logger.info(
                "ReprocessService.apply_retry: SUNAT returned 0 usable lines for %r → stays errored",
                guia_id,
            )
            return RetryResult(recovered=False, guia_id=guia_id, reason="sunat_empty")

        # Step 5: apply R9b delivery floor — fecha = fecha_entrega (no vision).
        # apply_delivery_floor(None, fecha_entrega) → (fecha_entrega, True) when
        # fecha_entrega is set; → (None, False) when SUNAT did not supply it.
        fecha, _ = apply_delivery_floor(None, official.fecha_entrega)

        # Step 6: build GuiaDeRemision — requires_review lines, SUNAT-authoritative fecha.
        guia = GuiaDeRemision(
            guia_id=guia_id,
            registro=None,  # ReviewService.add_recovered_guia will match by guia_id
            fecha=fecha,
            fecha_entrega=official.fecha_entrega,
            lines=lines,
            source_pages=source_pages,
            identity_source="qr",  # recovered via QR hashqr_url path
        )

        # Step 7: hand off to ReviewService (SOLE mutation hook — T-3).
        updated_rows = self._review_service.add_recovered_guia(guia)
        logger.info(
            "ReprocessService.apply_retry: %r recovered successfully; %d rows updated",
            guia_id,
            len(updated_rows),
        )
        return RetryResult(recovered=True, guia_id=guia_id, rows=updated_rows)
