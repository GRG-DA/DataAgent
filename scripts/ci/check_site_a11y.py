"""Static accessibility checks for the marketing site.

**This is not a substitute for a real accessibility audit.**  It is a
static checker that reads the HTML with ``html.parser`` and reports the
defects a machine can see reliably.  A human still has to check:

* **Colour contrast** of every text/background pair, including the ones
  that only appear in a hover or a focus state.
* **Focus order** and whether the focus ring is visible on every
  interactive element.
* **Screen-reader flow**: whether the reading order makes sense, whether
  the headings describe the sections, whether a form error is announced.
* **Keyboard traps**, skip links, and the mobile menu's keyboard
  behaviour.
* **Text alternatives** for meaningful images - this checker only knows
  whether ``alt`` is *present*, not whether it is *useful*.
* **Zoom and reflow** at 200% and 400%.

What it checks
--------------

* every ``<img>`` has an ``alt`` attribute (decorative images use
  ``alt=""``; a missing ``alt`` is a finding, an empty one is not);
* every ``<input>`` / ``<select>`` / ``<textarea>`` has an associated
  label (``<label for>``, a wrapping ``<label>``, ``aria-label`` or
  ``aria-labelledby``);
* every ``<button>`` and every ``<a>`` has an accessible name (text
  content, ``aria-label`` or ``title``);
* exactly one ``<h1>`` per page and no skipped heading level;
* ``<html lang="…">`` is declared;
* every ``<iframe>`` has a ``title``;
* ``<meta name="viewport">`` is declared.

Usage:
    python scripts/ci/check_site_a11y.py
    python scripts/ci/check_site_a11y.py --site website

Exit codes:
    0 - no findings
    1 - one or more findings
    2 - usage or I/O error
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

#: Form controls that must carry a label.
LABELLED_CONTROLS = ("input", "select", "textarea")

#: ``<input type="…">`` values that are not user-facing and need no label.
UNLABELLED_INPUT_TYPES = ("hidden", "submit", "reset", "button", "image")

#: Attributes that can carry an accessible name on their own.
NAME_ATTRIBUTES = ("aria-label", "title")


@dataclass(frozen=True)
class Finding:
    """One accessibility defect."""

    page: Path
    line: int
    problem: str


@dataclass
class _Control:
    """A form control and what is known about its label."""

    line: int
    attributes: dict[str, str]
    inside_label: bool


class _A11yParser(HTMLParser):
    """Collects the facts the checks need from one page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lang: str | None = None
        self.has_viewport = False
        self.imgs: list[tuple[int, dict[str, str]]] = []
        self.controls: list[_Control] = []
        self.buttons: list[tuple[int, dict[str, str]]] = []
        self.anchors: list[tuple[int, dict[str, str]]] = []
        self.iframes: list[tuple[int, dict[str, str]]] = []
        self.headings: list[tuple[int, int]] = []
        self.label_targets: set[str] = set()
        self.name_text: dict[int, str] = {}
        self._label_depth = 0
        self._in_cdata = False
        self._open_names: list[list] = []

    # -- parsing ---------------------------------------------------------
    def set_cdata_mode(self, elem: str, escapable: bool = False) -> None:
        super().set_cdata_mode(elem)
        self._in_cdata = True

    def clear_cdata_mode(self) -> None:
        super().clear_cdata_mode()
        self._in_cdata = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._collect(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._collect(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "label" and self._label_depth:
            self._label_depth -= 1
        for entry in reversed(self._open_names):
            if entry[0] == tag:
                self._open_names.remove(entry)
                text = "".join(entry[2])
                self.name_text[entry[1]] = self.name_text.get(entry[1], "") + text
                break

    def handle_data(self, data: str) -> None:
        if self._in_cdata:
            return
        for entry in self._open_names:
            entry[2].append(data)

    def close(self) -> None:
        super().close()
        for entry in list(self._open_names):
            self.name_text[entry[1]] = self.name_text.get(entry[1], "") + "".join(entry[2])
        self._open_names.clear()

    def _collect(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        line = self.getpos()[0]
        mapping = {key.lower(): (value or "") for key, value in attrs}
        tag = tag.lower()
        if tag == "html":
            self.lang = (mapping.get("lang") or "").strip() or None
        elif tag == "meta":
            if (mapping.get("name") or "").strip().lower() == "viewport":
                self.has_viewport = True
        elif tag == "img":
            self.imgs.append((line, mapping))
            self._add_to_open_names(mapping.get("alt", ""))
        elif tag in LABELLED_CONTROLS:
            self.controls.append(_Control(line, mapping, self._label_depth > 0))
        elif tag == "button":
            self.buttons.append((line, mapping))
        elif tag == "a":
            self.anchors.append((line, mapping))
        elif tag == "iframe":
            self.iframes.append((line, mapping))
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.headings.append((line, int(tag[1])))
        elif tag == "label":
            target = (mapping.get("for") or "").strip()
            if target:
                self.label_targets.add(target)
            self._label_depth += 1
        if tag in ("a", "button"):
            self._open_names.append([tag, line, []])

    def _add_to_open_names(self, text: str) -> None:
        if not text:
            return
        for entry in self._open_names:
            entry[2].append(text)


def _has_name(attributes: dict[str, str], text: str) -> bool:
    """An accessible name from an attribute or from the element's text."""
    for attribute in NAME_ATTRIBUTES:
        if (attributes.get(attribute) or "").strip():
            return True
    return bool(text.strip())


def _control_needs_label(control: _Control) -> bool:
    if control.attributes.get("type", "").strip().lower() in UNLABELLED_INPUT_TYPES:
        return False
    return True


def _check_controls(page: Path, parser: _A11yParser) -> list[Finding]:
    findings: list[Finding] = []
    for control in parser.controls:
        if not _control_needs_label(control):
            continue
        attributes = control.attributes
        if _has_name(attributes, ""):
            continue
        control_id = (attributes.get("id") or "").strip()
        if control_id and control_id in parser.label_targets:
            continue
        if control.inside_label:
            continue
        findings.append(
            Finding(page, control.line, "form control has no associated label")
        )
    return findings


def _check_headings(page: Path, headings: list[tuple[int, int]]) -> list[Finding]:
    findings: list[Finding] = []
    h1_lines = [line for line, level in headings if level == 1]
    if not h1_lines:
        findings.append(Finding(page, 1, "the page has no <h1>"))
    elif len(h1_lines) > 1:
        findings.append(
            Finding(
                page,
                h1_lines[1],
                f"the page has {len(h1_lines)} <h1> elements (expected 1)",
            )
        )
    previous = 0
    for line, level in headings:
        if level > previous + 1:
            findings.append(
                Finding(page, line, f"heading level skipped: h{previous} -> h{level}")
            )
        previous = level
    return findings


def _check_page(page: Path, parser: _A11yParser) -> list[Finding]:
    findings: list[Finding] = []

    if not parser.lang:
        findings.append(Finding(page, 1, "<html> has no lang attribute"))

    if not parser.has_viewport:
        findings.append(Finding(page, 1, 'no <meta name="viewport"> declared'))

    for line, attributes in parser.imgs:
        if "alt" not in attributes:
            findings.append(
                Finding(
                    page,
                    line,
                    '<img> has no alt attribute (use alt="" if it is decorative)',
                )
            )

    findings.extend(_check_controls(page, parser))

    for line, attributes in parser.buttons:
        if not _has_name(attributes, parser.name_text.get(line, "")):
            findings.append(Finding(page, line, "<button> has no accessible name"))

    for line, attributes in parser.anchors:
        if not _has_name(attributes, parser.name_text.get(line, "")):
            findings.append(Finding(page, line, "<a> has no accessible name"))

    for line, attributes in parser.iframes:
        if not (attributes.get("title") or "").strip():
            findings.append(Finding(page, line, "<iframe> has no title attribute"))

    findings.extend(_check_headings(page, parser.headings))
    return findings


def _parse_page(path: Path) -> _A11yParser:
    parser = _A11yParser()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))
    parser.close()
    return parser


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

    findings: list[Finding] = []
    for page in pages:
        findings.extend(_check_page(page, _parse_page(page)))

    print(f"Checked {len(pages)} page(s) in {_relative(site)}")
    if not findings:
        print("\nNo accessibility findings from the static checks.")
        return 0

    print(f"\nFINDINGS ({len(findings)})")
    width = max(len(_relative(f.page)) for f in findings)
    for finding in findings:
        print(
            f"{_relative(finding.page).ljust(width)}  "
            f"line {str(finding.line).rjust(4)}  {finding.problem}"
        )
    print(
        "\nA static check is not an accessibility audit: colour contrast, "
        "focus order and the screen-reader flow still need a human."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
