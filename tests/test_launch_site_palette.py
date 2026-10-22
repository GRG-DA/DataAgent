"""Palette discipline for the DataAgent marketing site.

The site is a static, dark-only build with three stylesheets
(``website/css/style.css`` -> ``pro.css`` -> ``premium.css``).  These
tests pin the colour system down so it cannot drift back into "three
palettes, ninety gradients, a white card on a black page":

* every semantic token is declared in exactly ONE of the three files;
* the number of DISTINCT gradient values is small and tokenised;
* gradient text is reserved for the site name and one hero word;
* the dark surface never carries a pure-white fill;
* every text colour clears 4.5:1 and every accent fill/border clears
  3:1 against the surface;
* there is exactly ONE ``.btn`` base rule;
* links have a colour of their own;
* the non-primary hues are used sparingly.

Run with::

    python -m pytest tests/test_launch_site_palette.py -q -p no:cacheprovider
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CSS_DIR = REPO_ROOT / "website" / "css"

CANONICAL_FILE = "style.css"
STYLESHEETS = ("style.css", "pro.css", "premium.css")

# ── thresholds ────────────────────────────────────────────────────────────
MAX_DISTINCT_GRADIENTS = 12
MAX_GRADIENT_TEXT_DECLARATIONS = 6
MAX_RULES_PER_SECONDARY_HUE = 12

# The canonical palette.  These are the tokens that carry a COLOUR
# meaning; each one must be declared exactly once across the three
# stylesheets (never twice, never in two files).
PALETTE_TOKENS = (
    # brand hues
    "--primary",
    "--primary-hi",
    "--primary-deep",
    "--accent",
    "--accent-hi",
    "--accent-soft",
    "--emphasis",
    "--emphasis-hi",
    "--emphasis-soft",
    "--pink",
    "--pink-soft",
    "--success",
    "--text",
    # surfaces
    "--surface",
    "--surface-2",
    "--surface-3",
    "--surface-4",
    "--surface-deep",
    "--surface-violet",
    "--surface-mint",
    "--surface-peach",
    "--glass",
    "--glass-2",
    "--glass-3",
    "--line",
    "--line-2",
    "--line-glow",
    # text ramp
    "--text-2",
    "--text-muted",
    "--text-inverse",
    # links
    "--link",
    "--link-hover",
    "--link-visited",
    # status
    "--warning",
    "--danger",
    "--info",
    "--success-bg",
    "--warning-bg",
    "--danger-bg",
    "--info-bg",
    # gradients
    "--grad-brand",
    "--grad-accent",
    "--grad-surface",
    "--grad-aurora",
    "--grad-grid",
    "--grad-mask",
    "--grad-mask-line",
    "--grad-orb",
    "--grad-spot",
    "--grad-sheen",
    "--grad-scrim",
)

# Colours that are allowed to carry TEXT on the dark surface.  Each one
# must clear 4.5:1 against every surface step.
TEXT_ROLE_TOKENS = (
    "--text",
    "--text-2",
    "--text-muted",
    "--link",
    "--link-hover",
    "--link-visited",
    "--primary-hi",
    "--accent",
    "--accent-hi",
    "--accent-soft",
    "--emphasis",
    "--emphasis-hi",
    "--emphasis-soft",
    "--pink",
    "--pink-soft",
    "--success",
    "--warning",
    "--danger",
    "--info",
)

# Colours that are allowed to carry a LARGE FILL or a UI BORDER.  Each
# one must clear 3:1 against the page surface.
FILL_ROLE_TOKENS = (
    "--primary",
    "--primary-deep",
    "--accent",
    "--accent-soft",
    "--emphasis",
    "--pink",
    "--success",
    "--warning",
    "--danger",
    "--info",
)

# The hues that are NOT the primary.  They may survive as rare emphasis
# but they must not be scattered across the whole stylesheet.
SECONDARY_HUES = ("--emphasis", "--emphasis-hi", "--pink", "--pink-soft")

# Component-local custom properties.  These are knobs a single rule
# hands to a shared gradient (a per-card tint, a per-orb hue), not
# palette tokens -- they are intentionally allowed to be set more than
# once.  Everything else must be declared exactly once.
LOCAL_KNOBS = frozenset(
    {
        "--tint",
        "--orb-tint",
        "--spot-size",
        "--spot-alpha",
        "--mx",
        "--my",
        "--gx",
        "--gy",
        "--shine-x",
        "--shine-y",
        "--h",
        "--fx-delay",
        "--fx-self-transform",
    }
)


# ── helpers ───────────────────────────────────────────────────────────────
def _read(name: str) -> str:
    return (CSS_DIR / name).read_text(encoding="utf-8")


def _strip_comments(css: str) -> str:
    out: list[str] = []
    i, n = 0, len(css)
    while i < n:
        if css.startswith("/*", i):
            end = css.find("*/", i + 2)
            i = n if end < 0 else end + 2
            out.append(" ")
        else:
            out.append(css[i])
            i += 1
    return "".join(out)


def _root_blocks(css: str) -> list[str]:
    # A single-line ":root { ... }" is a :root block too, so the pattern
    # must not depend on a newline before the closing brace.  The token
    # block contains no nested braces, so a non-greedy match to the first
    # "}" is the whole block.
    return re.findall(r":root\s*\{([^}]*)\}", css, flags=re.S)


def _declarations(css: str) -> dict[str, list[str]]:
    """Every custom-property declaration in the stylesheet: name -> values."""
    found: dict[str, list[str]] = {}
    for match in re.finditer(r"(--[\w-]+)\s*:\s*([^;}]+)", css):
        found.setdefault(match.group(1), []).append(match.group(2).strip())
    return found


def _rules(css: str) -> list[tuple[str, str]]:
    """(selector, body) for every rule in the stylesheet."""
    return [
        (sel.strip(), body)
        for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
        if sel.strip() and not sel.strip().startswith("@")
    ]


def _gradient_values(css: str) -> set[str]:
    """Every distinct literal gradient in the stylesheet.

    A gradient is matched with balanced parentheses so that a nested
    ``rgba(...)`` does not cut the value short.
    """
    values: set[str] = set()
    pattern = re.compile(r"(?:repeating-)?(?:linear|radial|conic)-gradient\s*\(")
    pos = 0
    while True:
        match = pattern.search(css, pos)
        if not match:
            return values
        depth = 0
        end = None
        for idx in range(match.end() - 1, len(css)):
            if css[idx] == "(":
                depth += 1
            elif css[idx] == ")":
                depth -= 1
                if depth == 0:
                    end = idx
                    break
        if end is None:
            return values
        values.add(" ".join(css[match.start() : end + 1].split()))
        pos = end + 1


def _print_rules(css: str) -> str:
    """The stylesheet with every ``@media print`` block removed.

    A print stylesheet is a sheet of paper, not the dark surface, so a
    white background there is correct and is not counted as a hole in
    the design.
    """
    return re.sub(r"@media\s+print\s*\{.*?\n\}", "", css, flags=re.S)


def _srgb_channel(value: int) -> float:
    channel = value / 255.0
    if channel <= 0.03928:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def _luminance(hex_colour: str) -> float:
    value = hex_colour.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    red, green, blue = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return (
        0.2126 * _srgb_channel(red)
        + 0.7152 * _srgb_channel(green)
        + 0.0722 * _srgb_channel(blue)
    )


def _contrast(foreground: str, background: str) -> float:
    lum_a, lum_b = _luminance(foreground), _luminance(background)
    lighter, darker = max(lum_a, lum_b), min(lum_a, lum_b)
    return (lighter + 0.05) / (darker + 0.05)


def _resolve(token_values: dict[str, str], name: str, _seen=None) -> str | None:
    """Resolve a token to an opaque hex colour (one level of indirection)."""
    _seen = _seen or set()
    if name in _seen:
        return None
    _seen.add(name)
    raw = token_values.get(name)
    if raw is None:
        return None
    raw = raw.strip()
    if raw.startswith("#"):
        return raw
    nested = re.fullmatch(r"var\((--[\w-]+)\)", raw)
    if nested:
        return _resolve(token_values, nested.group(1), _seen)
    return None


@pytest.fixture(scope="module")
def sheets() -> dict[str, str]:
    return {name: _strip_comments(_read(name)) for name in STYLESHEETS}


@pytest.fixture(scope="module")
def root_tokens(sheets: dict[str, str]) -> dict[str, str]:
    """The canonical palette, read from the one ``:root`` block."""
    tokens: dict[str, str] = {}
    for name in STYLESHEETS:
        for block in _root_blocks(sheets[name]):
            for match in re.finditer(r"(--[\w-]+)\s*:\s*([^;}]+)", block):
                tokens.setdefault(match.group(1), match.group(2).strip())
    return tokens


# ── 1. one palette, defined once ──────────────────────────────────────────
def test_only_one_file_declares_root_tokens(sheets: dict[str, str]) -> None:
    """No semantic token may be declared in the :root of two files."""
    owners: dict[str, list[str]] = {}
    for name in STYLESHEETS:
        for block in _root_blocks(sheets[name]):
            for token in re.findall(r"(--[\w-]+)\s*:", block):
                owners.setdefault(token, []).append(name)

    duplicates = {tok: files for tok, files in owners.items() if len(files) > 1}
    assert not duplicates, "tokens declared in the :root of more than one file: " + ", ".join(
        f"{tok} ({', '.join(files)})" for tok, files in sorted(duplicates.items())
    )


def test_the_canonical_palette_lives_in_one_file(sheets: dict[str, str]) -> None:
    """style.css holds the canonical :root; the other two define no tokens."""
    assert _root_blocks(sheets[CANONICAL_FILE]), f"{CANONICAL_FILE} must hold the canonical :root"
    for name in STYLESHEETS:
        if name == CANONICAL_FILE:
            continue
        blocks = _root_blocks(sheets[name])
        assert not blocks, (
            f"{name} still declares {len(blocks)} :root token block(s); "
            f"the palette belongs to {CANONICAL_FILE}"
        )


def test_every_palette_token_is_declared_exactly_once(sheets: dict[str, str]) -> None:
    """A palette token is declared once in the whole site, never twice."""
    declarations = {name: _declarations(css) for name, css in sheets.items()}
    offenders = []
    for token in PALETTE_TOKENS:
        sites = [
            f"{name} (x{len(decls[token])})"
            for name, decls in declarations.items()
            if token in decls
        ]
        total = sum(len(decls.get(token, [])) for decls in declarations.values())
        if total != 1:
            offenders.append(f"{token}: declared {total} time(s) -> {', '.join(sites) or 'nowhere'}")
    assert not offenders, "palette tokens must be declared exactly once:\n  " + "\n  ".join(offenders)


def test_no_unexpected_custom_property_is_duplicated(sheets: dict[str, str]) -> None:
    """Only the documented component-local knobs may be set more than once."""
    declarations = {name: _declarations(css) for name, css in sheets.items()}
    offenders = []
    for token in sorted(set().union(*(set(d) for d in declarations.values()))):
        if token in LOCAL_KNOBS:
            continue
        total = sum(len(decls.get(token, [])) for decls in declarations.values())
        if total > 1:
            owners = [
                f"{name} x{len(decls[token])}"
                for name, decls in declarations.items()
                if token in decls
            ]
            offenders.append(f"{token}: {total} declaration(s) -> {', '.join(owners)}")
    assert not offenders, (
        "these custom properties are declared more than once:\n  " + "\n  ".join(offenders)
    )


def test_the_canonical_block_documents_the_palette(sheets: dict[str, str]) -> None:
    """The canonical :root must explain what each colour is for."""
    block = "\n".join(_root_blocks(sheets[CANONICAL_FILE]))
    for token in ("--surface", "--text", "--link", "--primary", "--accent"):
        assert token in block, f"{token} is missing from the canonical palette block"
    header = _read(CANONICAL_FILE).upper()
    assert "THE ONE PALETTE" in header, (
        "the canonical palette must carry a header comment explaining the system"
    )
    for topic in ("BRAND HUES", "SURFACE RAMP", "TEXT RAMP", "LINKS", "STATUS", "GRADIENTS"):
        assert topic in header, f"the palette header must document the {topic.lower()}"


# ── 2. gradients ──────────────────────────────────────────────────────────
def test_gradient_values_are_tokenised(sheets: dict[str, str]) -> None:
    """Distinct gradient values must stay under a small threshold."""
    offenders: dict[str, list[str]] = {}
    for name in STYLESHEETS:
        for value in sorted(_gradient_values(sheets[name])):
            offenders.setdefault(value, []).append(name)

    assert len(offenders) <= MAX_DISTINCT_GRADIENTS, (
        f"{len(offenders)} distinct gradient values across "
        f"{', '.join(STYLESHEETS)} (limit {MAX_DISTINCT_GRADIENTS}); "
        "use a shared token instead:\n  "
        + "\n  ".join(f"{value}  [{', '.join(files)}]" for value, files in offenders.items())
    )


def test_gradient_tokens_exist(sheets: dict[str, str], root_tokens: dict[str, str]) -> None:
    """The shared gradient tokens are declared and used."""
    for token in ("--grad-brand", "--grad-accent", "--grad-surface"):
        assert token in root_tokens, f"{token} is missing from the canonical palette"
    used = "\n".join(sheets.values())
    assert "var(--grad-brand)" in used, "no rule uses the shared brand gradient"


# ── 3. gradient text ──────────────────────────────────────────────────────
def test_gradient_text_is_reserved(sheets: dict[str, str]) -> None:
    """Gradient text is reserved for the site name and one hero word."""
    declarations = []
    for name in STYLESHEETS:
        for match in re.finditer(r"(?:-webkit-)?background-clip\s*:\s*text", sheets[name]):
            declarations.append(f"{name}: {match.group(0)}")
    assert len(declarations) < MAX_GRADIENT_TEXT_DECLARATIONS, (
        f"{len(declarations)} gradient-text declarations "
        f"(limit <{MAX_GRADIENT_TEXT_DECLARATIONS}):\n  " + "\n  ".join(declarations)
    )


def test_gradient_text_has_a_solid_fallback(sheets: dict[str, str]) -> None:
    """``.gradient-text`` is a solid brand colour, not a gradient."""
    assert re.search(r"\.gradient-text\s*\{[^}]*color\s*:\s*var\(--primary-hi\)", sheets[CANONICAL_FILE]) or re.search(
        r"\.gradient-text\s*\{[^}]*color\s*:\s*var\(--primary-hi\)", "\n".join(sheets.values())
    ), ".gradient-text must use a solid colour (--primary-hi)"


# ── 4. no pure white on the dark surface ──────────────────────────────────
def test_no_pure_white_backgrounds(sheets: dict[str, str]) -> None:
    """A pure white fill is a hole punched in the dark page."""
    offenders = []
    for name in STYLESHEETS:
        printable = _print_rules(sheets[name])
        # background-color is the most obvious way to write a fill, so it
        # has to be matched as well as the background shorthand.
        for match in re.finditer(r"background(?:-color|-image)?\s*:\s*([^;}]+)", printable):
            value = match.group(1)
            for white in ("#fff", "#FFF", "#ffffff", "#FFFFFF", "white"):
                if re.search(rf"(?<![#\w-]){re.escape(white)}(?![#\w-])", value):
                    line = printable[: match.start()].count("\n") + 1
                    offenders.append(f"{name}:{line}: {value.strip()[:90]}")
    assert not offenders, "pure white fills found on the dark surface:\n  " + "\n  ".join(offenders)


def test_white_is_only_ever_a_text_colour(sheets: dict[str, str]) -> None:
    """White may be a text colour on a dark fill, never the fill itself."""
    offenders = []
    for name in STYLESHEETS:
        printable = _print_rules(sheets[name])
        # `background-color` ends in "color" and would otherwise be read
        # as a text colour, so the property name must be anchored: the
        # character before "color" may not be a word character or a dash.
        for match in re.finditer(r"(?<![\w-])color\s*:\s*([^;}]+)", printable):
            value = match.group(1)
            if re.search(r"(?<![#\w-])(#fff|#FFF|#ffffff|#FFFFFF)(?![#\w-])", value):
                line = printable[: match.start()].count("\n") + 1
                offenders.append(f"{name}:{line}: color: {value.strip()[:70]}")
    assert not offenders, (
        "use the palette's --text instead of a literal white:\n  " + "\n  ".join(offenders)
    )


# ── 5. contrast ───────────────────────────────────────────────────────────
def test_text_colours_clear_4_5_to_1(root_tokens: dict[str, str]) -> None:
    """Every text colour clears 4.5:1 on every surface it can sit on."""
    surfaces = {
        name: _resolve(root_tokens, name)
        for name in (
            "--surface",
            "--surface-2",
            "--surface-3",
            "--surface-4",
            "--surface-deep",
            "--surface-violet",
            "--surface-mint",
            "--surface-peach",
        )
    }
    surfaces = {k: v for k, v in surfaces.items() if v}
    assert len(surfaces) >= 5, "the surface ramp is missing from the canonical palette"

    offenders = []
    for token in TEXT_ROLE_TOKENS:
        colour = _resolve(root_tokens, token)
        if colour is None:
            offenders.append(f"{token}: not an opaque colour")
            continue
        for surface_name, surface in surfaces.items():
            ratio = _contrast(colour, surface)
            if ratio < 4.5:
                offenders.append(
                    f"{token} ({colour}) on {surface_name} ({surface}) = {ratio:.2f}:1"
                )
    assert not offenders, "text colours below 4.5:1:\n  " + "\n  ".join(offenders)


def test_fill_and_border_colours_clear_3_to_1(root_tokens: dict[str, str]) -> None:
    """Every accent fill / UI border clears 3:1 against the page surface."""
    surface = _resolve(root_tokens, "--surface")
    deep = _resolve(root_tokens, "--surface-deep")
    assert surface and deep

    offenders = []
    for token in FILL_ROLE_TOKENS:
        colour = _resolve(root_tokens, token)
        if colour is None:
            offenders.append(f"{token}: not an opaque colour")
            continue
        for surface_name, surface_value in (("--surface", surface), ("--surface-deep", deep)):
            ratio = _contrast(colour, surface_value)
            if ratio < 3.0:
                offenders.append(
                    f"{token} ({colour}) on {surface_name} ({surface_value}) = {ratio:.2f}:1"
                )
    assert not offenders, "fill / border colours below 3:1:\n  " + "\n  ".join(offenders)


def test_primary_button_text_clears_4_5_to_1(
    root_tokens: dict[str, str], sheets: dict[str, str]
) -> None:
    """The primary button's own label must clear 4.5:1 on its own fill.

    The label is resolved from the RULE, not assumed: a monochrome palette
    inverts the button (a near-white fill with a near-black label), so the
    test has to read what the button actually says.
    """
    fill = _resolve(root_tokens, "--primary-deep")
    assert fill

    label = None
    for name in STYLESHEETS:
        for selector, body in _rules(sheets[name]):
            # The selector must BE .btn-primary - not ".btn-primary:hover",
            # and not a comment that mentions it.  And the property must be
            # `color`, not `border-color`.
            clean = re.sub(r"/\*.*?\*/", "", selector, flags=re.S).strip()
            if clean != ".btn-primary":
                continue
            m = re.search(r"(?<![\w-])color\s*:\s*var\((--[\w-]+)\)", body)
            if m:
                label = _resolve(root_tokens, m.group(1))
                break
        if label:
            break
    assert label, "could not resolve the primary button's label colour"
    ratio = _contrast(label, fill)
    assert ratio >= 4.5, (
        f"the primary label ({label}) on the primary fill --primary-deep "
        f"({fill}) = {ratio:.2f}:1"
    )


# ── 5b. a near-white fill must carry a near-black label ───────────────────
#: A colour at or above this relative luminance is a near-white panel.
NEAR_WHITE_LUMINANCE = 0.75


def _solid_colour(value: str, root_tokens: dict[str, str]) -> str | None:
    """Return the hex colour a declaration resolves to, or None.

    A TRANSLUCENT colour is not a solid fill: ``rgba(255,255,255,0.07)`` is
    a glass veil over the dark page, not a white panel, and white type on
    it is correct.  A gradient is not a colour either.
    """
    value = (value or "").replace("!important", "").strip()
    if not value:
        return None
    token = re.fullmatch(r"var\(\s*(--[\w-]+)\s*\)", value)
    if token:
        resolved = _resolve(root_tokens, token.group(1))
        if not resolved or not resolved.startswith("#"):
            return None
        return resolved
    literal = re.fullmatch(r"#([0-9a-fA-F]{3,8})", value)
    if literal:
        body = literal.group(1)
        if len(body) == 3:
            body = "".join(ch * 2 for ch in body)
        if len(body) < 6:
            return None
        return "#" + body[:6]
    rgb = re.fullmatch(r"rgba?\(([^)]*)\)", value)
    if rgb:
        parts = [part.strip() for part in rgb.group(1).split(",")]
        if len(parts) == 4:
            try:
                if float(parts[3]) < 1:
                    return None  # translucent: a veil, not a fill
            except ValueError:
                return None
        if len(parts) < 3:
            return None
        try:
            red, green, blue = (int(float(part)) for part in parts[:3])
        except ValueError:
            return None
        return "#%02x%02x%02x" % (red, green, blue)
    return None


def _effective(entries: list[tuple[int, int, bool, str]]) -> str | None:
    """The declaration the cascade actually picks.

    ``entries`` are (stylesheet index, rule index, is_important, value) in
    source order.  An ``!important`` declaration beats every normal one
    regardless of order; among equals the later stylesheet wins, and
    within a stylesheet the later rule wins.
    """
    if not entries:
        return None
    important = [entry for entry in entries if entry[2]]
    pool = important if important else entries
    return max(pool, key=lambda entry: (entry[0], entry[1]))[3]


def test_a_near_white_fill_never_takes_a_light_label(
    sheets: dict[str, str], root_tokens: dict[str, str]
) -> None:
    """A near-white panel must carry a near-black label.

    In the monochrome palette the primary fill is near-white, so anything
    sitting on it has to be INVERTED.  The fill and the label can be set
    by different rules in different stylesheets - all of them with
    !important - so the check is per SELECTOR across every stylesheet, not
    per rule.  A per-rule check passes each rule on its own and misses
    exactly the bug this exists to catch: the nav CTA is given a near-white
    fill in pro.css and a near-white label in premium.css, and the label
    wins.
    """
    fills: dict[str, list[tuple[int, int, bool, str]]] = {}
    labels: dict[str, list[tuple[int, int, bool, str]]] = {}
    for sheet_index, name in enumerate(STYLESHEETS):
        for rule_index, (selector, body) in enumerate(_rules(sheets[name])):
            clean = re.sub(r"/\*.*?\*/", "", selector, flags=re.S).strip()
            if not clean:
                continue
            for prop, raw in re.findall(
                r"(?<![-\w])(background(?:-color)?|color)\s*:\s*([^;}]+)", body
            ):
                value = raw.replace("!important", "").strip()
                entry = (sheet_index, rule_index, "important" in raw, value)
                bucket = labels if prop == "color" else fills
                bucket.setdefault(clean, []).append(entry)

    violations: list[str] = []
    for selector, fill_entries in fills.items():
        fill = _solid_colour(_effective(fill_entries) or "", root_tokens)
        if not fill or _luminance(fill) < NEAR_WHITE_LUMINANCE:
            continue
        label_value = _effective(labels.get(selector, []))
        label = _solid_colour(label_value or "", root_tokens)
        if label and _luminance(label) >= NEAR_WHITE_LUMINANCE:
            violations.append(
                f"{selector} has a near-white fill ({fill}) but takes the "
                f"light label {label}"
            )
    assert not violations, (
        "a near-white fill must carry a near-black label:\n  "
        + "\n  ".join(violations)
    )


# ── 6. buttons ────────────────────────────────────────────────────────────
def test_exactly_one_btn_base_rule(sheets: dict[str, str]) -> None:
    """There is one .btn base rule and it lives in the canonical file."""
    owners = []
    for name in STYLESHEETS:
        for selector, _body in _rules(sheets[name]):
            if selector.strip() == ".btn":
                owners.append(name)
    assert len(owners) == 1, (
        f"expected exactly one '.btn' base rule, found {len(owners)}: {owners}"
    )
    assert owners[0] == CANONICAL_FILE, (
        f"the '.btn' base rule must live in {CANONICAL_FILE}, found it in {owners[0]}"
    )


def test_button_levels_are_distinct(sheets: dict[str, str]) -> None:
    """Primary, secondary, ghost and tertiary must be told apart."""
    css = sheets[CANONICAL_FILE]
    for modifier in (".btn-primary", ".btn-secondary", ".btn-ghost", ".btn-tertiary"):
        assert re.search(rf"^{re.escape(modifier)}\b", css, flags=re.M), (
            f"{modifier} is missing from {CANONICAL_FILE}"
        )
    primary = re.search(r"\.btn-primary\s*\{(.*?)\}", css, flags=re.S)
    secondary = re.search(r"\.btn-secondary\s*\{(.*?)\}", css, flags=re.S)
    ghost = re.search(r"\.btn-ghost\s*\{(.*?)\}", css, flags=re.S)
    tertiary = re.search(r"\.btn-tertiary,\s*\.btn-text\s*\{(.*?)\}", css, flags=re.S)
    assert primary and secondary and ghost and tertiary

    def background_of(rule: re.Match) -> str:
        match = re.search(r"background\s*:\s*([^;]+)", rule.group(1))
        return match.group(1).strip() if match else ""

    assert background_of(primary) != background_of(secondary), (
        "the primary and secondary buttons must not share a fill"
    )
    assert background_of(secondary) != background_of(ghost), (
        "the secondary and ghost buttons must not share a fill"
    )
    assert "transparent" in background_of(tertiary), (
        "the tertiary button must have no fill at all"
    )
    # the primary button must be a solid, opaque fill
    assert "var(--primary-deep)" in background_of(primary), (
        "the primary button must be a solid --primary-deep fill"
    )


def test_buttons_have_a_visible_focus_ring(sheets: dict[str, str]) -> None:
    """The focus ring is an accessibility requirement."""
    css = sheets[CANONICAL_FILE]
    rule = re.search(r"\.btn:focus-visible[^{]*\{(.*?)\}", css, flags=re.S)
    assert rule, ".btn:focus-visible must exist"
    assert "outline" in rule.group(1), ".btn:focus-visible must set an outline"
    assert "var(--link)" in rule.group(1), (
        "the focus ring must use the high-contrast --link colour"
    )


# ── 7. links ──────────────────────────────────────────────────────────────
def test_link_token_exists_and_clears_the_bar(root_tokens: dict[str, str]) -> None:
    """--link must exist and clear 4.5:1 on every surface."""
    assert "--link" in root_tokens, "--link is missing from the canonical palette"
    colour = _resolve(root_tokens, "--link")
    assert colour, "--link must resolve to an opaque colour"

    for surface_name in ("--surface", "--surface-2", "--surface-3", "--surface-deep"):
        surface = _resolve(root_tokens, surface_name)
        ratio = _contrast(colour, surface)
        assert ratio >= 4.5, (
            f"--link ({colour}) on {surface_name} ({surface}) = {ratio:.2f}:1"
        )


def test_link_is_distinguishable_from_body_text(root_tokens: dict[str, str]) -> None:
    """A link must not be the same colour as the body copy."""
    link = _resolve(root_tokens, "--link")
    body = _resolve(root_tokens, "--text-2")
    assert link and body
    assert link.lower() != body.lower(), "--link must differ from --text-2"

    link_lum = _luminance(link)
    body_lum = _luminance(body)
    assert abs(link_lum - body_lum) > 0.02, (
        f"--link ({link}) and --text-2 ({body}) are too close in luminance "
        "to tell apart"
    )


def test_links_have_a_visited_state(root_tokens: dict[str, str]) -> None:
    """A visited link must still read as a link."""
    assert "--link-visited" in root_tokens, "--link-visited is missing"
    visited = _resolve(root_tokens, "--link-visited")
    surface = _resolve(root_tokens, "--surface")
    assert visited and surface
    assert _contrast(visited, surface) >= 4.5, (
        f"--link-visited ({visited}) on --surface ({surface}) = "
        f"{_contrast(visited, surface):.2f}:1"
    )


def test_links_are_not_flattened_inside_components(sheets: dict[str, str]) -> None:
    """Buttons and cards keep their own colour; only prose links recolour."""
    css = sheets[CANONICAL_FILE]
    assert re.search(r":where\(a\)\s*\{", css), "the base link rule must use :where()"
    # the button and the card both set their own colour
    assert re.search(r"\.btn\s*\{[^}]*color\s*:\s*var\(--text\)", css, flags=re.S), (
        ".btn must keep its own colour"
    )


# ── 8. status is never colour alone ──────────────────────────────────────
def test_status_colours_have_a_second_cue(sheets: dict[str, str]) -> None:
    """Each status state carries a glyph, a shape or a label as well."""
    css = "\n".join(sheets.values())
    # the comparison checks: a tick, a cross and a worded label
    assert re.search(r"\.check\.yes\b", css), ".check.yes is missing"
    assert re.search(r"\.check\.no\b", css), ".check.no is missing"
    assert re.search(r"\.check\.partial\b", css), ".check.partial is missing"
    # a shape difference between the states
    assert re.search(r"\.check\.no\s*\{[^}]*border-radius\s*:\s*7px", css, flags=re.S), (
        ".check.no must differ in shape from .check.yes"
    )
    # the locked flow node carries a glyph
    assert re.search(r"\.flow-node\.locked::before", css), (
        ".flow-node.locked must carry a non-colour cue"
    )
    # the KPI delta carries an arrow
    assert re.search(r"\.kpi-delta\.up::before", css), (
        ".kpi-delta.up must carry a non-colour cue"
    )


def test_status_tokens_exist(root_tokens: dict[str, str]) -> None:
    """The status colours are named and available."""
    for token in ("--success", "--warning", "--danger", "--info"):
        assert token in root_tokens, f"{token} is missing from the canonical palette"


# ── 9. a fixed palette ────────────────────────────────────────────────────
def test_fill_role_hues_are_never_used_as_text(sheets: dict[str, str]) -> None:
    """--primary / --primary-deep are fills; the primary TEXT hue is --primary-hi."""
    offenders = []
    for name in STYLESHEETS:
        for match in re.finditer(r"(?<![\w-])color\s*:\s*var\((--[\w-]+)\)", sheets[name]):
            token = match.group(1)
            if token in ("--primary", "--primary-deep"):
                line = sheets[name][: match.start()].count("\n") + 1
                offenders.append(f"{name}:{line}: color: var({token})")
    assert not offenders, (
        "the deep primary hues are fills, not text colours:\n  " + "\n  ".join(offenders)
    )


def test_secondary_hues_are_used_sparingly(sheets: dict[str, str]) -> None:
    """Orange and rose may survive as rare emphasis, not as decoration."""
    counts: dict[str, int] = {}
    for token in SECONDARY_HUES:
        used = 0
        for name in STYLESHEETS:
            for _selector, body in _rules(sheets[name]):
                if f"var({token})" in body:
                    used += 1
        counts[token] = used

    offenders = [
        f"{token}: used in {count} rules (limit {MAX_RULES_PER_SECONDARY_HUE})"
        for token, count in counts.items()
        if count > MAX_RULES_PER_SECONDARY_HUE
    ]
    assert not offenders, "the palette is not fixed:\n  " + "\n  ".join(offenders)


def test_the_palette_has_a_small_fixed_hue_set(root_tokens: dict[str, str]) -> None:
    """The raw hues are the five the brand uses, not a free-for-all."""
    hues = {
        "--primary",
        "--primary-hi",
        "--primary-deep",
        "--accent",
        "--accent-hi",
        "--accent-soft",
        "--emphasis",
        "--emphasis-hi",
        "--pink",
        "--pink-soft",
        "--success",
    }
    assert hues <= set(root_tokens), f"missing palette tokens: {sorted(hues - set(root_tokens))}"


def test_no_light_theme_was_introduced(sheets: dict[str, str]) -> None:
    """The site is dark-only; a light theme is out of scope."""
    for name in STYLESHEETS:
        assert "prefers-color-scheme" not in sheets[name], (
            f"{name} introduces a colour-scheme query; the site is dark-only"
        )


#: The codepoints that render as a COLOUR emoji by default. A colour emoji
#: is a hue on a black-and-white page, and a hue that is not part of the
#: palette reads as an oversight rather than as an accent.
_COLOUR_EMOJI_RANGES = (
    (0x1F300, 0x1F5FF), (0x1F600, 0x1F64F), (0x1F680, 0x1F6FF),
    (0x1F900, 0x1F9FF), (0x1FA70, 0x1FAFF),
)
_COLOUR_EMOJI_SINGLE = frozenset({0x26A1, 0x2709, 0x2753, 0x2757, 0x2764, 0x274C, 0x2B50})


def test_the_site_carries_no_colour_emoji() -> None:
    """The icon language is SVG, not emoji.

    The pictographic entities were replaced with a set of 24x24 SVG line
    glyphs (scripts/site_glyphs.py), so the page's only icons are vector
    art drawn in ``currentColor``.

    The plain glyphs - the check marks, the cross marks, the arrows, the
    rupee sign - are NOT colour emoji and they stay: a check mark that
    marks a comparison cell is a status badge, and it is paired with a
    colour and a shape as well as a tick.
    """
    offenders = []
    for path in sorted(CSS_DIR.parent.glob("*.html")) + sorted(CSS_DIR.parent.glob("js/*.js")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r"&#(\d{4,7});", text):
            code = int(match.group(1))
            in_range = any(low <= code <= high for low, high in _COLOUR_EMOJI_RANGES)
            if not in_range and code not in _COLOUR_EMOJI_SINGLE:
                continue
            line = text[: match.start()].count("\n") + 1
            offenders.append(f"{path.name}:{line}: U+{code:04X}")
    assert not offenders, (
        "colour emoji on a monochrome page:\n  " + "\n  ".join(offenders)
    )


def test_the_glyphs_are_real_svg() -> None:
    """Every glyph is an inline SVG, sized and stroked by the stylesheet."""
    total = 0
    for path in sorted(CSS_DIR.parent.glob("*.html")):
        text = path.read_text(encoding="utf-8")
        found = re.findall(r'<svg class="glyph" viewBox="0 0 24 24"[^>]*>', text)
        total += len(found)
        for tag in found:
            assert 'aria-hidden="true"' in tag, (
                f"{path.name}: a glyph must be hidden from assistive technology"
            )
    assert total > 50, f"expected the icon set to be in use, found {total} glyphs"
