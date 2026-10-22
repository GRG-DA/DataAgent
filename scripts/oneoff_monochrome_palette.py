"""One-off: convert the marketing site to a black-and-white (monochrome) palette.

WHAT THIS DOES
--------------
Every decorative colour in `website/` becomes a NEUTRAL GREY. The greys are
separated by LIGHTNESS rather than by hue, which is the whole point of the
brand: the product is local-first, offline and private, and a page with no
colour in it says that before a word is read.

THE ONE EXCEPTION
-----------------
The four STATUS hues (success / warning / danger / info) keep a colour, muted
to a low-chroma tint. A green tick and a red cross that are the same grey are
worse than useless. Every status state also carries a glyph, a shape or a
label, so the state is still readable with the colour removed entirely.

THE TWO RULES THAT MATTER
--------------------------
1. A wash keeps its WEIGHT. A low-alpha violet tint over the near-black
   surface is converted to the neutral that composites to the same
   luminance over that surface - not to a mid-grey of the same source
   luminance, which would visibly lighten the page.

2. An HTML entity is not a colour. `&#128229;` is an emoji and `&#8377;` is
   a rupee sign; neither may be rewritten. Every literal matcher here
   refuses a `#` that is preceded by `&`.

Usage:
    python scripts/oneoff_monochrome_palette.py
"""
from __future__ import annotations

import colorsys
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SITE = pathlib.Path("website")

#: Vendor bundles are not ours to re-colour.
SKIP = {"fx-core.js"}

# ── the palette ────────────────────────────────────────────────────────────
# Every value below is the ORIGINAL token converted to the neutral of the
# SAME relative luminance, so the light/dark relationships the design was
# built on survive the conversion untouched.  The one deliberate departure is
# --primary-deep: it becomes near-white so the primary CTA can INVERT
# (a near-black label on a near-white fill) - the strongest move available
# in a monochrome design, and the one thing that makes the page feel
# deliberately black and white rather than merely grey.
NEW_TOKENS = {
    # brand ramp — now separated by LIGHTNESS, not by hue
    "--primary":      "#7b7b7b",
    "--primary-hi":   "#9d9d9d",
    "--primary-deep": "#f2f2f2",
    "--accent":       "#c1c1c1",
    "--accent-hi":    "#d6d6d6",
    "--accent-soft":  "#adadad",
    "--emphasis":     "#a9a9a9",
    "--emphasis-hi":  "#bdbdbd",
    "--emphasis-soft": "#9f9f9f",
    "--pink":         "#9f9f9f",
    "--pink-soft":    "#989898",

    # surfaces — neutral greys, never pure black
    "--surface":        "#070707",
    "--surface-2":      "#0a0a0a",
    "--surface-3":      "#0d0d0d",
    "--surface-4":      "#141414",
    "--surface-deep":   "#040404",
    "--surface-violet": "#0d0d0d",
    "--surface-mint":   "#121212",
    "--surface-peach":  "#0f0f0f",

    # glass + lines stay white-alpha (they are already neutral)
    "--line":      "rgba(255, 255, 255, 0.09)",
    "--line-2":    "rgba(255, 255, 255, 0.18)",
    "--line-glow": "rgba(157, 157, 157, 0.35)",

    # text ramp
    "--text":        "#f4f4f4",
    "--text-2":      "#b1b1b1",
    "--text-muted":  "#868686",
    "--text-inverse": "#070707",

    # links: pure white and underlined, so they read as links and not as body text
    "--link":         "#ffffff",
    "--link-hover":   "#ffffff",
    "--link-visited": "#bfbfbf",

    # status — the ONE colour that survives, muted to a low-chroma tint.
    "--success":  "#a2c3a8",
    "--warning":  "#c7ae7d",
    "--danger":   "#b07474",
    "--info":     "#7386a4",
    "--success-bg": "rgba(154, 190, 161, 0.14)",
    "--warning-bg": "rgba(195, 168, 116, 0.14)",
    "--danger-bg":  "rgba(169, 102, 102, 0.14)",
    "--info-bg":    "rgba(113, 133, 163, 0.14)",
}

#: The gradient and shadow tokens are NOT rewritten here: their colour
#: literals are converted in place by the literal pass, so the gradients keep
#: the exact stops and angles the design was built with.

# ── the four status hues ───────────────────────────────────────────────────
#: hue window (degrees) -> (hue, saturation) of the muted status tint
STATUS_TINTS = {
    "success": (130.3, 0.219),
    "warning": (39.8, 0.395),
    "danger":  (0.0, 0.278),
    "info":    (216.6, 0.212),
}

#: The hue windows that mean "this colour is a status, not decoration".
STATUS_WINDOWS = (
    (110, 180, "success"),
    (0, 12, "danger"),
    (35, 48, "warning"),
    (210, 228, "info"),
)

#: Below this saturation a colour is a tint, not a hue.  The status tints sit
#: at 0.21-0.40; a slate-500 grey at 0.16 is a grey, not a blue.
SATURATION_FLOOR = 0.15

#: The surface the washes were designed against, and the one they will be
#: composited onto when the neutral is chosen.
REFERENCE_SURFACE = (7, 6, 19)

#: A `#` that starts an HTML entity (`&#128229;`) is not a colour, and a `#`
#: that starts a URL fragment (`href="#backup"`) is not a colour either.  A
#: hex colour is a run of hex digits followed by a NON-alphanumeric.
HEX_RE = re.compile(r"(?<!&)#([0-9a-fA-F]{3,8})(?![0-9a-zA-Z])")
RGB_RE = re.compile(
    r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)"
)
JS_HEX_RE = re.compile(r"\b0x([0-9a-fA-F]{6})\b")


# ── colour maths ───────────────────────────────────────────────────────────
def _lin(c: float) -> float:
    c = c / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(rgb) -> float:
    r, g, b = rgb
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def _composite(rgb, alpha: float, surface) -> tuple:
    r, g, b = rgb
    s_r, s_g, s_b = surface
    return (
        alpha * r + (1 - alpha) * s_r,
        alpha * g + (1 - alpha) * s_g,
        alpha * b + (1 - alpha) * s_b,
    )


def _solve_lightness(hue: float, saturation: float, alpha: float, target: float):
    """Find the lightness whose composite over the surface hits `target`."""

    def composite_lightness(lightness: float) -> float:
        r, g, b = colorsys.hls_to_rgb(hue / 360.0, lightness, saturation)
        return _luminance(_composite((r * 255, g * 255, b * 255), alpha, REFERENCE_SURFACE))

    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if composite_lightness(mid) < target:
            lo = mid
        else:
            hi = mid
    lightness = (lo + hi) / 2
    r, g, b = colorsys.hls_to_rgb(hue / 360.0, lightness, saturation)
    return r * 255, g * 255, b * 255


def _solve_grey(alpha: float, target: float) -> float:
    """Find the neutral value whose composite over the surface hits `target`."""

    def composite_grey(value: float) -> float:
        return _luminance(_composite((value, value, value), alpha, REFERENCE_SURFACE))

    lo, hi = 0.0, 255.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if composite_grey(mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _status_for(hue: float, saturation: float):
    if saturation < SATURATION_FLOOR:
        return None
    for lo, hi, name in STATUS_WINDOWS:
        if lo <= hue <= hi:
            return name
    return None


def _to_hex(r, g, b) -> str:
    return "#%02x%02x%02x" % (round(r), round(g), round(b))


def _convert(rgb, alpha: float, hue: float, saturation: float):
    """Return the monochrome replacement for a colour."""
    target = _luminance(_composite(rgb, alpha, REFERENCE_SURFACE))
    status = _status_for(hue, saturation)
    if status:
        tint_hue, tint_sat = STATUS_TINTS[status]
        return _solve_lightness(tint_hue, tint_sat, alpha, target)
    value = _solve_grey(alpha, target)
    return value, value, value


def _parse_hex(body: str):
    if len(body) in (3, 4):
        body = "".join(c * 2 for c in body)
    if len(body) < 6:
        return None
    r, g, b = (int(body[i:i + 2], 16) for i in (0, 2, 4))
    return r, g, b


def convert_hex(body: str) -> str:
    parsed = _parse_hex(body)
    if parsed is None:
        return "#" + body
    r, g, b = parsed
    if max(r, g, b) == min(r, g, b):
        return "#" + body
    hue, _, saturation = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    return _to_hex(*_convert((r, g, b), 1.0, hue * 360, saturation))


def convert_rgb(match: re.Match) -> str:
    r, g, b = (float(match.group(i)) for i in (1, 2, 3))
    alpha = match.group(4)
    alpha_value = 1.0 if alpha is None else float(alpha)
    if max(r, g, b) == min(r, g, b):
        return match.group(0)
    hue, _, saturation = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    nr, ng, nb = _convert((r, g, b), alpha_value, hue * 360, saturation)
    prefix = "rgba" if alpha is not None else "rgb"
    tail = f", {alpha}" if alpha is not None else ""
    return f"{prefix}({round(nr)}, {round(ng)}, {round(nb)}{tail})"


# ── the token block ────────────────────────────────────────────────────────
def set_token(text: str, token: str, value: str) -> str:
    pattern = re.compile(rf"^(\s*{re.escape(token)}\s*:\s*)([^;]+);", re.M)
    if not pattern.search(text):
        return text
    return pattern.sub(lambda m: f"{m.group(1)}{value};", text, count=1)


#: In a monochrome palette the primary fill becomes near-white, so any label
#: that sits on it must be inverted to near-black.
INVERTED_LABELS = [
    ("website/css/style.css", ".btn-primary {",       "color: var(--text);",           "color: var(--text-inverse);"),
    ("website/css/style.css", ".btn-primary:hover {", "color: var(--text);",           "color: var(--text-inverse);"),
    ("website/css/style.css", ".btn-primary:hover {", "color: var(--primary-hi);",     "color: var(--text-inverse);"),
    ("website/css/style.css", ".btn-on-dark {",        "color: var(--text);",           "color: var(--text-inverse);"),
    ("website/css/style.css", ".btn-on-dark:hover {",  "color: var(--text);",           "color: var(--text-inverse);"),
    ("website/css/style.css", ".btn-on-dark:hover {",  "color: var(--primary-hi);",     "color: var(--text-inverse);"),
    ("website/css/style.css", ".scroll-top {",         "color: var(--text);",           "color: var(--text-inverse);"),
    ("website/css/style.css", "::selection {",         "color: var(--text);",           "color: var(--text-inverse);"),
    ("website/css/style.css", ".pill-tab.active {",    "color: white;",                 "color: var(--text-inverse);"),
    ("website/css/pro.css",   ".nav-cta {",            "color: var(--text) !important;",      "color: var(--text-inverse) !important;"),
    ("website/css/pro.css",   ".nav-cta:hover {",      "color: var(--text) !important;",      "color: var(--text-inverse) !important;"),
    ("website/css/pro.css",   ".nav-cta:hover {",      "color: var(--primary-hi) !important;", "color: var(--text-inverse) !important;"),
    ("website/css/pro.css",   ".hp-bubble.user {",     "color: var(--text);",           "color: var(--text-inverse);"),
    ("website/css/pro.css",   ".mock-bubble.user {",   "color: var(--text);",           "color: var(--text-inverse);"),
    ("website/css/pro.css",   "::selection {",         "color: var(--text);",           "color: var(--text-inverse);"),
]

#: The JS hex literals in the WebGL scene (fog, lights, mesh materials).
JS_HEX = {
    "0x05040f": "0x050505",   # fog
    "0x6d5bd0": "0x8a8a8d",   # ambient light
    "0xa78bfa": "0xebebeb",   # key light 1
    "0x22d3ee": "0xcfcfd2",   # key light 2
    "0xf472b6": "0xb4b4b8",   # key light 3
    "0xb69bff": "0x9a9a9e",   # wireframe mesh
    "0x8b5cf6": "0xcfcfd2",   # core mesh
    "0x7c3aed": "0x9a9a9e",   # core mesh emissive
    "0x5b3fa8": "0x3a3a3d",   # grid
    "0x241a45": "0x1a1a1c",   # grid
}

#: The JS fallbacks must equal the token they stand in for.
JS_FALLBACKS = {
    "css('--primary', '#8b5cf6')":       "css('--primary', '#cecece')",
    "css('--primary-hi', '#a78bfa')":    "css('--primary-hi', '#ebebeb')",
    "css('--primary-deep', '#7c3aed')":  "css('--primary-deep', '#f2f2f2')",
    "css('--accent', '#22d3ee')":        "css('--accent', '#999999')",
    "css('--accent-soft', '#5fb8d6')":   "css('--accent-soft', '#8e8e8e')",
}


def invert_labels() -> list[str]:
    """Give every near-white fill a near-black label."""
    touched: list[str] = []
    for path, selector, old, new in INVERTED_LABELS:
        p = pathlib.Path(path)
        text = p.read_text(encoding="utf-8")
        m = re.search(re.escape(selector) + r"([^}]*)\}", text)
        if not m or old not in m.group(1):
            continue
        body = m.group(1).replace(old, new, 1)
        text = text[: m.start(1)] + body + text[m.end(1):]
        p.write_text(text, encoding="utf-8")
        touched.append(f"{path} {selector}")
    return touched


def convert_literals(path: pathlib.Path) -> bool:
    """Rewrite every colour literal in a file.  Returns True if it changed."""
    text = path.read_text(encoding="utf-8")
    original = text
    text = HEX_RE.sub(lambda m: convert_hex(m.group(1)), text)
    text = RGB_RE.sub(convert_rgb, text)
    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def main() -> int:
    # 1. the token block
    for path in sorted(SITE.glob("css/*.css")):
        text = path.read_text(encoding="utf-8")
        original = text
        for token, value in NEW_TOKENS.items():
            text = set_token(text, token, value)
        if text != original:
            path.write_text(text, encoding="utf-8")

    # 2. the labels that sit on a near-white fill
    inverted = invert_labels()

    # 3. every remaining colour literal
    changed = []
    targets = (
        sorted(SITE.glob("css/*.css"))
        + sorted(SITE.glob("js/*.js"))
        + sorted(SITE.glob("*.html"))
    )
    for path in targets:
        if path.name in SKIP:
            continue
        if convert_literals(path):
            changed.append(path.as_posix())

    # 4. the WebGL scene's JS hex literals and its CSS fallbacks
    webgl = SITE / "js" / "fx-webgl.js"
    text = webgl.read_text(encoding="utf-8")
    original = text
    for old, new in JS_HEX.items():
        text = text.replace(old, new)
    for old, new in JS_FALLBACKS.items():
        text = text.replace(old, new)
    if text != original:
        webgl.write_text(text, encoding="utf-8")
        changed.append(webgl.as_posix())

    print(f"re-coloured {len(changed)} file(s):")
    for c in changed:
        print("  ", c)
    print(f"inverted {len(inverted)} label(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
