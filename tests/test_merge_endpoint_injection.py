"""TDD (F1 companion): /merge interpolated table names unquoted.

`DESCRIBE {table}` and `FROM {table}` used the raw (quote-stripped but not
SQL-quoted) request fields. A pre-existing table whose name contains `;`
(after the name is created directly in the DB, e.g. by an ETL or restore
path) turned the request into stacked statements that dropped other tables.

Fix under test: identifiers are quoted everywhere; the endpoint still
merges legitimately-named tables.
"""

import duckdb


def _table_exists(db_path, name: str) -> bool:
    con = duckdb.connect(str(db_path))
    try:
        return con.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = 'analytics' AND table_name = ?",
            [name],
        ).fetchone()[0] == 1
    finally:
        con.close()


def test_merge_weird_table_name_does_not_inject(billing_db, monkeypatch):
    from fastapi.testclient import TestClient

    main_mod = __import__("api.main", fromlist=["app"])
    monkeypatch.setattr(main_mod, "loader", billing_db)

    weird = "x ; DROP TABLE analytics.victim ; --"
    con = duckdb.connect(str(billing_db.db_path))
    con.execute(f'CREATE TABLE analytics."{weird}" (id INTEGER)')
    con.execute(f'INSERT INTO analytics."{weird}" VALUES (1), (2)')
    con.execute("CREATE TABLE analytics.victim (id INTEGER)")
    con.execute("INSERT INTO analytics.victim VALUES (1)")
    con.close()

    client = TestClient(main_mod.app)
    from tests.conftest import auth_headers

    headers = auth_headers()
    r = client.post(
        "/merge",
        json={
            "left_table": weird,
            "right_table": "victim",
            "left_key": "id",
            "right_key": "id",
        },
        headers=headers,
    )

    assert _table_exists(billing_db.db_path, "victim"), "injection dropped victim"
    assert r.status_code == 200, (r.status_code, r.text[:300])
    assert r.json().get("success") is True, r.text[:300]


def test_merge_legit_tables(billing_db, monkeypatch):
    from fastapi.testclient import TestClient

    main_mod = __import__("api.main", fromlist=["app"])
    monkeypatch.setattr(main_mod, "loader", billing_db)

    con = duckdb.connect(str(billing_db.db_path))
    con.execute("CREATE TABLE analytics.left_t (id INTEGER, v VARCHAR)")
    con.execute("INSERT INTO analytics.left_t VALUES (1, 'a'), (2, 'b')")
    con.execute("CREATE TABLE analytics.right_t (id INTEGER, w VARCHAR)")
    con.execute("INSERT INTO analytics.right_t VALUES (1, 'x')")
    con.close()

    client = TestClient(main_mod.app)
    from tests.conftest import auth_headers

    headers = auth_headers()
    r = client.post(
        "/merge",
        json={
            "left_table": "left_t",
            "right_table": "right_t",
            "left_key": "id",
            "right_key": "id",
        },
        headers=headers,
    )
    assert r.status_code == 200, (r.status_code, r.text[:300])
    body = r.json()
    assert body.get("success") is True, body
    assert body.get("total_rows") == 1
