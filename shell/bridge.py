"""JS bridge exposed to the SPA as window.pywebview.api.

pywebview injects the api object into every page loaded in the window, so
the SPA can call e.g. window.pywebview.api.openBrowser() without any
changes to the web app itself.
"""

import webbrowser
from pathlib import Path

from config import settings

from . import __version__


class ShellBridge:
    def __init__(self, app_url: str, window=None):
        self.app_url = app_url
        self._window = window

    def get_info(self) -> dict:
        return {
            "name": "DataAgent",
            "version": __version__,
            "url": self.app_url,
            "port": int(settings.app.port),
        }

    def open_browser(self) -> bool:
        return webbrowser.open(self.app_url)

    def quit(self) -> None:
        if self._window is not None:
            try:
                self._window.destroy()
            except Exception:  # noqa: BLE001
                pass

    def tail_log(self, n: int = 80) -> str:
        try:
            path = Path(settings.logging.file)
            if not path.exists():
                return ""
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            return "\n".join(lines[-max(1, min(int(n), 500)):])
        except Exception:  # noqa: BLE001
            return ""