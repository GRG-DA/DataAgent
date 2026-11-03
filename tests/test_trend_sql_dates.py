"""Regression: trend SQL must parse the mixed date formats found in real CSVs."""

from __future__ import annotations

import duckdb

from api.main import _trend_sql


def test_trend_sql_parses_mixed_date_formats_and_skips_invalid_rows():
    con = duckdb.connect()
    con.execute('CREATE TABLE sales_demo ("Order Date" VARCHAR, "Profit" DOUBLE)')
    con.executemany(
        'INSERT INTO sales_demo VALUES (?, ?)',
        [
            ("2026-04-02", 10.0),
            ("06/04/2026", 20.0),
            ("11-04-2026", 30.0),
            ("Apr 16 2026", 50.0),
            ("May 4 2026", 40.0),
            ("2026-06-01", 60.0),
            ("not-a-date", 99.0),
        ],
    )

    rows = con.execute(_trend_sql("sales_demo", "Order Date", "Profit", "month")).fetchall()

    assert rows == [
        ("2026-04", 110.0),
        ("2026-05", 40.0),
        ("2026-06", 60.0),
    ]
