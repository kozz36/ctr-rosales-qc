"""CTR Rosales QC — Windows frozen launcher.

This is the PyInstaller entrypoint for the Windows native installer.
It runs as a windowless executable (pythonw) and:

    1. Creates per-user writable dirs under %LOCALAPPDATA%\ctr-rosales-qc\.
    2. Resolves read-only bundled assets (SPA, config.yaml) from sys._MEIPASS
       when frozen, or from the repository root when running in source mode.
    3. Sets the deterministic vision-off environment profile (mirrors
       docker-compose.app.yml) BEFORE importing or constructing AppConfig.
    4. Picks a free ephemeral port by binding 127.0.0.1:0.
    5. Starts uvicorn against the imported app object on a daemon thread.
    6. Polls /api/v1/runs/ until the server is ready, then opens the browser.
    7. Shows a small always-on-top "quit" window so the operator can stop the
       server without hunting for a process in Task Manager.

FROZEN vs SOURCE:
    Frozen  → sys.frozen == True; sys._MEIPASS is the one-dir bundle root.
    Source  → __file__ walks up to find the repo root (developer shortcut).

ARCHITECTURE NOTE (DO NOT VIOLATE):
    The app object is imported and passed directly to uvicorn.run(), NOT as an
    import string.  This is mandatory in frozen mode because PyInstaller cannot
    use import strings that reference modules by dotted path at runtime.
    --reload MUST NOT be passed (it would fork the process, which breaks frozen).
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import NoReturn

# ---------------------------------------------------------------------------
# Logging (write to %LOCALAPPDATA%\ctr-rosales-qc\launcher.log)
# ---------------------------------------------------------------------------

# We configure logging after we know LOCALAPPDATA in _setup_user_dirs().
# Bootstrap with a console handler that silently drops if there is no console.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    stream=sys.stderr if not getattr(sys, "frozen", False) else open(os.devnull, "w"),
)
logger = logging.getLogger("ctr.launcher")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APP_NAME = "CTR Rosales QC"
APP_DIR_NAME = "ctr-rosales-qc"
HEALTH_POLL_URL_TEMPLATE = "http://127.0.0.1:{port}/api/v1/runs/"
HEALTH_POLL_TIMEOUT_S = 60
HEALTH_POLL_INTERVAL_S = 0.5


# ---------------------------------------------------------------------------
# Path resolution helper
# ---------------------------------------------------------------------------


def resource_path(relative: str) -> Path:
    """Resolve a path relative to the bundle root (frozen) or repo root (source).

    In frozen mode PyInstaller sets sys._MEIPASS to the one-dir bundle root.
    In source mode we walk up from __file__ to find the repo root (the directory
    that contains both ``backend/`` and ``frontend/``).
    """
    if getattr(sys, "frozen", False):
        # Frozen: sys._MEIPASS is the extraction/bundle root.
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        # Source dev mode: walk up from packaging/windows/ to repo root.
        base = Path(__file__).resolve().parent.parent.parent
    return base / relative


# ---------------------------------------------------------------------------
# Per-user writable directory setup
# ---------------------------------------------------------------------------


def _setup_user_dirs() -> tuple[Path, Path, Path]:
    """Create and return (runs_dir, sunat_cache_dir, secrets_dir).

    All dirs live under %LOCALAPPDATA%\ctr-rosales-qc\.  Created if missing.
    Raises RuntimeError if %LOCALAPPDATA% is not set (should never happen on
    a normal Windows 10/11 installation).
    """
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError(
            "%%LOCALAPPDATA%% environment variable is not set. "
            "Cannot determine writable directory. "
            "Please contact support."
        )

    app_data_root = Path(local_app_data) / APP_DIR_NAME
    runs_dir = app_data_root / "runs"
    sunat_cache_dir = app_data_root / "sunat-cache"
    secrets_dir = app_data_root / "secrets"

    for d in (runs_dir, sunat_cache_dir, secrets_dir):
        d.mkdir(parents=True, exist_ok=True)

    # Configure file logging now that we know the root directory.
    log_file = app_data_root / "launcher.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s")
    )
    logging.getLogger().addHandler(file_handler)
    logging.getLogger().setLevel(logging.INFO)

    logger.info("User dirs ready: %s", app_data_root)
    return runs_dir, sunat_cache_dir, secrets_dir


# ---------------------------------------------------------------------------
# Environment profile (deterministic vision-off; mirrors docker-compose.app.yml)
# ---------------------------------------------------------------------------


def _set_env_profile(
    spa_dir: Path,
    config_yaml: Path,
    runs_dir: Path,
    sunat_cache_dir: Path,
    secrets_dir: Path,
) -> None:
    """Inject the deterministic vision-off profile into os.environ.

    IMPORTANT: This MUST be called BEFORE importing the reconciliation package
    or constructing AppConfig.  pydantic-settings reads env vars at construction
    time; setting them afterwards has no effect on an already-built config.

    AppConfig._validate_date_source invariant: vision.enabled=False requires
    sunat.enabled=True (no date source otherwise).  Both are satisfied here.
    """
    env = os.environ

    # --- Core mode flags ---
    env["RECONCILIATION__VISION__ENABLED"] = "false"
    env["RECONCILIATION__OCR__ENABLED"] = "true"
    env["RECONCILIATION__OCR__ENGINE"] = "rapidocr"

    # --- SUNAT supplementary (required when vision is off; provides date floor) ---
    env["RECONCILIATION__SUNAT__ENABLED"] = "true"
    env["RECONCILIATION__SUNAT__CACHE"] = "true"
    env["RECONCILIATION__SUNAT__CACHE_DIR"] = str(sunat_cache_dir)

    # --- Writable output ---
    env["RECONCILIATION__OUTPUT_DIR"] = str(runs_dir)

    # --- Secrets dir (key store reads from here) ---
    env["RECONCILIATION_SECRETS_DIR"] = str(secrets_dir)

    # --- Bundled read-only assets ---
    env["RECONCILIATION_SPA_DIR"] = str(spa_dir)
    env["RECONCILIATION_CONFIG"] = str(config_yaml)

    logger.info(
        "Env profile set: vision=off ocr=rapidocr sunat=on "
        "spa_dir=%s config=%s runs=%s",
        spa_dir,
        config_yaml,
        runs_dir,
    )


# ---------------------------------------------------------------------------
# Asset validation (fail-fast: R3 risk mitigation)
# ---------------------------------------------------------------------------


def _assert_bundled_assets(spa_dir: Path, config_yaml: Path) -> None:
    """Assert that required bundled assets are present before starting uvicorn.

    Fails with a clear error message rather than letting the server start and
    crash silently mid-request (risk R3: RapidOCR models not found frozen).
    """
    errors: list[str] = []

    # SPA index.html (required for browser open to work)
    spa_index = spa_dir / "index.html"
    if not spa_index.is_file():
        errors.append(f"SPA index.html not found at: {spa_index}")

    # config.yaml
    if not config_yaml.is_file():
        errors.append(f"config.yaml not found at: {config_yaml}")

    # RapidOCR models (loaded from Path(rapidocr.__file__).parent / "models")
    # Under _MEIPASS the package is bundled at rapidocr/ so models live at
    # <_MEIPASS>/rapidocr/models/.
    try:
        import rapidocr  # noqa: PLC0415
        models_dir = Path(rapidocr.__file__).parent / "models"
        required_models = [
            "ch_PP-OCRv5_det_server.onnx",
            "ch_PP-OCRv5_rec_server.onnx",
            "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
            "ppocr_keys_v1.txt",
            "ppocrv5_dict.txt",
        ]
        for model_file in required_models:
            model_path = models_dir / model_file
            if not model_path.is_file():
                errors.append(f"RapidOCR model/dict missing: {model_path}")
    except ImportError as exc:
        errors.append(f"rapidocr package not importable: {exc}")

    if errors:
        msg = (
            f"{APP_NAME}: Required bundled assets are missing.\n\n"
            + "\n".join(f"  • {e}" for e in errors)
            + "\n\nPlease reinstall the application."
        )
        logger.error("Asset validation failed:\n%s", msg)
        _show_error_and_exit(msg)


# ---------------------------------------------------------------------------
# Free ephemeral port selection
# ---------------------------------------------------------------------------


def _pick_free_port() -> int:
    """Bind 127.0.0.1:0, read the assigned port, close the socket, return it.

    There is a brief TOCTOU window between close and uvicorn bind.  On a
    single-user local machine this is not a concern in practice.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        port = s.getsockname()[1]
    logger.info("Selected ephemeral port: %d", port)
    return port


# ---------------------------------------------------------------------------
# Health poll
# ---------------------------------------------------------------------------


def _poll_until_ready(port: int) -> bool:
    """Poll GET /api/v1/runs/ until HTTP 200 or timeout.

    Returns True if the server became ready, False on timeout.
    Uses urllib (stdlib) to avoid adding a dependency on requests/httpx.
    """
    import urllib.error  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    url = HEALTH_POLL_URL_TEMPLATE.format(port=port)
    deadline = time.monotonic() + HEALTH_POLL_TIMEOUT_S
    attempt = 0

    while time.monotonic() < deadline:
        attempt += 1
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310
                if resp.status == 200:
                    logger.info("Server ready after %d poll attempts (port %d)", attempt, port)
                    return True
        except (urllib.error.URLError, OSError):
            # Server not yet accepting connections — expected during startup.
            pass
        time.sleep(HEALTH_POLL_INTERVAL_S)

    logger.error("Server did not become ready within %ds", HEALTH_POLL_TIMEOUT_S)
    return False


# ---------------------------------------------------------------------------
# Quit window (minimal Tkinter always-on-top window)
# ---------------------------------------------------------------------------


def _run_quit_window(server_thread: threading.Thread) -> NoReturn:
    """Show a small always-on-top window with a Quit button.

    Closing the window or clicking Quit stops the server thread and exits.
    Falls back gracefully if Tkinter is unavailable (edge case in some
    stripped Python environments — should not happen with PyInstaller bundle).
    """
    try:
        import tkinter as tk  # noqa: PLC0415
    except ImportError:
        logger.warning("tkinter not available — launcher will keep alive until Ctrl+C or process kill")
        server_thread.join()
        sys.exit(0)

    root = tk.Tk()
    root.title(APP_NAME)
    root.resizable(False, False)
    root.attributes("-topmost", True)

    # Window geometry: small banner at top-right of primary monitor.
    root.geometry("340x80+20+20")

    label = tk.Label(
        root,
        text=f"{APP_NAME} está ejecutándose.\nCierra esta ventana para detener el servidor.",
        padx=12,
        pady=8,
        justify="center",
    )
    label.pack(fill="x")

    def _quit() -> None:
        logger.info("Quit requested by user.")
        root.destroy()

    btn = tk.Button(root, text="Cerrar / Quit", command=_quit, padx=10)
    btn.pack(pady=4)

    root.protocol("WM_DELETE_WINDOW", _quit)
    root.mainloop()

    # mainloop() returned → user closed or clicked Quit.
    logger.info("Launcher exiting — server process will terminate.")
    sys.exit(0)


# ---------------------------------------------------------------------------
# Error dialog helper
# ---------------------------------------------------------------------------


def _show_error_and_exit(message: str) -> NoReturn:
    """Show an error in a dialog (Tkinter) or stderr, then exit(1)."""
    try:
        import tkinter as tk  # noqa: PLC0415
        import tkinter.messagebox as mb  # noqa: PLC0415

        root = tk.Tk()
        root.withdraw()
        mb.showerror(APP_NAME, message)
        root.destroy()
    except Exception:  # noqa: BLE001
        print(message, file=sys.stderr)  # noqa: T201
    sys.exit(1)


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """Launcher entrypoint — called by PyInstaller frozen exe on double-click."""

    # --- 1. Per-user writable dirs ---
    try:
        runs_dir, sunat_cache_dir, secrets_dir = _setup_user_dirs()
    except RuntimeError as exc:
        _show_error_and_exit(str(exc))

    # --- 2. Resolve bundled read-only assets ---
    spa_dir = resource_path("frontend/dist")
    config_yaml = resource_path("config.yaml")
    logger.info("SPA dir resolved: %s", spa_dir)
    logger.info("Config resolved: %s", config_yaml)

    # --- 3. Set env profile BEFORE importing reconciliation ---
    _set_env_profile(spa_dir, config_yaml, runs_dir, sunat_cache_dir, secrets_dir)

    # --- 4. Assert bundled assets exist (fail fast, risk R3) ---
    _assert_bundled_assets(spa_dir, config_yaml)

    # --- 5. Import the app object (AFTER env is set) ---
    # Must import AFTER setting env so that pydantic-settings reads the
    # correct values when AppConfig is constructed during the lifespan startup.
    try:
        from reconciliation.infrastructure.api.main import app  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        _show_error_and_exit(
            f"Failed to import application module:\n{exc}\n\n"
            "Check the launcher log for details."
        )

    # --- 6. Pick a free ephemeral port ---
    port = _pick_free_port()

    # --- 7. Start uvicorn on a daemon thread ---
    # Pass the app OBJECT (not an import string) — required in frozen mode.
    # No --reload: reload forks the process which breaks the frozen bundle.
    import uvicorn  # noqa: PLC0415

    uvicorn_config = uvicorn.Config(
        app=app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        # access_log=False reduces noise in the launcher log
        access_log=True,
    )
    server = uvicorn.Server(config=uvicorn_config)

    server_thread = threading.Thread(target=server.run, daemon=True, name="uvicorn")
    server_thread.start()
    logger.info("uvicorn started on port %d (thread=%s)", port, server_thread.name)

    # --- 8. Poll until ready ---
    ready = _poll_until_ready(port)
    if not ready:
        _show_error_and_exit(
            f"{APP_NAME} no pudo iniciar en el tiempo esperado.\n\n"
            f"Revisa el log en:\n"
            f"  %LOCALAPPDATA%\\{APP_DIR_NAME}\\launcher.log"
        )

    # --- 9. Open browser ---
    url = f"http://127.0.0.1:{port}/"
    logger.info("Opening browser at %s", url)
    webbrowser.open(url)

    # --- 10. Keep alive with quit window ---
    _run_quit_window(server_thread)


if __name__ == "__main__":
    main()
