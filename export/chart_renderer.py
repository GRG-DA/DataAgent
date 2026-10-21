"""
Universal chart renderer — matplotlib (Agg) backend.

Renders every visual type the dashboard builder can produce into a PNG
byte buffer.  This is the single source of truth for chart images used by
the PPTX and XLSX exporters, so a chart widget NEVER degrades to a bare
data table.

Supported types (superset of blankdash ``_CHART_SUBTYPES`` +
``_CUSTOM_RENDER``):

  bar, column, horizontalbar, stackedbar, stackedcolumn, percentbar,
  line, area, stackedarea, slope, stream, sparkline,
  pie, donut/doughnut, scatter, bubble, radar, polar,
  heatmap, histogram, boxplot, funnel, treemap, waterfall,
  gauge, radial, bullet, progress, sankey, sunburst, wordcloud, venn, combo,
  population, pyramid
"""

import io
import math
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# Cohesive palette matching the in-app dashboard + Power BI theme.
PALETTE = ["#118DFF", "#12239E", "#E66C37", "#6B007B", "#E044A7",
           "#744EC2", "#D9B300", "#D64550", "#197278", "#7A7A7A"]

THEMES = {
    "light": {"bg": "#FFFFFF", "text": "#1F2937", "grid": "#E5E7EB", "muted": "#9CA3AF"},
    "dark": {"bg": "#1E1E2E", "text": "#E5E7EB", "grid": "#3A3A4A", "muted": "#8B8B9E"},
}

# Chart types that can be emitted as native editable charts (openpyxl /
# python-pptx) when the data shape is clean and predictable.
NATIVE_TYPES = {"bar", "column", "line", "area", "pie", "donut", "doughnut"}

_MAX_CATEGORIES = 24
_MAX_SERIES = 8


def _num(v) -> float:
    try:
        f = float(v)
        return f if math.isfinite(f) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _resolve_theme(theme: str) -> dict:
    t = str(theme or "auto").lower()
    return THEMES.get(t, THEMES["light"])


def _extract(df: pd.DataFrame) -> Tuple[List[str], Dict[str, List[float]], Optional[str]]:
    """Return (labels, {series_name: values}, group_col).

    Column 0 is the category axis, column 1 the measure.  When a third
    categorical column with a small cardinality exists it becomes the
    series/group dimension.
    """
    if df is None or df.empty:
        return [], {}, None
    cols = list(df.columns)
    if len(cols) < 2:
        labels = [str(i) for i in range(len(df))]
        values = [_num(v) for v in df[cols[0]].tolist()]
        return labels, {"": values}, None

    group_col = None
    if len(cols) >= 3:
        g = cols[2]
        try:
            uniq = df[g].nunique(dropna=True)
        except Exception:
            uniq = 0
        if 1 < uniq <= _MAX_SERIES:
            group_col = g

    if group_col:
        labels = [str(v)[:24] for v in df[cols[0]].unique()]
        series: Dict[str, List[float]] = {}
        for gi, grp in df.groupby(group_col, sort=False):
            m = dict(zip(grp[cols[0]].astype(str), grp[cols[1]].apply(_num)))
            series[str(gi)[:24]] = [m.get(lbl, 0.0) for lbl in labels]
        return labels, series, group_col

    labels = [str(v)[:24] for v in df[cols[0]].tolist()]
    values = [_num(v) for v in df[cols[1]].tolist()]
    return labels, {"": values}, None


def _draw_empty(ax, th: dict) -> None:
    ax.axis("off")
    ax.text(0.5, 0.5, "No data", ha="center", va="center",
            fontsize=14, color=th["muted"], transform=ax.transAxes)


# ── Cartesian renderers ────────────────────────────────────────────────

def _render_bar(ax, labels, series, df, th, palette,
                horizontal=False, stacked=False, percent=False):
    names = list(series.keys())
    n = len(labels)
    if n == 0:
        return
    if percent:
        rows = [[series[name][i] for name in names] for i in range(n)]
        for i, row in enumerate(rows):
            total = sum(row) or 1.0
            rows[i] = [v / total * 100.0 for v in row]
        for name in names:
            series = dict(series)
        series = {name: [rows[i][j] for i in range(n)]
                  for j, name in enumerate(names)}
    if horizontal:
        y = list(range(n))
        if stacked:
            bottom = [0.0] * n
            for i, name in enumerate(names):
                vals = series[name]
                ax.barh(y, vals, left=bottom, color=palette[i % len(palette)],
                        label=name or None, height=0.6)
                bottom = [b + v for b, v in zip(bottom, vals)]
            ax.set_yticks(y)
            ax.set_yticklabels(labels, fontsize=8)
        else:
            width = 0.8 / max(len(names), 1)
            for i, name in enumerate(names):
                vals = series[name]
                offs = [yy + (i - (len(names) - 1) / 2) * width for yy in y]
                ax.barh(offs, vals, color=palette[i % len(palette)],
                        label=name or None, height=width * 0.9)
            ax.set_yticks(y)
            ax.set_yticklabels(labels, fontsize=8)
    else:
        x = list(range(n))
        if stacked:
            bottom = [0.0] * n
            for i, name in enumerate(names):
                vals = series[name]
                ax.bar(x, vals, bottom=bottom, color=palette[i % len(palette)],
                       label=name or None, width=0.6)
                bottom = [b + v for b, v in zip(bottom, vals)]
            ax.set_xticks(x)
            ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
        else:
            width = 0.8 / max(len(names), 1)
            for i, name in enumerate(names):
                vals = series[name]
                offs = [xx + (i - (len(names) - 1) / 2) * width for xx in x]
                ax.bar(offs, vals, color=palette[i % len(palette)],
                       label=name or None, width=width * 0.9)
            ax.set_xticks(x)
            ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
    if len(names) > 1:
        ax.legend(fontsize=7, frameon=False)


def _render_line(ax, labels, series, df, th, palette,
                 area=False, stacked=False, spark=False):
    xs = list(range(len(labels)))
    names = list(series.keys())
    if stacked and area:
        base = [0.0] * len(xs)
        for i, name in enumerate(names):
            vals = series[name]
            ax.fill_between(xs, base, [b + v for b, v in zip(base, vals)],
                            color=palette[i % len(palette)], alpha=0.5)
            ax.plot(xs, vals, color=palette[i % len(palette)], linewidth=2,
                    label=name or None)
            base = [b + v for b, v in zip(base, vals)]
    else:
        for i, name in enumerate(names):
            vals = series[name]
            if area:
                ax.fill_between(xs, vals, color=palette[i % len(palette)], alpha=0.35)
            ax.plot(xs, vals, color=palette[i % len(palette)], linewidth=2.5,
                    marker="o" if not spark else None, markersize=4,
                    label=name or None)
    if not spark:
        ax.set_xticks(xs)
        ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
        if len(names) > 1:
            ax.legend(fontsize=7, frameon=False)


def _render_pie(ax, labels, series, df, th, palette, donut=False):
    vals = series.get("", []) or next(iter(series.values()), [])
    if not vals:
        return
    ax.grid(False)
    ax.pie(vals, labels=labels, colors=palette[:len(vals)], autopct="%1.0f%%",
           startangle=90,
           wedgeprops={"width": 0.4} if donut else None,
           textprops={"fontsize": 8})
    ax.axis("equal")


def _render_scatter(ax, labels, series, df, th, palette, bubble=False):
    cols = list(df.columns)
    xs = [_num(v) for v in df[cols[0]].tolist()]
    ys = [_num(v) for v in df[cols[1]].tolist()]
    if bubble and len(cols) >= 3:
        sizes = [_num(v) for v in df[cols[2]].tolist()]
        ax.scatter(xs, ys, s=sizes, color=palette[0], alpha=0.8)
    else:
        ax.scatter(xs, ys, s=40, color=palette[0], alpha=0.8)
    ax.set_xlabel(str(cols[0])[:24], fontsize=8)
    ax.set_ylabel(str(cols[1])[:24], fontsize=8)


def _render_radar(ax, labels, series, df, th, palette):
    names = list(series.keys())
    angles = [i / len(labels) * 2 * math.pi for i in range(len(labels))]
    for i, name in enumerate(names):
        vals = series[name]
        ang = angles + angles[:1]
        vv = vals + vals[:1]
        ax.plot(ang, vv, color=palette[i % len(palette)], linewidth=2, label=name or None)
        ax.fill(ang, vv, color=palette[i % len(palette)], alpha=0.2)
    ax.set_xticks(angles)
    ax.set_xticklabels(labels, fontsize=7)
    if len(names) > 1:
        ax.legend(fontsize=7, frameon=False, loc="lower right")


def _render_polar(ax, labels, series, df, th, palette):
    vals = next(iter(series.values()), [])
    if not vals:
        return
    angles = [i / len(vals) * 2 * math.pi for i in range(len(vals))]
    ax.plot(angles + angles[:1], vals + vals[:1], color=palette[0], linewidth=2)
    ax.fill(angles + angles[:1], vals + vals[:1], color=palette[0], alpha=0.2)
    ax.set_xticks(angles)
    ax.set_xticklabels(labels, fontsize=7)


def _render_heatmap(ax, labels, series, df, th, palette):
    cols = list(df.columns)
    ax.grid(False)
    if len(cols) >= 3:
        pivot = df.pivot_table(index=cols[0], columns=cols[1],
                               values=cols[2], aggfunc="mean")
        data = pivot.values
        xt = [str(c)[:12] for c in pivot.columns]
        yt = [str(i)[:12] for i in pivot.index]
    else:
        data = [[_num(v) for v in df[cols[1]].tolist()]]
        xt = labels
        yt = [str(cols[1])[:12]]
    if not data or not data[0]:
        return
    vmin = min(min(r) for r in data)
    vmax = max(max(r) for r in data)
    im = ax.imshow(data, aspect="auto", cmap="Blues", vmin=vmin, vmax=vmax)
    ax.set_xticks(range(len(xt)))
    ax.set_xticklabels(xt, rotation=45, ha="right", fontsize=7)
    ax.set_yticks(range(len(yt)))
    ax.set_yticklabels(yt, fontsize=7)
    mid = (vmin + vmax) / 2
    for i in range(len(yt)):
        for j in range(len(xt)):
            ax.text(j, i, f"{data[i][j]:.1f}", ha="center", va="center",
                    fontsize=6, color="white" if data[i][j] > mid else "black")


def _render_histogram(ax, labels, series, df, th, palette):
    vals = [_num(v) for v in df[df.columns[1]].tolist()]
    if not vals:
        return
    bins = min(20, max(5, len(set(vals))))
    ax.hist(vals, bins=bins, color=palette[0], edgecolor=th["bg"])
    ax.set_xlabel(str(df.columns[1])[:24], fontsize=8)
    ax.set_ylabel("Count", fontsize=8)


def _render_boxplot(ax, labels, series, df, th, palette):
    cols = list(df.columns)
    groups, names = [], []
    for gi, grp in df.groupby(cols[0], sort=False):
        groups.append([_num(v) for v in grp[cols[1]].tolist()])
        names.append(str(gi)[:16])
    if not groups:
        return
    bp = ax.boxplot(groups, patch_artist=True, widths=0.5)
    for patch, i in zip(bp["boxes"], range(len(groups))):
        patch.set_facecolor(palette[i % len(palette)])
    ax.set_xticks(range(1, len(names) + 1))
    ax.set_xticklabels(names, rotation=30, ha="right", fontsize=7)


def _render_funnel(ax, labels, series, df, th, palette):
    vals = next(iter(series.values()), [])
    if not vals:
        return
    n = len(vals)
    mx = max(vals) or 1.0
    y = list(range(n))
    for i in range(n):
        w = vals[i] / mx
        ax.barh(y[i], w, height=0.7, color=palette[i % len(palette)], left=(1 - w) / 2)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlim(0, 1)
    for i in range(n):
        ax.text(0.5, y[i], f"{vals[i]:,.0f}", ha="center", va="center",
                fontsize=7, color="white")


def _treemap_rects(sizes: List[float], x: float, y: float, w: float, h: float):
    """Slice-and-dice treemap layout. Returns list of (x, y, w, h)."""
    total = sum(sizes)
    if total <= 0:
        return []
    order = sorted(range(len(sizes)), key=lambda i: -sizes[i])
    rects = [None] * len(sizes)
    cx, cy, cw, ch = x, y, w, h
    remaining = total
    vertical = True
    for i in order:
        frac = sizes[i] / remaining
        if vertical:
            rw = cw * frac
            rects[i] = (cx, cy, rw, ch)
            cx += rw
            cw -= rw
        else:
            rh = ch * frac
            rects[i] = (cx, cy, cw, rh)
            cy += rh
            ch -= rh
        remaining -= sizes[i]
        vertical = not vertical
    return rects


def _render_treemap(ax, labels, series, df, th, palette):
    from matplotlib.patches import Rectangle
    vals = next(iter(series.values()), [])
    if not vals:
        return
    rects = _treemap_rects(vals, 0, 0, 1, 1)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    for i, (x, y, w, h) in enumerate(rects):
        ax.add_patch(Rectangle((x, y), w, h,
                               facecolor=palette[i % len(palette)],
                               edgecolor=th["bg"], linewidth=1))
        if w > 0.12 and h > 0.06:
            ax.text(x + w / 2, y + h / 2, f"{labels[i]}\n{vals[i]:,.0f}",
                    ha="center", va="center", fontsize=7, color="white")


def _render_waterfall(ax, labels, series, df, th, palette):
    vals = next(iter(series.values()), [])
    if not vals:
        return
    running = 0.0
    bottoms, heights, colors = [], [], []
    for v in vals:
        if v >= 0:
            bottoms.append(running)
            heights.append(v)
            colors.append(palette[0])
        else:
            bottoms.append(running + v)
            heights.append(-v)
            colors.append("#D64550")
        running += v
    ax.bar(range(len(vals)), heights, bottom=bottoms, color=colors, width=0.6)
    ax.set_xticks(range(len(vals)))
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)


def _render_gauge(ax, labels, series, df, th, palette):
    from matplotlib.patches import Wedge
    vals = next(iter(series.values()), [])
    if not vals:
        return
    value = vals[0]
    target = max(abs(v) for v in vals) or 100.0
    frac = max(0.0, min(1.0, value / target))
    theta = math.pi * (1 - frac)
    ax.set_xlim(-1.2, 1.2)
    ax.set_ylim(-1.2, 1.2)
    ax.axis("off")
    ax.add_patch(Wedge((0, 0), 1, 180, 0, width=0.25,
                       facecolor=palette[0], alpha=0.25))
    ax.add_patch(Wedge((0, 0), 1, 180, math.degrees(theta), width=0.25,
                       facecolor=palette[0]))
    ang = math.radians(180 - math.degrees(theta))
    ax.plot([0, math.cos(ang)], [0, math.sin(ang)], color=th["text"], linewidth=2)
    ax.text(0, -0.35, f"{value:,.0f}", ha="center", va="center",
            fontsize=14, color=th["text"])
    if labels:
        ax.text(0, -0.75, labels[0], ha="center", va="center",
                fontsize=8, color=th["muted"])


def _render_bullet(ax, labels, series, df, th, palette):
    vals = next(iter(series.values()), [])
    if not vals:
        return
    value = vals[0]
    target = max(abs(v) for v in vals) or 100.0
    ax.barh(0, target, color=palette[0], alpha=0.2, height=0.5)
    ax.barh(0, value, color=palette[0], height=0.5)
    ax.plot([target], [0], marker="|", color=th["text"], markersize=18)
    ax.set_yticks([])
    ax.set_xlim(0, target * 1.1)
    ax.text(value, 0.45, f"{value:,.0f}", fontsize=9, color=th["text"])


def _render_progress(ax, labels, series, df, th, palette):
    vals = next(iter(series.values()), [])
    value = vals[0] if vals else 0.0
    frac = max(0.0, min(1.0, value / 100.0))
    ax.barh(0, 100, color=palette[0], alpha=0.15, height=0.5)
    ax.barh(0, frac * 100, color=palette[0], height=0.5)
    ax.set_xlim(0, 100)
    ax.set_yticks([])
    ax.text(frac * 100, 0.45, f"{value:,.0f}%", fontsize=10, color=th["text"])


def _render_sankey(ax, labels, series, df, th, palette):
    from matplotlib.sankey import Sankey
    vals = next(iter(series.values()), [])
    if not vals:
        return
    total = sum(vals)
    if total == 0:
        return
    flows = vals + [-total]
    lab = labels + ["Total"]
    sankey = Sankey(ax=ax, scale=1.0 / total, offset=0.2, head_angle=120,
                    format="%.0f")
    sankey.add(flows=flows, labels=lab,
               orientations=[0] * len(vals) + [1], fc=palette[0], alpha=0.6)
    ax.axis("off")


def _render_sunburst(ax, labels, series, df, th, palette):
    cols = list(df.columns)
    if len(cols) >= 3:
        outer: Dict[str, float] = {}
        inner: Dict[Tuple[str, str], float] = {}
        for _, row in df.iterrows():
            o = str(row[cols[0]])[:20]
            i = str(row[cols[1]])[:20]
            outer[o] = outer.get(o, 0.0) + _num(row[cols[2]])
            inner[(o, i)] = inner.get((o, i), 0.0) + _num(row[cols[2]])
        outer_vals = list(outer.values())
        outer_lab = list(outer.keys())
        inner_items = sorted(inner.items(), key=lambda kv: -kv[1])
        inner_vals = [v for _, v in inner_items]
        inner_lab = [f"{o} / {i}" for (o, i) in inner_items]
        ax.pie(outer_vals, radius=1.0, colors=palette[:len(outer_vals)],
               labels=outer_lab, labeldistance=1.08, textprops={"fontsize": 7})
        ax.pie(inner_vals, radius=0.6, colors=palette[:len(inner_vals)],
               textprops={"fontsize": 6})
    else:
        vals = next(iter(series.values()), [])
        ax.pie(vals, radius=1.0, colors=palette[:len(vals)], labels=labels,
               textprops={"fontsize": 8})
    ax.axis("equal")


def _render_wordcloud(ax, labels, series, df, th, palette):
    vals = next(iter(series.values()), [])
    if not vals:
        return
    pairs = sorted(zip(labels, vals), key=lambda p: -p[1])[:15]
    names = [p[0] for p in pairs][::-1]
    counts = [p[1] for p in pairs][::-1]
    ax.barh(range(len(names)), counts, color=palette[0])
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=8)


def _render_venn(ax, labels, series, df, th, palette):
    from matplotlib.patches import Circle
    vals = next(iter(series.values()), [])
    if len(vals) < 2:
        return
    a, b = vals[0], vals[1]
    ab = vals[2] if len(vals) > 2 else min(a, b) * 0.3
    ax.set_xlim(-1.6, 1.6)
    ax.set_ylim(-1.2, 1.2)
    ax.axis("off")
    ax.add_patch(Circle((-0.5, 0), 1.0, facecolor=palette[0], alpha=0.5, edgecolor="none"))
    ax.add_patch(Circle((0.5, 0), 1.0, facecolor=palette[1], alpha=0.5, edgecolor="none"))
    ax.text(-0.9, 0, f"{a - ab:,.0f}", ha="center", va="center", fontsize=10, color="white")
    ax.text(0.9, 0, f"{b - ab:,.0f}", ha="center", va="center", fontsize=10, color="white")
    ax.text(0, 0, f"{ab:,.0f}", ha="center", va="center", fontsize=10, color="white")
    ax.text(-0.5, 1.05, labels[0] if labels else "A", ha="center", fontsize=9, color=th["text"])
    ax.text(0.5, 1.05, labels[1] if len(labels) > 1 else "B", ha="center", fontsize=9, color=th["text"])


def _render_combo(ax, labels, series, df, th, palette):
    cols = list(df.columns)
    vals = next(iter(series.values()), [])
    ax.bar(range(len(vals)), vals, color=palette[0], width=0.5)
    if len(cols) >= 3:
        ax2 = ax.twinx()
        ax2.plot(range(len(vals)), [_num(v) for v in df[cols[2]].tolist()],
                 color=palette[1], linewidth=2, marker="o", markersize=4)
        ax2.tick_params(colors=th["text"], labelsize=8)
    ax.set_xticks(range(len(vals)))
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)


def _render_population(ax, labels, series, df, th, palette):
    vals = next(iter(series.values()), [])
    y = list(range(len(vals)))
    ax.barh(y, [-v for v in vals], color=palette[0], height=0.6)
    ax.barh(y, vals, color=palette[1], height=0.6)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8)


_DISPATCH = {
    "bar": lambda ax, l, s, df, th, p: _render_bar(ax, l, s, df, th, p, horizontal=True),
    "column": lambda ax, l, s, df, th, p: _render_bar(ax, l, s, df, th, p, horizontal=False),
    "horizontalbar": lambda ax, l, s, df, th, p: _render_bar(ax, l, s, df, th, p, horizontal=True),
    "stackedbar": lambda ax, l, s, df, th, p: _render_bar(ax, l, s, df, th, p, horizontal=True, stacked=True),
    "stackedcolumn": lambda ax, l, s, df, th, p: _render_bar(ax, l, s, df, th, p, horizontal=False, stacked=True),
    "percentbar": lambda ax, l, s, df, th, p: _render_bar(ax, l, s, df, th, p, horizontal=True, stacked=True, percent=True),
    "line": lambda ax, l, s, df, th, p: _render_line(ax, l, s, df, th, p),
    "area": lambda ax, l, s, df, th, p: _render_line(ax, l, s, df, th, p, area=True),
    "stackedarea": lambda ax, l, s, df, th, p: _render_line(ax, l, s, df, th, p, area=True, stacked=True),
    "slope": lambda ax, l, s, df, th, p: _render_line(ax, l, s, df, th, p),
    "stream": lambda ax, l, s, df, th, p: _render_line(ax, l, s, df, th, p, area=True, stacked=True),
    "sparkline": lambda ax, l, s, df, th, p: _render_line(ax, l, s, df, th, p, spark=True),
    "pie": lambda ax, l, s, df, th, p: _render_pie(ax, l, s, df, th, p),
    "donut": lambda ax, l, s, df, th, p: _render_pie(ax, l, s, df, th, p, donut=True),
    "doughnut": lambda ax, l, s, df, th, p: _render_pie(ax, l, s, df, th, p, donut=True),
    "scatter": lambda ax, l, s, df, th, p: _render_scatter(ax, l, s, df, th, p),
    "bubble": lambda ax, l, s, df, th, p: _render_scatter(ax, l, s, df, th, p, bubble=True),
    "radar": lambda ax, l, s, df, th, p: _render_radar(ax, l, s, df, th, p),
    "polar": lambda ax, l, s, df, th, p: _render_polar(ax, l, s, df, th, p),
    "heatmap": lambda ax, l, s, df, th, p: _render_heatmap(ax, l, s, df, th, p),
    "histogram": lambda ax, l, s, df, th, p: _render_histogram(ax, l, s, df, th, p),
    "boxplot": lambda ax, l, s, df, th, p: _render_boxplot(ax, l, s, df, th, p),
    "funnel": lambda ax, l, s, df, th, p: _render_funnel(ax, l, s, df, th, p),
    "treemap": lambda ax, l, s, df, th, p: _render_treemap(ax, l, s, df, th, p),
    "waterfall": lambda ax, l, s, df, th, p: _render_waterfall(ax, l, s, df, th, p),
    "gauge": lambda ax, l, s, df, th, p: _render_gauge(ax, l, s, df, th, p),
    "radial": lambda ax, l, s, df, th, p: _render_gauge(ax, l, s, df, th, p),
    "bullet": lambda ax, l, s, df, th, p: _render_bullet(ax, l, s, df, th, p),
    "progress": lambda ax, l, s, df, th, p: _render_progress(ax, l, s, df, th, p),
    "sankey": lambda ax, l, s, df, th, p: _render_sankey(ax, l, s, df, th, p),
    "sunburst": lambda ax, l, s, df, th, p: _render_sunburst(ax, l, s, df, th, p),
    "wordcloud": lambda ax, l, s, df, th, p: _render_wordcloud(ax, l, s, df, th, p),
    "venn": lambda ax, l, s, df, th, p: _render_venn(ax, l, s, df, th, p),
    "combo": lambda ax, l, s, df, th, p: _render_combo(ax, l, s, df, th, p),
    "population": lambda ax, l, s, df, th, p: _render_population(ax, l, s, df, th, p),
    "pyramid": lambda ax, l, s, df, th, p: _render_population(ax, l, s, df, th, p),
}


def render_chart(chart_type: str, df: pd.DataFrame, w: Optional[Dict[str, Any]] = None,
                 width: int = 900, height: int = 420, dpi: int = 100,
                 theme: str = "auto") -> bytes:
    """Render a chart visual to PNG bytes. Never returns None.

    Empty or unusable data produces a styled "No data" panel so callers can
    always embed an image instead of falling back to a data table.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cfg = {}
    if isinstance(w, dict):
        cfg = w.get("config") or {}
    theme_name = str(cfg.get("theme") or theme or "auto").lower()
    th = _resolve_theme(theme_name)
    ct = str(chart_type or "").lower()

    fig, ax = plt.subplots(figsize=(width / dpi, height / dpi), dpi=dpi)
    fig.patch.set_facecolor(th["bg"])
    ax.set_facecolor(th["bg"])
    for s in ax.spines.values():
        s.set_color(th["grid"])
    ax.tick_params(colors=th["text"], labelsize=8)
    ax.yaxis.grid(True, linestyle="--", alpha=0.3, color=th["grid"])
    ax.set_axisbelow(True)

    labels, series, _ = _extract(df)
    if not labels or not series or all(not v for v in series.values()):
        _draw_empty(ax, th)
    else:
        renderer = _DISPATCH.get(ct, _DISPATCH["column"])
        try:
            renderer(ax, labels, series, df, th, PALETTE)
        except Exception:
            try:
                _render_bar(ax, labels, series, df, th, PALETTE)
            except Exception:
                _draw_empty(ax, th)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, facecolor=th["bg"])
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def render_table(df: pd.DataFrame, width: int = 900, height: int = 420,
                 dpi: int = 100, theme: str = "auto", max_rows: int = 20) -> bytes:
    """Render a DataFrame as a styled table PNG (used for table visuals on
    the Excel dashboard sheet)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    th = _resolve_theme(theme)
    fig, ax = plt.subplots(figsize=(width / dpi, height / dpi), dpi=dpi)
    fig.patch.set_facecolor(th["bg"])
    ax.axis("off")

    data = df.head(max_rows)
    cols = [str(c)[:20] for c in data.columns]
    rows = [[str(v)[:24] if v is not None else "" for v in row] for row in data.values]
    tbl = ax.table(cellText=rows, colLabels=cols, loc="center", cellLoc="left")
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(8)
    tbl.scale(1, 1.4)
    stripe = "#F3F4F6" if th["bg"] == "#FFFFFF" else "#2A2A3C"
    for (r, c), cell in tbl.get_celld().items():
        cell.set_edgecolor(th["grid"])
        if r == 0:
            cell.set_facecolor(PALETTE[0])
            cell.set_text_props(color="white", fontweight="bold")
        else:
            cell.set_facecolor(th["bg"] if r % 2 else stripe)
            cell.set_text_props(color=th["text"])

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, facecolor=th["bg"])
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()