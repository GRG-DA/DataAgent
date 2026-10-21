"""Path helpers that work both from source and from a frozen PyInstaller build."""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def resource_path(rel: str) -> str:
    """Resolve a bundled resource path (works frozen via PyInstaller _MEIPASS)."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, rel)
    return str(PROJECT_ROOT / rel)