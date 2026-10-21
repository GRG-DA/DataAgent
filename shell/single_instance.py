"""Single-instance guard: Windows named mutex, POSIX lockfile fallback."""

import sys
import tempfile
from pathlib import Path

_LOCK = None


def acquire() -> bool:
    """Return True if this is the only instance (or the guard is unavailable)."""
    global _LOCK
    if sys.platform == "win32":
        return _acquire_windows()
    return _acquire_posix()


def _acquire_windows() -> bool:
    global _LOCK
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, "Local\\DataAgentShell")
    if not handle:
        return True  # cannot create mutex — do not block startup
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False
    _LOCK = handle
    return True


def _acquire_posix() -> bool:
    global _LOCK
    try:
        import fcntl
    except ImportError:
        # No POSIX file locking available — do not block startup.
        return True

    lock_path = Path(tempfile.gettempdir()) / "dataagent-shell.lock"
    f = open(lock_path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return False
    _LOCK = f
    return True


def release() -> None:
    global _LOCK
    if _LOCK is None:
        return
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.CloseHandle(_LOCK)
    else:
        import fcntl

        try:
            fcntl.flock(_LOCK, fcntl.LOCK_UN)
        except Exception:  # noqa: BLE001
            pass
        _LOCK.close()
    _LOCK = None