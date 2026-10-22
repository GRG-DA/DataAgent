"""The ONE place the installer size is written down.

The site quoted the download size four different ways (175 MB, 182 MB,
400 MB and 499 MB) across three pages, and a release checklist that asked
an operator to "compare the number stated in website/download.html with
the real installer size" could only ever be satisfied for one of them.

So: one constant, one command, and a test that the pages agree with it.

Usage:
    python scripts/site_installer_size.py            # check the pages
    python scripts/site_installer_size.py --set 500  # rewrite the pages
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SITE = REPO_ROOT / "website"

#: The size of the Windows installer, in megabytes.  Measure it after the
#: final build and set it here; every page quotes this one number.
INSTALLER_SIZE_MB = 500

#: How a page says it.  The number is the only variable part.
_SIZE_RE = re.compile(r"roughly\s*\d+\s*(?:&nbsp;|&#160;|\s)*MB")

#: The rendered form.
SIZE_PHRASE = f"roughly {INSTALLER_SIZE_MB}&nbsp;MB"


def pages() -> list[pathlib.Path]:
    return sorted(SITE.glob("*.html"))


def sizes_in(text: str) -> list[str]:
    return [match.group(0) for match in _SIZE_RE.finditer(text)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", type=int, metavar="MB", help="rewrite the pages with this size")
    args = parser.parse_args()

    if args.set is not None:
        global INSTALLER_SIZE_MB, SIZE_PHRASE
        INSTALLER_SIZE_MB = args.set
        SIZE_PHRASE = f"roughly {INSTALLER_SIZE_MB}&nbsp;MB"
        changed = []
        for page in pages():
            text = page.read_text(encoding="utf-8")
            updated = _SIZE_RE.sub(SIZE_PHRASE, text)
            if updated != text:
                page.write_text(updated, encoding="utf-8")
                changed.append(page.name)
        print(f"set the installer size to {INSTALLER_SIZE_MB} MB on {len(changed)} page(s): {', '.join(changed)}")
        return 0

    quoted: dict[str, list[str]] = {}
    for page in pages():
        found = sizes_in(page.read_text(encoding="utf-8"))
        if found:
            quoted[page.name] = found

    numbers = {re.search(r"\d+", value).group(0) for found in quoted.values() for value in found}
    if len(numbers) > 1:
        print("the site quotes more than one installer size:")
        for name, found in quoted.items():
            for value in found:
                print(f"   {name}: {value}")
        return 1
    if numbers and numbers != {str(INSTALLER_SIZE_MB)}:
        print(f"the site quotes {numbers.pop()} MB; the constant says {INSTALLER_SIZE_MB} MB")
        print(f"run: python scripts/site_installer_size.py --set {INSTALLER_SIZE_MB}")
        return 1
    print(f"every page quotes {INSTALLER_SIZE_MB} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
