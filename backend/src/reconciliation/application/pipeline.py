"""ReconciliationPipeline — deterministic 10-stage orchestrator.

Stage sequence (fixed, non-negotiable per design):
  1. split       — count pages via DocumentSourcePort
  2. classify    — classify each page by title rules (PageClassifier);
                   scanned pages (empty digital text) receive an ocr_title
                   from the deskew/title-OCR stage when a DeskewPort is wired.
  3. deskew      — correct orientation of GUIA pages (optional DeskewPort)
  4. extract_declared — parse digital text from DECLARED pages using real
                        parsers (DigitalTextExtractionAdapter); dedupe
                        protocolo+detail into ONE Registro per numero
                        (protocolo is canonical source per decision 2026-05-31).
  5. extract_ocr      — OCR material tables from GUIA pages (ExtractionPort.extract_printed_table)
  6. extract_vision   — read handwritten dates (VisionLLMPort); abort if cost cap exceeded
  7. normalize        — canonicalize material descriptions (MaterialNormalizer)
  8. reconcile        — group + compare via ReconciliationService
  9. persist_sidecar  — write extraction cache + initial review sidecar via RunContext
  10. return          — yield PipelineResult

No concrete adapter is imported here.  All I/O is injected as Port implementations.

Cost cap policy (locked):
  Before each VisionLLMPort call the pipeline checks ``calls_made < cap``.
  On cap exhaustion, VisionCapExceededError is raised immediately, preserving
  any partial results already written to the extraction cache.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from reconciliation.domain.classifier import PageClassifier
from reconciliation.domain.errors import VisionCapExceededError
from reconciliation.domain.models import (
    GuiaDeRemision,
    MaterialLine,
    PageClassification,
    Registro,
    ReconciliationRow,
    VisionResult,
)
from reconciliation.domain.normalizer import MaterialNormalizer
from reconciliation.domain.ports import (
    DocumentSourcePort,
    ExtractionPort,
    VisionLLMPort,
)
from reconciliation.domain.reconciliation import ReconciliationService
from reconciliation.application.config import AppConfig
from reconciliation.application.run_context import RunContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DeskewPort — optional; enables scanned-page title-OCR injection
# ---------------------------------------------------------------------------


@runtime_checkable
class DeskewPort(Protocol):
    """Minimal interface the pipeline needs from a deskew/OCR-title adapter.

    Concrete implementation: DeskewAdapter in adapters/ocr/paddle_deskew.py.
    The protocol is defined here (not in domain.ports) to keep the domain pure
    of application-layer concerns.  The pipeline imports this protocol directly.
    """

    def correct_orientation(self, image: bytes) -> bytes:
        """Return orientation-corrected bytes; return original on any failure."""
        ...

    def extract_title(self, image: bytes) -> str | None:
        """Extract the document title string from a page image via OCR.

        Returns:
            Title string (e.g. "GUIA DE REMISION"), or None if OCR failed or
            the adapter is unavailable.  The pipeline uses this for GUIA
            classification of scanned pages.
        """
        ...


# ---------------------------------------------------------------------------
# Declared-page extractor protocol (injected for testing / loose coupling)
# ---------------------------------------------------------------------------


@runtime_checkable
class DeclaredExtractorPort(Protocol):
    """Sub-interface exposed by DigitalTextExtractionAdapter for declared pages.

    The pipeline uses this to call the richer Registro-level methods instead of
    the plain MaterialLine-level ``extract_declared``.
    """

    def extract_registro_from_detail_page(
        self, text: str, source_page: int
    ) -> "Registro | None":
        """Parse a Form Detail page into a Registro; None if not a valid page."""
        ...

    def extract_registro_from_proto_page(
        self, text: str, source_page: int
    ) -> "Registro | None":
        """Parse a Protocolo de Recepción page into a Registro; None if not a valid page."""
        ...


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class PipelineResult:
    """Immutable result returned after a successful pipeline run.

    Attributes:
        run_id:        Matches RunContext.run_id.
        classifications: One entry per page.
        declared:      Parsed declared-side Registro objects (deduped, one per numero).
        guias:         Extracted GuiaDeRemision objects (dates filled by vision).
        rows:          Final reconciliation output.
        vision_calls_made: Number of VisionLLMPort calls consumed.
    """

    run_id: str
    classifications: list[PageClassification]
    declared: list[Registro]
    guias: list[GuiaDeRemision]
    rows: list[ReconciliationRow]
    vision_calls_made: int = 0
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class ReconciliationPipeline:
    """Orchestrates the deterministic reconciliation pipeline.

    All dependencies are constructor-injected.  The class holds no mutable
    state between ``run()`` calls — each invocation is fully isolated via
    its RunContext.

    Args:
        doc_source:         DocumentSourcePort implementation.
        extractor:          ExtractionPort implementation (also used as
                            DeclaredExtractorPort if the object exposes the
                            higher-level Registro-parse methods).
        vision:             VisionLLMPort implementation.
        config:             AppConfig instance driving cost caps and thresholds.
        page_to_registro:   Pre-computed 0-based page→registro_numero mapping
                            (derived from Contents page).  When provided, guía
                            pages are tagged with their section's Registro numero
                            so they join the correct reconciliation group.
                            Pass an empty dict (default) to skip tagging.
        deskew:             Optional DeskewPort implementation.  When provided,
                            scanned pages (empty digital text) are deskewed and
                            their title is OCR-extracted to enable GUIA
                            classification.  When None (default), scanned pages
                            remain UNCLASSIFIED without crashing the pipeline.
    """

    def __init__(
        self,
        doc_source: DocumentSourcePort,
        extractor: ExtractionPort,
        vision: VisionLLMPort,
        config: AppConfig,
        page_to_registro: dict[int, str | None] | None = None,
        deskew: DeskewPort | None = None,
    ) -> None:
        self._doc = doc_source
        self._extractor = extractor
        self._vision = vision
        self._config = config
        self._page_to_registro: dict[int, str | None] = page_to_registro or {}
        self._deskew = deskew
        self._classifier = PageClassifier()
        self._normalizer = MaterialNormalizer()
        self._reconciler = ReconciliationService()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, ctx: RunContext) -> PipelineResult:
        """Execute all pipeline stages and return a PipelineResult.

        Args:
            ctx: Per-run isolation context.  The input PDF is read via
                 ``self._doc``; ``ctx`` owns all output paths.

        Returns:
            A PipelineResult with the full reconciliation output.

        Raises:
            VisionCapExceededError: If the vision cost cap is reached before
                all GUIA pages are processed.  Partial extraction results are
                written to the extraction cache before raising.
        """
        # Stage 1: split
        page_count = self._stage_split()

        # Stage 2: classify (scanned pages get ocr_title from deskew stage)
        classifications = self._stage_classify(page_count)

        # Stage 3: deskew (orientation correction for GUIA pages; no-op if deskew=None)
        # Note: title-OCR for scanned pages is already wired in _stage_classify above.
        # This stage applies image correction that the OCR in stage 5 benefits from.
        # (Explicit deskew pass on images is performed inside _stage_extract_ocr.)

        # Stage 4: extract declared (digital text; real parsers; dedupe proto+detail)
        declared = self._stage_extract_declared(classifications)

        # Stage 5: extract OCR tables from guia pages; tag with registro numero
        raw_guias = self._stage_extract_ocr(classifications)

        # Stage 6: extract vision dates (handwritten)
        guias, vision_calls_made, warnings = self._stage_extract_vision(raw_guias)

        # Stage 7: normalize descriptions
        declared, guias = self._stage_normalize(declared, guias)

        # Stage 8: reconcile
        rows = self._stage_reconcile(declared, guias)

        # Stage 9: persist sidecar
        self._stage_persist(ctx, classifications, declared, guias, rows)

        return PipelineResult(
            run_id=ctx.run_id,
            classifications=classifications,
            declared=declared,
            guias=guias,
            rows=rows,
            vision_calls_made=vision_calls_made,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Stage implementations
    # ------------------------------------------------------------------

    def _stage_split(self) -> int:
        """Stage 1: return total page count."""
        count = self._doc.page_count()
        logger.debug("split: %d pages", count)
        return count

    def _stage_classify(self, page_count: int) -> list[PageClassification]:
        """Stage 2: classify each page by title rules.

        For scanned pages (empty or noise-only digital text), the deskew
        adapter is called first to correct orientation, then ``extract_title``
        is used to produce an ``ocr_title`` that the classifier uses as a
        fallback.  When no deskew adapter is wired (``self._deskew is None``),
        scanned pages remain UNCLASSIFIED without crashing the pipeline.
        """
        classifications: list[PageClassification] = []
        for idx in range(page_count):
            text = self._doc.page_text(idx)
            ocr_title: str | None = None

            # Attempt title-OCR for potentially scanned pages only when deskew is wired.
            if self._deskew is not None and not _has_meaningful_text(text):
                try:
                    raw_image = self._doc.render_page(idx, dpi=200)
                    deskewed = self._deskew.correct_orientation(raw_image)
                    ocr_title = self._deskew.extract_title(deskewed)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "classify: deskew/title-OCR failed for page %d: %s", idx, exc
                    )

            classification = self._classifier.classify_page(
                page_index=idx,
                page_text=text,
                ocr_title=ocr_title,
            )
            classifications.append(classification)
            logger.debug(
                "classify: page %d → %s (title=%r, ocr_title=%r)",
                idx,
                classification.kind,
                classification.title_matched,
                ocr_title,
            )
        return classifications

    def _stage_extract_declared(
        self, classifications: list[PageClassification]
    ) -> list[Registro]:
        """Stage 4: extract declared material lists from DECLARED pages.

        Uses the real Registro-level parsers from DigitalTextExtractionAdapter
        (via DeclaredExtractorPort duck-typing).  Each (detail + protocolo) pair
        for the SAME registro numero is deduped into a SINGLE Registro with the
        Protocolo de Recepción as the canonical source (per locked decision
        2026-05-31: protocolo is the authoritative declared source).

        Algorithm:
        1. Collect all DECLARED pages.
        2. For each page, call the appropriate parser
           (proto → extract_registro_from_proto_page;
            detail → extract_registro_from_detail_page).
        3. Dedupe by numero: if a proto Registro exists for numero N, keep it
           as-is.  If only a detail Registro exists for N, use it.
        4. Return exactly one Registro per unique numero.
        """
        # Check whether the extractor exposes the richer Registro-level methods.
        # If not (e.g. a plain FakeExtractor in unit tests), fall back to the
        # MaterialLine-only path with page-based placeholder numbering.
        has_registro_api = (
            hasattr(self._extractor, "extract_registro_from_proto_page")
            and hasattr(self._extractor, "extract_registro_from_detail_page")
        )

        if not has_registro_api:
            return self._stage_extract_declared_legacy(classifications)

        # Map: numero → {"proto": Registro | None, "detail": Registro | None}
        by_numero: dict[str, dict[str, Registro | None]] = {}

        for cls in classifications:
            if cls.kind != "DECLARED":
                continue
            text = self._doc.page_text(cls.page)
            if not text:
                logger.warning(
                    "DECLARED page %d has no digital text; skipping.", cls.page
                )
                continue

            if "PROTOCOLO DE RECEPCI" in text:
                registro = self._extractor.extract_registro_from_proto_page(  # type: ignore[attr-defined]
                    text, cls.page
                )
                if registro is None:
                    logger.warning(
                        "DECLARED page %d (PROTO): parser returned None; skipping.",
                        cls.page,
                    )
                    continue
                slot = by_numero.setdefault(registro.numero, {"proto": None, "detail": None})
                slot["proto"] = registro
                logger.debug(
                    "extract_declared: PROTO page %d → numero=%r, %d lines",
                    cls.page,
                    registro.numero,
                    len(registro.declared_lines),
                )
            else:
                registro = self._extractor.extract_registro_from_detail_page(  # type: ignore[attr-defined]
                    text, cls.page
                )
                if registro is None:
                    logger.warning(
                        "DECLARED page %d (DETAIL): parser returned None; skipping.",
                        cls.page,
                    )
                    continue
                slot = by_numero.setdefault(registro.numero, {"proto": None, "detail": None})
                slot["detail"] = registro
                logger.debug(
                    "extract_declared: DETAIL page %d → numero=%r, %d lines",
                    cls.page,
                    registro.numero,
                    len(registro.declared_lines),
                )

        # Dedupe: protocolo is canonical; fall back to detail only if proto absent.
        registros: list[Registro] = []
        for numero, slots in by_numero.items():
            canonical = slots["proto"] if slots["proto"] is not None else slots["detail"]
            if canonical is not None:
                registros.append(canonical)
            else:
                logger.warning("extract_declared: numero=%r has no parseable page; skipping.", numero)

        logger.debug(
            "extract_declared: %d unique registros (from %d DECLARED pages)",
            len(registros),
            sum(1 for c in classifications if c.kind == "DECLARED"),
        )
        return registros

    def _stage_extract_declared_legacy(
        self, classifications: list[PageClassification]
    ) -> list[Registro]:
        """Fallback path for unit tests where the extractor only implements ExtractionPort.

        Uses page-based placeholder numbering (page_N) so existing tests that
        do not inject a DeclaredExtractorPort still pass.
        """
        registros: list[Registro] = []
        for cls in classifications:
            if cls.kind != "DECLARED":
                continue
            text = self._doc.page_text(cls.page)
            if not text:
                logger.warning("DECLARED page %d has no digital text; skipping.", cls.page)
                continue
            lines = self._extractor.extract_declared(text)
            if not lines:
                logger.warning("DECLARED page %d yielded no material lines.", cls.page)
                continue
            registro = Registro(
                numero=f"page_{cls.page}",
                fecha_declarada=None,
                declared_lines=lines,
            )
            registros.append(registro)
            logger.debug(
                "extract_declared (legacy): page %d → %d lines", cls.page, len(lines)
            )
        return registros

    def _stage_extract_ocr(
        self, classifications: list[PageClassification]
    ) -> list[_RawGuia]:
        """Stage 5: OCR material tables from GUIA pages; tag with registro numero.

        Each RawGuia is tagged with its section's Registro numero from the
        pre-computed ``page_to_registro`` map.  If the page is not in the map
        (outside all known section ranges), ``registro`` remains None and the
        guia surfaces as UNCLASSIFIED in reconciliation.
        """
        raw_guias: list[_RawGuia] = []
        for cls in classifications:
            if cls.kind != "GUIA":
                continue
            image = self._doc.render_page(cls.page, dpi=200)
            # Apply deskew before OCR when the adapter is wired
            if self._deskew is not None:
                try:
                    image = self._deskew.correct_orientation(image)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "extract_ocr: deskew failed for page %d: %s", cls.page, exc
                    )
            lines = self._extractor.extract_printed_table(image)
            registro_numero = self._page_to_registro.get(cls.page)
            raw_guias.append(
                _RawGuia(
                    guia_id=f"guia_page_{cls.page}",
                    source_page=cls.page,
                    image=image,
                    lines=lines,
                    registro=registro_numero,
                )
            )
            logger.debug(
                "extract_ocr: page %d → %d lines, registro=%r",
                cls.page,
                len(lines),
                registro_numero,
            )
        return raw_guias

    def _stage_extract_vision(
        self, raw_guias: list[_RawGuia]
    ) -> tuple[list[GuiaDeRemision], int, list[str]]:
        """Stage 6: attach handwritten dates to guías via VisionLLMPort.

        Respects the vision cost cap.  Raises VisionCapExceededError if the
        cap is exhausted before all guías are processed.

        Returns:
            (guias, calls_made, warnings)
        """
        cap = self._config.vision.max_vision_calls
        calls_made = 0
        warnings: list[str] = []
        guias: list[GuiaDeRemision] = []

        if not raw_guias:
            return guias, calls_made, warnings

        if self._vision.supports_batch:
            # Batch path: one call per batch of images
            remaining = cap - calls_made
            if remaining <= 0:
                raise VisionCapExceededError(
                    "Vision cost cap reached before processing started.",
                    detail={"calls_made": calls_made, "cap": cap, "pages_remaining": len(raw_guias)},
                )
            # Send all images in one batch call
            images = [rg.image for rg in raw_guias]
            if len(images) > remaining:
                # Partial batch up to cap
                to_process = raw_guias[:remaining]
                skipped = raw_guias[remaining:]
                results = self._vision.read_handwritten_date_batch(
                    [rg.image for rg in to_process]
                )
                calls_made += len(to_process)
                for rg, vr in zip(to_process, results):
                    guias.append(_build_guia(rg, vr))
                # Raise for the skipped portion
                raise VisionCapExceededError(
                    f"Vision cost cap ({cap}) reached after {calls_made} calls.",
                    detail={
                        "calls_made": calls_made,
                        "cap": cap,
                        "pages_remaining": len(skipped),
                    },
                )
            else:
                results = self._vision.read_handwritten_date_batch(images)
                calls_made += len(images)
                for rg, vr in zip(raw_guias, results):
                    guias.append(_build_guia(rg, vr))
        else:
            # Sequential path (Ollama / non-batch)
            for rg in raw_guias:
                if calls_made >= cap:
                    raise VisionCapExceededError(
                        f"Vision cost cap ({cap}) reached after {calls_made} calls.",
                        detail={
                            "calls_made": calls_made,
                            "cap": cap,
                            "pages_remaining": len(raw_guias) - calls_made,
                        },
                    )
                vr = self._vision.read_handwritten_date(rg.image)
                calls_made += 1
                guia = _build_guia(rg, vr)
                guias.append(guia)
                if vr.confidence < self._config.confidence.threshold:
                    warnings.append(
                        f"Low vision confidence ({vr.confidence:.2f}) "
                        f"on page {rg.source_page}."
                    )

        return guias, calls_made, warnings

    def _stage_normalize(
        self,
        declared: list[Registro],
        guias: list[GuiaDeRemision],
    ) -> tuple[list[Registro], list[GuiaDeRemision]]:
        """Stage 7: canonicalize material descriptions in all lines."""

        def _norm_line(line: MaterialLine) -> MaterialLine:
            canonical = self._normalizer.canonicalize(line.description_raw)
            return line.model_copy(update={"description_canonical": canonical})

        normalised_declared = [
            registro.model_copy(
                update={"declared_lines": [_norm_line(l) for l in registro.declared_lines]}
            )
            for registro in declared
        ]
        normalised_guias = [
            guia.model_copy(
                update={"lines": [_norm_line(l) for l in guia.lines]}
            )
            for guia in guias
        ]
        return normalised_declared, normalised_guias

    def _stage_reconcile(
        self,
        declared: list[Registro],
        guias: list[GuiaDeRemision],
    ) -> list[ReconciliationRow]:
        """Stage 8: group + compare via ReconciliationService."""
        rows = self._reconciler.reconcile(declared, guias)
        logger.debug("reconcile: %d output rows", len(rows))
        return rows

    def _stage_persist(
        self,
        ctx: RunContext,
        classifications: list[PageClassification],
        declared: list[Registro],
        guias: list[GuiaDeRemision],
        rows: list[ReconciliationRow],
    ) -> None:
        """Stage 9: write extraction cache + initial empty review sidecar."""
        if not ctx.has_extraction_cache():
            cache_data: dict[str, Any] = {
                "run_id": ctx.run_id,
                "classifications": [c.model_dump(mode="json") for c in classifications],
                "declared": [r.model_dump(mode="json") for r in declared],
                "guias": [g.model_dump(mode="json") for g in guias],
                "rows": [row.model_dump(mode="json") for row in rows],
            }
            ctx.write_extraction_cache(cache_data)

        # Initialise the review sidecar only if it doesn't exist yet
        # (preserve existing edits on a restart/reload scenario).
        if not ctx.has_review_sidecar():
            ctx.write_review_sidecar({"edits": [], "audit_trail": []})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _has_meaningful_text(text: str | None) -> bool:
    """Return True if ``text`` contains more than universal header/footer noise."""
    if not text or not text.strip():
        return False
    # Import locally to avoid circular dependency; classifier is domain-layer
    from reconciliation.domain.classifier import _clean_lines  # noqa: PLC0415
    return bool(_clean_lines(text))


@dataclass
class _RawGuia:
    """Intermediate object holding a guía's OCR data before date extraction."""

    guia_id: str
    source_page: int
    image: bytes
    lines: list[MaterialLine]
    registro: str | None = None  # section registro numero from page_to_registro map


def _build_guia(raw: _RawGuia, vision_result: VisionResult) -> GuiaDeRemision:
    """Assemble a GuiaDeRemision from OCR lines and a VisionResult date."""
    return GuiaDeRemision(
        guia_id=raw.guia_id,
        registro=raw.registro,  # set from page_to_registro map (C-4 fix)
        fecha=vision_result.date,
        fecha_confidence=vision_result.confidence,
        lines=raw.lines,
        source_pages=[raw.source_page],
    )
