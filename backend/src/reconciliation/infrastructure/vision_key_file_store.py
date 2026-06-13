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
import stat
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
        """Persist *key* atomically with mode 0600.

        Steps:
        1. mkdir(parents=True, exist_ok=True) for the secrets dir.
        2. Write to a tmp file in the same dir (same-fs → os.replace is atomic).
        3. chmod 0600 on the tmp file.
        4. os.replace(tmp → final path) — atomic rename.

        Key value NEVER logged; only path reference is logged.
        """
        self._secrets_dir.mkdir(parents=True, exist_ok=True)

        tmp_path = self._key_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(key, encoding="utf-8")
            # Set 0600 BEFORE the atomic rename so the final path is never
            # readable by other users even for the brief rename window.
            os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
            os.replace(tmp_path, self._key_path)
        except Exception:
            # Best-effort cleanup of tmp on failure.
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

        logger.info("vision key file written: %s", self._key_path)
