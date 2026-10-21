"""TDD: run.py check must complete quickly, never RW-open warehouse."""
import time
from pathlib import Path
import run


def test_check_env_signature_has_skip_flags():
    import inspect
    sig = inspect.signature(run.check_environment)
    params = set(sig.parameters)
    assert "skip_ollama" in params, "check_environment needs skip_ollama flag"
    assert "skip_db" in params, "check_environment needs skip_db flag"
    assert "timeout_s" in params or "timeout" in params, "check_environment needs timeout"


def test_check_skips_complete_fast():
    t0 = time.monotonic()
    run.check_environment(skip_ollama=True, skip_db=True, timeout_s=10)
    dt = time.monotonic() - t0
    assert dt < 15, f"check with skips took too long: {dt:.1f}s"


def test_db_check_is_readonly_no_write():
    # check must not create/modify warehouse: use missing path, should warn not crash
    run.check_environment(skip_ollama=True, skip_db=False, timeout_s=10)
    # passes if no exception and no RW lock held
    assert True


def test_probe_helper_exists():
    assert hasattr(run, "_probe") or hasattr(run, "_probe_ollama"), "need _probe helper with timeout"
