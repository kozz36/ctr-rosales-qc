"""Composition root — wires concrete adapters into the ReconciliationPipeline.

This is the ONLY module that imports concrete adapter classes.  Domain and
application layers must never import from here.

CompositeExtractionAdapter
--------------------------
Implements ExtractionPort by routing each call to the correct concrete adapter:

- extract_declared(text)       → DigitalTextExtractionAdapter  (DECLARED pages)
- extract_printed_table(image) → PrintedTableAdapter           (GUIA pages, OCR)

The split ensures that OCR (ML deps) is only invoked on guía images, never on
digital text pages.

Section ↔ Registro Correlation  (C-3 / C-4 fix)
------------------------------------------------
The PDF Contents page maps each section ID (e.g. "4252") to a 1-based start
page.  The DigitalTextExtractionAdapter parses the *Description numero* (e.g.
"232") from the PROTOCOLO / FORM DETAIL page within each section.  These two
identifiers are DIFFERENT (Contents system ID vs. form sequence number).

``build_page_to_registro_map`` now performs the two-step derivation:
  1. Compute page ranges from Contents offsets (as before).
  2. For each section, parse the first readable DECLARED page inside that range
     to extract the true *Description numero*.  If parsing fails, the
     Contents ID is used as a safe fallback.
  3. The returned dict maps ``0-based page → Description numero`` (e.g. "232"),
     matching the key used on the declared side.

Each GUIA page that falls within a section's page range is tagged with that
section's Description numero, which is the same key used on the declared side.

Lazy adapter construction
--------------------------
DeskewAdapter and PrintedTableAdapter require ML deps (paddleocr).
Vision adapters require SDK deps (anthropic/openai).
``build_pipeline`` constructs them lazily-by-reference: the objects are created
at build time but their heavy internal state (PaddleOCR, SDK client) is loaded
only on first use, via the lazy-load pattern already implemented in each adapter.
``build_pipeline`` will NOT crash if ML/SDK packages are absent — the crash
happens only when the adapter is first invoked.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from reconciliation.application.config import AppConfig
from reconciliation.application.pipeline import ReconciliationPipeline
from reconciliation.application.review_service import ReviewService
from reconciliation.application.run_context import RunContext
from reconciliation.domain.models import MaterialLine
from reconciliation.domain.ports import ExtractionPort
from reconciliation.domain.section_id_guard import is_section_id

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CompositeExtractionAdapter
# ---------------------------------------------------------------------------


class CompositeExtractionAdapter:
    """Routes ExtractionPort calls to the correct concrete adapter.

    - extract_declared  → DigitalTextExtractionAdapter (no OCR, always safe)
    - extract_printed_table → PrintedTableAdapter (OCR; lazy PaddleOCR load)

    Also exposes the higher-level Registro-parse methods from
    DigitalTextExtractionAdapter so the pipeline's DeclaredExtractorPort
    duck-typing check succeeds and the real parsers are called.

    ML deps (paddleocr) are loaded lazily inside PrintedTableAdapter on first
    call.  This class itself imports the two adapters unconditionally at module
    level — but both adapters defer their heavy imports to first use.
    """

    def __init__(self) -> None:
        # DigitalTextExtractionAdapter has no heavy deps — safe to import now.
        from reconciliation.adapters.pdf.digital_text_extractor import (  # noqa: PLC0415
            DigitalTextExtractionAdapter,
        )
        # PrintedTableAdapter defers PaddleOCR load until first OCR call.
        from reconciliation.adapters.ocr.paddle_table import (  # noqa: PLC0415
            PrintedTableAdapter,
        )

        self._declared_adapter = DigitalTextExtractionAdapter()
        self._ocr_adapter = PrintedTableAdapter()

    # ------------------------------------------------------------------
    # ExtractionPort interface
    # ------------------------------------------------------------------

    def extract_declared(self, text: str) -> list[MaterialLine]:
        """Parse declared material list from embedded digital text (no OCR)."""
        return self._declared_adapter.extract_declared(text)

    def extract_printed_table(self, image: bytes) -> list[MaterialLine]:
        """Extract material+quantity rows from a guía image via OCR."""
        return self._ocr_adapter.extract_printed_table(image)

    # ------------------------------------------------------------------
    # DeclaredExtractorPort interface (higher-level; used by pipeline Stage 4)
    # ------------------------------------------------------------------

    def extract_registro_from_detail_page(self, text: str, source_page: int):  # type: ignore[override]
        """Delegate to DigitalTextExtractionAdapter.extract_registro_from_detail_page."""
        return self._declared_adapter.extract_registro_from_detail_page(text, source_page)

    def extract_registro_from_proto_page(self, text: str, source_page: int):  # type: ignore[override]
        """Delegate to DigitalTextExtractionAdapter.extract_registro_from_proto_page."""
        return self._declared_adapter.extract_registro_from_proto_page(text, source_page)

    # ------------------------------------------------------------------
    # Runtime protocol check
    # ------------------------------------------------------------------

    def __instancecheck_protocol__(self) -> bool:
        return isinstance(self, ExtractionPort)


# ---------------------------------------------------------------------------
# Section ↔ Registro correlation helpers
# ---------------------------------------------------------------------------


def build_page_to_registro_map(
    contents_offsets: dict[str, int],
    total_pages: int,
    doc_source=None,  # type: ignore[assignment]  # DocumentSourcePort | None
    declared_extractor=None,  # type: ignore[assignment]  # DigitalTextExtractionAdapter | None
) -> dict[int, str | None]:
    """Build a 0-based page → registro_numero lookup from Contents offsets.

    The PDF Contents page provides a mapping like:
        {"4252": 3, "4253": 7, ...}  (1-based start pages, Contents system IDs)

    This function computes the page *range* for each section and resolves the
    true *Description numero* (e.g. "232") by parsing the first DECLARED page
    within each section's range.

    EXT-018 (rev-2) invariant: a Contents/section ID (e.g. "4252") MUST NEVER
    be emitted as a registro numero.  When derivation fails or the candidate
    matches the section-ID predicate, ``None`` is stored for those pages;
    the caller is responsible for routing them to the unresolved_guias bucket.

    Args:
        contents_offsets:   Dict[contents_id_str → 1-based start page].
        total_pages:        Total PDF page count (for the last section's end).
        doc_source:         Optional DocumentSourcePort.  Required for numero
                            derivation.  When None, derivation is skipped and
                            section IDs are guarded by the section-ID predicate
                            before being stored.
        declared_extractor: Optional DigitalTextExtractionAdapter (or duck-type
                            compatible).  Required for numero derivation.

    Returns:
        Dict[0-based page index → registro_numero string or None].
        None means the Registro N° could not be reliably derived; the page
        appears in the unresolved_guias bucket for human review.

    Notes:
        - The mapping is best-effort: a GUIA page outside all known ranges
          stays untagged (registro=None) and surfaces as UNCLASSIFIED.
        - Pages before the first section are unmapped.
    """
    if not contents_offsets:
        return {}

    # Sort sections by their 1-based start page to compute end boundaries.
    sorted_sections = sorted(contents_offsets.items(), key=lambda kv: kv[1])

    # Build page ranges first (contents_id → (start_0, end_0_exclusive))
    ranges: list[tuple[str, int, int]] = []
    for i, (contents_id, start_1based) in enumerate(sorted_sections):
        start_0 = start_1based - 1  # convert to 0-based
        if i + 1 < len(sorted_sections):
            end_0 = sorted_sections[i + 1][1] - 1
        else:
            end_0 = total_pages  # exclusive
        ranges.append((contents_id, start_0, end_0))

    # Resolve Description numero for each section by scanning pages in range.
    # The PROTO page is the canonical source; fall back to DETAIL.
    # EXT-018: if derivation fails or yields a section ID → None (never the section ID).
    can_derive = doc_source is not None and declared_extractor is not None

    page_to_registro: dict[int, str | None] = {}

    for contents_id, start_0, end_0 in ranges:
        if can_derive:
            numero: str | None = _derive_numero(
                contents_id, start_0, end_0, doc_source, declared_extractor
            )
        else:
            # No derivation possible — guard the raw contents_id.
            # EXT-018: if it looks like a section ID, return None.
            if is_section_id(contents_id):
                logger.warning(
                    "build_page_to_registro_map: contents_id=%r is a section ID and "
                    "derivation is unavailable; pages mapped to None (UNRESOLVED)",
                    contents_id,
                )
                numero = None
            else:
                numero = contents_id

        for page_idx in range(start_0, end_0):
            page_to_registro[page_idx] = numero

    return page_to_registro


def _derive_numero(
    contents_id: str,
    start_0: int,
    end_0: int,
    doc_source,  # type: ignore[assignment]  # DocumentSourcePort
    declared_extractor,  # type: ignore[assignment]  # DigitalTextExtractionAdapter duck
) -> str | None:
    """Scan pages in [start_0, end_0) to find and parse the Description numero.

    Priority:
        1. First PROTOCOLO page found in the range (canonical).
        2. First FORM DETAIL page found in the range.
        3. Fall back to None (UNRESOLVED) — NEVER return the Contents/section ID.

    EXT-018 (rev-2): the function MUST NOT return a value for which
    ``is_section_id(value)`` is ``True``.  If derivation fails, returns ``None``
    so the caller can route the pages to the unresolved_guias bucket.

    This is a best-effort scan: failure on any page is logged and skipped.

    Returns:
        Description numero string (e.g. ``"232"``) on success, or ``None`` on
        failure.  Never returns the Contents ID (e.g. ``"4252"``).
    """
    proto_numero: str | None = None
    detail_numero: str | None = None

    for page_idx in range(start_0, end_0):
        try:
            text = doc_source.page_text(page_idx)
        except Exception as exc:  # noqa: BLE001
            logger.debug("_derive_numero: page_text(%d) failed: %s", page_idx, exc)
            continue

        if not text:
            continue

        # Protocolo page — highest priority
        if proto_numero is None and "PROTOCOLO DE RECEPCI" in text:
            try:
                reg = declared_extractor.extract_registro_from_proto_page(text, page_idx)
                if reg is not None:
                    proto_numero = reg.numero
                    break  # proto found; no need to continue scanning
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "_derive_numero: extract_registro_from_proto_page(%d) failed: %s",
                    page_idx,
                    exc,
                )

        # Form Detail page — secondary
        if detail_numero is None and ("Form detail" in text or "Form date" in text):
            try:
                reg = declared_extractor.extract_registro_from_detail_page(text, page_idx)
                if reg is not None:
                    detail_numero = reg.numero
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "_derive_numero: extract_registro_from_detail_page(%d) failed: %s",
                    page_idx,
                    exc,
                )

    if proto_numero is not None:
        # Guard: derived numero must not itself be a section ID.
        if is_section_id(proto_numero):
            logger.warning(
                "_derive_numero: contents_id=%r → proto_numero=%r looks like a section ID; "
                "returning None (UNRESOLVED:%s)",
                contents_id,
                proto_numero,
                contents_id,
            )
            return None
        logger.debug(
            "_derive_numero: contents_id=%r → numero=%r (from PROTO)", contents_id, proto_numero
        )
        return proto_numero

    if detail_numero is not None:
        if is_section_id(detail_numero):
            logger.warning(
                "_derive_numero: contents_id=%r → detail_numero=%r looks like a section ID; "
                "returning None (UNRESOLVED:%s)",
                contents_id,
                detail_numero,
                contents_id,
            )
            return None
        logger.debug(
            "_derive_numero: contents_id=%r → numero=%r (from DETAIL)", contents_id, detail_numero
        )
        return detail_numero

    # EXT-018: do NOT fall back to contents_id — it is a section ID, not a Registro N°.
    logger.warning(
        "_derive_numero: contents_id=%r → no parseable DECLARED page; "
        "returning None (UNRESOLVED:%s) — guía will appear in unresolved_guias bucket",
        contents_id,
        contents_id,
    )
    return None


# ---------------------------------------------------------------------------
# Pipeline factory
# ---------------------------------------------------------------------------


def build_pipeline(
    pdf_path: Path,
    config: AppConfig,
    run_id: str | None = None,
) -> tuple[ReconciliationPipeline, RunContext, dict[int, str | None]]:
    """Instantiate all adapters, build the pipeline, and return a RunContext.

    This is the single factory that knows about every concrete adapter.

    Args:
        pdf_path: Absolute path to the source PDF (read-only).
        config:   Loaded AppConfig (env + yaml resolved).
        run_id:   Optional explicit run ID for resume/restart scenarios.

    Returns:
        A 3-tuple of (pipeline, ctx, page_to_registro) where:
        - pipeline:          Ready-to-run ReconciliationPipeline.
        - ctx:               Per-run RunContext owning output paths.
        - page_to_registro:  Pre-computed 0-based page→registro_numero map
                             (values are Description numeros, e.g. "232", or None
                             for unresolved pages — EXT-018).
                             May be empty if Contents page is absent.

    Notes on lazy construction:
        DeskewAdapter, PrintedTableAdapter, and all vision adapters defer their
        heavy ML/SDK initialisation to first use.  This function will NOT crash
        at import time even if paddleocr/anthropic/openai are not installed.
        The crash is deferred to the first pipeline.run() call that exercises
        those code paths.
    """
    # --- PDF source adapter (PyMuPDF — always available) ---
    from reconciliation.adapters.pdf.pymupdf_source import (  # noqa: PLC0415
        PdfStructureAdapter,
    )

    doc_source = PdfStructureAdapter(pdf_path)

    # --- Extraction adapter (composite: digital + OCR) ---
    # Instantiated early so we can pass the declared_extractor to build_page_to_registro_map.
    extractor = CompositeExtractionAdapter()

    # --- Section ↔ Registro correlation (C-3 fix) ---
    # Pass doc_source + extractor so the map is keyed on Description numeros ("232"),
    # not Contents IDs ("4252").
    try:
        contents_offsets = doc_source.contents_offsets()
        total_pages = doc_source.page_count()
        page_to_registro = build_page_to_registro_map(
            contents_offsets,
            total_pages,
            doc_source=doc_source,
            declared_extractor=extractor._declared_adapter,
        )
        resolved_values = [v for v in page_to_registro.values() if v is not None]
        unresolved_count = sum(1 for v in page_to_registro.values() if v is None)
        logger.debug(
            "build_pipeline: %d sections mapped across %d pages (resolved keys: %s; "
            "unresolved pages: %d)",
            len(contents_offsets),
            total_pages,
            sorted(set(resolved_values))[:5],
            unresolved_count,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("build_pipeline: contents_offsets failed (%s); registro map empty", exc)
        page_to_registro = {}

    # --- Vision adapter (provider-agnostic factory; lazy SDK load) ---
    from reconciliation.adapters.vision.factory import build_vision_adapter  # noqa: PLC0415

    vision = build_vision_adapter(config)

    # --- Deskew adapter (lazy PaddleOCR; None when ML deps absent at import time) ---
    # The DeskewAdapter itself loads PaddleOCR lazily on first call; it won't
    # crash here.  We pass it to the pipeline so H-5 wiring exists; if PaddleOCR
    # is absent at call time, DeskewAdapter._unavailable is set and it fast-returns.
    try:
        from reconciliation.adapters.ocr.paddle_deskew import DeskewAdapter  # noqa: PLC0415
        deskew: object | None = DeskewAdapter()
    except Exception as exc:  # noqa: BLE001
        logger.warning("build_pipeline: DeskewAdapter import failed (%s); deskew disabled", exc)
        deskew = None

    # --- Run context ---
    ctx = RunContext(
        pdf_path=pdf_path,
        output_base=config.output_dir,
        run_id=run_id,
    )

    # --- Pipeline (C-4 + H-5 fix: pass page_to_registro and deskew) ---
    pipeline = ReconciliationPipeline(
        doc_source=doc_source,
        extractor=extractor,
        vision=vision,
        config=config,
        page_to_registro=page_to_registro,
        deskew=deskew,  # type: ignore[arg-type]
    )

    return pipeline, ctx, page_to_registro


def build_review_service(
    ctx: RunContext,
    pipeline_result: "ReconciliationPipeline | None" = None,
) -> ReviewService:
    """Build a ReviewService from a completed pipeline result or sidecar.

    If a prior sidecar exists (restart path), replays persisted edits.
    Otherwise returns a fresh ReviewService from the extraction cache.

    Args:
        ctx:             RunContext for this run.
        pipeline_result: Unused — kept for API symmetry; extraction is loaded
                         directly from ctx.read_extraction_cache().

    Returns:
        A ReviewService with edits replayed from sidecar (or empty).
    """
    from reconciliation.domain.models import (  # noqa: PLC0415
        GuiaDeRemision,
        ReconciliationRow,
        Registro,
    )

    cache = ctx.read_extraction_cache()
    declared = [Registro.model_validate(r) for r in cache.get("declared", [])]
    guias = [GuiaDeRemision.model_validate(g) for g in cache.get("guias", [])]
    rows = [ReconciliationRow.model_validate(r) for r in cache.get("rows", [])]

    return ReviewService.restore_from_sidecar(
        declared=declared,
        guias=guias,
        rows=rows,
        ctx=ctx,
    )
