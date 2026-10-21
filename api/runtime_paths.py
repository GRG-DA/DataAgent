"""Canonical resolution of the writable runtime data directory.

Why this module exists
----------------------
The Windows desktop build is installed into ``C:\\Program Files\\DataAgent``,
which a normal (non-elevated) user **cannot write to**. Everything the app
persists at runtime therefore has to live in a per-user, writable directory
instead of next to the executable.

``run.py::_frozen_bootstrap()`` already prepares that directory and chdirs
into it:

* Windows — ``%LOCALAPPDATA%\\DataAgent``
* POSIX — ``$XDG_DATA_HOME/DataAgent`` or ``~/.local/share/DataAgent``

Modules that resolve their own paths from ``__file__`` bypass that chdir.
In a PyInstaller build ``__file__`` points *into the bundle*
(``<install>\\ _internal\\api\\...``), so the file lands in an unwritable
directory, the write raises ``OSError``, and the caller silently falls back
to a fresh random value on every launch. That is how the JWT signing secret
and the paid-plan grant key used to disappear on every restart.

This module is the single place that answers "where does writable runtime
state live?". It is deliberately free of side effects so it stays testable.

Resolution order
----------------
1. ``$DATAAGENT_DATA_DIR`` — an explicit override. It wins over everything
   else and is what ``run.py::_frozen_bootstrap`` exports for a frozen
   build.
2. The frozen-build per-user directory (``%LOCALAPPDATA%\\DataAgent`` on
   Windows, ``$XDG_DATA_HOME/DataAgent`` on POSIX) when the process is a
   PyInstaller bundle.
3. The current working directory — a source checkout running
   ``python run.py``.

In a frozen build the result is **never** inside the PyInstaller bundle
(``sys._MEIPASS``), even if an override points there.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

#: Environment variable that overrides every other resolution rule.
DATA_DIR_ENV = "DATAAGENT_DATA_DIR"

#: The name of the per-user runtime directory (inside %LOCALAPPDATA% etc.).
APP_DIR_NAME = "DataAgent"

#: The sub-directory of the runtime dir that holds app state (warehouse,
#: raw uploads, and the ``.jwt_secret`` / ``.grant_key`` key material).
DATA_SUBDIR = "data"


def is_frozen() -> bool:
    """True when this process runs from a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False)) or bool(getattr(sys, "_MEIPASS", None))


def bundle_dir() -> Path | None:
    """The PyInstaller bundle directory (``sys._MEIPASS``), when there is one."""
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else None


def _is_inside(child: Path, parent: Path) -> bool:
    """True when *child* is *parent* or lives under it."""
    child_norm = os.path.normcase(os.path.abspath(str(child)))
    parent_norm = os.path.normcase(os.path.abspath(str(parent)))
    if child_norm == parent_norm:
        return True
    return child_norm.startswith(parent_norm.rstrip("\\/") + os.sep)


def _per_user_dir() -> Path:
    """The writable per-user directory a frozen build runs in."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_DIR_NAME


def _cwd() -> Path:
    """The current working directory, with a safe fallback."""
    try:
        return Path.cwd()
    except OSError:  # pragma: no cover — the cwd was deleted underneath us
        return Path.home()


def project_root() -> Path:
    """The source checkout's root (the directory that holds ``config/``).

    In a frozen build this is the BUNDLE directory, so it is only meaningful
    for a source checkout.
    """
    return Path(__file__).resolve().parent.parent


def runtime_dir_warning() -> str:
    """A warning when a source run's working directory is not the project root.

    In a source checkout the runtime state (the warehouse, ``config/users.json``
    and the key files) is resolved against the CURRENT WORKING DIRECTORY, so
    running ``python C:\\repo\\run.py`` from somewhere else starts a SECOND,
    empty install: a different warehouse, different accounts, and a re-minted
    JWT secret and grant key.

    The shipped ``.exe`` is not affected - a frozen build always uses the
    per-user directory.  This only matters for a developer, but a silent
    second install is confusing, so it is reported.
    """
    if is_frozen() or (os.environ.get(DATA_DIR_ENV) or "").strip():
        return ""
    try:
        cwd = _cwd().resolve()
        root = project_root().resolve()
    except OSError:  # pragma: no cover
        return ""
    if cwd == root:
        return ""
    if (root / "config" / "settings.yaml").exists() and not (cwd / "config").exists():
        return (
            f"DataAgent is running from {cwd} but the project is at {root}. "
            "A source run keeps its data (warehouse, accounts, key files) in "
            "the working directory, so this starts a SEPARATE, empty install. "
            "Run it from the project root, or set "
            f"{DATA_DIR_ENV} to the directory that holds the existing data."
        )
    return ""


def runtime_data_dir() -> Path:
    """The WRITABLE per-user runtime directory.

    Resolution order:

    1. ``$DATAAGENT_DATA_DIR`` — an explicit override (tests, frozen builds).
    2. The frozen-build per-user directory (``%LOCALAPPDATA%\\DataAgent`` on
       Windows, ``$XDG_DATA_HOME/DataAgent`` on POSIX).
    3. The current working directory (a source checkout).

    Never returns a path inside the PyInstaller bundle: a frozen build
    installed under ``Program Files`` cannot write there, and a key file
    that cannot be persisted is silently re-minted on every launch.

    This function creates nothing — it only computes a path.
    """
    bundle = bundle_dir()

    override = (os.environ.get(DATA_DIR_ENV) or "").strip()
    if override:
        candidate = Path(override).expanduser()
        if bundle is None or not _is_inside(candidate, bundle):
            return candidate
        logger.warning(
            "%s points inside the PyInstaller bundle (%s); ignoring it — the "
            "bundle is not writable by a normal user.",
            DATA_DIR_ENV, candidate,
        )

    if is_frozen():
        return _per_user_dir()

    return _cwd()


def runtime_file(*parts: str) -> Path:
    """``runtime_data_dir()`` joined with *parts*.

    The parent directories are **not** created — callers create them right
    before they write.
    """
    return runtime_data_dir().joinpath(*parts)
