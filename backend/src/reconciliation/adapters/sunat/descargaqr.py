"""SunatDescargaqrAdapter — OPT-IN SUNAT GRE deterministic-data adapter (rev-3 / EXT-023 / D3).

Implements ``SunatGreFetchPort`` by performing a plain HTTP GET on the hashqr URL
decoded from the guía page URL-variant QR code.  The URL encodes the hashqr token
that acts as the bearer credential (no OAuth, no Clave SOL required).

The adapter is:
- **Lazy-importing**: ``httpx`` (or stdlib fallback) and ``fitz`` (PyMuPDF) are
  imported INSIDE ``fetch()`` so the module can be imported in test environments
  that do not have those packages installed.
- **Gracefully failing**: any exception (network, non-200, non-PDF, parse error)
  results in ``None`` being returned — the pipeline MUST fall back to OCR.
  The adapter MUST NOT raise into the pipeline.
- **Cache-aware**: when ``cache=True`` (the default), the downloaded PDF is saved
  to ``<cache_dir>/{guia_id}.pdf`` and reused on subsequent calls within the same
  run.
- **Retry-resilient**: read-timeouts on the first attempt are retried up to
  ``_MAX_RETRIES`` times with exponential backoff before giving up.

SUNAT endpoint (confirmed in spike #2750):
  GET https://e-factura.sunat.gob.pe/v1/contribuyente/gre/comprobantes/descargaqr
      ?hashqr=<BASE64_TOKEN>
  Returns: HTTP 200, Content-Type: application/pdf, ~4 KB
  Content-Disposition: filename "{RUC}-{tipo}-{serie}-{numero}-PDF.pdf"

Real text layout from PyMuPDF get_text() (R6 fix — verified against live SUNAT PDF):
  "N° T073 - 00680258"
  "Fecha de entrega de Bienes al  transportista:28/05/2026"
  "Bienes por transportar:"
  --- COLUMN HEADER TOKENS (one per line, no slashes) ---
  "Cantidad"
  "Bien"
  "normalizado"
  "Unidad de"
  "medida"
  "Código"
  "GTIN"
  "N°"
  "Código de"
  "Bien"
  "Partida"
  "arancelaria"
  "Descripción Detallada"
  "Código"
  "producto"
  "SUNAT"                        ← last header token
  --- VALUE TOKENS per line item (6 tokens, one per line) ---
  "BARRA A A615-G60 3/8\" X 9M"  ← descripcion
  "407797"                        ← codigo_producto (digits only)
  "TONELADAS"                     ← unidad (UoM text)
  "1"                             ← N° (integer line-counter)
  "NO"                            ← GTIN indicator
  "0.192"                         ← cantidad (decimal)
  --- END MARKER ---
  "Indicador de traslado ..."    ← or "Datos del traslado:" / "Peso Bruto"

  Multiple line items repeat the 6-token group sequentially.
  The header tokens and value tokens are COMPLETELY SEPARATE — no slash separators.

Parsing strategy (label-defensive, position-tolerant):
  - Locates "Bienes por transportar:" section boundary.
  - Skips all header tokens by finding the last known header anchor ("SUNAT").
  - Groups the remaining tokens as 6-token repeating value blocks until an end marker.
  - Within each block: token[0]=descripcion, token[1]=codigo(digits only), token[2]=unidad(text),
    token[3]=numero(int), token[4]=indicator, token[5]=cantidad(decimal).
  - Falls back gracefully when a field is absent (None for optional fields).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from datetime import date as datetime_date
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reconciliation.domain.models import OfficialGre

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry / timeout defaults (R6 resilience fix)
# ---------------------------------------------------------------------------

# Default HTTP timeout raised from 10 s → 30 s; burst SUNAT responses can be slow.
# NOTE (resilience fix): with the structured httpx.Timeout, ``timeout_s`` now bounds
# only the CONNECT phase (capped at _MAX_CONNECT_S); the read phase uses _READ_TIMEOUT_S.
_DEFAULT_TIMEOUT_S: float = 30.0
# Maximum number of attempts before giving up (first attempt + retries).
_MAX_RETRIES: int = 3
# Base back-off in seconds; actual wait = _BACKOFF_BASE * (attempt_index + 1).
_BACKOFF_BASE: float = 1.0
# Structured-timeout phase budgets (resilience fix). A scalar timeout is a TOTAL
# request budget; under SUNAT burst rate-limiting the read phase needs its own,
# generous allowance so the parse stage actually runs.
_MAX_CONNECT_S: float = 10.0   # upper bound for the connect phase
_READ_TIMEOUT_S: float = 60.0  # generous read budget to survive rate-limiting
_WRITE_TIMEOUT_S: float = 10.0
_POOL_TIMEOUT_S: float = 10.0
# Inter-request pause between consecutive network downloads (seconds) to avoid
# tripping SUNAT rate-limiting on sequential guía fetches.
_FETCH_PACING_S: float = 0.5

# ---------------------------------------------------------------------------
# Date patterns used by the SUNAT PDF text layout
# ---------------------------------------------------------------------------

# Matches "28/05/2026" or "28/05/2026 01:58 AM" etc.
_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")

# Label-anchored patterns for SUNAT PDF text fields
_EMISION_RE = re.compile(
    r"Fecha\s+de\s+emisi[oó]n\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{4})",
    re.IGNORECASE,
)
_ENTREGA_RE = re.compile(
    r"Fecha\s+de\s+entrega\s+de\s+Bienes\s+al\s+transportista\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{4})",
    re.IGNORECASE,
)
# Matches the guía document number in the header.  The SUNAT PDF renders it as
# "N° T073 - 00680258" (with possible spacing variations).
_GRE_NUM_RE = re.compile(
    r"N[°º]\s*([A-Z]\d{3})\s*[-–]\s*(\d{5,8})",
    re.IGNORECASE,
)
# End-of-items markers that delimit the value block in the SUNAT GRE PDF.
# When any of these strings appears (case-insensitive) the value block is over.
_ITEMS_END_RE = re.compile(
    r"Indicador\s+de\s+traslado|Datos\s+del\s+traslado|Peso\s+Bruto",
    re.IGNORECASE,
)
# RUC patterns — 11 consecutive digits
_RUC_EMISOR_RE = re.compile(
    r"(?:RUC|Ruc).*?(\d{11})",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class SunatDescargaqrAdapter:
    """Fetches and parses the official SUNAT GRE PDF for a guía via descargaqr.

    Args:
        timeout_s:  CONNECT-phase timeout in seconds (default 30, capped at 10s
                    by the structured ``httpx.Timeout``).  The READ phase uses a
                    separate, generous budget (``_READ_TIMEOUT_S``) so SUNAT
                    burst rate-limiting does not exhaust a single total budget
                    and falsely yield "no line items".
        cache_dir:  Directory to cache downloaded GRE PDFs.  ``None`` disables
                    caching (useful in tests).  When ``None``, no file is written.
    """

    def __init__(
        self,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        cache_dir: Path | None = None,
        max_retries: int = _MAX_RETRIES,
    ) -> None:
        self._timeout_s = timeout_s
        self._cache_dir = cache_dir
        self._max_retries = max_retries
        # Monotonic timestamp of the last completed network download; used to
        # pace consecutive fetches (None until the first network download).
        self._last_download_monotonic: float | None = None
        # KI-2: guards the pace read/write so the inter-request interval is honoured
        # when fetch_many() runs self.fetch concurrently via asyncio.to_thread.
        # (fetch_many also paces at the scheduling layer; this lock keeps the
        #  per-thread guard correct for the direct sequential fetch() entry point.)
        self._pace_lock = threading.Lock()

    # ------------------------------------------------------------------
    # SunatGreFetchPort interface
    # ------------------------------------------------------------------

    async def fetch_many(
        self,
        urls: list[str],
        concurrency: int = 5,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, OfficialGre | None]:
        """Bounded-concurrency batch fetch with REAL adaptive back-pressure
        (R10.7 / CONT-S09/S11; KI-2 / KI-3 fix).

        The URL list is processed in concurrency-sized WAVES.  Each wave dispatches
        at most ``wave_size`` fetches concurrently via ``asyncio.to_thread`` so the
        blocking HTTP call never stalls the event loop.  Dispatches WITHIN a wave
        are spaced by ``_FETCH_PACING_S`` at this scheduling layer (W2-B / KI-2):
        because pacing is enforced here — not in the per-thread ``_download`` —
        the inter-request interval holds even under concurrency, where the shared
        ``_last_download_monotonic`` was previously racy.

        Adaptive back-pressure (W1 / KI-3): after a wave in which 3+ fetches
        returned ``None`` (SUNAT 429 / rate-limit signal), the NEXT wave's size is
        reduced by 1 (floor 1).  Unlike the previous implementation — which only
        decremented a dead local int while a fixed-capacity ``Semaphore`` kept
        every URL in flight — this genuinely lowers the number of concurrent
        in-flight requests for subsequent waves.

        The graceful-None contract from ``fetch()`` is preserved: a URL whose
        fetch fails appears in the result as ``None`` — the run still completes.

        Args:
            urls:        List of hashqr URLs to fetch (may be empty).
            concurrency: Maximum parallel in-flight requests for the FIRST wave.
            on_progress: Optional callback ``(done: int, total: int) -> None``
                         called once after each wave completes, with cumulative
                         ``done`` count.  Enables the pipeline to advance the
                         progress bar DURING the fetch rather than after.

        Returns:
            Dict mapping each URL to its ``OfficialGre`` or ``None``.
        """
        import asyncio  # noqa: PLC0415 — stdlib; lazy for consistency with module style

        if not urls:
            return {}

        results: dict[str, OfficialGre | None] = {}
        wave_size = max(1, concurrency)
        pending = list(urls)

        # Shared async pacing gate (W2-B / KI-2): a single coroutine-serialised
        # monotonic timestamp enforces a MINIMUM interval between dispatches.
        # Enforcing it here (the scheduling layer) — rather than in the per-thread
        # _download via the racy _last_download_monotonic — keeps the inter-request
        # spacing correct under concurrency. Because it gates DISPATCH (not
        # completion), slow concurrent fetches still overlap up to wave_size.
        pace_lock = asyncio.Lock()
        last_dispatch: list[float | None] = [None]
        loop = asyncio.get_event_loop()

        async def _pace_gate() -> None:
            async with pace_lock:
                prev = last_dispatch[0]
                if prev is not None:
                    remaining = _FETCH_PACING_S - (loop.time() - prev)
                    if remaining > 0:
                        await asyncio.sleep(remaining)
                last_dispatch[0] = loop.time()

        async def _fetch_one(url: str) -> tuple[str, OfficialGre | None]:
            await _pace_gate()
            return url, await asyncio.to_thread(self.fetch, url)

        while pending:
            wave = pending[:wave_size]
            pending = pending[wave_size:]

            wave_results = await asyncio.gather(*(_fetch_one(url) for url in wave))

            none_count = 0
            for url, result in wave_results:
                results[url] = result
                if result is None:
                    none_count += 1

            # Per-wave progress: report cumulative done count so the pipeline
            # can advance the progress bar DURING the fetch (issue #21 fix).
            if on_progress is not None:
                on_progress(len(results), len(urls))

            # Adaptive back-pressure (W1 / KI-3): sustained failures in this wave
            # genuinely shrink the next wave's in-flight ceiling.
            if none_count >= 3 and wave_size > 1:
                wave_size = max(1, wave_size - 1)
                logger.debug(
                    "fetch_many: %d None results this wave → "
                    "shrinking next wave size to %d",
                    none_count,
                    wave_size,
                )

        return results

    def fetch(self, hashqr_url: str) -> OfficialGre | None:
        """Fetch the official GRE PDF and return parsed ``OfficialGre``, or ``None``.

        Algorithm:
        1. Derive a stable guia_id from the URL (used for cache key).
        2. Check the cache; return parsed result from cache if hit.
        3. Perform an HTTP GET on ``hashqr_url`` (lazy httpx import).
        4. Validate Content-Type is ``application/pdf``.
        5. Cache the PDF bytes when caching is enabled.
        6. Parse with PyMuPDF ``get_text()`` → ``OfficialGre``.
        7. Any exception at any step → log warning and return ``None``.

        Never raises.
        """
        try:
            return self._fetch_internal(hashqr_url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("SunatDescargaqrAdapter.fetch: unhandled error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Internal implementation
    # ------------------------------------------------------------------

    def _fetch_internal(self, hashqr_url: str) -> OfficialGre | None:
        """Inner fetch — may raise; outer fetch() wraps with catch-all."""
        from decimal import Decimal  # noqa: PLC0415

        from reconciliation.domain.models import GreLineItem, OfficialGre  # noqa: PLC0415

        # Derive guia_id from the Content-Disposition or use the URL hash as key.
        # We will refine after the actual download.
        cache_key = _url_to_cache_key(hashqr_url)
        pdf_bytes = self._try_cache(cache_key)

        if pdf_bytes is None:
            pdf_bytes = self._download(hashqr_url)
            if pdf_bytes is None:
                return None
            self._save_cache(cache_key, pdf_bytes)

        # Parse PDF text with PyMuPDF
        text = _extract_pdf_text(pdf_bytes)
        if not text:
            logger.warning(
                "SunatDescargaqrAdapter: PDF yielded no text for URL %s", hashqr_url
            )
            return None

        # Parse structured fields
        serie, numero = _parse_gre_number(text)
        if not serie and not numero:
            logger.warning(
                "SunatDescargaqrAdapter: could not parse N° from PDF text (URL %s)",
                hashqr_url,
            )
            # Still try to build a partial result
            serie = ""
            numero = ""

        guia_id = f"{serie}-{numero}" if serie and numero else cache_key

        fecha_emision = _parse_labelled_date(text, _EMISION_RE)
        fecha_entrega = _parse_labelled_date(text, _ENTREGA_RE)
        ruc_emisor, ruc_receptor = _parse_rucs(text)
        line_items = _parse_line_items(text, Decimal, GreLineItem)

        return OfficialGre(
            guia_id=guia_id,
            serie=serie,
            numero=numero,
            ruc_emisor=ruc_emisor or "",
            ruc_receptor=ruc_receptor or "",
            fecha_emision=fecha_emision,
            fecha_entrega=fecha_entrega,
            lines=line_items,
        )

    def _try_cache(self, key: str) -> bytes | None:
        """Return cached PDF bytes if present, else None."""
        if self._cache_dir is None:
            return None
        cache_file = self._cache_dir / f"{key}.pdf"
        if cache_file.exists():
            try:
                data = cache_file.read_bytes()
                logger.debug(
                    "SunatDescargaqrAdapter: cache hit for key=%r (%d bytes)", key, len(data)
                )
                return data
            except Exception as exc:  # noqa: BLE001
                logger.warning("SunatDescargaqrAdapter: cache read failed: %s", exc)
        return None

    def _save_cache(self, key: str, pdf_bytes: bytes) -> None:
        """Write PDF bytes to cache file; silently ignore failures."""
        if self._cache_dir is None:
            return
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = self._cache_dir / f"{key}.pdf"
            cache_file.write_bytes(pdf_bytes)
            logger.debug(
                "SunatDescargaqrAdapter: cached %d bytes → %s", len(pdf_bytes), cache_file
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("SunatDescargaqrAdapter: cache write failed: %s", exc)

    def _download(self, url: str) -> bytes | None:
        """Perform HTTP GET; validate Content-Type; return PDF bytes or None.

        Paces consecutive network downloads (``_FETCH_PACING_S``) to avoid
        tripping SUNAT burst rate-limiting on sequential guía fetches.  Cache
        hits never reach this method, so no pause is incurred for cached PDFs.
        """
        self._pace_request()
        try:
            import httpx  # noqa: PLC0415
            _http_client = httpx
        except ImportError:
            # Fallback to stdlib urllib when httpx is absent
            _http_client = None  # type: ignore[assignment]

        try:
            if _http_client is not None:
                return self._download_httpx(url, _http_client)
            return self._download_urllib(url)
        finally:
            with self._pace_lock:
                self._last_download_monotonic = time.monotonic()

    def _pace_request(self) -> None:
        """Sleep so consecutive network downloads are spaced by ``_FETCH_PACING_S``.

        KI-2: the read-compute-sleep sequence is guarded by ``_pace_lock`` so the
        interval is enforced even when ``fetch_many`` issues concurrent fetches.
        Holding the lock across the sleep serialises the spacing decision (a
        thread waits while another is pacing), which is the intended back-pressure.
        """
        with self._pace_lock:
            if self._last_download_monotonic is None:
                return
            elapsed = time.monotonic() - self._last_download_monotonic
            remaining = _FETCH_PACING_S - elapsed
            if remaining > 0:
                time.sleep(remaining)

    def _download_httpx(self, url: str, httpx_module: object) -> bytes | None:
        """Download using httpx with exponential-backoff retry on read-timeouts.

        Uses a STRUCTURED ``httpx.Timeout`` instead of a scalar value.  A scalar
        timeout is a TOTAL request budget; under SUNAT burst rate-limiting the
        2nd/3rd consecutive fetches exhaust it and the parser never runs (false
        "no line items").  The structured timeout bounds the connect phase
        (``min(timeout_s, _MAX_CONNECT_S)``) while granting a generous read
        budget (``_READ_TIMEOUT_S``).
        """
        import httpx  # noqa: PLC0415

        timeout = httpx.Timeout(
            connect=min(self._timeout_s, _MAX_CONNECT_S),
            read=_READ_TIMEOUT_S,
            write=_WRITE_TIMEOUT_S,
            pool=_POOL_TIMEOUT_S,
        )

        for attempt in range(self._max_retries):
            try:
                resp = httpx.get(url, timeout=timeout, follow_redirects=True)
            except httpx.TimeoutException as exc:
                wait = _BACKOFF_BASE * (attempt + 1)
                logger.warning(
                    "SunatDescargaqrAdapter: request timeout (attempt %d/%d, retry in %.1fs): %s",
                    attempt + 1,
                    self._max_retries,
                    wait,
                    exc,
                )
                if attempt < self._max_retries - 1:
                    time.sleep(wait)
                continue
            except httpx.RequestError as exc:
                logger.warning("SunatDescargaqrAdapter: request error: %s", exc)
                return None

            if resp.status_code != 200:
                logger.warning(
                    "SunatDescargaqrAdapter: HTTP %d for URL %s", resp.status_code, url
                )
                return None

            content_type = resp.headers.get("content-type", "")
            if "application/pdf" not in content_type:
                logger.warning(
                    "SunatDescargaqrAdapter: unexpected Content-Type %r (expected PDF)",
                    content_type,
                )
                return None

            return resp.content

        logger.warning(
            "SunatDescargaqrAdapter: all %d attempts timed out for URL %s",
            self._max_retries,
            url,
        )
        return None

    def _download_urllib(self, url: str) -> bytes | None:
        """Download using stdlib urllib as fallback when httpx is absent."""
        import urllib.error  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415

        try:
            req = urllib.request.Request(url, headers={"Accept": "application/pdf"})
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:  # noqa: S310
                content_type = resp.headers.get("Content-Type", "")
                if "application/pdf" not in content_type:
                    logger.warning(
                        "SunatDescargaqrAdapter(urllib): unexpected Content-Type %r", content_type
                    )
                    return None
                data: bytes = resp.read()
                return data
        except urllib.error.URLError as exc:
            logger.warning("SunatDescargaqrAdapter(urllib): URL error: %s", exc)
            return None
        except TimeoutError as exc:
            logger.warning("SunatDescargaqrAdapter(urllib): timeout: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Pure parsing helpers (no IO — used in tests directly)
# ---------------------------------------------------------------------------


def _url_to_cache_key(url: str) -> str:
    """Derive a filesystem-safe cache key from the descargaqr URL.

    Extracts the ``hashqr`` query parameter value (base64-url-safe chars are
    alphanumeric + ``-`` + ``_`` + ``=``).  Strips ``=`` padding for safety.
    Falls back to a truncated URL hash when the parameter is absent.
    """
    import urllib.parse  # noqa: PLC0415

    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    hashqr = qs.get("hashqr", [""])[0]
    if hashqr:
        # Keep only alphanumeric + safe chars; truncate to 64 chars
        safe = re.sub(r"[^a-zA-Z0-9\-_]", "", hashqr)[:64]
        return safe or "unknown"
    # Fallback: use the last 40 chars of the raw URL
    return re.sub(r"[^a-zA-Z0-9\-_]", "_", url)[-40:]


def _extract_pdf_text(pdf_bytes: bytes) -> str | None:
    """Extract all text from a PDF using PyMuPDF get_text().

    Returns the concatenated text of all pages, or None on failure.
    """
    try:
        import fitz  # noqa: PLC0415 (PyMuPDF)

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            pages_text = []
            for page in doc:
                pages_text.append(page.get_text())
            return "\n".join(pages_text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_extract_pdf_text: fitz failed: %s", exc)
        return None


def _parse_gre_number(text: str) -> tuple[str, str]:
    """Parse the GRE document number from SUNAT PDF text.

    Looks for the pattern ``N° T073 - 00680258`` and returns ``(serie, numero)``.
    Returns ``("", "")`` when the pattern is not found.
    """
    m = _GRE_NUM_RE.search(text)
    if m:
        serie = m.group(1).upper().strip()
        # Preserve the canonical 8-digit numero with leading zeros as SUNAT renders it
        # e.g. T073-00680258 → numero="00680258"
        numero = m.group(2).strip()
        return serie, numero
    return "", ""


def _parse_labelled_date(text: str, pattern: re.Pattern[str]) -> datetime_date | None:
    """Extract a date from text using a label-anchored regex."""
    m = pattern.search(text)
    if not m:
        return None
    date_str = m.group(1)
    dm = _DATE_RE.match(date_str)
    if not dm:
        return None
    try:
        day, month, year = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
        return datetime_date(year, month, day)
    except ValueError:
        return None


def _parse_rucs(text: str) -> tuple[str | None, str | None]:
    """Extract emisor and receptor RUC numbers from the PDF text.

    SUNAT PDFs contain two RUC blocks — the first is the emisor, the second the
    receptor.  We extract them in document order.
    """
    rucs = re.findall(r"\b(\d{11})\b", text)
    emisor = rucs[0] if len(rucs) >= 1 else None
    receptor = rucs[1] if len(rucs) >= 2 else None
    return emisor, receptor


def _parse_line_items(
    text: str,
    Decimal: type,  # noqa: N803
    GreLineItem: type,  # noqa: N803
) -> list:  # type: ignore[type-arg]
    """Parse line items from the SUNAT GRE PDF text (R6 rewrite — token-block algorithm).

    Real SUNAT PDF text layout (from PyMuPDF get_text()):
        The "Bienes por transportar:" section contains multi-line column HEADERS
        (one token per line, no slash separators) followed by VALUE BLOCKS.
        The last column header token is the literal word "SUNAT".

        After "SUNAT" comes a repeating 6-token group per line item:
          token 0 — descripcion        (free text, e.g. 'BARRA A A615-G60 3/8" X 9M')
          token 1 — codigo_producto    (digits only, e.g. '407797')
          token 2 — unidad             (UoM word, e.g. 'TONELADAS')
          token 3 — numero             (integer line counter, e.g. '1')
          token 4 — gtin_indicator     ('NO' / 'SI')
          token 5 — cantidad           (decimal string, e.g. '0.192')

        Multiple items repeat this 6-token group sequentially.
        The block ends when a line matches _ITEMS_END_RE
        ('Indicador de traslado' / 'Datos del traslado' / 'Peso Bruto').

    Units are stored as raw SUNAT strings (e.g. "TONELADAS", "KILOGRAMOS").
    Normalisation to domain codes (TN, KG, etc.) is performed downstream by
    ``_normalize_sunat_unit`` in the application pipeline, which owns that mapping.

    Returns a list of ``GreLineItem`` instances.
    """
    _DIGITS_ONLY_RE = re.compile(r"^\d+$")

    items: list[object] = []

    # Step 1 — Locate "Bienes por transportar:" section
    section_start = re.search(r"Bienes\s+por\s+transportar\s*:", text, re.IGNORECASE)
    if section_start is None:
        logger.debug("_parse_line_items: 'Bienes por transportar' section not found")
        return items

    # Step 2 — Find the last header anchor ("SUNAT") within the section
    # The header block always ends with the literal token "SUNAT" on its own line.
    section_text = text[section_start.end():]
    sunat_token = re.search(r"(?:^|\n)\s*SUNAT\s*(?:\n|$)", section_text)
    if sunat_token is None:
        logger.debug("_parse_line_items: 'SUNAT' header-end token not found in section")
        return items

    # Step 3 — Collect value tokens until the end marker
    value_text = section_text[sunat_token.end():]

    raw_lines: list[str] = []
    for raw_line in value_text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if _ITEMS_END_RE.search(stripped):
            break  # End of items block
        raw_lines.append(stripped)

    # Step 4 — Group into 6-token blocks (one item per group)
    # Expected order: [descripcion, codigo, unidad, numero, indicator, cantidad]
    TOKENS_PER_ITEM = 6
    item_idx = 0
    while item_idx + TOKENS_PER_ITEM <= len(raw_lines):
        group = raw_lines[item_idx : item_idx + TOKENS_PER_ITEM]
        item_idx += TOKENS_PER_ITEM

        desc_raw = group[0]
        code_raw = group[1]
        unit_raw = group[2].upper()
        # group[3] = N° (line counter, integer) — skip
        # group[4] = GTIN indicator ('NO'/'SI') — skip
        qty_raw = group[5]

        # Validate token types defensively
        if not _DIGITS_ONLY_RE.match(code_raw):
            logger.debug(
                "_parse_line_items: expected digits for codigo, got %r — skipping group",
                code_raw,
            )
            continue

        qty_str = qty_raw.replace(",", ".")
        try:
            qty = Decimal(qty_str)
        except Exception:  # noqa: BLE001
            logger.debug(
                "_parse_line_items: could not parse cantidad %r — skipping group", qty_raw
            )
            continue

        # Store the raw SUNAT unit string; normalisation to domain codes is the
        # responsibility of _normalize_sunat_unit in the application pipeline.
        items.append(
            GreLineItem(
                cantidad=qty,
                unidad=unit_raw,
                descripcion=desc_raw,
                codigo_producto=code_raw,
            )
        )

    logger.debug(
        "_parse_line_items: found %d line items in SUNAT PDF text", len(items)
    )
    return items
