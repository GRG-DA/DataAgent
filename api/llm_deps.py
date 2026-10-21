"""
Local AI Dependencies & Models
================================
Backend for the Settings → "Local AI Dependencies & Models" panel.

Handles, entirely from the web UI:

* Ollama — detect install, detect/start the server, list pulled models,
  pull new models (with live progress), delete pulled models, and
  one-click install of the official Ollama runtime for the host OS.
* LM Studio — probe the OpenAI-compatible server (default :1234/v1) and
  list the models it currently serves.
* Python backend deps — status-only check (no auto pip-install).

All routes live under /llm/ (already public — no auth) and only ever talk
to the local machine that runs the app, never over the network for config.
Model names are validated against a strict pattern before use.
"""

import asyncio
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from config import settings
from agent.warp import get_rotator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/llm", tags=["LLM Dependencies"])

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Runtime state (the downloaded Ollama binary and the server logs) must live
# in the WRITABLE per-user directory, not next to the executable: the frozen
# desktop build installs into "C:\Program Files\DataAgent", which a normal
# user cannot write to.  ``api.runtime_paths`` is the single place that
# answers "where does writable runtime state live?" — in a source checkout it
# resolves to the project root, exactly as before.
#
# These are FUNCTIONS, not module constants: the runtime directory is
# resolved on every call so a caller that sets $DATAAGENT_DATA_DIR after this
# module was imported still gets the right answer.
from api.runtime_paths import runtime_file as _runtime_file  # noqa: E402


def storage_dir() -> Path:
    """Where the managed Ollama/LMStudio binaries are installed."""
    return _runtime_file("storage", "ollama")


def bin_dir() -> Path:
    """The directory the managed Ollama executable is written to."""
    return storage_dir() / "bin"


def ollama_bin() -> Path:
    """The managed Ollama executable."""
    return bin_dir() / ("ollama.exe" if os.name == "nt" else "ollama")


def logs_dir() -> Path:
    """Where the managed local-model server logs are written."""
    return _runtime_file("logs")


# Backwards-compatible aliases. These snapshot the path at import time; new
# code should call the functions above.
STORAGE_DIR = storage_dir()
BIN_DIR = bin_dir()
OLLAMA_BIN = ollama_bin()
LOGS_DIR = logs_dir()

# ── Recommended model families (quick-pull chips in the UI) ─────────────
RECOMMENDED_OLLAMA_MODELS = [
    "qwen2.5:7b",
    "qwen2.5:14b",
    "qwen2.5:32b",
    "llama3.1:8b",
    "mistral:7b",
    "codellama:13b",
]

LMSTUDIO_DOWNLOAD_URL = "https://lmstudio.ai"
LMSTUDIO_GUIDE = (
    "Start LM Studio → open the Developer tab → 'Start Server' "
    "(defaults to http://localhost:1234/v1) → load a model in the GUI."
)
# Official installer scripts that publish the current daemon version; the
# `lms` daemon (llmster) is what actually serves the OpenAI-compatible API
# on :1234/v1, so installing it is a real, headless install — no GUI needed.
LMSTUDIO_INSTALL_SCRIPTS = {
    "linux": "https://lmstudio.ai/install.sh",
    "darwin": "https://lmstudio.ai/install.sh",
    "windows": "https://lmstudio.ai/install.ps1",
}
LMSTUDIO_DOWNLOAD_HOST = "https://llmster.lmstudio.ai/download"

_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*(:[A-Za-z0-9][A-Za-z0-9_.-]*)?$")

# In-memory job tracking (single-process app server).
_pull_jobs: Dict[str, Dict[str, Any]] = {}
_install_jobs: Dict[str, Dict[str, Any]] = {}

DEFAULT_SERVER_TIMEOUT = httpx.Timeout(5.0)
PULL_TIMEOUT = httpx.Timeout(3600.0, connect=10.0)


def _server_reachable(url: str) -> bool:
    try:
        import socket

        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=2.0):
            return True
    except Exception:  # noqa: BLE001
        return False


def _fetch_server_version(url: str) -> str:
    try:
        import httpx as _hx
        r = _hx.get(url.rstrip("/") + "/api/version", timeout=3.0)
        if r.status_code == 200:
            return str((r.json() or {}).get("version", ""))
    except Exception:  # noqa: BLE001
        pass
    return ""


def _valid_model(name: str) -> bool:
    return bool(name and _MODEL_PATTERN.match(name))


def _find_ollama() -> Optional[str]:
    """Locate the ollama binary (PATH, standard install dirs, managed dir)."""
    for probe in ("ollama", "ollama.exe"):
        found = shutil.which(probe)
        if found:
            return found
    # Windows default install location
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        candidates = [
            Path(local) / "Programs" / "Ollama" / "ollama.exe",
            Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe",
        ]
        for c in candidates:
            if c.exists():
                return str(c)
    # Our managed copy (from one-click install)
    if ollama_bin().exists():
        return str(ollama_bin())
    return None


def _ollama_version(binary: str) -> str:
    try:
        result = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = (result.stdout or result.stderr or "").strip()
        m = re.search(r"version(?: is)? ([\d.]+)", out)
        return m.group(1) if m else out
    except Exception:  # noqa: BLE001
        return ""


def _machine_arch() -> str:
    m = platform.machine().lower()
    if m in ("amd64", "x86_64", "x64"):
        return "amd64"
    if m in ("aarch64", "arm64"):
        return "arm64"
    return "amd64"


def _os_platform() -> str:
    if sys_platform := platform.system().lower():
        if "darwin" in sys_platform:
            return "darwin"
        if "win" in sys_platform:
            return "windows"
        if "linux" in sys_platform:
            return "linux"
    return sys_platform


# Python dependencies are temporarily imported in functions to avoid failing
# this module at import time on minimal installs.
def _python_deps() -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for pkg in ("ollama", "httpx", "pandas", "duckdb", "fastapi"):
        try:
            mod = __import__(pkg)
            result[pkg] = {"installed": True, "version": getattr(mod, "__version__", "") or ""}
        except Exception:  # noqa: BLE001
            result[pkg] = {"installed": False, "version": ""}
    return result


async def _ollama_models(base_url: str) -> List[Dict[str, Any]]:
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=DEFAULT_SERVER_TIMEOUT) as client:
            r = await client.get("/api/tags")
            r.raise_for_status()
            data = r.json()
        models = []
        for m in data.get("models", []):
            details = m.get("details", {}) or {}
            models.append({
                "name": m.get("name") or m.get("model"),
                "size": _fmt_bytes(m.get("size") or 0),
                "size_bytes": m.get("size") or 0,
                "parameter_size": details.get("parameter_size") or "",
                "quantization_level": details.get("quantization_level") or "",
                "context_length": details.get("context_length") or 0,
                "modified_at": (m.get("modified_at") or "")[:16],
            })
        return models
    except Exception as e:  # noqa: BLE001
        logger.debug("ollama list_models failed: %s", e)
        return []


async def _lmstudio_models(base_url: str) -> List[Dict[str, Any]]:
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=DEFAULT_SERVER_TIMEOUT) as client:
            r = await client.get("/models")
            r.raise_for_status()
            data = r.json()
        models = []
        for m in data.get("data", []):
            if isinstance(m, dict):
                models.append({"id": m.get("id")})
        return models
    except Exception:  # noqa: BLE001
        return []


def _fmt_bytes(num: int) -> str:
    num = float(num or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024.0 or unit == "TB":
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024.0
    return f"{num:.1f} TB"


def _ollama_install_url() -> Optional[str]:
    plat = _os_platform()
    arch = _machine_arch()
    if plat == "linux":
        return f"https://ollama.com/download/ollama-linux-{arch}.tgz"
    if plat == "darwin":
        return "https://ollama.com/download/Ollama-darwin.zip"
    if plat == "windows":
        return "https://ollama.com/download/OllamaSetup.exe"
    return None


# ── LM Studio (lms daemon) helpers ─────────────────────────────────────

def _find_lmstudio() -> Optional[str]:
    """Locate the LM Studio `lms` daemon binary."""
    for probe in ("lms", "lms.exe"):
        found = shutil.which(probe)
        if found:
            return found
    # The official installer can redirect the home via ~/.lmstudio-home-pointer
    home_dir = None
    try:
        ptr = Path.home() / ".lmstudio-home-pointer"
        if ptr.is_file():
            home_dir = Path((ptr.read_text(errors="ignore") or "").strip())
    except Exception:  # noqa: BLE001
        home_dir = None
    bases = [Path.home() / ".lmstudio", Path.home() / ".cache" / "lm-studio"]
    if home_dir:
        bases.insert(0, home_dir)
    for base in bases:
        for b in ("lms", "lms.exe"):
            candidate = base / "bin" / b
            if candidate.exists():
                return str(candidate)
    return None


def _lmstudio_version(binary: str) -> str:
    try:
        result = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return (result.stdout or result.stderr or "").strip()[:200]
    except Exception:  # noqa: BLE001
        return ""


def _cuda12_suffix() -> str:
    """Replicate the official script: linux-x64 'full' bundles add a +cuda12
    suffix when the NVIDIA driver is >= 550.54.14."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        parts = (out.splitlines() or [""])[0].strip().split(".")
        if len(parts) >= 3:
            if (int(parts[0]), int(parts[1]), int(parts[2])) >= (550, 54, 14):
                return "+cuda12"
    except Exception:  # noqa: BLE001
        pass
    return ""


def _lmstudio_install_script(plat: str) -> str:
    return LMSTUDIO_INSTALL_SCRIPTS.get(plat, "")


def _fetch_lmstudio_script_info(plat: str) -> Dict[str, str]:
    """Fetch the official installer script to learn the current version/variant."""
    info = {"script_url": _lmstudio_install_script(plat), "version": "", "variant": "full"}
    if not info["script_url"]:
        return info
    try:
        r = httpx.get(info["script_url"], timeout=15.0, follow_redirects=True)
        r.raise_for_status()
        text = r.text
        m = re.search(r"^APP_VERSION\s*=\s*['\"]?([^'\"\n]+)", text, re.M)
        if m:
            info["version"] = m.group(1).strip()
        m = re.search(r"^APP_VARIANT\s*=\s*['\"]?([^'\"\n]+)", text, re.M)
        if m:
            info["variant"] = m.group(1).strip()
    except Exception as e:  # noqa: BLE001
        logger.debug("LM Studio install script fetch failed: %s", e)
    return info


def _lmstudio_artifact_url(plat: str, arch: str, version: str, variant: str) -> Optional[str]:
    if not version:
        return None
    ext = ".zip" if plat == "windows" else ".tar.gz"
    if plat == "windows":
        arch_name = {"amd64": "x64", "arm64": "arm64"}.get(arch, "x64")
        name = f"{version}-win32-{arch_name}.{variant}"
    else:
        os_name = "linux" if plat == "linux" else "darwin"
        arch_name = "x64" if arch == "amd64" else "arm64"
        name = f"{version}-{os_name}-{arch_name}.{variant}"
        if plat == "linux" and arch == "amd64" and variant == "full":
            name += _cuda12_suffix()
    return f"{LMSTUDIO_DOWNLOAD_HOST}/{name}{ext}"


def _resolve_lmstudio_artifact(plat: str, arch: str) -> Optional[str]:
    """Resolve the current LM Studio artifact URL for this OS/arch."""
    info = _fetch_lmstudio_script_info(plat)
    url = _lmstudio_artifact_url(plat, arch, info["version"], info["variant"])
    if not url:
        return None
    candidates = [url]
    if plat == "linux" and arch == "amd64" and "+cuda12" in url:
        candidates.append(url.replace("+cuda12", ""))
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            for u in candidates:
                try:
                    if client.head(u).status_code < 400:
                        return u
                except Exception:  # noqa: BLE001
                    continue
    except Exception:  # noqa: BLE001
        pass
    return candidates[0]


# ═══════════════════════════════════════════════════════════════════
# Status
# ═══════════════════════════════════════════════════════════════════

@router.get("/dependencies", response_model=Dict[str, Any])
async def llm_dependencies():
    """Aggregate status of local AI dependencies (Ollama, LM Studio, python)."""
    ollama_url = (settings.llm.base_url or "http://localhost:11434").rstrip("/")
    lmstudio_url = (settings.llm.lmstudio_base_url or "http://localhost:1234/v1").rstrip("/")

    binary = _find_ollama()
    server_up = _server_reachable(ollama_url)
    models = await _ollama_models(ollama_url) if server_up else []

    lmstudio_server_up = _server_reachable(lmstudio_url)
    lmstudio_models = await _lmstudio_models(lmstudio_url) if lmstudio_server_up else []
    lmstudio_binary = _find_lmstudio()

    plat = _os_platform()
    install_url = _ollama_install_url()

    warp = await get_rotator().status()

    return {
        "success": True,
        "os": {"platform": plat, "arch": _machine_arch(), "host": platform.node()},
        "ollama": {
            "installed": bool(binary),
            "binary": binary or "",
            "version": _ollama_version(binary) if binary else "",
            "server_reachable": server_up,
            "server_url": ollama_url,
            "server_version": _fetch_server_version(ollama_url) if server_up else "",
            "install_url": install_url,
            "recommended_models": RECOMMENDED_OLLAMA_MODELS,
            "models": models,
            "active_model": settings.llm.model or "",
        },
        "lmstudio": {
            "installed_server": lmstudio_server_up,
            "app_installed": bool(lmstudio_binary),
            "binary": lmstudio_binary or "",
            "version": _lmstudio_version(lmstudio_binary) if lmstudio_binary else "",
            "server_url": lmstudio_url,
            "download_url": LMSTUDIO_DOWNLOAD_URL,
            "guide": LMSTUDIO_GUIDE,
            "models": lmstudio_models,
        },
        "python": _python_deps(),
        "warp": warp,
    }


# ═══════════════════════════════════════════════════════════════════
# Ollama — pull / delete / start / install
# ═══════════════════════════════════════════════════════════════════

class OllamaModelRequest(BaseModel):
    model: str


@router.post("/ollama/pull", response_model=Dict[str, Any])
async def ollama_pull(request: OllamaModelRequest):
    """Start pulling an Ollama model. Progress is tracked in a job the UI polls."""
    model = (request.model or "").strip()
    if not _valid_model(model):
        return {"success": False, "error": f"Invalid model name: {request.model!r}"}

    ollama_url = (settings.llm.base_url or "http://localhost:11434").rstrip("/")
    if not _server_reachable(ollama_url):
        return {"success": False, "error": f"Ollama server not reachable at {ollama_url} — start it first."}

    # Reuse a running job for the same model
    for jid, job in _pull_jobs.items():
        if job.get("model") == model and job.get("status") in ("running", "queued"):
            return {"success": True, "job_id": jid, "already_running": True}

    job_id = uuid.uuid4().hex[:12]
    _pull_jobs[job_id] = {"id": job_id, "model": model, "status": "running", "pct": 0, "stage": "queued", "error": ""}
    asyncio.create_task(_run_pull_job(job_id, model, ollama_url))
    return {"success": True, "job_id": job_id}


async def _run_pull_job(job_id: str, model: str, ollama_url: str) -> None:
    job = _pull_jobs[job_id]
    current_pct = 0
    try:
        async with httpx.AsyncClient(base_url=ollama_url, timeout=PULL_TIMEOUT) as client:
            async with client.stream("POST", "/api/pull", json={"model": model, "stream": True}) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except ValueError:
                        continue
                    status = chunk.get("status", "")
                    job["stage"] = status
                    if status == "success":
                        job["status"] = "done"
                        job["pct"] = 100
                        break
                    if status.startswith(("pulling ", "downloading ", "verifying ")) or status in (
                        "downloading", "pulling", "blob.downloading", "compressing", "pushing"):
                        total = chunk.get("total") or 0
                        completed = chunk.get("completed") or 0
                        if total > 0:
                            current_pct = min(int((completed / total) * 100), 99)
                    elif status in ("pulling manifest", "writing manifest", "removing any unused layers", "creating", "created"):
                        current_pct = max(current_pct, 95)
                    job["pct"] = current_pct
    except Exception as e:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = f"{type(e).__name__}: {e}"
        logger.error("Ollama pull %s failed: %s", model, e)
    finally:
        if job.get("status") == "running":
            job["status"] = "error"
            job["error"] = job.get("error") or "Pull ended without a success status"


@router.get("/ollama/pull/{job_id}", response_model=Dict[str, Any])
async def ollama_pull_status(job_id: str):
    job = _pull_jobs.get(job_id)
    if not job:
        return {"success": False, "error": "Unknown pull job"}
    return {"success": True, "job": job}


@router.post("/ollama/delete", response_model=Dict[str, Any])
async def ollama_delete(request: OllamaModelRequest):
    """Delete a pulled Ollama model."""
    model = (request.model or "").strip()
    if not _valid_model(model):
        return {"success": False, "error": f"Invalid model name: {request.model!r}"}
    ollama_url = (settings.llm.base_url or "http://localhost:11434").rstrip("/")
    if not _server_reachable(ollama_url):
        return {"success": False, "error": f"Ollama server not reachable at {ollama_url}."}
    try:
        async with httpx.AsyncClient(base_url=ollama_url, timeout=DEFAULT_SERVER_TIMEOUT) as client:
            r = await client.request("DELETE", "/api/delete", json={"name": model})
        if r.status_code in (200, 404):
            return {"success": True, "deleted": model}
        return {"success": False, "error": f"Ollama returned HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


@router.post("/ollama/start", response_model=Dict[str, Any])
async def ollama_start():
    """Start the Ollama server (detached) if the binary exists."""
    binary = _find_ollama()
    if not binary:
        return {"success": False, "error": "Ollama binary not found — install it first."}
    ollama_url = (settings.llm.base_url or "http://localhost:11434").rstrip("/")
    if _server_reachable(ollama_url):
        return {"success": True, "already_running": True, "server_url": ollama_url}
    try:
        logs_dir().mkdir(parents=True, exist_ok=True)
        log_path = logs_dir() / "ollama_serve.log"
        kwargs: Dict[str, Any] = {
            "stdout": open(log_path, "ab"),
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.DEVNULL,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen([binary, "serve"], **kwargs)
        return {"success": True, "started": True, "server_url": ollama_url, "log": str(log_path)}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


@router.post("/ollama/install", response_model=Dict[str, Any])
async def ollama_install():
    """One-click install of the official Ollama runtime for the host OS.

    Runs in a background job (id returned) so the download/extract can be
    polled from the UI. Never uses `shell=True` — paths/args stay as lists.
    """
    if _find_ollama():
        return {"success": True, "already_installed": True}

    plat = _os_platform()
    url = _ollama_install_url()
    if not url:
        return {"success": False, "error": f"No installer known for platform: {platform.system()}"}

    job_id = uuid.uuid4().hex[:12]
    _install_jobs[job_id] = {"id": job_id, "status": "downloading", "stage": "queued", "pct": 0, "error": "", "log": ""}
    asyncio.create_task(_run_install_job(job_id, url, plat))
    return {"success": True, "job_id": job_id}


async def _run_install_job(job_id: str, url: str, plat: str) -> None:
    job = _install_jobs[job_id]
    known_dir = bin_dir()
    try:
        known_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="ollama_install_") as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / "artifact.tgz" if plat == "linux" else tmp_path / "artifact.bin"
            job["stage"] = "downloading"
            total_bytes = 0
            downloaded = 0
            async with httpx.AsyncClient(timeout=httpx.Timeout(1800.0, connect=15.0)) as client:
                async with client.stream("GET", url, follow_redirects=True) as r:
                    r.raise_for_status()
                    total_bytes = int(r.headers.get("content-length") or 0)
                    with open(archive, "wb") as fh:
                        async for chunk in r.aiter_bytes(262144):
                            fh.write(chunk)
                            downloaded += len(chunk)
                            if total_bytes > 0:
                                job["pct"] = min(int((downloaded / total_bytes) * 100), 99)

            if plat == "linux":
                job["stage"] = "extracting"
                with tarfile.open(archive) as tf:
                    member = next((m for m in tf.getmembers() if Path(m.name).name == "ollama"), None)
                    if member is None:
                        raise RuntimeError("ollama binary not found in archive")
                    src = tf.extractfile(member)
                    if not src:
                        raise RuntimeError("could not read ollama binary from archive")
                    with open(ollama_bin(), "wb") as out:
                        shutil.copyfileobj(src, out)
                ollama_bin().chmod(0o755)
                # Symlink into /usr/local/bin when permitted so `ollama` is on PATH
                try:
                    link = Path("/usr/local/bin/ollama")
                    if os.access("/usr/local/bin", os.W_OK) and not link.exists():
                        link.symlink_to(ollama_bin())
                except Exception as exc:  # noqa: BLE001
                    job["log"] += f"(note: no global symlink — {exc})\n"
                job["pct"] = 100
                job["status"] = "done"
                job["stage"] = "installed"
            elif plat == "darwin":
                job["stage"] = "extracting"
                dest = storage_dir() / "Ollama.app"
                base = dest.parent
                with zipfile.ZipFile(archive) as zf:
                    names = zf.namelist()
                    if not names:
                        raise RuntimeError("empty archive")
                    if dest.exists() and names and names[0].startswith("Ollama.app/"):
                        shutil.rmtree(dest, ignore_errors=True)
                    _safe_extract_zip(zf, base)
                # Move the app into /Applications if possible
                app_root = base / "Ollama.app"
                if app_root.exists():
                    applications = Path("/Applications")
                    try:
                        if applications.exists() and os.access(applications, os.W_OK):
                            target = applications / "Ollama.app"
                            shutil.rmtree(target, ignore_errors=True)
                            shutil.move(str(app_root), str(target))
                            job["log"] += "Installed to /Applications/Ollama.app\n"
                    except Exception as exc:  # noqa: BLE001
                        job["log"] += f"(could not copy to /Applications — finished, unmount the app manually: {exc})\n"
                job["pct"] = 100
                job["status"] = "done"
                job["stage"] = "installed"
            elif plat == "windows":
                # archive is the downloaded OllamaSetup.exe
                job["stage"] = "launching"
                exe_art = archive
                if exe_art.exists():
                    subprocess.Popen(
                        [str(exe_art)],
                        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    job["log"] += "Installer launched — complete the setup wizard in the new window.\n"
                job["pct"] = 100
                job["status"] = "done"
                job["stage"] = "installer_launched"
    except Exception as e:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = f"{type(e).__name__}: {e}"
        job["log"] += f"\nERROR: {e}\n"
        logger.error("Ollama install failed: %s", e)
    finally:
        if job.get("status") == "downloading":
            job["status"] = "error"
            job["error"] = job.get("error") or "Install ended before finishing"


@router.get("/ollama/install/{job_id}", response_model=Dict[str, Any])
async def ollama_install_status(job_id: str):
    job = _install_jobs.get(job_id)
    if not job:
        return {"success": False, "error": "Unknown install job"}
    return {"success": True, "job": job}


# ═══════════════════════════════════════════════════════════════════
# LM Studio — one-click install of the official `lms` daemon
# ═══════════════════════════════════════════════════════════════════

@router.post("/lmstudio/install", response_model=Dict[str, Any])
async def lmstudio_install():
    """One-click install of the official LM Studio daemon (`lms`) for the
    host OS — server included, no GUI needed. Runs as a pollable job."""
    if _find_lmstudio():
        return {"success": True, "already_installed": True}

    plat = _os_platform()
    arch = _machine_arch()
    url = _resolve_lmstudio_artifact(plat, arch)
    if not url:
        return {"success": False, "error": f"No LM Studio installer known for platform: {platform.system()} {arch}"}

    job_id = uuid.uuid4().hex[:12]
    _install_jobs[job_id] = {
        "id": job_id, "kind": "lmstudio", "tool": "LM Studio",
        "status": "downloading", "stage": "queued", "pct": 0, "error": "", "log": "",
    }
    asyncio.create_task(_run_lmstudio_install_job(job_id, url, plat))
    return {"success": True, "job_id": job_id, "url": url}


def _safe_extract_zip(zf: zipfile.ZipFile, dest: Path) -> None:
    """ZipSlip-safe extraction: members stay under dest or are skipped."""
    dest = dest.resolve()
    for info in zf.infolist():
        target = (dest / info.filename).resolve()
        try:
            target.relative_to(dest)
        except ValueError:
            logger.warning("skipping zip member outside dest: %s", info.filename)
            continue
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def _safe_extract_tar(tf: tarfile.TarFile, dest: Path) -> None:
    """TarSlip-safe extraction (symlinks/hardlinks/devices skipped)."""
    dest = dest.resolve()
    for member in tf.getmembers():
        if member.issym() or member.islnk() or not member.isfile():
            logger.warning("skipping tar member %s (not a regular file)", member.name)
            continue
        target = (dest / member.name).resolve()
        try:
            target.relative_to(dest)
        except ValueError:
            logger.warning("skipping tar member outside dest: %s", member.name)
            continue
        src = tf.extractfile(member)
        if not src:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)
        try:
            mode = member.mode & 0o777
            if mode:
                target.chmod(mode)
        except OSError:
            pass


async def _run_lmstudio_install_job(job_id: str, url: str, plat: str) -> None:
    job = _install_jobs[job_id]
    try:
        with tempfile.TemporaryDirectory(prefix="lmstudio_install_") as tmp:
            tmp_path = Path(tmp)
            ext = ".zip" if url.endswith(".zip") else ".tar.gz"
            archive = tmp_path / f"lmstudio{ext}"

            job["stage"] = "downloading"
            total_bytes = 0
            downloaded = 0
            async with httpx.AsyncClient(timeout=httpx.Timeout(1800.0, connect=15.0), follow_redirects=True) as client:
                async with client.stream("GET", url) as r:
                    r.raise_for_status()
                    total_bytes = int(r.headers.get("content-length") or 0)
                    with open(archive, "wb") as fh:
                        async for chunk in r.aiter_bytes(262144):
                            fh.write(chunk)
                            downloaded += len(chunk)
                            if total_bytes > 0:
                                job["pct"] = min(int((downloaded / total_bytes) * 100), 50)

            # Optional SHA-512 verification (the official installer does this too)
            job["stage"] = "verifying"
            try:
                async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                    cr = await client.get(url + ".sha512")
                if cr.status_code == 200:
                    want = cr.text.strip().split()[0]
                    if want:
                        got = hashlib.sha512(archive.read_bytes()).hexdigest()
                        if got.lower() != want.lower():
                            raise RuntimeError(f"checksum mismatch for {Path(url).name}")
            except httpx.HTTPStatusError:  # noqa: BLE001
                pass

            job["stage"] = "extracting"
            job["pct"] = 60
            if ext == ".zip":
                with zipfile.ZipFile(archive) as zf:
                    _safe_extract_zip(zf, tmp_path)
            else:
                with tarfile.open(archive) as tf:
                    _safe_extract_tar(tf, tmp_path)

            boot_name = "llmster.exe" if plat == "windows" else "llmster"
            boot = next((p for p in tmp_path.glob(boot_name)), None)
            if boot is None:
                boot = next((p for p in tmp_path.rglob(boot_name)), None)
            if boot is None:
                raise RuntimeError("bootstrap binary (llmster) not found in archive")
            if plat != "windows":
                boot.chmod(0o755)

            job["stage"] = "installing"
            job["pct"] = 80
            env = dict(os.environ)
            env["LMS_BOOTSTRAP_INSTALL_SH"] = "1"
            result = subprocess.run(
                [str(boot), "bootstrap"],
                capture_output=True, text=True, timeout=600, cwd=str(tmp_path), env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()[-1200:]
                raise RuntimeError(f"lms bootstrap failed ({result.returncode}): {detail}")
            if (result.stdout or "").strip():
                job["log"] += (result.stdout.strip()[-1200:]) + "\n"

            binary = _find_lmstudio()
            if not binary:
                raise RuntimeError("lms not found after bootstrap")
            job["binary"] = binary
            job["version"] = _lmstudio_version(binary)
            job["pct"] = 100
            job["status"] = "done"
            job["stage"] = "installed"
            job["log"] += f"\nLM Studio daemon installed at {binary}\n"
    except Exception as e:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = f"{type(e).__name__}: {e}"
        job["log"] += f"\nERROR: {e}\n"
        logger.error("LM Studio install failed: %s", e)
    finally:
        if job.get("status") == "downloading":
            job["status"] = "error"
            job["error"] = job.get("error") or "Install ended before finishing"


@router.get("/lmstudio/install/{job_id}", response_model=Dict[str, Any])
async def lmstudio_install_status(job_id: str):
    job = _install_jobs.get(job_id)
    if not job:
        return {"success": False, "error": "Unknown install job"}
    return {"success": True, "job": job}


@router.post("/lmstudio/launch", response_model=Dict[str, Any])
async def lmstudio_launch():
    """Start the LM Studio `lms` server (serves the OpenAI-compatible API)."""
    url = (settings.llm.lmstudio_base_url or "http://localhost:1234/v1").rstrip("/")
    if _server_reachable(url):
        return {"success": True, "already_running": True, "server_url": url}
    binary = _find_lmstudio()
    if not binary:
        return {"success": False, "error": "LM Studio daemon (lms) not installed — use Auto-install first."}
    try:
        logs_dir().mkdir(parents=True, exist_ok=True)
        log_path = logs_dir() / "lmstudio_server.log"
        kwargs: Dict[str, Any] = {
            "stdout": open(log_path, "ab"),
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.DEVNULL,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen([binary, "server", "start"], **kwargs)
        return {"success": True, "started": True, "server_url": url, "log": str(log_path)}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"{type(e).__name__}: {e}"}

# ═══════════════════════════════════════════════════════════════════
# Cloudflare WARP — status / connect / disconnect / quick-reset / install
# ═══════════════════════════════════════════════════════════════════


@router.get("/warp/status", response_model=Dict[str, Any])
async def warp_status():
    """Current WARP tunnel state + egress IP (for the Settings UI)."""
    st = await get_rotator().status()
    return {"success": True, **st}


@router.post("/warp/connect", response_model=Dict[str, Any])
async def warp_connect():
    """Connect the WARP tunnel."""
    ok = await get_rotator().connect()
    st = await get_rotator().status()
    return {"success": ok, "connected": st.get("connected", False), "ip": st.get("ip", "")}


@router.post("/warp/disconnect", response_model=Dict[str, Any])
async def warp_disconnect():
    """Disconnect the WARP tunnel."""
    ok = await get_rotator().disconnect()
    return {"success": ok, "connected": False}


@router.post("/warp/reset", response_model=Dict[str, Any])
async def warp_reset():
    """Quick-reset: disconnect → registration new → connect (new egress IP)."""
    rotator = get_rotator()
    ok = await rotator.rotate()
    st = await rotator.status()
    return {
        "success": ok,
        "connected": st.get("connected", False),
        "ip": st.get("ip", ""),
        "note": "WARP quick-reset complete — new egress IP assigned." if ok
                else "WARP quick-reset failed — see logs.",
    }


@router.post("/warp/install", response_model=Dict[str, Any])
async def warp_install():
    """One-click install of the Cloudflare WARP client for the host OS.

    Delegates to ``agent.warp_bootstrap`` so Windows (silent MSI), Linux
    (distro repo + warp-svc) and macOS (silent pkg) share one code path.
    Runs as an async job the UI polls (same pattern as Ollama install).
    """
    if await get_rotator().healthy():
        return {"success": True, "already_installed": True}
    job_id = uuid.uuid4().hex[:12]
    _install_jobs[job_id] = {"id": job_id, "status": "running", "stage": "queued", "pct": 0, "error": "", "log": ""}
    asyncio.create_task(_run_warp_install_job(job_id))
    return {"success": True, "job_id": job_id}


async def _run_warp_install_job(job_id: str) -> None:
    job = _install_jobs[job_id]

    async def _progress(stage: str, pct: int, line: str = "") -> None:
        job["stage"] = stage
        job["pct"] = pct
        if line:
            job["log"] += line

    try:
        from agent.warp_bootstrap import bootstrap
        result = await bootstrap(progress=_progress)
        if result.get("success"):
            job["status"] = "done"
            job["pct"] = 100
            job["stage"] = "connected" if result.get("connected") else "installed"
            job["log"] += "\nCloudflare WARP ready.\n"
        else:
            job["status"] = "error"
            job["error"] = result.get("error") or "WARP install failed — see logs."
    except Exception as e:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = f"{type(e).__name__}: {e}"
        logger.error("WARP install failed: %s", e)
    finally:
        if job.get("status") == "running":
            job["status"] = "error"
            job["error"] = job.get("error") or "WARP install ended without a success status"


@router.get("/warp/install/{job_id}", response_model=Dict[str, Any])
async def warp_install_status(job_id: str):
    job = _install_jobs.get(job_id)
    if not job:
        return {"success": False, "error": "Unknown install job"}
    return {"success": True, "job": job}
