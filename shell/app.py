"""Desktop shell entry point: `python run.py shell`."""

import sys

from config import settings

from .port import PortOccupiedError, resolve_port
from .server import BackendThread
from .single_instance import acquire, release
from .tray import start_tray


def _out(msg: str) -> None:
    """Print safely — windowed PyInstaller builds have no console (stdout=None)."""
    try:
        print(msg)
    except Exception:  # noqa: BLE001
        pass


def run_shell(debug: bool = False) -> int:
    """Launch the desktop shell. Returns a process exit code."""
    from shell.runtime_check import preflight

    ok, messages = preflight()
    if not ok:
        detail = "\n".join(messages)
        _out(f"Preflight failed:\n{detail}")
        if sys.platform == "win32":
            try:
                import ctypes

                ctypes.windll.user32.MessageBoxW(0, detail, "DataAgent", 0x10)
            except Exception:  # noqa: BLE001
                pass
        return 1

    try:
        import webview
    except ImportError:
        _out(
            "The desktop shell requires pywebview.\n"
            "Install it with:  pip install -r requirements-desktop.txt"
        )
        return 1

    from .window import create_window

    if not acquire():
        _out("DataAgent is already running.")
        return 0

    try:
        port, owned = resolve_port()
    except PortOccupiedError as exc:
        _out(str(exc))
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, str(exc), "DataAgent", 0x10)
        return 1

    backend = BackendThread(settings.app.host, port, settings.logging.level.lower())
    backend.owned = owned
    if owned:
        backend.start()

    window = create_window(backend, debug=debug)
    # Tray only on Windows: on Linux both pystray and pywebview drive GTK, and
    # the tray thread grabs the main context before webview.start() runs.
    if sys.platform == "win32":
        start_tray(backend.url, on_quit=lambda: window.destroy())

    try:
        webview.start(debug=debug, private_mode=False)
    finally:
        if owned:
            backend.stop()
        release()
    return 0