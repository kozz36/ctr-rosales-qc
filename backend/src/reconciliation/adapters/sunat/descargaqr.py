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

SUNAT endpoint (confirmed in spike #2750):
  GET https://e-factura.sunat.gob.pe/v1/contribuyente/gre/comprobantes/descargaqr
      ?hashqr=<BASE64_TOKEN>
  Returns: HTTP 200, Content-Type: application/pdf, ~4 KB
  Content-Disposition: filename "{RUC}-{tipo}-{serie}-{numero}-PDF.pdf"

Text layout in the returned PDF (full digital text; NOT a scan):
  "N° T073 - 00680258" — guia serie and numero
  "Fecha de emisión <date>" — GRE issue date
  "Fecha de entrega de Bienes al transportista:<date>" — delivery date (lower bound)
  "Cantidad / Unidad de medida / Descripción Detallada" (table header)
  "<cantidad> / <unidad_code> / <description_text>" — per line item
  "Código de identificación del Bien o Servicio <codigo>" — product code
  RUC blocks for emisor and receptor

Parsing strategy (label-defensive, position-tolerant):
  - Uses regex on the full ``get_text()`` output to find the labelled fields.
  - Does not depend on exact whitespace or table column positions.
  - Falls back gracefully when a field is absent (None for optional fields).
"""

from __future__ import annotations

import logging
import re
from datetime import date as datetime_date
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reconciliation.domain.models import OfficialGre

logger = logging.getLogger(__name__)

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
# Line-item table row.  SUNAT formats each line as:
# "<cantidad> / <unidad_code> / <description>"  (possibly with newlines)
# The raw text from get_text() may have the fields on consecutive lines or
# separated by slashes.  We try both patterns.
_LINE_ITEM_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*/\s*(\w+)\s*/\s*([^\n/]{5,}?)(?=\s*\d+(?:[.,]\d+)?\s*/|\Z|\n)",
    re.DOTALL,
)
# Product code that may follow the description
_PRODUCT_CODE_RE = re.compile(
    r"[Cc][oó]digo\s+de\s+identificaci[oó]n\s+del\s+Bien\s+o\s+Servicio\s+(\d+)",
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
        timeout_s:  HTTP request timeout in seconds (default 10).
        cache_dir:  Directory to cache downloaded GRE PDFs.  ``None`` disables
                    caching (useful in tests).  When ``None``, no file is written.
    """

    def __init__(
        self,
        timeout_s: float = 10.0,
        cache_dir: Path | None = None,
    ) -> None:
        self._timeout_s = timeout_s
        self._cache_dir = cache_dir

    # ------------------------------------------------------------------
    # SunatGreFetchPort interface
    # ------------------------------------------------------------------

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
        """Perform HTTP GET; validate Content-Type; return PDF bytes or None."""
        try:
            import httpx  # noqa: PLC0415
            _http_client = httpx
        except ImportError:
            # Fallback to stdlib urllib when httpx is absent
            _http_client = None  # type: ignore[assignment]

        if _http_client is not None:
            return self._download_httpx(url, _http_client)
        return self._download_urllib(url)

    def _download_httpx(self, url: str, httpx_module: object) -> bytes | None:
        """Download using httpx."""
        import httpx  # noqa: PLC0415

        try:
            resp = httpx.get(url, timeout=self._timeout_s, follow_redirects=True)
        except httpx.TimeoutException as exc:
            logger.warning("SunatDescargaqrAdapter: request timeout: %s", exc)
            return None
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
                "SunatDescargaqrAdapter: unexpected Content-Type %r (expected PDF)", content_type
            )
            return None

        return resp.content

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
    """Parse line items from the SUNAT PDF text.

    The SUNAT GRE PDF text layout for line items (as observed in spike #2750):

        Cantidad / Unidad de medida / Descripción Detallada
        0.192 / TONELADAS / BARRA A A615-G60 3/8" X 9M
        Código de identificación del Bien o Servicio 407797

    The table may span multiple lines.  We use a combination of the header
    anchor and per-row patterns.

    Returns a list of ``GreLineItem`` instances.
    """
    import re as _re  # noqa: PLC0415

    items = []

    # Find the table header to anchor the search region
    header_match = _re.search(
        r"Cantidad\s*/\s*Unidad\s+de\s+medida\s*/\s*Descripci[oó]n",
        text,
        _re.IGNORECASE,
    )
    # Search region: from header onward (or the whole text if not found)
    search_text = text[header_match.end():] if header_match else text

    # Match rows: decimal / UNIT_CODE / description
    # The description ends at next row OR end of section.
    # Qty may use '.' or ',' as decimal separator.
    row_re = _re.compile(
        r"(\d+(?:[.,]\d+)?)\s*/\s*([A-Z]{2,15})\s*/\s*([^\n]{5,100})",
        _re.IGNORECASE,
    )
    # Product codes follow descriptions (one per line item)
    code_re = _re.compile(
        r"[Cc][oó]digo\s+de\s+identificaci[oó]n\s+del\s+Bien\s+o\s+Servicio\s+(\d+)",
    )

    product_codes = code_re.findall(text)
    code_iter = iter(product_codes)

    for m in row_re.finditer(search_text):
        qty_str = m.group(1).replace(",", ".")
        unit_str = m.group(2).strip().upper()
        desc_str = m.group(3).strip()

        # Skip header-like rows
        if unit_str in ("DE", "MEDIDA") or "DESCRIPCI" in desc_str.upper():
            continue

        try:
            qty = Decimal(qty_str)
        except Exception:  # noqa: BLE001
            continue

        code: str | None = next(code_iter, None)

        items.append(
            GreLineItem(
                cantidad=qty,
                unidad=unit_str,
                descripcion=desc_str,
                codigo_producto=code,
            )
        )

    logger.debug(
        "_parse_line_items: found %d line items in SUNAT PDF text", len(items)
    )
    return items
