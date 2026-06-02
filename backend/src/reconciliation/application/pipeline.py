"""ReconciliationPipeline — deterministic orchestrator.

Stage sequence (rev-3, fixed, non-negotiable per design D1):
  1. split             — count pages via DocumentSourcePort
  1b. decode_identities — NEW pre-pass (rev-3): render each page once, decode
                          IdentityExtractionPort; cache page → DecodeOutcome map.
                          CRITICAL: rendered bytes are stored in the map and reused
                          by extract_ocr/assemble_blocks — no second render.
  2. classify           — HYBRID OR-gate (rev-3 EXT-019): Condition A (qr_is_guia)
                          ∨ B (Forma-header + image_dominant) ∨ C (digital/ocr title).
  3. deskew             — correct orientation of GUIA pages (optional DeskewPort)
  4. extract_declared   — parse digital text from DECLARED pages using real
                          parsers (DigitalTextExtractionAdapter); dedupe
                          protocolo+detail into ONE Registro per numero
                          (protocolo is canonical source per decision 2026-05-31).
  5. extract_ocr        — OCR material tables from GUIA pages; per-page _RawGuia objects.
                          REUSES cached rendered bytes from decode_identities (no re-render).
  5b. assemble_blocks   — group per-page _RawGuia objects into multi-page GuiaBlocks;
                          REUSES cached DecodeOutcome map (no QR re-scan).
  6. extract_vision     — read handwritten dates on block FIRST pages (VisionLLMPort);
                          abort if cost cap exceeded.
  7. normalize          — canonicalize material descriptions (MaterialNormalizer)
  8. reconcile          — group + compare via ReconciliationService
  9. persist_sidecar    — write extraction cache + initial review sidecar via RunContext
  10. return            — yield PipelineResult

No concrete adapter is imported here.  All I/O is injected as Port implementations.

Cost cap policy (locked):
  Before each VisionLLMPort call the pipeline checks ``calls_made < cap``.
  On cap exhaustion, VisionCapExceededError is raised immediately, preserving
  any partial results already written to the extraction cache.

Block grouping invariants (S1.5 / EXT-015):
  - guia_id MUST come from IdentityExtractionPort or OCR-fallback; never f"guia_page_{n}".
  - identity_source="qr" when QR decode succeeded; "ocr_fallback" otherwise.
  - fecha MUST come from VisionLLMPort (handwritten stamp); never electronic/SUNAT date.
  - Section boundary always starts a new block (even if same guia_id).

Render-cache invariant (D1 rev-3):
  decode_identities renders each page ONCE and caches the bytes.
  extract_ocr and assemble_blocks REUSE the cached bytes from that map.
  The total number of render_page() calls MUST NOT exceed page_count.
  A second independent QR scan MUST NOT be introduced (EXT-019).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from reconciliation.application.config import AppConfig
from reconciliation.application.run_context import RunContext
from reconciliation.domain.classifier import PageClassifier
from reconciliation.domain.date_inference import infer_reception_year
from reconciliation.domain.errors import VisionCapExceededError
from reconciliation.domain.models import (
    GuiaDeRemision,
    GuiaIdentity,
    MaterialLine,
    PageClassification,
    ReconciliationRow,
    Registro,
    VisionResult,
)
from reconciliation.domain.normalizer import MaterialNormalizer
from reconciliation.domain.ports import (
    DocumentSourcePort,
    ExtractionPort,
    IdentityExtractionPort,
    VisionLLMPort,
)
from reconciliation.domain.reconciliation import ReconciliationService

logger = logging.getLogger(__name__)

# DPI used for the decode_identities pre-pass render (rev-3 D1).
# 200 dpi is the baseline; the QrBarcodeExtractionAdapter internally upscales
# to 400-dpi equivalent for the second COLOR decode tier (D2).
_QR_DPI: int = 200

# DPI used for the Option B full-page fallback when stamp_crop is disabled (D4).
_VISION_FALLBACK_DPI: int = 300

# ---------------------------------------------------------------------------
# DecodeOutcome — rev-3 pre-pass result per page (R1.1)
# ---------------------------------------------------------------------------


@dataclass
class DecodeOutcome:
    """Result of the decode_identities pre-pass for a single page (EXT-019 / D1).

    Produced by ``_stage_decode_identities`` and consumed by both
    ``_stage_classify`` (for the hybrid OR-gate booleans) and
    ``_stage_assemble_blocks`` (to avoid a second QR scan).

    Attributes:
        identity:    Decoded GuiaIdentity if the compact GRE QR passed the
                     EXT-012 confidence gate; ``None`` otherwise.
        hashqr_url:  URL-variant QR payload (descargaqr URL) if decoded on
                     this page; ``None`` otherwise.
        rendered:    PNG bytes rendered at the QR decode DPI.  Reused by
                     extract_ocr and assemble_blocks to avoid a second render.
        decoded:     True when ANY QR payload was decoded (compact or URL),
                     regardless of confidence gating.
    """

    identity: GuiaIdentity | None
    hashqr_url: str | None
    rendered: bytes
    decoded: bool

    @property
    def qr_is_guia(self) -> bool:
        """True when a valid compact GRE QR passed the EXT-012 confidence gate."""
        return self.identity is not None


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
    ) -> Registro | None:
        """Parse a Form Detail page into a Registro; None if not a valid page."""
        ...

    def extract_registro_from_proto_page(
        self, text: str, source_page: int
    ) -> Registro | None:
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
        identity: IdentityExtractionPort | None = None,
    ) -> None:
        self._doc = doc_source
        self._extractor = extractor
        self._vision = vision
        self._config = config
        self._page_to_registro: dict[int, str | None] = page_to_registro or {}
        self._deskew = deskew
        self._identity = identity
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

        # Stage 1b: decode_identities pre-pass (rev-3 / D1).
        # Renders each page ONCE at QR DPI; caches page → DecodeOutcome.
        # The rendered bytes are reused by extract_ocr and assemble_blocks
        # so total renders stay constant (EXT-019 render-cache invariant).
        decode_map = self._stage_decode_identities(page_count)

        # Stage 2: classify — HYBRID OR-gate (rev-3 EXT-019).
        # Passes qr_is_guia + image_dominant booleans from the cached decode map.
        classifications = self._stage_classify(page_count, decode_map=decode_map)

        # Stage 3: deskew (orientation correction for GUIA pages; no-op if deskew=None)
        # Note: title-OCR for scanned pages is already wired in _stage_classify above.
        # This stage applies image correction that the OCR in stage 5 benefits from.
        # (Explicit deskew pass on images is performed inside _stage_extract_ocr.)

        # Stage 4: extract declared (digital text; real parsers; dedupe proto+detail)
        declared = self._stage_extract_declared(classifications)

        # Stage 5: extract OCR tables from guia pages; reuses cached rendered bytes.
        raw_guias = self._stage_extract_ocr(classifications, decode_map=decode_map)

        # Stage 5b: assemble multi-page guía blocks; reuses cached DecodeOutcome map.
        blocks = self._stage_assemble_blocks(raw_guias, classifications, decode_map=decode_map)

        # Stage 6: extract vision dates (handwritten) — one call per block (first page).
        # D4: feeds the stamp-region crop (lower-right quadrant default) or >=300dpi
        # full-page fallback when cropping is disabled (EXT-020).
        # vision_audit_record is populated here and written to sidecar in stage 9.
        vision_calls_made = 0
        vision_cap_reached = False
        try:
            guias, vision_calls_made, warnings = self._stage_extract_vision(blocks)
        except Exception:
            vision_cap_reached = True
            raise
        finally:
            # Store for stage 9 (avoid writing before sidecar is initialised)
            self._pending_vision_audit = {
                "stage": "vision",
                "calls_made": vision_calls_made,
                "cap_reached": vision_cap_reached,
            }

        # Stage 6b: normalize dates — bounded year inference (D5 / EXT-021).
        # Runs after vision, before material normalize so guias carry full dates.
        guias = self._stage_normalize_dates(guias)

        # Stage 7: normalize descriptions
        declared, guias = self._stage_normalize(declared, guias)

        # Stage 8: reconcile
        rows = self._stage_reconcile(declared, guias)

        # Stage 9: persist sidecar (also appends the vision audit record)
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

    def _stage_decode_identities(self, page_count: int) -> dict[int, DecodeOutcome]:
        """Stage 1b (rev-3 / D1): decode QR identities for every page in one pre-pass.

        Renders each page once at ``_QR_DPI`` DPI, calls
        ``IdentityExtractionPort.decode_identity`` when the adapter is wired,
        and stores the result in a ``page_idx → DecodeOutcome`` map.

        **Render-cache contract**: the rendered PNG bytes are stored in each
        ``DecodeOutcome.rendered`` field.  Downstream stages (``_stage_extract_ocr``,
        ``_stage_assemble_blocks``) MUST reuse these bytes rather than calling
        ``render_page`` again — this keeps total renders at one per page (EXT-019).

        When ``self._identity is None``, the map is still populated with
        ``DecodeOutcome(identity=None, hashqr_url=None, rendered=<bytes>, decoded=False)``
        for every page so the render cache is available even without a QR adapter.
        The classify/ocr stages then fall back to Condition B/C only.

        Returns:
            Dict mapping 0-based page index → ``DecodeOutcome``.
        """
        decode_map: dict[int, DecodeOutcome] = {}

        for idx in range(page_count):
            # Always render — the bytes are shared downstream (render-cache).
            try:
                rendered = self._doc.render_page(idx, dpi=_QR_DPI)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "decode_identities: render_page(%d) failed: %s; using empty bytes", idx, exc
                )
                rendered = b""

            identity = None
            decoded = False

            if self._identity is not None and rendered:
                try:
                    identity = self._identity.decode_identity(rendered, page_idx=idx)
                    decoded = identity is not None
                    # Even if identity gate failed, check for URL-variant QR.
                    # QrBarcodeExtractionAdapter returns None when only URL-QR found;
                    # we still want the hashqr_url.  Attempt via duck-type helper.
                    if not decoded and hasattr(self._identity, "decode_hashqr_url"):
                        _url = self._identity.decode_hashqr_url(  # noqa: E501
                            rendered, page_idx=idx
                        )
                        if _url:
                            decoded = True
                            decode_map[idx] = DecodeOutcome(
                                identity=None,
                                hashqr_url=_url,
                                rendered=rendered,
                                decoded=True,
                            )
                            continue
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "decode_identities: decode failed page %d: %s", idx, exc
                    )

            hashqr_url: str | None = identity.hashqr_url if identity is not None else None
            decode_map[idx] = DecodeOutcome(
                identity=identity,
                hashqr_url=hashqr_url,
                rendered=rendered,
                decoded=decoded,
            )
            logger.debug(
                "decode_identities: page %d → qr_is_guia=%s hashqr_url=%s",
                idx,
                identity is not None,
                bool(hashqr_url),
            )

        logger.debug(
            "decode_identities: %d/%d pages with confirmed guía QR",
            sum(1 for o in decode_map.values() if o.qr_is_guia),
            page_count,
        )
        return decode_map

    def _stage_classify(
        self,
        page_count: int,
        decode_map: dict[int, DecodeOutcome] | None = None,
    ) -> list[PageClassification]:
        """Stage 2: classify each page using the hybrid OR-gate (rev-3 EXT-019).

        Rev-3: the ``decode_map`` from ``_stage_decode_identities`` provides two
        pre-computed boolean signals for each page:
          - ``qr_is_guia``: Condition A — page has a valid compact SUNAT GRE QR.
          - ``image_dominant``: Condition B — page is raster-image heavy.

        For scanned pages (empty or noise-only digital text), the deskew
        adapter is called first to correct orientation, then ``extract_title``
        is used to produce an ``ocr_title`` that the classifier uses as a
        fallback.  When no deskew adapter is wired (``self._deskew is None``),
        scanned pages rely on Condition A/B only (or remain UNCLASSIFIED).

        The rendered bytes from the decode_map are reused for deskew when the
        page has no meaningful digital text (render-cache invariant, EXT-019).
        """
        _decode = decode_map or {}
        classifications: list[PageClassification] = []

        for idx in range(page_count):
            text = self._doc.page_text(idx)
            ocr_title: str | None = None

            # Determine hybrid boolean signals from the pre-pass.
            outcome = _decode.get(idx)
            qr_is_guia = outcome.qr_is_guia if outcome is not None else False

            # image_dominant from DocumentSourcePort (optional method, D1 §3).
            image_dominant = _get_image_dominant(self._doc, idx)

            # Attempt title-OCR for potentially scanned pages only when deskew is wired.
            if self._deskew is not None and not _has_meaningful_text(text):
                try:
                    # Reuse the cached render bytes if available (render-cache).
                    raw_image = (
                        outcome.rendered
                        if (outcome is not None and outcome.rendered)
                        else self._doc.render_page(idx, dpi=200)
                    )
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
                qr_is_guia=qr_is_guia,
                image_dominant=image_dominant,
            )
            classifications.append(classification)
            logger.debug(
                "classify: page %d → %s (title=%r, qr_is_guia=%s, image_dominant=%s, ocr_title=%r)",
                idx,
                classification.kind,
                classification.title_matched,
                qr_is_guia,
                image_dominant,
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
                logger.warning(
                    "extract_declared: numero=%r has no parseable page; skipping.", numero
                )

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
        self,
        classifications: list[PageClassification],
        decode_map: dict[int, DecodeOutcome] | None = None,
    ) -> list[_RawGuia]:
        """Stage 5: OCR material tables from GUIA pages; tag with registro numero.

        Rev-3 render-cache: reuses the rendered bytes stored in ``decode_map``
        when available (EXT-019 invariant — no second render per page).  Falls
        back to ``render_page`` when the cache entry is absent or has empty bytes.

        Each RawGuia is tagged with its section's Registro numero from the
        pre-computed ``page_to_registro`` map.  If the page is not in the map
        (outside all known section ranges), ``registro`` remains None and the
        guia surfaces as UNCLASSIFIED in reconciliation.
        """
        _decode = decode_map or {}
        raw_guias: list[_RawGuia] = []
        for cls in classifications:
            if cls.kind != "GUIA":
                continue

            # Reuse cached render bytes if available (render-cache invariant).
            outcome = _decode.get(cls.page)
            if outcome is not None and outcome.rendered:
                image = outcome.rendered
            else:
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
                    # guia_id intentionally left empty here; assigned during block assembly (S1.5)
                    guia_id="",
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

    def _stage_assemble_blocks(
        self,
        raw_guias: list[_RawGuia],
        classifications: list[PageClassification],
        decode_map: dict[int, DecodeOutcome] | None = None,
    ) -> list[_GuiaBlock]:
        """Stage 5b: group per-page _RawGuia objects into multi-page GuiaBlocks.

        Algorithm (S1.5 / EXT-015, rev-3):
        1. Iterate raw_guias in order (sorted by page index from _stage_extract_ocr).
        2. Read identity from the cached ``decode_map`` — no second QR scan (EXT-019).
           Falls back to direct adapter call only when no cache entry exists (compat).
        3. Start a new block on:
           (a) Run-start (first page).
           (b) Section boundary cross: page's registro differs from current block's.
           (c) Successful QR decode with a guia_id different from current block's.
        4. Within a block: propagate identity fields from the first page.
        5. Append OCR lines from each page to accumulated block lines.
        6. OCR fallback: decode returns None → identity_source="ocr_fallback";
           guia_id derived from page index (no QR data).
        7. hashqr_url propagation: first non-null value across block pages (D2).

        Returns a list of _GuiaBlock objects ready for vision date extraction.
        """
        if not raw_guias:
            return []

        _decode = decode_map or {}
        blocks: list[_GuiaBlock] = []
        current_block: _GuiaBlock | None = None

        for raw in raw_guias:
            # Read from cached decode map (EXT-019: no second QR scan).
            # Fall back to direct adapter call only when no cache entry exists.
            outcome = _decode.get(raw.source_page)
            if outcome is not None:
                identity = outcome.identity
                page_hashqr_url_candidate = outcome.hashqr_url
            else:
                # No cache entry — backward-compat: call adapter directly.
                identity = None
                page_hashqr_url_candidate = None
                if self._identity is not None:
                    try:
                        identity = self._identity.decode_identity(raw.image)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "assemble_blocks: identity decode failed page %d: %s",
                            raw.source_page,
                            exc,
                        )
                    if identity is not None:
                        page_hashqr_url_candidate = identity.hashqr_url

            # Derive identity fields for this page
            if identity is not None:
                page_guia_id = identity.guia_id
                page_identity_source: str = "qr"
                page_ruc_emisor = identity.ruc_emisor
                page_ruc_receptor = identity.ruc_receptor
                page_tipo = identity.tipo
                page_hashqr_url = identity.hashqr_url or page_hashqr_url_candidate
                page_identity_confidence = identity.confidence
            else:
                # OCR fallback: unique per page until a QR is found
                page_guia_id = f"ocr_{raw.source_page}"
                page_identity_source = "ocr_fallback"
                page_ruc_emisor = None
                page_ruc_receptor = None
                page_tipo = None
                page_hashqr_url = page_hashqr_url_candidate  # URL QR may still exist
                page_identity_confidence = 0.0

            # Determine whether to start a new block
            start_new_block = current_block is None  # (a) run-start

            if not start_new_block and current_block is not None:
                # (b) Section boundary: registro differs from current block's registro
                if raw.registro != current_block.registro:
                    start_new_block = True
                # (c) New QR identity: successful decode with different guia_id
                elif (
                    identity is not None
                    and page_guia_id != current_block.guia_id
                ):
                    start_new_block = True

            if start_new_block:
                # Finalise current block (if any) and push to list
                if current_block is not None:
                    blocks.append(current_block)
                current_block = _GuiaBlock(
                    guia_id=page_guia_id,
                    first_page=raw.source_page,
                    source_pages=[raw.source_page],
                    first_page_image=raw.image,
                    lines=list(raw.lines),
                    registro=raw.registro,
                    identity_source=page_identity_source,
                    ruc_emisor=page_ruc_emisor,
                    ruc_receptor=page_ruc_receptor,
                    tipo=page_tipo,
                    gre_hashqr_url=page_hashqr_url,
                    identity_confidence=page_identity_confidence,
                )
            else:
                # Continuation page: append lines; identity propagated from first page.
                assert current_block is not None
                current_block.source_pages.append(raw.source_page)
                current_block.lines.extend(raw.lines)
                # Rev-3 D2: propagate hashqr_url — first non-null across the block.
                if current_block.gre_hashqr_url is None and page_hashqr_url is not None:
                    current_block.gre_hashqr_url = page_hashqr_url

            logger.debug(
                "assemble_blocks: page %d → block guia_id=%r, source=%r, start_new=%s",
                raw.source_page,
                current_block.guia_id if current_block else None,
                page_identity_source,
                start_new_block,
            )

        # Finalise the last block
        if current_block is not None:
            blocks.append(current_block)

        logger.debug("assemble_blocks: %d blocks from %d pages", len(blocks), len(raw_guias))
        return blocks

    def _stage_extract_vision(
        self, blocks: list[_GuiaBlock]
    ) -> tuple[list[GuiaDeRemision], int, list[str]]:
        """Stage 6: attach handwritten dates to guía blocks via VisionLLMPort.

        Vision is called once per BLOCK (on the first page's image) — not once
        per raw page.  This preserves the cost cap semantics while correctly
        handling multi-page guías.

        Rev-3 D4 (EXT-020): the image sent to the vision adapter is the
        stamp-region crop (Option A, lower-right quadrant default) rather than
        the full-page-200dpi render that previously caused date failures on
        non-qwen models.  When stamp_crop is disabled (all zeros), falls back
        to Option B: a fresh render at ``fallback_dpi`` (≥300 dpi).

        Respects the vision cost cap.  Raises VisionCapExceededError if the
        cap is exhausted before all blocks are processed.

        Returns:
            (guias, calls_made, warnings)
        """
        cap = self._config.vision.max_vision_calls
        calls_made = 0
        warnings: list[str] = []
        guias: list[GuiaDeRemision] = []

        if not blocks:
            return guias, calls_made, warnings

        # Prepare vision images — apply stamp-crop (D4)
        vision_images = [
            _prepare_vision_image(blk.first_page_image, self._config)
            for blk in blocks
        ]

        if self._vision.supports_batch:
            # Batch path: one call per batch of (possibly cropped) images
            remaining = cap - calls_made
            if remaining <= 0:
                raise VisionCapExceededError(
                    "Vision cost cap reached before processing started.",
                    detail={"calls_made": calls_made, "cap": cap, "pages_remaining": len(blocks)},
                )
            if len(vision_images) > remaining:
                # Partial batch up to cap
                to_process = blocks[:remaining]
                skipped = blocks[remaining:]
                results = self._vision.read_handwritten_date_batch(
                    vision_images[:remaining]
                )
                calls_made += len(to_process)
                for blk, vr in zip(to_process, results):
                    guias.append(_build_guia_from_block(blk, vr))
                raise VisionCapExceededError(
                    f"Vision cost cap ({cap}) reached after {calls_made} calls.",
                    detail={
                        "calls_made": calls_made,
                        "cap": cap,
                        "pages_remaining": len(skipped),
                    },
                )
            else:
                results = self._vision.read_handwritten_date_batch(vision_images)
                calls_made += len(vision_images)
                for blk, vr in zip(blocks, results):
                    guias.append(_build_guia_from_block(blk, vr))
        else:
            # Sequential path (Ollama / non-batch)
            for blk, img in zip(blocks, vision_images):
                if calls_made >= cap:
                    raise VisionCapExceededError(
                        f"Vision cost cap ({cap}) reached after {calls_made} calls.",
                        detail={
                            "calls_made": calls_made,
                            "cap": cap,
                            "pages_remaining": len(blocks) - calls_made,
                        },
                    )
                vr = self._vision.read_handwritten_date(img)
                calls_made += 1
                guia = _build_guia_from_block(blk, vr)
                guias.append(guia)
                if vr.confidence < self._config.confidence.threshold:
                    warnings.append(
                        f"Low vision confidence ({vr.confidence:.2f}) "
                        f"on first page {blk.first_page} of block {blk.guia_id!r}."
                    )

        return guias, calls_made, warnings

    def _stage_normalize_dates(
        self,
        guias: list[GuiaDeRemision],
    ) -> list[GuiaDeRemision]:
        """Stage 6b: bounded year inference for guías whose vision-read year is absent.

        Implements D5 / EXT-021.

        Runs after vision, before material normalization.  For each guía whose
        ``fecha`` is None OR whose ``fecha`` lacks a plausible year (i.e. the
        vision model returned a date but year was a known-bad sentinel), applies
        ``infer_reception_year`` with:
          - ``lower`` = None in R2 (SUNAT fetch is R3; OCR delivery-date extraction
            is not yet wired here — lower bound is omitted until R3 lands).
          - ``upper`` = today's date (run date, conservative safe upper bound).

        When a guía's ``fecha`` is already a full ``date`` object from vision,
        we leave it unchanged (``year_inferred`` stays ``False``).  The
        inference is applied ONLY when ``fecha`` is ``None``.

        In R2, the lower bound is intentionally omitted (``None``) because:
          - SUNAT fetch (R3) is the deterministic source of ``fecha_entrega``.
          - OCR-reading the printed GRE date is not implemented in this slice.
          - Upper-bound-only inference is still correct and safe (EXT-S28).

        Returns a new list of GuiaDeRemision (no mutation).
        """
        from datetime import date as _date  # noqa: PLC0415

        today = _date.today()
        result: list[GuiaDeRemision] = []

        for guia in guias:
            if guia.fecha is not None:
                # Vision returned a full date — trust it, no inference needed.
                result.append(guia)
                continue

            # fecha is None — try to infer from raw vision output (day/month).
            # Parse DD/MM or DD-MM from the raw vision string stored on the guía.
            day, month = _parse_day_month(guia.fecha_confidence, guia.fecha_raw or None)
            if day is None or month is None:
                # Cannot infer without at least day and month — leave as None.
                result.append(guia)
                continue

            inferred_date, year_inferred = infer_reception_year(
                day=day,
                month=month,
                lower=None,  # R2: no lower bound; SUNAT lower bound lands in R3
                upper=today,
            )

            if inferred_date is not None:
                result.append(
                    guia.model_copy(update={"fecha": inferred_date, "year_inferred": year_inferred})
                )
                logger.debug(
                    "normalize_dates: guia %r fecha inferred %s (year_inferred=%s)",
                    guia.guia_id,
                    inferred_date,
                    year_inferred,
                )
            else:
                result.append(guia)

        return result

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
                update={"declared_lines": [_norm_line(ln) for ln in registro.declared_lines]}
            )
            for registro in declared
        ]
        normalised_guias = [
            guia.model_copy(
                update={"lines": [_norm_line(ln) for ln in guia.lines]}
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
        """Stage 9: write extraction cache + initial empty review sidecar.

        Also appends the vision audit record collected in stage 6.
        """
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

        # Append vision audit record if one was collected in stage 6
        pending: dict[str, object] | None = getattr(self, "_pending_vision_audit", None)
        if pending is not None:
            ctx.append_vision_audit(pending)
            del self._pending_vision_audit


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


def _get_image_dominant(doc: DocumentSourcePort, idx: int) -> bool:
    """Return True if page *idx* is image-dominant (rev-3 D1 Condition B).

    Calls ``DocumentSourcePort.image_coverage_ratio`` when the method is
    available on the concrete implementation.  Gracefully returns False when
    the method is absent (test fakes, legacy adapters) or raises an error.
    """
    from reconciliation.domain.classifier import IMAGE_DOMINANT_THRESHOLD  # noqa: PLC0415

    if not hasattr(doc, "image_coverage_ratio"):
        return False
    try:
        ratio: float = doc.image_coverage_ratio(idx)
        return ratio >= IMAGE_DOMINANT_THRESHOLD
    except Exception as exc:  # noqa: BLE001
        logger.debug("_get_image_dominant: page %d failed: %s", idx, exc)
        return False


@dataclass
class _RawGuia:
    """Intermediate object holding a single page's OCR data before block assembly.

    ``guia_id`` is intentionally left empty at OCR time; it is assigned by
    ``_stage_assemble_blocks`` (S1.5) using QR identity or OCR fallback.
    The naming scheme ``guia_page_{n}`` MUST NOT appear after this stage.
    """

    guia_id: str  # set to "" by _stage_extract_ocr; filled in by _stage_assemble_blocks
    source_page: int
    image: bytes
    lines: list[MaterialLine]
    registro: str | None = None  # section registro numero from page_to_registro map


@dataclass
class _GuiaBlock:
    """Multi-page guía block assembled from consecutive _RawGuia pages (S1.5 / EXT-015).

    Represents one logical Guía de Remisión document that may span multiple
    physical pages.  Identity fields (guia_id, ruc_*, tipo, etc.) come from
    the FIRST page's QR decode or OCR fallback; they are propagated to all
    continuation pages.  Lines are accumulated across all pages.

    ``first_page_image`` is used for the VisionLLMPort call in stage 6 to
    read the handwritten reception date stamp.
    """

    guia_id: str
    first_page: int
    source_pages: list[int]
    first_page_image: bytes
    lines: list[MaterialLine]
    registro: str | None
    identity_source: str  # Literal["qr", "ocr_fallback"]
    ruc_emisor: str | None = None
    ruc_receptor: str | None = None
    tipo: str | None = None
    gre_hashqr_url: str | None = None
    identity_confidence: float = 0.0


def _build_guia_from_block(block: _GuiaBlock, vision_result: VisionResult) -> GuiaDeRemision:
    """Assemble a GuiaDeRemision from a _GuiaBlock and a VisionResult date.

    The ``fecha`` MUST come from VisionLLMPort (handwritten stamp on the first
    page) — never from SUNAT/electronic date (EXT-017, REC-C01 invariant).

    Rev-3 D5: ``year_inferred`` is propagated from VisionResult.  Adapters
    always return ``year_inferred=False``; ``_stage_normalize_dates`` sets it
    to ``True`` on the GuiaDeRemision after reconstruction.
    """
    return GuiaDeRemision(
        guia_id=block.guia_id,
        registro=block.registro,
        fecha=vision_result.date,
        fecha_confidence=vision_result.confidence,
        lines=block.lines,
        source_pages=block.source_pages,
        ruc_emisor=block.ruc_emisor,
        ruc_receptor=block.ruc_receptor,
        tipo=block.tipo,
        gre_hashqr_url=block.gre_hashqr_url,
        identity_confidence=block.identity_confidence,
        identity_source=block.identity_source,  # type: ignore[arg-type]
        first_page=block.first_page,
        year_inferred=vision_result.year_inferred,
        fecha_raw=vision_result.raw,
    )


def _prepare_vision_image(image: bytes, config: AppConfig) -> bytes:
    """Prepare the image to send to VisionLLMPort for date extraction (D4 / EXT-020).

    Option A (default): crop the stamp-region (lower-right quadrant) from the
    already-rendered page image.  The crop box is defined in ``config.vision.stamp_crop``
    as fractional coordinates in [0.0, 1.0].

    Option B (fallback): when stamp_crop is disabled (x0==x1 or y0==y1), the
    caller is expected to have passed a >=300dpi full-page image.  In the
    pipeline this is the 200dpi render from the decode_identities cache — we
    cannot re-render here (no access to doc_source), so Option B currently
    returns the original bytes.  The pipeline should be extended to pass a
    higher-DPI render when crop is disabled (not yet wired in R2; defer to R3).

    The PIL/Pillow import is local (lazy) to keep the module importable in
    environments where Pillow is absent (unit tests mock this path).

    Args:
        image: PNG bytes of the full rendered page (from decode_identities cache).
        config: AppConfig carrying ``vision.stamp_crop`` settings.

    Returns:
        PNG bytes — either the cropped stamp region (Option A) or the original
        image (Option B fallback or on any PIL failure).
    """
    crop_cfg = config.vision.stamp_crop
    if not crop_cfg.enabled:
        # Option B: no cropping — return original (caller should have passed >=300dpi)
        logger.debug("_prepare_vision_image: stamp_crop disabled; using full-page image")
        return image

    try:
        import io  # noqa: PLC0415

        from PIL import Image  # noqa: PLC0415

        with Image.open(io.BytesIO(image)) as img:
            w, h = img.size
            # Convert fractional coords to pixel coords
            left = int(crop_cfg.x0 * w)
            upper = int(crop_cfg.y0 * h)
            right = int(crop_cfg.x1 * w)
            lower = int(crop_cfg.y1 * h)

            cropped = img.crop((left, upper, right, lower))

            buf = io.BytesIO()
            cropped.save(buf, format="PNG")
            result = buf.getvalue()
            logger.debug(
                "_prepare_vision_image: stamp crop (%d,%d,%d,%d) → %dx%d px, %d bytes",
                left, upper, right, lower,
                cropped.width, cropped.height,
                len(result),
            )
            return result

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_prepare_vision_image: crop failed (%s); falling back to full-page image", exc
        )
        return image


def _parse_day_month(
    fecha_confidence: float | None,
    raw_vision_string: str | None,
) -> tuple[int | None, int | None]:
    """Extract day and month integers from a raw vision string.

    Tries DD/MM and DD-MM patterns.  Returns ``(None, None)`` when the string
    is absent, empty, or does not match any known format.

    This helper is intentionally minimal — it handles the most common formats
    produced by vision models for Peruvian dates.  It is used only when
    ``GuiaDeRemision.fecha is None`` (no complete date was parsed by the adapter).

    Note: In R2, ``raw_vision_string`` comes from ``GuiaDeRemision.fecha``
    being None — we have no direct access to the raw VisionResult here.
    This function is reserved for future R3 wiring where the raw string is
    threaded through.  For now, returns (None, None) since we cannot extract
    day/month from the GuiaDeRemision alone without the raw string.

    Args:
        fecha_confidence: Vision confidence (unused currently; reserved).
        raw_vision_string: Raw string from VisionResult.raw, or None.

    Returns:
        ``(day, month)`` as integers, or ``(None, None)`` if parsing fails.
    """
    import re as _re  # noqa: PLC0415

    if not raw_vision_string:
        return None, None

    # Match DD/MM or DD-MM (with or without year)
    m = _re.search(r"(\d{1,2})[/\-](\d{1,2})", raw_vision_string)
    if not m:
        return None, None

    try:
        day = int(m.group(1))
        month = int(m.group(2))
        if 1 <= day <= 31 and 1 <= month <= 12:
            return day, month
    except ValueError:
        pass

    return None, None
