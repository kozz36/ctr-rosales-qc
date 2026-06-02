"""QrBarcodeExtractionAdapter — local QR/barcode decode for guía identity (rev-2).

Implements ``IdentityExtractionPort`` using local image processing only.
No network call is made.

**Lazy imports**: ``pyzbar`` and ``zxing-cpp`` are imported INSIDE the decode
method body — NEVER at module level — so the test suite runs without them
installed (EXT-012 requirement).

**Decoder union**: both pyzbar and zxing-cpp are attempted; the result is the
union of decoded payloads from both.  Either decoder alone is insufficient:
zbar requires the 2× upscale for small QR codes; zxing-cpp catches the
URL-variant QR and pages that pyzbar misses (EXT-012).

**Compact GRE QR format** (pipe-delimited, positional):
    RUC_emisor | tipo | serie | numero | doc_type_code | RUC_receptor
    Example: ``20370146994|09|T009|0741770|6|20613231871``

**Confidence gate**: 1.0 iff ALL: ruc_emisor and ruc_receptor are exactly 11
numeric digits, tipo ∈ {09, 31}, serie and numero non-empty.  Any failure →
return None; log failure.  (EXT-012)

**URL-variant QR**: payload beginning with http(s):// and containing ``hashqr=``
→ stored in GuiaIdentity.hashqr_url; not parsed as a data QR.  If ONLY a
URL-variant QR is decoded and no compact data QR is present, return None →
caller falls back to OCR identity (EXT-014, risk-3 defensive scenario).
"""

from __future__ import annotations

import logging
import re
from io import BytesIO

from reconciliation.domain.errors import IdentityDecodeError
from reconciliation.domain.models import GuiaIdentity

logger = logging.getLogger(__name__)

# Compact SUNAT GRE QR pipe-count heuristic: must have at least 5 pipes (6+ fields).
_MIN_PIPE_COUNT = 5

# RUC must be exactly 11 numeric digits.
_RUC_RE = re.compile(r"^\d{11}$")

# URL-variant QR detection.
_URL_PREFIX_RE = re.compile(r"^https?://", re.IGNORECASE)
_HASHQR_RE = re.compile(r"hashqr=", re.IGNORECASE)

# Valid tipo codes (EXT-012 confidence gate).
_VALID_TIPO = {"09", "31"}


# ---------------------------------------------------------------------------
# Pure parse function (testable without pyzbar/zxing-cpp installed)
# ---------------------------------------------------------------------------


def parse_compact_gre_qr(payload: str) -> dict[str, str] | None:
    """Parse a compact SUNAT GRE QR payload into field dict.

    Format: ``RUC_emisor|tipo|serie|numero|doc_type_code|RUC_receptor``
    Returns a dict with keys ``ruc_emisor``, ``tipo``, ``serie``, ``numero``,
    ``ruc_receptor``, or ``None`` if the payload cannot be parsed.

    This function is pure (no IO) and can be tested without the barcode libs.
    """
    if payload.count("|") < _MIN_PIPE_COUNT:
        return None

    parts = payload.split("|")
    if len(parts) < 6:  # noqa: PLR2004
        return None

    return {
        "ruc_emisor": parts[0].strip(),
        "tipo": parts[1].strip(),
        "serie": parts[2].strip(),
        "numero": parts[3].strip(),
        # parts[4] = doc_type_code — not exposed in GuiaIdentity
        "ruc_receptor": parts[5].strip(),
    }


def build_guia_identity(
    fields: dict[str, str],
    hashqr_url: str | None,
    page_idx: int | None = None,
) -> GuiaIdentity | None:
    """Apply confidence gate and build GuiaIdentity from parsed fields.

    Returns ``None`` and logs a structured error when any gate condition fails.
    This function is pure (no IO) and testable without barcode libraries.

    Args:
        fields: Dict returned by ``parse_compact_gre_qr``.
        hashqr_url: URL-variant QR value if decoded on the same page.
        page_idx: Page index for audit logging (optional).
    """
    ruc_emisor = fields.get("ruc_emisor", "")
    ruc_receptor = fields.get("ruc_receptor", "")
    tipo = fields.get("tipo", "")
    serie = fields.get("serie", "")
    numero = fields.get("numero", "")

    gate_failures: list[str] = []

    if not _RUC_RE.match(ruc_emisor):
        gate_failures.append(f"ruc_emisor={ruc_emisor!r} is not 11 digits")
    if not _RUC_RE.match(ruc_receptor):
        gate_failures.append(f"ruc_receptor={ruc_receptor!r} is not 11 digits")
    if tipo not in _VALID_TIPO:
        gate_failures.append(f"tipo={tipo!r} not in {_VALID_TIPO}")
    if not serie:
        gate_failures.append("serie is empty")
    if not numero:
        gate_failures.append("numero is empty")

    if gate_failures:
        err = IdentityDecodeError(
            "QR confidence gate failed — returning None",
            detail={"page_idx": page_idx, "failures": gate_failures},
        )
        logger.warning(
            "QrBarcodeExtractionAdapter: confidence gate failed on page %s: %s",
            page_idx,
            gate_failures,
        )
        # Log the error to audit trail but do NOT raise — degrade to OCR fallback.
        _ = err  # referenced for clarity; caller sees None
        return None

    return GuiaIdentity(
        serie=serie,
        numero=numero,
        ruc_emisor=ruc_emisor,
        ruc_receptor=ruc_receptor,
        tipo=tipo,
        hashqr_url=hashqr_url,
        confidence=1.0,
    )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class QrBarcodeExtractionAdapter:
    """Decode guía identity from a QR code on the page image.

    Implements ``IdentityExtractionPort``.  Uses only local image processing;
    no network call is made.

    ``pyzbar`` and ``zxing-cpp`` are lazy-imported inside ``decode_identity``
    so the test suite can import this module without those libraries installed.

    Args:
        render_dpi: Nominal DPI for page rendering (default 150 as per EXT-012).
        upscale: Grayscale upscale factor applied before decode (default 2).
    """

    def __init__(
        self,
        render_dpi: int = 150,
        upscale: int = 2,
    ) -> None:
        self._render_dpi = render_dpi
        self._upscale = upscale

    # ------------------------------------------------------------------
    # IdentityExtractionPort interface
    # ------------------------------------------------------------------

    def decode_identity(self, image: bytes, page_idx: int | None = None) -> GuiaIdentity | None:
        """Decode guía identity from *image* bytes.

        Renders the image at ``render_dpi`` × ``upscale`` grayscale effective
        resolution, attempts decode with both pyzbar and zxing-cpp (union),
        then parses the compact GRE QR payload.

        Args:
            image: PNG or JPEG bytes of a rendered guía page.
            page_idx: Page index for audit logging (optional).

        Returns:
            ``GuiaIdentity`` with ``confidence=1.0`` if all gate conditions
            pass; ``None`` on any failure (QR absent, malformed payload,
            confidence gate rejection).
        """
        try:
            processed = self._preprocess(image)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "QrBarcodeExtractionAdapter: image preprocessing failed on page %s: %s",
                page_idx,
                exc,
            )
            return None

        payloads: list[str] = self._decode_union(processed)

        if not payloads:
            logger.debug(
                "QrBarcodeExtractionAdapter: no QR/barcode decoded on page %s", page_idx
            )
            return None

        data_payload: str | None = None
        hashqr_url: str | None = None

        for payload in payloads:
            if _URL_PREFIX_RE.match(payload) and _HASHQR_RE.search(payload):
                # URL-variant QR — store and skip data parse
                hashqr_url = payload
                logger.debug(
                    "QrBarcodeExtractionAdapter: URL-variant QR on page %s: %r",
                    page_idx,
                    payload[:80],
                )
            else:
                # Candidate compact data QR — pick the first parseable one
                if data_payload is None:
                    data_payload = payload

        if data_payload is None:
            # Only a URL-variant QR was found — no compact data payload
            # Risk-3 defensive: return None → OCR fallback (EXT-014)
            logger.info(
                "QrBarcodeExtractionAdapter: only URL-variant QR on page %s; "
                "falling back to OCR identity",
                page_idx,
            )
            return None

        fields = parse_compact_gre_qr(data_payload)
        if fields is None:
            logger.warning(
                "QrBarcodeExtractionAdapter: payload not parseable as compact GRE QR on "
                "page %s: %r",
                page_idx,
                data_payload[:80],
            )
            return None

        return build_guia_identity(fields, hashqr_url, page_idx)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _preprocess(self, image: bytes) -> bytes:
        """Apply grayscale upscale to *image* and return processed bytes.

        Returns PIL image bytes at effective decode resolution.
        Lazy-imports PIL so the test suite can stub this out.
        """
        from PIL import Image  # noqa: PLC0415

        img = Image.open(BytesIO(image))
        w, h = img.size
        new_w = int(w * self._upscale)
        new_h = int(h * self._upscale)
        # Image.Resampling.LANCZOS (Pillow ≥9) / Image.LANCZOS (legacy).
        lanczos = getattr(getattr(Image, "Resampling", None), "LANCZOS", None) or Image.LANCZOS  # type: ignore[attr-defined]
        resized = img.resize((new_w, new_h), lanczos)
        img = resized.convert("L")  # type: ignore[assignment]  # grayscale; ImageFile→Image
        out = BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()

    def _decode_union(self, image: bytes) -> list[str]:
        """Attempt decode with pyzbar + zxing-cpp; return union of payloads.

        Both decoders are attempted regardless of the first result.  Any
        ImportError is caught silently — the absent library is skipped and
        the other is still tried.
        """
        from PIL import Image  # noqa: PLC0415

        img = Image.open(BytesIO(image))
        seen: set[str] = set()
        results: list[str] = []

        # --- pyzbar ---
        try:
            import pyzbar.pyzbar as pyzbar  # noqa: PLC0415

            for barcode in pyzbar.decode(img):
                payload = barcode.data.decode("utf-8", errors="replace")
                if payload not in seen:
                    seen.add(payload)
                    results.append(payload)
        except ImportError:
            logger.debug("QrBarcodeExtractionAdapter: pyzbar not installed; skipping")
        except Exception as exc:  # noqa: BLE001
            logger.debug("QrBarcodeExtractionAdapter: pyzbar failed: %s", exc)

        # --- zxing-cpp ---
        try:
            import numpy as np  # noqa: PLC0415
            import zxingcpp  # noqa: PLC0415

            img_array = np.array(img)
            for result in zxingcpp.read_barcodes(img_array):
                payload = result.text
                if payload not in seen:
                    seen.add(payload)
                    results.append(payload)
        except ImportError:
            logger.debug("QrBarcodeExtractionAdapter: zxing-cpp not installed; skipping")
        except Exception as exc:  # noqa: BLE001
            logger.debug("QrBarcodeExtractionAdapter: zxing-cpp failed: %s", exc)

        return results
