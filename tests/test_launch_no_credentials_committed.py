"""Nothing that carries a credential may ever be tracked by git.

The project already gitignores the runtime credential state, but a
**gitignore rule is not a guarantee**: ``git add -f`` ignores it, an
archive of the working tree re-packages it, and a ``git add -A`` in the
wrong moment commits it.

A 1.9 GB ``DataAgent-public.rar`` of the working tree was found during the
pre-launch review.  It contained ``config/users.json`` (23 accounts,
password hashes and PLAINTEXT API keys), ``data/.jwt_secret`` (the session
signing key), ``data/.grant_key`` (the plan-grant signing key),
``data/curated/warehouse.duckdb`` and ``.env`` - every file the
never-commit list exists to exclude.  It was deleted.

These tests make the guarantee structural rather than procedural: they
read what git is actually tracking, not what it is supposed to track.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Runtime state that must never be committed.
CREDENTIAL_STATE = {
    ".env",
    ".env.local",
    ".env.production",
    "config/users.json",
    "config/db_connections.json",
    "config/google_login_pending.json",
    "config/llm_runtime.json",
    "config/revoked_jtis.json",
    "data/.jwt_secret",
    "data/.grant_key",
    "data/.grant_migrated",
    "data/users.db",
    "data/audit.db",
    "data/curated/warehouse.duckdb",
    "data/curated/warehouse.duckdb.wal",
    "data/app.duckdb",
}

#: An archive of the project is never a source artefact.
ARCHIVE_SUFFIXES = (".rar", ".7z", ".zip", ".tar", ".tar.gz", ".tgz")

#: A file this large in the repo root is a build/packaging accident.
MAX_SOURCE_FILE_BYTES = 25 * 1024 * 1024


def _git(*args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=str(ROOT), capture_output=True, text=True, timeout=60
    )
    return out.stdout


def _tracked_files() -> list:
    return [line for line in _git("ls-files").split("\n") if line.strip()]


def test_no_credential_state_is_tracked():
    tracked = set(_tracked_files())
    offenders = sorted(tracked & CREDENTIAL_STATE)
    assert not offenders, (
        "git is tracking runtime credential state:\n  "
        + "\n  ".join(offenders)
        + "\nRemove it with: git rm --cached <path>  (and rotate the secret)"
    )


def test_no_credential_state_is_staged_for_commit():
    """The INDEX, not just HEAD - this catches a pending `git add`."""
    staged = [line for line in _git("diff", "--cached", "--name-only").split("\n")
              if line.strip()]
    offenders = sorted(set(staged) & CREDENTIAL_STATE)
    assert not offenders, (
        "credential state is staged for commit:\n  " + "\n  ".join(offenders)
    )


def test_no_archive_of_the_project_is_tracked():
    tracked = _tracked_files()
    offenders = [
        name for name in tracked
        if name.lower().endswith(ARCHIVE_SUFFIXES)
        or name.lower().startswith("dataagent-public.")
    ]
    assert not offenders, (
        "an archive of the project is tracked - it re-packages the credential "
        "state the gitignore excludes:\n  " + "\n  ".join(offenders)
    )


def test_no_large_file_is_tracked():
    """A source repository has no business carrying a 25 MB+ file."""
    big = []
    for name in _tracked_files():
        path = ROOT / name
        try:
            if path.is_file() and path.stat().st_size > MAX_SOURCE_FILE_BYTES:
                big.append(f"{name} ({path.stat().st_size // (1024 * 1024)} MB)")
        except OSError:
            continue
    assert not big, "oversized files are tracked:\n  " + "\n  ".join(big)


def test_the_gitignore_covers_the_credential_state():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for name in (".env", "config/users.json", "data/.jwt_secret", "data/.grant_key"):
        assert name.split("/")[-1] in ignore, (
            f".gitignore does not mention {name}"
        )


def test_the_gitignore_blocks_project_archives():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "*.rar" in ignore, (
        ".gitignore must block project archives - an archive of the working "
        "tree re-packages every credential the ignore rules exclude"
    )
