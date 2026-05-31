"""DigitalTextExtractionAdapter — ExtractionPort implementation for digital text.

Parses the DECLARED side from two kinds of pages that appear in the target PDF:

1. **Form Detail page** (``DECLARED`` kind, classified by PageClassifier as
   "FORM DETAIL"):
   - Contains a "Description\n<registro_num>" field (the Registro N°).
   - Contains a "Form date\n<Month DD, YYYY>" field (the declared date).
   - Contains a "Notes\n<material entries>" block where entries are
     separated by " / " (space-slash-space).  The PDF renderer wraps long
     lines by inserting a bare "\n" inside descriptions (e.g. "BARRA A615/\n
     A706"), so the parser must rejoin these before splitting.

2. **Protocolo de Recepción page** (``DECLARED`` kind, classified by
   PageClassifier as "PROTOCOLO DE RECEPCION"):
   - Contains "Registro N°:\nCONTRATANTE\n:\n<contractor>\n<registro_num>"
   - Contains a date in DD-MM-YY format immediately after the registro num.
   - Material lines follow a double \x14 marker and are one-per-line until
     the next \x14 or a date-stamp block.

Both page types are parsed into :class:`~reconciliation.domain.models.Registro`
objects with ``declared_lines`` populated.

**No OCR is performed.**  confidence is None (trusted digital source) per spec.

``extract_printed_table`` is a no-op stub — this adapter handles digital-text
DECLARED pages only; OCR is delegated to PrintedTableAdapter (task 3.3).
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Final

from reconciliation.domain.models import MaterialLine, Registro
from reconciliation.domain.normalizer import MaterialNormalizer

# ---------------------------------------------------------------------------
# Module-level normalizer (stateless, safe to share)
# ---------------------------------------------------------------------------

_NORMALIZER: Final[MaterialNormalizer] = MaterialNormalizer()

# ---------------------------------------------------------------------------
# Month lookup (English — "Form date" field uses English month names)
# ---------------------------------------------------------------------------

_MONTHS_EN: Final[dict[str, int]] = {
    # Full names
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    # 3-letter abbreviations (as output by Autodesk Forma)
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

# Detail page — description field: "\nDescription\n<num>\n"
_DETAIL_DESC_RE: Final[re.Pattern[str]] = re.compile(
    r"\nDescription\n(\S+)\s*\n"
)

# Detail page — form date: "\nForm date\n<Month> <DD>, <YYYY>\n"
_DETAIL_DATE_RE: Final[re.Pattern[str]] = re.compile(
    r"Form date\n(\w+)\s+(\d{1,2}),\s+(\d{4})"
)

# Detail page — notes block: "\nNotes\n<content>\nCreated by"
_DETAIL_NOTES_RE: Final[re.Pattern[str]] = re.compile(
    r"\nNotes\n(.*?)(?:\nCreated by|$)", re.DOTALL
)

# Protocolo page — registro num + date (DD-MM-YY)
# Structure: "Registro N°:\nCONTRATANTE\n:\n<contractor name>\n<num>\n<DD-MM-YY>"
_PROTO_REG_RE: Final[re.Pattern[str]] = re.compile(
    r"Registro N[^\n]*:\nCONTRATANTE\n:\n[^\n]+\n(\d+)\n(\d{2}-\d{2}-\d{2})"
)

# Material line grammar: "<DESCRIPTION> - <QTY> <UNIT>"
# UNIT options: TN, KG, RD, Rollo (case-insensitive)
_MATERIAL_LINE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(.+?)\s+-\s+(\d+(?:[.,]\d+)?)\s+(TN|KG|RD|Rollo)\s*$",
    re.IGNORECASE,
)

# Protocolo material block: lines between the double-\x14 header and the
# next \x14 section marker or end of text.
# Previously anchored on "BARRA" prefix which silently dropped non-BARRA
# materials (alambre, malla, clavos, etc.).  De-anchored to capture any
# non-empty, non-\x14 line after the double-\x14 marker.
_PROTO_BLOCK_RE: Final[re.Pattern[str]] = re.compile(
    r"\x14\n\x14\n((?:[^\x14\n][^\x14]*?(?:\n|$))+)",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_material_entry(raw: str) -> MaterialLine | None:
    """Parse a single '<DESCRIPTION> - <QTY> <UNIT>' entry.

    Returns None if the string does not match the grammar.
    Extracts verbatim (typos are preserved); MaterialNormalizer canonicalises
    the description but the raw value is also kept.
    """
    raw = raw.strip()
    if not raw:
        return None
    m = _MATERIAL_LINE_RE.match(raw)
    if not m:
        return None
    desc_raw = m.group(1).strip()
    qty_str = m.group(2).strip().replace(",", ".")
    unit_raw = m.group(3).strip()
    # Normalise unit to uppercase canonical
    unit = unit_raw.upper()
    # Rollo is mixed-case in source
    if unit not in {"TN", "KG", "RD", "ROLLO"}:
        return None
    # Map ROLLO → Rollo (Literal in model)
    unit_literal = "Rollo" if unit == "ROLLO" else unit  # type: ignore[assignment]

    try:
        cantidad = Decimal(qty_str)
    except InvalidOperation:
        return None

    return MaterialLine(
        description_raw=desc_raw,
        description_canonical=_NORMALIZER.canonicalize(desc_raw),
        unidad=unit_literal,  # type: ignore[arg-type]
        cantidad=cantidad,
        confidence=None,  # trusted digital source
    )


def _parse_notes_entries(notes_text: str) -> list[MaterialLine]:
    """Parse the Notes block from a Form Detail page.

    The Notes block contains material entries separated by " / ".
    The PDF renderer wraps long lines by inserting bare "\n" characters inside
    entries — including inside the description word ("BARRA A615/\\nA706").

    Normalisation strategy (applied in order):
    1. Rejoin a unit-on-next-line: "qty-digits \\nUNIT" → "qty-digits UNIT"
    2. Rejoin a bare "/\\n" that is a mid-description word wrap → "/"
    3. Collapse remaining bare "\\n" separators that the PDF inserted mid-entry
       into a single space (covers cases like "6.749 \\nTN" already handled,
       and edge-wrapped description text without a slash).
    4. Split on " / " (space-slash-space) — the true entry separator.
    5. Parse each entry through _parse_material_entry.
    """
    # Step 1: rejoin unit-on-next-line: "\d \nTN" → "\d TN"
    text = re.sub(
        r"(\d)\s*\n(TN|KG|RD|Rollo)",
        r"\1 \2",
        notes_text,
        flags=re.IGNORECASE,
    )
    # Step 2: rejoin bare "/\n" (mid-description PDF word-wrap)
    text = text.replace("/\n", "/")
    # Step 3: collapse remaining bare newlines
    text = text.replace("\n", " ")
    # Step 4: split on " / " separator (space-slash-space only — bare "/" in
    # descriptions like "A615/A706" must NOT be treated as a separator)
    parts = re.split(r" / ", text)

    lines: list[MaterialLine] = []
    for part in parts:
        entry = _parse_material_entry(part.strip())
        if entry is not None:
            lines.append(entry)
    return lines


def _parse_date_ddmmyy(date_str: str) -> date | None:
    """Parse DD-MM-YY format (used in Protocolo pages).

    Assumes 20XX century for the 2-digit year.
    Returns None on any parse failure.
    """
    parts = date_str.split("-")
    if len(parts) != 3:
        return None
    try:
        day, month, year_2d = int(parts[0]), int(parts[1]), int(parts[2])
        year = 2000 + year_2d
        return date(year, month, day)
    except (ValueError, OverflowError):
        return None


def _parse_date_english(month_name: str, day_str: str, year_str: str) -> date | None:
    """Parse 'Month DD, YYYY' format (used in Form Detail pages)."""
    month = _MONTHS_EN.get(month_name.lower())
    if month is None:
        return None
    try:
        return date(int(year_str), month, int(day_str))
    except (ValueError, OverflowError):
        return None


def _parse_proto_material_block(text: str) -> list[MaterialLine]:
    """Extract material lines from a Protocolo de Recepción page.

    The block begins after a double-\x14 marker.  Lines are one-per-newline.
    The last line may have multiple entries joined by " / ".
    Parsing stops at the first \x14 after the block.
    """
    m = _PROTO_BLOCK_RE.search(text)
    if not m:
        return []

    raw_block = m.group(1)
    # Expand any " / " separators on a single line (space-slash-space only)
    expanded = raw_block.replace(" / ", "\n")

    lines: list[MaterialLine] = []
    for raw_line in expanded.split("\n"):
        entry = _parse_material_entry(raw_line.strip())
        if entry is not None:
            lines.append(entry)
    return lines


# ---------------------------------------------------------------------------
# Public adapter
# ---------------------------------------------------------------------------


class DigitalTextExtractionAdapter:
    """Implements the extract_declared side of ExtractionPort for digital text.

    This adapter is responsible for the declared (trusted) side:
    - Form Detail pages:   uses Description/Form date/Notes fields
    - Protocolo pages:     uses Registro N°, DD-MM-YY date, and BARRA block

    extract_printed_table is a no-op — OCR-based extraction is handled by
    PrintedTableAdapter (task 3.3).
    """

    # ------------------------------------------------------------------
    # ExtractionPort interface
    # ------------------------------------------------------------------

    def extract_declared(self, text: str) -> list[MaterialLine]:
        """Extract material lines from a DECLARED page's digital text.

        The caller is responsible for passing only DECLARED pages.
        This method returns MaterialLine objects with confidence=None.

        Args:
            text: Full digital text of the page (as returned by page_text()).

        Returns:
            List of MaterialLine; empty list if no parseable content found.
        """
        # Determine page kind from heuristic signals
        if "PROTOCOLO DE RECEPCI" in text:
            return _parse_proto_material_block(text)
        if "Form detail" in text or "Form date" in text:
            return self._extract_from_detail_page(text)
        return []

    def extract_printed_table(self, image: bytes) -> list[MaterialLine]:  # noqa: ARG002
        """No-op — OCR table extraction is handled by PrintedTableAdapter."""
        return []

    # ------------------------------------------------------------------
    # Registro-level extraction (higher-level, used by pipeline adapter)
    # ------------------------------------------------------------------

    def extract_registro_from_detail_page(self, text: str, source_page: int) -> Registro | None:
        """Parse a Form Detail page and return a Registro, or None if unparseable.

        Args:
            text:        Full digital text of the detail page.
            source_page: 0-based page index (stored on each MaterialLine).

        Returns:
            Registro with numero, fecha_declarada, and declared_lines populated.
            None if the Description field is missing (not a valid detail page).
        """
        desc_m = _DETAIL_DESC_RE.search(text)
        if not desc_m:
            return None

        numero = desc_m.group(1).strip()

        fecha: date | None = None
        date_m = _DETAIL_DATE_RE.search(text)
        if date_m:
            fecha = _parse_date_english(
                date_m.group(1), date_m.group(2), date_m.group(3)
            )

        notes_m = _DETAIL_NOTES_RE.search(text)
        lines: list[MaterialLine] = []
        if notes_m:
            raw_lines = _parse_notes_entries(notes_m.group(1).strip())
            # Stamp source_page on each line
            lines = [l.model_copy(update={"source_page": source_page}) for l in raw_lines]

        return Registro(
            numero=numero,
            fecha_declarada=fecha,
            declared_lines=lines,
        )

    def extract_registro_from_proto_page(self, text: str, source_page: int) -> Registro | None:
        """Parse a Protocolo de Recepción page and return a Registro, or None.

        Args:
            text:        Full digital text of the protocolo page.
            source_page: 0-based page index.

        Returns:
            Registro with registro number, date, and material lines.
            None if the Registro N° field is not found.
        """
        reg_m = _PROTO_REG_RE.search(text)
        if not reg_m:
            return None

        numero = reg_m.group(1).strip()
        fecha = _parse_date_ddmmyy(reg_m.group(2))

        lines = _parse_proto_material_block(text)
        lines = [l.model_copy(update={"source_page": source_page}) for l in lines]

        return Registro(
            numero=numero,
            fecha_declarada=fecha,
            declared_lines=lines,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_from_detail_page(self, text: str) -> list[MaterialLine]:
        """Extract MaterialLine objects from a Form Detail page's Notes block."""
        notes_m = _DETAIL_NOTES_RE.search(text)
        if not notes_m:
            return []
        return _parse_notes_entries(notes_m.group(1).strip())
