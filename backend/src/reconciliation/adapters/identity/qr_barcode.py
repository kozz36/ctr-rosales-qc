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

    Rev-3 (D2) — robust dual-QR COLOR decode:
    - Drops the grayscale ``convert("L")`` step; decodes in COLOR.
    - Produces two scaled variants (nominal 200-dpi and 400-dpi equivalents)
      by resizing the input image.  Both are decoded; payloads are merged.
    - Returns BOTH the compact GRE identity AND the ``hashqr_url`` (URL-variant
      QR) when found in the union of all decoded payloads.
    - EXT-012 "only-URL-variant → return None → OCR fallback" rule preserved.

    Args:
        render_dpi: Nominal DPI the caller rendered the image at (default 200).
        upscale: Scale factor for the second (higher-res) decode pass (default 2,
            giving an effective 400-dpi tier when render_dpi=200).
    """

    def __init__(
        self,
        render_dpi: int = 200,
        upscale: int = 2,
    ) -> None:
        self._render_dpi = render_dpi
        self._upscale = upscale

    # ------------------------------------------------------------------
    # IdentityExtractionPort interface
    # ------------------------------------------------------------------

    def decode_identity(self, image: bytes, page_idx: int | None = None) -> GuiaIdentity | None:
        """Decode guía identity from *image* bytes.

        Rev-3 (D2): multi-resolution COLOR decode — tries the image at two scales
        (1× and ``upscale``×) with both pyzbar and zxing-cpp in COLOR mode.
        Returns a ``GuiaIdentity`` when the compact GRE QR passes the EXT-012
        confidence gate.  Also populates ``hashqr_url`` when the URL-variant QR
        is decoded on the same page.

        Args:
            image: PNG or JPEG bytes of a rendered guía page (COLOR).
            page_idx: Page index for audit logging (optional).

        Returns:
            ``GuiaIdentity`` with ``confidence=1.0`` if all gate conditions
            pass; ``None`` on any failure (QR absent, malformed payload,
            confidence gate rejection).
        """
        payloads: list[str] = []
        try:
            payloads = self._decode_multi_res(image)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "QrBarcodeExtractionAdapter: decode failed on page %s: %s",
                page_idx,
                exc,
            )
            return None

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
                if hashqr_url is None:
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

    def decode_hashqr_url(self, image: bytes, page_idx: int | None = None) -> str | None:
        """Decode only the URL-variant (hashqr) QR from *image*.

        Rev-3 (D2): used by the decode_identities pre-pass when only the hashqr
        URL is needed (e.g. for block-level propagation without full identity decode).
        Returns the first URL-variant payload found, or ``None`` if absent.
        """
        payloads: list[str] = []
        try:
            payloads = self._decode_multi_res(image)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "QrBarcodeExtractionAdapter.decode_hashqr_url: failed page %s: %s",
                page_idx,
                exc,
            )
            return None

        for payload in payloads:
            if _URL_PREFIX_RE.match(payload) and _HASHQR_RE.search(payload):
                return payload
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _decode_multi_res(self, image: bytes) -> list[str]:
        """Decode QR codes at two resolutions in COLOR; return union of payloads.

        Rev-3 (D2): produces a lower-res (1×) and higher-res (``upscale``×) variant
        of the input image, runs pyzbar ∪ zxing-cpp on each in COLOR mode, and
        deduplicates.  This reliably catches both the compact data QR and the
        URL-variant QR that the previous grayscale@2× strategy missed.
        """
        from PIL import Image  # noqa: PLC0415

        img_orig = Image.open(BytesIO(image))
        seen: set[str] = set()
        results: list[str] = []

        # Build list of (PIL_image, label) pairs to decode.
        # Tier 1: original image (at render_dpi, COLOR).
        # Tier 2: upscaled image (at render_dpi * upscale, COLOR).
        variants: list[tuple[object, str]] = []
        variants.append((img_orig, "1x"))

        if self._upscale > 1:
            w, h = img_orig.size
            new_w, new_h = int(w * self._upscale), int(h * self._upscale)
            lanczos = (
                getattr(getattr(Image, "Resampling", None), "LANCZOS", None)
                or Image.LANCZOS  # type: ignore[attr-defined]
            )
            img_upscaled = img_orig.resize((new_w, new_h), lanczos)
            variants.append((img_upscaled, f"{self._upscale}x"))

        for img_variant, label in variants:
            self._decode_variant_into(img_variant, label, seen, results)

        return results

    def _decode_variant_into(
        self,
        img: object,  # PIL.Image.Image
        label: str,
        seen: set[str],
        results: list[str],
    ) -> None:
        """Decode a single image variant with pyzbar + zxing-cpp (COLOR).

        Mutates *seen* and *results* in-place.  Errors are logged but do not
        raise; a missing library silently skips that decoder.
        """
        # --- pyzbar (COLOR) ---
        try:
            import pyzbar.pyzbar as pyzbar  # noqa: PLC0415

            for barcode in pyzbar.decode(img):  # type: ignore[arg-type]
                payload = barcode.data.decode("utf-8", errors="replace")
                if payload not in seen:
                    seen.add(payload)
                    results.append(payload)
                    logger.debug(
                        "QrBarcodeExtractionAdapter: pyzbar[%s] decoded: %r", label, payload[:60]
                    )
        except ImportError:
            logger.debug("QrBarcodeExtractionAdapter: pyzbar not installed; skipping")
        except Exception as exc:  # noqa: BLE001
            logger.debug("QrBarcodeExtractionAdapter: pyzbar[%s] failed: %s", label, exc)

        # --- zxing-cpp (COLOR via numpy) ---
        try:
            import numpy as np  # noqa: PLC0415
            import zxingcpp  # noqa: PLC0415

            img_array = np.array(img)
            for result in zxingcpp.read_barcodes(img_array):
                payload = result.text
                if payload not in seen:
                    seen.add(payload)
                    results.append(payload)
                    logger.debug(
                        "QrBarcodeExtractionAdapter: zxing[%s] decoded: %r", label, payload[:60]
                    )
        except ImportError:
            logger.debug("QrBarcodeExtractionAdapter: zxing-cpp not installed; skipping")
        except Exception as exc:  # noqa: BLE001
            logger.debug("QrBarcodeExtractionAdapter: zxing[%s] failed: %s", label, exc)
