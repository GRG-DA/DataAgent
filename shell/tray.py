"""System tray icon (pystray). Best-effort: skipped silently if unavailable."""

import threading
import webbrowser


def start_tray(app_url: str, on_quit):
    """Start the tray icon in a daemon thread. Returns the icon or None."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception:  # noqa: BLE001
        return None

    def _make_icon():
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([4, 4, 60, 60], radius=16, fill=(23, 131, 255, 255))
        d.text((17, 15), "DA", fill=(255, 255, 255, 255))
        return img

    def _logs_dir():
        import os
        import sys
        from pathlib import Path

        if getattr(sys, "frozen", False) and sys.platform == "win32":
            return Path(
                os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
            ) / "DataAgent" / "logs"
        return Path("logs").resolve()

    def _open_logs():
        import os
        import sys

        try:
            path = str(_logs_dir())
            os.makedirs(path, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606 — local logs dir only
            else:
                webbrowser.open(f"file://{path}")
        except Exception:  # noqa: BLE001
            pass

    def _about():
        try:
            from config import settings

            version = settings.app.version
        except Exception:  # noqa: BLE001
            version = "unknown"
        msg = f"DataAgent v{version}\nLocal-first AI data analyst."
        try:
            import sys as _sys

            if _sys.platform == "win32":
                import ctypes

                ctypes.windll.user32.MessageBoxW(0, msg, "About DataAgent", 0x40)
        except Exception:  # noqa: BLE001
            pass

    try:
        from config import settings as _settings

        _ver = _settings.app.version
    except Exception:  # noqa: BLE001
        _ver = "?"
    menu = pystray.Menu(
        pystray.MenuItem("Open in browser", lambda icon, item: webbrowser.open(app_url)),
        pystray.MenuItem("Open logs folder", lambda icon, item: _open_logs()),
        pystray.MenuItem(f"About DataAgent v{_ver}", lambda icon, item: _about()),
        pystray.MenuItem("Quit DataAgent", lambda icon, item: on_quit()),
    )
    icon = pystray.Icon("dataagent", _make_icon(), "DataAgent", menu)
    threading.Thread(target=icon.run, daemon=True).start()
    return icon