"""Top-level pytest conftest.py for the backend test suite.

JD follow-up (SDD#3 PR-2): AUTOUSE fixture that redirects
RECONCILIATION__OUTPUT_DIR to a per-test tmp directory so the real
backend/runs/ directory is NEVER scanned/swept during any test that uses
the FastAPI TestClient (via routes.py / main.py / create_app) unless the
test explicitly opts out.

Without this isolation the lifespan scan touches real backend/runs/ on every
TestClient(create_app()) call — a class-A isolation trap:
  - Tests see each other's on-disk state.
  - Sweep can delete real legacy dirs.
  - Scan timing makes tests non-deterministic.

Scope:
  Applied only to tests under tests/unit/infrastructure/ and tests/integration/
  (the directories where TestClient is used). Tests in tests/unit/application/
  (pure domain/application tests with no HTTP layer) are NOT affected.

  The fixture skips itself when:
  - The test has the @pytest.mark.real_runs_dir marker (opts out for real-data
    gates and legacy scan gates that intentionally access backend/runs/).
  - The test is NOT in an infrastructure or integration path.

Opt-out mechanism:
  Mark a test (or its class/module) with:
      @pytest.mark.real_runs_dir
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers to avoid warnings."""
    config.addinivalue_line(
        "markers",
        "real_runs_dir: opt out of RECONCILIATION__OUTPUT_DIR isolation; "
        "test accesses real backend/runs/ directory.",
    )


def _should_isolate(request: pytest.FixtureRequest) -> bool:
    """Return True if this test should have output_dir isolated to a tmp path.

    Only infrastructure and integration tests need isolation (they use TestClient
    or the API layer). Application tests (domain/application-only) should NOT
    have their env vars tampered with — doing so breaks AppConfig default checks.
    """
    if request.node.get_closest_marker("real_runs_dir"):
        return False

    # Isolate tests that live in infrastructure or integration directories.
    nodeid = request.node.nodeid
    if "/unit/infrastructure/" in nodeid or "/integration/" in nodeid:
        return True

    return False


@pytest.fixture(autouse=True)
def _isolate_output_dir(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    """Redirect RECONCILIATION__OUTPUT_DIR to a tmp dir for infrastructure tests.

    Only active for tests under tests/unit/infrastructure/ and tests/integration/.
    Pure application/domain tests are excluded to avoid breaking AppConfig default
    assertions (e.g. test_output_dir_default).

    Tests marked @pytest.mark.real_runs_dir are excluded so they can access the
    real backend/runs/ directory on disk (legacy gates, restart round-trip).
    """
    if not _should_isolate(request):
        yield
        return

    isolated_dir = tmp_path / "runs"
    isolated_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("RECONCILIATION__OUTPUT_DIR", str(isolated_dir))
    yield
