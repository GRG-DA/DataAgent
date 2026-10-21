#!/usr/bin/env python
"""
Main Entry Point
Run the LLM Data Analytics Agent
"""

import sys
import os
import argparse
import subprocess
import logging
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env into os.environ so JWT_SECRET / APP_* / LLM_* work for bare-metal
# runs too (docker compose already does this itself). No-op when .env is absent.
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=False)
except Exception:
    pass


def run_api():
    """Run the FastAPI server."""
    import uvicorn
    from config import settings

    uvicorn.run(
        "api.main:app",
        host=settings.app.host,
        port=settings.app.port,
        # Auto-reload spawns a second process that also opens warehouse.duckdb
        # read-write, which on Windows causes "file in use" IO errors in the
        # dashboard API. Opt-in via WAREHOUSE_RELOAD=1 instead of using debug.
        reload=os.getenv("WAREHOUSE_RELOAD", "") == "1",
        log_level=settings.logging.level.lower(),
    )


def run_web():
    """Run the web application (FastAPI + HTML frontend)."""
    import uvicorn
    from config import settings
    print(f"Starting LLM Data Agent web server on http://localhost:{settings.app.port}")
    uvicorn.run(
        "api.main:app",
        host=settings.app.host,
        port=settings.app.port,
        # Auto-reload spawns a second process that also opens warehouse.duckdb
        # read-write, which on Windows causes "file in use" IO errors in the
        # dashboard API. Opt-in via WAREHOUSE_RELOAD=1 instead of using debug.
        reload=os.getenv("WAREHOUSE_RELOAD", "") == "1",
        log_level=settings.logging.level.lower(),
    )


def run_shell(debug: bool = False):
    """Run the desktop shell (pywebview window around the web app)."""
    from shell.app import run_shell as _run_shell

    sys.exit(_run_shell(debug=debug))


def init_db():
    """Initialize database schema."""
    from warehouse.schema import create_warehouse_schema
    from config import settings

    print(f"Initializing database at {settings.duckdb.database_path}...")
    schema_manager = create_warehouse_schema(
        db_path=settings.duckdb.database_path,
        schema=settings.duckdb.schema,
    )
    print("Database initialized successfully!")


def install_ollama_model(model: str):
    """Install an Ollama model."""
    print(f"Pulling model: {model}...")
    result = subprocess.run(["ollama", "pull", model], capture_output=True, text=True)
    if result.returncode == 0:
        print(f"Model {model} installed successfully!")
    else:
        print(f"Failed to install model: {result.stderr}")
        sys.exit(1)


def list_ollama_models():
    """List available Ollama models."""
    result = subprocess.run(["ollama", "list"], capture_output=True, text=True)
    if result.returncode == 0:
        print(result.stdout)
    else:
        print("Ollama not running or not installed")
        sys.exit(1)


def _probe(name: str, fn, timeout_s: float = 5.0):
    """Run fn() with exception containment for env checks. Never raises."""
    import time as _time

    t0 = _time.monotonic()
    try:
        return fn()
    except FileNotFoundError:
        print(f"  X {name} not installed")
        return None
    except Exception as e:
        print(f"  ? {name} unavailable ({type(e).__name__})")
        return None
    finally:
        _ = _time.monotonic() - t0


def _probe_ollama(timeout_s: float = 5.0, use_cli: bool = False):
    """Probe Ollama HTTP API only by default. CLI fallback is opt-in
    because ollama.exe as GUI-subsystem binary can deadlock with
    capture_output on Windows."""
    print("\n--- Ollama ---")
    try:
        import httpx as _httpx
        resp = _httpx.get("http://localhost:11434/api/tags", timeout=timeout_s)
        if resp.status_code == 200:
            data = resp.json()
            models = data.get("models", [])
            print("  OK Ollama running")
            if models:
                print("  Models:")
                for m in models:
                    print(f"    - {m.get('name', m.get('model', 'unknown'))}")
            else:
                print("  (no models installed)")
            return
        print("  X Ollama not responding")
        return
    except Exception:
        print("  ? Ollama HTTP unavailable")
    if not use_cli:
        return
    try:
        result = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=timeout_s,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode == 0:
            print("  OK Ollama installed (cli)")
        else:
            print("  X Ollama not running")
    except FileNotFoundError:
        print("  X Ollama not installed")
    except Exception:
        print("  ? Ollama timeout / unavailable")


def check_environment(skip_ollama: bool = False, skip_db: bool = False,
                      timeout_s: float = 30.0, use_ollama_cli: bool = False):
    """Check environment setup. Never RW-opens the warehouse.

    check never writes warehouse.duckdb: config-file existence + optional
    read-only SELECT probe only.
    """
    import time as _time

    deadline = _time.monotonic() + max(5.0, float(timeout_s or 30))
    from config import settings

    print("=== Environment Check ===\n")

    # Python version
    print(f"Python: {sys.version}")

    # Check config files
    config_files = [
        "config/model_config.yaml",
        "config/db_config.yaml",
        "config/settings.yaml",
        "requirements.txt",
    ]
    for f in config_files:
        path = PROJECT_ROOT / f
        status = "OK" if path.exists() else "X"
        print(f"  {status} {f}")
        if _time.monotonic() > deadline:
            print("  ! check deadline reached")
            return

    # Check directories
    dirs = [
        "data/raw",
        "data/staging",
        "data/curated",
        "logs",
        "outputs/charts",
    ]
    for d in dirs:
        path = PROJECT_ROOT / d
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        status = "OK" if path.exists() else "X"
        print(f"  {status} {d}/")

    # Check Ollama - HTTP only, 5s cap, CLI opt-in
    if skip_ollama:
        print("\n--- Ollama (skipped) ---")
    else:
        _probe("ollama", lambda: _probe_ollama(
            timeout_s=min(5.0, max(1.0, float(timeout_s or 5) / 6)),
            use_cli=use_ollama_cli))

    # Check LLM model config
    model_config = load_yaml_config("config/model_config.yaml")
    model_name = model_config.get("ollama", {}).get("model_name", "")
    print(f"\n--- Configured Model: {model_name or 'NOT SET'} ---")
    if not model_name:
        print("  ! Set model_name in config/model_config.yaml")

    # Check database - read-only probe only, never RW open
    if skip_db:
        print("\n--- Database (skipped) ---")
    else:
        print("\n--- Database ---")
        db_path = Path(str(settings.duckdb.database_path))

        def _db_probe():
            if not db_path.exists():
                print(f"  ! Database not found: {db_path}")
                print("  Run: python run.py init-db")
                return
            print(f"  OK Database exists: {db_path}")
            try:
                import duckdb as _ddb
                con = _ddb.connect(str(db_path), read_only=True)
                try:
                    con.execute("SELECT 1").fetchone()
                    print("  OK Database readable (read-only probe)")
                finally:
                    con.close()
            except Exception as e:
                print(f"  ? Database probe failed ({type(e).__name__}): locked or busy?")
            return None
        _probe("database", _db_probe)

    print("\n=== Check Complete ===")


def load_yaml_config(path: str) -> dict:
    """Load YAML config."""
    import yaml
    p = Path(path)
    if p.exists():
        with open(p) as f:
            return yaml.safe_load(f) or {}
    return {}


def _frozen_bootstrap() -> None:
    """Prepare a writable runtime dir for frozen (PyInstaller) builds.

    The installer places the exe under Program Files, which normal users
    cannot write to. All app paths (logs/, data/, config/) are cwd-relative,
    so we chdir to a per-user data dir instead. Bundled config files are
    copied from _MEIPASS on first run; user edits survive reinstall.
    """
    import shutil

    # Resolve the per-user writable runtime dir BEFORE the stdout redirect, so
    # the redirect target (<base>/logs/) is defined. base was previously set
    # below the redirect block, so every frozen windowed web launch crashed
    # with UnboundLocalError: cannot access local variable 'base' (no console,
    # no log, silent exit at startup).
    if sys.platform == "win32":
        base = Path(
            os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
        ) / "DataAgent"
    else:
        base = Path(
            os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
        ) / "DataAgent"
    base.mkdir(parents=True, exist_ok=True)

    # Export the runtime dir so every module in the process agrees on where
    # writable state lives (see api/runtime_paths.py). Without this the JWT
    # signing secret and the paid-plan grant key would resolve from
    # __file__, i.e. inside the (unwritable) bundle, and be re-minted on
    # every launch.
    os.environ.setdefault("DATAAGENT_DATA_DIR", str(base))
    (base / "data").mkdir(parents=True, exist_ok=True)

    # Windowed frozen builds (PyInstaller --windowed on Windows) have
    # sys.stdout/sys.stderr set to None, so any print() in the frozen web/api
    # path raises AttributeError at launch. This is why the onefile web build
    # historically shipped with --console (a visible terminal every launch).
    # Redirect stdout/stderr to a file only when frozen AND stdout is None, so
    # the windowed web exe launches cleanly with no console; the desktop shell
    # (also windowed) is unaffected.
    if (
        getattr(sys, "frozen", False)
        and (sys.stdout is None or sys.stderr is None)
    ):
        import io

        redirect = (base / "logs" / "runtime-stdout.log")
        redirect.parent.mkdir(parents=True, exist_ok=True)
        # Path.open("a", encoding=...) already returns a *text* handle, so
        # wrapping it again in TextIOWrapper makes print() encode to bytes and
        # feed them into a text-mode wrapper -> "TypeError: write() argument
        # must be str, not bytes" at launch. Open the raw file in binary append
        # mode so the single TextIOWrapper below is the only text layer.
        fh = redirect.open("ab")
        wrapper = io.TextIOWrapper(fh, encoding="utf-8", write_through=True)
        sys.stdout = wrapper
        if sys.stderr is None or not hasattr(sys.stderr, "write"):
            sys.stderr = wrapper
        print(
            "[DataAgent][windowed] stdout/stderr redirected to "
            f"{redirect} (no console in windowed frozen build)."
        )

    os.chdir(base)

    # App paths are cwd-relative (data/, logs/, outputs/). Create them in the
    # runtime dir now â€” duckdb.connect() errors if data\curated is missing, and
    # the audit/logging file handlers need logs/ to exist.
    for sub in ("data/raw", "data/staging", "data/curated", "logs", "outputs/charts"):
        (base / sub).mkdir(parents=True, exist_ok=True)

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        src_cfg = Path(meipass) / "config"
        dst_cfg = base / "config"
        dst_cfg.mkdir(parents=True, exist_ok=True)
        for name in ("settings.yaml", "model_config.yaml",
                     "db_config.yaml", "llm_runtime.json"):
            src = src_cfg / name
            dst = dst_cfg / name
            if src.exists() and not dst.exists():
                shutil.copy2(src, dst)


def main():
    # Frozen builds: resolve cwd-relative paths (logs/, data/, config/) against
    # a writable per-user dir, so launching by double-click or from any cwd works.
    if getattr(sys, "frozen", False):
        _frozen_bootstrap()
    else:
        # A source run keeps its data in the working directory. Running it
        # from somewhere other than the project root starts a SECOND, empty
        # install (a different warehouse, different accounts, a re-minted JWT
        # secret and grant key), so say so instead of failing silently.
        try:
            from api.runtime_paths import runtime_dir_warning

            _warning = runtime_dir_warning()
            if _warning:
                print(f"WARNING: {_warning}")
        except Exception:  # noqa: BLE001 — the warning must never block a run
            pass

    parser = argparse.ArgumentParser(
        description="LLM Data Analytics Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run.py              # Start API server
  python run.py web          # Start web UI (FastAPI + HTML frontend)
  python run.py shell        # Start desktop shell (pywebview window)
  python run.py init-db      # Initialize database
  python run.py check        # Check environment
  python run.py pull-model qwen2.5:7b  # Install Ollama model
  python run.py list-models  # List Ollama models
        """
    )

    parser.add_argument(
        "command",
        nargs="?",
        default="shell" if _is_shell_build() else "api",
        choices=["api", "web", "shell", "init-db", "check", "pull-model", "list-models"],
        help="Command to run",
    )

    parser.add_argument(
        "model",
        nargs="?",
        help="Model name for pull-model command",
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable developer tools in the desktop shell",
    )

    parser.add_argument(
        "--skip-ollama",
        action="store_true",
        help="check: skip Ollama probe",
    )
    parser.add_argument(
        "--skip-db",
        action="store_true",
        help="check: skip warehouse probe",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="check: overall deadline seconds (default 30)",
    )
    parser.add_argument(
        "--ollama-cli",
        action="store_true",
        help="check: allow ollama CLI fallback (may hang on Windows)",
    )

    args = parser.parse_args()

    if args.command == "api":
        run_api()
    elif args.command == "web":
        run_web()
    elif args.command == "shell":
        run_shell(debug=args.debug)
    elif args.command == "init-db":
        init_db()
    elif args.command == "check":
        check_environment(skip_ollama=args.skip_ollama, skip_db=args.skip_db,
                          timeout_s=args.timeout, use_ollama_cli=args.ollama_cli)
    elif args.command == "pull-model":
        if not args.model:
            print("Error: Model name required")
            print("Usage: python run.py pull-model <model_name>")
            sys.exit(1)
        install_ollama_model(args.model)
    elif args.command == "list-models":
        list_ollama_models()


def _is_shell_build() -> bool:
    """True when frozen as the DataAgentShell executable (no-args -> shell)."""
    if getattr(sys, "frozen", False):
        return "shell" in Path(sys.executable).name.lower()
    return False


if __name__ == "__main__":
    main()
