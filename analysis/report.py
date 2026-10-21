"""
Report Generation Module
Generate structured HTML and PDF reports from analysis results
"""

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
import pandas as pd
from jinja2 import Environment, FileSystemLoader
import html

logger = logging.getLogger(__name__)


def _esc(value: Any) -> str:
    """HTML-escape a value that reaches the report.

    Column names come straight from an uploaded CSV header and the
    recommendation / finding texts are LLM output, so both are untrusted
    input into an HTML document.
    """
    return html.escape(str(value), quote=True)



# Characters that are illegal in Windows filenames. A ":" would otherwise be
# interpreted as an NTFS Alternate Data Stream and the report would silently be
# written into a hidden stream instead of a real .html file.
_ILLEGAL_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _sanitize_filename(name: str) -> str:
    """Replace Windows-illegal filename characters with underscores."""
    safe = _ILLEGAL_FILENAME_CHARS.sub("_", name).strip()
    return safe or "report"

REPORT_TEMPLATE_DIR = Path(__file__).parent / "templates"


@dataclass
class ReportSection:
    """A section of a report."""
    title: str
    content: str
    charts: List[str] = field(default_factory=list)
    tables: List[pd.DataFrame] = field(default_factory=list)


@dataclass
class GeneratedReport:
    """Complete generated report."""
    title: str
    sections: List[ReportSection]
    html: str
    generated_at: str
    table_name: str = ""
    row_count: int = 0
    column_count: int = 0


class ReportGenerator:
    """Generate structured reports from analysis results."""

    def __init__(self, output_dir: str = "outputs/reports"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Set up Jinja2 environment
        if REPORT_TEMPLATE_DIR.exists():
            self.env = Environment(loader=FileSystemLoader(str(REPORT_TEMPLATE_DIR)),
                                autoescape=True)
        else:
            # Fallback: inline template
            self.env = Environment()
            self.env = None  # Will use fallback

    def generate_html_report(
        self,
        title: str,
        sections: List[ReportSection],
        table_name: str = "",
        row_count: int = 0,
        column_count: int = 0,
    ) -> GeneratedReport:
        """Generate a complete HTML report."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Render with Jinja2 template
        try:
            if self.env:
                template = self.env.get_template("report_template.html")
            else:
                template = None

            if template:
                html = template.render(
                    title=title,
                    sections=_sections_to_dicts(sections),
                    generated_at=now,
                    table_name=table_name,
                    row_count=row_count,
                    column_count=column_count,
                )
            else:
                html = self._fallback_html(title, sections, now, table_name, row_count, column_count)

        except Exception as e:
            logger.warning(f"Template rendering failed, using fallback: {e}")
            html = self._fallback_html(title, sections, now, table_name, row_count, column_count)

        return GeneratedReport(
            title=title,
            sections=sections,
            html=html,
            generated_at=now,
            table_name=table_name,
            row_count=row_count,
            column_count=column_count,
        )

    def save_report(self, report: GeneratedReport, filename: str = "") -> str:
        """Save report HTML to file. Returns the file path."""
        if not filename:
            safe_name = _sanitize_filename(report.title.replace(" ", "_").lower())[:30]
            filename = f"report_{safe_name}_{datetime.now():%Y%m%d_%H%M%S}.html"
        else:
            # Sanitize explicitly supplied filenames too (same ADS risk on Windows).
            filename = _sanitize_filename(filename)

        filepath = self.output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(report.html)

        logger.info(f"Report saved to {filepath}")
        return str(filepath)

    def generate_executive_report(
        self,
        table_name: str,
        profile: Dict[str, Any],
        quality: Dict[str, Any],
        insights: Dict[str, Any],
        correlations: Optional[Dict[str, Any]] = None,
        outliers: Optional[Dict[str, Any]] = None,
        include_charts: bool = True,
    ) -> GeneratedReport:
        """Generate an executive report from analysis results."""
        sections = []

        # 1. Executive Summary
        summary_text = f"""
        <p>Analysis of <strong>{_esc(table_name)}</strong> revealed key insights about the data.</p>
        <ul>
            <li><strong>{profile.get('row_count', 0):,}</strong> rows across <strong>{profile.get('column_count', 0)}</strong> columns</li>
            <li>Data quality score: <strong>{quality.get('overall_score', 0):.1f}/100</strong></li>
        </ul>
        """
        sections.append(ReportSection(
            title="Executive Summary",
            content=summary_text,
        ))

        # 2. Data Quality
        quality_text = ""
        if quality:
            dims = quality.get("dimension_scores", {})
            quality_text = f"""
            <p>Overall quality score: <span class="score-gauge {'score-high' if quality.get('overall_score', 0) >= 80 else 'score-medium' if quality.get('overall_score', 0) >= 50 else 'score-low'}">{quality.get('overall_score', 0):.1f}</span></p>
            <table>
                <tr><th>Dimension</th><th>Score</th><th>Status</th></tr>
                {
                ''.join(
                    f'<tr><td>{_esc(k.title())}</td><td>{v:.1f}</td><td>{"&#10004;" if v >= 80 else "&#9888;" if v >= 50 else "&#10008;"}</td></tr>'
                    for k, v in dims.items()
                )
                }
            </table>
            """
            if quality.get("recommendations"):
                quality_text += "<h3>Recommendations</h3><ul>" + "".join(
                    f"<li>{_esc(r)}</li>" for r in quality["recommendations"][:3]
                ) + "</ul>"

        sections.append(ReportSection(title="Data Quality Assessment", content=quality_text))

        # 3. Key Insights
        if insights and insights.get("key_findings"):
            insights_text = "<ul>"
            for finding in insights["key_findings"]:
                insights_text += f"<li>{_esc(finding)}</li>"
            insights_text += "</ul>"
            sections.append(ReportSection(title="Key Findings", content=insights_text))

        # 4. Correlations
        if correlations and correlations.get("strongest"):
            s = correlations["strongest"]
            corr_text = f"""
            <p><strong>Strongest correlation:</strong> {_esc(s.get('col1', ''))} vs {_esc(s.get('col2', ''))} ({s.get('correlation', 0):.3f})</p>
            <p>Correlation strength: {_esc(s.get('strength', ''))}, direction: {_esc(s.get('direction', ''))}</p>
            """
            sections.append(ReportSection(title="Correlation Analysis", content=corr_text))

        # 5. Outliers
        if outliers and outliers.get("outliers"):
            outlier_text = "<table><tr><th>Column</th><th>Outlier Count</th><th>Percentage</th></tr>"
            for o in outliers["outliers"][:5]:
                outlier_text += f"""
                <tr><td>{_esc(o.get('column', ''))}</td><td>{o.get('outlier_count', 0)}</td><td>{o.get('outlier_pct', 0):.1f}%</td></tr>
                """
            outlier_text += "</table>"
            sections.append(ReportSection(title="Outlier Detection", content=outlier_text))

        # 6. Recommendations
        if insights and insights.get("recommendations"):
            rec_text = "<ul>" + "".join(
                f"<li>{_esc(r)}</li>" for r in insights["recommendations"]
            ) + "</ul>"
            sections.append(ReportSection(title="Recommendations", content=rec_text))

        return self.generate_html_report(
            # NOT escaped here: the title is escaped once, at render time
            # (Jinja autoescape / _esc(title) in the fallback). Escaping it
            # twice renders `tbl & co` as `tbl &amp;amp; co`.
            title=f"Executive Analysis Report: {table_name}",
            sections=sections,
            table_name=table_name,
            row_count=profile.get("row_count", 0),
            column_count=profile.get("column_count", 0),
        )

    def _fallback_html(
        self,
        title: str,
        sections: List[ReportSection],
        generated_at: str,
        table_name: str,
        row_count: int,
        column_count: int,
    ) -> str:
        """Fallback HTML generation without Jinja2 template."""
        html = [
            "<!DOCTYPE html><html><head><meta charset='UTF-8'><style>",
            "body{font-family:system-ui,sans-serif;max-width:900px;margin:40px auto;padding:20px;color:#333}",
            "h1{color:#1a1a2e;border-bottom:3px solid #667eea;padding-bottom:10px}",
            "table{width:100%;border-collapse:collapse;margin:15px 0}",
            "th,td{padding:10px;text-align:left;border-bottom:1px solid #dfe6e9}",
            "th{background:#667eea;color:white}",
            ".section{background:#f8f9fa;padding:15px;border-radius:8px;margin:20px 0}",
            "</style></head><body>",
            f"<h1>{_esc(title)}</h1>",
            f"<p>Generated: {generated_at} | {_esc(table_name)} | {row_count:,} rows × {column_count} cols</p>",
        ]
        for section in sections:
            html.append(f"<div class='section'><h2>{_esc(section.title)}</h2>{section.content}</div>")
        html.append("<div class='footer' style='margin-top:50px;color:#b2bec3;text-align:center;font-size:0.8em'>")
        html.append(f"<p>Generated by LLM Data Analytics Agent &mdash; {generated_at}</p>")
        html.append("</div></body></html>")
        return "\n".join(html)


def _sections_to_dicts(sections: List[ReportSection]) -> List[Dict]:
    """Convert ReportSection dataclasses to dicts for Jinja2."""
    result = []
    for section in sections:
        tables_html = []
        for table in section.tables:
            tables_html.append(table.to_html(index=False))
        result.append({
            "title": section.title,
            "content": section.content,
            "charts": section.charts,
            "tables": tables_html,
        })
    return result
