"""Rebuild the shareable zip with security-sensitive files excluded.

Excluded entirely (runtime/credential state — regenerated on first boot):
  - .env                                 (JWT signing secret, API keys)
  - data/                                (the whole runtime data tree:
                                         customers' uploaded CSVs, the
                                         warehouse, the identity database)
  - data/.jwt_secret                     (JWT signing key)
  - data/.grant_key                      (local grant-signing key)
  - data/.grant_migrated                 (grant-migration marker)
  - data/users.db                        (password hashes, salts, MFA secrets)
  - config/users.json                    (password hashes, plaintext API keys)
  - config/db_connections.json           (saved DB credentials)
  - config/google_login_pending.json     (OAuth flow state)
  - config/llm_runtime.json              (provider/model runtime state)
  - data/curated/warehouse.duckdb*       (user data; keep the zip shareable)
Also excluded: .git, venvs, caches, build outputs, logs, backups, the
third-party binaries fetched at build time (``installers/opencode``) and the
stray repo-root artefacts (``out.html``, ``lib/``).

Usage:
    python scripts/build_secure_zip.py [--root DIR] [--out FILE] [--top NAME]

The previous version of this script shipped ``.env`` inside the "secure"
zip, because neither the exclude list nor the self-check mentioned it. The
exclusion list and the self-check are now derived from the same set, and
the self-check additionally rejects any ``*.env`` file and any file whose
name looks like a secret.

ADR-009 added the ``data/`` exclusion: the runtime data tree holds the
customers' uploaded CSVs, so every release zip used to ship them. The app
recreates ``data/raw``, ``data/staging`` and ``data/curated`` on startup
(``run.py`` and ``etl/load.py``/``warehouse/schema.py`` create the parent
directories), so excluding the whole tree costs a desktop install nothing.
"""
from __future__ import annotations

import argparse
import os
import zipfile
from pathlib import Path
from typing import Iterable, List

# Security-sensitive runtime/credential files — never ship.
EXCLUDE_FILES = {
    ".env",
    ".env.local",
    ".env.production",
    # Stale copy of the SPA index that used to be committed at the repo root.
    "out.html",
    os.path.join("data", ".jwt_secret"),
    os.path.join("data", ".grant_key"),
    os.path.join("data", ".grant_migrated"),
    os.path.join("data", "users.db"),
    os.path.join("data", "audit.db"),
    os.path.join("config", "users.json"),
    os.path.join("config", "db_connections.json"),
    os.path.join("config", "google_login_pending.json"),
    os.path.join("config", "llm_runtime.json"),
    os.path.join("data", "curated", "warehouse.duckdb"),
    os.path.join("data", "curated", "warehouse.duckdb.wal"),
    os.path.join("data", "app.duckdb"),
}

# Sibling copies (e.g. linux-install/) get the same filename-level exclusions.
SENSITIVE_BASENAMES = {
    ".env", ".jwt_secret", ".grant_key", ".grant_migrated",
    "users.json", "users.db", "audit.db", "db_connections.json",
    "google_login_pending.json", "llm_runtime.json", "revoked_jtis.json",
    "warehouse.duckdb", "warehouse.duckdb.wal", "app.duckdb",
}

# Whole directories that must never be shipped, matched on the FIRST path
# component so only the runtime data tree is dropped (a nested directory
# that happens to be called "data" elsewhere is left alone).
EXCLUDE_TOP_DIRS = {
    "data",
}

EXCLUDE_DIRS = {
    ".git", ".venv-mcp", ".venv-laya", ".venv-strix", ".pytest_cache",
    ".freebuff", ".ecc", ".agents", ".opencode", ".vscode", ".github",
    "__pycache__", "build", "dist", "dist-verify2", "graphify-out",
    "logs", "outputs", "backups", "node_modules", "keygen_app-dist",
    "google_login_pending", "website-react",
    # Third-party binaries fetched at build time (the opencode CLI is ~180 MB
    # and is downloaded from GitHub by scripts/build_windows.ps1) - never
    # ship them in a source archive.
    "opencode",
    # Empty stray directory that used to be committed at the repo root.
    "lib",
    # Local E2E artifacts: screenshots, console logs and copies of the
    # files the upload tests push through the app.
    ".playwright-mcp",
}


def _is_sensitive_name(fn: str) -> bool:
    """True when a *filename* alone marks the file as secret-bearing."""
    if fn in SENSITIVE_BASENAMES:
        return True
    if fn.endswith(".pem") or fn.endswith(".key"):
        return True
    # The billing server's local order store (purchase data).
    if fn.endswith((".sqlite3", ".sqlite3-wal", ".sqlite3-shm")):
        return True
    # Any dotenv variant: .env, .env.local, foo.env
    if fn == ".env" or fn.startswith(".env.") or fn.endswith(".env"):
        return True
    if fn.startswith("id_rsa") or fn.endswith((".pfx", ".p12", ".keystore")):
        return True
    return False


def _is_excluded(rel_norm: str, fn: str) -> bool:
    if _is_sensitive_name(fn):
        return True
    rel_norm = rel_norm.replace("/", os.sep)
    return rel_norm in EXCLUDE_FILES or os.path.normpath(rel_norm) in EXCLUDE_FILES


def _is_excluded_dir(rel_dir: str) -> bool:
    """True for a directory (relative to the archive root) that must not ship.

    The runtime ``data/`` tree is dropped whole: it holds the customers'
    uploaded CSVs, the warehouse and the identity database. The app
    recreates the directories it needs on startup.
    """
    rel = os.path.normpath(rel_dir or ".")
    if rel in (".", ""):
        return False
    return rel.split(os.sep)[0] in EXCLUDE_TOP_DIRS


def _relative_to_top(name: str, top: str = "") -> str:
    """Strip the archive's top-level folder from *name*."""
    parts = name.replace("/", os.sep).split(os.sep)
    if top and parts and parts[0] == top:
        parts = parts[1:]
    return os.path.join(*parts) if parts else ""


def find_violations(names: Iterable[str], top: str = "") -> List[str]:
    """Return the archive entries that must never ship.

    Two rules:
      * nothing under the runtime ``data/`` directory (customer uploads, the
        warehouse, the identity database), and
      * nothing whose *name* marks it as secret-bearing.
    """
    violations: List[str] = []
    for name in names:
        rel = _relative_to_top(name, top)
        if _is_excluded_dir(rel) or _is_sensitive_name(os.path.basename(rel)):
            violations.append(name)
    return violations


def build(root: Path, out: Path, top: str) -> int:
    count, skipped = 0, []
    excluded_dirs: List[str] = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = os.path.relpath(dirpath, root)
            if _is_excluded_dir(rel_dir):
                # Prune the whole subtree: the runtime data tree must never
                # reach a release archive.
                excluded_dirs.append(rel_dir)
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for fn in filenames:
                if fn.endswith((".pyc", ".pyo")):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root)
                if _is_excluded(rel, fn):
                    skipped.append(rel)
                    continue
                try:
                    zf.write(full, os.path.join(top, rel))
                    count += 1
                except (PermissionError, OSError) as exc:
                    skipped.append(f"{rel} ({exc})")

    size = out.stat().st_size
    print(f"zipped {count} files -> {out} ({size / 1024 / 1024:.1f} MB)")
    for d in excluded_dirs:
        print("excluded directory:", d)
    if skipped:
        print("skipped (sensitive/locked):")
        for s in skipped:
            print("  -", s)

    # Self-check: assert nothing sensitive slipped in. Uses the same
    # predicates as the writer, so the check can never drift from the rules.
    with zipfile.ZipFile(out) as zf:
        bad = find_violations(zf.namelist(), top)
    if bad:
        raise SystemExit(f"SENSITIVE FILES PRESENT IN ZIP: {bad}")
    print("self-check OK: no sensitive files and no data/ paths in zip")
    return count


def main() -> None:
    default_root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(default_root),
                    help="source tree to zip (default: repo root)")
    ap.add_argument("--out", default="",
                    help="output zip path (default: <root>/../<root>-secure.zip)")
    ap.add_argument("--top", default="",
                    help="top-level folder name inside the zip (default: root name)")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    out = Path(args.out).resolve() if args.out else root.parent / f"{root.name}-secure.zip"
    top = args.top or root.name
    out.parent.mkdir(parents=True, exist_ok=True)
    build(root, out, top)


if __name__ == "__main__":
    main()
