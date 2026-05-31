"""MaterialNormalizer — canonicalizes material description strings.

INVARIANTS (REC-001, REC-002):
- Only the *description* field is normalized.
- The *unit* field is NEVER touched.
- No external library dependencies — stdlib only (unicodedata).
"""

from __future__ import annotations

import unicodedata


class MaterialNormalizer:
    """Canonicalizes a raw material description.

    Spec: REC-001, REC-002.
    Operations applied (in order):
    1. Unicode NFC normalization.
    2. Lowercase.
    3. Strip leading/trailing whitespace.
    4. Collapse internal whitespace sequences to a single space.
    """

    def canonicalize(self, description: str) -> str:
        """Return the canonical form of *description*.

        The unit is NEVER passed to this method — callers keep it separate.

        Args:
            description: Raw description string (may contain extra spaces or accented chars).

        Returns:
            Canonical lowercase NFC description with collapsed whitespace.
        """
        if not description:
            return ""
        nfc = unicodedata.normalize("NFC", description)
        lowered = nfc.lower()
        stripped = lowered.strip()
        collapsed = " ".join(stripped.split())
        return collapsed
