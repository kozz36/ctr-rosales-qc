"""Section-ID guard — predicate that identifies Contents/section IDs (rev-2, EXT-018).

A "section ID" is the numeric identifier from the PDF Contents/TOC (e.g. ``4252``,
``4251``).  These are NOT valid Registro N° values.  The domain invariant is:

    Contents-ID ≠ Registro N° ≠ QR serie-numero

This module provides a configurable predicate ``is_section_id(value)`` that returns
``True`` when *value* matches the observed section-ID pattern, and ``False`` for
realistic Registro N° strings (e.g. ``"232"``, ``"100"``).

The predicate is configurable (not hardcoded to a single range) so it stays valid if
the PDF's Contents structure changes or a different PDF is processed.

Default pattern: ``^4\\d{3}$`` — matches 4-digit numbers in the 4000–4999 range,
which covers the observed CTR-PLC01 Contents IDs (e.g. 4250–4259).
"""

from __future__ import annotations

import re

# Default regex: 4-digit numbers in the 4000–4999 range.
# Rationale: observed CTR-PLC01 Contents entries are in the 425x range; the broader
# 4000–4999 guard is conservative and correct — no real Registro N° starts with 4xxx.
_DEFAULT_SECTION_ID_PATTERN = r"^4\d{3}$"


class SectionIdPredicate:
    """Configurable predicate that identifies PDF Contents/section IDs.

    Args:
        pattern: Regex pattern that matches section IDs (not Registro N° strings).
                 Default is ``^4\\d{3}$``.
    """

    def __init__(self, pattern: str = _DEFAULT_SECTION_ID_PATTERN) -> None:
        self._re = re.compile(pattern)

    def __call__(self, value: str | None) -> bool:
        """Return ``True`` when *value* matches the section-ID pattern.

        Returns ``False`` for ``None`` or empty string (no crash).
        """
        if not value:
            return False
        return bool(self._re.match(value))


# ---------------------------------------------------------------------------
# Module-level singleton using the default pattern (convenience import)
# ---------------------------------------------------------------------------

#: Default predicate instance using the CTR-PLC01 observed range.
#: Use ``SectionIdPredicate(pattern=...)`` to override for a different PDF.
is_section_id: SectionIdPredicate = SectionIdPredicate()
