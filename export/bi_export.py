"""
BI Export Module
Export warehouse data for Power BI, Tableau, and other BI tools

Features:
- Export tables as CSV, Parquet, or formatted Excel
- Generate BI connection config and schema documentation
- Package all tables as a ZIP for easy import
- Generate Power BI / Tableau connection instructions
"""

import html
import io
import json
import logging
import os
import zipfile
from typing import Dict, List, Optional, Any, Union
from pathlib import Path

import duckdb
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, numbers
from openpyxl.utils import get_column_letter

logger = logging.getLogger(__name__)


# ── injection hardening ──────────────────────────────────────────────────
# Exported data is attacker-controlled: an uploaded CSV can carry
# ``=HYPERLINK("http://evil","x")`` or ``=cmd|' /C calc'!A1``.  Excel,
# LibreOffice and Google Sheets evaluate a cell whose text starts with
# ``= + - @`` as a *live formula* when the exported file is opened, so every
# string written to a spreadsheet is prefixed with a single quote, which
# forces the value to be stored and displayed as text.

_FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@")
_LEADING_WHITESPACE = " \t\r\n"


def sanitize_spreadsheet_value(value: Any) -> Any:
    """Neutralise spreadsheet formula injection in a single cell value.

    A string whose first non-whitespace character (whitespace includes tab,
    CR and LF, the classic ``\\t=cmd|...`` DDE vector) is one of
    ``= + - @`` is prefixed with a single quote.  Non-string values are
    returned untouched, so a float stays a float and is still written as a
    number rather than as text.

    This is part of the module's public API: ``export/dashboard_export.py``
    sanitises its cells with it, so it must not be private.
    """
    if not isinstance(value, str):
        return value
    if value.lstrip(_LEADING_WHITESPACE)[:1] in _FORMULA_TRIGGER_CHARS:
        return "'" + value
    return value


def _sanitize_series(series: pd.Series) -> pd.Series:
    """Neutralise formula-like strings inside one column."""
    if isinstance(series.dtype, pd.CategoricalDtype):
        series = series.astype(object)
    if series.dtype == object or pd.api.types.is_string_dtype(series):
        return series.map(sanitize_spreadsheet_value)
    return series


def sanitize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of ``df`` with formula-like strings neutralised.

    Column names are sanitised as well — a header called ``=cmd|...`` is a
    formula too.  Numeric, boolean and date columns keep their dtype.
    Columns are addressed by position so a table with duplicate column
    names cannot break the export.
    """
    out = df.copy()
    for idx in range(out.shape[1]):
        out.isetitem(idx, _sanitize_series(out.iloc[:, idx]))
    out.columns = [sanitize_spreadsheet_value(str(c)) for c in out.columns]
    return out


def _escape_markdown_cell(value: Any) -> str:
    """Escape a value for use inside a Markdown table cell.

    Line breaks are flattened (a newline inside a cell would split the row),
    HTML is neutralised so a cell cannot inject markup into the rendered
    document, and the column separator is escaped so the table stays
    structurally valid.
    """
    text = str(value).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    text = html.escape(text, quote=False)
    return text.replace("|", "\\|")


def _html_escape(value: Any) -> str:
    """HTML-escape a value for safe interpolation into an HTML document."""
    return html.escape(str(value), quote=True)


def _get_connection(
    db_path: Union[str, Path],
    schema: str = "analytics",
):
    """Open a DuckDB connection with schema set."""
    conn = duckdb.connect(str(db_path))
    conn.execute(f"SET search_path = '{schema}'")
    return conn


def load_table_frame(
    table: str,
    db_path: Union[str, Path] = "data/curated/warehouse.duckdb",
    schema: str = "analytics",
) -> pd.DataFrame:
    """Read a warehouse table into a DataFrame (read-only)."""
    conn = _get_connection(db_path, schema)
    try:
        return conn.execute(f"SELECT * FROM {schema}.{table}").df()
    finally:
        conn.close()


def export_table_csv(
    table: str,
    db_path: Union[str, Path] = "data/curated/warehouse.duckdb",
    schema: str = "analytics",
    encoding: str = "utf-8",
) -> str:
    """
    Export a warehouse table as CSV string.

    Cell values and column names are sanitised so a spreadsheet cannot
    evaluate them as formulas (see ``sanitize_spreadsheet_value``).

    Args:
        table: Table name
        db_path: Path to DuckDB database
        schema: Schema name
        encoding: File encoding

    Returns:
        CSV content as string
    """
    df = load_table_frame(table, db_path, schema)
    return sanitize_dataframe(df).to_csv(index=False, encoding=encoding)


def export_table_csv_bytes(
    table: str,
    db_path: Union[str, Path] = "data/curated/warehouse.duckdb",
    schema: str = "analytics",
) -> bytes:
    """Export a warehouse table as CSV bytes."""
    csv_str = export_table_csv(table, db_path, schema)
    return csv_str.encode("utf-8")


def _max_export_rows() -> int:
    """Row ceiling for exports (override via EXPORT_MAX_ROWS env var)."""
    try:
        return max(1000, int(os.environ.get("EXPORT_MAX_ROWS", "100000")))
    except (TypeError, ValueError):
        return 100000


def export_table_xlsx(
    table: str,
    db_path: Union[str, Path] = "data/curated/warehouse.duckdb",
    schema: str = "analytics",
) -> bytes:
    """
    Export a warehouse table as a formatted Excel (.xlsx) file.

    Features:
    - Header row with bold white text on a dark blue background
    - Auto-column width based on content length (capped at 60 chars)
    - Number formatting for numeric cells (commas, decimals)
    - Freeze top row (header always visible)
    - Auto-filter on header row
    - Sheet named after the table
    - Row-capped so huge tables can't exhaust memory; a truncation
      notice is written to a second sheet when capped.
    - Cell values and column names are sanitised so Excel cannot
      evaluate them as formulas.

    Args:
        table: Table name
        db_path: Path to DuckDB database
        schema: Schema name

    Returns:
        Excel file as bytes
    """
    conn = _get_connection(db_path, schema)
    try:
        total_rows = conn.execute(f"SELECT COUNT(*) FROM {schema}.{table}").fetchone()[0]
        max_rows = _max_export_rows()
        df = conn.execute(
            f"SELECT * FROM {schema}.{table} LIMIT {int(max_rows)}"
        ).df()
    finally:
        conn.close()

    truncated = total_rows > len(df)

    wb = Workbook()
    ws = wb.active
    ws.title = table[:31]  # Excel sheet name limit

    _write_xlsx_header(ws, df.columns)
    _write_xlsx_rows(ws, df)
    _set_xlsx_widths(ws, df)
    ws.freeze_panes = "A2"
    _set_xlsx_autofilter(ws, df)
    if truncated:
        _write_xlsx_truncation_notice(
            wb, schema, table, len(df), total_rows, max_rows
        )

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


def _write_xlsx_header(ws, columns) -> None:
    """Write the styled header row (column names are sanitised)."""
    header_fill = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True, size=11)

    for col_idx, col_name in enumerate(columns, start=1):
        cell = ws.cell(
            row=1,
            column=col_idx,
            value=sanitize_spreadsheet_value(str(col_name)),
        )
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")


def _write_xlsx_rows(ws, df) -> None:
    """Write the data rows; string cells are neutralised against formulas."""
    for row_idx, (_, row) in enumerate(df.iterrows(), start=2):
        for col_idx, col_name in enumerate(df.columns, start=1):
            val = row[col_name]
            cell = ws.cell(row=row_idx, column=col_idx)

            if val is None or (isinstance(val, float) and pd.isna(val)):
                cell.value = None
            else:
                cell.value = sanitize_spreadsheet_value(val)

            # Number formatting for numeric types
            dtype = df[col_name].dtype
            if pd.api.types.is_integer_dtype(dtype):
                cell.number_format = '#,##0'
            elif pd.api.types.is_float_dtype(dtype):
                cell.number_format = '#,##0.00'


def _set_xlsx_widths(ws, df) -> None:
    """Size each column from the header and the widest (capped) cell."""
    for col_idx, col_name in enumerate(df.columns, start=1):
        header_len = len(str(col_name))
        data_len = (
            df[col_name].astype(str).str.len().max()
            if len(df) > 0
            else 0
        )
        data_len = min(data_len, 60) if data_len > 0 else 0
        best_width = max(header_len + 2, data_len + 2, 10)
        ws.column_dimensions[get_column_letter(col_idx)].width = best_width


def _set_xlsx_autofilter(ws, df) -> None:
    """Enable the header filter across the written range."""
    if len(df.columns) == 0:
        return
    last_col_letter = get_column_letter(len(df.columns))
    last_row = len(df) + 1  # +1 for header
    ws.auto_filter.ref = f"A1:{last_col_letter}{last_row}"


def _write_xlsx_truncation_notice(
    wb, schema: str, table: str, exported: int, total: int, max_rows: int
) -> None:
    """Add the 'Export Info' sheet describing a capped export."""
    info = wb.create_sheet("Export Info")
    info.column_dimensions["A"].width = 30
    notes = [
        ("Table", table),
        ("Rows exported", exported),
        ("Rows in warehouse", total),
        ("Truncated", "yes — export capped at "
         f"{max_rows:,} rows (EXPORT_MAX_ROWS)"),
    ]
    for r, (k, v) in enumerate(notes, start=1):
        kc = info.cell(row=r, column=1, value=k)
        kc.font = Font(bold=True)
        info.cell(row=r, column=2, value=str(v))
    logger.warning(
        "Export of %s.%s truncated: %d of %d rows (cap=%d)",
        schema, table, exported, total, max_rows,
    )


def export_table_parquet(
    table: str,
    db_path: Union[str, Path] = "data/curated/warehouse.duckdb",
    schema: str = "analytics",
) -> bytes:
    """
    Export a warehouse table as Parquet bytes.

    Args:
        table: Table name
        db_path: Path to DuckDB database
        schema: Schema name

    Returns:
        Parquet file as bytes
    """
    conn = _get_connection(db_path, schema)
    try:
        df = conn.execute(f"SELECT * FROM {schema}.{table}").df()
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        return buf.getvalue()
    finally:
        conn.close()


def export_all_tables(
    output_dir: Union[str, Path] = "outputs/bi_export",
    db_path: Union[str, Path] = "data/curated/warehouse.duckdb",
    schema: str = "analytics",
    fmt: str = "csv",
) -> List[str]:
    """
    Export all tables in the schema to files.

    Args:
        output_dir: Output directory for exported files
        db_path: Path to DuckDB database
        schema: Schema name
        fmt: Export format ('csv' or 'parquet')

    Returns:
        List of exported file paths
    """
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    conn = _get_connection(db_path, schema)
    exported = []

    try:
        tables = conn.execute(
            f"SELECT table_name FROM information_schema.tables "
            f"WHERE table_schema = '{schema}'"
        ).fetchall()

        for (table_name,) in tables:
            try:
                df = conn.execute(f"SELECT * FROM {schema}.{table_name}").df()
                if fmt == "parquet":
                    filepath = output / f"{table_name}.parquet"
                    df.to_parquet(filepath, index=False)
                else:
                    filepath = output / f"{table_name}.csv"
                    sanitize_dataframe(df).to_csv(filepath, index=False)
                exported.append(str(filepath))
                logger.info(f"Exported {table_name} ({df.shape[0]} rows) to {filepath}")
            except Exception as e:
                logger.warning(f"Failed to export {table_name}: {e}")

    finally:
        conn.close()

    return exported


def export_all_as_zip(
    db_path: Union[str, Path] = "data/curated/warehouse.duckdb",
    schema: str = "analytics",
    tables: Optional[List[str]] = None,
) -> bytes:
    """
    Export tables as a ZIP archive containing CSV files.

    Args:
        tables: Optional whitelist of table names to include. None or empty
            means every table in the schema. Names not present in the schema
            raise ValueError before any SQL runs (catalog acts as allowlist).

    Returns:
        ZIP archive as bytes
    """
    buf = io.BytesIO()

    conn = _get_connection(db_path, schema)
    try:
        available = [
            row[0]
            for row in conn.execute(
                f"SELECT table_name FROM information_schema.tables "
                f"WHERE table_schema = '{schema}'"
            ).fetchall()
        ]

        if tables:
            requested = [t for t in tables if t]
            missing = [t for t in requested if t not in available]
            if missing:
                raise ValueError(
                    f"Unknown table(s) in schema '{schema}': {', '.join(missing)}"
                )
            selected = requested
        else:
            selected = available

        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for table_name in selected:
                try:
                    df = conn.execute(f"SELECT * FROM {schema}.{table_name}").df()
                    csv_data = sanitize_dataframe(df).to_csv(index=False)
                    zf.writestr(f"{schema}_{table_name}.csv", csv_data)
                    logger.info(f"Packed {table_name} ({df.shape[0]} rows)")
                except Exception as e:
                    logger.warning(f"Failed to pack {table_name}: {e}")

    finally:
        conn.close()

    return buf.getvalue()


def get_bi_config(
    db_path: Union[str, Path] = "data/curated/warehouse.duckdb",
    schema: str = "analytics",
) -> Dict[str, Any]:
    """
    Generate BI connection configuration for Power BI and Tableau.

    Returns a dict with:
    - connection info (DuckDB file path, ODBC string)
    - table schemas with columns and relationships
    - step-by-step connection instructions for each tool
    """
    db_path = Path(db_path).resolve()

    conn = _get_connection(db_path, schema)
    config: Dict[str, Any] = {
        "database": {
            "type": "DuckDB",
            "file_path": str(db_path),
            "schema": schema,
        },
        "connection_strings": {
            "duckdb_file": f"duckdb:///{db_path}",
            "odbc_dsn": f"DRIVER={{DuckDB}};Database={db_path}",
        },
        "tables": {},
        "relationships": [],
    }

    try:
        # Get table list
        tables = conn.execute(
            f"SELECT table_name FROM information_schema.tables "
            f"WHERE table_schema = '{schema}' ORDER BY table_name"
        ).fetchall()

        for (table_name,) in tables:
            cols = conn.execute(f"DESCRIBE {schema}.{table_name}").fetchall()
            count = conn.execute(
                f"SELECT COUNT(*) FROM {schema}.{table_name}"
            ).fetchone()[0]

            config["tables"][table_name] = {
                "row_count": count,
                "columns": [
                    {"name": c[0], "type": c[1], "nullable": c[2]}
                    for c in cols
                ],
            }

        # Define suggested relationships
        config["relationships"] = [
            {"from": "sales.customer_id", "to": "customers.customer_id"},
            {"from": "sales.product_id", "to": "products.product_id"},
            {"from": "orders.customer_id", "to": "customers.customer_id"},
        ]

        # Power BI instructions
        config["power_bi"] = {
            "title": "Connecting Power BI to DuckDB",
            "steps": [
                "Open Power BI Desktop",
                "Click Home > Get Data > More > ODBC",
                f"Select DSN or enter connection string:\n  {config['connection_strings']['odbc_dsn']}",
                "Navigate to the analytics schema and select the tables you need",
                "Use the recommended relationships below to build your data model",
                "Create measures using DAX as needed",
            ],
            "dax_examples": {
                "Total Revenue": "SUM(sales[total_amount])",
                "Avg Order Value": "DIVIDE([Total Revenue], DISTINCTCOUNT(sales[order_id]))",
                "Revenue Growth %": "VAR _cur = [Total Revenue] RETURN DIVIDE([Total Revenue] - [PrevRevenue], [PrevRevenue])",
            },
        }

        # Tableau instructions
        config["tableau"] = {
            "title": "Connecting Tableau to DuckDB",
            "steps": [
                "Install the DuckDB ODBC driver (https://github.com/duckdb/duckdb-odbc)",
                "Open Tableau Desktop",
                "Connect > To a Server > Other Databases (ODBC)",
                f"Enter DSN: {config['connection_strings']['odbc_dsn']}",
                "Select the analytics schema and your tables",
                "Define relationships in the Data Source page",
                "Build worksheets and dashboards",
            ],
            "calculated_field_examples": {
                "Profit Margin": "SUM([total_amount]) - SUM([shipping_cost])",
                "Revenue per Customer": "SUM([total_amount]) / COUNTD([customer_id])",
            },
        }

    finally:
        conn.close()

    return config


def get_schema_doc(
    db_path: Union[str, Path] = "data/curated/warehouse.duckdb",
    schema: str = "analytics",
) -> str:
    """
    Generate a Markdown documentation of the warehouse schema.

    Useful for sharing with the BI team.
    """
    config = get_bi_config(db_path, schema)
    lines = [
        f"# {schema.upper()} Warehouse Schema\n",
        f"**Database:** `{config['database']['file_path']}`\n",
        f"**Tables:** {len(config['tables'])}\n",
        "## Tables\n",
    ]

    for table_name, table_info in config["tables"].items():
        lines.append(f"### {table_name} ({table_info['row_count']} rows)\n")
        lines.append("| Column | Type | Nullable |")
        lines.append("|--------|------|----------|")
        for col in table_info["columns"]:
            lines.append(f"| `{col['name']}` | {col['type']} | {col['nullable']} |")
        lines.append("")

    if config["relationships"]:
        lines.append("## Relationships\n")
        lines.append("| From | To |")
        lines.append("|------|----|")
        for rel in config["relationships"]:
            lines.append(f"| `{rel['from']}` | `{rel['to']}` |")
        lines.append("")

    return "\n".join(lines)


def export_table_markdown(
    table: str,
    db_path: Union[str, Path] = "data/curated/warehouse.duckdb",
    schema: str = "analytics",
    max_rows: int = 100,
) -> str:
    """
    Export a table as a Markdown-formatted string.

    Cell values and column names are HTML-escaped so a cell cannot inject
    markup into the rendered document, and the column separator is escaped
    so the table stays structurally valid.

    Args:
        table: Table name
        db_path: Path to DuckDB database
        schema: Schema name
        max_rows: Maximum rows to include

    Returns:
        Markdown-formatted table string
    """
    conn = _get_connection(db_path, schema)
    try:
        df = conn.execute(f"SELECT * FROM {schema}.{table}").df()
        total_rows = len(df)
        df = df.head(max_rows)

        lines = [f"## {_escape_markdown_cell(table)} ({total_rows} rows)\n"]
        if total_rows > max_rows:
            lines.append(f"*Showing {max_rows} of {total_rows} rows*\n")

        # Header
        cols = list(df.columns)
        lines.append("| " + " | ".join(_escape_markdown_cell(c) for c in cols) + " |")
        lines.append("| " + " | ".join("---" for _ in cols) + " |")

        # Rows
        for _, row in df.iterrows():
            vals = []
            for c in cols:
                v = row[c]
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    vals.append("NULL")
                elif isinstance(v, (int, float)):
                    vals.append(f"{v:,.2f}" if isinstance(v, float) else f"{v:,}")
                else:
                    vals.append(sanitize_spreadsheet_value(_escape_markdown_cell(v)))
            lines.append("| " + " | ".join(vals) + " |")

        return "\n".join(lines)
    finally:
        conn.close()


def export_report_html(
    table: str,
    db_path: Union[str, Path] = "data/curated/warehouse.duckdb",
    schema: str = "analytics",
    title: str = "",
) -> bytes:
    """
    Export a table as a styled HTML report.

    Every interpolated column name, cell value and title is HTML-escaped,
    so attacker-controlled data cannot inject markup into the document.

    Returns HTML as bytes.
    """
    conn = _get_connection(db_path, schema)
    try:
        df = conn.execute(f"SELECT * FROM {schema}.{table}").df()
    finally:
        conn.close()

    display_title = _html_escape(title or f"Data Report: {table}")
    cols = list(df.columns)
    total = len(df)

    stats_html = _html_stats_block(df)

    # Data table (max 200 rows)
    table_html = _html_data_table(df, cols)
    if total > 200:
        table_html += f"<p style='color:#666;'>Showing 200 of {total} rows</p>"

    html = f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'><title>{display_title}</title>
<style>
    body {{ font-family: -apple-system, sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px; }}
    h1 {{ color: #2F5496; border-bottom: 2px solid #2F5496; padding-bottom: 10px; }}
    h3 {{ color: #333; }}
    table {{ border-radius: 4px; overflow: hidden; }}
    tr:nth-child(even) {{ background: #f8f9fa; }}
</style></head>
<body>
    <h1>{display_title}</h1>
    <p>Generated on {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')} | {total} rows × {len(cols)} columns</p>
    {stats_html}
    <h3>Data Preview</h3>
    {table_html}
</body></html>"""

    return html.encode("utf-8")


def _html_stats_block(df) -> str:
    """Render the numeric column statistics block (escaped)."""
    num_cols = df.select_dtypes(include=["number"]).columns
    if len(num_cols) == 0:
        return ""

    out = [
        "<div style='margin:20px 0;'><h3>Column Statistics</h3>"
        "<table border='1' cellpadding='6' cellspacing='0' "
        "style='border-collapse:collapse;font-size:13px;'>"
    ]
    out.append(
        "<tr><th>Column</th><th>Count</th><th>Mean</th>"
        "<th>Std</th><th>Min</th><th>Max</th></tr>"
    )
    for col in num_cols:
        s = df[col].dropna()
        out.append(
            f"<tr><td><strong>{_html_escape(col)}</strong></td><td>{len(s)}</td>"
            f"<td>{s.mean():.2f}</td><td>{s.std():.2f}</td>"
            f"<td>{s.min():.2f}</td><td>{s.max():.2f}</td></tr>"
        )
    out.append("</table></div>")
    return "".join(out)


def _html_data_table(df, cols) -> str:
    """Render the (max 200 row) data table with every value escaped.

    Values are also neutralised against formula injection: Excel evaluates
    a cell that starts with ``=`` even when the file is an HTML table.
    """
    out = [
        "<table border='1' cellpadding='6' cellspacing='0' "
        "style='border-collapse:collapse;font-size:12px;'>"
    ]
    out.append(
        "<tr style='background:#2F5496;color:white;'>"
        + "".join(f"<th>{_html_escape(c)}</th>" for c in cols)
        + "</tr>"
    )
    for _, row in df.head(200).iterrows():
        cells = []
        for c in cols:
            value = row[c]
            if pd.isna(value):
                cells.append("<td></td>")
            else:
                safe = sanitize_spreadsheet_value(value)
                cells.append(f"<td>{_html_escape(safe)}</td>")
        out.append("<tr>" + "".join(cells) + "</tr>")
    out.append("</table>")
    return "".join(out)


def export_table_pptx(
    table: str,
    db_path: Union[str, Path] = "data/curated/warehouse.duckdb",
    schema: str = "analytics",
    title: str = "",
) -> bytes:
    """
    Export a table as a PowerPoint presentation.

    Slide 1: Title + summary stats
    Slide 2: Key metrics
    Slide 3: Data preview table

    Returns PPTX as bytes.

    Note: Requires python-pptx package.
    """
    try:
        from pptx import Presentation
        from pptx.util import Inches, Pt
        from pptx.enum.text import PP_ALIGN
    except ImportError:
        raise ImportError("python-pptx is required. Install: pip install python-pptx")

    conn = _get_connection(db_path, schema)
    try:
        df = conn.execute(f"SELECT * FROM {schema}.{table}").df()
    finally:
        conn.close()

    prs = Presentation()
    display_title = title or f"Data Report: {table}"

    # Slide 1: Title
    slide1 = prs.slides.add_slide(prs.slide_layouts[0])
    title_shape = slide1.shapes.title
    title_shape.text = display_title
    subtitle = slide1.placeholders[1]
    subtitle.text = f"{len(df)} rows × {len(df.columns)} columns\nGenerated {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}"

    # Slide 2: Key metrics
    slide2 = prs.slides.add_slide(prs.slide_layouts[1])
    slide2.shapes.title.text = "Key Metrics"
    num_cols = df.select_dtypes(include=["number"]).columns
    body = slide2.placeholders[1]
    tf = body.text_frame
    tf.clear()
    for i, col in enumerate(num_cols[:8]):
        s = df[col].dropna()
        if len(s) > 0:
            p = tf.add_paragraph() if i > 0 else tf.paragraphs[0]
            p.text = f"{col}: mean={s.mean():.2f}, std={s.std():.2f}, min={s.min():.2f}, max={s.max():.2f}"
            p.font.size = Pt(11)

    # Slide 3: Data table
    slide3 = prs.slides.add_slide(prs.slide_layouts[1])
    slide3.shapes.title.text = "Data Preview"
    cols = list(df.columns[:8])  # Max 8 columns
    rows_data = df.head(15)[cols]

    tbl_rows = min(len(rows_data) + 1, 16)
    tbl_cols = len(cols) + 1
    table = slide3.shapes.add_table(tbl_rows, tbl_cols, Inches(0.5), Inches(1.5), Inches(9), Inches(5)).table

    # Headers
    table.cell(0, 0).text = "#"
    for j, c in enumerate(cols):
        table.cell(0, j + 1).text = str(c)

    # Data
    for i, (_, row) in enumerate(rows_data.iterrows()):
        table.cell(i + 1, 0).text = str(i + 1)
        for j, c in enumerate(cols):
            v = row[c]
            table.cell(i + 1, j + 1).text = "" if pd.isna(v) else str(v)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
