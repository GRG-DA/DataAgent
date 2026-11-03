"""
Generate `web/css/utilities.css` — the token-driven utility layer.

Every colour utility resolves to a design-system custom property so the app
cannot drift into a second palette. Layout utilities use plain px/rem.

The generated file is deterministic: re-running this script overwrites it with
the same content, so it can be regenerated when the token set changes.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
OUT = WEB / "css" / "utilities.css"

# ── Colour maps ────────────────────────────────────────────────────────────
# Each entry: utility suffix -> token expression.
BG_MAP = {
    "primary": "var(--color-primary)",
    "primary-400": "var(--color-primary)",
    "primary-500": "var(--color-primary-solid)",
    "primary-600": "var(--color-primary-solid)",
    "primary-700": "var(--color-primary-active)",
    "primary-50": "var(--color-primary-light)",
    "primary-100": "var(--color-primary-light)",
    "primary-200": "var(--color-primary-border)",
    "primary-300": "var(--color-primary-border)",
    "primary-900": "var(--color-primary-light)",
    "gray-50": "var(--color-surface-2)",
    "gray-100": "var(--color-surface-2)",
    "gray-200": "var(--color-surface-3)",
    "gray-300": "var(--color-surface-3)",
    "gray-400": "var(--color-border-strong)",
    "gray-500": "var(--color-text-muted)",
    "gray-600": "var(--color-text-secondary)",
    "gray-700": "var(--color-text-secondary)",
    "gray-800": "var(--color-text)",
    "gray-900": "var(--color-text)",
    "indigo-100": "var(--color-primary-light)",
    "indigo-200": "var(--color-primary-border)",
    "indigo-400": "var(--color-primary-hover)",
    "indigo-500": "var(--color-primary-solid)",
    "indigo-600": "var(--color-primary-solid)",
    "indigo-700": "var(--color-primary-active)",
    "purple-50": "var(--color-accent-purple-bg)",
    "purple-100": "var(--color-accent-purple-bg)",
    "purple-300": "var(--color-accent-purple)",
    "purple-600": "var(--color-accent-purple-solid)",
    "purple-700": "var(--color-accent-purple)",
    # Modal scrims.
    "black-50": "rgba(15, 23, 42, .5)",
    "black-30": "rgba(15, 23, 42, .3)",
    "blue-50": "var(--color-info-bg)",
    "blue-100": "var(--color-info-bg)",
    "blue-300": "var(--color-info)",
    "blue-400": "var(--color-info)",
    "blue-700": "var(--color-info)",
    "green-50": "var(--color-success-bg)",
    "green-100": "var(--color-success-bg)",
    "green-200": "var(--color-success-bg)",
    "green-300": "var(--color-success)",
    "green-400": "var(--color-success)",
    "emerald-50": "var(--color-success-bg)",
    "emerald-100": "var(--color-success-bg)",
    "emerald-200": "var(--color-success-bg)",
    "emerald-500": "var(--color-success-solid)",
    "emerald-600": "var(--color-success)",
    "red-50": "var(--color-danger-bg)",
    "red-100": "var(--color-danger-bg)",
    "red-400": "var(--color-danger)",
    "amber-200": "var(--color-warning-bg)",
    "amber-400": "var(--color-warning)",
    "amber-500": "var(--color-warning-solid)",
    "amber-600": "var(--color-warning)",
    "yellow-100": "var(--color-warning-bg)",
    "yellow-200": "var(--color-warning-bg)",
    "orange-50": "var(--color-warning-bg)",
    "orange-500": "var(--color-warning-solid)",
    "orange-600": "var(--color-warning)",
    "teal-500": "var(--color-accent-teal-solid)",
    "teal-600": "var(--color-accent-teal)",
}

TEXT_MAP = {
    "primary": "var(--color-primary)",
    "primary-200": "var(--color-primary)",
    "primary-300": "var(--color-primary)",
    "primary-400": "var(--color-primary)",
    "primary-500": "var(--color-primary-hover)",
    "primary-600": "var(--color-primary-hover)",
    "primary-700": "var(--color-primary-active)",
    "gray-100": "var(--color-text)",
    "gray-200": "var(--color-text)",
    "gray-300": "var(--color-text-secondary)",
    "gray-400": "var(--color-text-muted)",
    "gray-500": "var(--color-text-muted)",
    "gray-600": "var(--color-text-secondary)",
    "gray-700": "var(--color-text-secondary)",
    "gray-800": "var(--color-text)",
    "gray-900": "var(--color-text)",
    "indigo-300": "var(--color-primary)",
    "indigo-400": "var(--color-primary)",
    "indigo-500": "var(--color-primary)",
    "indigo-600": "var(--color-primary-hover)",
    "indigo-700": "var(--color-primary-active)",
    "purple-500": "var(--color-accent-purple)",
    "purple-600": "var(--color-accent-purple-solid)",
    "purple-700": "var(--color-accent-purple)",
    "blue-500": "var(--color-info)",
    "blue-600": "var(--color-info)",
    "blue-700": "var(--color-info)",
    "cyan-500": "var(--color-info)",
    "emerald-500": "var(--color-success)",
    "emerald-600": "var(--color-success)",
    "green-500": "var(--color-success)",
    "green-600": "var(--color-success)",
    "green-700": "var(--color-success)",
    "red-600": "var(--color-danger)",
    "red-700": "var(--color-danger)",
    "rose-500": "var(--color-danger)",
    "orange-400": "var(--color-warning)",
    "orange-500": "var(--color-warning)",
    "amber-300": "var(--color-warning)",
    "amber-400": "var(--color-warning)",
    "white": "var(--color-on-solid)",
    "muted": "var(--color-text-muted)",
}

BORDER_MAP = {
    "primary-200": "var(--color-primary-border)",
    "primary-300": "var(--color-primary-border)",
    "primary-400": "var(--color-primary-border)",
    "primary-500": "var(--color-primary-solid)",
    "gray-100": "var(--color-border)",
    "gray-200": "var(--color-border)",
    "gray-300": "var(--color-border-strong)",
    "indigo-200": "var(--color-primary-border)",
    "indigo-400": "var(--color-primary-border)",
    "purple-300": "var(--color-accent-purple)",
    "blue-300": "var(--color-info)",
    "blue-400": "var(--color-info)",
    "green-200": "var(--color-success)",
    "green-300": "var(--color-success)",
    "emerald-200": "var(--color-success)",
    "amber-200": "var(--color-warning)",
    "yellow-200": "var(--color-warning)",
    "red-400": "var(--color-danger)",
}

# ── Layout scales ──────────────────────────────────────────────────────────
SPACING = {
    "0": "0px", "0.5": "2px", "1": "4px", "1.5": "6px", "2": "8px", "2.5": "10px",
    "3": "12px", "4": "16px", "5": "20px", "6": "24px", "8": "32px", "10": "40px",
    "12": "48px", "14": "56px", "16": "64px", "20": "80px",
}
SIZES = {
    "1.5": "6px", "2": "8px", "3": "12px", "16": "64px", "20": "80px",
    "40": "160px", "48": "192px", "56": "224px", "72": "288px", "80": "320px",
    "96": "384px",
}
MAXH = {
    "32": "128px", "48": "192px", "56": "224px", "64": "256px",
    "[200px]": "200px", "[250px]": "250px", "[300px]": "300px",
}
MAXW = {
    "xs": "20rem", "3xl": "48rem", "5xl": "64rem", "none": "none",
    "[120px]": "120px", "[140px]": "140px", "[190px]": "190px", "[200px]": "200px",
}
MINH = {"[28px]": "28px", "[80px]": "80px", "[300px]": "300px"}
MINW = {"[120px]": "120px", "[140px]": "140px", "[150px]": "150px", "[200px]": "200px"}
WIDTH = {
    "1.5": "6px", "2": "8px", "3": "12px", "16": "64px", "20": "80px",
    "40": "160px", "48": "192px", "56": "224px", "72": "288px", "80": "320px",
    "1/3": "33.3333%", "2/3": "66.6667%", "px": "1px", "full": "100%",
}
FONTSIZE = {"5xl": "48px", "[8px]": "8px", "[9px]": "9px", "[10px]": "10px",
            "[11px]": "11px", "[12px]": "12px"}
OPACITY = {"0": "0", "70": ".7", "90": ".9", "100": "1"}


def esc(name: str) -> str:
    """Escape a utility name for use in a CSS selector."""
    return name.replace("\\", "\\\\").replace(":", "\\:").replace("/", "\\/").replace("[", "\\[").replace("]", "\\]").replace(".", "\\.")


def build() -> str:
    out: list[str] = []
    a = out.append

    a("/* ═══════════════════════════════════════════════════════════════════")
    a("   DataAgent — Utility Layer")
    a("   ─────────────────────────────────────────────────────────────────")
    a("   Tailwind-style helper classes used across the app markup.")
    a("")
    a("   Every COLOUR here resolves to a design-system custom property, so")
    a("   this layer can never introduce a second palette. Layout values are")
    a("   plain px/rem because they carry no brand meaning.")
    a("")
    a("   Regenerate with: python scripts/_gen_utilities.py")
    a("   ═══════════════════════════════════════════════════════════════════ */")
    a("")

    def section(title: str) -> None:
        a("")
        a(f"/* ── {title} {'─' * max(0, 66 - len(title))} */")

    # ── Backgrounds ────────────────────────────────────────────────────────
    section("Backgrounds")
    for key, token in sorted(BG_MAP.items()):
        a(f".bg-{esc(key)} {{ background-color: {token}; }}")
    # Alpha variants resolve through color-mix on the semantic tokens.
    for key, token in (
        ("gray-50/50", "var(--color-surface-2)"),
        ("green-50/50", "var(--color-success-bg)"),
        ("red-50/50", "var(--color-danger-bg)"),
        ("purple-50/50", "var(--color-accent-purple-bg)"),
    ):
        a(f".bg-{esc(key)} {{ background-color: color-mix(in srgb, {token} 50%, transparent); }}")

    # ── Text ───────────────────────────────────────────────────────────────
    section("Text")
    for key, token in sorted(TEXT_MAP.items()):
        a(f".text-{esc(key)} {{ color: {token}; }}")
    for key, size in sorted(FONTSIZE.items()):
        a(f".text-{esc(key)} {{ font-size: {size}; }}")

    # ── Borders ────────────────────────────────────────────────────────────
    section("Borders")
    for key, token in sorted(BORDER_MAP.items()):
        a(f".border-{esc(key)} {{ border-color: {token}; }}")
    a(".border-3 { border-width: 3px; }")
    a(".border-b-2 { border-bottom-width: 2px; }")
    a(".border-l-2 { border-left-width: 2px; }")
    a(".border-collapse { border-collapse: collapse; }")
    a(".border-dashed { border-style: dashed; }")
    a(".border-t-primary-500 { border-top-color: var(--color-primary); }")
    a(".divide-y > :not([hidden]) ~ :not([hidden]) {")
    a("  border-top-width: 1px;")
    a("  border-top-style: solid;")
    a("}")
    a(".divide-gray-100 > :not([hidden]) ~ :not([hidden]) {")
    a("  border-top-color: var(--color-border);")
    a("}")

    # ── Spacing ────────────────────────────────────────────────────────────
    section("Spacing")
    for key, value in sorted(SPACING.items(), key=lambda kv: float(kv[0])):
        a(f".p-{esc(key)} {{ padding: {value}; }}")
        a(f".px-{esc(key)} {{ padding-left: {value}; padding-right: {value}; }}")
        a(f".py-{esc(key)} {{ padding-top: {value}; padding-bottom: {value}; }}")
        a(f".pt-{esc(key)} {{ padding-top: {value}; }}")
        a(f".pb-{esc(key)} {{ padding-bottom: {value}; }}")
        a(f".pl-{esc(key)} {{ padding-left: {value}; }}")
        a(f".pr-{esc(key)} {{ padding-right: {value}; }}")
        a(f".m-{esc(key)} {{ margin: {value}; }}")
        a(f".mx-{esc(key)} {{ margin-left: {value}; margin-right: {value}; }}")
        a(f".my-{esc(key)} {{ margin-top: {value}; margin-bottom: {value}; }}")
        a(f".mt-{esc(key)} {{ margin-top: {value}; }}")
        a(f".mb-{esc(key)} {{ margin-bottom: {value}; }}")
        a(f".ml-{esc(key)} {{ margin-left: {value}; }}")
        a(f".mr-{esc(key)} {{ margin-right: {value}; }}")
    a(".gap-x-4 { column-gap: 16px; }")
    a(".gap-y-1 { row-gap: 4px; }")
    a(".space-y-1\\.5 > :not([hidden]) ~ :not([hidden]) { margin-top: 6px; }")
    a(".space-y-5 > :not([hidden]) ~ :not([hidden]) { margin-top: 20px; }")

    # ── Sizing ─────────────────────────────────────────────────────────────
    section("Sizing")
    for key, value in sorted(SIZES.items(), key=lambda kv: float(kv[0])):
        a(f".h-{esc(key)} {{ height: {value}; }}")
    a(".h-[500px] { height: 500px; }")
    for key, value in sorted(MAXH.items()):
        a(f".max-h-{esc(key)} {{ max-height: {value}; }}")
    for key, value in sorted(MAXW.items()):
        a(f".max-w-{esc(key)} {{ max-width: {value}; }}")
    for key, value in sorted(MINH.items()):
        a(f".min-h-{esc(key)} {{ min-height: {value}; }}")
    for key, value in sorted(MINW.items()):
        a(f".min-w-{esc(key)} {{ min-width: {value}; }}")
    for key, value in sorted(WIDTH.items(), key=lambda kv: kv[0]):
        a(f".w-{esc(key)} {{ width: {value}; }}")

    # ── Position ───────────────────────────────────────────────────────────
    section("Position")
    a(".left-3 { left: 12px; }")
    a(".right-5 { right: 20px; }")
    a(".top-2\\.5 { top: 10px; }")
    a(".top-14 { top: 56px; }")
    a(".top-16 { top: 64px; }")

    # ── Gradient stops ─────────────────────────────────────────────────────
    # Used with `.bg-gradient-to-*` to tint a brand gradient. Flat brand
    # colour only — no decorative multi-hue gradients.
    section("Gradient stops")
    a(".from-indigo-500 { --tw-gradient-from: var(--color-primary); }")
    a(".from-purple-500 { --tw-gradient-from: var(--color-accent-purple); }")
    a(".to-blue-500 { --tw-gradient-to: var(--color-info); }")
    a(".to-purple-600 { --tw-gradient-to: var(--color-accent-purple); }")

    # ── Misc ───────────────────────────────────────────────────────────────
    section("Misc")
    a(".pl-9 { padding-left: 36px; }")
    a(".h-\\[500px\\] { height: 500px; }")
    a(".rounded-t { border-top-left-radius: var(--radius-sm); border-top-right-radius: var(--radius-sm); }")
    a(".rounded-t-lg { border-top-left-radius: var(--radius-lg); border-top-right-radius: var(--radius-lg); }")

    # ── Effects ────────────────────────────────────────────────────────────
    section("Effects")
    for key, value in sorted(OPACITY.items()):
        a(f".opacity-{key} {{ opacity: {value}; }}")
    a(".shadow-indigo-500\\/25 { box-shadow: 0 4px 12px rgba(var(--color-primary-rgb), .25); }")
    a(".hover\\:shadow-sm:hover { box-shadow: var(--shadow-sm); }")
    a(".hover\\:shadow-md:hover { box-shadow: var(--shadow-md); }")
    a(".hover\\:opacity-70:hover { opacity: .7; }")
    a(".hover\\:opacity-90:hover { opacity: .9; }")
    a(".hover\\:opacity-100:hover { opacity: 1; }")
    a(".hover\\:underline:hover { text-decoration: underline; }")
    a(".transition-opacity { transition: opacity var(--transition-fast); }")
    a(".transition-shadow { transition: box-shadow var(--transition-fast); }")
    a(".duration-300 { transition-duration: 300ms; }")

    # ── Typography ─────────────────────────────────────────────────────────
    section("Typography")
    a(".leading-none { line-height: 1; }")
    a(".list-decimal { list-style-type: decimal; }")
    a(".whitespace-pre-wrap { white-space: pre-wrap; }")
    a(".line-clamp-2 {")
    a("  display: -webkit-box;")
    a("  -webkit-line-clamp: 2;")
    a("  -webkit-box-orient: vertical;")
    a("  overflow: hidden;")
    a("}")
    a(".placeholder-indigo-300::placeholder { color: var(--color-text-muted); }")

    # ── Flex / grid ────────────────────────────────────────────────────────
    section("Flex & grid")
    a(".flex-nowrap { flex-wrap: nowrap; }")
    a(".overflow-auto { overflow: auto; }")
    a(".resize-y { resize: vertical; }")
    a(".cursor-not-allowed { cursor: not-allowed; }")
    a(".disabled\\:cursor-not-allowed:disabled { cursor: not-allowed; }")
    a(".focus\\:outline-none:focus { outline: none; }")
    a(".focus\\:border-primary-500:focus { border-color: var(--color-primary); }")
    a(".last\\:border-0:last-child { border-width: 0; }")
    a(".group:hover .group-hover\\:opacity-100 { opacity: 1; }")

    # ── Hover colours ──────────────────────────────────────────────────────
    section("Hover colours")
    HOVER_BG = {
        "gray-50": "var(--color-surface-2)",
        "gray-100": "var(--color-surface-2)",
        "gray-600": "var(--color-text-secondary)",
        "primary-700": "var(--color-primary-active)",
        "indigo-100": "var(--color-primary-light)",
        "indigo-600": "var(--color-primary-solid)",
        "blue-50": "var(--color-info-bg)",
        "blue-100": "var(--color-info-bg)",
        "green-50": "var(--color-success-bg)",
        "green-100": "var(--color-success-bg)",
        "emerald-100": "var(--color-success-bg)",
        "emerald-600": "var(--color-success)",
        "red-50": "var(--color-danger-bg)",
        "red-100": "var(--color-danger-bg)",
        "purple-50": "var(--color-accent-purple-bg)",
        "purple-100": "var(--color-accent-purple-bg)",
        "purple-700": "var(--color-accent-purple)",
        "amber-600": "var(--color-warning)",
        "orange-50": "var(--color-warning-bg)",
        "orange-600": "var(--color-warning)",
        "teal-600": "var(--color-accent-teal)",
        "yellow-50": "var(--color-warning-bg)",
        "yellow-100": "var(--color-warning-bg)",
    }
    for key, token in sorted(HOVER_BG.items()):
        a(f".hover\\:bg-{esc(key)}:hover {{ background-color: {token}; }}")
    HOVER_TEXT = {
        "gray-600": "var(--color-text-secondary)",
        "gray-700": "var(--color-text)",
        "gray-900": "var(--color-text)",
        "primary-500": "var(--color-primary-solid)",
        "primary-600": "var(--color-primary-solid)",
        "primary-700": "var(--color-primary-active)",
        "indigo-600": "var(--color-primary-solid)",
        "purple-600": "var(--color-accent-purple-solid)",
        "red-600": "var(--color-danger)",
        "green-700": "var(--color-success)",
    }
    for key, token in sorted(HOVER_TEXT.items()):
        a(f".hover\\:text-{esc(key)}:hover {{ color: {token}; }}")
    a(".hover\\:border-gray-300:hover { border-color: var(--color-border-strong); }")

    # ── Prose ──────────────────────────────────────────────────────────────
    section("Prose")
    a(".prose-sm {")
    a("  font-size: 13px;")
    a("  line-height: 1.6;")
    a("  color: var(--color-content-text);")
    a("}")
    a(".prose-sm > :first-child { margin-top: 0; }")
    a(".prose-sm > :last-child { margin-bottom: 0; }")
    a(".prose-sm p { margin: 0 0 10px; }")
    a(".prose-sm h1 { font-size: 20px; font-weight: 700; margin: 16px 0 8px; }")
    a(".prose-sm h2 { font-size: 17px; font-weight: 700; margin: 14px 0 8px; }")
    a(".prose-sm h3 { font-size: 15px; font-weight: 600; margin: 12px 0 6px; }")
    a(".prose-sm h4 { font-size: 13px; font-weight: 600; margin: 10px 0 6px; }")
    a(".prose-sm ul, .prose-sm ol { margin: 0 0 10px; padding-left: 20px; }")
    a(".prose-sm li { margin-bottom: 4px; }")
    a(".prose-sm a { color: var(--color-primary); text-decoration: underline; }")
    a(".prose-sm code {")
    a("  font-family: var(--font-mono);")
    a("  font-size: 12px;")
    a("  background: var(--color-surface-3);")
    a("  padding: 1px 4px;")
    a("  border-radius: 3px;")
    a("}")
    a(".prose-sm pre {")
    a("  font-family: var(--font-mono);")
    a("  font-size: 12px;")
    a("  background: var(--color-surface-3);")
    a("  padding: 12px;")
    a("  border-radius: var(--radius-md);")
    a("  overflow-x: auto;")
    a("  margin: 0 0 10px;")
    a("}")
    a(".prose-sm blockquote {")
    a("  border-left: 3px solid var(--color-border-strong);")
    a("  padding-left: 12px;")
    a("  color: var(--color-text-secondary);")
    a("  margin: 0 0 10px;")
    a("}")
    a(".prose-sm table { width: 100%; border-collapse: collapse; font-size: 12px; }")
    a(".prose-sm th, .prose-sm td {")
    a("  border: 1px solid var(--color-border);")
    a("  padding: 6px 8px;")
    a("  text-align: left;")
    a("}")
    a(".prose-sm th { background: var(--color-surface-2); font-weight: 600; }")
    a("[data-theme=\"dark\"] .prose-invert,")
    a(".dark .prose-invert {")
    a("  color: var(--color-text);")
    a("}")
    a("[data-theme=\"dark\"] .prose-invert code,")
    a("[data-theme=\"dark\"] .prose-invert pre,")
    a(".dark .prose-invert code,")
    a(".dark .prose-invert pre { background: var(--color-surface-3); }")
    a("[data-theme=\"dark\"] .prose-invert th,")
    a(".dark .prose-invert th { background: var(--color-surface-2); }")

    # ── Responsive ─────────────────────────────────────────────────────────
    # Display helpers carry !important so they beat the base
    # `.hidden { display: none !important }` utility.
    section("Responsive (sm: >= 640px)")
    a("@media (min-width: 640px) {")
    a("  .sm\\:hidden { display: none !important; }")
    a("  .sm\\:flex-row { flex-direction: row; }")
    a("  .sm\\:grid-cols-2 { grid-template-columns: repeat(2, 1fr); }")
    a("  .sm\\:items-center { align-items: center; }")
    a("  .sm\\:justify-between { justify-content: space-between; }")
    a("}")

    a("")
    return "\n".join(out)


if __name__ == "__main__":
    OUT.write_text(build(), encoding="utf-8", newline="\n")
    print(f"wrote {OUT}")
    text = OUT.read_text(encoding="utf-8")
    print("braces:", text.count("{"), text.count("}"))
    assert text.count("{") == text.count("}"), "unbalanced braces"
    print("rules:", text.count("{"))
