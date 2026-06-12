"""Build a small front-subset of the real CTR PDF for the fast acceptance gate.

`make verify-fast` runs the SAME in-container R8 + R9 assertions
(test_container_verification.py) against the first N complete Protocolo sections
instead of all 493 pages — minutes instead of ~90 min on CPU.

Why a *section* boundary, not an arbitrary page cut: a registro's guías must stay
together or the summed quantity changes (registro 232 = 4.124 TN is the sum of
guías on pages 5/6/8). The default 50-page window ends just before the 4th
Protocolo (p51), keeping 3 complete sections — registro 232 plus two more so a
non-MATCH status exists for `test_multiple_status_types_present`.

Runs on the HOST (uses the backend venv's PyMuPDF); the override mounts the output
into the container and points CTR_PDF_PATH at it. fitz is imported lazily here and
never at any package boundary.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import fitz  # PyMuPDF — host-only build utility, not part of the package surface

_DEFAULT_SRC = (
    "/data/Projects/ctr-rosales-qc/"
    "Informe de detalle del formulario-202606020255.pdf"
)
_DEFAULT_PAGES = 50  # 3 Protocolo sections (boundaries at p4/p26/p38; p51 excluded)


def main() -> int:
    src = os.environ.get("CTR_PDF_PATH", _DEFAULT_SRC)
    pages = int(os.environ.get("CTR_VERIFY_SUBSET_PAGES", str(_DEFAULT_PAGES)))
    out = os.environ.get(
        "CTR_VERIFY_SUBSET_OUT", str(Path(src).parent / ".verify-subset.pdf")
    )

    if not Path(src).exists():
        print(f"ERROR: source PDF not found: {src!r}", file=sys.stderr)
        return 1

    doc = fitz.open(src)
    n = _section_safe_cut(doc, pages)
    subset = fitz.open()
    subset.insert_pdf(doc, from_page=0, to_page=n - 1)
    subset.save(out)
    print(f"Wrote {n}-page subset → {out} (source had {len(doc)} pages)")
    return 0


def _section_safe_cut(doc: "fitz.Document", budget: int) -> int:
    """Largest page count <= *budget* that ends on a Protocolo boundary.

    A registro's guías must stay together (splitting drops guías and breaks the
    summed quantity), so the cut must land just before a Protocolo page — never
    mid-section. Detects boundaries instead of trusting a magic constant, so a
    smaller CTR_VERIFY_SUBSET_PAGES override can never silently split a section.
    """
    proto = [
        i + 1
        for i in range(len(doc))
        if "PROTOCOLO DE RECEPCI" in doc[i].get_text().upper()
    ]
    # A complete section ends just before the NEXT Protocolo, so boundaries are the
    # pages immediately preceding each Protocolo after the first.
    section_cuts = [p - 1 for p in proto[1:]]
    safe = [c for c in section_cuts if c <= budget]
    if safe:
        return max(safe)
    if section_cuts:
        n = min(section_cuts)
        print(
            f"WARNING: budget {budget} < first whole section ({n} pages); "
            f"using {n} to keep a complete section.",
            file=sys.stderr,
        )
        return n
    print(
        "WARNING: no Protocolo pages detected; falling back to the raw page budget.",
        file=sys.stderr,
    )
    return min(budget, len(doc))


if __name__ == "__main__":
    raise SystemExit(main())
