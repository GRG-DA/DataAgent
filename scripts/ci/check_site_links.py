"""Broken-link checker for the static marketing site.

The marketing site (``website/``) is plain HTML with no build step, so
nothing validates its links before they ship.  A dead ``href`` is invisible
in review and only shows up as a 404 for a visitor.

What it checks
--------------

* **Internal links** — an ``href``/``src`` that points at a file which is
  not in ``website/`` is a broken link and fails the run.
* **Anchors** — ``page.html#id`` where the target page has no element with
  that ``id`` (or ``name``) is a broken anchor and fails the run.
* **External links** — reported for review, **never fetched**.  A checker
  that runs in CI must not make network calls: it would be slow, flaky and
  it would leak the CI runner's IP address to every host the site links to.

Why the HTML is parsed instead of regexed
------------------------------------------

A regex over the raw file matches JavaScript string literals.  The site
contains things like

    issues.push(" + issues + ");

which a naive ``href="..."`` regex happily reports as a link to a file
called ``" + issues + "``.  ``html.parser`` reads *attributes*, so a string
inside a ``<script>`` block is never mistaken for a link.

Usage:
    python scripts/ci/check_site_links.py
    python scripts/ci/check_site_links.py --site website

Exit codes:
    0 - no broken internal link and no broken anchor
    1 - broken internal link(s) and/or broken anchor(s)
    2 - usage or I/O error
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent.parent

#: Schemes that are not a file reference and are therefore not checked.
NON_FILE_SCHEMES = ("mailto:", "tel:", "data:", "javascript:", "sms:", "callto:")

#: Attributes that reference a file (or a fragment of the current page).
LINK_ATTRIBUTES = ("href", "src", "data", "poster", "action")


@dataclass(frozen=True)
class Reference:
    """One ``href``/``src`` found in the markup."""

    line: int
    tag: str
    attribute: str
    value: str


@dataclass(frozen=True)
class Finding:
    """A broken internal link or a broken anchor."""

    page: Path
    line: int
    target: str
    problem: str


class _PageParser(HTMLParser):
    """Collects link references, element ids and headings from one page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[Reference] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._collect(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._collect(tag, attrs)

    def _collect(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        mapping = {key.lower(): (value or "") for key, value in attrs}
        element_id = mapping.get("id", "").strip()
        if element_id:
            self.ids.add(element_id)
        name = mapping.get("name", "").strip()
        if name:
            self.ids.add(name)
        for attribute in LINK_ATTRIBUTES:
            value = mapping.get(attribute, "").strip()
            if value:
                self.references.append(
                    Reference(self.getpos()[0], tag, attribute, value)
                )


def _parse_page(path: Path) -> _PageParser:
    parser = _PageParser()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))
    parser.close()
    return parser


def _is_external(value: str) -> bool:
    """True for a URL that points at another host (or a bare protocol)."""
    if value.startswith("//"):
        return True
    return "://" in value


def _is_non_file(value: str) -> bool:
    return value.lower().startswith(NON_FILE_SCHEMES)


def _split_reference(value: str) -> tuple[str, str]:
    """``"page.html?q=1#frag"`` -> ``("page.html", "frag")``."""
    parts = urlsplit(value)
    path = parts.path
    fragment = parts.fragment
    return path, fragment


def _resolve(site: Path, page: Path, target: str) -> Path | None:
    """The file a relative target points at, or ``None`` when it is not ours."""
    if not target:
        return None
    if target.startswith("/"):
        return site / target.lstrip("/")
    return (page.parent / target).resolve()


def _check_page(
    site: Path, page: Path, parser: _PageParser, ids_by_page: dict[str, set[str]]
) -> tuple[list[Finding], list[tuple[Path, int, str]]]:
    """Return the broken references and the external ones for one page."""
    broken: list[Finding] = []
    external: list[tuple[Path, int, str]] = []
    page_ids = ids_by_page[page.name]
    for reference in parser.references:
        value = reference.value
        if _is_non_file(value):
            continue
        if _is_external(value):
            external.append((page, reference.line, value))
            continue
        target, fragment = _split_reference(value)
        if not target:
            # A same-page anchor: "#section".
            if fragment and fragment not in page_ids:
                broken.append(
                    Finding(page, reference.line, value, f"no element with id {fragment!r}")
                )
            continue
        resolved = _resolve(site, page, target)
        if resolved is None or not resolved.exists():
            broken.append(
                Finding(page, reference.line, value, "file does not exist")
            )
            continue
        if fragment and resolved.suffix == ".html":
            target_ids = ids_by_page.get(resolved.name, set())
            if fragment not in target_ids:
                broken.append(
                    Finding(
                        page,
                        reference.line,
                        value,
                        f"{resolved.name} has no element with id {fragment!r}",
                    )
                )
    return broken, external


def _print_table(rows: list[list[str]], headers: list[str]) -> None:
    """Print an aligned text table."""
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    line = "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers))
    print(line)
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)))


def _relative(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--site",
        type=Path,
        default=ROOT / "website",
        help="the site root to check (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    site: Path = args.site.resolve()
    if not site.is_dir():
        print(f"error: {site} is not a directory", file=sys.stderr)
        return 2

    pages = sorted(site.glob("*.html"))
    if not pages:
        print(f"error: no HTML pages found in {site}", file=sys.stderr)
        return 2

    parsers = {page.name: _parse_page(page) for page in pages}
    ids_by_page = {name: parser.ids for name, parser in parsers.items()}

    all_broken: list[Finding] = []
    all_external: list[tuple[Path, int, str]] = []
    for page in pages:
        broken, external = _check_page(site, page, parsers[page.name], ids_by_page)
        all_broken.extend(broken)
        all_external.extend(external)

    print(f"Checked {len(pages)} page(s) in {_relative(site)}")

    if all_broken:
        print(f"\nBROKEN ({len(all_broken)})")
        _print_table(
            [
                [_relative(f.page), str(f.line), f.target, f.problem]
                for f in all_broken
            ],
            ["file", "line", "target", "problem"],
        )
    else:
        print("\nNo broken internal links and no broken anchors.")

    if all_external:
        unique = sorted({url for _, _, url in all_external})
        print(f"\nEXTERNAL ({len(all_external)} reference(s), {len(unique)} unique) - not fetched")
        _print_table(
            [
                [_relative(page), str(line), url]
                for page, line, url in all_external
            ],
            ["file", "line", "url"],
        )

    if all_broken:
        print(f"\nFAILED: {len(all_broken)} broken link(s)/anchor(s).")
        return 1
    print("\nOK: every internal link and anchor resolves.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
