"""Port selection for the desktop shell.

The SPA hardcodes the API origin to port 8002 (web/js/api.js reads
window.API_PORT, default "8002"), so the shell pins the configured port
instead of drifting to a free one. If the port is already serving a
DataAgent backend (e.g. a headless `python run.py web`), we reuse it;
anything else is a foreign process and we refuse to start.
"""

import json
import socket
import urllib.request

from config import settings


class PortOccupiedError(RuntimeError):
    pass


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def _is_our_app(port: int) -> bool:
    """True if /health on `port` answers with a DataAgent-shaped payload."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as resp:
            if resp.status != 200:
                return False
            data = json.loads(resp.read().decode("utf-8", "replace"))
            return data.get("status") == "ok" and "components" in data
    except Exception:  # noqa: BLE001
        return False


def resolve_port() -> tuple[int, bool]:
    """Return (port, owned).

    owned=False means an existing DataAgent backend is already serving the
    port and the shell should reuse it instead of starting its own.
    """
    port = int(settings.app.port)
    if not _port_in_use(settings.app.host, port):
        return port, True
    if _is_our_app(port):
        return port, False
    raise PortOccupiedError(
        f"Port {port} is already in use by another application.\n"
        f"Close the other program or set APP_PORT to a free port, then retry."
    )