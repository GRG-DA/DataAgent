"""Port selection for the desktop shell.

The shell pins the configured port (settings.app.port, default 8002). If
that port is already serving a DataAgent backend (e.g. a headless
`python run.py web`), we reuse it; a foreign process owning the port no
longer aborts startup — the shell drifts upward to the first free port
instead. The API page it serves reports its own origin to the SPA
(window.__DATAAGENT_API_SAME_ORIGIN__, injected by `api/main.py`
serve_index and honoured by web/js/api.js), so a drifted port still serves
a fully working app. Startup only fails when the whole fallback range is
busy.
"""

import json
import socket
import urllib.request

from config import settings


class PortOccupiedError(RuntimeError):
    pass


# Number of consecutive ports tried after the pinned one when a foreign
# process holds it (8002 busy -> try 8003..8012).
FALLBACK_PORTS = 10


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
    port and the shell should reuse it instead of starting its own. When a
    foreign process owns the configured port, the first free port after it
    is returned with owned=True (the backend binds there and the served
    SPA resolves its API origin from the page). Raises PortOccupiedError
    when no port in the fallback range is free.
    """
    preferred = int(settings.app.port)
    if not _port_in_use(settings.app.host, preferred):
        return preferred, True
    if _is_our_app(preferred):
        return preferred, False
    last = preferred + FALLBACK_PORTS
    for candidate in range(preferred + 1, last + 1):
        if not _port_in_use(settings.app.host, candidate):
            return candidate, True
    raise PortOccupiedError(
        f"Ports {preferred}-{last} are all already in use by other applications.\n"
        "Close the other programs or set APP_PORT to a free port, then retry."
    )
