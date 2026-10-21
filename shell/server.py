"""In-process FastAPI backend for the desktop shell.

Runs uvicorn.Server (api.main:app) in a daemon thread with its own event
loop. A single process means the Windows DuckDB "file in use" problem
(auto-reload spawns a second process) cannot occur, and shutdown is a
simple `should_exit` flag.
"""

import asyncio
import logging
import threading
import time
import urllib.request

import uvicorn

log = logging.getLogger("shell.server")


class BackendThread:
    """Runs the FastAPI app in a daemon thread and tracks readiness."""

    def __init__(self, host: str, port: int, log_level: str = "info"):
        self.host = host
        self.port = port
        self.log_level = log_level
        self._thread: threading.Thread | None = None
        self._server: uvicorn.Server | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # False when we reuse an already-running backend (we don't own it).
        self.owned = True

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="dataagent-backend", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        config = uvicorn.Config(
            "api.main:app",
            host=self.host,
            port=self.port,
            log_level=self.log_level,
            reload=False,
        )
        self._server = uvicorn.Server(config)
        try:
            self._loop.run_until_complete(self._server.serve())
        except Exception:  # noqa: BLE001
            log.exception("Backend server exited with an error")
        finally:
            self._loop.close()
            self._loop = None

    def is_healthy(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.url}/health", timeout=2) as resp:
                return resp.status == 200
        except Exception:  # noqa: BLE001
            return False

    def wait_ready(self, timeout: float = 90.0) -> bool:
        """Poll /health until the backend answers or the timeout elapses."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_healthy():
                return True
            time.sleep(0.25)
        return False

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=10)