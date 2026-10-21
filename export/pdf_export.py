"""
PDF Export Module
Generate professional PDF reports from table data using Playwright or WeasyPrint.

Usage:
    from export.pdf_export import export_table_pdf
    pdf_bytes = export_table_pdf("sales", title="Sales Report")
"""

import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)


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
    # First generate HTML report
    from export.bi_export import export_report_html

    html_bytes = export_report_html(table, db_path, schema, title)
    html_content = html_bytes.decode("utf-8")

    # Try Playwright first (best quality), fallback to WeasyPrint
    try:
        return _html_to_pdf_playwright(html_content)
    except ImportError:
        logger.info("Playwright not available, trying WeasyPrint...")
    except Exception as e:
        logger.warning(f"Playwright PDF failed: {e}, trying WeasyPrint...")

    try:
        return _html_to_pdf_weasyprint(html_content)
    except ImportError:
        logger.warning("WeasyPrint not available either. Install: pip install weasyprint")
        # Last resort: return the HTML wrapped in a simple PDF-like message
        return _generate_text_pdf(title or f"Data Report: {table}", table)

    except Exception as e:
        logger.error(f"PDF generation failed: {e}")
        raise


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


def _generate_text_pdf(title: str, table: str) -> bytes:
    """
    Generate a minimal PDF using reportlab (always available via pip).
    Only used as last resort when Playwright and WeasyPrint are both unavailable.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib import colors

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4,
                                rightMargin=72, leftMargin=72,
                                topMargin=72, bottomMargin=72)
        styles = getSampleStyleSheet()
        story = []
        story.append(Paragraph(f"<b>{title}</b>", styles["Title"]))
        story.append(Spacer(1, 12))
        story.append(Paragraph(f"Table: {table}", styles["Normal"]))
        story.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", styles["Normal"]))
        story.append(Spacer(1, 24))
        story.append(Paragraph("<i>Install Playwright for full report formatting:</i>", styles["Normal"]))
        story.append(Paragraph("<i>pip install playwright && playwright install chromium</i>", styles["Normal"]))
        doc.build(story)
        return buf.getvalue()
    except ImportError:
        # No PDF backend available — fail loudly so the API returns an error
        # instead of a corrupt/fake PDF file.
        logger.error("No PDF library available. Install: pip install weasyprint or pip install playwright")
        raise ImportError(
            "No PDF library available. Install one of: "
            "pip install playwright && playwright install chromium, "
            "pip install weasyprint, or pip install reportlab"
        )
