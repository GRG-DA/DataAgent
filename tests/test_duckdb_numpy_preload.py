"""DuckDB's Python client must find numpy's legacy multiarray shim early.

DuckDB's Windows Python client documents that fetching pandas/numpy results
from worker threads can deadlock or corrupt the heap when
``numpy.core.multiarray`` was not imported on the main thread. NumPy 2.x's
``import numpy`` loads ``numpy._core.multiarray`` but not the legacy
``numpy.core.multiarray`` shim, so the desktop shell must load it before the
backend thread (and DuckDB) starts.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_import(module: str) -> subprocess.CompletedProcess[str]:
    code = (
        f"import sys; import {module}; "
        "print('numpy.core.multiarray' in sys.modules)"
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )


def test_run_entrypoint_preloads_numpy_multiarray():
    """The frozen shell starts through run.py; it must load the shim first."""
    result = _run_import("run")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "True", result.stdout


def test_api_module_also_preloads_numpy_multiarray():
    """Direct uvicorn/web startup must get the same guard."""
    result = _run_import("api.main")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "True", result.stdout
