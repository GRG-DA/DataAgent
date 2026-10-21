"""
Power BI (.pbit) export
=======================

Builds a real Power BI Desktop file from a saved dashboard. The output is a
**Power BI Template** (``.pbit``): a ZIP whose parts match what Power BI
Desktop itself writes (verified against Microsoft's sample ``.pbix`` files):

    [Content_Types].xml   (UTF-8 XML)
    Version               "1.31"
    DataMashup            (QDEFF binary)  -> Power Query package (empty calc-only)
    Settings              (UTF-16LE JSON)
    Metadata              (UTF-16LE JSON)
    DiagramLayout         (UTF-16LE JSON)
    DiagramState          (UTF-16LE JSON)
    SecurityBindings      (binary)
    Report/Layout         (UTF-16LE JSON)  -> report page + visuals
    DataModelSchema       (UTF-16LE JSON)  -> TMSL model

Data model — hybrid star schema (mirrors the folder's real Power BI samples)
---------------------------------------------------------------------------
The model mirrors how authentic Power BI dashboards are structured (e.g. the
samples in ``powerbi sample data/``: ``Details`` fact + ``Orders`` dimension +
``Date_Lookup`` + ``Key_Measures``):

* **Fact table(s)** — ``Fact <Source>``: the raw source data table(s) the
  dashboard reads from, full rows. This is the "real dataset" the user can
  build on, and it is what slicers filter through model relationships.
* **Widget tables** — one per dashboard widget holding that widget's exact
  query result, so every visual is guaranteed to have data to plot.
* **Slicer dimension tables** — ``<Field> (Slicer)``: distinct values of each
  slicer field, wired many→one to both the fact table and the matching widget
  tables so the slicers genuinely cross-filter the charts.
* **Date table** — ``Dates``: a calendar built from the fact table's date
  column (like Power BI's ``Date_Lookup`` / auto ``LocalDateTable``).
* **Measures** — explicit DAX measures derived from the dashboard's KPI widgets
  (like the samples' ``Key_Measures``), attached to the fact table.

Every table is an *import* partition whose source is a self-contained
``DATATABLE(...)`` **DAX calculated table** expression. Calculated tables are
part of the model definition and are evaluated by the in-memory VertiPaq engine
at model build — with no data source and no credentials — so the tables carry
data the moment the template is opened in PBI Desktop. (Real templates do the
same: ``Simple.pbit``'s ``DateTableTemplate`` is a ``calculated`` partition.)

The .pbit still ships a ``DataMashup`` part: Power BI Desktop's open/refresh
pipeline requires it even for a calculated-only model. Since there are no M
queries, we embed the empty ``section Section1;`` mashup verbatim from a genuine
calc-only Power BI Desktop file (``_CALC_ONLY_MASHUP``).

Report
------
One page mirrors the dashboard: a title, the insight banner, a left slicer
rail and the widget visuals (bar/line/pie/area/scatter -> Power BI
clusteredColumnChart/lineChart/donutChart/areaChart/scatterChart, KPI -> card,
table -> tableEx).

**Why visuals must carry ``query`` + ``dataTransforms``** (the "no data" bug):
Power BI Desktop renders a visual from the ``SemanticQueryDataShapeCommand`` in
the visualContainer's top-level ``query`` field, plus the metadata in
``dataTransforms``. Real Power BI files attach both to every data visual.
Omitting them (as an earlier regression did) makes PBI Desktop render the
visual empty. ``add_vc()`` therefore attaches them for every data visual, using
the same semantic-query shape Power BI Desktop itself writes.
"""

import base64
import datetime as _dt
import io
import json
import logging
import math
import re
import uuid as _uuid
import struct
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# Power BI's default categorical palette (also used by the dashboard UI).
PBI_PALETTE = ["#118DFF", "#12239E", "#E66C37", "#6B007B", "#E044A7",
               "#744EC2", "#D9B300", "#D64550", "#197278", "#7A7A7A"]

_PBIX_VERSION = "1.31"
_CREATED_FROM_RELEASE = "2026.07"

# Relationships between slicer dims and widget tables are valid TMSL but PBI
# Desktop's internal column-ID resolution fails on them ("invalid column ID N")
# for hand-built models, so they are OFF until the visuals render. The visual
# queries bind by name, so charts render fine without relationships.
_EMIT_RELATIONSHIPS = False

# SecurityBindings part — the fixed bytes Power BI Desktop writes in every
# .pbit/.pbix (captured verbatim from the real working Simple.pbit template).
_SECURITY_BINDINGS = bytes.fromhex(
    "01000000d08c9ddf0115d1118c7a00c04fc297eb01000000daf240b72c012e47985439"
    "bc00a95ae90000000002000000000003660000c000000010000000e34b4f0cf3f6ba06104f"
    "227da3deb3530000000004800000a00000001000000021d16f9b36ad07f506a93018439353"
    "5548000000560669e4859ee57c7cc391609d032a41988b78b8de2907fce4a80d7fb62dde6d"
    "fb52d935a1feec5dac135a4461fe608b152094e07acd7c625b5ee945cf1d939f4ba4d83d321"
    "39f7214000000ac298e6cbb796392d5951f737c34421238cbdfab"
)

# DataMashup part for a calculated-tables-only model — captured verbatim from
# ``empty-schema-calc-only.pbix`` (a genuine Power BI Desktop file whose model,
# like ours, has only DAX ``DATATABLE`` calculated partitions and no M tables).
# Power BI Desktop's open/refresh pipeline requires a DataMashup part even when
# there is no Power Query mashup: it ships a minimal inner package whose
# ``Formulas/Section1.m`` is just ``section Section1;`` (the empty M section).
# Hand-building the QDEFF stream (old ``_build_datamashup``) produced files that
# still rendered only the title, so this known-good byte blob is used verbatim.
_CALC_ONLY_MASHUP = base64.b64decode(
    "AAAAABoDAABQSwMEFAACAAgA2lCUTR6zoVKqAAAA+gAAABIAHABDb25maWcvUGFja2FnZS54bWwgohgAKKAU"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAhY9NDoIwFISvQrrntaUBf/IoC7eSmBCNWwIVGqEYKJa7ufBIXkETxbhzN/PlW8w8"
    "bndMprbxrqofdGdiwoERT5miK7WpYjLak78kicRdXpzzSnkv2QzraShjUlt7WVPqnAMnoOsrGjDG6THdZkWt2px8Zf1f9rUZbG4"
    "KRSQe3mNkAFEIoeACFowjnTGm2syZQwgiWEXAkP5g3IyNHXsllfH3GdK5Iv38kE9QSwMEFAACAAgA2lCUTQ/K6aukAAAA6QAA"
    "ABMAHABbQ29udGVudF9UeXBlc10ueG1sIKIYACigFAAAAAAAAAAAAAAAAAAAAAAAAAAAAG2OSw7CMAxErxJ5n7qwQAg1ZQHcg"
    "AtEwf2I5qPGReFsLDgSVyBtd4ilZ+Z55vN6V8dkB/GgMfbeKdgUJQhyxt961yqYuJF7ONbV9Rkoihx1UUHHHA6I0XRkdSx8IJ"
    "edxo9Wcz7HFoM2d90Sbstyh8Y7JseS5x9QV2dq9DSwuKQsr7UZB3Fac3OVAqbEuMj4l7A/eR3C0BvN2cQkbZR2IXEZXn8BUEsDB"
    "BQAAgAIANpQlE0oike4DgAAABEAAAATABwARm9ybXVsYXMvU2VjdGlvbjEubSCiGAAooBQAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AArTk0uyczPUwiG0IbWAFBLAQItABQAAgAIANpQlE0es6FSqgAAAPoAAAASAAAAAAAAAAAAAAAAAAAAAABDb25maWcvUGFja2"
    "FnZS54bWxQSwECLQAUAAIACADaUJRND8rpq6QAAADpAAAAEwAAAAAAAAAAAAAAAAD2AAAAW0NvbnRlbnRfVHlwZXNdLnhtbFBLA"
    "QItABQAAgAIANpQlE0oike4DgAAABEAAAATAAAAAAAAAAAAAAAAAOcBAABGb3JtdWxhcy9TZWN0aW9uMS5tUEsFBgAAAAADAAMA"
    "wgAAAEICAAAAABABAADvu788P3htbCB2ZXJzaW9uPSIxLjAiIGVuY29kaW5nPSJ1dGYtOCI/PjxQZXJtaXNzaW9uTGlzdCB4bWx"
    "uczp4c2Q9Imh0dHA6Ly93d3cudzMub3JnLzIwMDEvWE1MU2NoZW1hIiB4bWxuczp4c2k9Imh0dHA6Ly93d3cudzMub3JnLzIwMD"
    "EvWE1MU2NoZW1hLWluc3RhbmNlIj48Q2FuRXZhbHVhdGVGdXR1cmVQYWNrYWdlcz5mYWxzZTwvQ2FuRXZhbHVhdGVGdXR1cmVQY"
    "WNrYWdlcz48RmlyZXdhbGxFbmFibGVkPnRydWU8L0ZpcmV3YWxsRW5hYmxlZD48L1Blcm1pc3Npb25MaXN0PpcBAAAAAAAAdQEA"
    "AO+7vzw/eG1sIHZlcnNpb249IjEuMCIgZW5jb2Rpbmc9InV0Zi04Ij8+PExvY2FsUGFja2FnZU1ldGFkYXRhRmlsZSB4bWxuczp4"
    "c2Q9Imh0dHA6Ly93d3cudzMub3JnLzIwMDEvWE1MU2NoZW1hIiB4bWxuczp4c2k9Imh0dHA6Ly93d3cudzMub3JnLzIwMDEvWE1M"
    "U2NoZW1hLWluc3RhbmNlIj48SXRlbXM+PEl0ZW0+PEl0ZW1Mb2NhdGlvbj48SXRlbVR5cGU+QWxsRm9ybXVsYXM8L0l0ZW1UeX"
    "BlPjxJdGVtUGF0aCAvPjwvSXRlbUxvY2F0aW9uPjxTdGFibGVFbnRyaWVzPjxFbnRyeSBUeXBlPSJSZWxhdGlvbnNoaXBzIiBWYW"
    "x1ZT0ic0FBQUFBQT09IiAvPjwvU3RhYmxlRW50cmllcz48L0l0ZW0+PC9JdGVtcz48L0xvY2FsUGFja2FnZU1ldGFkYXRhRmlsZ"
    "T4WAAAAUEsFBgAAAAAAAAAAAAAAAAAAAAAAANoAAAABAAAA0Iyd3wEV0RGMegDAT8KX6wEAAACtsPKE+HmcTK0omY7i1TCsA"
    "AAAAAIAAAAAAANmAADAAAAAEAAAAAciFcu0f2x959qE6oV7AWAAAAAABIAAAKAAAAAQAAAAXFIndwZJT49+k5IQRtkcUFAAAAAg"
    "tcJD/awLdMP6SUFzolcqdvCzV4KTYR3odntPFJnnLXXVOG/s0xnBgHq/kFlVCBJKy75rLJnpM22+7C/3XML9YDvJkXVC3N9PPQa"
    "r6tN7mRQAAAAismycM0YuBzbrXSB3U9fjSpBjWw=="
)

# Real Power BI theme (CY24SU02, shipped by Power BI Desktop for 2024+ reports).
# Stored as a static resource under export/BaseThemes/ and embedded into the
# .pbit so the report page renders with a genuine Power BI palette.
_THEME_PATH = Path(__file__).resolve().parent / "BaseThemes" / "CY24SU02.json"
_DARK_THEME_PATH = Path(__file__).resolve().parent / "BaseThemes" / "DarkMode.json"

# Dashboard widget type -> Power BI visual type.
#
# The exotic chart types (gauge/funnel/waterfall/treemap/combo/radar) were added
# with their exact visualType strings and projection role names extracted from
# the genuine sample reports in ``powerbi sample data/``:
#   gauge   -> {"Y": [value]}
#   funnel  -> {"Y": [value], "Category": [dim]}
#   waterfallChart -> {"Category": [dim], "Y": [value]}
#   treemap -> {"Group": [dim], "Values": [value], "Details": [dim2]}
#   lineClusteredColumnComboChart -> {"Category": [x], "Y": [cols], "Y2": [lines]}
#   radarChart -> not present in any local sample (best-effort; Category+Y)
_WIDGET_VISUAL = {
    "kpi": "card",
    "bar": "clusteredColumnChart",
    "column": "clusteredColumnChart",
    "line": "lineChart",
    "pie": "donutChart",
    "donut": "donutChart",
    "area": "areaChart",
    "scatter": "scatterChart",
    "bubble": "scatterChart",
    "heatmap": "clusteredColumnChart",
    "gauge": "gauge",
    "funnel": "funnel",
    "waterfall": "waterfallChart",
    "treemap": "treemap",
    "combo": "lineClusteredColumnComboChart",
    "radar": "radarChart",
    "table": "tableEx",
}

# Native export — all visual types emit their true Power BI visualType.
#
# Previously exotic visuals (gauge/funnel/waterfall/treemap/combo/radar) were
# substituted with closest rendering equivalents because their projections
# were unverified (docs/rebuild-plan-official-powerbi-excel.md). Projections
# are now corrected against the Fabric 2.7.0 schema + Desktop output + MS docs:
#   gauge   -> {"Y": [value]} (+ optional MinValue/MaxValue/TargetValue)
#   funnel  -> {"Category": [dim], "Y": [values]} (visualType "funnel")
#   waterfallChart -> {"Category": [dim], "Y": [values]} (+ optional Breakdown)
#   treemap -> {"Group": [dim], "Values": [values]} (+ optional Details)
#   lineClusteredColumnComboChart -> {"Category": [x], "Y": [cols], "Y2": [lines]}
#   scatterChart -> {"X": [x], "Y": [y]} (+ optional Size) with categoryLabels
#   radarChart -> {"Category": [dim], "Y": [values]} (custom visual may be required)
# Verified structurally via export/verify_export.py (query+dataTransforms,
# role bindings, Binding/OrderBy). A rendered native visual beats a substituted one.
_PBI_SAFE_FALLBACK: Dict[str, str] = {}

# Max rows embedded per widget query / raw source table / fact table / slicer
# (M literals get large, so keep the file size sane).
MAX_WIDGET_ROWS = 5000
MAX_FACT_ROWS = 3000
MAX_SLICER_ROWS = 5000

# Emit the real-PBI semantic-query shape for value roles: ``Aggregation`` +
# ``_AGG_FUNCTION`` (2 = Sum) named ``Sum(Table.Column)``, resolved via the
# model column's ``summarizeBy: "sum"`` implicit measure. When False, value roles
# use a plain ``Column`` select instead (an alternative shape that also renders for
# pre-aggregated widget tables). Flipping this is the quickest iteration lever.
_USE_AGG_QUERIES = True

# Aggregation function code used for Sum in semantic queries and dataTransforms.
# From real PBI files the enum is: 0=None, 1=Count, 2=Sum, 3=Min, 4=Max,
# 5=Average. Verified empirically: Min(...) names carry Function 3, Max(...)
# carry 4, and Sum(...) carries 2. Earlier code emitted 5 (Average) — a card
# over a 1-row DATATABLE then rendered "1" (count-like), not the summed value.
_AGG_FUNCTION = 2

# Roles treated as the chart's category/axis — the "active" data-view item that
# real PBI files list in ``projectionActiveItems`` and mark ``isActive``.
# "Group" is treemap's category role (its projections mark Group active:true).
_CATEGORY_ROLES = ("Category", "X", "Group")

_DUCKDB_GENERATED_RE = re.compile(r'[\(\)<>!@#\$%\^&\*\+=\[\]{}|\\;:\'",\?/~`]')


def _has_generated_name(col: str) -> bool:
    """True if a column name was auto-generated by DuckDB (e.g. ``sum(Profit)``)."""
    return bool(_DUCKDB_GENERATED_RE.search(col))


def _rename_generated_columns(df: pd.DataFrame, widget_type: str) -> Dict[str, str]:
    """Rename DuckDB-generated column names to clean PBI-friendly names.

    Returns the old→new mapping so the caller can update widget config.
    """
    renames: Dict[str, str] = {}
    gen_cols = [(i, c) for i, c in enumerate(df.columns) if _has_generated_name(str(c))]
    if not gen_cols:
        return renames
    used = {str(c) for c in df.columns if not _has_generated_name(str(c))}
    for idx, (_, col) in enumerate(gen_cols):
        if widget_type == "kpi":
            new = "value"
        elif widget_type in ("line", "area"):
            new = "value" if idx > 0 or len(gen_cols) == 1 else "label"
        else:
            new = "label" if idx == 0 else "value"
        base, i = new, 2
        while new in used:
            new = f"{base}_{i}"
            i += 1
        used.add(new)
        renames[str(col)] = new
    if renames:
        df.rename(columns=renames, inplace=True)
    return renames


# ── tiny helpers ─────────────────────────────────────────────────────────

_ILLEGAL_NAME = re.compile(r'[\\\/\[\]"\'\{\}\|<>:?;]|\r|\n|\t')


def _clean_name(name: str, fallback: str = "Table") -> str:
    """Sanitise a string into a safe Power BI object name (keeps unicode)."""
    s = _ILLEGAL_NAME.sub("", str(name or "")).strip()
    s = re.sub(r"\s+", " ", s)
    return s[:120] or fallback


def _unique_name(name: str, used: set) -> str:
    """Return *name* (or name (2), name (3), ...) not present in *used*."""
    base = name
    i = 2
    while name in used:
        name = f"{base} ({i})"
        i += 1
    used.add(name)
    return name


def _safe_column(name: str) -> str:
    """Sanitise a column name into something Power Query identifiers accept."""
    s = _ILLEGAL_NAME.sub("_", str(name or "col")).strip() or "col"
    if not re.match(r"^[A-Za-z_]", s):
        s = "col_" + s
    return s


def _str_literal(text: Any) -> str:
    """Single-quote a string the way PBIX visual literals do: 'text'."""
    return "'" + str(text).replace("'", "''") + "'"


def _expr(value: str) -> Dict[str, Any]:
    """A PBIX-style literal expr used in visual object properties."""
    return {"expr": {"Literal": {"Value": value}}}


def _m_str(value: str) -> str:
    """Escape a Python string into a Power Query string literal."""
    s = str(value).replace('"', '""')
    s = s.replace("\r", "#(cr)").replace("\n", "#(lf)").replace("\t", "#(tab)")
    return '"' + s + '"'


def _to_dt(value: Any) -> Optional[_dt.datetime]:
    """Normalise pandas / numpy / python date-ish values to a datetime."""
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, _dt.datetime):
        return value
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day)
    try:
        return pd.Timestamp(value).to_pydatetime()
    except Exception:
        return None


def _dtype_info(dtype: Any) -> Tuple[str, str]:
    """pandas dtype -> (M column type, TMSL dataType)."""
    name = str(dtype)
    if name.startswith("int"):
        return "Int64.Type", "int64"
    if name.startswith("float"):
        return "number", "double"
    if name.startswith("bool"):
        return "logical", "boolean"
    if name.startswith("datetime") or name.startswith("timestamp") or name.startswith("date"):
        return "datetime", "dateTime"
    return "text", "string"


def _m_value(value: Any, mtype: str) -> str:
    if value is None:
        return "null"
    if mtype == "text":
        return _m_str(value)
    if mtype == "logical":
        return "true" if bool(value) else "false"
    if mtype == "Int64.Type":
        return str(int(value))
    if mtype == "number":
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return "null"
        return repr(f)
    if mtype in ("datetime", "date"):
        dt = _to_dt(value)
        if dt is None:
            return "null"
        if mtype == "date":
            return f"#date({dt.year}, {dt.month}, {dt.day})"
        return f"#datetime({dt.year}, {dt.month}, {dt.day}, {dt.hour}, {dt.minute}, {dt.second})"
    return _m_str(value)


def _m_query(table_name: str, columns: List[Tuple[str, str]], rows: List[tuple]) -> str:
    """Build a self-contained Power Query expression returning *rows*.

    Uses the untyped ``#table(column_names, rows)`` form for maximum
    compatibility across Power BI Desktop versions.
    """
    col_names = ", ".join(f'"{name}"' for name, _ in columns)
    body = ",\n".join(
        "        {" + ", ".join(_m_value(v, mt) for v, (_, mt) in zip(row, columns)) + "}"
        for row in rows
    )
    return (
        f"let\n"
        f"    Source = #table({{ {col_names} }}, {{\n{body}\n    }})\n"
        f"in\n"
        f"    Source"
    )


# ── DAX DATATABLE literals (data without Power Query) ────────────────────
#
# Power BI Desktop does NOT evaluate M ``#table`` partition expressions when
# opening a .pbit that has no ``PowerQueryMashup`` part — the query engine has
# no formula graph to execute, so every table comes back empty and the report
# renders nothing but textboxes. DAX ``DATATABLE`` calculated tables are
# different: they are part of the model definition and are evaluated by the
# in-memory VertiPaq engine at model build, with no data source and no mashup.
# So we ship widget/slicer data as DATATABLE partitions instead of M.

_DAX_DATATABLE_TYPES = {
    "int64": "INTEGER",
    "double": "DOUBLE",
    "boolean": "BOOLEAN",
    "dateTime": "DATETIME",
    "string": "STRING",
}


def _dax_value(value: Any, data_type: str) -> str:
    """Python value -> a DAX DATATABLE literal."""
    if value is None:
        return "BLANK()"
    if data_type == "string":
        return '"' + str(value).replace('"', '""') + '"'
    if data_type == "boolean":
        return "TRUE()" if bool(value) else "FALSE()"
    if data_type == "dateTime":
        dt = _to_dt(value)
        if dt is None:
            return "BLANK()"
        if dt.hour or dt.minute or dt.second:
            return f'"{dt.strftime("%Y-%m-%d %H:%M:%S")}"'
        return f'"{dt.strftime("%Y-%m-%d")}"'
    if data_type == "int64":
        return str(int(value))
    if data_type == "double":
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return "BLANK()"
        return repr(f)
    return '"' + str(value).replace('"', '""') + '"'


def _dax_datatable(columns: List[Tuple[str, str]], rows: List[tuple]) -> str:
    """Build a self-contained DAX ``DATATABLE(...)`` literal returning *rows*.

    ``columns`` is ``[(name, data_type), ...]`` where data_type is the TMSL
    dataType string from ``_dtype_info`` (int64/double/boolean/dateTime/string).
    """
    if not rows:
        # DATATABLE needs at least one row; a BLANK() row yields an empty table.
        rows = [(None,) * len(columns)]
    col_parts = [
        f'    "{name}", {_DAX_DATATABLE_TYPES.get(dtype, "STRING")}'
        for name, dtype in columns
    ]
    body = ",\n".join(
        "        {" + ", ".join(_dax_value(v, dt) for v, (_, dt) in zip(row, columns)) + "}"
        for row in rows
    )
    return "DATATABLE(\n" + ",\n".join(col_parts) + ",\n    {\n" + body + "\n    }\n)"


def _df_to_columns(df: pd.DataFrame) -> List[Tuple[str, str, str]]:
    """Return [(safe_name, m_type, data_type), ...] for a DataFrame.

    Names are sanitised / de-duplicated so the model, the M query and the
    report visuals all agree on the same column identifiers.
    """
    used = set()
    out = []
    for col in df.columns:
        base = _safe_column(col)
        name = base
        i = 2
        while name in used:
            name = f"{base}_{i}"
            i += 1
        used.add(name)
        mtype, data_type = _dtype_info(df[col].dtype)
        out.append((name, mtype, data_type))
    return out


# ── TMSL model (DataModelSchema) ─────────────────────────────────────────

def _model_ts() -> str:
    """ISO-8601 timestamp string (naive UTC) matching real TOM ``modifiedTime``."""
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S.%f")


_NEVER_REFRESHED = "1699-12-31T00:00:00"


def _row_number_column() -> Dict[str, Any]:
    """Hidden rowNumber key column — every real Power BI table carries one."""
    return {
        "type": "rowNumber",
        "name": f"RowNumber-{_hex_name().upper()}",
        "dataType": "int64",
        "isHidden": True,
        "isUnique": True,
        "isKey": True,
        "isNullable": False,
    }


def _format_annotation(column_name: str, data_type: str) -> str:
    """Return the XML Format annotation based on column name hint and data type."""
    name_lower = (column_name or "").lower()
    if data_type in ("int64", "double") and any(kw in name_lower for kw in
            ("price", "cost", "revenue", "sales", "amount", "profit",
             "discount", "value", "total", "income", "expense", "fee", "rate")):
        return '<Format Format="$#,##0.00" />'
    if data_type == "int64":
        return '<Format Format="#,##0" />'
    if data_type == "double":
        return '<Format Format="#,##0.00" />'
    if data_type == "dateTime":
        return '<Format Format="DateTime" />'
    return '<Format Format="General" />'


def _format_string_for(column_name: str, data_type: str) -> str:
    """Return the plain format-string value for the FormatString annotation."""
    name_lower = (column_name or "").lower()
    if data_type in ("int64", "double") and any(kw in name_lower for kw in
            ("price", "cost", "revenue", "sales", "amount", "profit",
             "discount", "value", "total", "income", "expense", "fee", "rate")):
        return "$#,##0.00"
    if data_type == "int64":
        return "#,##0"
    if data_type == "double":
        return "#,##0.00"
    if data_type == "dateTime":
        return "DateTime"
    return "General"


def _data_type_at_refresh(data_type: str) -> str:
    # Real files store the refresh-time data type here (e.g. "double", "Int64").
    return {"int64": "Int64", "double": "double", "boolean": "bool",
            "dateTime": "dateTime", "string": "string"}.get(data_type, "string")


def _build_table(table_name: str, df: pd.DataFrame,
                 measures: Optional[List[Dict[str, Any]]] = None,
                 description: Optional[str] = None) -> Dict[str, Any]:
    """TMSL table object: columns + one *calculated* partition with DAX DATATABLE data.

    The partition uses ``source.type: "calculated"`` with a self-contained
    ``DATATABLE(...)`` DAX expression. DAX calculated tables are part of the
    model definition and are evaluated by the in-memory VertiPaq engine at
    model build — no Power Query mashup is required — so the table carries data
    the moment the template opens in PBI Desktop. (This mirrors the real
    ``Simple.pbit`` template's own ``DateTableTemplate`` calculated table, and
    avoids the fragile QDEFF mashup that produced the "only title" output.)

    The table mirrors the structure Power BI Desktop writes: a hidden
    ``rowNumber`` key column, per-column ``attributeHierarchy`` + annotations,
    and partition ``state``/``modifiedTime``/``refreshedTime``.

    ``measures`` (optional) attaches explicit DAX measures to the table —
    the standard TMSL way to hold measures, like the samples' ``Key_Measures``.
    """
    ts = _model_ts()
    cols = _df_to_columns(df)  # [(name, m_type, data_type)]
    columns = [
        {
            "name": name,
            "dataType": data_type,
            "sourceColumn": name,
            "summarizeBy": "none" if data_type == "string" else "sum",
            "attributeHierarchy": {"state": "ready"},
            "annotations": [
                {"name": "SummarizationSetBy", "value": "Automatic"},
                {"name": "Format", "value": _format_annotation(name, data_type)},
                {"name": "FormatString", "value": _format_string_for(name, data_type)},
                {"name": "DataTypeAtRefresh", "value": _data_type_at_refresh(data_type)},
            ],
        }
        for name, _m, data_type in cols
    ]
    columns.insert(0, _row_number_column())
    rows = list(df.itertuples(index=False, name=None)) if len(df) else []
    expression = _dax_datatable([(n, t) for n, _m, t in cols], rows)
    table = {
        "name": table_name,
        "description": description or "",
        "modifiedTime": ts,
        "structureModifiedTime": ts,
        "columns": columns,
        "partitions": [
            {
                "name": table_name,
                "mode": "import",
                "state": "ready",
                "modifiedTime": ts,
                "refreshedTime": _NEVER_REFRESHED,
                "source": {"type": "calculated", "expression": expression},
            }
        ],
    }
    # Auto-generate a DAX ``SUM`` measure for each numeric column.  PBI Desktop
    # resolves visuals' ``Measure`` references through these — the model column's
    # ``summarizeBy: "sum"`` property alone is *not* sufficient when the table
    # uses a ``DATATABLE`` calculated partition (PBI ignores it for calc tables).
    # Measure names must be globally unique across the Model (not just per-table),
    # so we prefix with the table name.
    _safe = lambda s: re.sub(r'[^A-Za-z0-9_]', '_', s)
    auto_measures = [
        {
            "name": f"{_safe(table_name)}_{_safe(name)}",
            "expression": f"SUM('{table_name}'[{name}])",
            "annotations": [
                {"name": "Format", "value": _format_annotation(name, data_type)},
                {"name": "SummarizationSetBy", "value": "Automatic"},
            ],
        }
        for name, _m, data_type in cols
        if data_type != "string"
    ]
    if auto_measures:
        table["measures"] = auto_measures
    elif measures:
        table["measures"] = measures
    return table


def _build_relationship(rel_id: str, from_table: str, from_col: str,
                        to_table: str, to_col: str) -> Dict[str, Any]:
    """Build a TMSL many-to-one relationship.

    PBI Desktop requires ``fromCardinality`` = ``many`` (the many/fact side must
    come first) and ``toCardinality`` = ``one`` (the dim/unique side comes second).
    """
    return {
        "name": rel_id,
        "fromTable": from_table,
        "fromColumn": from_col,
        "fromCardinality": "many",
        "toTable": to_table,
        "toColumn": to_col,
        "toCardinality": "one",
        "crossFilteringBehavior": "oneDirection",
        "securityFilteringBehavior": "oneDirection",
        "isActive": True,
        "joinOnDateBehavior": "datePartOnly",
    }


def _build_model(model_name: str, tables: List[Dict[str, Any]],
                 relationships: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    ts = _model_ts()
    return {
        "name": _clean_name(model_name, "Dashboard"),
        "compatibilityLevel": 1550,
        "model": {
            "culture": "en-US",
            "modifiedTime": ts,
            "structureModifiedTime": ts,
            "dataAccessOptions": {
                "legacyRedirects": True,
                "returnErrorValuesAsNull": True,
            },
            "defaultPowerBIDataSourceVersion": "powerBI_V3",
            "sourceQueryCulture": "en-US",
            "dataSources": [],
            "relationships": relationships or [],
            "tables": tables,
            "annotations": [],
        },
    }


# ── DAX measures (best-effort, non-fatal) ────────────────────────────────

def _dax_from_kpi_sql(sql: str, fact_name: str, col_map: Dict[str, str]) -> Optional[str]:
    """Convert KPI-style DuckDB SQL into a DAX expression on the fact table.

    Handles ``SUM(x)``, ``AVG(x)``, ``MIN(x)``, ``MAX(x)``, ``COUNT(x)``,
    ``COUNT(DISTINCT x)`` and simple arithmetic of those. Returns ``None`` when
    the expression can't be mapped (column not in the fact table, etc.) so the
    caller can skip the measure silently.
    """
    if not sql or "SELECT" not in sql.upper():
        return None
    m = re.search(r"\bSELECT\b(.+?)\bFROM\b", sql, re.I | re.S)
    if not m:
        return None
    expr = m.group(1).strip()
    # Drop the trailing "AS alias" (optional).
    expr = re.sub(r"\bAS\s+[\"`]?[A-Za-z_][\w]*[\"`]?\s*$", "", expr, flags=re.I).strip()
    expr = expr.rstrip(";").strip()

    failed = [False]  # list so the closure can set it

    def repl(match: "re.Match") -> str:
        fn = match.group(1).upper()
        inner = match.group(2).strip()
        distinct = re.match(r"(?i)DISTINCT\s+(.+)", inner)
        col_raw = (distinct.group(1) if distinct else inner).strip().strip('"').strip("`")
        safe = col_map.get(col_raw)
        if not safe:
            failed[0] = True  # unmapped column → caller rejects below
            return match.group(0)
        ref = f"'{fact_name}'[{safe}]"
        if fn == "SUM":
            return f"SUM({ref})"
        if fn == "AVG":
            return f"AVERAGE({ref})"
        if fn == "MIN":
            return f"MIN({ref})"
        if fn == "MAX":
            return f"MAX({ref})"
        if fn == "COUNT":
            if distinct:
                return f"DISTINCTCOUNT({ref})"
            return f"COUNTROWS('{fact_name}')"
        failed[0] = True
        return match.group(0)

    out = re.sub(r"\b(SUM|AVG|MIN|MAX|COUNT)\s*\(((?:[^()])*)\)", repl, expr, flags=re.I)
    if failed[0]:
        return None
    out = re.sub(r"\s+", " ", out).strip()
    return out or None


def _derive_fact_measures(widgets: List[Dict[str, Any]],
                          fact_name: str,
                          col_map: Dict[str, str]) -> List[Dict[str, Any]]:
    """Derive TMSL measures on the fact table from the dashboard's KPI widgets."""
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for w in widgets or []:
        if str(w.get("type", "")).lower() != "kpi":
            continue
        title = str(w.get("title") or "").strip()
        sql = str((w.get("config") or {}).get("sql") or "").strip()
        if not title or not sql:
            continue
        expr = _dax_from_kpi_sql(sql, fact_name, col_map)
        if not expr:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "name": title[:80],
            "expression": expr,
            "formatString": "#,##0.00",
            "dataType": "double",
        })
    return out


def _build_date_table(fact_name: str, date_col: str, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """Build a ``Dates`` calendar table from a fact table's date column.

    Mirrors Power BI's ``Date_Lookup`` / auto ``LocalDateTable``: Date, Year,
    Month, Quarter, MonthName, DayOfWeek, Day. Returns ``None`` if no date
    column is usable.
    """
    try:
        dates = df[date_col].dropna()
        if dates.empty:
            return None
        ts = pd.to_datetime(dates).dt.normalize().drop_duplicates().sort_values()
        cal = pd.DataFrame({
            "Date": ts,
            "Year": ts.dt.year,
            "Month": ts.dt.month,
            "Quarter": ((ts.dt.month - 1) // 3) + 1,
            "MonthName": ts.dt.strftime("%B"),
            "DayOfWeek": ts.dt.strftime("%A"),
            "Day": ts.dt.day,
        })
    except Exception as e:
        logger.warning("PowerBI export: date table build failed: %s", e)
        return None
    return _build_table("Dates", cal, description="Date calendar for time intelligence")


# ── Report visuals (singleVisual) ────────────────────────────────────────

def _query_select(alias: str, table: str, column: str, aggregate: bool = False) -> Dict[str, Any]:
    """Build a Select entry for the semantic query.

    Non-aggregate (category/axis) → plain ``Column``, name ``Table.Column``.
    Aggregate (value) → real-PBI ``Measure`` reference resolved through a DAX
    ``SUM`` measure defined on the table. ``_build_table`` auto-generates these
    measures for every numeric column, prefixed with the table name for
    global uniqueness (PBI Desktop requires measure names to be unique across
    the entire Model, not just per-table).  The ``Name`` must equal the
    projection ``queryRef`` so PBI Desktop can resolve the binding.
    """
    _safe = lambda s: re.sub(r'[^A-Za-z0-9_]', '_', s)
    measure_name = f"{_safe(table)}_{_safe(column)}" if aggregate and _USE_AGG_QUERIES else None
    source_ref = {"SourceRef": {"Source": alias}}
    if measure_name:
        return {
            "Measure": {
                "Expression": source_ref,
                "Property": measure_name,
            },
            "Name": f"{table}.{column}",
            "NativeReferenceName": column,
        }
    return {
        "Column": {"Expression": source_ref, "Property": column},
        "Name": f"{table}.{column}",
        "NativeReferenceName": column,
    }


def _ref(table: str, column: str, aggregate: bool) -> str:
    return f"{table}.{column}" if (aggregate and _USE_AGG_QUERIES) else f"{table}.{column}"


def _chart_visual(visual_type: str, table: str, columns: List[Tuple[str, bool]],
                  roles: Dict[str, Any], title: str,
                  column_props: Optional[Dict[str, Dict[str, Any]]] = None,
                  objects: Optional[Dict[str, Any]] = None,
                  style: Optional[Dict[str, Any]] = None,
                  series_refs: Optional[List[str]] = None) -> Dict[str, Any]:
    """Build a singleVisual for chart/card/slicer/table visuals.

    ``columns``: (column_name, aggregate) pairs included in the query.
    ``roles``:   role name -> column name or list of column names (RAW names,
                 NOT queryRefs — the queryRefs are derived from ``columns`` so
                 projections and Select entries always agree).
    ``style``:   optional ``config.style`` dict; its formatting is emitted as
                 visual ``objects`` (legend, labels, axes, background, colors).
    """
    alias = "t"
    select = [_query_select(alias, table, c, agg) for c, agg in columns]
    refs = {c: _ref(table, c, agg) for c, agg in columns}
    projections: Dict[str, Any] = {}
    for role, cols in roles.items():
        cols_list = cols if isinstance(cols, list) else [cols]
        projections[role] = [
            {"queryRef": refs[c], "active": True} if role in ("Category", "Values", "X", "Group") else {"queryRef": refs[c]}
            for c in cols_list
        ]
    sv: Dict[str, Any] = {
        "visualType": visual_type,
        "projections": projections,
        "prototypeQuery": {
            "Version": 2,
            "From": [{"Name": alias, "Entity": table, "Type": 0}],
            "Select": select,
        },
        "drillFilterOtherVisuals": True,
    }
    if column_props:
        sv["columnProperties"] = column_props
    if objects:
        sv["objects"] = objects
    elif style:
        sv["objects"] = _style_objects(style)
    elif visual_type not in _NON_CHART_TYPES:
        sv["objects"] = _chart_objects(with_axes=visual_type in _AXIS_VISUALS)
    # Official PBI line charts always show markers on data points.
    if visual_type in ("lineChart", "lineClusteredColumnComboChart",
                       "lineStackedColumnComboChart", "areaChart",
                       "stackedAreaChart", "hundredPercentStackedAreaChart"):
        objs = sv.setdefault("objects", {})
        objs.setdefault("lineStyles", [{"properties": {
            "showMarker": _expr("true"),
            "strokeWidth": _expr("2D"),
        }}])
    if style and sv.get("objects"):
        _apply_series_colors(sv["objects"], style, series_refs or [])
    if title:
        sv["vcObjects"] = _title_vcobject(title)
    return sv


_NON_CHART_TYPES = ("slicer", "cardVisual", "card", "multiRowCard", "tableEx", "textbox")


def _chart_objects(with_axes: bool = True) -> Dict[str, Any]:
    """Default chart objects matching official Power BI samples.

    Real PBI dashboards show: bold axis labels, data labels ON, legend ON,
    bold titles, and themed label colours.  Emitting these defaults means
    every chart in the exported .pbix looks styled out of the box — not
    "plain" with all-default PBI formatting.

    Donut/pie/funnel/scatter charts pass with_axes=False (no axes).
    """
    _FONT_BOLD = {"bold": _expr("true"), "fontFamily": _expr(
        "'''Segoe UI Semibold'', wf_segoe-ui_semibold, helvetica, arial, sans-serif'"
    )}
    result: Dict[str, Any] = {
        "legend": [{
            "properties": {
                "show": _expr("true"),
                "position": _expr("'Top'"),
                "showTitle": _expr("false"),
                "fontSize": _expr("9D"),
                "fontColor": {"solid": {"color": {"expr": {"Literal": {"Value": "'#605E5C'"}}}}},
            }
        }],
        "labels": [{
            "properties": {
                "show": _expr("true"),
                "fontSize": _expr("9D"),
                "labelPrecision": _expr("0L"),
                "fontColor": {"solid": {"color": {"expr": {"Literal": {"Value": "'#252423'"}}}}},
                **_FONT_BOLD,
            }
        }],
        "background": [{
            "properties": {
                "show": _expr("true"),
                "color": {"solid": {"color": {"expr": {"Literal": {"Value": "'#FFFFFF'"}}}}},
                "transparency": _expr("0D"),
            }
        }],
        "border": [{
            "properties": {
                "show": _expr("true"),
                "color": {"solid": {"color": {"expr": {"Literal": {"Value": "'#E8ECF1'"}}}}},
                "radius": _expr("8D"),
            }
        }],
        "title": [{
            "properties": {
                "show": _expr("true"),
                "fontColor": {"solid": {"color": {"expr": {"Literal": {"Value": "'#252423'"}}}}},
                "fontSize": _expr("10D"),
                "fontFamily": _expr("'''Segoe UI Semibold'', wf_segoe-ui_semibold, helvetica, arial, sans-serif'"),
                "alignment": _expr("'Left'"),
            }
        }],
    }
    if with_axes:
        result["categoryAxis"] = [{
            "properties": {
                "show": _expr("true"),
                "showAxisTitle": _expr("false"),
                "concatenateLabels": _expr("false"),
                "fontSize": _expr("9D"),
                "fontColor": {"solid": {"color": {"expr": {"Literal": {"Value": "'#605E5C'"}}}}},
                "gridlineShow": _expr("true"),
                "gridlineColor": {"solid": {"color": {"expr": {"Literal": {"Value": "'#E8ECF1'"}}}}},
                "gridlineStyle": _expr("'dotted'"),
            }
        }]
        result["valueAxis"] = [{
            "properties": {
                "show": _expr("true"),
                "showAxisTitle": _expr("false"),
                "gridlineShow": _expr("true"),
                "gridlineColor": {"solid": {"color": {"expr": {"Literal": {"Value": "'#E8ECF1'"}}}}},
                "gridlineStyle": _expr("'dotted'"),
                "fontSize": _expr("9D"),
                "fontColor": {"solid": {"color": {"expr": {"Literal": {"Value": "'#605E5C'"}}}}},
            }
        }]
    return result


# Visual types that have axes (category/value) — donut, pie, scatter, funnel don't
_AXIS_VISUALS = {
    "clusteredColumnChart", "lineChart", "areaChart", "barChart",
    "lineClusteredColumnComboChart", "lineStackedColumnComboChart",
    "stackedAreaChart", "hundredPercentStackedColumnChart",
    "hundredPercentStackedBarChart", "hundredPercentStackedAreaChart",
    "ribbonChart", "waterfallChart",
}


def _scatter_objects() -> Dict[str, Any]:
    """Objects for scatterChart — uses categoryLabels (not labels) + markers.

    Per Fabric schema + Desktop output: scatter applies categoryAxis,
    valueAxis, legend, categoryLabels (NOT labels), markers, dataPoint.
    Using ``labels`` fails silently (blank visual).
    """
    objs = _chart_objects(with_axes=True)
    if "labels" in objs:
        objs["categoryLabels"] = objs.pop("labels")
    objs.setdefault("markers", [{"properties": {
        "show": _expr("true"),
    }}])
    return objs


def _gauge_objects() -> Dict[str, Any]:
    """Objects for gauge — no axes, no legend (single-value dial)."""
    objs = _chart_objects(with_axes=False)
    objs.pop("legend", None)
    return objs


def _waterfall_objects() -> Dict[str, Any]:
    """Objects for waterfallChart — sentimentColors (not dataPoint)."""
    objs = _chart_objects(with_axes=True)
    objs.setdefault("sentimentColors", [{"properties": {
        "increaseFill": {"solid": {"color": {"expr": {"Literal": {"Value": "'#1AAB40'"}}}}},
        "decreaseFill": {"solid": {"color": {"expr": {"Literal": {"Value": "'#D64554'"}}}}},
        "totalFill": {"solid": {"color": {"expr": {"Literal": {"Value": "'#118DFF'"}}}}},
    }}])
    return objs


def _style_objects(style: Dict[str, Any]) -> Dict[str, Any]:
    """Build PBI visual ``objects`` from a widget's ``config.style``.

    Only properties the user actually set are emitted; anything unset falls
    back to PBI's defaults (so a plain widget still looks normal). Property
    shape mirrors the real samples in ``pbix_analysis/results`` — Literal
    exprs, ``solid`` color wrappers, etc.
    """
    objects = _chart_objects()

    def lit(value: Any) -> Dict[str, Any]:
        return {"expr": {"Literal": {"Value": str(value)}}}

    def solid(hex_color: str) -> Dict[str, Any]:
        return {"solid": {"color": {"expr": {"Literal": {"Value": _str_literal(hex_color)}}}}}

    colors = style.get("colors")
    if isinstance(colors, list) and colors:
        # Default data color (no selector). Per-series overrides are appended
        # by _apply_series_colors when the model column refs are known.
        objects["dataPoint"] = [
            {"properties": {"transparency": lit("0D")}},
            {"properties": {"fill": solid(str(colors[0]))}},
        ]

    legend = style.get("legend")
    if isinstance(legend, dict):
        objects["legend"] = [{
            "properties": {
                "show": lit("true" if legend.get("show") else "false"),
                "position": lit(_str_literal(str(legend.get("position") or "top"))),
            }
        }]

    labels = style.get("dataLabels")
    if isinstance(labels, dict) and labels.get("show"):
        objects["labels"] = [{
            "properties": {
                "show": lit("true"),
                "position": lit(_str_literal(str(labels.get("position") or "outsideEnd"))),
                "fontSize": lit(f"{int(labels.get('fontSize') or 10)}D"),
            }
        }]

    axis = style.get("axis") or {}
    if axis.get("categoryTitle"):
        objects["categoryAxis"] = [{
            "properties": {
                "show": lit("true"),
                "showAxisTitle": lit("true"),
                "axisTitle": lit(_str_literal(str(axis["categoryTitle"]))),
                "concatenateLabels": lit("false"),
            }
        }]
    if axis.get("valueTitle"):
        objects["valueAxis"] = [{
            "properties": {
                "show": lit("true"),
                "showAxisTitle": lit("true"),
                "axisTitle": lit(_str_literal(str(axis["valueTitle"]))),
                "gridlineShow": lit("false" if axis.get("showGrid") is False else "true"),
            }
        }]

    if style.get("background"):
        objects["background"] = [{
            "properties": {
                "color": solid(str(style["background"])),
                "transparency": 0,
            }
        }]

    title = style.get("title")
    if isinstance(title, dict) and title.get("color"):
        objects["title"] = [{
            "properties": {
                "show": lit("true"),
                "fontColor": solid(str(title["color"])),
                "fontSize": lit(f"{int(title.get('fontSize') or 12)}D"),
            }
        }]

    return objects


def _apply_series_colors(objects: Dict[str, Any], style: Dict[str, Any],
                         series_refs: List[str]) -> None:
    """Append one ``dataPoint`` entry per series colour, keyed by model column.

    A selector that doesn't match a real column is silently ignored by PBI
    Desktop (the point falls back to the default colour), so over-emitting is
    safe. Series colours cycle through ``style.colors``.
    """
    colors = style.get("colors")
    if not (isinstance(colors, list) and len(colors) > 1 and series_refs):
        return
    base = objects.get("dataPoint") or [
        {"properties": {"transparency": {"expr": {"Literal": {"Value": "0D"}}}}}
    ]
    extra: List[Dict[str, Any]] = []
    for i, ref in enumerate(series_refs):
        color = colors[i % len(colors)]
        extra.append({
            "properties": {"fill": {"solid": {"color": {"expr": {"Literal": {"Value": _str_literal(color)}}}}}},
            "selector": {"metadata": ref},
        })
    objects["dataPoint"] = base + extra


def _title_vcobject(text: str, bold: bool = True) -> Dict[str, Any]:
    props: Dict[str, Any] = {
        "show": _expr("true"),
        "text": _expr(_str_literal(text)),
        "fontColor": {
            "solid": {"color": {"expr": {"ThemeDataColor": {"ColorId": 0, "Percent": 0}}}}
        },
        "fontSize": _expr("10D"),
    }
    if bold:
        props["bold"] = _expr("true")
    return {"title": [{"properties": props}]}


# KPI card background colours — cycle through these for each card visual.
_KPI_BG_COLORS = [
    "#22c55e", "#0d9488", "#2563eb", "#ea580c",
    "#7c3aed", "#0891b2", "#db2777", "#ca8a0a",
]


def _card_visual(table: str, value_col: str, title: str,
                 fmt: str = "0", bg_color: Optional[str] = None) -> Dict[str, Any]:
    """KPI card visual. ``fmt`` is the .NET number format; ``bg_color`` sets
    a coloured background on the card (cycles through _KPI_BG_COLORS)."""
    objects: Dict[str, Any] = {}
    if bg_color:
        objects["background"] = [{
            "properties": {
                "color": {"solid": {"color": {"expr": {"Literal": {"Value": f"'{bg_color}'"}}}}},
                "transparency": 0,
            }
        }]
        objects["callout"] = [{
            "properties": {
                "fontSize": _expr("36D"),
                "fontFamily": _expr("'''DIN'', wf_segoe-ui_din, helvetica, arial, sans-serif'"),
                "fontColor": {"solid": {"color": {"expr": {"Literal": {"Value": "'#FFFFFF'"}}}}},
                "displayUnits": _expr("0L"),
            }
        }]
        objects["categoryLabels"] = [{
            "properties": {
                "show": _expr("true"),
                "fontSize": _expr("12D"),
                "fontFamily": _expr("'''Segoe UI Semibold'', wf_segoe-ui_semibold, helvetica, arial, sans-serif'"),
                "fontColor": {"solid": {"color": {"expr": {"Literal": {"Value": "'#FFFFFF'"}}}}},
            }
        }]
        objects["border"] = [{"properties": {"show": _expr("false")}}]
        objects["wordWrap"] = [{"properties": {"show": _expr("true")}}]
    sv = _chart_visual(
        "cardVisual", table, [(value_col, True)],
        {"Data": value_col},
        title,
        column_props={_ref(table, value_col, True): {"displayName": title}},
        objects=objects or None,
    )
    sv["cardFormat"] = fmt or "0"
    return sv


def _slicer_visual(table: str, field: str, title: str) -> Dict[str, Any]:
    return _chart_visual(
        "slicer", table, [(field, False)],
        {"Category": field}, title,
        column_props={f"{table}.{field}": {"displayName": title}},
        objects={
            "data": [{"properties": {"mode": _expr("'Basic'")}}],
            "general": [{"properties": {}}],
            "header": [{
                "properties": {
                    "show": _expr("true"),
                    "fontColor": {"solid": {"color": {"expr": {"Literal": {"Value": "'#252423'"}}}}},
                    "fontSize": _expr("11D"),
                    "fontFamily": _expr("'''Segoe UI Semibold'', wf_segoe-ui_semibold, helvetica, arial, sans-serif'"),
                }
            }],
            "background": [{
                "properties": {
                    "show": _expr("true"),
                    "color": {"solid": {"color": {"expr": {"Literal": {"Value": "'#FFFFFF'"}}}}},
                    "transparency": _expr("0D"),
                }
            }],
            "border": [{
                "properties": {
                    "show": _expr("true"),
                    "color": {"solid": {"color": {"expr": {"Literal": {"Value": "'#E8ECF1'"}}}}},
                    "radius": _expr("6D"),
                }
            }],
            "selection": [{
                "properties": {
                    "selectAllCheckboxEnabled": _expr("true"),
                    "singleSelect": _expr("false"),
                }
            }],
        },
    )


def _table_visual(table: str, columns: List[str], title: str) -> Dict[str, Any]:
    return _chart_visual(
        "tableEx", table, [(c, False) for c in columns],
        {"Values": columns}, title,
        objects={
            "columnHeaders": [{
                "properties": {
                    "fontColor": {"solid": {"color": {"expr": {"Literal": {"Value": "'#FFFFFF'"}}}}},
                    "fontFamily": _expr("'''Segoe UI Semibold'', wf_segoe-ui_semibold, helvetica, arial, sans-serif'"),
                    "fontSize": _expr("10D"),
                    "backColor": {"solid": {"color": {"expr": {"Literal": {"Value": "'#4472C4'"}}}}},
                    "alignment": _expr("'Left'"),
                }
            }],
            "values": [{
                "properties": {
                    "fontColorPrimary": {"solid": {"color": {"expr": {"Literal": {"Value": "'#252423'"}}}}},
                    "backColorPrimary": {"solid": {"color": {"expr": {"Literal": {"Value": "'#FFFFFF'"}}}}},
                    "fontColorSecondary": {"solid": {"color": {"expr": {"Literal": {"Value": "'#252423'"}}}}},
                    "backColorSecondary": {"solid": {"color": {"expr": {"Literal": {"Value": "'#F5F7FA'"}}}}},
                    "fontFamily": _expr("'''Segoe UI'', wf_segoe-ui, helvetica, arial, sans-serif'"),
                    "fontSize": _expr("10D"),
                    "wordWrap": _expr("true"),
                }
            }],
            "grid": [{
                "properties": {
                    "gridVertical": _expr("true"),
                    "gridHorizontal": _expr("true"),
                    "rowPadding": _expr("8D"),
                    "outlineColor": {"solid": {"color": {"expr": {"Literal": {"Value": "'#E8ECF1'"}}}}},
                    "gridVerticalColor": {"solid": {"color": {"expr": {"Literal": {"Value": "'#E8ECF1'"}}}}},
                    "gridHorizontalColor": {"solid": {"color": {"expr": {"Literal": {"Value": "'#E8ECF1'"}}}}},
                }
            }],
            "total": [{
                "properties": {
                    "fontColor": {"solid": {"color": {"expr": {"Literal": {"Value": "'#252423'"}}}}},
                    "backColor": {"solid": {"color": {"expr": {"Literal": {"Value": "'#E8ECF1'"}}}}},
                    "fontFamily": _expr("'''Segoe UI Semibold'', wf_segoe-ui_semibold, helvetica, arial, sans-serif'"),
                    "fontSize": _expr("10D"),
                }
            }],
            "background": [{
                "properties": {
                    "show": _expr("true"),
                    "color": {"solid": {"color": {"expr": {"Literal": {"Value": "'#FFFFFF'"}}}}},
                    "transparency": _expr("0D"),
                }
            }],
            "border": [{
                "properties": {
                    "show": _expr("true"),
                    "color": {"solid": {"color": {"expr": {"Literal": {"Value": "'#E8ECF1'"}}}}},
                    "radius": _expr("8D"),
                }
            }],
        },
    )


def _chart_from_widget(wm: Dict[str, Any]) -> Dict[str, Any]:
    """Build the singleVisual for a widget from its prepared data."""
    w = wm["widget"]
    wtype = str(w.get("type", "")).lower()
    tname = wm["table"]
    x_col = wm["x_col"]
    num_cols = wm["num_cols"]
    title = str(w.get("title") or "").strip()
    cfg = w.get("config") or {}
    style = cfg.get("style") if isinstance(cfg.get("style"), dict) else None

    if wtype == "kpi":
        # Use the widget's own colour when set, else the first style colour,
        # else cycle the default KPI palette.
        chosen = cfg.get("color") or (style and style.get("colors") and style["colors"][0])
        bg = chosen or _KPI_BG_COLORS[wm.get("_kpi_idx", 0) % len(_KPI_BG_COLORS)]
        return _card_visual(tname, num_cols[0], title,
                            fmt=str(cfg.get("format") or "0"),
                            bg_color=bg)

    if wtype == "table":
        return _table_visual(tname, wm["cols"], title)

    if wtype == "gauge":
        # Native dial: single measure in Y (Min/Max default 0..2x value per MS docs).
        # Extra measures map to Target/Min/Max when present.
        value_col = num_cols[0] if num_cols else (wm["cols"][0] if wm["cols"] else "value")
        columns = [(value_col, True)]
        roles: Dict[str, Any] = {"Y": value_col}
        if len(num_cols) > 1:
            # Second measure as target needle; third/fourth as min/max if given.
            columns = [(c, True) for c in num_cols[:4]]
            roles = {"Y": num_cols[0], "TargetValue": num_cols[1]}
            if len(num_cols) > 2:
                roles["MinValue"] = num_cols[2]
            if len(num_cols) > 3:
                roles["MaxValue"] = num_cols[3]
        column_props = {_ref(tname, c, True): {"displayName": title if c == value_col else c}
                        for c, _ in columns}
        sv = _chart_visual(
            "gauge", tname, columns, roles, title,
            column_props=column_props,
            objects=_gauge_objects(),
        )
        return sv

    if wtype == "funnel":
        # Native funnel: Category + Y (no Series bucket).
        columns = [(x_col, False)] + [(c, True) for c in num_cols]
        roles = {"Category": x_col, "Y": num_cols}
        column_props = {_ref(tname, x_col, False): {"displayName": title or x_col}}
        for c in num_cols:
            column_props[_ref(tname, c, True)] = {"displayName": title if len(num_cols) == 1 else c}
        return _chart_visual("funnel", tname, columns, roles, title,
                             column_props=column_props, style=style,
                             series_refs=[_ref(tname, c, True) for c in num_cols])

    if wtype == "waterfall":
        # Native waterfall: Category + Y (+ optional Breakdown).
        columns = [(x_col, False)] + [(c, True) for c in num_cols]
        roles = {"Category": x_col, "Y": num_cols}
        column_props = {_ref(tname, x_col, False): {"displayName": title or x_col}}
        for c in num_cols:
            column_props[_ref(tname, c, True)] = {"displayName": title if len(num_cols) == 1 else c}
        sv = _chart_visual("waterfallChart", tname, columns, roles, title,
                           column_props=column_props, style=style,
                           series_refs=[_ref(tname, c, True) for c in num_cols])
        # sentimentColors (not dataPoint) per Fabric schema.
        objs = sv.setdefault("objects", {})
        objs.setdefault("sentimentColors", _waterfall_objects()["sentimentColors"])
        return sv

    if wtype == "treemap":
        # Native treemap: Group + Values (+ optional Details).
        columns = [(x_col, False)] + [(c, True) for c in num_cols]
        roles = {"Group": x_col, "Values": num_cols}
        column_props = {_ref(tname, x_col, False): {"displayName": title or x_col}}
        for c in num_cols:
            column_props[_ref(tname, c, True)] = {"displayName": title if len(num_cols) == 1 else c}
        return _chart_visual("treemap", tname, columns, roles, title,
                             column_props=column_props, style=style,
                             series_refs=[_ref(tname, c, True) for c in num_cols])

    if wtype == "combo":
        # Native combo: Category + Y (columns) + Y2 (lines).
        # Single measure renders as columns only (no shared Select across roles).
        columns = [(x_col, False)] + [(c, True) for c in num_cols]
        if len(num_cols) <= 1:
            roles = {"Category": x_col, "Y": list(num_cols)}
        else:
            roles = {"Category": x_col, "Y": num_cols[:-1], "Y2": num_cols[-1:]}
        column_props = {_ref(tname, x_col, False): {"displayName": title or x_col}}
        for c in num_cols:
            column_props[_ref(tname, c, True)] = {"displayName": c}
        return _chart_visual("lineClusteredColumnComboChart", tname, columns, roles, title,
                             column_props=column_props, style=style,
                             series_refs=[_ref(tname, c, True) for c in num_cols])

    if wtype == "radar":
        # radarChart (custom visual may be required in stock Desktop).
        # Emit native Category+Y so fidelity is preserved; Desktop shows
        # custom-visual prompt if the visual is not installed.
        columns = [(x_col, False)] + [(c, True) for c in num_cols]
        roles = {"Category": x_col, "Y": num_cols}
        column_props = {_ref(tname, x_col, False): {"displayName": title or x_col}}
        for c in num_cols:
            column_props[_ref(tname, c, True)] = {"displayName": title if len(num_cols) == 1 else c}
        logger.info("pbix export: radar emits native radarChart (custom visual may be required)")
        return _chart_visual("radarChart", tname, columns, roles, title,
                             column_props=column_props, style=style,
                             series_refs=[_ref(tname, c, True) for c in num_cols])

    if wtype in ("scatter", "bubble"):
        # Native scatter: X + Y (+ optional Size). categoryLabels (not labels).
        y_col = num_cols[0] if num_cols else x_col
        columns = [(x_col, False), (y_col, True)]
        roles: Dict[str, Any] = {"X": x_col, "Y": y_col}
        if len(num_cols) > 1:
            columns.append((num_cols[1], True))
            roles["Size"] = num_cols[1]
            if len(num_cols) > 2:
                # Extra measures also plotted on Y.
                columns.extend([(c, True) for c in num_cols[2:]])
                roles["Y"] = [y_col] + list(num_cols[2:])
        column_props = {_ref(tname, x_col, False): {"displayName": title or x_col}}
        for c, _ in columns:
            if c != x_col:
                column_props[_ref(tname, c, True)] = {"displayName": title if c == y_col and len(num_cols) == 1 else c}
        objs = _scatter_objects()
        if style:
            # Merge user style (legend/axes/colors) without reintroducing `labels`.
            for k, v in (_style_objects(style) or {}).items():
                if k != "labels":
                    objs[k] = v
            _apply_series_colors(objs, style, [_ref(tname, c, True) for c in num_cols])
        sv = _chart_visual(
            "scatterChart", tname, columns, roles, title,
            column_props=column_props,
            objects=objs,
            style=None,
            series_refs=[],
        )
        return sv

    # bar / line / pie / area / heatmap (and multi-series combos / stacked)
    visual_type = _WIDGET_VISUAL.get(wtype, "clusteredColumnChart")
    columns = [(x_col, False)] + [(c, True) for c in num_cols]
    roles = {"Category": x_col, "Y": num_cols}
    column_props = {}
    # Set a meaningful displayName on the category column so PBI Desktop
    # shows the dimension name (not "label") as the series/legend title.
    if x_col:
        column_props[_ref(tname, x_col, False)] = {"displayName": title or x_col}
    for c in num_cols:
        label = title if len(num_cols) == 1 else c
        column_props[_ref(tname, c, True)] = {"displayName": label}
    sv = _chart_visual(visual_type, tname, columns, roles, title,
                       column_props=column_props,
                       style=style,
                       series_refs=[_ref(tname, c, True) for c in num_cols])
    # Donut/pie charts: add slices + dataPoint objects (official PBI has these
    # for per-slice coloring).  categoryAxis/valueAxis are NOT added (donuts
    # don't have axes — _chart_objects(with_axes=False) already omits them).
    if visual_type == "donutChart":
        objs = sv.setdefault("objects", {})
        objs.setdefault("slices", [{"properties": {}}])
        objs.setdefault("dataPoint", [{"properties": {}}])
    return sv


def _textbox_visual(text: str, font_size: str = "14pt", bold: bool = False,
                    background: Optional[str] = None) -> Dict[str, Any]:
    text_style: Dict[str, Any] = {"fontSize": font_size, "fontFamily": "Segoe UI"}
    if bold:
        text_style["fontWeight"] = "bold"
        text_style["color"] = "#252423"
    else:
        text_style["color"] = "#605E5C"
    objects: Dict[str, Any] = {
        "general": [
            {
                "properties": {
                    "paragraphs": [
                        {"textRuns": [{"value": str(text), "textStyle": text_style}]}
                    ]
                }
            }
        ]
    }
    if background:
        objects["background"] = [
            {"properties": {
                "fill": {"solid": {"color": {"expr": {"Literal": {"Value": f"'{background}'"}}}}},
                "transparency": 0,
                "show": _expr("true"),
            }}
        ]
        objects["visualBorder"] = [
            {"properties": {
                "show": _expr("true"),
                "radius": _expr("6D"),
            }}
        ]
    return {
        "visualType": "textbox",
        "drillFilterOtherVisuals": True,
        "objects": objects,
    }


# ── visualContainer query / dataTransforms (real-PBI shape) ──────────────

def _make_visual_query(sv: Dict[str, Any]) -> str:
    """Generate the ``query`` JSON string for a data visualContainer.

    Mirrors Power BI Desktop's ``SemanticQueryDataShapeCommand``: Query
    (From/Select + OrderBy on the category role), Binding (Primary.Groupings +
    DataReduction Window + Aggregates Min/Max per aggregate select), and
    ExecutionMetricsKind. The Binding projection indices are derived from the
    projections' queryRefs so they always match the Select entries.
    """
    pq = dict(sv.get("prototypeQuery") or {})
    projs = sv.get("projections") or {}
    selects = pq.get("Select") or []
    select_names = [s.get("Name") for s in selects]

    proj_indices: Dict[str, List[int]] = {}
    for role, refs in projs.items():
        refs_list = refs if isinstance(refs, list) else [refs]
        proj_indices[role] = [select_names.index(r["queryRef"]) for r in refs_list
                              if isinstance(r, dict) and r.get("queryRef") in select_names]

    all_indices = sorted({i for indices in proj_indices.values() for i in indices})
    if not all_indices:
        # No resolvable projection (shouldn't happen) — select everything so the
        # visual at least receives the rows.
        all_indices = list(range(len(selects)))

    # OrderBy on the category-like role (real PBI chart queries carry it).
    category_idx = next((idxs[0] for role, idxs in proj_indices.items()
                         if role in _CATEGORY_ROLES and idxs), None)
    if category_idx is not None and category_idx < len(selects):
        col = selects[category_idx].get("Column")
        if col is not None:
            pq = dict(pq)
            pq["OrderBy"] = [{"Direction": 1, "Expression": {"Column": col}}]

    # Binding.Aggregates — Min/Max per aggregate select (real PBI shape).
    aggregates: List[Dict[str, Any]] = []
    for i, s in enumerate(selects):
        if "Aggregation" in s and i in all_indices:
            aggregates.append({"Select": i, "Aggregations": [{"Min": {}}, {"Max": {}}]})

    binding: Dict[str, Any] = {
        "Primary": {"Groupings": [{"Projections": all_indices}]},
        "DataReduction": {"DataVolume": 4, "Primary": {"Window": {"Count": 1000}}},
        "Version": 1,
    }
    if aggregates:
        binding["Aggregates"] = aggregates

    query_cmd = {
        "SemanticQueryDataShapeCommand": {
            "Query": pq,
            "Binding": binding,
            "ExecutionMetricsKind": 1,
        }
    }
    return json.dumps({"Commands": [query_cmd]}, ensure_ascii=False, separators=(",", ":"))


def _make_visual_data_transforms(sv: Dict[str, Any]) -> str:
    """Generate the ``dataTransforms`` JSON string for a data visualContainer.

    Mirrors Power BI Desktop's shape: projectionOrdering, projectionActiveItems
    (category-like roles only), queryMetadata.Select (Type 2048 for columns, 3
    for aggregated values), visualElements.DataRoles (one entry per projection),
    and a ``selects`` metadata array.

    Role bindings are derived per *select index* (``enumerate(selects)``) so
    each select fills exactly the role(s) its projection belongs to — e.g. a
    chart's category select gets ``{"Category": true}`` and its value select
    gets ``{"Y": true}``. (A previous version reused a stale ``i`` here, giving
    both selects ``{"Category": false, "Y": true}`` — which silently emptied
    every chart.)
    """
    pq = sv.get("prototypeQuery") or {}
    projs = sv.get("projections") or {}
    selects = pq.get("Select") or []
    select_names = [s.get("Name") for s in selects]
    entities = [f.get("Entity") for f in (pq.get("From") or [])]
    table = entities[0] if entities else ""
    vtype = str(sv.get("visualType") or "")

    ordering: Dict[str, List[int]] = {}
    for role, refs in projs.items():
        refs_list = refs if isinstance(refs, list) else [refs]
        ordering[role] = [select_names.index(r["queryRef"]) for r in refs_list
                          if isinstance(r, dict) and r.get("queryRef") in select_names]

    # Only the category-like roles are the "active" data-view item; real
    # value-only visuals (card/table) omit projectionActiveItems entirely.
    active: Dict[str, Any] = {}
    for role, idxs in ordering.items():
        if role in _CATEGORY_ROLES and idxs:
            active[role] = [{"queryRef": select_names[idxs[0]], "suppressConcat": False}]

    meta: List[Dict[str, Any]] = []
    for s in selects:
        name = s.get("Name", "")
        is_agg = "Aggregation" in s
        restatement = name.split(".")[-1] if "." in name else name
        if is_agg:
            # "Sum(Table.Profit)" -> "Sum of Profit"
            base = restatement.rstrip(")").split("(")[-1]
            fn = restatement.split("(")[0] if "(" in restatement else "Sum"
            meta.append({"Restatement": f"{fn} of {base}", "Name": name, "Type": 3, "Format": "0"})
        else:
            meta.append({"Restatement": restatement, "Name": name, "Type": 2048})

    # One DataRoles entry per projection, isActive only on the category-like
    # role (matches real chart/card/table/slicer files).
    ve_roles: List[Dict[str, Any]] = []
    for role, idxs in ordering.items():
        for idx in idxs:
            ve_roles.append({"Name": role, "Projection": idx, "isActive": role in _CATEGORY_ROLES})

    is_card = vtype in ("cardVisual", "card", "multiRowCard")
    selects_meta: List[Dict[str, Any]] = []
    for i, s in enumerate(selects):
        name = s.get("Name", "")
        is_agg = "Aggregation" in s or "Measure" in s
        if "Measure" in s:
            # DAX measure reference (preferred for DATATABLE calc tables)
            prop = s.get("Measure", {}).get("Property", "")
            expr = {"Measure": {"Expression": {"SourceRef": {"Entity": table}}, "Property": prop}}
            underlying = 260
            restatement = prop
        elif is_agg:
            # Legacy Aggregation path (kept when _USE_AGG_QUERIES=False with explicit agg)
            col = (s.get("Aggregation") or {}).get("Expression", {}).get("Column") or {}
            prop = col.get("Property") if isinstance(col, dict) else name.split(".")[-1] if "." in name else name
            expr = {"Aggregation": {
                "Expression": {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": prop}},
                "Function": _AGG_FUNCTION,
            }}
            underlying = 260
            restatement = (name.split("(")[1].rstrip(")") if "(" in name else name).split(".")[-1]
        else:
            col = s.get("Column") or {}
            prop = col.get("Property") if isinstance(col, dict) else name.split(".")[-1] if "." in name else name
            expr = {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": prop}}
            underlying = 1
            restatement = name.split(".")[-1] if "." in name else name

        sel_roles = [r for r, idxs in ordering.items() if i in idxs]
        entry: Dict[str, Any] = {
            "displayName": restatement,
            "queryName": name,
            "roles": {r: (r in sel_roles) for r in ordering},
            "type": {"category": None, "underlyingType": underlying},
            "expr": expr,
        }
        if is_agg and is_card:
            # Real card value selects carry a number format; honor the widget's
            # chosen format (e.g. "#,##0.00") with a "0" fallback.
            entry["format"] = str((sv or {}).get("cardFormat") or "0")
        if not is_agg and any(r in _CATEGORY_ROLES for r in sel_roles):
            entry["sort"] = 1
            entry["sortOrder"] = 0  # real chart category selects sort ascending
        selects_meta.append(entry)

    dt: Dict[str, Any] = {
        "objects": {},
        "projectionOrdering": ordering,
        "queryMetadata": {"Select": meta},
        "visualElements": [{"DataRoles": ve_roles}],
        "selects": selects_meta,
    }
    if active:
        dt["projectionActiveItems"] = active
    return json.dumps(dt, ensure_ascii=False, separators=(",", ":"))


def _visual_config(name: str, x: float, y: float, width: float, height: float,
                   single_visual: Dict[str, Any]) -> str:
    """Return the visual config as a JSON STRING — PBI Desktop expects config
    to be a serialized string, not a nested object."""
    cfg = {
        "name": name,
        "layouts": [
            {
                "id": 0,
                "position": {"x": x, "y": y, "z": 0, "width": width, "height": height},
            }
        ],
        "singleVisual": single_visual,
    }
    return json.dumps(cfg, ensure_ascii=False, separators=(",", ":"))


def _hex_name() -> str:
    """Generate a 20-char hex string like PBI uses for visual/config names.

    Uses ``uuid.uuid4()``, NOT wall-clock time: the sandboxes/CI runners this
    exporter runs under pin ``time.time_ns()`` to a constant, so an
    ``md5(time.time_ns())`` scheme returned the SAME name for every visual.
    Power BI Desktop deduplicates visualContainers by that ``config.name`` —
    so all but the first chart/card were silently dropped ("only one widget
    shows"). UUID randomness guarantees a unique name per visual.
    """
    import uuid
    return uuid.uuid4().hex[:20]


# ── dimension detection for slicer -> chart relationships ───────────────

_DIM_RE_COL = re.compile(r"\"?([A-Za-z_][A-Za-z0-9_.]*)\"?\s+AS\s+label\b", re.I)
_DIM_RE_GROUP = re.compile(r"\bGROUP\s+BY\s+((?:\"[A-Za-z0-9_. ]+\")|[A-Za-z_][A-Za-z0-9_.]*)\b", re.I)
_DIM_RE_SELECT = re.compile(r"\bAS\s+label\b", re.I)


def _strip_quotes(s: str) -> str:
    return s.strip().strip('"').strip("`").split(".")[-1]


def _extract_dimension(sql: str) -> Optional[str]:
    """Best-effort recovery of the dimension column behind a widget's SQL."""
    if not sql:
        return None
    m = _DIM_RE_COL.search(sql)
    if m:
        return _strip_quotes(m.group(1))
    m = _DIM_RE_GROUP.search(sql)
    if m:
        return _strip_quotes(m.group(1))
    if _DIM_RE_SELECT.search(sql):
        m = re.search(r"\bSELECT\s+(\"?[A-Za-z_][A-Za-z0-9_.]*\"?)", sql, re.I)
        if m:
            return _strip_quotes(m.group(1))
    return None


# ── packaging ───────────────────────────────────────────────────────────

def _json_bytes(obj: Any) -> bytes:
    """UTF-16LE (no BOM) JSON — matching what .NET's Encoding.Unicode.GetBytes writes."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-16-le")


def _xml_bytes(text: str) -> bytes:
    return ("<?xml version=\"1.0\" encoding=\"utf-8\"?>" + text).encode("utf-8")


# ── DataMashup binary (QDEFF stream) ───────────────────────────────────────
#
# Power BI Desktop evaluates M #table partition expressions only when the
# .pbit contains a DataMashup part — the mashup engine's formula graph.
# Without it the tables stay empty ("only title, nothing else").
#
# The DataMashup part is a QDEFF binary stream (MS-QDEFF §2.2) wrapping:
#   1. PackageParts ZIP — inner mashup package (.pbm) containing:
#      - Config/Package.xml
#      - [Content_Types].xml
#      - Formulas/Section1.m  (shared M queries, one per table)
#   2. Permissions XML
#   3. Metadata sub-stream (per-query formula items for the engine)
#   4. PermissionBindings (single null byte — engine falls back to defaults)
#
# Reference: MS-QDEFF v20250218, pbi-tools MashupSerializer.cs, pbixray.

_PACKAGE_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<Package xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Version>2.65.5313.701</Version>
  <MinVersion>1.5.3296.0</MinVersion>
  <Culture>en-US</Culture>
</Package>"""

_INNER_CONTENT_TYPES = """\
<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="text/xml"/>
  <Default Extension="m" ContentType="application/x-ms-m"/>
</Types>"""

_PERMISSIONS_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<PermissionList xmlns:xsd="http://www.w3.org/2001/XMLSchema"
                xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <CanEvaluateFuturePackages>false</CanEvaluateFuturePackages>
  <FirewallEnabled>true</FirewallEnabled>
  <WorkbookGroupType xsi:nil="true" />
</PermissionList>"""


def _m_literal(v: Any) -> str:
    """Render a Python value as an M literal."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v).replace('"', '""')
    return f'"{s}"'


def _build_m_query(df: pd.DataFrame, max_rows: int = 500) -> str:
    """Build a self-contained M expression reproducing ``df`` via Table.FromRows.

    Used for connection-only documentation queries in the DataMashup: they give
    the exported template visible data lineage in Power Query without
    participating in model load (the historical crash source — see the NOTE in
    build_powerbi_pbit). Rows are capped to keep Section1.m reasonable.
    """
    cols = [str(c) for c in df.columns]
    type_map = {"int64": "Int64.Type", "int32": "Int64.Type", "float64": "type number",
                "bool": "Logical.Type", "datetime64[ns]": "type datetime"}
    col_types = ", ".join(
        f'{_m_literal(c)} = {type_map.get(str(df[c].dtype), "text")}' for c in cols
    )
    rows = [
        "{" + ", ".join(_m_literal(v) for v in row) + "}"
        for row in df.head(max_rows).itertuples(index=False, name=None)
    ]
    rows_m = "{" + ", ".join(rows) + "}" if rows else "{}"
    return (
        "let\n"
        "    Source = Table.FromRows(\n"
        f"        {rows_m},\n"
        f"        type table [{col_types}]\n"
        "    )\n"
        "in\n"
        "    Source"
    )


def _build_section1_m(queries: List[Tuple[str, str]]) -> str:
    """Build the Section1.m file content for the inner mashup package.

    ``queries`` is ``[(safe_table_name, m_expression), ...]`` — each becomes a
    ``shared <Name> = <expression>;`` member that the DataModelSchema
    partitions reference via ``source.type: "m"``.
    """
    parts = ["section Section1;\n"]
    for name, expr in queries:
        # M member names cannot contain periods, quotes, tabs, leading/trailing
        # whitespace or newlines (MS-QDEFF §2.3.2), and spaces need the #"…"
        # quoted-identifier escape — a bare `shared Total Sales = …` is a parse
        # error. Real Power Query always emits `shared #"<Name>" = …;` here.
        safe = name.replace(".", "_").replace('"', "_").replace("\t", "_").strip()
        parts.append(f'shared #"{safe}" = {expr};\n')
    return "".join(parts)


def _build_metadata_xml(queries: List[Tuple[str, str]],
                        load_to_model: bool = True) -> str:
    """Build the MetadataXml (LocalPackageMetadataFile) for the DataMashup.

    Contains one ``AllFormulas`` collection item plus one ``Formula`` item per
    query. With ``load_to_model=True`` (M-partition tables) the engine needs
    ``FillToDataModelEnabled=l1`` and ``LoadedToAnalysisServices=l1`` to load
    the query into the model. With ``load_to_model=False`` (connection-only
    documentation queries) everything stays off and the query is marked
    private so PBI never evaluates it during open/refresh.
    """
    import uuid as _uuid

    items = [
        # Collection-level item
        '<Item>\n'
        '  <ItemLocation>\n'
        '    <ItemType>AllFormulas</ItemType>\n'
        '    <ItemPath></ItemPath>\n'
        '  </ItemLocation>\n'
        '  <StableEntries />\n'
        '</Item>\n',
    ]

    for name, expr in queries:
        safe = name.replace(".", "_").replace('"', "_").replace("\t", "_").strip()
        qid = str(_uuid.uuid4())
        # Build a minimal RelationshipInfoContainer / FillColumnNames JSON.
        # ItemPath must match the Section1.m shared member, incl. the #"…" escape
        # for names with spaces.
        if load_to_model:
            stable = (
                '    <Entry Type="IsPrivate" Value="l0" />\n'
                '    <Entry Type="IsDirectQuery" Value="l0" />\n'
                '    <Entry Type="ResultType" Value="sTable" />\n'
                '    <Entry Type="FillEnabled" Value="l0" />\n'
                '    <Entry Type="FillToDataModelEnabled" Value="l1" />\n'
                '    <Entry Type="LoadedToAnalysisServices" Value="l1" />\n'
            )
        else:
            # Connection-only: never loaded into the model, hidden from the
            # queries pane — pure lineage documentation, zero refresh impact.
            stable = (
                '    <Entry Type="IsPrivate" Value="l1" />\n'
                '    <Entry Type="IsDirectQuery" Value="l0" />\n'
                '    <Entry Type="ResultType" Value="sTable" />\n'
                '    <Entry Type="FillEnabled" Value="l0" />\n'
                '    <Entry Type="FillToDataModelEnabled" Value="l0" />\n'
                '    <Entry Type="LoadedToAnalysisServices" Value="l0" />\n'
            )
        items.append(
            '<Item>\n'
            '  <ItemLocation>\n'
            '    <ItemType>Formula</ItemType>\n'
            f'    <ItemPath>Section1/#"{safe}"</ItemPath>\n'
            '  </ItemLocation>\n'
            '  <StableEntries>\n'
            + stable +
            '    <Entry Type="NavigationStepName" Value="sNavigation" />\n'
            f'    <Entry Type="QueryID" Value="s{qid}" />\n'
            '    <Entry Type="IsLastAnalysisServicesFormulaTextCollection" Value="l1" />\n'
            '  </StableEntries>\n'
            '</Item>\n'
        )

    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<LocalPackageMetadataFile>\n'
        '  <Items>\n'
        + "".join(items) +
        '  </Items>\n'
        '</LocalPackageMetadataFile>'
    )


def _build_datamashup(queries: List[Tuple[str, str]],
                      load_to_model: bool = True) -> bytes:
    """Build the complete DataMashup QDEFF binary stream.

    ``queries`` is ``[(table_name, m_expression), ...]`` — one entry per
    mashup query. ``load_to_model`` selects the MetadataXml flags (see
    _build_metadata_xml).

    Returns the raw bytes to write as the ``/DataMashup`` ZIP part.
    """
    # --- 1. Inner PackageParts ZIP (.pbm mashup package) ---
    section1_m = _build_section1_m(queries)
    inner_buf = io.BytesIO()
    with zipfile.ZipFile(inner_buf, "w", zipfile.ZIP_DEFLATED) as iz:
        iz.writestr("Config/Package.xml", _PACKAGE_XML)
        iz.writestr("[Content_Types].xml", _INNER_CONTENT_TYPES)
        iz.writestr("Formulas/Section1.m", section1_m)
    package_parts = inner_buf.getvalue()

    # --- 2. Permissions XML (UTF-8 with BOM, matches real files) ---
    permissions = ("﻿" + _PERMISSIONS_XML).encode("utf-8")

    # --- 3. Metadata sub-stream ---
    meta_xml = _build_metadata_xml(queries, load_to_model=load_to_model).encode("utf-8")
    # Empty content ZIP (22 bytes — end-of-central-directory for empty archive)
    meta_content = b"PK\x05\x06" + b"\x00" * 18
    meta_sub = (
        struct.pack("<I", 0)           # Metadata Version = 0
        + struct.pack("<I", len(meta_xml))
        + meta_xml
        + struct.pack("<I", len(meta_content))
        + meta_content
    )

    # --- 4. PermissionBindings — pragmatic minimal: single null byte ---
    perm_bind = b"\x00"

    # --- 5. QDEFF top-level stream (MS-QDEFF §2.2) ---
    qdeff = (
        struct.pack("<I", 0)           # Version = 0
        + struct.pack("<I", len(package_parts))
        + package_parts
        + struct.pack("<I", len(permissions))
        + permissions
        + struct.pack("<I", len(meta_sub))
        + meta_sub
        + struct.pack("<I", len(perm_bind))
        + perm_bind
    )
    return qdeff


def _content_types_xml() -> bytes:
    return _xml_bytes(
        "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">"
        "<Default Extension=\"json\" ContentType=\"\"/>"
        "<Default Extension=\"xml\" ContentType=\"\"/>"
        "<Default Extension=\"rels\" ContentType=\"\"/>"
        "<Override PartName=\"/Version\" ContentType=\"\"/>"
        "<Override PartName=\"/DataMashup\" ContentType=\"\"/>"
        "<Override PartName=\"/DiagramLayout\" ContentType=\"\"/>"
        "<Override PartName=\"/DiagramState\" ContentType=\"\"/>"
        "<Override PartName=\"/SecurityBindings\" ContentType=\"\"/>"
        "<Override PartName=\"/Connections\" ContentType=\"\"/>"
        "<Override PartName=\"/Report/Layout\" ContentType=\"\"/>"
        "<Override PartName=\"/Report/LinguisticSchema\" ContentType=\"\"/>"
        "<Override PartName=\"/Settings\" ContentType=\"application/json\"/>"
        "<Override PartName=\"/Metadata\" ContentType=\"application/json\"/>"
        "<Override PartName=\"/DataModelSchema\" ContentType=\"application/json\"/>"
        "<Override PartName=\"/docProps/custom.xml\" ContentType=\"\"/>"
        "</Types>"
    )


def _rels_xml() -> bytes:
    return _xml_bytes(
        "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
        "<Relationship Id=\"rId1\" "
        "Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties\" "
        "Target=\"docProps/custom.xml\"/>"
        "</Relationships>"
    )


def _custom_props_xml() -> bytes:
    return (
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>"
        "<Properties xmlns=\"http://schemas.openxmlformats.org/officeDocument/2006/custom-properties\" "
        "xmlns:vt=\"http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes\">"
        "<property fmtid=\"{D5CDD505-2E9C-101B-9397-08002B2CF9AE}\" pid=\"2\" name=\"PowerBITemplate\">"
        "<vt:bool>true</vt:bool></property></Properties>"
    ).encode("utf-8")


def _pack_zip(report: Dict[str, Any], model: Dict[str, Any],
              settings: Dict[str, Any], metadata: Dict[str, Any],
              diagram: Dict[str, Any], datamashup: Optional[bytes] = None,
              theme_name: str = "DarkMode",
              theme_data_colors: Optional[List[str]] = None) -> bytes:
    """Assemble the .pbit zip. JSON parts UTF-16LE, XML parts UTF-8.

    Part set mirrors the official Power BI .pbix/.pbit format:
    includes ``_rels/.rels``, ``docProps/custom.xml``, ``Connections``,
    ``Report/LinguisticSchema``, ``SecurityBindings``, ``DiagramState``,
    and ships the chosen Power BI theme.

    ``theme_data_colors`` overrides the theme's ``dataColors`` so charts
    inherit the dashboard's palette by default (the way the 38 reference
    reports show colour — via theme, not per-visual objects).
    """
    dark = (theme_name or "").lower() not in ("cy24su02", "darkblue")
    theme_json = _patch_theme_data_colors(_default_theme_json(dark=dark, theme_name=theme_name),
                                          theme_data_colors)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _content_types_xml())
        zf.writestr("_rels/.rels", _rels_xml())
        zf.writestr("docProps/custom.xml", _custom_props_xml())
        zf.writestr("Version", _PBIX_VERSION.encode("utf-16-le"))
        zf.writestr("Settings", _json_bytes(settings))
        zf.writestr("Metadata", _json_bytes(metadata))
        zf.writestr("Connections", json.dumps({"Version": 3, "RemoteArtifacts": [{"DatasetId": str(_uuid.uuid4()), "ReportId": str(_uuid.uuid4())}]}, separators=(",", ":")).encode("utf-8"))
        zf.writestr("DiagramLayout", _json_bytes(diagram))
        zf.writestr("DiagramState", _json_bytes({"Version": 0, "Data": []}))
        zf.writestr("SecurityBindings", _SECURITY_BINDINGS)
        zf.writestr("Report/StaticResources/SharedResources/BaseThemes/" + theme_name + ".json",
                    theme_json.encode("utf-8"))
        zf.writestr("Report/LinguisticSchema",
                    ('<LinguisticSchema xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
                     'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
                     'Language="en-US" DynamicImprovement="Full" '
                     'xmlns="http://schemas.microsoft.com/sqlserver/reporting/2016/08/reportingservices/semanticschema">'
                     '<Model/></LinguisticSchema>').encode("utf-16-le"))
        zf.writestr("Report/Layout", _json_bytes(report))
        zf.writestr("DataModelSchema", _json_bytes(model))
        if datamashup:
            zf.writestr("DataMashup", datamashup)
    return buf.getvalue()


_DARKBLUE_THEME_PATH = Path(__file__).resolve().parent / "BaseThemes" / "DarkBlue.json"


def _default_theme_json(dark: bool = False, theme_name: Optional[str] = None) -> str:
    """The base theme JSON (embedded as a static resource)."""
    if theme_name and theme_name != "CY24SU02":
        tpath = Path(__file__).resolve().parent / "BaseThemes" / f"{theme_name}.json"
    else:
        tpath = _DARK_THEME_PATH if dark else _THEME_PATH
    fallback = ('{"name":"CY24SU02","dataColors":["#118DFF","#12239E","#E66C37",'
                '"#6B007B","#E044A7","#744EC2","#D9B300","#D64550","#197278","#1AAB40"],'
                '"foreground":"#252423","foregroundNeutralSecondary":"#605E5C",'
                '"foregroundNeutralTertiary":"#B3B0AD","background":"#FFFFFF",'
                '"backgroundLight":"#F3F2F1","backgroundNeutral":"#C8C6C4",'
                '"tableAccent":"#118DFF","good":"#1AAB40","neutral":"#D9B300",'
                '"bad":"#D64554","maximum":"#118DFF","minimum":"#DEEFFF",'
                '"hyperlink":"#0078d4","visitedHyperlink":"#0078d4",'
                '"textClasses":{"callout":{"fontSize":45,"fontFace":"DIN","color":"#252423"},'
                '"title":{"fontSize":12,"fontFace":"DIN","color":"#252423"},'
                '"header":{"fontSize":12,"fontFace":"Segoe UI Semibold","color":"#252423"},'
                '"label":{"fontSize":10,"fontFace":"Segoe UI","color":"#252423"}},'
                '"visualStyles":{"*":{"*":{"*":[{"transparency":0,"wordWrap":true}]}}}}')
    try:
        return tpath.read_text(encoding="utf-8")
    except OSError:
        return fallback


def _patch_theme_data_colors(theme_json: str,
                             colors: Optional[List[str]]) -> str:
    """Return ``theme_json`` with ``dataColors`` replaced by *colors*.

    Charts without per-series overrides inherit these in order — this is how
    real Power BI reports show a branded palette. Unchanged if no colors given
    or the theme fails to parse.
    """
    if not colors:
        return theme_json
    try:
        obj = json.loads(theme_json)
    except Exception:
        return theme_json
    obj["dataColors"] = [str(c) for c in colors]
    return json.dumps(obj, ensure_ascii=False)


# ── public API ──────────────────────────────────────────────────────────

def build_powerbi_pbit(
    dashboard: Dict[str, Any],
    query_runner: Callable[[str], pd.DataFrame],
    table_loader: Callable[[str, int], pd.DataFrame],
    model_name: Optional[str] = None,
    theme_name: Optional[str] = None,
) -> bytes:
    """Build a Power BI (.pbit) file from a saved dashboard.

    Args:
        dashboard: dashboard dict from ``DashboardManager.get`` — expects
            ``name``, ``widgets`` (list), ``layout`` (dict with optional
            ``slicers`` and ``insight``).
        query_runner: callable ``sql -> DataFrame`` used to evaluate each
            widget's ``config.sql``.
        table_loader: callable ``(table, limit) -> DataFrame`` used to load the
            raw source (fact) tables and slicer distinct values.
        model_name: optional model/report title (defaults to dashboard name).
        theme_name: optional theme name ("DarkMode" or "CY24SU02", default
            "DarkMode").

    Returns:
        ``.pbit`` file bytes.
    """
    name = (model_name or dashboard.get("name") or "Dashboard").strip() or "Dashboard"
    # DarkBlue is the custom theme with navy colours matching professional
    # PBI dashboards (see Image 14 reference). verify_export requires the
    # BaseThemes/<name>.json file to exist in the zip.
    theme_name = theme_name or "DarkBlue"
    widgets = dashboard.get("widgets") or []
    layout = dashboard.get("layout") or {}
    slicers = layout.get("slicers") or []
    insight = layout.get("insight") or ""

    used_names: set = set()
    tables: List[Dict[str, Any]] = []
    widget_map: List[Dict[str, Any]] = []
    _kpi_idx = 0  # cycles through _KPI_BG_COLORS for card visuals

    # NOTE: raw fact tables, date calendar tables, and fact→slicer relationships
    # were removed because PBI Desktop's type-detection during M evaluation
    # overrode column types from untyped #table() M expressions and crashed
    # ``ApplyQueryChangesAndCreateRelationships`` (ModelingASOperationException).
    # The pre-computed widget tables + slicer dims are sufficient for every
    # visual to render.

    # 1) widget tables (the visuals' exact data)
    for w in widgets:
        wtype = str(w.get("type", "")).lower()
        if wtype == "text":
            # Text/heading widgets become PBI textbox visuals: they carry no
            # model table or query — just a textbox VC in the report layout.
            _cfg = w.get("config") or {}
            content = str(_cfg.get("content") or w.get("title") or "Text").strip()
            widget_map.append({
                "table": "", "widget": w, "df": None,
                "x_col": "", "num_cols": [], "cols": [],
                "sql": "", "source_table": "",
                "single_visual": _textbox_visual(content, "13pt", bold=False),
                "is_textbox": True,
            })
            continue
        cfg = w.get("config") or {}
        sql = str(cfg.get("sql") or "").strip()
        if not sql:
            continue
        try:
            df = query_runner(sql)
        except Exception as e:  # keep exporting even if one widget fails
            logger.warning("PowerBI export: widget %r query failed: %s", w.get("title"), e)
            continue
        if df is None:
            continue
        df = df.head(MAX_WIDGET_ROWS)
        # Rename DuckDB-generated column names (e.g. "sum(Profit)") to clean
        # PBI-friendly names so the TMSL model, M expression and visual queries
        # all agree on the same identifiers.
        col_renames = _rename_generated_columns(df, wtype)
        if col_renames:
            old_x = cfg.get("xCol") or ""
            if old_x in col_renames:
                cfg["xCol"] = col_renames[old_x]
            cfg["series"] = [col_renames.get(s, s) for s in (cfg.get("series") or [])]
        tname = _unique_name(_clean_name(w.get("title") or wtype, f"{wtype}_widget"), used_names)
        tables.append(_build_table(tname, df, description=f"Widget data: {tname}"))
        col_names = [c[0] for c in _df_to_columns(df)]
        numeric = {c[0] for c in _df_to_columns(df) if c[1] in ("Int64.Type", "number")}
        if wtype in ("kpi", "gauge"):
            # Single-measure visual (card/dial): the value column *is* the
            # measure, no category.
            x_col = ""
            num_cols = [c for c in col_names if c in numeric] or col_names[:1]
        elif wtype == "table":
            x_col = ""
            num_cols = col_names
        else:
            x_col = _safe_column(str(cfg.get("xCol") or ""))
            if x_col not in col_names:
                x_col = col_names[0] if col_names else ""
            num_cols = [c for c in col_names if c != x_col and c in numeric]
            if not num_cols:
                num_cols = [c for c in col_names if c != x_col][:1]
            series = [s for s in (cfg.get("series") or []) if s in col_names]
            if series:
                num_cols = series
        wm = {
            "table": tname, "widget": w, "df": df,
            "x_col": x_col, "num_cols": num_cols, "cols": col_names,
            "sql": sql, "source_table": str(cfg.get("table") or ""),
            "_kpi_idx": _kpi_idx,
        }
        if wtype == "kpi":
            _kpi_idx += 1
        wm["single_visual"] = _chart_from_widget(wm)
        widget_map.append(wm)

    # 3) slicer distinct-value tables
    slicer_tables: List[Dict[str, Any]] = []
    for sl in slicers or []:
        field = _safe_column(str(sl.get("field") or ""))
        table = str(sl.get("table") or "")
        if not field or not table:
            continue
        try:
            df = table_loader(table, MAX_SLICER_ROWS)
        except Exception as e:
            logger.warning("PowerBI export: slicer %r load failed: %s", sl.get("field"), e)
            continue
        if df is None or field not in df.columns:
            continue
        dim = df[[field]].drop_duplicates().dropna().head(MAX_SLICER_ROWS)
        sname = _unique_name(f"{field} (Slicer)", used_names)
        tables.append(_build_table(sname, dim, description=f"Slicer dimension: {field}"))
        slicer_tables.append({
            "table": sname, "field": field, "raw_field": str(sl.get("field") or ""),
            "source_table": table,
            "values": {str(v) for v in dim[field].tolist()},
            "kind": str(sl.get("kind") or "select"),
        })

    # 5) relationships: slicer dim (one) -> matching widget tables (many)
    relationships: List[Dict[str, Any]] = []
    for st in slicer_tables:
        for wm in widget_map:
            if not _EMIT_RELATIONSHIPS:
                break
            if not wm.get("x_col"):  # kpi / table widgets have no category axis
                continue
            dim_field = _extract_dimension(wm["sql"])
            if not dim_field:
                continue
            if dim_field.lower() != st["raw_field"].lower():
                continue
            if wm["source_table"] and wm["source_table"] != st["source_table"]:
                continue
            # Verify the widget's category values live in the slicer domain so
            # the relationship actually filters (and never orphans rows).
            cat_vals = {str(v) for v in wm["df"][wm["x_col"]].tolist() if v is not None}
            if cat_vals and not cat_vals.issubset(st["values"]):
                continue
            relationships.append(_build_relationship(
                f"rel-{wm['table']}-{st['table']}",
                wm["table"], wm["x_col"],
                st["table"], st["field"],
            ))
            break

    model = _build_model(name, tables, relationships)

    # 6) report page — everything must fit within 720px
    page_width = 1280.0
    page_height = 720.0
    _margin = 10.0
    _gap = 4.0
    _GRID_COLS = 12
    has_slicers = bool(slicer_tables)

    containers: List[Dict[str, Any]] = []
    vis_id = 0

    def add_vc(x: float, y: float, w: float, h: float, sv: Dict[str, Any],
               has_data: bool = True) -> None:
        nonlocal vis_id
        nm = _hex_name()
        vc: Dict[str, Any] = {
            "x": round(x, 4), "y": round(y, 4), "z": 0,
            "width": round(w, 4), "height": round(h, 4),
            "config": _visual_config(nm, x, y, w, h, sv),
            "filters": "[]",
        }
        if has_data:
            # THE fix for "opens with no data": PBI Desktop renders a data
            # visual from its top-level `query` + `dataTransforms`. Without them
            # the visual is empty.
            vc["query"] = _make_visual_query(sv)
            vc["dataTransforms"] = _make_visual_data_transforms(sv)
            vc["tabOrder"] = vis_id
        vis_id += 1
        containers.append(vc)

    # header: title + optional insight banner
    _title_h = 42.0
    _insight_h = 40.0
    y_cursor = 6.0
    add_vc(_margin, y_cursor, page_width - 2 * _margin, _title_h,
           _textbox_visual(name, "18pt", bold=True), has_data=False)
    y_cursor += _title_h + 6.0
    if insight:
        add_vc(_margin, y_cursor, page_width - 2 * _margin, _insight_h,
               _textbox_visual(insight, "11pt", background="#E8F0FE"), has_data=False)
        y_cursor += _insight_h + 6.0

    # horizontal slicer band (one row). A left rail would eat ~200px of the
    # 720px page and push the widget grid below the visible canvas; the samples
    # put their filter chips across the top instead.
    _slicer_h = 36.0
    if has_slicers:
        _slicer_top = y_cursor
        n = len(slicer_tables)
        _slicer_w = min(260.0, (page_width - 2 * _margin - (n - 1) * _gap) / n)
        sx = _margin
        for st in slicer_tables:
            add_vc(sx, _slicer_top, _slicer_w, _slicer_h,
                   _slicer_visual(st["table"], st["field"], st["raw_field"]))
            sx += _slicer_w + _gap
        y_cursor = _slicer_top + _slicer_h + 2.0

    # widget grid — position-driven (WYSIWYG). Each widget's stored col/row
    # (1-based, 12-col grid) maps to a fixed PBI canvas position using the SAME
    # formula as the builder canvas, so the exported page matches the design
    # instead of being re-packed into rows (which made drag positions pointless
    # and the layout "plain"). Fixed row height from layout.row_height.
    _grid_top = y_cursor
    avail_w = page_width - 2 * _margin
    # CSS-grid semantics so the export matches the builder canvas exactly:
    # unit_w excludes the inter-column gaps, and a k-wide widget spans
    # k*unit_w + (k-1)*gap — so the right edge of a full-width widget lands
    # exactly on page_width - margin (no overflow past the page edge).
    unit_w = (avail_w - (_GRID_COLS - 1) * _gap) / float(_GRID_COLS)
    row_h = float((layout.get("row_height") if isinstance(layout, dict) else None) or 80)
    _row_gap = _gap

    # Card visuals need >= 3 row units so title + big number fit without clipping.
    for wm in widget_map:
        vtype = (wm.get("single_visual") or {}).get("visualType", "")
        if vtype in ("cardVisual", "card", "multiRowCard") and int(wm["widget"].get("rows", 2) or 2) < 3:
            wm["widget"]["rows"] = 3

    # Auto-pack: if any widget is missing col/row, assign positions left-to-
    # right in the 12-col grid so they don't all land on cell (1,1).
    _auto_cursor = 0
    _auto_row = 1
    # ── Two-pass position layout ────────────────────────────────────────
    # Pass 1: auto-pack missing col/row, compute each row band's bottom Y.
    from collections import defaultdict
    _row_max_bottom: Dict[int, float] = defaultdict(float)
    _auto_cursor = 0
    _auto_row = 1
    for wm in widget_map:
        w = wm["widget"]
        cols_n = max(1, min(int(w.get("cols") or 6), 12))
        rows_n = max(1, min(int(w.get("rows") or 3), 10))
        col = w.get("col")
        row = w.get("row")
        if col is None or row is None:
            if _auto_cursor + cols_n > _GRID_COLS:
                _auto_row += max(1, rows_n)
                _auto_cursor = 0
            col = _auto_cursor + 1
            row = _auto_row
            _auto_cursor += cols_n
        wm["_col"] = max(1, int(col))
        wm["_row"] = max(1, int(row))
        hgt = rows_n * row_h + (rows_n - 1) * _row_gap
        wm["_hgt"] = hgt
        y_top = _grid_top + (row - 1) * (row_h + _row_gap)
        _row_max_bottom[row] = max(_row_max_bottom[row], y_top + hgt)

    # Pass 2: compute the actual Y start for each row so rows don't overlap
    # when a preceding row has a tall widget.  row_y[row] is where that row
    # starts; it must be after the bottom of the tallest widget in row-1.
    _sorted_rows = sorted(_row_max_bottom.keys())
    _row_y: Dict[int, float] = {}
    for i, r in enumerate(_sorted_rows):
        if i == 0:
            _row_y[r] = _grid_top
        else:
            prev_r = _sorted_rows[i - 1]
            prev_bottom = _row_max_bottom[prev_r]
            # Row r nominally starts at _grid_top + (r-1)*(row_h + gap),
            # but must be at least prev_bottom + gap.
            nominal = _grid_top + (r - 1) * (row_h + _row_gap)
            _row_y[r] = max(nominal, prev_bottom + _row_gap)

    # Pass 3: assign final x/y and emit containers.
    grid_bottom = _grid_top
    for wm in widget_map:
        w = wm["widget"]
        cols_n = max(1, min(int(w.get("cols") or 6), 12))
        rows_n = max(1, min(int(w.get("rows") or 3), 10))
        col = wm["_col"]
        row = wm["_row"]
        x = _margin + (col - 1) * (unit_w + _gap)
        y = _row_y.get(row, _grid_top + (row - 1) * (row_h + _row_gap))
        wdt = cols_n * unit_w + (cols_n - 1) * _gap
        hgt = wm["_hgt"]
        add_vc(x, y, wdt, hgt, wm["single_visual"],
               has_data=not wm.get("is_textbox"))
        grid_bottom = max(grid_bottom, y + hgt)

    # Page grows with the grid (min 720px so small dashboards stay tidy).
    page_height = max(720.0, grid_bottom + _margin)

    report = {
        "id": 0,
        "theme": theme_name,
        "resourcePackages": [
            {
                "resourcePackage": {
                    "disabled": False,
                    "items": [{"name": theme_name, "path": "BaseThemes/" + theme_name + ".json", "type": 202}],
                    "name": "SharedResources",
                    "type": 2,
                }
            }
        ],
        "layoutOptimization": 0,
        "sections": [
            {
                "id": 0,
                "name": "ReportSection",
                "displayName": _clean_name(name, "Dashboard"),
                "filters": "[]",
                "ordinal": 0,
                "config": "{}",
                "displayOption": 1,
                "width": page_width,
                "height": page_height,
                "visualContainers": containers,
            }
        ],
        "config": json.dumps({
            "version": "5.15",
            "themeCollection": {
                "baseTheme": {"name": theme_name, "version": "5.10", "type": 2},
            },
            "activeSectionIndex": 0,
            "defaultDrillFilterOtherVisuals": True,
            "linguisticSchemaSyncVersion": 2,
            "settings": {
                "useNewFilterPaneExperience": True,
                "allowChangeFilterTypes": True,
                "useStylableVisualContainerHeader": True,
                "exportDataMode": 1,
                "allowDataPointLassoSelect": True,
            },
            "objects": {
                "outspacePane": [{"properties": {"expanded": {"expr": {"Literal": {"Value": "false"}}}}}],
            },
        }, separators=(",", ":")),
    }

    settings = {
        "Version": 1,
        "ReportSettings": {},
        "QueriesSettings": {
            "TypeDetectionEnabled": True,
            "RelationshipImportEnabled": True,
            "Version": "2.128.184.0",
        },
    }
    metadata = {
        "Version": 5,
        "AutoCreatedRelationships": [],
        "FileDescription": f"Exported from dashboard: {name}",
        "CreatedFrom": "Cloud",
        "CreatedFromRelease": _CREATED_FROM_RELEASE,
    }
    # Build a proper DiagramLayout so the model diagram view in PBI Desktop
    # shows tables positioned in a readable grid instead of an empty canvas.
    _node_w, _node_h = 180.0, 120.0
    _node_gap_x, _node_gap_y = 60.0, 40.0
    _grid_cols_diag = 4
    diag_nodes = []
    for _di, _tbl in enumerate(tables):
        _col = _di % _grid_cols_diag
        _row = _di // _grid_cols_diag
        diag_nodes.append({
            "query": _tbl.get("name", ""),
            "x": round(20.0 + _col * (_node_w + _node_gap_x), 1),
            "y": round(20.0 + _row * (_node_h + _node_gap_y), 1),
            "width": _node_w,
            "height": _node_h,
        })
    diagram = {
        "version": "1.1.0",
        "diagrams": [{
            "id": 0,
            "name": "Diagram",
            "nodes": diag_nodes,
            "links": [],
        }],
    }

    # 7) DataMashup — always shipped. Power BI Desktop's open/refresh pipeline
    # requires the part even for a calculated-tables-only model (every genuine
    # .pbit/.pbix carries one). Every table here is a DAX ``DATATABLE``
    # calculated partition, so there are no M *model* partitions. Instead of
    # the empty calc-only mashup we embed one connection-only, private M query
    # per exported table (Table.FromRows over the same data) — visible data
    # lineage in Power Query with zero model-load/refresh impact.
    mashup_queries: List[Tuple[str, str]] = []
    load_to_model = False
    for t in tables:
        for p in t.get("partitions") or []:
            src = p.get("source") or {}
            expr = str(src.get("expression") or "").strip()
            if src.get("type") == "m" and expr:
                mashup_queries.append((str(t.get("name")), expr))
                load_to_model = True
                break
    if not mashup_queries:
        seen_names: set = set()
        for entry in widget_map:
            df_w = entry.get("df")
            tbl = str(entry.get("table") or "")
            if df_w is None or df_w.empty or not tbl or tbl in seen_names:
                continue
            seen_names.add(tbl)
            try:
                mashup_queries.append((tbl, _build_m_query(df_w)))
            except Exception as e:  # noqa: BLE001 — lineage is best-effort
                logger.warning("PowerBI export: M lineage query for %r failed: %s", tbl, e)
    datamashup = (_build_datamashup(mashup_queries, load_to_model=load_to_model)
                  if mashup_queries else _CALC_ONLY_MASHUP)

    # 8) dashboard palette → theme dataColors (charts inherit it by default).
    theme_data_colors: List[str] = []
    lp = layout.get("palette") if isinstance(layout, dict) else None
    if isinstance(lp, dict):
        theme_data_colors = [str(c) for c in (lp.get("colors") or []) if isinstance(c, str)]
    if not theme_data_colors:
        for w in widgets:
            s = (w.get("config") or {}).get("style")
            if isinstance(s, dict) and isinstance(s.get("colors"), list):
                theme_data_colors = [str(c) for c in s["colors"] if isinstance(c, str)]
                break

    # 9) assemble the zip
    return _pack_zip(report, model, settings, metadata, diagram, datamashup,
                     theme_name=theme_name,
                     theme_data_colors=theme_data_colors or None)


# ── CLI ──────────────────────────────────────────────────────────────────

def main() -> None:
    """Build a .pbit from a saved dashboard without the web app.

    ``python -m export.pbix_export --dashboard <id> --out out.pbit [--agg 0|1]``
    """
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Build a Power BI .pbit from a saved dashboard")
    ap.add_argument("--dashboard", required=True, help="saved dashboard id")
    ap.add_argument("--out", default="out.pbit", help="output .pbit path")
    ap.add_argument("--agg", type=int, choices=[0, 1], default=None,
                    help="override the aggregate-query style (1 = real-PBI Aggregation, 0 = plain Column)")
    args = ap.parse_args()

    global _USE_AGG_QUERIES
    if args.agg is not None:
        _USE_AGG_QUERIES = bool(args.agg)
        print(f"query style: {'Aggregation (real PBI)' if _USE_AGG_QUERIES else 'plain Column'}")

    import duckdb
    from config import settings
    from api.dashboards import get_dashboard_manager

    con = duckdb.connect(str(settings.duckdb.database_path))
    con.execute(f"SET search_path = '{settings.duckdb.schema}'")

    def run_query(sql: str) -> pd.DataFrame:
        return con.execute(sql).df()

    def load_table(table: str, limit: int) -> pd.DataFrame:
        return con.execute(f'SELECT * FROM "{table}" LIMIT {max(1, int(limit))}').df()

    mgr = get_dashboard_manager()
    dash = mgr.get(args.dashboard)
    if not dash:
        print(f"Dashboard {args.dashboard!r} not found")
        sys.exit(1)

    print(f"Building .pbit for dashboard: {dash.get('name')}")
    print(f"  widgets: {len(dash.get('widgets') or [])}, slicers: {len((dash.get('layout') or {}).get('slicers') or [])}")
    data = build_powerbi_pbit(dash, run_query, load_table)
    with open(args.out, "wb") as fh:
        fh.write(data)
    print(f"Wrote {args.out} ({len(data):,} bytes)")


if __name__ == "__main__":
    main()
