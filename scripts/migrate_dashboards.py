"""
migrate_dashboards.py — normalize stored dashboards to the canonical v2 shape.

Stored rows (from the pre-rebuild dashboards) deviate from the canonical widget
schema ``{id, type, title, col, row, cols, rows, config}``:

* widgets may be missing ``id`` / ``col`` / ``row`` (the grid auto-packed them)
* ``layout`` may carry a nested ``plan`` / ``kpiOverrides`` / ``size`` duplicate
  of the real ``widgets`` list (two sources of truth)
* widget ``config`` may contain leaked render-time preview blobs (``value``,
  ``labels``, ``data``, ``datasets``, ``matrix``, ``chartType``, ...) from the
  old sanitizer
* ``layout.page`` is absent (the exporter will need a fixed page size)

What this script does (idempotent):
  1. drop ``layout.plan`` / ``kpiOverrides`` / ``size`` (widgets column is the
     single source of truth)
  2. backfill ``id`` (uuid), ``col``/``row`` (left-to-right pack in a 12-col
     grid), ``cols``/``rows`` (defaults) on every widget
  3. strip render-only keys from ``widget.config``
  4. seed ``layout.page = {width: 1280, height: 720}``

Usage:
    python scripts/migrate_dashboards.py          # dry run — show what changes
    python scripts/migrate_dashboards.py --apply  # write changes to the DB
"""

import argparse
import json
import sys
import uuid
from pathlib import Path

# Project root so `from config import settings` resolves when run from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.db import get_connection  # noqa: E402

COLUMNS = 12
DEFAULT_PAGE = {"width": 1280, "height": 720}

# Render-time preview artifacts the old renderer wrote into widget.config.
# These must never be persisted/exported.
_PREVIEW_KEYS = {
    "value", "data", "labels", "datasets", "matrix", "dataPoints",
    "xLabels", "yLabels", "chartType", "sparklineData", "trend",
    "columns", "rows", "kpiIndex",
    "agg", "sort", "limit", "sparkSql",
}


def _strip_preview(config: dict) -> dict:
    clean = {}
    for k, v in (config or {}).items():
        if k.startswith("_") or k in _PREVIEW_KEYS:
            continue
        clean[k] = v
    return clean


def _rect(w: dict) -> tuple:
    cols = max(1, int(w.get("cols") or 4))
    rows = max(1, int(w.get("rows") or 2))
    col = max(1, int(w.get("col") or 1))
    row = max(1, int(w.get("row") or 1))
    return (col, col + cols - 1, row, row + rows - 1)


def _overlaps(a: tuple, b: tuple) -> bool:
    return a[0] <= b[1] and b[0] <= a[1] and a[2] <= b[3] and b[2] <= a[3]


def resolve_layout(widgets: list, columns: int = COLUMNS) -> None:
    """Give every widget a collision-free {col,row} in place.

    Mirrors web/ts/bidashboard/grid.ts ``resolveLayout``: widgets without a
    position are band-packed left-to-right; widgets with an explicit position
    keep their column intent but are pushed down until free (heals layouts
    saved by the old ``row += 1`` packer). Also clamps widgets inside the
    1..columns band.
    """
    placed: list = []
    band_row, band_col, band_h = 1, 1, 0
    for w in widgets:
        w["cols"] = max(1, int(w.get("cols") or 4))
        w["rows"] = max(1, int(w.get("rows") or 2))
        if w["cols"] > columns:
            w["cols"] = columns
        if w.get("row") is not None and w.get("col") is not None:
            w["col"] = max(1, min(int(w["col"]), columns - w["cols"] + 1))
            rect = _rect(w)
            guard = 0
            while guard < 200 and any(_overlaps(rect, p) for p in placed):
                w["row"] = int(w["row"]) + 1
                rect = _rect(w)
                guard += 1
            placed.append(rect)
        else:
            if band_col + w["cols"] - 1 > columns and band_col > 1:
                band_row += band_h
                band_col, band_h = 1, 0
            w["row"], w["col"] = band_row, band_col
            rect = _rect(w)
            guard = 0
            while guard < 200 and any(_overlaps(rect, p) for p in placed):
                w["row"] = int(w["row"]) + 1
                rect = _rect(w)
                guard += 1
            placed.append(rect)
            band_col += w["cols"]
            band_h = max(band_h, w["rows"])


def _pack_widgets(widgets: list, repack: bool = False) -> list:
    """Backfill id / cols/rows defaults, strip preview keys, then give every
    widget a collision-free position (see ``resolve_layout``)."""
    out = []
    for w in widgets or []:
        w = dict(w)
        if not w.get("id"):
            w["id"] = "w" + uuid.uuid4().hex[:10]
        w["cols"] = int(w.get("cols") or 4)
        w["rows"] = int(w.get("rows") or 2)
        if repack:
            w.pop("col", None)
            w.pop("row", None)
        w["config"] = _strip_preview(w.get("config") or {})
        out.append(w)
    resolve_layout(out)
    return out


def _normalize_layout(layout: dict) -> dict:
    layout = dict(layout or {})
    layout.pop("plan", None)
    layout.pop("kpiOverrides", None)
    layout.pop("size", None)
    layout.setdefault("columns", COLUMNS)
    layout.setdefault("row_height", 80)
    layout.setdefault("page", dict(DEFAULT_PAGE))
    return layout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write changes to the DB (default is a dry run)")
    parser.add_argument("--repack", action="store_true",
                        help="drop stored col/row and re-pack every dashboard "
                             "(heals overlapping legacy layouts)")
    args = parser.parse_args()

    con = get_connection()
    rows = con.execute(
        "SELECT id, name, layout, widgets FROM analytics.dashboards"
    ).fetchall()

    changed = 0
    for dash_id, name, layout_raw, widgets_raw in rows:
        layout = json.loads(layout_raw) if isinstance(layout_raw, str) else layout_raw
        widgets = json.loads(widgets_raw) if isinstance(widgets_raw, str) else widgets_raw

        new_layout = _normalize_layout(layout)
        new_widgets = _pack_widgets(widgets, repack=args.repack)

        layout_diff = new_layout != layout
        widgets_diff = json.dumps(new_widgets, sort_keys=True) != json.dumps(widgets, sort_keys=True)
        if not (layout_diff or widgets_diff):
            continue

        changed += 1
        if not args.apply:
            print(f"[DRY] {name!r} ({dash_id[:8]}…): "
                  f"layout_changed={layout_diff} widgets_changed={widgets_diff} "
                  f"({len(new_widgets)} widgets)")
            continue

        con.execute(
            "UPDATE analytics.dashboards SET layout = CAST(? AS JSON), "
            "widgets = CAST(? AS JSON), updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            [json.dumps(new_layout), json.dumps(new_widgets), dash_id],
        )
        print(f"[OK ] {name!r} ({dash_id[:8]}…): normalized")

    print(f"\n{changed} dashboard(s) {'would change' if not args.apply else 'updated'}.")


if __name__ == "__main__":
    main()
