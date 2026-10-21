"""
BI Export Module
Export warehouse data for Power BI, Tableau, and other BI tools

Features:
- Export tables as CSV, Parquet, or formatted Excel
- Generate BI connection config and schema documentation
- Package all tables as a ZIP for easy import
- Generate Power BI / Tableau connection instructions
"""

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


def _get_connection(
    db_path: Union[str, Path],
    schema: str = "analytics",
):
    """Open a DuckDB connection with schema set."""
    conn = duckdb.connect(str(db_path))
    conn.execute(f"SET search_path = '{schema}'")
    return conn


def export_table_csv(
    table: str,
    db_path: Union[str, Path] = "data/curated/warehouse.duckdb",
    schema: str = "analytics",
    encoding: str = "utf-8",
) -> str:
    """
    Export a warehouse table as CSV string.

    Args:
        table: Table name
        db_path: Path to DuckDB database
        schema: Schema name
        encoding: File encoding

    Returns:
        CSV content as string
    """
    conn = _get_connection(db_path, schema)
    try:
        df = conn.execute(f"SELECT * FROM {schema}.{table}").df()
        return df.to_csv(index=False, encoding=encoding)
    finally:
        conn.close()


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

    # --- Header style ---
    header_fill = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True, size=11)

    # Write header row
    for col_idx, col_name in enumerate(df.columns, start=1):
        cell = ws.cell(row=1, column=col_idx, value=str(col_name))
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    # Write data rows
    for row_idx, (_, row) in enumerate(df.iterrows(), start=2):
        for col_idx, col_name in enumerate(df.columns, start=1):
            val = row[col_name]
            cell = ws.cell(row=row_idx, column=col_idx)

            if val is None or (isinstance(val, float) and pd.isna(val)):
                cell.value = None
            else:
                cell.value = val

            # Number formatting for numeric types
            dtype = df[col_name].dtype
            if pd.api.types.is_integer_dtype(dtype):
                cell.number_format = '#,##0'
            elif pd.api.types.is_float_dtype(dtype):
                cell.number_format = '#,##0.00'

    # --- Auto-column width ---
    for col_idx, col_name in enumerate(df.columns, start=1):
        # Header width
        header_len = len(str(col_name))
        # Data content width (max 60)
        data_len = (
            df[col_name].astype(str).str.len().max()
            if len(df) > 0
            else 0
        )
        data_len = min(data_len, 60) if data_len > 0 else 0
        best_width = max(header_len + 2, data_len + 2, 10)
        ws.column_dimensions[get_column_letter(col_idx)].width = best_width

    # --- Freeze top row ---
    ws.freeze_panes = "A2"

    # --- Auto-filter ---
    if len(df.columns) > 0:
        last_col_letter = get_column_letter(len(df.columns))
        last_row = len(df) + 1  # +1 for header
        ws.auto_filter.ref = f"A1:{last_col_letter}{last_row}"

    # --- Truncation notice ---
    if truncated:
        info = wb.create_sheet("Export Info")
        info.column_dimensions["A"].width = 30
        notes = [
            ("Table", table),
            ("Rows exported", len(df)),
            ("Rows in warehouse", total_rows),
            ("Truncated", "yes — export capped at "
             f"{max_rows:,} rows (EXPORT_MAX_ROWS)"),
        ]
        for r, (k, v) in enumerate(notes, start=1):
            kc = info.cell(row=r, column=1, value=k)
            kc.font = Font(bold=True)
            info.cell(row=r, column=2, value=str(v))
        logger.warning(
            "Export of %s.%s truncated: %d of %d rows (cap=%d)",
            schema, table, len(df), total_rows, max_rows,
        )

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


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
                    df.to_csv(filepath, index=False)
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
                    csv_data = df.to_csv(index=False)
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

        lines = [f"## {table} ({total_rows} rows)\n"]
        if total_rows > max_rows:
            lines.append(f"*Showing {max_rows} of {total_rows} rows*\n")

        # Header
        cols = list(df.columns)
        lines.append("| " + " | ".join(str(c) for c in cols) + " |")
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
                    vals.append(str(v).replace("|", "\\|"))
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

    Returns HTML as bytes.
    """
    conn = _get_connection(db_path, schema)
    try:
        df = conn.execute(f"SELECT * FROM {schema}.{table}").df()
    finally:
        conn.close()

    display_title = title or f"Data Report: {table}"
    cols = list(df.columns)
    total = len(df)

    # Stats
    stats_html = ""
    num_cols = df.select_dtypes(include=["number"]).columns
    if len(num_cols) > 0:
        stats_html = "<div style='margin:20px 0;'><h3>Column Statistics</h3><table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse;font-size:13px;'>"
        stats_html += "<tr><th>Column</th><th>Count</th><th>Mean</th><th>Std</th><th>Min</th><th>Max</th></tr>"
        for col in num_cols:
            s = df[col].dropna()
            stats_html += f"<tr><td><strong>{col}</strong></td><td>{len(s)}</td><td>{s.mean():.2f}</td><td>{s.std():.2f}</td><td>{s.min():.2f}</td><td>{s.max():.2f}</td></tr>"
        stats_html += "</table></div>"

    # Data table (max 200 rows)
    display = df.head(200)
    table_html = "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse;font-size:12px;'>"
    table_html += "<tr style='background:#2F5496;color:white;'>" + "".join(f"<th>{c}</th>" for c in cols) + "</tr>"
    for _, row in display.iterrows():
        table_html += "<tr>" + "".join(f"<td>{'' if pd.isna(row[c]) else row[c]}</td>" for c in cols) + "</tr>"
    table_html += "</table>"
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
