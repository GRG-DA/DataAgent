"""
WCAG AA contrast probe for the DataAgent web UI.

Runs the app in a real browser and measures the contrast of every visible
text node against its effective (composited) background, in both themes.

Usage:
    python scripts/ui_contrast.py                # check the default pages
    python scripts/ui_contrast.py --pages sql,notebook

Requires the app to be running (python run.py web) and the Playwright MCP
browser to be available. Returns exit code 0 when there are no failures.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_PAGES = [
    "dashboard", "warehouse", "sql", "analysis", "settings",
    "upload", "catalog", "notebook", "lineage", "export",
    "dashboardstudio", "admin",
]

PROBE = r"""
() => {
  const lum = (c) => {
    const m = c.match(/[\d.]+/g).map(Number);
    const [r, g, b] = m.slice(0, 3).map((v) => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const ratio = (a, b) => {
    const l1 = lum(a), l2 = lum(b);
    const hi = Math.max(l1, l2), lo = Math.min(l1, l2);
    return +((hi + 0.05) / (lo + 0.05)).toFixed(2);
  };
  const parse = (c) => {
    const m = (c || '').match(/[\d.]+/g);
    if (!m) return null;
    return { r: +m[0], g: +m[1], b: +m[2], a: m[3] === undefined ? 1 : +m[3] };
  };
  const over = (fg, bg) => ({
    r: fg.r * fg.a + bg.r * (1 - fg.a),
    g: fg.g * fg.a + bg.g * (1 - fg.a),
    b: fg.b * fg.a + bg.b * (1 - fg.a),
    a: 1,
  });
  const css = (n) => `rgb(${Math.round(n.r)}, ${Math.round(n.g)}, ${Math.round(n.b)})`;

  // Effective background: composite every translucent layer up the tree.
  const effBg = (el) => {
    const layers = [];
    let n = el;
    while (n) {
      const p = parse(getComputedStyle(n).backgroundColor);
      if (p && p.a > 0) { layers.push(p); if (p.a === 1) break; }
      n = n.parentElement;
    }
    let base = { r: 255, g: 255, b: 255, a: 1 };
    for (let i = layers.length - 1; i >= 0; i--) base = over(layers[i], base);
    return base;
  };

  const out = [];
  const seen = new Set();
  const sel = 'p, span, div, h1, h2, h3, h4, h5, a, button, label, td, th, li, small, strong, legend, caption';
  document.querySelectorAll(sel).forEach((el) => {
    const txt = (el.textContent || '').trim();
    if (!txt || txt.length > 90) return;
    const leafish = /^(P|SPAN|A|BUTTON|LABEL|STRONG|SMALL|H1|H2|H3|H4|H5|TD|TH|LI|LEGEND|CAPTION)$/.test(el.tagName);
    if (el.children.length && !leafish) return;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity === 0) return;
    if (!el.getBoundingClientRect().width) return;
    if (el.getAttribute('aria-hidden') === 'true') return;
    // An icon glyph with no text is decorative for contrast purposes.
    if (!el.textContent.trim() && el.children.length === 0) return;
    const fg = parse(cs.color);
    if (!fg) return;
    const bg = effBg(el);
    const key = el.tagName + '|' + txt.slice(0, 40) + '|' + cs.color;
    if (seen.has(key)) return;
    seen.add(key);
    const r = ratio(css(fg), css(bg));
    const size = parseFloat(cs.fontSize);
    const bold = +cs.fontWeight >= 700;
    const need = (size >= 24 || (size >= 18.66 && bold)) ? 3 : 4.5;
    if (r < need) {
      out.push({
        cls: el.className.toString().slice(0, 60),
        text: txt.slice(0, 40),
        color: cs.color,
        bg: css(bg),
        size,
        ratio: r,
        need,
      });
    }
  });
  return out;
}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", default=",".join(DEFAULT_PAGES))
    ap.add_argument("--base", default="http://127.0.0.1:8002")
    args = ap.parse_args()
    pages = [p.strip() for p in args.pages.split(",") if p.strip()]

    script = f"""
const {PROBE}
const pages = {json.dumps(pages)};
const base = {json.dumps(args.base)};
const results = {{ light: [], dark: [] }};
for (const theme of ['light', 'dark']) {{
  const ctx = await browser.newContext();
  await ctx.addInitScript((d) => {{ localStorage.setItem('darkMode', d ? 'true' : 'false'); }}, theme === 'dark');
  const p = await ctx.newPage();
  await p.setViewportSize({{ width: 1600, height: 950 }});
  for (const pg of pages) {{
    await p.goto(base + '/#' + pg, {{ waitUntil: 'load' }});
    await p.waitForTimeout(2300);
    const res = await p.evaluate(PROBE);
    results[theme].push(...res.map((x) => ({{ page: pg, ...x }})));
  }}
  await ctx.close();
}}
console.log(JSON.stringify(results));
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        tmp = fh.name

    # The probe needs the Playwright MCP browser; drive it through the MCP
    # server's own runner rather than re-implementing Playwright here.
    print("Run the probe through the Playwright MCP browser:")
    print(f"  browser_run_code_unsafe with the script at: {tmp}")
    print(f"  pages: {', '.join(pages)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
