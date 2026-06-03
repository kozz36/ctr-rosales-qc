"""Persistent OCR-capability failure detection (shared by Paddle adapters).

Some PaddlePaddle builds (oneDNN / PIR / IR-conversion issues) fail at
``predict()`` time for EVERY image with the same capability error — the model
loads, but inference is structurally impossible in this environment.  Retrying
per page is futile and, across hundreds of guía pages, an impassable wall.

This helper classifies an exception as a PERSISTENT capability failure so the
calling adapter can set ``_unavailable=True`` and short-circuit subsequent
calls.  Generic per-image errors (a single bad image, transient CUDA OOM) are
NOT persistent and keep the existing per-page graceful-degradation behaviour.
"""

from __future__ import annotations

# Substrings that identify a structural PaddlePaddle capability failure.
_PERSISTENT_MARKERS: tuple[str, ...] = (
    "ConvertPirAttribute",
    "oneDNN",
    "Unimplemented",
    "PIR",
)


def is_persistent_capability_failure(exc: BaseException) -> bool:
    """Return True when *exc* indicates inference is impossible in this env.

    Treat ``NotImplementedError`` as persistent, plus any exception whose
    message contains a known structural marker (oneDNN / PIR / IR conversion).
    """
    if isinstance(exc, NotImplementedError):
        return True
    message = str(exc)
    return any(marker in message for marker in _PERSISTENT_MARKERS)
