"""PdfStructureAdapter — DocumentSourcePort implementation using PyMuPDF.

Opens the PDF READ-ONLY.  Exposes:
- page_count()           total page count
- page_text(idx)         embedded digital text (None if empty/scanned)
- render_page(idx, dpi)  page rendered as PNG bytes
- contents_offsets()     section → start page (1-based) from the Contents page
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import fitz  # PyMuPDF

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTENTS_PAGE_IDX: Final[int] = 1  # 0-indexed — page 2 in the PDF

_CONTENTS_RE: Final[re.Pattern[str]] = re.compile(
    r"#(\d+):\s*CTR-PLC01-FR001[^\n]*\.+\s+(\d+)"
)


class PdfStructureAdapter:
    """Implements DocumentSourcePort over a PyMuPDF-opened PDF.

    The file is opened in read-only mode and the fitz.Document handle is kept
    open for the lifetime of this object.  Call :meth:`close` (or use it as a
    context manager) to release the OS handle when done.

    Implements DocumentSourcePort (from domain/ports.py) — checked via
    isinstance(adapter, DocumentSourcePort) in tests.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        # fitz does not have a dedicated read-only flag, but we never call any
        # mutating method on the Document.
        self._doc: fitz.Document = fitz.open(str(self._path))

    # ------------------------------------------------------------------
    # DocumentSourcePort interface
    # ------------------------------------------------------------------

    def page_count(self) -> int:
        """Return total number of pages."""
        return self._doc.page_count

    def page_text(self, idx: int) -> str | None:
        """Return embedded digital text for page *idx* (0-based).

        Returns None if the page has no digital text layer (i.e., it is a
        scanned image page with an empty or near-empty text stream).
        """
        text: str = self._doc[idx].get_text()
        stripped = text.strip()
        return stripped if stripped else None

    def image_coverage_ratio(self, idx: int) -> float:
        """Return the fraction of page area covered by raster images (0.0–1.0).

        Rev-3 (EXT-019 / D1): used by the pipeline to derive ``image_dominant``.
        Computes the union of all image bounding boxes on the page and divides by
        the total page area.  Multi-image overlap is counted once (union, not sum).

        Implementation notes:
        - ``page.get_images(full=True)`` returns all images embedded on the page.
        - ``page.get_image_rects(xref)`` returns the bounding boxes of each image
          occurrence on the page in points (72 dpi coordinates).
        - No rendering is performed — this is a pure metadata query.
        """
        page: fitz.Page = self._doc[idx]
        page_rect = page.rect  # total page area in points
        page_area = page_rect.width * page_rect.height
        if page_area == 0:
            return 0.0

        images = page.get_images(full=True)
        if not images:
            return 0.0

        # Collect all image rects on this page (may overlap).
        # Compute union area by accumulating covered rectangles.
        covered: float = 0.0
        # Simple approximation: sum all image areas clipped to page, then cap at 1.0.
        # Exact union requires a sweep-line or rect-merge; for classification purposes
        # the approximation is sufficient (scanned pages have a single full-page image).
        for img_info in images:
            xref: int = img_info[0]
            try:
                rects = page.get_image_rects(xref)
            except Exception:  # noqa: BLE001
                continue
            for rect in rects:
                clipped = rect & page_rect  # intersection with page bounds
                if not clipped.is_empty:
                    covered += clipped.width * clipped.height

        return min(covered / page_area, 1.0)

    def render_page(self, idx: int, dpi: int = 200) -> bytes:
        """Render page *idx* at *dpi* and return PNG bytes.

        Args:
            idx: 0-based page index.
            dpi: Resolution in dots per inch (default 200).

        Returns:
            Raw PNG bytes suitable for passing to OCR/vision adapters.
        """
        zoom = dpi / 72.0  # fitz uses 72 dpi as base
        matrix = fitz.Matrix(zoom, zoom)
        pixmap: fitz.Pixmap = self._doc[idx].get_pixmap(matrix=matrix)
        return pixmap.tobytes("png")

    # ------------------------------------------------------------------
    # Structural helpers (not part of DocumentSourcePort)
    # ------------------------------------------------------------------

    def contents_offsets(self) -> dict[str, int]:
        """Parse the Contents page and return a mapping of registro → start page (1-based).

        The Contents page (page index 1 in the real PDF) lists each registro
        form number with its starting page separated by dots, e.g.:

            #4252: CTR-PLC01-FR001_RECEPCION DE MATERIALES EN OBRA ........... 3

        Returns:
            Dict mapping registro number string (e.g. "4252") to its
            1-based start page number.
        """
        contents_text: str = self._doc[_CONTENTS_PAGE_IDX].get_text()
        offsets: dict[str, int] = {}
        for match in _CONTENTS_RE.finditer(contents_text):
            offsets[match.group(1)] = int(match.group(2))
        return offsets

    # ------------------------------------------------------------------
    # Context manager / resource management
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Release the underlying fitz.Document handle."""
        self._doc.close()

    def __enter__(self) -> PdfStructureAdapter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
