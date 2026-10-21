"""
On-demand llama.cpp (llama-server) supervisor.

Starts the local llama-server only when the app actually needs the LLM and
stops it again after a configurable idle period, freeing the ~5GB of RAM
the model holds while sitting unused.

Usage from other modules:

    from agent.llama_server import get_llama_server_manager

    mgr = get_llama_server_manager()
    if mgr.manages_url("http://localhost:8080/v1"):
        await mgr.ensure_running()   # spawns server if not up (idempotent)
        mgr.touch()                  # mark "in use" (resets idle timer)

Configuration (environment variables):
    LLAMACPP_AUTOSTART      true/false  — master switch (default: true)
    LLAMACPP_IDLE_TIMEOUT   seconds without requests before stop (default: 300)
    LLAMACPP_PORT           port to serve on (default: 8080)
    LLAMACPP_STARTUP_TIMEOUT seconds to wait for model load (default: 180)
    LLAMACPP_DIR            llama.cpp checkout (default: ~/llama.cpp)
    LLAMACPP_MODEL          path to .gguf model (default: <dir>/models/qwen2.5-coder-7b-instruct-q4_k_m.gguf)
    LLM_OPENAI_BASE_URL     app's OpenAI-compatible endpoint; the manager
                            only activates when this points at its own port.

Only servers spawned by this manager are ever stopped; one started manually
via scripts/start_llamacpp.sh is detected as "already running" and left alone.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

import httpx

try:
    from dotenv import dotenv_values

    def _env_file_values() -> dict:
        """Values from the project .env — pydantic-settings consumes .env
        internally but never exports it to os.environ, so read it directly."""
        try:
            return dotenv_values(Path(".env")) or {}
        except Exception:  # noqa: BLE001
            return {}
except ImportError:  # pragma: no cover
    def _env_file_values() -> dict:
        return {}

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}

_ENV_FILE = _env_file_values()


def _get(name: str, default: str) -> str:
    """Real environment first, then .env, then default."""
    return os.getenv(name) or _ENV_FILE.get(name) or default


def _env_bool(name: str, default: bool) -> bool:
    raw = _get(name, "")
    if not raw:
        return default
    return raw.strip().lower() in _TRUTHY


class LlamaServerManager:
    """Spawn/kill llama-server around periods of actual use."""

    def __init__(self) -> None:
        self.enabled = _env_bool("LLAMACPP_AUTOSTART", True)
        self.port = int(_get("LLAMACPP_PORT", "8080"))
        self.idle_timeout = int(_get("LLAMACPP_IDLE_TIMEOUT", "300"))
        self.startup_timeout = int(_get("LLAMACPP_STARTUP_TIMEOUT", "180"))
        self.llama_dir = Path(_get("LLAMACPP_DIR", str(Path.home() / "llama.cpp")))
        default_model = self.llama_dir / "models" / "qwen2.5-coder-7b-instruct-q4_k_m.gguf"
        self.model_path = Path(_get("LLAMACPP_MODEL", str(default_model)))
        self.api_key = _get("LLAMACPP_API_KEY", "sk-local")
        self.log_file = Path(_get("LLAMACPP_LOG_FILE", "logs/llama-server.log"))

        self._proc: Optional[subprocess.Popen] = None
        self._lock = asyncio.Lock()
        self._last_use = 0.0
        self._reaper_task: Optional[asyncio.Task] = None

    # ── URL / health helpers ─────────────────────────────────────

    @property
    def root_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def manages_url(self, base_url: str) -> bool:
        """True when an OpenAI-compatible base_url points at our local port."""
        if not self.enabled:
            return False
        try:
            host = (base_url or "").split("//", 1)[-1].split("/", 1)[0]
            _, _, port = host.rpartition(":")
            return port == str(self.port)
        except Exception:  # noqa: BLE001
            return False

    async def is_healthy(self, timeout: float = 2.0) -> bool:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(f"{self.root_url}/health")
                return resp.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    # ── lifecycle ────────────────────────────────────────────────

    def touch(self) -> None:
        self._last_use = time.monotonic()

    async def ensure_running(self, force_check: bool = False) -> bool:
        """Make sure llama-server is up. Returns True when it is serving."""
        if not self.enabled:
            return await self.is_healthy()

        # Fast path (no lock): already healthy and recently verified.
        if not force_check and self._proc is None and await self.is_healthy():
            self.touch()
            return True

        async with self._lock:
            self.touch()
            if await self.is_healthy():
                return True
            if self._proc is not None and self._proc.poll() is None:
                # We own a process but it is not answering yet — keep waiting
                # rather than spawning a second instance on the same port.
                return await self._wait_healthy()
            return await self._spawn()

    async def _wait_healthy(self) -> bool:
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if await self.is_healthy():
                return True
            await asyncio.sleep(0.5)
        logger.error("llama-server did not become healthy within %ss", self.startup_timeout)
        return False

    async def _spawn(self) -> bool:
        binary = self.llama_dir / "build" / "bin" / "llama-server"
        if not binary.exists():
            logger.error(
                "llama-server binary not found at %s — build it with cmake "
                "(see scripts/start_llamacpp.sh)", binary,
            )
            return False
        if not self.model_path.exists():
            logger.error("GGUF model not found at %s", self.model_path)
            return False

        threads = str(max(4, os.cpu_count() or 4))
        cmd = [
            str(binary),
            "-m", str(self.model_path),
            "--host", "127.0.0.1",
            "--port", str(self.port),
            "-t", threads,
            "-tb", threads,
            "-c", "8192",
            "--temp", "0.1",
            "--top-p", "0.95",
            "--mlock",
            "--alias", _get("LLM_OPENAI_MODEL", "qwen2.5-coder-7b"),
            "--api-key", self.api_key,
        ]

        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        log_handle = open(self.log_file, "ab")
        kwargs: dict = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            kwargs["start_new_session"] = True

        logger.info("Starting llama-server on :%s (%s threads, idle stop after %ss)…",
                    self.port, threads, self.idle_timeout)
        t0 = time.monotonic()
        self._proc = subprocess.Popen(cmd, stdout=log_handle, stderr=subprocess.STDOUT, **kwargs)

        ok = await self._wait_healthy()
        if not ok:
            self._terminate()
            return False
        logger.info("llama-server ready in %.1fs (pid %s)", time.monotonic() - t0, self._proc.pid)
        return True

    def _terminate(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        logger.info("Stopping llama-server (pid %s) to free RAM", proc.pid)
        try:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        except Exception as e:  # noqa: BLE001
            logger.warning("Error stopping llama-server: %s", e)

    async def stop_if_idle(self) -> None:
        if self.idle_timeout <= 0 or self._proc is None:
            return
        idle_for = time.monotonic() - self._last_use
        if idle_for >= self.idle_timeout and await self.is_healthy():
            self._terminate()
            logger.info("llama-server stopped after %.0fs idle (RAM freed)", idle_for)

    async def shutdown(self) -> None:
        self._stop_reaper()
        self._terminate()

    # ── idle reaper ──────────────────────────────────────────────

    def start_reaper(self) -> None:
        if not self.enabled or self._reaper_task is not None:
            return
        self._reaper_task = asyncio.create_task(self._reaper_loop())

    def _stop_reaper(self) -> None:
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            self._reaper_task = None

    async def _reaper_loop(self) -> None:
        while True:
            await asyncio.sleep(15)
            try:
                await self.stop_if_idle()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.debug("llama-server reaper error: %s", e)


_manager: Optional[LlamaServerManager] = None


def get_llama_server_manager() -> LlamaServerManager:
    global _manager
    if _manager is None:
        _manager = LlamaServerManager()
    return _manager


async def ensure_local_llama_server(base_url: str) -> bool:
    """
    Convenience hook for LLM providers: no-op unless *base_url* points at the
    managed local llama-server. Starts it on demand and marks it in-use.
    """
    mgr = get_llama_server_manager()
    if not mgr.manages_url(base_url):
        return False
    return await mgr.ensure_running()
