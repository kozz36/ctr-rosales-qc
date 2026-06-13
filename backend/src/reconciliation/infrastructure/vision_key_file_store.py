"""VisionKeyFileStore — file-based adapter for VisionKeyStorePort.

Architecture:
  Implements VisionKeyStorePort (application/vision_key_store.py).
  Lives in infrastructure/ — NEVER imported from domain/ or application/.
  Mirrors the RunHistoryPort precedent.

Security invariants:
  - Key value NEVER logged (only "vision key file present/absent").
  - Atomic write: tmp file + os.replace (no partial-write exposure).
  - chmod 0600 on the key file after write.
  - Key stripped of whitespace on read.

Path default: /data/secrets/vision_api_key
  Overridable via RECONCILIATION_SECRETS_DIR environment variable.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_SECRETS_DIR = Path("/data/secrets")
_KEY_FILENAME = "vision_api_key"


class VisionKeyFileStore:
    """Concrete file-based implementation of VisionKeyStorePort (VKS-002).

    Args:
        secrets_dir: Directory where the key file is stored.
                     Defaults to RECONCILIATION_SECRETS_DIR env var,
                     falling back to /data/secrets.
    """

    def __init__(self, secrets_dir: Path | None = None) -> None:
        if secrets_dir is not None:
            self._secrets_dir = Path(secrets_dir)
        else:
            env_dir = os.environ.get("RECONCILIATION_SECRETS_DIR")
            self._secrets_dir = Path(env_dir) if env_dir else _DEFAULT_SECRETS_DIR

    @property
    def _key_path(self) -> Path:
        return self._secrets_dir / _KEY_FILENAME

    def read(self) -> str | None:
        """Return the stored key, or None when absent / empty / whitespace-only.

        Never raises on missing file or directory — returns None instead.
        """
        try:
            raw = self._key_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.debug("vision key file absent: %s", self._key_path)
            return None
        except OSError as exc:
            logger.warning("vision key file read error (non-fatal): %s", exc)
            return None

        value = raw.strip()
        if not value:
            logger.debug("vision key file empty/whitespace: %s", self._key_path)
            return None

        # Log presence only — NEVER log the key value itself.
        logger.info("vision key file found: %s", self._key_path)
        return value

    def write(self, key: str) -> None:
        """Persist *key* atomically with mode 0600, no TOCTOU window.

        Steps:
        1. mkdir(parents=True, exist_ok=True, mode=0o700) — secrets dir at 0700.
        2. Create .tmp file at exactly 0600 via os.open(O_WRONLY|O_CREAT|O_TRUNC|O_EXCL,
           0o600) — secret NEVER exists on disk at any other mode (TOCTOU fix).
           Handle EEXIST by unlinking a stale .tmp first.
        3. Write the encoded secret via os.write / os.close.
        4. os.replace(tmp → final path) — atomic rename.

        Key value NEVER logged; only path reference is logged.

        JD MEDIUM-3: the previous write_text()+chmod() pattern had a TOCTOU window
        where the .tmp file existed at umask 0644 with the secret already written.
        """
        self._secrets_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

        tmp_path = self._key_path.with_suffix(".tmp")
        tmp_str = str(tmp_path)
        encoded = key.encode("utf-8")

        # Create the tmp file at 0600 atomically — secret never exists at 0644.
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_EXCL
        try:
            fd = os.open(tmp_str, flags, 0o600)
        except FileExistsError:
            # Stale .tmp from a previous crashed write — unlink and retry once.
            try:
                os.unlink(tmp_str)
            except OSError:
                pass
            fd = os.open(tmp_str, flags, 0o600)

        try:
            os.write(fd, encoded)
        finally:
            os.close(fd)

        try:
            os.replace(tmp_str, str(self._key_path))
        except Exception:
            # Best-effort cleanup of tmp on failure.
            try:
                os.unlink(tmp_str)
            except OSError:
                pass
            raise

        logger.info("vision key file written: %s", self._key_path)

    def clear(self) -> None:
        """Remove the stored key file (idempotent — no-op if absent).

        After clear() + server restart, no key file → vision stays off.
        Key path NEVER logged (only whether absent or removed).
        """
        try:
            self._key_path.unlink()
            logger.info("vision key file cleared: %s", self._key_path)
        except FileNotFoundError:
            logger.debug("vision key file already absent on clear: %s", self._key_path)
        except OSError as exc:
            logger.warning("vision key file clear error (non-fatal): %s", exc)
            raise
