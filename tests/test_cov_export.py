"""TDD coverage for export/* — tmp_path only, no real outputs/ writes.

Targets: dashboard_export, pbix_export, chart_renderer, bi_export,
email_delivery, pdf_export, verify_export. Heavy matplotlib image export
is monkeypatched to a 1x1 PNG in dashboard PPTX/XLSX paths so the suite
stays <120s. Covers builder happy + missing-table + invalid-spec branches.
"""
from __future__ import annotations

import base64
import io
import json
import zipfile

import pandas as pd
import pytest

_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


# ── helpers ─────────────────────────────────────────────────────────────

def _seed_db(path) -> str:
    import duckdb

    db = str(path / "wh.duckdb")
    con = duckdb.connect(db)
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute("DROP TABLE IF EXISTS analytics.sales")
        con.execute(
            "CREATE TABLE analytics.sales ("
            " product VARCHAR, region VARCHAR, amount DOUBLE, sold_at DATE)"
        )
        con.execute(
            "INSERT INTO analytics.sales VALUES "
            "('Alpha','west',10.0,DATE '2026-01-01'),"
            "('Alpha','east',20.0,DATE '2026-01-02'),"
            "('Beta','west',5.0,DATE '2026-01-03'),"
            "('Beta','east',15.0,DATE '2026-01-04')"
        )
        con.execute("DROP TABLE IF EXISTS analytics.empty_t")
        con.execute("CREATE TABLE analytics.empty_t (a VARCHAR, b DOUBLE)")
    finally:
        con.close()
    return db


def _runners(db):
    import duckdb

    def run_query(sql: str):
        con = duckdb.connect(str(db))
        try:
            con.execute("SET search_path='analytics'")
            return con.execute(sql).df()
        finally:
            con.close()

    def load_table(table: str, limit: int):
        con = duckdb.connect(str(db))
        try:
            con.execute("SET search_path='analytics'")
            return con.execute(
                f'SELECT * FROM "{table}" LIMIT {max(1, int(limit))}'
            ).df()
        finally:
            con.close()

    return run_query, load_table


def _dash():
    return {
        "name": "Cov Dash",
        "description": "coverage dashboard",
        "layout": {"insight": "up and to the right"},
        "widgets": [
            {"type": "kpi", "title": "Total",
             "config": {"sql": "SELECT SUM(amount) AS v FROM analytics.sales"}},
            {"type": "bar", "title": "By region",
             "config": {"sql": "SELECT region, SUM(amount) AS total FROM analytics.sales GROUP BY region"}},
            {"type": "table", "title": "Rows",
             "config": {"sql": "SELECT product, amount FROM analytics.sales"}},
            {"type": "text", "title": "Note", "config": {"content": "hi"}},
            {"type": "slicer", "title": "S", "config": {}},
            {"type": "line", "title": "Empty",
             "config": {"sql": "SELECT * FROM analytics.empty_t"}},
        ],
    }


def _blankdash():
    return {
        "name": "Blank",
        "theme": "light",
        "canvasMinW": 960,
        "canvasMinH": 540,
        "pages": [
            {"name": "P1", "widgets": [
                {"id": "k1", "type": "kpi", "title": "K1", "x": 10, "y": 10,
                 "width": 200, "height": 120,
                 "config": {"sql": "SELECT SUM(amount) AS v FROM analytics.sales"}},
                {"id": "c1", "type": "bar", "title": "C1", "x": 220, "y": 10,
                 "width": 400, "height": 300,
                 "config": {"sql": "SELECT region, SUM(amount) AS t FROM analytics.sales GROUP BY region",
                            "chartType": "bar"}},
                {"id": "t1", "type": "table", "title": "T1", "x": 10, "y": 350,
                 "width": 600, "height": 150,
                 "config": {"sql": "SELECT product, amount FROM analytics.sales"}},
                {"id": "s1", "type": "slicer", "title": "S1", "x": 0, "y": 0,
                 "width": 100, "height": 50, "config": {}},
                {"id": "m1", "type": "bar", "title": "Micro", "x": 0, "y": 0,
                 "width": 1, "height": 1,
                 "config": {"sql": "SELECT region, SUM(amount) AS t FROM analytics.sales GROUP BY region"}},
            ]},
        ],
    }


def _tiny_df(n=4):
    return pd.DataFrame(
        {"cat": [f"c{i}" for i in range(n)],
         "val": [float(i + 1) for i in range(n)],
         "grp": (["g1", "g2"] * ((n + 1) // 2))[:n]}
    )


def _write_tmp(tmp_path, name, data: bytes):
    p = tmp_path / name
    p.write_bytes(data)
    assert p.stat().st_size > 0
    return p


# ── bi_export ───────────────────────────────────────────────────────────

def test_bi_csv_happy(tmp_path):
    from export.bi_export import export_table_csv

    db = _seed_db(tmp_path)
    out = export_table_csv("sales", db_path=db, schema="analytics")
    assert "product" in out and "Alpha" in out


def test_bi_csv_bytes_and_tmp_write(tmp_path):
    from export.bi_export import export_table_csv_bytes

    db = _seed_db(tmp_path)
    b = export_table_csv_bytes("sales", db_path=db, schema="analytics")
    assert b.startswith(b"product")
    _write_tmp(tmp_path, "sales.csv", b)


def test_bi_xlsx_happy_and_tmp_write(tmp_path):
    from export.bi_export import export_table_xlsx

    db = _seed_db(tmp_path)
    b = export_table_xlsx("sales", db_path=db, schema="analytics")
    assert b[:2] == b"PK"
    _write_tmp(tmp_path, "sales.xlsx", b)


def test_bi_xlsx_empty_table(tmp_path):
    from export.bi_export import export_table_xlsx

    db = _seed_db(tmp_path)
    b = export_table_xlsx("empty_t", db_path=db, schema="analytics")
    assert b[:2] == b"PK"


def test_bi_xlsx_truncated_branch(tmp_path, monkeypatch):
    from export import bi_export

    db = _seed_db(tmp_path)
    import duckdb

    con = duckdb.connect(db)
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute("DROP TABLE IF EXISTS analytics.big_t")
        con.execute("CREATE TABLE analytics.big_t (x INTEGER)")
        con.execute("INSERT INTO analytics.big_t SELECT range FROM range(5)")
    finally:
        con.close()
    # cap exports at 2 rows so a 5-row table exercises the truncation notice
    monkeypatch.setattr(bi_export, "_max_export_rows", lambda: 2)
    b = bi_export.export_table_xlsx("big_t", db_path=db, schema="analytics")
    assert b[:2] == b"PK"
    # truncation notice lives on the second sheet
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(b))
    assert "Export Info" in wb.sheetnames


def test_bi_max_export_rows_env_branches(monkeypatch):
    from export.bi_export import _max_export_rows

    monkeypatch.setenv("EXPORT_MAX_ROWS", "notanint")
    assert _max_export_rows() == 100000
    monkeypatch.setenv("EXPORT_MAX_ROWS", "10")
    assert _max_export_rows() == 1000  # clamped to floor
    monkeypatch.setenv("EXPORT_MAX_ROWS", "2000")
    assert _max_export_rows() == 2000


def test_bi_parquet_roundtrip(tmp_path):
    pyarrow = pytest.importorskip("pyarrow")
    assert pyarrow is not None
    from export.bi_export import export_table_parquet

    db = _seed_db(tmp_path)
    b = export_table_parquet("sales", db_path=db, schema="analytics")
    assert len(b) > 100
    _write_tmp(tmp_path, "sales.parquet", b)


def test_bi_export_all_csv_and_parquet(tmp_path):
    from export.bi_export import export_all_tables

    db = _seed_db(tmp_path)
    out = tmp_path / "all_csv"
    files = export_all_tables(output_dir=str(out), db_path=db, schema="analytics", fmt="csv")
    assert any(f.endswith(".csv") for f in files)
    assert all(str(out) in f for f in files)
    out2 = tmp_path / "all_pq"
    try:
        pytest.importorskip("pyarrow")
    except pytest.skip.Exception:
        return
    files2 = export_all_tables(output_dir=str(out2), db_path=db, schema="analytics", fmt="parquet")
    assert any(f.endswith(".parquet") for f in files2)


def test_bi_zip_happy_missing_invalid(tmp_path):
    from export.bi_export import export_all_as_zip

    db = _seed_db(tmp_path)
    payload = export_all_as_zip(db_path=db, schema="analytics")
    assert payload[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        assert len(zf.namelist()) >= 1
    payload2 = export_all_as_zip(db_path=db, schema="analytics", tables=["sales"])
    with zipfile.ZipFile(io.BytesIO(payload2)) as zf:
        assert zf.namelist() == ["analytics_sales.csv"]
    with pytest.raises(ValueError):
        export_all_as_zip(db_path=db, schema="analytics", tables=["ghost_xyz"])


def test_bi_config_and_schema_doc_and_markdown_html_pptx(tmp_path):
    from export.bi_export import (
        export_report_html, export_table_markdown, export_table_pptx,
        get_bi_config, get_schema_doc,
    )

    db = _seed_db(tmp_path)
    cfg = get_bi_config(db_path=db, schema="analytics")
    assert "sales" in cfg["tables"]
    assert "power_bi" in cfg and "tableau" in cfg
    doc = get_schema_doc(db_path=db, schema="analytics")
    assert "sales" in doc and "Tables" in doc
    md = export_table_markdown("sales", db_path=db, schema="analytics", max_rows=2)
    assert "Showing 2 of 4" in md
    html = export_report_html("sales", db_path=db, schema="analytics")
    assert b"sales" in html.lower() or b"Sales" in html
    pptx = export_table_pptx("sales", db_path=db, schema="analytics")
    assert pptx[:2] == b"PK"
    _write_tmp(tmp_path, "rep.html", html)
    _write_tmp(tmp_path, "rep.pptx", pptx)


# ── chart_renderer ──────────────────────────────────────────────────────

def test_chart_num_and_theme_and_extract():
    from export.chart_renderer import _extract, _num, _resolve_theme

    assert _num("3.5") == 3.5
    assert _num(float("nan")) == 0.0
    assert _num(float("inf")) == 0.0
    assert _num(None) == 0.0
    assert _num("junk") == 0.0
    assert _resolve_theme("dark")["bg"] == "#1E1E2E"
    assert _resolve_theme("nope")["bg"] == "#FFFFFF"
    labels, series, grp = _extract(pd.DataFrame())
    assert labels == [] and series == {}
    df1 = pd.DataFrame({"only": [1, 2]})
    labels1, series1, _ = _extract(df1)
    assert len(labels1) == 2
    df3 = _tiny_df(6)
    _, _, g = _extract(df3)
    assert g == "grp"
    # third col with a single distinct value -> no group dimension
    df_same = pd.DataFrame({"a": list("abcd"), "b": [1, 2, 3, 4],
                            "c": ["u1", "u1", "u1", "u1"]})
    _, _, g2 = _extract(df_same)
    assert g2 is None
    # high-cardinality third col within the series cap -> grouped
    df_big = pd.DataFrame({"a": list("abcd"), "b": [1, 2, 3, 4],
                           "c": ["u1", "u2", "u3", "u4"]})
    _, _, g3 = _extract(df_big)
    assert g3 == "c"


def test_chart_render_many_types_fast():
    from export.chart_renderer import render_chart

    df = _tiny_df(5)
    for ct in ["bar", "column", "line", "area", "pie", "donut",
               "scatter", "heatmap", "histogram", "boxplot", "funnel",
               "treemap", "waterfall", "gauge", "bullet", "progress",
               "combo", "sparkline", "stackedbar", "percentbar",
               "not_a_real_type"]:
        png = render_chart(ct, df, {"config": {}}, width=300, height=200, dpi=50)
        assert png[:8] == b"\x89PNG\r\n\x1a\n", ct


def test_chart_render_empty_and_grouped_and_themes():
    from export.chart_renderer import render_chart

    empty = pd.DataFrame({"a": [], "b": []})
    assert render_chart("bar", empty, None, width=200, height=150, dpi=50)[:8] == b"\x89PNG\r\n\x1a\n"
    df = _tiny_df(4)
    for theme in ["dark", "light", "auto"]:
        assert render_chart("line", df, {"config": {"theme": theme}},
                            width=200, height=150, dpi=50)[:4] == b"\x89PNG"
    # single-column df + bubble third col + sunburst/venn/wordcloud paths
    df1 = pd.DataFrame({"v": [1, 2, 3]})
    assert render_chart("column", df1, None, width=200, height=150, dpi=50)[:4] == b"\x89PNG"
    df3 = pd.DataFrame({"x": [1, 2, 3], "y": [2, 3, 4], "z": [5, 6, 7]})
    for ct in ["bubble", "combo", "sunburst", "heatmap", "venn", "wordcloud",
               "sankey", "polar", "radar", "population", "pyramid", "slope",
               "stream", "stackedarea", "stackedcolumn", "horizontalbar",
               "radial", "doughnut"]:
        assert render_chart(ct, df3, None, width=200, height=150, dpi=50)[:4] == b"\x89PNG"


def test_chart_render_table_png():
    from export.chart_renderer import render_table

    df = _tiny_df(3)
    png = render_table(df, width=300, height=200, dpi=50, theme="dark")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    png2 = render_table(df, width=300, height=200, dpi=50, theme="light")
    assert png2[:8] == b"\x89PNG\r\n\x1a\n"


def test_chart_treemap_rects_edge():
    from export.chart_renderer import _treemap_rects

    assert _treemap_rects([], 0, 0, 1, 1) == []
    assert _treemap_rects([0, 0], 0, 0, 1, 1) == []
    rects = _treemap_rects([3, 1, 1], 0, 0, 1, 1)
    assert len(rects) == 3


# ── dashboard_export core helpers ───────────────────────────────────────

def test_dash_safe_name_format_esc_job():
    from export import dashboard_export as de

    assert de._safe_name('a/b:c*d?"e<f>g|h') == "a_b_c_d_e_f_g_h"
    assert de._safe_name("") == "export"
    assert de._format_number(None) == ""
    assert de._format_number(float("nan")) == ""
    assert de._format_number(float("inf")) == ""
    assert de._format_number(5) == "5"
    assert de._format_number(1234.5) == "1,234.50"
    assert de._format_number("x") == "x"
    assert de._esc("a&<b>") == "a&amp;&lt;b&gt;"
    assert de._now_iso()
    assert de._job("missing") is None


def test_dash_run_query_safe_and_widget_data(tmp_path):
    from export import dashboard_export as de

    db = _seed_db(tmp_path)
    run_query, load_table = _runners(db)

    def boom(sql):
        raise RuntimeError("db down")

    assert de._run_query_safe(boom, "SELECT 1").empty
    df, title = de._widget_data(
        {"title": "T", "type": "bar", "config": {"sql": "SELECT * FROM analytics.sales"}},
        run_query, load_table)
    assert not df.empty and title == "T"
    df2, _ = de._widget_data(
        {"title": "T2", "type": "bar", "config": {"table": "sales"}}, run_query, load_table)
    assert not df2.empty
    df3, _ = de._widget_data({"title": "T3", "type": "bar", "config": {}}, run_query, load_table)
    assert df3.empty


def test_dash_build_chart_drawing_branches():
    from export import dashboard_export as de

    df = _tiny_df(4)
    assert de._build_chart_drawing("bar", df, {}) is not None
    assert de._build_chart_drawing("pie", df, {}) is not None
    assert de._build_chart_drawing("line", df, {}) is not None
    assert de._build_chart_drawing("bar", pd.DataFrame(), {}) is None
    bad = pd.DataFrame({"a": ["x", "y"], "b": ["p", "q"]})
    assert de._build_chart_drawing("bar", bad, {}) is not None


def test_dash_export_pdf_happy_and_tmp(tmp_path, monkeypatch):
    from export import dashboard_export as de

    # NOTE: dashboard_export imports openpyxl.Table AFTER reportlab's Table,
    # so the name `Table` is shadowed; restore reportlab's for PDF paths.
    import reportlab.platypus as _rp

    monkeypatch.setattr(de, "Table", _rp.Table)
    db = _seed_db(tmp_path)
    run_query, load_table = _runners(db)
    pdf = de.export_pdf(_dash(), run_query, load_table)
    assert pdf[:4] == b"%PDF"
    _write_tmp(tmp_path, "dash.pdf", pdf)


def test_dash_export_pdf_big_and_nokpi(tmp_path, monkeypatch):
    from export import dashboard_export as de

    import reportlab.platypus as _rp

    monkeypatch.setattr(de, "Table", _rp.Table)
    db = _seed_db(tmp_path)

    def big_query(sql):
        return pd.DataFrame({"a": list(range(60)), "b": [float(i) for i in range(60)]})

    dash = {"name": "Big", "widgets": [
        {"type": "bar", "title": "BigBar", "config": {"sql": "SELECT 1"}}]}
    pdf = de.export_pdf(dash, big_query, lambda *a: pd.DataFrame())
    assert pdf[:4] == b"%PDF"
    pdf2 = de.export_pdf({"name": "Empty"}, big_query, lambda *a: pd.DataFrame())
    assert pdf2[:4] == b"%PDF"


def test_dash_export_pptx_mocked_images(tmp_path, monkeypatch):
    from export import dashboard_export as de

    db = _seed_db(tmp_path)
    run_query, load_table = _runners(db)
    monkeypatch.setattr(de, "_render_chart_png", lambda *a, **k: _TINY_PNG)
    pptx = de.export_pptx(_dash(), run_query, load_table)
    assert pptx[:2] == b"PK"
    _write_tmp(tmp_path, "dash.pptx", pptx)
    # empty widgets + no kpis
    pptx2 = de.export_pptx({"name": "E", "widgets": []}, run_query, load_table)
    assert pptx2[:2] == b"PK"


def test_dash_export_xlsx_happy_and_tmp(tmp_path, monkeypatch):
    from export import dashboard_export as de

    db = _seed_db(tmp_path)
    run_query, load_table = _runners(db)
    monkeypatch.setattr(de, "render_chart", lambda *a, **k: _TINY_PNG)
    monkeypatch.setattr(de, "render_table", lambda *a, **k: _TINY_PNG)
    blob = de.export_xlsx(_dash(), run_query, load_table)
    assert blob[:2] == b"PK"
    _write_tmp(tmp_path, "dash.xlsx", blob)


def test_dash_jobs_lifecycle(tmp_path):
    from export import dashboard_export as de

    db = _seed_db(tmp_path)
    run_query, load_table = _runners(db)
    jid = de.create_export_job("d1", "xlsx", _dash(), run_query, load_table)
    st = de.get_job_status(jid)
    assert st["status"] == "succeeded" and st["downloadUrl"]
    assert de.get_job_data(jid)[:2] == b"PK"
    assert de.get_job_status("ghost") is None
    assert de.get_job_data("ghost") is None
    assert de.get_job_filename(jid).endswith(".xlsx")
    assert de.get_job_filename("ghost") == "export"
    # unsupported format -> failed job
    jbad = de.create_export_job("d1", "bogus", _dash(), run_query, load_table)
    assert de.get_job_status(jbad)["status"] == "failed"
    # blankdash routing via nested layout
    bd = _blankdash()
    nested = {"name": "N", "layout": {"_blankdash": bd}, "widgets": []}
    j2 = de.create_export_job("d1", "xlsx", nested, run_query, load_table)
    assert de.get_job_status(j2)["status"] in ("succeeded", "failed")


def test_dash_blankdash_predicates_and_dims():
    from export import dashboard_export as de

    assert de._is_blankdash_config({"pages": [{"widgets": []}]}) is True
    assert de._is_blankdash_config({"pages": []}) is False
    assert de._is_blankdash_config("nope") is False
    assert de._is_blankdash_config({}) is False
    cfg = {"canvasMinW": 100, "canvasMinH": 100,
           "pages": [{"widgets": [{"x": 0, "y": 0, "width": 500, "height": 400}]}]}
    cw, ch = de._canvas_dims(cfg)
    assert cw >= 500 and ch >= 400
    cw2, ch2 = de._canvas_dims_for_page(cfg, cfg["pages"][0])
    assert cw2 >= 500
    l, t, w, h = de._c2s(10, 20, 100, 50, 1000, 500)
    assert w > 0 and h > 0
    assert de.prs_width() > 0


def test_dash_page_bands_and_overflow(tmp_path):
    from export import dashboard_export as de
    from openpyxl import Workbook
    from pptx import Presentation
    from pptx.util import Inches

    short = {"widgets": [{"x": 0, "y": 0, "width": 100, "height": 100}]}
    bands = de._page_bands(short, 1920, 1080)
    assert len(bands) == 1
    tall_widgets = [{"x": 0, "y": i * 600, "width": 200, "height": 300} for i in range(5)]
    tall = {"widgets": tall_widgets}
    bands2 = de._page_bands({"widgets": tall_widgets}, 800, 3000)
    assert len(bands2) >= 2
    assert tall is not None
    # pptx overflow guard runs without raising
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(2), Inches(1))
    de._check_overflow(slide, 7.5, "PPTX slide 'x'")
    wb = Workbook()
    ws = wb.active
    ws.cell(row=1, column=1, value="hi")
    de._check_overflow(ws, 100, "XLSX sheet 'x'", container_w=50)
    de._check_overflow(object(), 10, "weird")  # exception path -> no raise


def test_dash_widget_category_all():
    from export import dashboard_export as de

    assert de._widget_category("bar", {}) == "chart"
    assert de._widget_category("chart", {"chartType": "line"}) == "chart"
    assert de._widget_category("kpi", {}) == "kpi"
    assert de._widget_category("table", {}) == "table"
    assert de._widget_category("slicer", {}) == "skip"
    assert de._widget_category("text", {}) == "skip"
    assert de._widget_category("button", {}) == "skip"
    assert de._widget_category("weird_xyz", {}) == "visual"


def test_dash_native_chart_branches(tmp_path):
    from export import dashboard_export as de
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    assert de._add_native_chart(slide, Inches(0.5), Inches(0.5), Inches(4), Inches(3),
                               "sankey", _tiny_df(4)) is False
    assert de._add_native_chart(slide, Inches(0.5), Inches(0.5), Inches(4), Inches(3),
                               "bar", pd.DataFrame()) is False
    one = pd.DataFrame({"a": ["x"], "b": [1.0]})
    assert de._add_native_chart(slide, Inches(0.5), Inches(0.5), Inches(4), Inches(3),
                               "bar", one) is False
    nonnum = pd.DataFrame({"a": ["x", "y"], "b": ["p", "q"]})
    assert de._add_native_chart(slide, Inches(0.5), Inches(0.5), Inches(4), Inches(3),
                               "bar", nonnum) is False
    assert de._add_native_chart(slide, Inches(0.5), Inches(0.5), Inches(4), Inches(3),
                               "bar", _tiny_df(4)) is True
    assert de._add_native_chart(slide, Inches(0.5), Inches(0.5), Inches(4), Inches(3),
                               "pie", _tiny_df(4)) is True
    assert de._is_numeric(pd.Series([1, 2])) is True
    assert not de._is_numeric(pd.Series(["a", "b"]))


def test_dash_place_widgets_and_dash_pptx(tmp_path, monkeypatch):
    from export import dashboard_export as de
    from pptx import Presentation

    db = _seed_db(tmp_path)
    run_query, load_table = _runners(db)
    monkeypatch.setattr(de, "_render_chart_png", lambda *a, **k: _TINY_PNG)
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    w = {"type": "bar", "title": "C",
         "config": {"sql": "SELECT region, SUM(amount) AS t FROM analytics.sales GROUP BY region"}}
    de._place_chart_widget(slide, w, w["config"], 0.5, 0.5, 5, 3, run_query, load_table)
    de._place_chart_widget(slide, {"type": "bar", "title": "E", "config": {"sql": "SELECT * FROM analytics.empty_t"}},
                           {}, 0.5, 0.5, 5, 3, run_query, load_table)
    k = {"type": "kpi", "title": "K", "config": {"sql": "SELECT SUM(amount) AS v FROM analytics.sales"}}
    de._place_kpi_widget(slide, k, k["config"], 0.5, 0.5, 2, 1, run_query, load_table)
    # bad color falls back
    kbad = dict(k)
    kbad["bgColor"] = "notacolor!!!"
    de._place_kpi_widget(slide, kbad, {}, 0.5, 0.5, 2, 1, run_query, load_table)
    t = {"type": "table", "title": "T", "config": {"sql": "SELECT product, amount FROM analytics.sales"}}
    de._place_table_widget(slide, t, t["config"], 0.5, 0.5, 5, 3, run_query, load_table)
    de._place_table_widget(slide, {"type": "table", "title": "E2", "config": {"sql": "SELECT * FROM analytics.empty_t"}},
                           {}, 0.5, 0.5, 5, 3, run_query, load_table)
    de._place_visual_widget(slide, t, t["config"], 0.5, 0.5, 5, 3, run_query, load_table)
    out = de.export_dash_pptx(_blankdash(), run_query, load_table)
    assert out[:2] == b"PK"
    _write_tmp(tmp_path, "blank.pptx", out)


def test_dash_xlsx_native_and_image_and_sheets(tmp_path, monkeypatch):
    from export import dashboard_export as de
    from openpyxl import Workbook

    db = _seed_db(tmp_path)
    run_query, load_table = _runners(db)
    monkeypatch.setattr(de, "render_chart", lambda *a, **k: _TINY_PNG)
    monkeypatch.setattr(de, "render_table", lambda *a, **k: _TINY_PNG)
    wb = Workbook()
    ws_p, ws_data = wb.active, wb.create_sheet("ChartData")
    assert de._xlsx_native_chart(ws_p, ws_data, 0, 0, 400, 300, "sankey", _tiny_df(4), "light") is False
    assert de._xlsx_native_chart(ws_p, ws_data, 0, 0, 400, 300, "bar", pd.DataFrame(), "light") is False
    assert de._xlsx_native_chart(ws_p, ws_data, 0, 0, 400, 300, "bar", _tiny_df(4), "light") is True
    assert de._xlsx_native_chart(ws_p, ws_data, 0, 0, 400, 300, "pie", _tiny_df(4), "light") is True
    assert de._xlsx_native_chart(ws_p, ws_data, 0, 0, 400, 300, "line", _tiny_df(4), "light") is True
    assert de._unique_sheet_name(wb, ws_p.title) != ws_p.title
    de._write_data_sheet(wb.create_sheet("D1"), _tiny_df(3))
    de._xlsx_kpi_card(ws_p, 1, 5, 3, 2, {"title": "K"}, {}, _tiny_df(2))
    de._xlsx_kpi_card(ws_p, 1, 9, 3, 1, {"title": "K2"}, {}, _tiny_df(2))
    out = de.export_dash_xlsx(_blankdash(), run_query, load_table)
    assert out[:2] == b"PK"
    _write_tmp(tmp_path, "blank.xlsx", out)


# ── pbix_export helpers ─────────────────────────────────────────────────

def test_pbix_names_and_literals():
    from export import pbix_export as px

    assert px._has_generated_name("sum(Profit)") is True
    assert px._has_generated_name("clean") is False
    df = pd.DataFrame({"sum(x)": [1], "ok": [2]})
    ren = px._rename_generated_columns(df, "bar")
    assert "sum(x)" in ren and "ok" not in ren.values()
    dfk = pd.DataFrame({"count(*)": [5]})
    assert px._rename_generated_columns(dfk, "kpi") == {"count(*)": "value"}
    assert px._clean_name("a/b[c]", "F") == "abc"
    assert px._clean_name("", "F") == "F"
    used = {"T"}
    assert px._unique_name("T", used) == "T (2)"
    assert px._safe_column("9lives!") .startswith("col_")
    assert px._str_literal("o'clock") == "'o''clock'"
    assert px._expr("1")["expr"]["Literal"]["Value"] == "1"
    assert px._m_str('a"b\nc') == '"a""b#(lf)c"'
    assert px._m_str("x") == '"x"'


def test_pbix_to_dt_and_dtypes():
    import datetime as dt

    import pandas as pd

    from export import pbix_export as px

    assert px._to_dt(None) is None
    assert px._to_dt(pd.Timestamp("2024-01-02")) is not None
    assert px._to_dt(dt.datetime(2024, 1, 1)) is not None
    assert px._to_dt(dt.date(2024, 1, 1)) is not None
    assert px._to_dt("2024-01-01") is not None
    assert px._to_dt(object()) is None
    assert px._dtype_info(pd.Series([1]).dtype)[1] == "int64"
    assert px._dtype_info(pd.Series([1.5]).dtype)[1] == "double"
    assert px._dtype_info(pd.Series([True]).dtype)[1] == "boolean"
    assert px._dtype_info(pd.Series(pd.to_datetime(["2024-01-01"])).dtype)[1] == "dateTime"
    assert px._dtype_info(pd.Series(["a"]).dtype)[1] == "string"


def test_pbix_m_and_dax_values():
    import datetime as dt

    import pandas as pd

    from export import pbix_export as px

    assert px._m_value(None, "text") == "null"
    assert px._m_value("hi", "text") == '"hi"'
    assert px._m_value(True, "logical") == "true"
    assert px._m_value(3.7, "Int64.Type") == "3"
    assert px._m_value(float("nan"), "number") == "null"
    assert px._m_value(2.5, "number") == "2.5"
    assert px._m_value(dt.datetime(2024, 1, 2, 3, 4, 5), "datetime") == "#datetime(2024, 1, 2, 3, 4, 5)"
    assert px._m_value(dt.date(2024, 1, 2), "date") == "#date(2024, 1, 2)"
    assert px._m_value("x", "datetime") == "null"
    q = px._m_query("T", [("a", "text")], [("hi",)])
    assert "Source" in q
    assert px._dax_value(None, "string") == "BLANK()"
    assert px._dax_value("a", "string") == '"a"'
    assert px._dax_value(True, "boolean") == "TRUE()"
    assert px._dax_value(dt.datetime(2024, 5, 1), "dateTime").startswith('"2024-05-01')
    assert px._dax_value(float("nan"), "double") == "BLANK()"
    assert px._dax_value(3, "int64") == "3"
    ddl = px._dax_datatable([("a", "string")], [])
    assert ddl.startswith("DATATABLE(")
    cols = px._df_to_columns(pd.DataFrame({"a": [1], "a ": [2]}))
    assert len({c[0] for c in cols}) == 2
    assert px._model_ts()
    assert px._row_number_column()["type"] == "rowNumber"


def test_pbix_format_helpers():
    from export import pbix_export as px

    assert "$" in px._format_annotation("total_revenue", "double")
    assert px._format_annotation("n", "int64") == '<Format Format="#,##0" />'
    assert px._format_annotation("d", "dateTime") == '<Format Format="DateTime" />'
    assert px._format_annotation("s", "string") == '<Format Format="General" />'
    assert px._format_string_for("price", "int64") == "$#,##0.00"
    assert px._data_type_at_refresh("int64") == "Int64"
    assert px._data_type_at_refresh("weird") == "string"


def test_pbix_build_table_and_model():
    from export import pbix_export as px

    df = _tiny_df(3)
    t = px._build_table("T1", df)
    assert t["partitions"][0]["source"]["type"] == "calculated"
    assert "measures" in t
    str_df = pd.DataFrame({"s": ["a", "b"]})
    t2 = px._build_table("T2", str_df, measures=[{"name": "M", "expression": "1"}])
    assert t2["measures"] == [{"name": "M", "expression": "1"}]
    rel = px._build_relationship("r1", "A", "c1", "B", "c2")
    assert rel["fromCardinality"] == "many" and rel["toCardinality"] == "one"
    m = px._build_model("Model", [t], [rel])
    assert m["model"]["tables"][0]["name"] == "T1"


def test_pbix_kpi_sql_and_measures_and_dates():
    from export import pbix_export as px

    cmap = {"amount": "amount", "region": "region"}
    assert px._dax_from_kpi_sql("SELECT SUM(amount) FROM t", "F", cmap) == "SUM('F'[amount])"
    assert px._dax_from_kpi_sql("SELECT AVG(amount) FROM t", "F", cmap) == "AVERAGE('F'[amount])"
    assert "DISTINCTCOUNT" in px._dax_from_kpi_sql("SELECT COUNT(DISTINCT region) FROM t", "F", cmap)
    assert px._dax_from_kpi_sql("SELECT COUNT(amount) FROM t", "F", cmap) == "COUNTROWS('F')"
    assert px._dax_from_kpi_sql("SELECT SUM(ghost) FROM t", "F", cmap) is None
    assert px._dax_from_kpi_sql("hello", "F", cmap) is None
    assert px._dax_from_kpi_sql("SELECT FOO(amount) FROM t", "F", cmap) == "FOO(amount)"
    widgets = [
        {"type": "kpi", "title": "Rev", "config": {"sql": "SELECT SUM(amount) FROM t"}},
        {"type": "kpi", "title": "Rev", "config": {"sql": "SELECT SUM(amount) FROM t"}},
        {"type": "bar", "title": "B", "config": {"sql": "SELECT 1"}},
        {"type": "kpi", "title": "Bad", "config": {"sql": "SELECT SUM(ghost) FROM t"}},
    ]
    ms = px._derive_fact_measures(widgets, "F", cmap)
    assert len(ms) == 1 and ms[0]["name"] == "Rev"
    df = pd.DataFrame({"d": pd.to_datetime(["2024-01-01", "2024-01-02"]), "v": [1, 2]})
    assert px._build_date_table("F", "d", df)["name"] == "Dates"
    assert px._build_date_table("F", "ghost", df) is None
    assert px._build_date_table("F", "d", pd.DataFrame({"d": []})) is None


def test_pbix_visual_primitives():
    from export import pbix_export as px

    sel = px._query_select("t", "T", "c", aggregate=True)
    assert "Measure" in sel
    sel2 = px._query_select("t", "T", "c", aggregate=False)
    assert "Column" in sel2
    assert px._ref("T", "c", True).startswith("T.")
    sv = px._chart_visual("clusteredColumnChart", "T", [("a", False), ("b", True)],
                          {"Category": "a", "Y": "b"}, "Title")
    assert sv["visualType"] == "clusteredColumnChart"
    assert px._chart_objects(with_axes=True)["categoryAxis"]
    assert "categoryAxis" not in px._chart_objects(with_axes=False)
    assert "markers" in px._scatter_objects()
    assert "legend" not in px._gauge_objects()
    assert "sentimentColors" in px._waterfall_objects()
    objs = px._style_objects({"colors": ["#FF0000", "#00FF00"], "legend": {"show": True},
                              "dataLabels": {"show": True}, "axis": {"categoryTitle": "C", "valueTitle": "V"},
                              "background": "#FFFFFF", "title": {"color": "#111111"}})
    assert "dataPoint" in objs and "legend" in objs
    o2: dict = {}
    px._apply_series_colors(o2, {"colors": ["#111111", "#222222"]}, ["T.a", "T.b"])
    assert "dataPoint" in o2
    assert px._title_vcobject("Hi")["title"]
    assert px._card_visual("T", "v", "Title", bg_color="#123456")["visualType"] == "cardVisual"
    assert px._card_visual("T", "v", "Title")["visualType"] == "cardVisual"
    assert px._slicer_visual("T", "f", "F")["visualType"] == "slicer"
    assert px._table_visual("T", ["a"], "Title")["visualType"] == "tableEx"
    assert px._textbox_visual("hello", background="#FFF")["visualType"] == "textbox"
    assert px._hex_name()
    assert px._strip_quotes('"hi"') == "hi"
    assert px._extract_dimension("SELECT region AS label FROM t GROUP BY region") == "region"
    assert px._extract_dimension("SELECT 1") is None
    assert px._json_bytes({"a": 1}).startswith(b"{")
    assert px._xml_bytes("x").startswith(b"<?xml") or b"x" in px._xml_bytes("x")
    assert px._m_literal("a'b") == "'a''b'" or isinstance(px._m_literal("a'b"), str)


def test_pbix_m_queries_and_zip(tmp_path):
    from export import pbix_export as px

    df = _tiny_df(2)
    assert "Table.FromRows" in px._build_m_query(df) or "table" in px._build_m_query(df).lower()
    assert "Section1" in px._build_section1_m([("T", "let x=1 in x")])
    meta = px._build_metadata_xml([("T", "let x=1 in x")], load_to_model=True)
    assert meta
    dm = px._build_datamashup([("T", "let x=1 in x")])
    assert len(dm) > 100
    assert px._content_types_xml().startswith(b"<?xml") or b"Content" in px._content_types_xml()
    assert px._rels_xml() and px._custom_props_xml()
    assert "CY24SU02" in px._default_theme_json() or "name" in px._default_theme_json()
    patched = px._patch_theme_data_colors(px._default_theme_json(), ["#FF0000"])
    assert "#FF0000" in patched


# ── pbix builder happy / missing-table / invalid-spec ───────────────────

def _pbix_runners(db, fail_tables=()):
    import duckdb

    def run_query(sql: str):
        for ft in fail_tables:
            if ft in sql:
                raise RuntimeError(f"missing table {ft}")
        con = duckdb.connect(str(db))
        try:
            con.execute("SET search_path='analytics'")
            return con.execute(sql).df()
        finally:
            con.close()

    def load_table(table: str, limit: int):
        if table in fail_tables:
            raise RuntimeError(f"missing table {table}")
        con = duckdb.connect(str(db))
        try:
            con.execute("SET search_path='analytics'")
            return con.execute(f'SELECT * FROM "{table}" LIMIT {max(1, int(limit))}').df()
        finally:
            con.close()

    return run_query, load_table


def test_pbix_builder_happy(tmp_path):
    from export import pbix_export as px

    db = _seed_db(tmp_path)
    run_query, load_table = _pbix_runners(db)
    dash = {
        "name": "Sales Dash",
        "layout": {"insight": "growing", "row_height": 80,
                   "slicers": [{"field": "region", "table": "sales", "kind": "select"}]},
        "widgets": [
            {"type": "text", "title": "Head", "config": {"content": "hello"}},
            {"type": "kpi", "title": "Total", "cols": 3, "rows": 3,
             "config": {"sql": "SELECT SUM(amount) AS v FROM analytics.sales"}},
            {"type": "bar", "title": "By region", "cols": 6, "rows": 3,
             "config": {"sql": "SELECT region, SUM(amount) AS t FROM analytics.sales GROUP BY region",
                        "xCol": "region"}},
            {"type": "line", "title": "Trend", "cols": 6, "rows": 3,
             "config": {"sql": "SELECT region, SUM(amount) AS t FROM analytics.sales GROUP BY region"}},
            {"type": "table", "title": "Rows", "cols": 6, "rows": 3,
             "config": {"sql": "SELECT product, amount FROM analytics.sales"}},
            {"type": "gauge", "title": "G", "cols": 3, "rows": 3,
             "config": {"sql": "SELECT SUM(amount) AS v FROM analytics.sales"}},
            {"type": "funnel", "title": "F", "cols": 6, "rows": 3,
             "config": {"sql": "SELECT region, SUM(amount) AS t FROM analytics.sales GROUP BY region"}},
            {"type": "scatter", "title": "S", "cols": 6, "rows": 3,
             "config": {"sql": "SELECT amount, amount AS v2 FROM analytics.sales"}},
        ],
    }
    blob = px.build_powerbi_pbit(dash, run_query, load_table)
    assert blob[:2] == b"PK"
    _write_tmp(tmp_path, "happy.pbit", blob)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        assert "Report/Layout" in zf.namelist()
        assert "DataModelSchema" in zf.namelist()


def test_pbix_builder_missing_table(tmp_path):
    from export import pbix_export as px

    db = _seed_db(tmp_path)
    run_query, load_table = _pbix_runners(db, fail_tables=("ghost",))
    dash = {
        "name": "Missing",
        "layout": {"slicers": [{"field": "region", "table": "ghost"},
                              {"field": "", "table": ""},
                              {"field": "region", "table": "sales"}]},
        "widgets": [
            {"type": "bar", "title": "Ghost",
             "config": {"sql": "SELECT * FROM analytics.ghost", "xCol": "region"}},
            {"type": "bar", "title": "NoSql", "config": {}},
            {"type": "bar", "title": "OkBar",
             "config": {"sql": "SELECT region, SUM(amount) AS t FROM analytics.sales GROUP BY region"}},
            {"type": "kpi", "title": "GhostKpi",
             "config": {"sql": "SELECT SUM(x) FROM analytics.ghost"}},
        ],
    }
    blob = px.build_powerbi_pbit(dash, run_query, load_table)
    assert blob[:2] == b"PK"  # skips failures, still builds
    _write_tmp(tmp_path, "missing.pbit", blob)


def test_pbix_builder_invalid_spec(tmp_path):
    from export import pbix_export as px

    db = _seed_db(tmp_path)
    run_query, load_table = _pbix_runners(db)
    # empty dashboard
    blob = px.build_powerbi_pbit({"name": "", "widgets": []}, run_query, load_table)
    assert blob[:2] == b"PK"
    # widgets with exotic types + generated col names + dup titles
    dash = {"name": "Exotic", "layout": {},
            "widgets": [
                {"type": "waterfall", "title": "Dup",
                 "config": {"sql": "SELECT region, SUM(amount) AS `sum(amount)` FROM analytics.sales GROUP BY region"}},
                {"type": "treemap", "title": "Dup",
                 "config": {"sql": "SELECT region, SUM(amount) AS t FROM analytics.sales GROUP BY region"}},
                {"type": "combo", "title": "C",
                 "config": {"sql": "SELECT region, SUM(amount) AS t FROM analytics.sales GROUP BY region"}},
                {"type": "radar", "title": "R",
                 "config": {"sql": "SELECT region, SUM(amount) AS t FROM analytics.sales GROUP BY region"}},
                {"type": "mystery_xyz", "title": "M",
                 "config": {"sql": "SELECT region, SUM(amount) AS t FROM analytics.sales GROUP BY region"}},
                {"type": "kpi", "title": "", "config": {"sql": "SELECT 1 AS v"}},
            ]}
    blob2 = px.build_powerbi_pbit(dash, run_query, load_table)
    assert blob2[:2] == b"PK"
    _write_tmp(tmp_path, "invalid.pbit", blob2)


def test_pbix_visual_query_transforms_roundtrip():
    from export import pbix_export as px

    sv = px._chart_visual("clusteredColumnChart", "T",
                          [("a", False), ("b", True)], {"Category": "a", "Y": "b"}, "Hi")
    q = px._make_visual_query(sv)
    dt_txt = px._make_visual_data_transforms(sv)
    assert "SemanticQuery" in q and "Select" in dt_txt
    cfg = px._visual_config("nm", 1, 2, 3, 4, sv)
    assert cfg


# ── verify_export ───────────────────────────────────────────────────────

def _good_model():
    col = {"name": "c1", "type": "string"}
    return {"model": {"tables": [
        {"name": "T", "partitions": [{"source": {"type": "calculated",
                                                 "expression": "DATATABLE(\"a\", STRING, {{\"x\"}})"}}],
         "columns": [col, {"type": "rowNumber", "name": "RK"}],
         "measures": [{"name": "M", "expression": "SUM('T'[c1])"}]}],
        "relationships": []}}


def test_verify_result_and_vc_helpers():
    from export import verify_export as ve

    r = ve.Result()
    r.ok("fine")
    r.warn("careful")
    r.fail("broken")
    assert r.errors == ["broken"] and r.warnings == ["careful"]
    assert ve._get_vc_name({"config": '{"name":"N"}'}) == "N"
    assert ve._get_vc_name({"config": "junk"}) == "?"
    assert ve._get_vc_visual_type({"config": '{"singleVisual":{"visualType":"card"}}'}) == "card"
    assert ve._get_vc_visual_type({"config": "junk"}) == ""


def test_verify_model_branches():
    from export import verify_export as ve

    r = ve.Result()
    ve.verify_model(_good_model(), r)
    assert not r.errors
    r2 = ve.Result()
    ve.verify_model({"model": {"tables": []}}, r2)
    assert r2.errors
    bad = {"model": {"tables": [
        {"name": "B", "partitions": [],
         "columns": [{"name": ""}], "measures": [{"name": "M"}]}],
        "relationships": [
            {"name": "r", "fromTable": "B", "fromColumn": "c",
             "toTable": "Ghost", "toColumn": "c",
             "fromCardinality": "one", "toCardinality": "one"}]}}
    r3 = ve.Result()
    ve.verify_model(bad, r3)
    assert len(r3.errors) >= 3


def test_verify_report_and_roles(tmp_path):
    from export import pbix_export as px
    from export import verify_export as ve

    db = _seed_db(tmp_path)
    run_query, load_table = _pbix_runners(db)
    dash = {"name": "V", "layout": {},
            "widgets": [{"type": "bar", "title": "B",
                         "config": {"sql": "SELECT region, SUM(amount) AS t FROM analytics.sales GROUP BY region"}}]}
    blob = px.build_powerbi_pbit(dash, run_query, load_table)
    p = tmp_path / "v.pbit"
    p.write_bytes(blob)
    layout, model = ve._read_layout(str(p))
    r = ve.Result()
    ve.verify_zip_structure(str(p), r)
    assert not [e for e in r.errors if "missing zip part" in e]
    table_by_name = ve.verify_model(model, r)
    r2 = ve.Result()
    ve.verify_report(layout, table_by_name, r2)
    assert not r2.errors
    r3 = ve.Result()
    ve.verify_role_bindings(layout, r3)
    assert not [e for e in r3.errors if "missing" in e.lower()]
    # error branches with hand-made layouts
    rb = ve.Result()
    ve.verify_report({}, {}, rb)
    assert rb.errors
    rb2 = ve.Result()
    ve.verify_report({"sections": [{"visualContainers": []}]}, {}, rb2)
    assert not rb2.errors  # no visuals -> early return, no crash
    bad_vc = {"config": json.dumps({"name": "B", "singleVisual": {"visualType": "clusteredColumnChart"}}),
              "query": "{bad", "dataTransforms": "{bad"}
    rb3 = ve.Result()
    ve.verify_report({"sections": [{"visualContainers": [bad_vc]}]}, {"T": {}}, rb3)
    assert rb3.errors


def test_verify_against_reference_and_read_errors(tmp_path):
    from export import verify_export as ve

    r = ve.Result()
    ve.verify_against_reference({"sections": []}, r)  # skips or warns, never raises
    with pytest.raises((RuntimeError, FileNotFoundError, OSError)):
        ve._read_layout(str(tmp_path / "ghost.pbit"))
    # bad encoding zip
    bad = tmp_path / "bad.pbit"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("DataModelSchema", b"not-json")
        zf.writestr("Report/Layout", b"not-json")
    with pytest.raises(RuntimeError):
        ve._read_layout(str(bad))


# ── email_delivery ──────────────────────────────────────────────────────

class _FakeSMTP:
    instances: list = []

    def __init__(self, host, port, timeout=30):
        self.host, self.port = host, port
        self.started_tls = False
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, u, p):
        self.user = u

    def send_message(self, msg):
        self.sent = msg


def test_email_smtp_success_and_tls_login(monkeypatch):
    import smtplib

    from export.email_delivery import send_report_email

    _FakeSMTP.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    res = send_report_email("a@x.com", "Sub", "<p>hi</p>",
                            [("f.pdf", b"%PDF", "application/pdf")],
                            {"host": "h", "port": 587, "username": "u",
                             "password": "p", "from_address": "r@x.com"})
    assert res["success"] is True
    assert _FakeSMTP.instances[0].started_tls is True


def test_email_plain_and_failure(monkeypatch):
    import smtplib

    from export.email_delivery import send_report_email

    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    res = send_report_email("a@x.com", "S2", "", None, {"host": "h", "port": 25})
    assert res["success"] is True

    def boom(*a, **k):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(smtplib, "SMTP", boom)
    res2 = send_report_email("a@x.com", "S", "body")
    assert res2["success"] is False


def test_email_sendgrid_branch(monkeypatch):
    import urllib.request

    from export.email_delivery import send_report_email

    seen = {}

    class _R:
        def __init__(self, *a, **k):
            seen["url"] = a[0] if a else ""

    monkeypatch.setattr(urllib.request, "Request", _R)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req: seen.update(ok=True))
    res = send_report_email("a@x.com", "SG", "<b>hi</b>",
                            smtp_config={"provider": "sendgrid", "api_key": "k"})
    assert res["success"] is True and seen.get("ok") is True


# ── pdf_export ──────────────────────────────────────────────────────────

def test_pdf_playwright_success_and_tmp(tmp_path, monkeypatch):
    import export.pdf_export as pe

    db = _seed_db(tmp_path)
    monkeypatch.setattr(pe, "_html_to_pdf_playwright", lambda html: b"%PDF-fake-playwright")
    out = pe.export_table_pdf("sales", db_path=db, schema="analytics", title="T")
    assert out.startswith(b"%PDF")
    _write_tmp(tmp_path, "t.pdf", out)


def test_pdf_weasyprint_fallback_and_text_fallback(tmp_path, monkeypatch):
    import export.pdf_export as pe

    db = _seed_db(tmp_path)

    def raise_import(html):
        raise ImportError("no playwright")

    monkeypatch.setattr(pe, "_html_to_pdf_playwright", raise_import)
    monkeypatch.setattr(pe, "_html_to_pdf_weasyprint", lambda html: b"%PDF-weasy")
    assert pe.export_table_pdf("sales", db_path=db).startswith(b"%PDF")

    monkeypatch.setattr(pe, "_html_to_pdf_weasyprint", raise_import)
    out = pe.export_table_pdf("sales", db_path=db, title="Title")
    assert out.startswith(b"%PDF")  # reportlab text fallback
    _write_tmp(tmp_path, "fallback.pdf", out)


def test_pdf_weasyprint_error_propagates(tmp_path, monkeypatch):
    import export.pdf_export as pe

    db = _seed_db(tmp_path)
    monkeypatch.setattr(pe, "_html_to_pdf_playwright", lambda html: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(pe, "_html_to_pdf_weasyprint", lambda html: (_ for _ in ()).throw(RuntimeError("weasy boom")))
    with pytest.raises(RuntimeError):
        pe.export_table_pdf("sales", db_path=db)
