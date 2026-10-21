"""Launch-hardening tests for the export layer.

Covers three launch defects:

* **S3** — CSV / XLSX / Markdown *formula injection*.  Attacker-controlled
  cell values (an uploaded CSV can contain ``=HYPERLINK(...)``,
  ``=cmd|' /C calc'!A1``, ``+1+1``, ``@SUM(...)``) must never be written to a
  spreadsheet as a *live formula*: openpyxl turns a leading ``=`` into a
  formula cell and Excel/LibreOffice evaluate it when the victim opens the
  exported file.
* **S4** — HTML injection.  Column names and cell values are interpolated
  raw into the HTML report export, so ``<script>`` / ``<img onerror=...>``
  land in the document.
* **C5** — the PDF export fallback is a silent blank-ish page that tells the
  user to "install Playwright" (not shipped in the desktop build).

Every test builds its own throwaway DuckDB file under ``tmp_path``; the
real warehouse is never touched.
"""

from __future__ import annotations

import base64
import csv
import io
import re
import zipfile
import zlib

import pytest


# ── helpers ──────────────────────────────────────────────────────────────

_FORMULA_TRIGGERS = ("=", "+", "-", "@")


def _seed_db(tmp_path, name: str = "harden.duckdb") -> str:
    """Create a warehouse with hostile cell values, column names and HTML."""
    import duckdb

    db = str(tmp_path / name)
    con = duckdb.connect(db)
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute("DROP TABLE IF EXISTS analytics.evil")
        con.execute(
            "CREATE TABLE analytics.evil ("
            " plain VARCHAR, formula VARCHAR, plus VARCHAR, minus VARCHAR,"
            " at VARCHAR, tabbed VARCHAR, crlf VARCHAR, pipe VARCHAR,"
            " amount DOUBLE)"
        )
        con.execute(
            "INSERT INTO analytics.evil VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                "hello",
                '=HYPERLINK("http://evil","x")',
                "+1+1",
                "-1+1",
                "@SUM(1+1)",
                "\t=cmd|'/C calc'!A1",
                "\r=cmd|'/C calc'!A1",
                "x|y",
                42.0,
            ],
        )
        # a column *name* that is itself a formula
        con.execute("DROP TABLE IF EXISTS analytics.evil_cols")
        con.execute(
            "CREATE TABLE analytics.evil_cols "
            "(\"=cmd|'/C calc'!A1\" VARCHAR, \"ok\" VARCHAR)"
        )
        con.execute("INSERT INTO analytics.evil_cols VALUES (?, ?)", ["x", "y"])
        # hostile column name + hostile value for the HTML / Markdown paths
        con.execute("DROP TABLE IF EXISTS analytics.evil_html")
        con.execute(
            "CREATE TABLE analytics.evil_html "
            "(\"<script>alert(1)</script>\" VARCHAR, \"plain\" VARCHAR)"
        )
        con.execute(
            "INSERT INTO analytics.evil_html VALUES (?, ?)",
            ["<img src=x onerror=alert(1)>", "safe"],
        )
    finally:
        con.close()
    return db


def _csv_rows(text: str):
    """Parse a CSV document into a list of rows (newline-safe)."""
    return list(csv.reader(io.StringIO(text, newline="")))


def _csv_row(text: str, index: int):
    return _csv_rows(text)[index]


def _assert_no_live_formula(rows):
    """No field may start with a character a spreadsheet evaluates."""
    for row in rows:
        for field in row:
            assert not field.startswith(_FORMULA_TRIGGERS), (
                f"field {field!r} would be evaluated as a formula"
            )


def _read_xlsx(data: bytes):
    """Return (headers, data_rows, worksheet) for an exported workbook."""
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data))
    ws = wb.active
    rows = [[c.value for c in row] for row in ws.iter_rows()]
    return rows[0], rows[1:], ws


def _pdf_text(data: bytes) -> str:
    """Best-effort text extraction from a PDF (ASCII85 + Flate aware)."""
    chunks = []
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        raw = match.group(1).strip()
        try:
            raw = base64.a85decode(raw, adobe=True)
        except Exception:
            pass
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass
        for token in re.finditer(rb"\(((?:\\.|[^\\()])*)\)", raw, re.S):
            piece = re.sub(rb"\\([()\\])", rb"\1", token.group(1))
            chunks.append(piece.decode("latin-1"))
    return " ".join(chunks)


def _force_pdf_fallback(monkeypatch, pdf_export) -> None:
    """Make both HTML renderers unavailable so the reportlab stub is used."""

    def _unavailable(html_content):
        raise ImportError("renderer not installed")

    monkeypatch.setattr(pdf_export, "_html_to_pdf_playwright", _unavailable)
    monkeypatch.setattr(pdf_export, "_html_to_pdf_weasyprint", _unavailable)


# ── S3: CSV formula injection ────────────────────────────────────────────

def test_csv_formula_value_is_written_as_text(tmp_path):
    from export.bi_export import export_table_csv

    db = _seed_db(tmp_path)
    out = export_table_csv("evil", db_path=db, schema="analytics")

    header = _csv_row(out, 0)
    values = dict(zip(header, _csv_row(out, 1)))

    assert values["formula"] == '\'=HYPERLINK("http://evil","x")'
    assert values["formula"].startswith("'")


def test_csv_plus_minus_at_values_are_written_as_text(tmp_path):
    from export.bi_export import export_table_csv

    db = _seed_db(tmp_path)
    out = export_table_csv("evil", db_path=db, schema="analytics")

    values = dict(zip(_csv_row(out, 0), _csv_row(out, 1)))

    assert values["plus"] == "'+1+1"
    assert values["minus"] == "'-1+1"
    assert values["at"] == "'@SUM(1+1)"
    for key in ("plus", "minus", "at"):
        assert values[key].startswith("'")


def test_csv_control_character_prefixed_values_are_written_as_text(tmp_path):
    from export.bi_export import export_table_csv

    db = _seed_db(tmp_path)
    out = export_table_csv("evil", db_path=db, schema="analytics")

    values = dict(zip(_csv_row(out, 0), _csv_row(out, 1)))

    assert values["tabbed"] == "'\t=cmd|'/C calc'!A1"
    assert values["crlf"] == "'\r=cmd|'/C calc'!A1"


def test_csv_contains_no_bare_formula_field(tmp_path):
    from export.bi_export import export_table_csv

    db = _seed_db(tmp_path)
    out = export_table_csv("evil", db_path=db, schema="analytics")

    _assert_no_live_formula(_csv_rows(out))


def test_csv_plain_string_is_unchanged(tmp_path):
    from export.bi_export import export_table_csv

    db = _seed_db(tmp_path)
    out = export_table_csv("evil", db_path=db, schema="analytics")

    values = dict(zip(_csv_row(out, 0), _csv_row(out, 1)))

    assert values["plain"] == "hello"


def test_csv_numeric_value_is_written_as_number(tmp_path):
    from export.bi_export import export_table_csv

    db = _seed_db(tmp_path)
    out = export_table_csv("evil", db_path=db, schema="analytics")

    values = dict(zip(_csv_row(out, 0), _csv_row(out, 1)))

    assert values["amount"] == "42.0"
    assert not values["amount"].startswith("'")


def test_csv_formula_column_name_is_written_as_text(tmp_path):
    from export.bi_export import export_table_csv

    db = _seed_db(tmp_path)
    out = export_table_csv("evil_cols", db_path=db, schema="analytics")

    header = _csv_row(out, 0)

    assert header[0].startswith("'")
    assert header[0] == "\'=cmd|'/C calc'!A1"
    assert header[1] == "ok"


def test_export_all_tables_csv_is_sanitised(tmp_path):
    from export.bi_export import export_all_tables

    db = _seed_db(tmp_path)
    out_dir = tmp_path / "all"
    files = export_all_tables(output_dir=str(out_dir), db_path=db, schema="analytics", fmt="csv")

    evil_csv = [f for f in files if f.endswith("evil.csv")]
    assert evil_csv, "expected evil.csv to be exported"
    text = (tmp_path / "all" / "evil.csv").read_text(encoding="utf-8")
    _assert_no_live_formula(_csv_rows(text))


def test_export_all_as_zip_csv_is_sanitised(tmp_path):
    from export.bi_export import export_all_as_zip

    db = _seed_db(tmp_path)
    payload = export_all_as_zip(db_path=db, schema="analytics")

    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        name = "analytics_evil.csv"
        assert name in zf.namelist()
        text = zf.read(name).decode("utf-8")
    _assert_no_live_formula(_csv_rows(text))


# ── S3: XLSX formula injection ───────────────────────────────────────────

def test_xlsx_formula_cell_is_text_not_formula(tmp_path):
    from export.bi_export import export_table_xlsx

    db = _seed_db(tmp_path)
    data = export_table_xlsx("evil", db_path=db, schema="analytics")

    headers, rows, ws = _read_xlsx(data)
    idx = headers.index("formula")
    cell = ws.cell(row=2, column=idx + 1)

    assert isinstance(rows[0][idx], str)
    assert rows[0][idx].startswith("'")
    assert rows[0][idx] == '\'=HYPERLINK("http://evil","x")'
    assert cell.data_type != "f"


def test_xlsx_control_character_prefixed_cells_are_text(tmp_path):
    from export.bi_export import export_table_xlsx

    db = _seed_db(tmp_path)
    data = export_table_xlsx("evil", db_path=db, schema="analytics")

    headers, rows, ws = _read_xlsx(data)

    for name in ("plus", "minus", "at", "tabbed", "crlf"):
        idx = headers.index(name)
        assert rows[0][idx].startswith("'"), name
        assert ws.cell(row=2, column=idx + 1).data_type != "f", name


def test_xlsx_formula_column_name_is_text_not_formula(tmp_path):
    from export.bi_export import export_table_xlsx

    db = _seed_db(tmp_path)
    data = export_table_xlsx("evil_cols", db_path=db, schema="analytics")

    headers, _rows, ws = _read_xlsx(data)

    assert headers[0].startswith("'")
    assert ws.cell(row=1, column=1).data_type != "f"


def test_xlsx_numeric_cell_stays_numeric(tmp_path):
    from export.bi_export import export_table_xlsx

    db = _seed_db(tmp_path)
    data = export_table_xlsx("evil", db_path=db, schema="analytics")

    headers, rows, ws = _read_xlsx(data)
    idx = headers.index("amount")
    cell = ws.cell(row=2, column=idx + 1)

    assert rows[0][idx] == 42.0
    assert cell.data_type == "n"


def test_xlsx_plain_string_is_unchanged(tmp_path):
    from export.bi_export import export_table_xlsx

    db = _seed_db(tmp_path)
    data = export_table_xlsx("evil", db_path=db, schema="analytics")

    headers, rows, _ws = _read_xlsx(data)
    idx = headers.index("plain")

    assert rows[0][idx] == "hello"


# ── S3: Markdown export ──────────────────────────────────────────────────

def test_markdown_escapes_html_in_cell_values(tmp_path):
    from export.bi_export import export_table_markdown

    db = _seed_db(tmp_path)
    md = export_table_markdown("evil_html", db_path=db, schema="analytics")

    assert "<script>" not in md
    assert "<img src=x onerror=alert(1)>" not in md
    assert "&lt;script&gt;" in md
    assert "&lt;img src=x onerror=alert(1)&gt;" in md


def test_markdown_escapes_html_in_column_names(tmp_path):
    from export.bi_export import export_table_markdown

    db = _seed_db(tmp_path)
    md = export_table_markdown("evil_html", db_path=db, schema="analytics")

    assert "<script>" not in md


def test_markdown_keeps_table_structurally_valid(tmp_path):
    from export.bi_export import export_table_markdown

    db = _seed_db(tmp_path)
    md = export_table_markdown("evil", db_path=db, schema="analytics")

    lines = [ln for ln in md.splitlines() if ln.startswith("|")]
    assert len(lines) >= 3
    for line in lines:
        # every row has the same number of unescaped cell separators
        assert len(re.findall(r"(?<!\\)\|", line)) == len(
            re.findall(r"(?<!\\)\|", lines[0])
        )
    assert "x\\|y" in md


def test_markdown_neutralises_formula_prefixed_values(tmp_path):
    from export.bi_export import export_table_markdown

    db = _seed_db(tmp_path)
    md = export_table_markdown("evil", db_path=db, schema="analytics")

    assert "'=HYPERLINK" in md


# ── S4: HTML export injection ────────────────────────────────────────────

def test_html_export_escapes_column_names_and_values(tmp_path):
    from export.bi_export import export_report_html

    db = _seed_db(tmp_path)
    html = export_report_html("evil_html", db_path=db, schema="analytics").decode("utf-8")

    assert "<script>" not in html
    assert "<img src=x onerror=alert(1)>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_html_export_keeps_layout(tmp_path):
    from export.bi_export import export_report_html

    db = _seed_db(tmp_path)
    html = export_report_html("evil_html", db_path=db, schema="analytics").decode("utf-8")

    assert html.startswith("<!DOCTYPE html>")
    assert "Data Preview" in html
    assert "</html>" in html


def test_html_export_neutralises_formula_values(tmp_path):
    """Excel evaluates a cell that starts with '=' even in an HTML table."""
    from export.bi_export import export_report_html

    db = _seed_db(tmp_path)
    html = export_report_html("evil", db_path=db, schema="analytics").decode("utf-8")

    # the value is quote-prefixed (HTML-escaped as &#x27;) so it stays text
    assert "<td>=HYPERLINK" not in html
    assert "<td>&#x27;=HYPERLINK" in html


# ── C5: PDF fallback honesty ──────────────────────────────────────────────

def test_pdf_fallback_returns_a_valid_pdf(tmp_path, monkeypatch):
    import export.pdf_export as pe

    db = _seed_db(tmp_path)
    _force_pdf_fallback(monkeypatch, pe)

    out = pe.export_table_pdf("evil", db_path=db, schema="analytics", title="Hardened")

    assert out.startswith(b"%PDF-")
    assert len(out) > 500


def test_pdf_fallback_states_formatting_is_unavailable(tmp_path, monkeypatch):
    import export.pdf_export as pe

    db = _seed_db(tmp_path)
    _force_pdf_fallback(monkeypatch, pe)

    out = pe.export_table_pdf("evil", db_path=db, schema="analytics", title="Hardened")
    text = _pdf_text(out).lower()

    assert "not available in this build" in text
    assert "install playwright" not in text


def test_pdf_fallback_names_a_supported_alternative(tmp_path, monkeypatch):
    import export.pdf_export as pe

    db = _seed_db(tmp_path)
    _force_pdf_fallback(monkeypatch, pe)

    out = pe.export_table_pdf("evil", db_path=db, schema="analytics", title="Hardened")
    text = _pdf_text(out).lower()

    assert "html" in text
    assert "csv" in text or "pptx" in text


def test_pdf_detailed_export_flags_degraded_output(tmp_path, monkeypatch):
    import export.pdf_export as pe

    db = _seed_db(tmp_path)
    _force_pdf_fallback(monkeypatch, pe)

    result = pe.export_table_pdf_detailed("evil", db_path=db, schema="analytics")

    assert result["pdf"].startswith(b"%PDF-")
    assert result["backend"] == "reportlab"
    assert result["warning"]
    assert "html" in result["warning"].lower()


def test_pdf_detailed_export_has_no_warning_with_a_real_renderer(tmp_path, monkeypatch):
    import export.pdf_export as pe

    db = _seed_db(tmp_path)
    monkeypatch.setattr(pe, "_html_to_pdf_playwright", lambda html: b"%PDF-1.4 fake")

    result = pe.export_table_pdf_detailed("evil", db_path=db, schema="analytics")

    assert result["backend"] == "playwright"
    assert result["warning"] is None
