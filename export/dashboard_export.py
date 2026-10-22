"""
Dashboard Export Module — Export Power BI dashboards as PDF, PPTX, and XLSX.

Follows the Power BI export architecture described in the Deep Research Report:
- PDF: Server-side render using reportlab (pixel-perfect, selectable text)
- PPTX: python-pptx with chart images placed on slides (like Power BI's export)
- XLSX: openpyxl with raw data re-queried server-side (like PBI's "export underlying data")

Uses the async 3-step export pattern from Power BI's REST API:
  1. POST  /export/job   → returns {jobId, status: "queued"}
  2. GET   /export/{id}  → poll until status="succeeded" + downloadUrl
  3. GET   /download/{id}→ download the file
"""

import io
import json
import logging
import math
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image,
    PageBreak, KeepTogether,
)
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics import renderPDF
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.enum.text import PP_ALIGN
from pptx.enum.shapes import MSO_SHAPE
from pptx.dml.color import RGBColor
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from openpyxl.utils.units import pixels_to_EMU
from openpyxl.drawing.image import Image as XLImage
from openpyxl.drawing.spreadsheet_drawing import AbsoluteAnchor
from openpyxl.drawing.xdr import XDRPoint2D, XDRPositiveSize2D
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.chart import BarChart, LineChart, AreaChart, PieChart, DoughnutChart, Reference

from export.chart_renderer import render_chart, render_table, NATIVE_TYPES
from export.bi_export import sanitize_spreadsheet_value

logger = logging.getLogger(__name__)


# ── In-memory job store (report: "Queue exports like Power BI's async export jobs") ──

_export_jobs: Dict[str, Dict[str, Any]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job(job_id: str) -> Optional[Dict[str, Any]]:
    return _export_jobs.get(job_id)


# ── Helpers ─────────────────────────────────────────────────────────────

def _safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", str(name or "export")).strip() or "export"


def _run_query_safe(run_query, sql: str) -> pd.DataFrame:
    try:
        return run_query(sql)
    except Exception as e:
        logger.warning("query failed: %s — %s", sql[:120], e)
        return pd.DataFrame()


def _widget_data(w: Dict[str, Any], run_query, load_table) -> Tuple[pd.DataFrame, str]:
    """Execute a widget's SQL and return (DataFrame, title)."""
    cfg = w.get("config") or {}
    sql = str(cfg.get("sql") or "").strip()
    table = cfg.get("table") or ""
    title = str(w.get("title") or w.get("type") or "Visual")

    if sql:
        df = _run_query_safe(run_query, sql)
    elif table:
        df = _run_query_safe(run_query, f'SELECT * FROM "{table}" LIMIT 200')
    else:
        df = pd.DataFrame()
    return df, title


def _format_number(val) -> str:
    if val is None:
        return ""
    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            return ""
        return f"{val:,.2f}"
    if isinstance(val, int):
        return f"{val:,}"
    return str(val)


# ── PDF Export (reportlab) ──────────────────────────────────────────────

def export_pdf(dashboard: Dict[str, Any], run_query, load_table,
               output_path: Optional[str] = None) -> bytes:
    """Export dashboard as a multi-page PDF report.

    Mirrors Power BI's PDF export: each visual becomes a section with
    title + data table (chart charts rendered as reportlab drawings).
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=30, rightMargin=30, topMargin=40, bottomMargin=30,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "DashTitle", parent=styles["Title"],
        fontSize=18, textColor=colors.HexColor("#2F5496"),
        spaceAfter=12,
    )
    subtitle_style = ParagraphStyle(
        "DashSubtitle", parent=styles["Normal"],
        fontSize=10, textColor=colors.grey, spaceAfter=6,
    )
    visual_style = ParagraphStyle(
        "VisualTitle", parent=styles["Heading2"],
        fontSize=13, textColor=colors.HexColor("#1F4E79"),
        spaceBefore=14, spaceAfter=6,
    )
    cell_style = ParagraphStyle(
        "Cell", parent=styles["Normal"],
        fontSize=7, leading=9, wordWrap="CJK",
    )
    header_style = ParagraphStyle(
        "CellBold", parent=cell_style,
        textColor=colors.white, fontName="Helvetica-Bold",
    )

    story: List[Any] = []

    # Title page
    name = str(dashboard.get("name") or "Dashboard Report")
    desc = str(dashboard.get("description") or "")
    insight = ""
    layout = dashboard.get("layout") or {}
    if isinstance(layout, dict):
        insight = str(layout.get("insight") or "")

    story.append(Paragraph(_esc(name), title_style))
    story.append(Paragraph(f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}", subtitle_style))
    if desc:
        story.append(Paragraph(_esc(desc), subtitle_style))
    if insight:
        story.append(Spacer(1, 10))
        story.append(Paragraph("<b>Insight:</b> " + _esc(insight), styles["Normal"]))

    story.append(Spacer(1, 20))

    # Executive summary table
    widgets = dashboard.get("widgets") or []
    kpis = [w for w in widgets if w.get("type") == "kpi"]
    if kpis:
        story.append(Paragraph("<b>Key Metrics</b>", visual_style))
        kpi_data = [["Metric", "Value"]]
        for w in kpis:
            df, title = _widget_data(w, run_query, load_table)
            val = ""
            if not df.empty:
                first_col = df.columns[0]
                raw = df.iloc[0][first_col] if len(df) > 0 else ""
                val = _format_number(raw)
            kpi_data.append([Paragraph(_esc(title), cell_style),
                           Paragraph(str(val), cell_style)])
        t = Table(kpi_data, colWidths=[200, 150])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F5496")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 9),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F2F2")]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t)

    # Per-visual pages
    for w in widgets:
        wtype = str(w.get("type") or "").lower()
        if wtype in ("text", "slicer"):
            continue

        df, title = _widget_data(w, run_query, load_table)
        story.append(PageBreak())
        story.append(Paragraph(_esc(title), visual_style))
        story.append(Paragraph(f"Type: {wtype}", subtitle_style))

        if df.empty:
            story.append(Paragraph("<i>No data returned</i>", styles["Normal"]))
            continue

        # Try to render a chart drawing for chart types
        if wtype in ("bar", "column", "line", "area", "pie", "donut") and len(df.columns) >= 2:
            drawing = _build_chart_drawing(wtype, df, w, width=680, height=280)
            if drawing:
                story.append(drawing)
                story.append(Spacer(1, 10))

        # Data table
        cols = list(df.columns[:12])
        header_row = [Paragraph(_esc(c), header_style) for c in cols]
        rows = []
        display = df.head(50)
        for _, row in display.iterrows():
            cells = []
            for c in cols:
                v = row[c]
                cells.append(Paragraph(_esc(_format_number(v) if isinstance(v, (int, float)) else v), cell_style))
            rows.append(cells)

        if rows:
            data = [header_row] + rows
            n_cols = len(cols)
            avail_width = 730
            col_w = avail_width / n_cols
            t = Table(data, colWidths=[col_w] * n_cols, repeatRows=1)
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F5496")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#D0D0D0")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8F9FA")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]))
            story.append(t)

        if len(df) > 50:
            story.append(Spacer(1, 4))
            story.append(Paragraph(f"<i>Showing 50 of {len(df)} rows</i>", subtitle_style))

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()


def _build_chart_drawing(chart_type: str, df: pd.DataFrame, w: Dict,
                         width: int = 680, height: int = 280) -> Optional[Drawing]:
    """Build a reportlab Drawing for a chart visual."""
    try:
        d = Drawing(width, height)
        x_col = df.columns[0]
        y_col = df.columns[1] if len(df.columns) > 1 else df.columns[0]
        labels = [str(v)[:20] for v in df[x_col].head(12).tolist()]
        values = []
        for v in df[y_col].head(12).tolist():
            try:
                values.append(float(v) if v is not None else 0)
            except (ValueError, TypeError):
                values.append(0)

        if not values:
            return None

        if chart_type in ("bar", "column"):
            chart = VerticalBarChart()
            chart.x = 50
            chart.y = 30
            chart.width = width - 80
            chart.height = height - 60
            chart.data = [values]
            chart.categoryAxis.categoryNames = labels
            chart.categoryAxis.labels.fontName = "Helvetica"
            chart.categoryAxis.labels.fontSize = 7
            chart.categoryAxis.labels.angle = 45
            chart.valueAxis.labels.fontName = "Helvetica"
            chart.valueAxis.labels.fontSize = 7
            chart.bars[0].fillColor = colors.HexColor("#118DFF")
            d.add(chart)

        elif chart_type in ("pie", "donut"):
            chart = Pie()
            chart.x = width // 4
            chart.y = 30
            chart.width = min(width, height) - 80
            chart.height = min(width, height) - 80
            chart.data = values
            chart.labels = labels
            chart.slices.strokeWidth = 0.5
            palette = ["#118DFF", "#12239E", "#E66C37", "#6B007B",
                       "#E044A7", "#744EC2", "#D9B300", "#D64550",
                       "#197278", "#7A7A7A"]
            for i in range(min(len(values), len(palette))):
                chart.slices[i].fillColor = colors.HexColor(palette[i])
            d.add(chart)

        elif chart_type in ("line", "area"):
            chart = LinePlot()
            chart.x = 50
            chart.y = 30
            chart.width = width - 80
            chart.height = height - 60
            chart.data = [list(enumerate(values))]
            chart.lines[0].strokeColor = colors.HexColor("#118DFF")
            chart.lines[0].strokeWidth = 2
            chart.xValueAxis.labels.fontName = "Helvetica"
            chart.xValueAxis.labels.fontSize = 7
            chart.yValueAxis.labels.fontName = "Helvetica"
            chart.yValueAxis.labels.fontSize = 7
            d.add(chart)

        return d
    except Exception as e:
        logger.debug("chart drawing failed: %s", e)
        return None


def _esc(text: str) -> str:
    """Escape text for reportlab XML paragraphs."""
    s = str(text or "")
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return s


# ── PPTX Export (python-pptx) ──────────────────────────────────────────

# Cohesive report palette — matches the in-app dashboard + Power BI theme.
_PRIMARY = 0x1F4E79          # deep navy (headers, titles)
_ACCENT = 0x118DFF           # bright blue (accents, chart bars)
_KPI_BG = ["#2F5496", "#118DFF", "#E66C37", "#6B007B", "#197278", "#D64550"]
_CHART_PALETTE = ["#118DFF", "#12239E", "#E66C37", "#6B007B", "#E044A7",
                  "#744EC2", "#D9B300", "#D64550", "#197278", "#7A7A7A"]

_XLSX_HEADER_FILL = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
_XLSX_HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)


def export_pptx(dashboard: Dict[str, Any], run_query, load_table) -> bytes:
    """Export dashboard as a designed PowerPoint presentation.

    Mirrors Power BI's PPTX export: each report page becomes one slide.
    Chart visuals are rendered as real chart images (matplotlib) so the deck
    looks like a designed report rather than a raw table dump.
    """
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    name = str(dashboard.get("name") or "Dashboard Report")
    desc = str(dashboard.get("description") or "")
    layout_obj = dashboard.get("layout") or {}
    insight = ""
    if isinstance(layout_obj, dict):
        insight = str(layout_obj.get("insight") or "")

    # Slide 1: Title with header band
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank layout
    _add_header_band(slide, name, desc, insight)

    # Slide 2: KPI Summary (colored cards)
    widgets = dashboard.get("widgets") or []
    kpis = [w for w in widgets if w.get("type") == "kpi"]
    if kpis:
        slide2 = prs.slides.add_slide(prs.slide_layouts[6])
        _add_section_title(slide2, "Key Metrics")
        left = 0.5
        top = 1.2
        for i, w in enumerate(kpis):
            df, title = _widget_data(w, run_query, load_table)
            val = ""
            if not df.empty:
                first_col = df.columns[0]
                raw = df.iloc[0][first_col] if len(df) > 0 else ""
                val = _format_number(raw)
            _add_kpi_card(slide2, Inches(left), Inches(top),
                          title, str(val), i)
            left += 3.0
            if left > 11:
                left = 0.5
                top += 2.2

    # Per-visual slides (2 charts per slide, like Power BI's 1280x720 layout)
    chart_widgets = [w for w in widgets if w.get("type") not in ("text", "slicer", "kpi", "table")]
    for idx in range(0, len(chart_widgets), 2):
        slide_v = prs.slides.add_slide(prs.slide_layouts[6])
        for col_offset in range(2):
            if idx + col_offset >= len(chart_widgets):
                break
            w = chart_widgets[idx + col_offset]
            df, title = _widget_data(w, run_query, load_table)
            x = Inches(0.5 + col_offset * 6.3)
            _add_text_box(slide_v, x, Inches(0.3), Inches(6), Inches(0.5),
                          title, font_size=16, bold=True, color=_PRIMARY)
            if df.empty:
                continue
            wtype = str(w.get("type") or "").lower()
            png = _render_chart_png(wtype, df, w)
            if png:
                _add_chart_image(slide_v, x, Inches(1.0), Inches(5.8), Inches(3.9), png)
                _add_data_table(slide_v, x, Inches(5.1), Inches(5.8), Inches(2.1), df.head(8))
            else:
                _add_data_table(slide_v, x, Inches(1), Inches(5.8), Inches(5.5), df.head(20))

    # Table slides
    table_widgets = [w for w in widgets if w.get("type") == "table"]
    for w in table_widgets:
        df, title = _widget_data(w, run_query, load_table)
        if df.empty:
            continue
        slide_t = prs.slides.add_slide(prs.slide_layouts[6])
        _add_section_title(slide_t, title)
        _add_data_table(slide_t, Inches(0.3), Inches(1), Inches(12.5), Inches(5.8),
                       df.head(30))

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf.getvalue()


def _add_header_band(slide, name: str, desc: str, insight: str) -> None:
    """Draw a navy header band + title block on the title slide."""
    band = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, 0, 0, prs_width(), Inches(1.35))
    band.fill.solid()
    band.fill.fore_color.rgb = RGBColor(0x1F, 0x4E, 0x79)
    band.line.fill.background()
    band.shadow.inherit = False

    _add_text_box(slide, Inches(0.6), Inches(0.22), Inches(12), Inches(0.9),
                  name, font_size=34, bold=True, color=0xFFFFFF)
    _add_text_box(slide, Inches(0.6), Inches(1.6), Inches(12), Inches(0.5),
                  f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                  font_size=13, color=0x808080)
    if desc:
        _add_text_box(slide, Inches(0.6), Inches(2.2), Inches(12), Inches(0.6),
                      desc, font_size=12, color=0x666666)
    if insight:
        _add_text_box(slide, Inches(0.6), Inches(2.9), Inches(12), Inches(1),
                      f"Insight: {insight}", font_size=11, color=_PRIMARY)


def prs_width():
    return Inches(13.333)


def _add_section_title(slide, title: str) -> None:
    """Section header with a small accent underline bar."""
    _add_text_box(slide, Inches(0.5), Inches(0.3), Inches(12), Inches(0.6),
                  title, font_size=24, bold=True, color=_PRIMARY)
    bar = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(0.55), Inches(0.95), Inches(1.6), Inches(0.06))
    bar.fill.solid()
    bar.fill.fore_color.rgb = RGBColor(0x11, 0x8D, 0xFF)
    bar.line.fill.background()
    bar.shadow.inherit = False


def _add_text_box(slide, left, top, width, height, text, font_size=12,
                  bold=False, color=0x000000):
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = str(text or "")
    p.font.size = Pt(font_size)
    p.font.bold = bold
    if isinstance(color, int):
        p.font.color.rgb = RGBColor((color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF)
    else:
        p.font.color.rgb = color
    return txBox


def _add_kpi_card(slide, left, top, label, value, idx=0):
    """KPI card with a colored rounded-rectangle background."""
    bg = _KPI_BG[idx % len(_KPI_BG)]
    card = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE, left, top, Inches(2.8), Inches(1.7))
    card.fill.solid()
    card.fill.fore_color.rgb = RGBColor.from_string(bg.lstrip("#"))
    card.line.fill.background()
    card.shadow.inherit = False
    _add_text_box(slide, left + Inches(0.2), top + Inches(0.15), Inches(2.4), Inches(0.5),
                  label, font_size=11, color=0xFFFFFF)
    _add_text_box(slide, left + Inches(0.2), top + Inches(0.6), Inches(2.4), Inches(0.9),
                  value, font_size=26, bold=True, color=0xFFFFFF)


def _render_chart_png(chart_type: str, df: pd.DataFrame, w: Dict,
                      width: int = 900, height: int = 420,
                      theme: str = "auto") -> Optional[bytes]:
    """Render a chart visual to PNG bytes via the universal renderer.

    Supports every visual type the dashboard builder can produce (bar,
    column, line, area, pie, donut, scatter, treemap, funnel, gauge,
    waterfall, radar, heatmap, histogram, boxplot, bullet, sankey,
    sparkline, ...).  Never returns None for chartable types — empty data
    produces a styled "No data" panel so callers always embed an image.
    """
    try:
        return render_chart(chart_type, df, w, width=width, height=height,
                            dpi=100, theme=theme)
    except Exception as e:
        logger.debug("chart png render failed: %s", e)
        return None


def _add_chart_image(slide, left, top, width, height, png_bytes) -> None:
    """Embed a PNG chart image on a slide."""
    slide.shapes.add_picture(io.BytesIO(png_bytes), left, top,
                             width=width, height=height)


def _add_data_table(slide, left, top, width, height, df: pd.DataFrame):
    cols = list(df.columns[:8])
    rows_count = min(len(df) + 1, 21)
    cols_count = len(cols) + 1

    shape = slide.shapes.add_table(rows_count, cols_count, left, top, width, height)
    tbl = shape.table

    # Header
    tbl.cell(0, 0).text = "#"
    for j, c in enumerate(cols):
        tbl.cell(0, j + 1).text = str(c)[:30]

    # Data
    for i, (_, row) in enumerate(df.head(20).iterrows()):
        tbl.cell(i + 1, 0).text = str(i + 1)
        for j, c in enumerate(cols):
            v = row[c]
            tbl.cell(i + 1, j + 1).text = "" if pd.isna(v) else str(v)[:40]


# ── XLSX Export (openpyxl) ──────────────────────────────────────────────

def export_xlsx(dashboard: Dict[str, Any], run_query, load_table) -> bytes:
    """Export dashboard as an Excel workbook.

    Mirrors Power BI's "export underlying data": one sheet per widget with
    the raw query result, plus a Summary sheet.
    """
    wb = Workbook()
    header_fill = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True, size=11)

    # Summary sheet
    ws = wb.active
    ws.title = "Summary"
    name = str(dashboard.get("name") or "Dashboard Report")
    ws.cell(row=1, column=1, value="Dashboard Report").font = Font(bold=True, size=14, color="2F5496")
    ws.cell(row=2, column=1, value=name)
    ws.cell(row=3, column=1, value=f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    ws.cell(row=4, column=1, value=f"Widgets: {len(dashboard.get('widgets') or [])}")

    desc = str(dashboard.get("description") or "")
    if desc:
        ws.cell(row=5, column=1, value=desc)

    layout_obj = dashboard.get("layout") or {}
    insight = ""
    if isinstance(layout_obj, dict):
        insight = str(layout_obj.get("insight") or "")
    if insight:
        ws.cell(row=6, column=1, value=f"Insight: {insight}")

    # Widget list
    widgets = dashboard.get("widgets") or []
    ws.cell(row=8, column=1, value="Widget").font = header_font
    ws.cell(row=8, column=2, value="Type").font = header_font
    ws.cell(row=8, column=3, value="Rows").font = header_font
    ws.cell(row=8, column=4, value="Columns").font = header_font
    for c in range(1, 5):
        ws.cell(row=8, column=c).fill = header_fill

    row_num = 9
    widget_sheets = []
    for w in widgets:
        wtype = str(w.get("type") or "").lower()
        if wtype in ("text", "slicer"):
            continue
        df, title = _widget_data(w, run_query, load_table)
        ws.cell(row=row_num, column=1, value=title)
        ws.cell(row=row_num, column=2, value=wtype)
        ws.cell(row=row_num, column=3, value=len(df))
        ws.cell(row=row_num, column=4, value=len(df.columns) if not df.empty else 0)
        row_num += 1
        if not df.empty:
            widget_sheets.append((title, df))

    # One sheet per widget
    for title, df in widget_sheets:
        sheet_name = re.sub(r'[\\/*?\[\]:]', '_', title)[:28]
        # Dedup sheet names
        existing = [ws.title for ws in wb.worksheets]
        if sheet_name in existing:
            sheet_name = sheet_name[:25] + str(len(existing))
        ws_v = wb.create_sheet(sheet_name)

        cols = list(df.columns)
        for j, c in enumerate(cols, 1):
            cell = ws_v.cell(row=1, column=j,
                            value=sanitize_spreadsheet_value(str(c)))
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        for i, (_, row) in enumerate(df.head(10000).iterrows(), 2):
            for j, c in enumerate(cols, 1):
                v = row[c]
                cell = ws_v.cell(row=i, column=j)
                if pd.isna(v) or v is None:
                    cell.value = None
                else:
                    cell.value = sanitize_spreadsheet_value(v)
                if isinstance(v, (int,)):
                    cell.number_format = "#,##0"
                elif isinstance(v, float):
                    cell.number_format = "#,##0.00"

        # Auto-width
        for j, c in enumerate(cols, 1):
            max_len = max(len(str(c)), 10)
            for i in range(2, min(len(df) + 2, 52)):
                val = str(df.iloc[i - 2][c]) if not pd.isna(df.iloc[i - 2][c]) else ""
                max_len = max(max_len, min(len(val), 40))
            ws_v.column_dimensions[ws_v.cell(row=1, column=j).column_letter].width = max_len + 2

        if len(df) > 10000:
            ws_v.cell(row=len(df) + 2, column=1,
                     value=f"Showing 10,000 of {len(df)} rows").font = Font(italic=True, color="808080")

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


# ── Async Job API (report: "POST→poll→GET pattern from PBI REST API") ───

def create_export_job(dash_id: str, fmt: str, dashboard: Dict[str, Any],
                      run_query, load_table) -> str:
    """Create an async export job. Returns job_id.

    This mirrors Power BI's ``POST /reports/{id}/ExportTo`` endpoint which
    returns a job ID for asynchronous export processing.
    """
    job_id = str(uuid.uuid4())
    _export_jobs[job_id] = {
        "id": job_id,
        "dashId": dash_id,
        "format": fmt,
        "status": "queued",
        "created": _now_iso(),
        "completed": None,
        "error": None,
        "downloadUrl": None,
    }

    # Run the export synchronously in this thread (the report recommends
    # using a task queue like BullMQ/Celery in production, but for a
    # single-user desktop app this is fine).
    try:
        _export_jobs[job_id]["status"] = "running"

        # Detect blankdash format: dashboard may carry the full config in
        # 'layout' (pages[].widgets[] with x/y/w/h) or as a top-level dict.
        # Saved blankdash dashboards nest the config under layout._blankdash.
        bd_config = None
        if isinstance(dashboard, dict):
            lay = dashboard.get("layout")
            if isinstance(lay, dict):
                nested = lay.get("_blankdash")
                if _is_blankdash_config(nested):
                    bd_config = nested
                elif _is_blankdash_config(lay):
                    bd_config = lay
            elif _is_blankdash_config(dashboard):
                bd_config = dashboard

        if fmt == "pdf":
            data = export_pdf(dashboard, run_query, load_table)
        elif fmt == "pptx":
            if bd_config:
                data = export_dash_pptx(bd_config, run_query, load_table)
            else:
                data = export_pptx(dashboard, run_query, load_table)
        elif fmt == "xlsx":
            if bd_config:
                data = export_dash_xlsx(bd_config, run_query, load_table)
            else:
                data = export_xlsx(dashboard, run_query, load_table)
        else:
            raise ValueError(f"Unsupported format: {fmt}")

        # Store the data temporarily (in production: write to disk/S3)
        _export_jobs[job_id]["_data"] = data
        _export_jobs[job_id]["status"] = "succeeded"
        _export_jobs[job_id]["completed"] = _now_iso()
        _export_jobs[job_id]["downloadUrl"] = f"/dashboards/export/download/{job_id}"
    except Exception as e:
        logger.exception("export job %s failed", job_id)
        _export_jobs[job_id]["status"] = "failed"
        _export_jobs[job_id]["error"] = str(e)
        _export_jobs[job_id]["completed"] = _now_iso()

    return job_id


def get_job_status(job_id: str) -> Optional[Dict[str, Any]]:
    """Get export job status. Mirrors ``GetExportToFileStatus``."""
    j = _job(job_id)
    if not j:
        return None
    return {
        "id": j["id"],
        "status": j["status"],
        "created": j["created"],
        "completed": j["completed"],
        "error": j.get("error"),
        "downloadUrl": j.get("downloadUrl"),
        "format": j.get("format"),
        "dashId": j.get("dashId"),
    }


def get_job_data(job_id: str) -> Optional[bytes]:
    """Retrieve the exported file data. Mirrors ``GET download URL``."""
    j = _job(job_id)
    if not j:
        return None
    return j.get("_data")


# ── BlankDash Layout-Preserving Export ──────────────────────────────────
# These functions export dashboards that use the blankdash (Dashboard Studio)
# format: pages[].widgets[] with x, y, width, height pixel coordinates on
# a canvas (default 1920×1080).  The exported files preserve the visual
# layout — each page becomes one PPTX slide or one XLSX sheet.

# Canvas defaults (must match blankdash.js _CANVAS_MIN_W / _CANVAS_MIN_H)
_DEFAULT_CANVAS_W = 1920
_DEFAULT_CANVAS_H = 1080

# Slide dimensions in inches (widescreen 16:9)
_SLIDE_W = 13.333
_SLIDE_H = 7.5


def _is_blankdash_config(config: Any) -> bool:
    """Return True if *config* is a blankdash dashboard (has pages[].widgets)."""
    if not isinstance(config, dict):
        return False
    pages = config.get("pages")
    return isinstance(pages, list) and len(pages) > 0 and isinstance(pages[0], dict) and "widgets" in pages[0]


def _canvas_dims(config: dict) -> Tuple[float, float]:
    """Return (canvas_w, canvas_h) from a blankdash config.

    The declared canvasMinW/H are only a *minimum*: the real canvas is the
    maximum extent of the placed widgets across all pages.  Trusting the
    declared height alone caused the widget-overflow bug where content taller
    than the declared canvas was scaled with too large a factor and rendered
    off-slide/off-sheet.
    """
    cw = float(config.get("canvasMinW") or config.get("canvasW") or _DEFAULT_CANVAS_W)
    ch = float(config.get("canvasMinH") or config.get("canvasH") or _DEFAULT_CANVAS_H)
    for page in config.get("pages") or []:
        pw, ph = _canvas_dims_for_page(config, page)
        cw = max(cw, pw)
        ch = max(ch, ph)
    return max(1, cw), max(1, ch)


def _canvas_dims_for_page(config: dict, page: dict) -> Tuple[float, float]:
    """Per-page canvas dims = declared minimum vs. actual widget extents.

    Widgets placed beyond the declared canvas (e.g. a tall stack of KPI
    cards + charts + a table) extend the page canvas so the scale factor
    never overflows the slide/sheet.
    """
    cw = float(config.get("canvasMinW") or config.get("canvasW") or _DEFAULT_CANVAS_W)
    ch = float(config.get("canvasMinH") or config.get("canvasH") or _DEFAULT_CANVAS_H)
    for w in page.get("widgets") or []:
        cw = max(cw, float(w.get("x") or 0) + float(w.get("width") or 0))
        ch = max(ch, float(w.get("y") or 0) + float(w.get("height") or 0))
    return max(1, cw), max(1, ch)


def _c2s(x: float, y: float, w: float, h: float,
         canvas_w: float, canvas_h: float, y_offset: float = 0.0) -> Tuple[float, float, float, float]:
    """Convert canvas pixel coordinates to slide inches.

    y_offset subtracts a paginated band's top so each band starts at y=0.
    """
    sx = _SLIDE_W / canvas_w
    sy = _SLIDE_H / canvas_h
    return (x * sx, (y - y_offset) * sy, w * sx, h * sy)


# A single slide/sheet can legibly show content whose height is at most
# _SLIDE_H/_SLIDE_W of its width (16:9).  Pages taller than ~1.3× that are
# split into vertical bands, one slide/sheet per band (widget-overflow bug).
_MAX_PAGE_ASPECT_TOLERANCE = 1.3


def _page_bands(page: dict, canvas_w: float, canvas_h: float) -> List[Tuple[List[dict], float, float]]:
    """Split a page's widgets into vertical bands that each fit one slide.

    Returns a list of (widgets, band_canvas_h, band_top) where band_canvas_h
    is the band's own content height and band_top is the canvas-y offset to
    subtract so the band starts at y=0 when scaled.  A single band is returned
    when the page fits within the aspect tolerance.
    """
    max_fit_h = canvas_w * (_SLIDE_H / _SLIDE_W)
    if canvas_h <= max_fit_h * _MAX_PAGE_ASPECT_TOLERANCE:
        return [(page.get("widgets") or [], canvas_h, 0.0)]
    n_bands = max(2, int(math.ceil(canvas_h / max_fit_h)))
    band_h = canvas_h / n_bands
    bands: List[List[dict]] = [[] for _ in range(n_bands)]
    for w in page.get("widgets") or []:
        wy = float(w.get("y") or 0)
        bi = min(n_bands - 1, int(wy // band_h))
        bands[bi].append(w)
    out: List[Tuple[List[dict], float, float]] = []
    for bi, ws in enumerate(bands):
        if not ws:
            continue
        top = bi * band_h
        max_bottom = max((float(w.get("y") or 0) + float(w.get("height") or 0)) for w in ws)
        out.append((ws, max(max_bottom - top, 1.0), top))
    return out


def _check_overflow(shapes_or_sheet, container_h, label: str, container_w: Optional[float] = None) -> None:
    """Fail-loud overflow guard: warn when content exceeds the container.

    PPTX: pass a slide; container_h is the slide height in inches.  Shape
    positions are EMU, converted to inches.
    XLSX: pass a worksheet; container_h/container_w are the max allowed
    used row/column indices.
    """
    try:
        if hasattr(shapes_or_sheet, "shapes"):
            max_bottom = 0.0
            for shape in shapes_or_sheet.shapes:
                try:
                    max_bottom = max(max_bottom, shape.top + shape.height)
                except Exception:
                    continue
            max_bottom = max_bottom / 914400.0  # EMU → inches
            if max_bottom > container_h * 1.02:
                logger.warning("%s overflow: content bottom=%.2f exceeds container=%.2f",
                               label, max_bottom, container_h)
        else:
            max_row = shapes_or_sheet.max_row or 0
            max_col = shapes_or_sheet.max_column or 0
            if max_row > container_h * 1.02:
                logger.warning("%s overflow: content rows=%d exceeds container=%.0f",
                               label, max_row, container_h)
            if container_w and max_col > container_w * 1.02:
                logger.warning("%s overflow: content cols=%d exceeds container=%.0f",
                               label, max_col, container_w)
    except Exception as e:
        logger.debug("overflow check failed for %s: %s", label, e)


# ── Widget type classification ────────────────────────────────────────

_CHART_TYPES = {
    "bar", "column", "line", "area", "pie", "donut", "scatter",
    "stackedbar", "stackedcolumn", "horizontalbar",
    "treemap", "funnel", "gauge", "waterfall", "radar",
    "heatmap", "histogram", "boxplot", "bullet", "sankey", "sparkline",
}
_KPI_TYPES = {"kpi", "metriccard", "radial", "watertank", "status",
              "comparison", "targetactual", "trendkpi", "percentagekpi",
              "prevkpi", "iconkpi", "progress"}
_FILTER_TYPES_SET = {"slicer", "dropdown", "date", "relativedate",
                     "daterange", "numrange", "globalsearch", "searchbox",
                     "filterpane", "hierarchy", "topn"}
_NON_RENDERABLE = {"text", "image", "custom", "page", "button",
                   "shape", "divider"}


def _widget_category(wtype: str, cfg: dict) -> str:
    """Classify a widget into 'chart', 'kpi', 'table', 'filter', 'visual', or 'skip'."""
    t = (wtype or "").lower()
    ct = (cfg.get("chartType") or "").lower() if isinstance(cfg, dict) else ""
    if t in _NON_RENDERABLE or t in _FILTER_TYPES_SET:
        return "skip"
    if t == "table":
        return "table"
    if t in _KPI_TYPES or ct in ("gauge",):
        return "kpi"
    if t in _CHART_TYPES or ct in _CHART_TYPES or t == "chart":
        return "chart"
    return "visual"


# ── BlankDash PPTX export ─────────────────────────────────────────────

def export_dash_pptx(config: dict, run_query, load_table) -> bytes:
    """Export a blankdash config as a layout-preserving PowerPoint deck.

    Each page becomes one slide.  Widgets are placed at their canvas
    positions (scaled to 13.333″ × 7.5″ widescreen).  Charts are
    rendered as matplotlib PNG images; KPI cards get coloured shapes;
    tables are native PPTX tables.
    """
    prs = Presentation()
    prs.slide_width = Inches(_SLIDE_W)
    prs.slide_height = Inches(_SLIDE_H)

    name = str(config.get("name") or "Dashboard")
    pages = config.get("pages") or []
    theme = config.get("theme") or "auto"

    # ── Title slide ──
    slide_title = prs.slides.add_slide(prs.slide_layouts[6])
    _add_header_band(slide_title, name, "", "")
    page_names = [str(p.get("name") or f"Page {i+1}") for i, p in enumerate(pages)]
    if page_names:
        _add_text_box(slide_title, Inches(0.6), Inches(2.0), Inches(12), Inches(0.5),
                      f"{len(pages)} page(s): {', '.join(page_names)}",
                      font_size=13, color=0x808080)

    # ── One slide per page (paginated into vertical bands when tall) ──
    for pidx, page in enumerate(pages):
        pname = str(page.get("name") or f"Page {pidx+1}")
        canvas_w, canvas_h = _canvas_dims_for_page(config, page)
        bands = _page_bands(page, canvas_w, canvas_h)

        for bi, (band_widgets, band_h, band_top) in enumerate(bands):
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            label = pname if len(bands) == 1 else f"{pname} ({bi+1} of {len(bands)})"

            # Page title footer
            _add_text_box(slide, Inches(0.3), Inches(7.05), Inches(6), Inches(0.4),
                          label, font_size=9, color=0x999999)
            _add_text_box(slide, Inches(9), Inches(7.05), Inches(4), Inches(0.4),
                          name, font_size=9, color=0x999999, bold=True)

            # Sort by zIndex for correct stacking
            sorted_w = sorted(band_widgets, key=lambda w: (w.get("zIndex") or 0))

            for w in sorted_w:
                wtype = str(w.get("type") or "").lower()
                cfg = w.get("config") or {}
                cat = _widget_category(wtype, cfg)
                if cat == "skip":
                    continue

                wx = float(w.get("x") or 0)
                wy = float(w.get("y") or 0)
                ww = float(w.get("width") or 300)
                wh = float(w.get("height") or 200)
                left, top, width, height = _c2s(wx, wy, ww, wh, canvas_w, band_h, y_offset=band_top)

                # Minimum size guard — skip micro-widgets
                if width < 0.15 or height < 0.15:
                    continue

                try:
                    if cat == "chart":
                        _place_chart_widget(slide, w, cfg, left, top, width, height, run_query, load_table)
                    elif cat == "kpi":
                        _place_kpi_widget(slide, w, cfg, left, top, width, height, run_query, load_table)
                    elif cat == "table":
                        _place_table_widget(slide, w, cfg, left, top, width, height, run_query, load_table)
                    else:
                        _place_visual_widget(slide, w, cfg, left, top, width, height, run_query, load_table)
                except Exception as e:
                    logger.debug("widget %s render failed: %s", w.get("id", "?"), e)
                    _add_text_box(slide, Inches(left), Inches(top), Inches(width), Inches(height),
                                  f"[{wtype}]", font_size=9, color=0x999999)

            _check_overflow(slide, _SLIDE_H, f"PPTX slide '{label}'")

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf.getvalue()


def _add_native_chart(slide, left, top, width, height, chart_type: str, df: pd.DataFrame) -> bool:
    """Embed a native (editable) PowerPoint chart for the clean chart types.

    Returns True when a native chart was added; False when the type isn't
    supported or the data isn't suitable (caller falls back to a PNG image).
    """
    ct = str(chart_type).lower()
    if ct not in NATIVE_TYPES or df.empty or len(df.columns) < 2:
        return False
    cats = [str(v) for v in df.iloc[:, 0].head(12).tolist()]
    if len(cats) < 2:
        return False
    data_cols = [c for c in df.columns[1:] if _is_numeric(df[c])][:6]
    if not data_cols:
        return False
    if ct in ("pie", "donut"):
        data_cols = data_cols[:1]

    chart_data = CategoryChartData()
    chart_data.categories = cats
    for c in data_cols:
        vals = []
        for v in df[c].head(12).tolist():
            try:
                vals.append(float(v) if v is not None else 0)
            except (ValueError, TypeError):
                vals.append(0)
        chart_data.add_series(str(c), vals)

    xl_type = {
        "bar": XL_CHART_TYPE.BAR_CLUSTERED,
        "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
        "line": XL_CHART_TYPE.LINE,
        "area": XL_CHART_TYPE.AREA,
        "pie": XL_CHART_TYPE.PIE,
        "donut": XL_CHART_TYPE.DOUGHNUT,
    }[ct]
    try:
        slide.shapes.add_chart(xl_type, left, top, width, height, chart_data)
        return True
    except Exception as e:
        logger.debug("native chart failed: %s", e)
        return False


def _is_numeric(series) -> bool:
    """True when a pandas Series is numeric (or coercible to numeric)."""
    try:
        return bool(pd.api.types.is_numeric_dtype(series)
                    or pd.to_numeric(series, errors="coerce").notna().any())
    except Exception:
        return False


def _place_chart_widget(slide, w, cfg, left, top, width, height, run_query, load_table):
    """Render a chart widget as a matplotlib PNG image on the slide."""
    df, title = _widget_data(w, run_query, load_table)
    wtype = str(w.get("type") or "").lower()
    chart_type = str(cfg.get("chartType") or wtype).lower()

    # Title label above the chart
    if title and title != wtype:
        _add_text_box(slide, Inches(left), Inches(top), Inches(width), Inches(min(0.35, height * 0.15)),
                      title, font_size=max(8, min(12, int(height * 5))), bold=True, color=_PRIMARY)

    if df.empty:
        _add_text_box(slide, Inches(left), Inches(top + 0.3), Inches(width), Inches(height - 0.3),
                      "No data", font_size=9, color=0x999999)
        return

    img_top = top + min(0.35, height * 0.15)
    img_height = height - min(0.35, height * 0.15)

    if _add_native_chart(slide, Inches(left), Inches(img_top),
                         Inches(width), Inches(img_height), chart_type, df):
        return

    png = _render_chart_png(chart_type, df, w,
                            width=int(width * 100), height=int(img_height * 100),
                            theme=str(cfg.get("theme") or "auto"))
    if png:
        _add_chart_image(slide, Inches(left), Inches(img_top),
                         Inches(width), Inches(img_height), png)
    else:
        # Safety net — renderer should never return None for chartable types.
        _add_data_table(slide, Inches(left), Inches(img_top),
                        Inches(width), Inches(img_height), df.head(10))


def _place_kpi_widget(slide, w, cfg, left, top, width, height, run_query, load_table):
    """Render a KPI card as a coloured rectangle with value."""
    df, title = _widget_data(w, run_query, load_table)
    val = ""
    if not df.empty:
        first_col = df.columns[0]
        raw = df.iloc[0][first_col] if len(df) > 0 else ""
        val = _format_number(raw)

    # Background colour from widget or default palette
    bg_hex = str(w.get("bgColor") or cfg.get("bgColor") or "#2F5496").lstrip("#")
    try:
        bg_rgb = RGBColor.from_string(bg_hex)
    except Exception:
        bg_rgb = RGBColor(0x2F, 0x54, 0x96)

    card = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE,
        Inches(left), Inches(top), Inches(width), Inches(height))
    card.fill.solid()
    card.fill.fore_color.rgb = bg_rgb
    card.line.fill.background()
    card.shadow.inherit = False

    # Title (small, top)
    title_fs = max(7, min(11, int(height * 4)))
    _add_text_box(slide, Inches(left + 0.08), Inches(top + height * 0.08),
                  Inches(width - 0.16), Inches(height * 0.3),
                  title, font_size=title_fs, color=0xFFFFFF)

    # Value (large, center-bottom)
    val_fs = max(10, min(36, int(height * 30)))
    _add_text_box(slide, Inches(left + 0.08), Inches(top + height * 0.35),
                  Inches(width - 0.16), Inches(height * 0.55),
                  val, font_size=val_fs, bold=True, color=0xFFFFFF)


def _place_table_widget(slide, w, cfg, left, top, width, height, run_query, load_table):
    """Render a table widget as a native PPTX table."""
    df, title = _widget_data(w, run_query, load_table)

    if title and title != "table":
        _add_text_box(slide, Inches(left), Inches(top), Inches(width), Inches(min(0.3, height * 0.12)),
                      title, font_size=max(8, min(11, int(height * 4))), bold=True, color=_PRIMARY)

    tbl_top = top + min(0.3, height * 0.12)
    tbl_height = height - min(0.3, height * 0.12)

    if df.empty:
        _add_text_box(slide, Inches(left), Inches(tbl_top), Inches(width), Inches(tbl_height),
                      "No data", font_size=9, color=0x999999)
        return

    _add_data_table(slide, Inches(left), Inches(tbl_top),
                    Inches(width), Inches(tbl_height), df.head(15))


def _place_visual_widget(slide, w, cfg, left, top, width, height, run_query, load_table):
    """Fallback: try chart render, then data table."""
    df, title = _widget_data(w, run_query, load_table)
    if title:
        _add_text_box(slide, Inches(left), Inches(top), Inches(width), Inches(min(0.3, height * 0.12)),
                      title, font_size=max(8, min(11, int(height * 4))), bold=True, color=_PRIMARY)
    body_top = top + min(0.3, height * 0.12)
    body_h = height - min(0.3, height * 0.12)
    if df.empty:
        _add_text_box(slide, Inches(left), Inches(body_top), Inches(width), Inches(body_h),
                      "No data", font_size=9, color=0x999999)
        return
    _add_data_table(slide, Inches(left), Inches(body_top),
                    Inches(width), Inches(body_h), df.head(15))


# ── BlankDash XLSX export ─────────────────────────────────────────────

def export_dash_xlsx(config: dict, run_query, load_table) -> bytes:
    """Export a blankdash config as a designed Excel workbook.

    Each page becomes a canvas-scaled Dashboard sheet: KPI cards are styled
    colour blocks and chart/table visuals are embedded as images at their
    canvas positions, so the workbook mirrors the on-screen dashboard.
    Every widget also gets a raw data sheet (AutoFilter + Excel Table),
    plus a Summary sheet.
    """
    wb = Workbook()

    name = str(config.get("name") or "Dashboard")
    canvas_w, canvas_h = _canvas_dims(config)
    pages = config.get("pages") or []
    theme = str(config.get("theme") or "auto")

    ws_data = wb.create_sheet("ChartData")
    ws_data.sheet_state = "hidden"

    # ── Summary sheet ──
    ws = wb.active
    ws.title = "Summary"
    ws.cell(row=1, column=1, value="Dashboard Report").font = Font(bold=True, size=14, color="2F5496")
    ws.cell(row=2, column=1, value=name)
    ws.cell(row=3, column=1, value=f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    ws.cell(row=4, column=1, value=f"Pages: {len(pages)}")
    total_widgets = sum(len(p.get("widgets") or []) for p in pages)
    ws.cell(row=5, column=1, value=f"Total widgets: {total_widgets}")
    ws.cell(row=6, column=1, value=f"Canvas: {int(canvas_w)} x {int(canvas_h)} px")

    r = 8
    for hdr, ci in [("Page", 1), ("Widgets", 2), ("Page Name", 3)]:
        c = ws.cell(row=r, column=ci, value=hdr)
        c.font = _XLSX_HEADER_FONT; c.fill = _XLSX_HEADER_FILL
    r += 1
    for i, page in enumerate(pages):
        ws.cell(row=r, column=1, value=f"Page {i+1}")
        ws.cell(row=r, column=2, value=len(page.get("widgets") or []))
        ws.cell(row=r, column=3, value=page.get("name") or f"Page {i+1}")
        r += 1

    # ── One canvas-scaled Dashboard sheet per page (paginated when tall) ──
    # Grid granularity: 8px internally for positioning precision, but the
    # exposed grid is capped (~60 cols / ~120 rows) so a 1920×1080+ canvas
    # doesn't become a sea of hairline-thin slivers (widget-overflow bug).
    cell_px = 8
    max_cols, max_rows = 60, 120
    title_h_pt = 26
    title_h_px = title_h_pt * 4 / 3  # 1pt ≈ 1.333px

    for pidx, page in enumerate(pages):
        pname = str(page.get("name") or f"Page {pidx+1}")
        canvas_w, canvas_h = _canvas_dims_for_page(config, page)
        bands = _page_bands(page, canvas_w, canvas_h)

        for bi, (band_widgets, band_h, band_top) in enumerate(bands):
            sheet_label = pname if len(bands) == 1 else f"{pname} ({bi+1} of {len(bands)})"
            ws_p = wb.create_sheet(_unique_sheet_name(wb, sheet_label))

            # Effective cell size keeps the grid within sane column/row caps.
            eff_px = max(cell_px, canvas_w / max_cols, band_h / max_rows)
            n_cols = max(1, int(math.ceil(canvas_w / eff_px)))
            n_rows = max(1, int(math.ceil(band_h / eff_px)))

            # Page title band
            tc = ws_p.cell(row=1, column=1, value=sheet_label)
            tc.font = Font(bold=True, size=13, color="2F5496")
            ws_p.merge_cells(start_row=1, start_column=1, end_row=1, end_column=n_cols)
            ws_p.row_dimensions[1].height = title_h_pt

            # Clean dashboard look: no gridlines, sensible zoom.
            ws_p.sheet_view.showGridLines = False
            ws_p.sheet_view.zoomScale = max(40, min(100, int(100 * 60 / n_cols)))

            # Canvas grid sizing (eff_px cells)
            for ci in range(1, n_cols + 1):
                ws_p.column_dimensions[get_column_letter(ci)].width = eff_px / 7.0
            for ri in range(2, n_rows + 2):
                ws_p.row_dimensions[ri].height = eff_px * 0.75

            widgets = sorted(band_widgets, key=lambda w: (w.get("zIndex") or 0))
            for w in widgets:
                wtype = str(w.get("type") or "").lower()
                cfg = w.get("config") or {}
                cat = _widget_category(wtype, cfg)
                if cat == "skip":
                    continue
                wx = float(w.get("x") or 0)
                wy = float(w.get("y") or 0) - band_top
                ww = float(w.get("width") or 300)
                wh = float(w.get("height") or 200)
                col = int(math.ceil(wx / eff_px)) + 1
                row = int(math.ceil(wy / eff_px)) + 2
                cw = max(1, int(math.floor(ww / eff_px)))
                rh = max(1, int(math.floor(wh / eff_px)))

                df, title = _widget_data(w, run_query, load_table)

                if cat == "kpi":
                    _xlsx_kpi_card(ws_p, col, row, cw, rh, w, cfg, df)
                elif cat == "table":
                    _xlsx_chart_image(ws_p, ws_data, wx, title_h_px + wy, ww, wh, w, cfg, df, theme,
                                      table=True, cell_px=eff_px)
                else:
                    _xlsx_chart_image(ws_p, ws_data, wx, title_h_px + wy, ww, wh, w, cfg, df, theme,
                                      cell_px=eff_px)

            _check_overflow(ws_p, n_rows + 2, f"XLSX sheet '{sheet_label}'", container_w=n_cols + 1)

    # ── One data sheet per widget (AutoFilter + Excel Table) ──
    for pidx, page in enumerate(pages):
        pname = str(page.get("name") or f"Page {pidx+1}")
        for w in page.get("widgets") or []:
            wtype = str(w.get("type") or "").lower()
            cat = _widget_category(wtype, w.get("config") or {})
            if cat == "skip":
                continue
            df, title = _widget_data(w, run_query, load_table)
            if df.empty:
                continue
            ws_d = wb.create_sheet(_unique_sheet_name(wb, f"{pname}_{title}"))
            _write_data_sheet(ws_d, df)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


def _unique_sheet_name(wb, base: str) -> str:
    """Return a valid, collision-free Excel sheet name for `base`."""
    base = re.sub(r'[\\/*?\[\]:]', '_', str(base))[:28] or "Sheet"
    existing = {s.title for s in wb.worksheets}
    if base not in existing:
        return base
    i = 1
    while f"{base[:25]}{i}" in existing:
        i += 1
    return f"{base[:25]}{i}"


def _write_data_sheet(ws_d, df: pd.DataFrame) -> None:
    """Write a widget's raw data with styled header, AutoFilter and Excel Table."""
    cols = list(df.columns)
    for ci, c in enumerate(cols, 1):
        cell = ws_d.cell(row=1, column=ci,
                          value=sanitize_spreadsheet_value(str(c)))
        cell.font = _XLSX_HEADER_FONT; cell.fill = _XLSX_HEADER_FILL
        cell.alignment = Alignment(horizontal="center")

    for ri, (_, row) in enumerate(df.head(5000).iterrows(), 2):
        for ci, c in enumerate(cols, 1):
            v = row[c]
            cell = ws_d.cell(row=ri, column=ci)
            if pd.isna(v) or v is None:
                cell.value = None
            else:
                cell.value = sanitize_spreadsheet_value(v)
            if isinstance(v, int):
                cell.number_format = "#,##0"
            elif isinstance(v, float):
                cell.number_format = "#,##0.00"

    for ci, c in enumerate(cols, 1):
        max_len = max(len(str(c)), 10)
        for ri in range(2, min(len(df) + 2, 52)):
            val = str(df.iloc[ri - 2][c]) if not pd.isna(df.iloc[ri - 2][c]) else ""
            max_len = max(max_len, min(len(val), 40))
        ws_d.column_dimensions[ws_d.cell(row=1, column=ci).column_letter].width = max_len + 2

    if len(df) > 0:
        last_col = get_column_letter(len(cols))
        ref = f"A1:{last_col}{min(len(df) + 1, 5001)}"
        ws_d.auto_filter.ref = ref
        try:
            tab = Table(displayName=f"Tbl{abs(hash(f'{ws_d.title}|{ref}')) % 10**9}", ref=ref)
            tab.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
            ws_d.add_table(tab)
        except Exception:
            pass


def _xlsx_kpi_card(ws_p, col, row, cw, rh, w, cfg, df) -> None:
    """Draw a KPI card as a styled colour block on the dashboard sheet."""
    val = ""
    if not df.empty:
        first_col = df.columns[0]
        raw = df.iloc[0][first_col] if len(df) > 0 else ""
        val = _format_number(raw)
    bg = str(w.get("bgColor") or cfg.get("bgColor") or _KPI_BG[0]).lstrip("#")
    fill = PatternFill(start_color=bg, end_color=bg, fill_type="solid")

    ws_p.merge_cells(start_row=row, start_column=col, end_row=row, end_column=col + cw - 1)
    tc = ws_p.cell(row=row, column=col, value=w.get("title") or "KPI")
    tc.font = Font(color="FFFFFF", size=10)
    tc.fill = fill
    tc.alignment = Alignment(horizontal="center", vertical="center")

    if rh > 1:
        ws_p.merge_cells(start_row=row + 1, start_column=col,
                         end_row=row + rh - 1, end_column=col + cw - 1)
        vc = ws_p.cell(row=row + 1, column=col, value=val)
        vc.font = Font(color="FFFFFF", bold=True, size=16)
        vc.fill = fill
        vc.alignment = Alignment(horizontal="center", vertical="center")
    else:
        tc.value = f"{tc.value}: {val}"


def _xlsx_native_chart(ws_p, ws_data, x_px, y_px, w_px, h_px, chart_type, df, theme, cell_px=8) -> bool:
    """Embed a native (editable) Excel chart for the clean chart types.

    Data is written to a hidden ChartData sheet and referenced by the chart.
    Returns True when a native chart was added; False when the type isn't
    supported or the data isn't suitable (caller falls back to a PNG image).
    """
    ct = str(chart_type).lower()
    if ct not in NATIVE_TYPES or df.empty or len(df.columns) < 2:
        return False
    cats = df.iloc[:, 0].head(12).tolist()
    if len(cats) < 2:
        return False
    data_cols = [c for c in df.columns[1:] if _is_numeric(df[c])][:6]
    if not data_cols:
        return False
    if ct in ("pie", "donut"):
        data_cols = data_cols[:1]

    start_row = (ws_data.max_row or 0) + 2
    ws_data.cell(row=start_row, column=1, value="Category")
    for j, c in enumerate(data_cols, 2):
        ws_data.cell(row=start_row, column=j,
                     value=sanitize_spreadsheet_value(str(c)))
    for i, cat in enumerate(cats, 1):
        ws_data.cell(row=start_row + i, column=1,
                     value=sanitize_spreadsheet_value(cat))
        for j, c in enumerate(data_cols, 2):
            v = df.iloc[i - 1][c]
            ws_data.cell(row=start_row + i, column=j,
                         value=float(v) if not pd.isna(v) else 0)

    data_ref = Reference(ws_data, min_col=2, min_row=start_row,
                         max_col=1 + len(data_cols), max_row=start_row + len(cats))
    cat_ref = Reference(ws_data, min_col=1, min_row=start_row + 1,
                        max_row=start_row + len(cats))

    if ct in ("bar", "column"):
        chart = BarChart()
        chart.type = "col" if ct == "column" else "bar"
    elif ct == "line":
        chart = LineChart()
    elif ct == "area":
        chart = AreaChart()
    elif ct == "pie":
        chart = PieChart()
    else:
        chart = DoughnutChart()

    chart.add_data(data_ref, titles_from_data=True)
    chart.set_categories(cat_ref)
    chart.title = ""
    chart.width = w_px * 0.026458
    chart.height = h_px * 0.026458
    col = int(x_px // cell_px) + 1
    row = int(y_px // cell_px) + 2
    chart.anchor = f"{get_column_letter(col)}{row}"
    try:
        ws_p.add_chart(chart)
        return True
    except Exception as e:
        logger.debug("xlsx native chart failed: %s", e)
        return False


def _xlsx_chart_image(ws_p, ws_data, x_px, y_px, w_px, h_px, widget, cfg, df, theme, table=False, cell_px=8) -> None:
    """Embed a chart (or table) PNG as a floating image on the dashboard sheet."""
    if df.empty:
        df = pd.DataFrame()
    w_px = max(200, int(w_px))
    h_px = max(120, int(h_px))
    if not table:
        chart_type = str(cfg.get("chartType") or widget.get("type") or "column").lower()
        if _xlsx_native_chart(ws_p, ws_data, x_px, y_px, w_px, h_px, chart_type, df, theme, cell_px=cell_px):
            return
    if table:
        png = render_table(df, width=w_px, height=h_px, dpi=100, theme=theme)
    else:
        chart_type = str(cfg.get("chartType") or widget.get("type") or "column").lower()
        png = render_chart(chart_type, df.head(200), widget,
                           width=w_px, height=h_px, dpi=100, theme=theme)
    img = XLImage(io.BytesIO(png))
    img.anchor = AbsoluteAnchor(
        pos=XDRPoint2D(pixels_to_EMU(int(x_px)), pixels_to_EMU(int(y_px))),
        ext=XDRPositiveSize2D(pixels_to_EMU(w_px), pixels_to_EMU(h_px)),
    )
    ws_p.add_image(img)


# ── Legacy helpers (kept for backward compat) ────────────────────────

def get_job_filename(job_id: str) -> str:
    """Get the suggested filename for a completed job."""
    j = _job(job_id)
    if not j:
        return "export"
    name = "dashboard"
    dash_id = j.get("dashId", "")
    fmt = j.get("format", "pdf")
    return f"{name}.{fmt}"
