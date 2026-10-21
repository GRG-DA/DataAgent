"""
PDF Export Module
Generate PDF reports from table data using Playwright or WeasyPrint.

Neither renderer is part of the shipped desktop build (they would add a
browser download and ~150 MB to the installer), so the export degrades to
a plain reportlab document.  The fallback says so in the document itself
and points at the exports that *are* available in the build.

Usage:
    from export.pdf_export import export_table_pdf
    pdf_bytes = export_table_pdf("sales", title="Sales Report")

    # when the caller wants to surface the degradation:
    result = export_table_pdf_detailed("sales", title="Sales Report")
    result["pdf"], result["backend"], result["warning"]
"""

import html
import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Union

logger = logging.getLogger(__name__)


#: First line of the fallback notice — kept short so it renders on one line.
PDF_FALLBACK_HEADLINE = "Full PDF formatting is not available in this build."

#: The full notice shown in the fallback document and returned as the
#: ``warning`` key of :func:`export_table_pdf_detailed`.
PDF_FALLBACK_NOTICE = (
    PDF_FALLBACK_HEADLINE
    + " This PDF contains a plain-text summary of the table instead of the "
    "formatted report. Use the HTML report, the PowerPoint (PPTX) deck or "
    "the CSV / Excel export from the same Export page for a fully "
    "formatted document."
)

#: The remainder of the notice (everything after the headline).
PDF_FALLBACK_DETAIL = PDF_FALLBACK_NOTICE[len(PDF_FALLBACK_HEADLINE):].strip()


def export_table_pdf(
    table: str,
    db_path: Union[str, Path] = "data/curated/warehouse.duckdb",
    schema: str = "analytics",
    title: str = "",
) -> bytes:
    """
    Export a warehouse table as a PDF report.

    Args:
        table: Table name
        db_path: Path to DuckDB database
        schema: Schema name
        title: Report title (defaults to "Data Report: {table}")

    Returns:
        PDF file as bytes
    """
    return export_table_pdf_detailed(table, db_path, schema, title)["pdf"]


def export_table_pdf_detailed(
    table: str,
    db_path: Union[str, Path] = "data/curated/warehouse.duckdb",
    schema: str = "analytics",
    title: str = "",
) -> Dict[str, Any]:
    """
    Export a warehouse table as a PDF and report how it was produced.

    Returns a dict with:
    - ``pdf``: the PDF bytes
    - ``backend``: ``'playwright'``, ``'weasyprint'`` or ``'reportlab'``
    - ``warning``: ``None`` for a fully formatted render, otherwise a
      human-readable note explaining that the output is degraded.  The
      caller keeps its own success flag; the note is additive.
    """
    from export.bi_export import export_report_html

    html_bytes = export_report_html(table, db_path, schema, title)
    html_content = html_bytes.decode("utf-8")

    backend, pdf_bytes = _render_with_backends(html_content)
    if backend is not None:
        return {"pdf": pdf_bytes, "backend": backend, "warning": None}

    logger.warning("PDF export degraded to the plain report: %s", PDF_FALLBACK_NOTICE)
    frame = _load_preview_frame(table, db_path, schema)
    pdf_bytes = _generate_text_pdf(
        title or f"Data Report: {table}", table, frame=frame
    )
    return {
        "pdf": pdf_bytes,
        "backend": "reportlab",
        "warning": PDF_FALLBACK_NOTICE,
    }


def _render_with_backends(html_content: str):
    """
    Render HTML to PDF with the best available renderer.

    Returns ``(backend_name, pdf_bytes)``, or ``(None, None)`` when no
    HTML renderer is installed (the shipped desktop build).
    """
    try:
        return "playwright", _html_to_pdf_playwright(html_content)
    except ImportError:
        logger.info("Playwright not available, trying WeasyPrint...")
    except Exception as e:
        logger.warning(f"Playwright PDF failed: {e}, trying WeasyPrint...")

    try:
        return "weasyprint", _html_to_pdf_weasyprint(html_content)
    except ImportError:
        logger.warning(
            "No HTML renderer available (Playwright/WeasyPrint); "
            "writing the plain report instead."
        )
    except Exception as e:
        logger.error(f"PDF generation failed: {e}")
        raise

    return None, None


def _load_preview_frame(table: str, db_path, schema: str):
    """Best-effort data preview for the fallback document."""
    try:
        from export.bi_export import load_table_frame

        return load_table_frame(table, db_path, schema)
    except Exception as e:
        logger.warning("Could not load the PDF data preview: %s", e)
        return None


def _html_to_pdf_playwright(html_content: str) -> bytes:
    """Convert HTML to PDF using Playwright (headless Chromium)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html_content, wait_until="networkidle")
        pdf_bytes = page.pdf(format="A4", margin={"top": "20mm", "bottom": "20mm", "left": "15mm", "right": "15mm"})
        browser.close()
        return pdf_bytes


def _html_to_pdf_weasyprint(html_content: str) -> bytes:
    """Convert HTML to PDF using WeasyPrint."""
    from weasyprint import HTML

    pdf_bytes = HTML(string=html_content).write_pdf()
    return pdf_bytes


def _generate_text_pdf(title: str, table: str, frame=None) -> bytes:
    """
    Generate a plain PDF using reportlab (always available via pip).

    Used as the last resort when neither Playwright nor WeasyPrint is
    installed — which is the case in the shipped desktop build.  The
    document states the limitation, names the exports that do work and
    carries a preview of the data so the file is still worth opening.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate
    except ImportError as e:
        # No PDF backend available — fail loudly so the API returns an error
        # instead of a corrupt/fake PDF file.
        logger.error("No PDF library available. Install: pip install reportlab")
        raise ImportError(
            "No PDF library available. Install one of: "
            "pip install playwright && playwright install chromium, "
            "pip install weasyprint, or pip install reportlab"
        ) from e

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            rightMargin=72, leftMargin=72,
                            topMargin=72, bottomMargin=72)
    doc.build(_text_pdf_story(title, table, frame))
    return buf.getvalue()


def _text_pdf_story(title: str, table: str, frame=None) -> list:
    """Build the reportlab story for the fallback PDF."""
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import Paragraph, Spacer

    styles = getSampleStyleSheet()
    small = ParagraphStyle("CellSmall", parent=styles["Normal"], fontSize=8, leading=10)

    story = [
        Paragraph(_rl_text(title), styles["Title"]),
        Spacer(1, 12),
        Paragraph(f"Table: {_rl_text(table)}", styles["Normal"]),
        Paragraph(
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            styles["Normal"],
        ),
        Spacer(1, 18),
        Paragraph(f"<b>{_rl_text(PDF_FALLBACK_HEADLINE)}</b>", styles["Normal"]),
        Paragraph(_rl_text(PDF_FALLBACK_DETAIL), styles["Normal"]),
        Spacer(1, 18),
    ]

    preview = _preview_flowables(frame, small)
    if preview:
        story.append(Paragraph("Data preview", styles["Heading3"]))
        story.extend(preview)

    return story


def _preview_flowables(frame, small) -> list:
    """Build a small table with the first rows of the data (if any)."""
    if frame is None or len(frame.columns) == 0:
        return []

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import Paragraph, Table, TableStyle

    cols = list(frame.columns)[:6]
    body = [[Paragraph(f"<b>{_rl_text(c)}</b>", small) for c in cols]]
    for _, row in frame.head(15)[cols].iterrows():
        body.append([Paragraph(_rl_text(_cell_text(row[c])), small) for c in cols])

    width = (A4[0] - 144) / len(cols)
    table = Table(body, colWidths=[width] * len(cols))
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF7")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return [table]


def _cell_text(value) -> str:
    """Render one cell value as a short single-line string."""
    if value is None:
        return ""
    text = str(value)
    if len(text) > 40:
        text = text[:39] + "..."
    return text


def _rl_text(value) -> str:
    """Escape a value for reportlab's mini markup, keeping it Latin-1 safe."""
    text = html.escape(str(value), quote=False)
    return text.encode("latin-1", "replace").decode("latin-1")
