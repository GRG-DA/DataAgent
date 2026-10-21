#!/usr/bin/env python3
"""Backup and restore for a **hosted** DataAgent deployment.

A local install keeps its data in a folder; a hosted install has to be able
to say "here is a consistent copy of everything, and here is how we put it
back".  This tool does that, and it refuses to do anything unsafe.

What is copied
--------------

===========================  ==========================================
``data/curated/warehouse``    the DuckDB warehouse (``EXPORT DATABASE``)
``data/tenants``              per-tenant warehouse files (optional mode)
``data/users.db``             the SQLite identity store
``data/audit.db``             the SQLite audit sink (when it exists)
``data/raw``                  the raw customer uploads
``config/*.yaml``             the non-secret configuration
===========================  ==========================================

Secrets are **excluded by default**: ``data/.jwt_secret``,
``data/.grant_key`` and the credential-bearing files in ``config/`` are
skipped and the tool says so loudly.  ``--include-secrets`` copies them and
prints a warning.

The DuckDB problem
------------------

DuckDB allows **one writer per database file**.  While the app is running
it holds ``warehouse.duckdb`` open, and *no* other process -- not even a
read-only one -- may open that file.  So the tool:

* opens the warehouse **read-only** and runs ``EXPORT DATABASE`` (a
  consistent, writer-safe snapshot that never fights the app), and
* if the file is locked by a running instance, **refuses with an explicit
  message** instead of copying a file that is mid-write.

``EXPORT DATABASE`` does not carry secondary indexes; the app recreates
them on boot (``CREATE INDEX IF NOT EXISTS``), so this is not data loss.

Usage
-----

    python scripts/hosted_backup.py backup  --out ./backups
    python scripts/hosted_backup.py verify  --from ./backups/2026-10-20T10-00-00Z
    python scripts/hosted_backup.py restore --from ./backups/2026-10-20T10-00-00Z --confirm

``restore`` is destructive: it replaces the live warehouse and the identity
store, so it requires ``--confirm`` -- the same confirmation the
``DELETE /tables`` endpoint demands.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

#: Manifest format version.
MANIFEST_VERSION = 1

#: The name of the manifest file inside a backup directory.
MANIFEST_NAME = "manifest.json"

#: Secret files outside ``config/``: copied only when the operator insists.
EXTRA_SECRET_PATHS = (
    "data/.jwt_secret",
    "data/.grant_key",
    "data/.grant_migrated",
    ".env",
)

#: Credential-bearing files inside ``config/``.
CONFIG_SECRET_NAMES = (
    "users.json",
    "db_connections.json",
    "google_login_pending.json",
    "llm_runtime.json",
    "revoked_jtis.json",
)

#: Every path (relative to the installation root) that must never reach a
#: backup unless ``--include-secrets`` is given.
SECRET_PATHS = EXTRA_SECRET_PATHS + tuple(
    f"config/{name}" for name in CONFIG_SECRET_NAMES
)

#: Secret files by name, whatever directory they live in.
SECRET_NAMES = frozenset({
    ".jwt_secret",
    ".grant_key",
    ".grant_migrated",
    ".env",
    *CONFIG_SECRET_NAMES,
})


class BackupError(RuntimeError):
    """A backup or restore could not be completed."""


class ConfirmationRequired(BackupError):
    """A destructive operation was attempted without ``--confirm``."""


# ── small helpers ─────────────────────────────────────────────────────────

def _timestamp() -> str:
    """A filesystem-safe UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_secret(relative: str) -> bool:
    """True when a root-relative path is a secret that must be excluded."""
    posix = relative.replace(os.sep, "/").lstrip("/")
    if posix in SECRET_PATHS:
        return True
    return Path(posix).name in SECRET_NAMES


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _collect_files(directory: Path) -> List[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.rglob("*") if p.is_file())


def _unique_directory(parent: Path, name: str) -> Path:
    """A directory that does not exist yet (a second in the same minute)."""
    candidate = parent / name
    suffix = 1
    while candidate.exists():
        candidate = parent / f"{name}-{suffix}"
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate


# ── the warehouse snapshot ────────────────────────────────────────────────

def export_warehouse(warehouse: Path, target: Path) -> Dict[str, Any]:
    """Snapshot the DuckDB warehouse with ``EXPORT DATABASE``.

    The source is opened **read-only**, so the snapshot never competes with
    the app's writer.  When the app holds the file, DuckDB refuses the
    connection and we raise a clear, actionable error instead of copying a
    file that is being written to.
    """
    if not warehouse.exists():
        raise BackupError(f"the warehouse does not exist: {warehouse.name}")

    import duckdb

    try:
        conn = duckdb.connect(str(warehouse), read_only=True)
    except Exception as exc:  # noqa: BLE001
        raise BackupError(
            "the warehouse is locked by another process -- DataAgent is most "
            "likely running. DuckDB allows a single writer per file, so the "
            "snapshot has to wait. Stop the app (or take a volume snapshot) "
            f"and run the backup again. ({type(exc).__name__})"
        ) from exc

    try:
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        conn.execute(f"EXPORT DATABASE '{target.as_posix()}'")
    except Exception as exc:  # noqa: BLE001
        raise BackupError(
            f"the warehouse snapshot failed ({type(exc).__name__})"
        ) from exc
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    return {"method": "export_database", "source": warehouse.name}


# ── the backup ────────────────────────────────────────────────────────────

def _backup_config(root: Path, backup_dir: Path, include_secrets: bool) -> List[str]:
    """Copy the configuration; return the secret paths that were skipped."""
    source = root / "config"
    excluded: List[str] = []
    if not source.is_dir():
        return excluded
    for path in sorted(source.iterdir()):
        if not path.is_file():
            continue
        relative = _relative(path, root)
        secret = _is_secret(relative)
        if secret and not include_secrets:
            excluded.append(relative)
            continue
        if not secret and path.suffix != ".yaml":
            continue  # an unknown file type is not part of a backup
        _copy_file(path, backup_dir / "config" / path.name)
    return excluded


def _backup_tree(
    root: Path,
    backup_dir: Path,
    source_relative: str,
    dest_relative: str,
    include_secrets: bool,
    excluded: List[str],
) -> int:
    """Copy a directory tree; return the number of files copied."""
    source = root / source_relative
    if not source.is_dir():
        return 0
    copied = 0
    for path in _collect_files(source):
        relative = _relative(path, root)
        if not include_secrets and _is_secret(relative):
            excluded.append(relative)
            continue
        _copy_file(path, backup_dir / dest_relative / path.relative_to(source))
        copied += 1
    return copied


def _backup_single(
    root: Path,
    backup_dir: Path,
    source_relative: str,
    dest_relative: str,
    include_secrets: bool,
    excluded: List[str],
) -> bool:
    """Copy one file when it exists and is not a secret."""
    source = root / source_relative
    if not source.exists():
        return False
    if not include_secrets and _is_secret(source_relative):
        excluded.append(source_relative)
        return False
    _copy_file(source, backup_dir / dest_relative)
    return True


def _backup_extra_secrets(
    root: Path, backup_dir: Path, include_secrets: bool, excluded: List[str]
) -> None:
    """Handle the secrets that live outside ``config/``."""
    for relative in EXTRA_SECRET_PATHS:
        source = root / relative
        if not source.exists():
            continue
        if not include_secrets:
            excluded.append(relative)
            continue
        _copy_file(source, backup_dir / relative)


def _copy_data(
    root: Path,
    backup_dir: Path,
    include_secrets: bool,
    excluded: List[str],
) -> Dict[str, Any]:
    """Copy everything the backup contains; return the warehouse info."""
    warehouse_info = export_warehouse(
        root / "data" / "curated" / "warehouse.duckdb",
        backup_dir / "warehouse" / "export",
    )
    _backup_tree(
        root, backup_dir, "data/tenants", "data/tenants", include_secrets, excluded
    )
    _backup_single(
        root, backup_dir, "data/users.db", "data/users.db", include_secrets, excluded
    )
    _backup_single(
        root, backup_dir, "data/audit.db", "data/audit.db", include_secrets, excluded
    )
    _backup_tree(
        root, backup_dir, "data/raw", "data/raw", include_secrets, excluded
    )
    excluded.extend(_backup_config(root, backup_dir, include_secrets))
    _backup_extra_secrets(root, backup_dir, include_secrets, excluded)
    return warehouse_info


def _manifest_files(backup_dir: Path) -> List[Dict[str, Any]]:
    """One hash-and-size entry per file in the backup."""
    entries: List[Dict[str, Any]] = []
    for path in sorted(backup_dir.rglob("*")):
        if not path.is_file() or path.name == MANIFEST_NAME:
            continue
        entries.append(
            {
                "path": path.relative_to(backup_dir).as_posix(),
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return entries


def _print_summary(backup_dir: Path, manifest: Dict[str, Any]) -> None:
    """Tell the operator what is in the backup -- and what is not."""
    print(f"Backup written to {backup_dir}")
    print(f"  files      : {len(manifest['files'])}")
    included = manifest["includes_secrets"]
    print(f"  secrets    : {'INCLUDED (danger!)' if included else 'excluded'}")
    excluded = manifest["excluded_secrets"]
    if not excluded:
        print("  excluded   : none")
        return
    print("  excluded   :")
    for name in excluded:
        print(f"               - {name}")


def create_backup(
    root: str,
    out_dir: str,
    include_secrets: bool = False,
    label: str = "",
) -> Dict[str, Any]:
    """Create a backup of *root* under *out_dir*; return the manifest."""
    root_path = Path(root).resolve()
    out_path = Path(out_dir).resolve()
    name = _timestamp() + (f"-{label}" if label else "")
    backup_dir = _unique_directory(out_path, name)

    if include_secrets:
        print(
            "WARNING: --include-secrets copies credential material "
            "(JWT secret, grant key, credential files) into the backup. "
            "The archive is now as sensitive as the live installation -- "
            "encrypt it and restrict it.",
            file=sys.stderr,
        )

    excluded: List[str] = []
    try:
        warehouse_info = _copy_data(
            root_path, backup_dir, include_secrets, excluded
        )
    except Exception:
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise

    manifest: Dict[str, Any] = {
        "version": MANIFEST_VERSION,
        "tool": "scripts/hosted_backup.py",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "label": label,
        "backup_dir": str(backup_dir),
        "includes_secrets": bool(include_secrets),
        "excluded_secrets": sorted(set(excluded)),
        "warehouse": warehouse_info,
        "files": _manifest_files(backup_dir),
    }
    (backup_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    _print_summary(backup_dir, manifest)
    return manifest


# ── verification ──────────────────────────────────────────────────────────

def verify_backup(backup_dir: str) -> Dict[str, Any]:
    """Check a backup against its manifest (hashes + presence)."""
    directory = Path(backup_dir).resolve()
    manifest_path = directory / MANIFEST_NAME
    report: Dict[str, Any] = {
        "backup_dir": str(directory),
        "ok": False,
        "checked": 0,
        "missing": [],
        "mismatched": [],
    }
    if not manifest_path.exists():
        report["missing"].append(MANIFEST_NAME)
        return report

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report["mismatched"].append(MANIFEST_NAME)
        return report

    for entry in manifest.get("files", []):
        relative = entry.get("path", "")
        target = directory / relative
        report["checked"] += 1
        if not target.exists():
            report["missing"].append(relative)
            continue
        if target.stat().st_size != entry.get("size"):
            report["mismatched"].append(relative)
            continue
        if _sha256(target) != entry.get("sha256"):
            report["mismatched"].append(relative)

    report["ok"] = not report["missing"] and not report["mismatched"]
    report["manifest_created_at"] = manifest.get("created_at", "")
    return report


# ── restore ───────────────────────────────────────────────────────────────

def _move_aside(path: Path) -> Optional[str]:
    """Move an existing file (and its WAL) out of the way.

    The DuckDB write-ahead log has to move with the database: leaving it
    behind would let the new file replay the old transactions.
    """
    if not path.exists():
        return None
    aside = path.with_name(path.name + f".pre-restore-{_timestamp()}")
    try:
        shutil.move(str(path), str(aside))
    except OSError as exc:
        raise BackupError(
            f"{path.name} is in use by another process -- stop the app before "
            "restoring."
        ) from exc
    for suffix in (".wal", ".tmp"):
        sibling = path.with_name(path.name + suffix)
        if sibling.exists():
            shutil.move(str(sibling), str(aside) + suffix)
    return aside.name


def _import_warehouse(export_dir: Path, target: Path) -> None:
    """Rebuild a warehouse file from an ``EXPORT DATABASE`` directory."""
    import duckdb

    target.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(target))
    try:
        conn.execute(f"IMPORT DATABASE '{export_dir.as_posix()}'")
    finally:
        conn.close()


def _restore_warehouse(directory: Path, root_path: Path) -> Optional[str]:
    """Replace the live warehouse with the exported snapshot."""
    export_dir = directory / "warehouse" / "export"
    warehouse = root_path / "data" / "curated" / "warehouse.duckdb"
    warehouse.parent.mkdir(parents=True, exist_ok=True)
    aside = _move_aside(warehouse)
    _import_warehouse(export_dir, warehouse)
    return aside


def _restore_files(
    directory: Path, root_path: Path, include_secrets: bool
) -> Dict[str, Any]:
    """Restore the identity store, the audit sink, the uploads and secrets."""
    restored: Dict[str, Any] = {}
    for relative in ("data/users.db", "data/audit.db"):
        source = directory / relative
        if not source.exists():
            continue
        _move_aside(root_path / relative)
        _copy_file(source, root_path / relative)
        restored[relative] = True

    raw_source = directory / "data" / "raw"
    for path in _collect_files(raw_source):
        _copy_file(path, root_path / "data" / "raw" / path.relative_to(raw_source))
    restored["data/raw"] = True

    if include_secrets:
        for relative in SECRET_PATHS:
            source = directory / relative
            if source.exists():
                _copy_file(source, root_path / relative)
                restored[relative] = True
    return restored


def restore_backup(
    backup_dir: str,
    root: str,
    confirm: bool = False,
    include_secrets: bool = False,
) -> Dict[str, Any]:
    """Restore a backup over *root*.

    This is destructive -- it replaces the live warehouse and the identity
    store -- so it refuses to run without ``confirm=True`` (the CLI's
    ``--confirm``).
    """
    if not confirm:
        raise ConfirmationRequired(
            "restoring replaces the live warehouse and the identity store. "
            "Re-run with --confirm once the app is stopped."
        )

    directory = Path(backup_dir).resolve()
    root_path = Path(root).resolve()
    manifest_path = directory / MANIFEST_NAME
    if not manifest_path.exists():
        raise BackupError(f"no manifest in {directory}")

    report = verify_backup(str(directory))
    if not report["ok"]:
        raise BackupError(
            "the backup failed verification; refusing to restore. "
            f"missing={report['missing']} mismatched={report['mismatched']}"
        )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("includes_secrets") and not include_secrets:
        print(
            "WARNING: this backup contains secrets; they are NOT restored "
            "unless --include-secrets is given.",
            file=sys.stderr,
        )

    aside = _restore_warehouse(directory, root_path)
    restored = _restore_files(directory, root_path, include_secrets)
    restored["warehouse"] = True

    print(f"Restore complete: {restored}")
    if aside:
        print(f"  previous warehouse kept as {aside}")
    return {"restored": restored, "previous_warehouse": aside}


# ── the CLI ───────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hosted_backup.py",
        description="Backup and restore a hosted DataAgent deployment.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("backup", "verify", "restore"),
        help="what to do",
    )
    parser.add_argument("--root", default=".", help="the installation root")
    parser.add_argument("--out", default="backups", help="where backups are written")
    parser.add_argument("--from", dest="source", help="the backup directory")
    parser.add_argument("--label", default="", help="a label for the backup name")
    parser.add_argument(
        "--include-secrets",
        action="store_true",
        help="copy credential material too (dangerous)",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="confirm a destructive restore",
    )
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    """The command-line entry point; returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.command:
        parser.print_help()
        return 2

    try:
        if args.command == "backup":
            create_backup(
                args.root,
                args.out,
                include_secrets=args.include_secrets,
                label=args.label,
            )
            return 0
        if args.command == "verify":
            if not args.source:
                parser.error("--from is required for verify")
            report = verify_backup(args.source)
            print(json.dumps(report, indent=2))
            return 0 if report["ok"] else 1
        restore_backup(
            args.source or "",
            args.root,
            confirm=args.confirm,
            include_secrets=args.include_secrets,
        )
        return 0
    except ConfirmationRequired as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    except BackupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
