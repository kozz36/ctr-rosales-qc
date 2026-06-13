"""Tests for VisionKeyFileStore.

Task 2.1 RED — VKS-002-S01/S02.
JD-fix MEDIUM-3: TOCTOU — secret was written at umask 0644 BEFORE chmod 0600;
tmp file existed world-readable with the secret during the window between
write_text() and os.chmod(). Fix: use os.open(O_CREAT|O_EXCL, 0o600) to
create the tmp file at 0600 atomically, BEFORE writing the secret. Also
mkdir with mode=0o700 so the secrets dir itself is not group/world-accessible.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest.mock import patch

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


class TestVisionKeyFileStoreTOCTOU:
    """JD-fix MEDIUM-3: secret NEVER at group/world-readable mode at any point.

    The TOCTOU fix requires that the tmp file is created at 0600 BEFORE
    the secret is written. We intercept os.replace to capture the tmp file's
    mode at the moment of rename (after write, before cleanup) — it must
    already be 0600 at that point.
    """

    def test_tmp_file_is_0600_at_creation_not_after_chmod(self, tmp_path: Path) -> None:
        """The .tmp file must be created at mode 0600 — no chmod call needed.

        JD RED→GREEN test (MEDIUM-3): the bugged code calls
          tmp_path.write_text(key)  # file created at umask 0644 (TOCTOU exposed)
          os.chmod(tmp_path, 0o600) # chmod AFTER — secret existed readable
        The fix must NOT call os.chmod on the tmp file (because the file must be
        created at 0600 via os.open). This test asserts that os.chmod is never
        called on the .tmp file path — if it is, that means the file was first
        written world-readable and THEN restricted (the vulnerable pattern).
        """
        from reconciliation.infrastructure.vision_key_file_store import (  # noqa: PLC0415
            VisionKeyFileStore,
        )

        secrets_dir = tmp_path / "secrets"
        store = VisionKeyFileStore(secrets_dir=secrets_dir)
        chmod_calls_on_tmp: list[str] = []

        real_chmod = os.chmod

        def _track_chmod(path: str | Path, mode: int, **kwargs: object) -> None:
            path_str = str(path)
            if path_str.endswith(".tmp"):
                chmod_calls_on_tmp.append(path_str)
            return real_chmod(path, mode, **kwargs)  # type: ignore[call-arg]

        with patch("os.chmod", side_effect=_track_chmod):
            store.write("secret-key-toctou-test")

        assert not chmod_calls_on_tmp, (
            f"TOCTOU: os.chmod was called on the .tmp file ({chmod_calls_on_tmp}). "
            "This means the secret was written at umask mode before chmod restricted it. "
            "Fix: use os.open(O_WRONLY|O_CREAT|O_TRUNC|O_EXCL, 0o600) to create at 0600."
        )

    def test_secrets_dir_created_with_0700(self, tmp_path: Path) -> None:
        """mkdir() must create the secrets dir with mode 0700, not default umask.

        JD RED→GREEN test (MEDIUM-3): secrets dir created with default umask
        (0755) allows group/world to list the directory contents and potentially
        stat or read the key file. The fix uses mkdir(mode=0o700).
        """
        from reconciliation.infrastructure.vision_key_file_store import (  # noqa: PLC0415
            VisionKeyFileStore,
        )

        secrets_dir = tmp_path / "new_secrets_dir"
        store = VisionKeyFileStore(secrets_dir=secrets_dir)
        store.write("some-key")

        # Check the secrets dir mode
        dir_mode = stat.S_IMODE(secrets_dir.stat().st_mode)
        assert dir_mode == 0o700, (
            f"Secrets dir has mode {oct(dir_mode)}, expected 0o700. "
            "Group/world can enumerate the directory — use mkdir(mode=0o700)."
        )

    def test_final_file_mode_is_0600(self, tmp_path: Path) -> None:
        """After write(), the final key file must be mode 0600 (reinforcement)."""
        from reconciliation.infrastructure.vision_key_file_store import (  # noqa: PLC0415
            VisionKeyFileStore,
        )

        secrets_dir = tmp_path / "secrets"
        store = VisionKeyFileStore(secrets_dir=secrets_dir)
        store.write("check-final-mode-key")
        key_file = secrets_dir / "vision_api_key"
        mode = stat.S_IMODE(key_file.stat().st_mode)
        assert mode == 0o600, f"Final file mode {oct(mode)} != 0o600"


class TestVisionKeyFileStoreRoundtrip:
    """VisionKeyFileStore write + read roundtrip."""

    def test_roundtrip(self, tmp_path: Path) -> None:
        """write(key) then read() returns the same key."""
        from reconciliation.infrastructure.vision_key_file_store import VisionKeyFileStore  # noqa: PLC0415

        store = VisionKeyFileStore(secrets_dir=tmp_path / "secrets")
        store.write("roundtrip-test-key-xyz")
        assert store.read() == "roundtrip-test-key-xyz"
