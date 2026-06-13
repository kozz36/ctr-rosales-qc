"""VisionKeyStorePort and VisionKeyProbePort — application-layer Protocols.

Architecture invariant:
  This module is PURE typing — zero IO, zero SDK imports.
  Domain layer is never touched.
  Concrete adapters live in infrastructure/ and adapters/.

Covers VKS-002 (secret storage port) and VKS-001 (key probe port).
"""

from __future__ import annotations

import dataclasses
from typing import Literal, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# KeyProbeResult — value object returned by VisionKeyProbePort
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class KeyProbeResult:
    """Result of a key-validity probe (VKS-001).

    ok:     True when the key authenticated successfully.
    reason: Outcome discriminant — "valid", "unauthorized", "unreachable", "error".
    message: Human-readable summary; key value NEVER included.
    """

    ok: bool
    reason: Literal["valid", "unauthorized", "unreachable", "error"]
    message: str


# ---------------------------------------------------------------------------
# VisionKeyStorePort — abstract storage contract for the vision API key
# ---------------------------------------------------------------------------


@runtime_checkable
class VisionKeyStorePort(Protocol):
    """Port for persisting and reading the vision API key (VKS-002).

    Implementations MUST:
    - write(key): persist atomically; chmod 0600; never log the key value.
    - read():     return None when the file is absent or empty/whitespace.
    """

    def read(self) -> str | None:
        """Return the stored key, or None if absent / empty."""
        ...

    def write(self, key: str) -> None:
        """Persist the key atomically with mode 0600."""
        ...


# ---------------------------------------------------------------------------
# VisionKeyProbePort — abstract key-validity probe contract
# ---------------------------------------------------------------------------


@runtime_checkable
class VisionKeyProbePort(Protocol):
    """Port for probing whether a vision API key is valid (VKS-001).

    Implementations MUST:
    - Use the CANDIDATE key per call (not stored globally).
    - Distinguish 401 (unauthorized) from connection failure (unreachable).
    - NEVER include the key value in KeyProbeResult.message or any log line.
    - Lazy-import the underlying SDK inside the method.
    """

    def probe(self, key: str) -> KeyProbeResult:
        """Probe whether *key* authenticates successfully.

        Returns:
            KeyProbeResult(ok=True, reason="valid", ...) on success.
            KeyProbeResult(ok=False, reason="unauthorized", ...) on 401.
            KeyProbeResult(ok=False, reason="unreachable", ...) on connection/timeout.
            KeyProbeResult(ok=False, reason="error", ...) on unexpected failure.
        """
        ...
