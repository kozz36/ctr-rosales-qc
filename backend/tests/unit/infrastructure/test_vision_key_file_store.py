"""Failing tests for VisionKeyFileStore.

Task 2.1 RED — VKS-002-S01/S02.
These tests will FAIL until infrastructure/vision_key_file_store.py is created
(Task 2.2 GREEN).
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest


class TestVisionKeyFileStoreRead:
    """VisionKeyFileStore.read() contract."""

    def test_read_returns_none_when_dir_missing(self, tmp_path: Path) -> None:
        """read() returns None (no exception) when secrets dir does not exist (VKS-002-S01)."""
        from reconciliation.infrastructure.vision_key_file_store import VisionKeyFileStore  # noqa: PLC0415

        store = VisionKeyFileStore(secrets_dir=tmp_path / "no-such-dir")
        result = store.read()
        assert result is None

    def test_read_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        """read() returns None when dir exists but key file is absent."""
        from reconciliation.infrastructure.vision_key_file_store import VisionKeyFileStore  # noqa: PLC0415

        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        store = VisionKeyFileStore(secrets_dir=secrets_dir)
        result = store.read()
        assert result is None

    def test_read_returns_none_when_file_empty(self, tmp_path: Path) -> None:
        """read() returns None when key file is empty (VKS-002-S01)."""
        from reconciliation.infrastructure.vision_key_file_store import VisionKeyFileStore  # noqa: PLC0415

        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        key_file = secrets_dir / "vision_api_key"
        key_file.write_text("", encoding="utf-8")
        store = VisionKeyFileStore(secrets_dir=secrets_dir)
        result = store.read()
        assert result is None

    def test_read_returns_none_when_file_whitespace_only(self, tmp_path: Path) -> None:
        """read() returns None when key file contains only whitespace."""
        from reconciliation.infrastructure.vision_key_file_store import VisionKeyFileStore  # noqa: PLC0415

        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        key_file = secrets_dir / "vision_api_key"
        key_file.write_text("   \n\t  ", encoding="utf-8")
        store = VisionKeyFileStore(secrets_dir=secrets_dir)
        result = store.read()
        assert result is None

    def test_read_strips_whitespace(self, tmp_path: Path) -> None:
        """read() returns stripped key value (no leading/trailing whitespace)."""
        from reconciliation.infrastructure.vision_key_file_store import VisionKeyFileStore  # noqa: PLC0415

        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        key_file = secrets_dir / "vision_api_key"
        key_file.write_text("  my-api-key  \n", encoding="utf-8")
        store = VisionKeyFileStore(secrets_dir=secrets_dir)
        result = store.read()
        assert result == "my-api-key"


class TestVisionKeyFileStoreWrite:
    """VisionKeyFileStore.write() contract."""

    def test_write_creates_file(self, tmp_path: Path) -> None:
        """write() creates the key file with the stored value (VKS-002-S02)."""
        from reconciliation.infrastructure.vision_key_file_store import VisionKeyFileStore  # noqa: PLC0415

        secrets_dir = tmp_path / "secrets"
        store = VisionKeyFileStore(secrets_dir=secrets_dir)
        store.write("test-api-key-abc")
        key_file = secrets_dir / "vision_api_key"
        assert key_file.exists()
        assert key_file.read_text(encoding="utf-8").strip() == "test-api-key-abc"

    def test_write_creates_parent_dir(self, tmp_path: Path) -> None:
        """write() creates the secrets dir with mkdir(parents=True, exist_ok=True)."""
        from reconciliation.infrastructure.vision_key_file_store import VisionKeyFileStore  # noqa: PLC0415

        secrets_dir = tmp_path / "deep" / "nested" / "secrets"
        store = VisionKeyFileStore(secrets_dir=secrets_dir)
        store.write("some-key")
        assert secrets_dir.is_dir()

    def test_write_sets_chmod_0600(self, tmp_path: Path) -> None:
        """write() sets file permissions to 0600 (owner read/write only)."""
        from reconciliation.infrastructure.vision_key_file_store import VisionKeyFileStore  # noqa: PLC0415

        secrets_dir = tmp_path / "secrets"
        store = VisionKeyFileStore(secrets_dir=secrets_dir)
        store.write("my-secret-key")
        key_file = secrets_dir / "vision_api_key"
        mode = stat.S_IMODE(key_file.stat().st_mode)
        assert mode == 0o600, f"Expected 0600, got {oct(mode)}"

    def test_write_is_atomic_via_tmp_replace(self, tmp_path: Path) -> None:
        """write() uses tmp file + os.replace — no partial-write exposure.

        We verify the final file is written correctly (atomic semantic).
        A proper partial-write test would require OS-level injection;
        here we verify that write succeeds even when the key file already exists.
        """
        from reconciliation.infrastructure.vision_key_file_store import VisionKeyFileStore  # noqa: PLC0415

        secrets_dir = tmp_path / "secrets"
        store = VisionKeyFileStore(secrets_dir=secrets_dir)
        store.write("first-key")
        store.write("second-key")  # overwrite
        result = store.read()
        assert result == "second-key"


class TestVisionKeyFileStoreRoundtrip:
    """VisionKeyFileStore write + read roundtrip."""

    def test_roundtrip(self, tmp_path: Path) -> None:
        """write(key) then read() returns the same key."""
        from reconciliation.infrastructure.vision_key_file_store import VisionKeyFileStore  # noqa: PLC0415

        store = VisionKeyFileStore(secrets_dir=tmp_path / "secrets")
        store.write("roundtrip-test-key-xyz")
        assert store.read() == "roundtrip-test-key-xyz"
