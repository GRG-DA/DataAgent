"""P4: compile the license-enforcement surface to native modules (.pyd).

PyInstaller Python bytecode decompiles in minutes (pyinstxtractor + pycdc).
Cythonising the license files forces attackers up to C-level patching —
it does NOT make the build uncrackable, it prices out casual cracking.

This step is OPT-IN (release builds only): dev/test always run from source.

    python scripts/cythonize_license.py check    # prerequisites only
    python scripts/cythonize_license.py build    # compile in place (.pyd next to .py)
    python scripts/cythonize_license.py verify  # assert .pyd shadows .py on import
    python scripts/cythonize_license.py clean   # remove generated .c/.pyd/.so

Release flow (or: scripts\\build_windows.ps1 -Cythonize):
    build → PyInstaller (bundles the .pyd, drops the .py) → clean

Requires: `pip install cython` + a C toolchain
(Windows: Visual Studio Build Tools · Linux: build-essential).
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The license-enforcement surface: every module an attacker patches to
# bypass payment (signature checks, entitlement resolution, gates, sync).
# tests/test_packaging.py asserts this list stays in sync with the code.
LICENSE_MODULES = [
    "keygen_app/keygen.py",
    "keygen_app/config.py",
    "keygen_app/storage.py",
    "api/premium.py",
    "api/billing/license_guard.py",
    "api/billing/entitlement_token.py",
    "api/billing/feature_ticket.py",
    "api/billing/entitlements.py",
    "api/billing/cloud_sync.py",
    "api/billing/redemption.py",
    "api/billing/gate.py",
]

_GENERATED_SUFFIXES = (".c", ".pyd", ".so")


def _missing_sources() -> list[str]:
    return [m for m in LICENSE_MODULES if not (ROOT / m).exists()]


def check() -> int:
    """Prerequisites only. Exit 0 when a build would work, 1 with guidance."""
    ok = True
    missing = _missing_sources()
    if missing:
        print(f"MISSING SOURCES: {missing}")
        ok = False
    try:
        import Cython  # noqa: F401
        print(f"Cython present")
    except ImportError:
        print("MISSING: pip install cython")
        ok = False
    cc = shutil.which("cl") or shutil.which("gcc") or shutil.which("cc")
    if cc:
        print(f"C compiler: {cc}")
    else:
        print("MISSING C compiler (Windows: Visual Studio Build Tools; "
              "Linux: apt install build-essential)")
        ok = False
    print(f"modules: {len(LICENSE_MODULES)}")
    return 0 if ok else 1


def build(language_level: str = "3") -> int:
    """Cythonize LICENSE_MODULES in place. Returns process exit code."""
    if check() != 0:
        return 1
    from Cython.Build import cythonize

    # setuptools in-place extension build via a synthetic Distribution.
    from setuptools.dist import Distribution
    from setuptools.extension import Extension

    exts = [
        Extension(
            name=Path(m).with_suffix("").as_posix().replace("/", "."),
            sources=[str(ROOT / m)],
        )
        for m in LICENSE_MODULES
    ]
    dist = Distribution({"ext_modules": cythonize(
        exts, language_level=language_level, nthreads=0)})
    dist.parse_config_files()
    build_ext = dist.get_command_obj("build_ext")
    build_ext.inplace = 1
    # Silence the build chatter; errors still raise.
    build_ext.ensure_finalized()
    build_ext.run()
    print(f"compiled {len(LICENSE_MODULES)} modules in place")
    return verify()


def verify() -> int:
    """Assert every compiled module shadows its source on import."""
    import importlib

    failures = []
    for m in LICENSE_MODULES:
        mod = Path(m).with_suffix("").as_posix().replace("/", ".")
        for loaded in list(sys.modules):
            if loaded == mod or loaded.startswith(mod + "."):
                del sys.modules[loaded]
        try:
            module = importlib.import_module(mod)
            path = getattr(module, "__file__", "") or ""
            if Path(path).suffix.lower() not in (".pyd", ".so"):
                failures.append(f"{mod} -> {path or 'no __file__'} (source shadowed?)")
            else:
                print(f"OK {mod} -> {Path(path).name}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{mod}: {exc}")
    if failures:
        print("VERIFY FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    return 0


def clean() -> int:
    """Remove generated C / extension artifacts. Always succeeds."""
    import shutil as _shutil

    removed = 0
    for m in LICENSE_MODULES:
        stem = (ROOT / m).with_suffix("").name
        parent = (ROOT / m).with_suffix("").parent
        candidates = [parent / f"{stem}.c"]
        for suffix in (".pyd", ".so"):
            candidates.append(parent / f"{stem}{suffix}")
            candidates.extend(parent.glob(f"{stem}.*{suffix}"))
        for artifact in candidates:
            try:
                if artifact.exists() and artifact.is_file():
                    artifact.unlink()
                    removed += 1
            except OSError:
                pass
    build_dir = ROOT / "build"
    if build_dir.exists():
        try:
            _shutil.rmtree(build_dir)
        except OSError:
            pass
    print(f"cleaned {removed} artifact(s)")
    return 0


def main(argv: list[str]) -> int:
    cmd = (argv[1] if len(argv) > 1 else "check").lower()
    if cmd == "check":
        return check()
    if cmd == "build":
        return build()
    if cmd == "verify":
        return verify()
    if cmd == "clean":
        return clean()
    print(f"unknown command {cmd!r}; want check|build|verify|clean")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
