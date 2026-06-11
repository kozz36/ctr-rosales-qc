"""ReprocessService — REINTENTAR + Reprocesar-con-IA recovery for errored guías.

PR#2: deterministic SUNAT path (apply_retry).
PR#3: vision path (apply_reprocess) — renders pages, calls VisionLLMPort.read_material_table,
      builds normalized lines, commits via ReviewService.add_recovered_guia.

Architecture invariants (auto-reject if violated):
  - Ports-only constructor: DocumentSourcePort, IdentityExtractionPort,
    SunatGreFetchPort (optional), MaterialKeyResolver, ReviewService, VisionLLMPort.
  - ZERO concrete adapter imports at module level (container.py does the wiring).
  - Heavy deps (PIL/fitz/requests/etc.) stay INSIDE adapter methods or module-level
    helpers that lazy-import — never imported at module top.
  - Recovered lines: requires_review=True (invariant — reconciliation gate).
  - PDF read-only (render_page reads via DocumentSourcePort; no writes).
  - fecha is NEVER a grouping axis; normalization produces the same group_token
    (description_canonical) as the pipeline's _norm_line for identical inputs.
  - Vision lines: identity_source="vision" (PR#3 provenance).
  - asyncio.Semaphore bounds concurrent vision calls; asyncio.Lock serializes commits.

Design ref: design-pr2.md §Architecture Decision 3 (CRUX normalization parity).
            design-pr3.md §REV-R11 (downscale), §REV-R14 (identity_source),
            §REV-R15 (Semaphore+Lock concurrency).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from reconciliation.domain.date_floor import apply_delivery_floor
from reconciliation.domain.models import (
    GuiaDeRemision,
    MaterialLine,
    OfficialGre,
)

if TYPE_CHECKING:
    from reconciliation.application.review_service import ReviewService
    from reconciliation.domain.material_key_resolver import MaterialKeyResolver
    from reconciliation.domain.ports import (
        DocumentSourcePort,
        ExtractionPort,
        IdentityExtractionPort,
        SunatGreFetchPort,
        VisionLLMPort,
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
# Vision-path module-level helpers (PR#3)
# ---------------------------------------------------------------------------


def _downscale_image(image_bytes: bytes, max_edge: int) -> bytes:
    """Downscale an image so its long edge does not exceed *max_edge* pixels.

    Lazy-imports PIL (Pillow) inside the function so the test suite and air-gap
    machines that have no Pillow installed can still import this module.

    If PIL is unavailable or any error occurs, the original bytes are returned
    unchanged (graceful degradation).  This is intentional: vision adapters
    tolerate arbitrarily-sized images; the downscale is a best-effort bandwidth
    optimisation (REV-R11), not a hard gate.

    Args:
        image_bytes: Raw image bytes (PNG or JPEG from fitz render_page).
        max_edge:    Maximum pixel size for the longer dimension.

    Returns:
        Possibly-downscaled image bytes in the original format (PNG), or the
        original bytes if the image is already within bounds or PIL is absent.
    """
    try:
        import io  # noqa: PLC0415

        from PIL import Image  # noqa: PLC0415

        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        long_edge = max(w, h)
        if long_edge <= max_edge:
            return image_bytes
        scale = max_edge / long_edge
        new_w = max(1, int(w * scale))
        new_h = max(1, int(h * scale))
        img = img.resize((new_w, new_h), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        logger.debug("_downscale_image: skipping downscale (error=%s)", exc)
        return image_bytes


def _build_recovered_guia_lines_from_vision(
    vision_lines: list[MaterialLine],
    source_page: int,
    key_resolver: MaterialKeyResolver,
) -> list[MaterialLine]:
    """Build normalized MaterialLines from vision-extracted rows (PR#3 CRUX).

    Mirrors ``_build_recovered_guia_lines`` (SUNAT path) but takes lines already
    parsed by ``VisionLLMPort.read_material_table`` instead of ``OfficialGre``.

    Key invariants — same as SUNAT path (normalization parity):
      - ``requires_review=True`` on ALL output lines (service policy, unconditional).
      - Non-domain units skipped (mirrored from _build_recovered_guia_lines).
      - ``description_canonical`` = ``key.group_token`` from key_resolver.resolve.
      - ``match_method`` = ``key.method`` from key_resolver.
      - ``confidence`` preserved from the adapter line.

    Args:
        vision_lines: Lines returned by ``VisionLLMPort.read_material_table``.
        source_page:  0-based page index for ``MaterialLine.source_page``.
        key_resolver: Shared ``MaterialKeyResolver`` instance (parity with pipeline).

    Returns:
        List of normalized ``MaterialLine`` with ``requires_review=True``.
    """
    lines: list[MaterialLine] = []
    for vline in vision_lines:
        if vline.unidad not in _VALID_UNITS:
            logger.warning(
                "_build_recovered_guia_lines_from_vision: unit %r not in domain set; "
                "skipping (description=%r)",
                vline.unidad,
                vline.description_raw,
            )
            continue

        key = key_resolver.resolve(vline.description_raw, vline.unidad)
        normalized_line = vline.model_copy(update={
            "description_canonical": key.group_token,
            "match_method": key.method,
            "source_page": source_page,
            "requires_review": True,  # ALWAYS True — reconciliation gate
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


@dataclass
class ReprocessResult:
    """Outcome of a single ReprocessService.apply_reprocess call (PR#3).

    Attributes:
        recovered:   True when the guía was successfully recovered via vision.
        guia_id:     The guía that was reprocessed.
        reason:      Failure reason when recovered=False (None on success).
                     Values: ``"vision_empty"`` | ``"not_found"``.
        rows:        Updated ReconciliationRow list from ReviewService.
    """

    recovered: bool
    guia_id: str
    reason: str | None = None
    rows: list[Any] = field(default_factory=list)


# ---------------------------------------------------------------------------
# PageRecoveryResult  (PR-2 — discarded-page recovery outcome)
# ---------------------------------------------------------------------------


@dataclass
class PageRecoveryResult:
    """Outcome of a single ReprocessService.apply_page_recovery call (PR-2).

    Attributes:
        recovered:  True when the discarded page was successfully recovered.
        page:       The 0-based PDF page index that was recovered.
        guia_id:    The synthetic guía id (f"recovered_{page}") on success; None on failure.
        reason:     Failure reason when recovered=False (None on success).
                    Values: ``"empty"`` (all tiers returned 0 lines) |
                            ``"not_found"`` (page not in discarded list).
        rows:       Updated ReconciliationRow list from ReviewService (empty on failure).
    """

    recovered: bool
    page: int
    guia_id: str | None = None
    reason: str | None = None
    rows: list[Any] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ReprocessService
# ---------------------------------------------------------------------------


class ReprocessService:
    """Orchestrates REINTENTAR + Reprocesar-con-IA recovery for errored guías.

    Constructor ports (all Protocols — ZERO concrete adapter imports here):
      - ``doc_source``:     DocumentSourcePort — render source pages at DPI.
      - ``identity``:       IdentityExtractionPort — decode_identity + decode_hashqr_url.
      - ``sunat``:          SunatGreFetchPort | None — optional; REINTENTAR only.
      - ``key_resolver``:   MaterialKeyResolver — normalize descriptions to canonical key.
      - ``review_service``: ReviewService — add_recovered_guia (SOLE mutation hook).
      - ``vision``:         VisionLLMPort | None — required for apply_reprocess (PR#3).
      - ``max_concurrency``: int — max concurrent vision calls (default 3, REV-R15).
      - ``downscale_max_edge``: int — long-edge px cap before vision (default 2000, REV-R11).

    ``build_reprocess_service`` in container.py wires the concrete adapters.

    Design: Approach B (locked) — ReprocessService = adapter orchestrator (ports-only);
    ReviewService keeps SRP over in-memory guía list / re-reconcile / persistence.

    Render DPI=300: higher than the pipeline's 200 DPI first-pass to increase
    QR decode success on errored pages (design-pr2.md §Decision 2).

    Concurrency (apply_reprocess only): asyncio.Semaphore bounds parallel vision
    calls; asyncio.Lock serializes ReviewService.add_recovered_guia commits.
    Both are lazy-initialized on first use (avoid event-loop binding at __init__
    time which can run outside an async context). REV-R15.
    """

    _RENDER_DPI: int = 300

    def __init__(
        self,
        doc_source: DocumentSourcePort,
        identity: IdentityExtractionPort,
        sunat: SunatGreFetchPort | None,
        key_resolver: MaterialKeyResolver,
        review_service: ReviewService,
        vision: VisionLLMPort | None = None,
        max_concurrency: int = 3,
        downscale_max_edge: int = 2000,
        extractor: ExtractionPort | None = None,
    ) -> None:
        self._doc_source = doc_source
        self._identity = identity
        self._sunat = sunat
        self._key_resolver = key_resolver
        self._review_service = review_service
        self._vision = vision
        self._max_concurrency = max_concurrency
        self._downscale_max_edge = downscale_max_edge
        # PR-2: ExtractionPort for Tier-2 OCR re-run on discarded pages.
        # Ports-only (no concrete adapter import here; container.py wires this).
        self._extractor: ExtractionPort | None = extractor
        # Lazy asyncio primitives — created on first apply_reprocess call so
        # they bind to the running event loop, not the import-time loop.
        self._semaphore: asyncio.Semaphore | None = None
        self._commit_lock: asyncio.Lock | None = None

    def _get_semaphore(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._max_concurrency)
        return self._semaphore

    def _get_commit_lock(self) -> asyncio.Lock:
        if self._commit_lock is None:
            self._commit_lock = asyncio.Lock()
        return self._commit_lock

    async def apply_page_recovery(self, page: int) -> PageRecoveryResult:
        """Recover a discarded GUIA page via a 3-tier OCR-first chain (PR-2).

        Sequence (design §4):
          1. Lookup ``page`` in ``review_service.discarded_pages``.
             → not found: return PageRecoveryResult(recovered=False, reason="not_found").
          2. TIER 1 — cached lines: ``entry.lines`` non-empty → use directly.
             (RapidOCR is deterministic: same image → same lines; zero render/OCR/vision.)
          3. TIER 2 — OCR re-run: render page at DPI=300 + OCR in executor.
             Skipped when ``self._extractor is None``.
          4. TIER 3 — vision fallback: downscale + vision.read_material_table under Semaphore.
          5. All tiers returned 0 usable lines → PageRecoveryResult(recovered=False, reason="empty").
             The entry is NOT removed from discarded_pages (REV-R30-S04).
          6. Normalize via _build_recovered_guia_lines_from_vision (sets requires_review=True).
          7. Build GuiaDeRemision(guia_id=f"recovered_{page}", registro=entry.registro,
             fecha=None, fecha_entrega=None, identity_source="operator").
             fecha=None is intentional: no vision date read; R9b/R9c do not apply.
          8. Under commit Lock: review_service.recover_discarded_page(page, guia).
          9. Return PageRecoveryResult(recovered=True, guia_id=..., rows=...).

        Args:
            page: 0-based PDF page index.

        Returns:
            PageRecoveryResult.
        """
        from reconciliation.domain.models import GuiaDeRemision  # noqa: PLC0415

        # Step 1: lookup discarded entry.
        entry = next(
            (dp for dp in self._review_service.discarded_pages if dp.page == page),
            None,
        )
        if entry is None:
            logger.warning(
                "apply_page_recovery: page=%d not found in discarded_pages", page
            )
            return PageRecoveryResult(recovered=False, page=page, reason="not_found")

        raw_lines: list = []

        # Step 2: Tier 1 — cached lines (zero render/OCR/vision).
        if entry.lines:
            raw_lines = list(entry.lines)
            logger.info(
                "apply_page_recovery: page=%d — Tier 1 (cached lines, count=%d)",
                page,
                len(raw_lines),
            )
        else:
            # Step 3: Tier 2 — OCR re-run.
            if self._extractor is not None:
                rendered: bytes = b""
                try:
                    loop = asyncio.get_running_loop()
                    rendered = await loop.run_in_executor(
                        None,
                        lambda: self._doc_source.render_page(page, dpi=self._RENDER_DPI),
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "apply_page_recovery: render_page(%d) failed: %s", page, exc
                    )

                if rendered:
                    try:
                        loop = asyncio.get_running_loop()
                        raw_lines = await loop.run_in_executor(
                            None,
                            lambda: self._extractor.extract_printed_table(rendered),  # type: ignore[union-attr]
                        )
                        logger.info(
                            "apply_page_recovery: page=%d — Tier 2 (OCR, count=%d)",
                            page,
                            len(raw_lines),
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "apply_page_recovery: extract_printed_table(%d) failed: %s",
                            page,
                            exc,
                        )

            # Step 4: Tier 3 — vision fallback (if still empty).
            if not raw_lines and self._vision is not None:
                async with self._get_semaphore():
                    rendered_for_vision: bytes = b""
                    try:
                        loop = asyncio.get_running_loop()
                        rendered_for_vision = await loop.run_in_executor(
                            None,
                            lambda: self._doc_source.render_page(page, dpi=self._RENDER_DPI),
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "apply_page_recovery: render_page(%d) for vision failed: %s",
                            page,
                            exc,
                        )
                    if rendered_for_vision:
                        rendered_for_vision = _downscale_image(
                            rendered_for_vision, self._downscale_max_edge
                        )
                        vision_lines: list = []
                        try:
                            loop = asyncio.get_running_loop()
                            vision_lines = await loop.run_in_executor(
                                None,
                                lambda: self._vision.read_material_table(  # type: ignore[union-attr]
                                    rendered_for_vision, hint=f"page_{page}"
                                ),
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "apply_page_recovery: vision.read_material_table(%d) failed: %s",
                                page,
                                exc,
                            )
                        if vision_lines:
                            raw_lines = vision_lines
                            logger.info(
                                "apply_page_recovery: page=%d — Tier 3 (vision, count=%d)",
                                page,
                                len(raw_lines),
                            )

        # Step 5: all tiers empty — structured failure; entry STAYS.
        if not raw_lines:
            logger.info(
                "apply_page_recovery: page=%d — all tiers empty → stays discarded", page
            )
            return PageRecoveryResult(recovered=False, page=page, reason="empty")

        # Step 6: normalize lines (requires_review=True unconditionally).
        lines = _build_recovered_guia_lines_from_vision(
            vision_lines=raw_lines,
            source_page=page,
            key_resolver=self._key_resolver,
        )
        if not lines:
            logger.info(
                "apply_page_recovery: page=%d — 0 usable lines after normalization", page
            )
            return PageRecoveryResult(recovered=False, page=page, reason="empty")

        # Step 7: build GuiaDeRemision.
        guia_id = f"recovered_{page}"
        async with self._get_commit_lock():
            guia = GuiaDeRemision(
                guia_id=guia_id,
                registro=entry.registro,   # inherited directly — no dialog/parameter
                fecha=None,                # intentional: no date read (material-only recovery)
                fecha_entrega=None,        # no SUNAT for discarded pages
                lines=lines,
                source_pages=[page],
                identity_source="operator",  # D2 — operator asserted this is a guía
            )
            # Step 8: commit under lock (mirrors apply_reprocess :575).
            updated_rows = self._review_service.recover_discarded_page(page=page, guia=guia)

        logger.info(
            "apply_page_recovery: page=%d recovered as %r; %d rows updated",
            page,
            guia_id,
            len(updated_rows),
        )
        return PageRecoveryResult(
            recovered=True,
            page=page,
            guia_id=guia_id,
            rows=updated_rows,
        )

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

    async def apply_reprocess(
        self,
        guia_id: str,
        source_pages: list[int],
    ) -> ReprocessResult:
        """Recover a single errored guía via vision (Reprocesar con IA, PR#3).

        Sequence (REV-R11..REV-R15):
          1. Look up guía in review_service.errored_guias (by guia_id).
          2. Acquire Semaphore (bounded concurrency, REV-R15).
          3. Render first source_page at DPI=300.
          4. Downscale to max_edge (REV-R11) via _downscale_image.
          5. Call VisionLLMPort.read_material_table (in executor; non-blocking).
          6. Release Semaphore.
          7. If vision returned 0 lines → ReprocessResult(recovered=False, reason="vision_empty").
          8. Build normalized lines via _build_recovered_guia_lines_from_vision (parity crux).
          9. Apply R9b delivery floor: fecha = ErroredGuia.fecha_entrega (already persisted).
         10. Acquire commit Lock (serialized commit, REV-R15).
         11. Build GuiaDeRemision(identity_source="vision") + call add_recovered_guia.
         12. Release Lock.
         13. Return ReprocessResult(recovered=True, rows=...).

        Args:
            guia_id:      Deterministic guía identifier (serie-numero).
            source_pages: 0-based PDF page indices.

        Returns:
            ReprocessResult.
        """
        # Step 1: look up in errored_guias.
        errored = next(
            (e for e in self._review_service.errored_guias if e.guia_id == guia_id),
            None,
        )
        if errored is None:
            logger.warning(
                "apply_reprocess: guia_id=%r not found in errored_guias", guia_id
            )
            return ReprocessResult(recovered=False, guia_id=guia_id, reason="not_found")

        first_page = source_pages[0] if source_pages else (
            errored.source_pages[0] if errored.source_pages else 0
        )

        # Step 2: bounded concurrency via Semaphore (REV-R15).
        async with self._get_semaphore():
            # Step 3: render source page at DPI=300.
            rendered: bytes = b""
            try:
                rendered = self._doc_source.render_page(first_page, dpi=self._RENDER_DPI)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "apply_reprocess: render_page(%d) failed for %r: %s",
                    first_page, guia_id, exc,
                )

            # Step 4: downscale (REV-R11).
            if rendered:
                rendered = _downscale_image(rendered, self._downscale_max_edge)

            # Step 5: call vision in executor (sync blocking call; non-blocking here).
            vision_lines: list[MaterialLine] = []
            if rendered and self._vision is not None:
                loop = asyncio.get_running_loop()
                try:
                    vision_lines = await loop.run_in_executor(
                        None,
                        lambda: self._vision.read_material_table(rendered, hint=guia_id),  # type: ignore[union-attr]
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "apply_reprocess: read_material_table failed for %r: %s",
                        guia_id, exc,
                    )
        # Semaphore released here (end of async with block, Step 6 implicit).

        # Step 7: guard empty vision result.
        if not vision_lines:
            logger.info(
                "apply_reprocess: vision returned 0 lines for %r → stays errored", guia_id
            )
            return ReprocessResult(recovered=False, guia_id=guia_id, reason="vision_empty")

        # Step 8: build normalized lines (parity crux).
        lines = _build_recovered_guia_lines_from_vision(
            vision_lines=vision_lines,
            source_page=first_page,
            key_resolver=self._key_resolver,
        )
        if not lines:
            logger.info(
                "apply_reprocess: 0 usable lines after normalization for %r → stays errored",
                guia_id,
            )
            return ReprocessResult(recovered=False, guia_id=guia_id, reason="vision_empty")

        # Step 9: R9b delivery floor — use persisted fecha_entrega from ErroredGuia.
        fecha, _ = apply_delivery_floor(None, errored.fecha_entrega)

        # Steps 10-12: serialized commit (REV-R15).
        async with self._get_commit_lock():
            guia = GuiaDeRemision(
                guia_id=guia_id,
                registro=None,  # ReviewService.add_recovered_guia will match by guia_id
                fecha=fecha,
                fecha_entrega=errored.fecha_entrega,
                lines=lines,
                source_pages=source_pages,
                identity_source="vision",  # PR#3 provenance (REV-R14)
            )
            updated_rows = self._review_service.add_recovered_guia(guia)

        logger.info(
            "apply_reprocess: %r recovered via vision; %d rows updated",
            guia_id, len(updated_rows),
        )
        return ReprocessResult(recovered=True, guia_id=guia_id, rows=updated_rows)
