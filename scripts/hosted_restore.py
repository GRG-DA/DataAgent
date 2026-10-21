#!/usr/bin/env python3
"""Restore a hosted DataAgent backup.

A convenience wrapper around ``scripts/hosted_backup.py restore`` so an
operator does not have to remember which of the two scripts owns the
restore path.  Everything (including the ``--confirm`` gate) is the
backup tool's behaviour; this file only forwards the arguments.

    python scripts/hosted_restore.py --from ./backups/2026-10-20T10-00-00Z --confirm

Exit codes: ``0`` success, ``1`` error, ``3`` refused (no ``--confirm``).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Optional

_TOOL = Path(__file__).resolve().parent / "hosted_backup.py"


def _load_tool():
    """Import the backup tool next to this script."""
    spec = importlib.util.spec_from_file_location("_hosted_backup", _TOOL)
    if spec is None or spec.loader is None:  # pragma: no cover - broken install
        raise RuntimeError(f"cannot load {_TOOL}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main(argv: Optional[list[str]] = None) -> int:
    """Forward to the backup tool's ``restore`` command."""
    tool = _load_tool()
    args = list(argv) if argv is not None else sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        print("usage: hosted_restore.py --from <backup-dir> [--root DIR] --confirm")
        return 2
    return tool.main(["restore", *args])


if __name__ == "__main__":
    raise SystemExit(main())
