"""
One-time DuckDB corruption repair.

Symptom: "Failed to delete all rows from index. Only deleted 0 out of 1 rows."
on analytics.catalog_assets (e.g. when the 'calendar' dimension row is
updated). The heap rows are fine but the primary-key ART index entry is
dangling, so DuckDB invalidates the whole DB connection.

Fix: rebuild the table (fresh heap + fresh ART index) by copy/DROP/RENAME.

Run with the app STOPPED:
    python3 server/repair_warehouse.py   # or: python3 scripts/repair_warehouse.py
The original file is backed up next to it first.

Usage: python3 repair_warehouse.py [--dry-run]
"""

import argparse
import shutil
import sys
import time
from pathlib import Path

import duckdb

DB = Path("data/curated/warehouse.duckdb")
SCHEMA = "analytics"

# Exact DDL from api/catalog.py::CatalogManager._init_tables
CATALOG_DDL = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA}.catalog_assets (
    id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    table_name VARCHAR NOT NULL,
    description VARCHAR DEFAULT '',
    schema_json VARCHAR DEFAULT '{{}}',
    owner VARCHAR DEFAULT '',
    domain VARCHAR DEFAULT 'general',
    certification_status VARCHAR DEFAULT 'uncertified',
    tags VARCHAR[] DEFAULT [],
    quality_score DOUBLE DEFAULT 0.0,
    row_count BIGINT DEFAULT 0,
    source_type VARCHAR DEFAULT 'warehouse',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


def backup():
    bak = DB.with_name(f"{DB.name}.bak-{time.strftime('%Y%m%d%H%M%S')}")
    if not bak.exists():
        shutil.copy2(DB, bak)
        print(f"backup -> {bak}")
    return bak


def rebuild_catalog(con, dry_run: bool) -> bool:
    has = con.execute(
        "SELECT 1 FROM information_schema.tables "
        f"WHERE table_schema='{SCHEMA}' AND table_name='catalog_assets'"
    ).fetchone()
    if not has or dry_run:
        print("catalog_assets: nothing to rebuild (missing or dry-run)")
        return True
    n = con.execute(f"SELECT COUNT(*) FROM {SCHEMA}.catalog_assets").fetchone()[0]
    con.execute("DROP TABLE IF EXISTS analytics.catalog_assets_new")
    con.execute(CATALOG_DDL.replace("catalog_assets (", "catalog_assets_new ("))
    # copy columns explicitly in the canonical order
    con.execute(f"""
        INSERT INTO {SCHEMA}.catalog_assets_new
            (id, name, table_name, description, schema_json, owner, domain,
             certification_status, tags, quality_score, row_count, source_type,
             created_at, updated_at)
        SELECT id, name, table_name, description, schema_json, owner, domain,
               certification_status, tags, quality_score, row_count, source_type,
               created_at, updated_at
        FROM {SCHEMA}.catalog_assets
    """)
    con.execute(f"DROP TABLE {SCHEMA}.catalog_assets")
    con.execute(f"ALTER TABLE {SCHEMA}.catalog_assets_new RENAME TO catalog_assets")
    con.execute(f"CREATE INDEX IF NOT EXISTS idx_catalog_assets_table ON {SCHEMA}.catalog_assets(table_name)")
    con.execute(f"CREATE INDEX IF NOT EXISTS idx_catalog_assets_domain ON {SCHEMA}.catalog_assets(domain)")
    con.execute(f"CREATE INDEX IF NOT EXISTS idx_catalog_assets_cert ON {SCHEMA}.catalog_assets(certification_status)")
    print(f"catalog_assets: rebuilt with {n} rows")
    return True


def validate(con) -> bool:
    ok = True
    # the exact statement that used to fatal: UPDATE the calendar row in place
    row = con.execute(
        f"SELECT id FROM {SCHEMA}.catalog_assets WHERE lower(table_name)='calendar'"
    ).fetchone()
    if row:
        try:
            con.execute(
                f"UPDATE {SCHEMA}.catalog_assets SET updated_at=? WHERE id=?",
                ["2026-01-01T00:00:00", row[0]],
            )
            print("validate: calendar UPDATE ok")
        except Exception as e:  # noqa: BLE001
            print(f"validate: calendar UPDATE FAILED: {e}")
            ok = False
    else:
        print("validate: no calendar row (fine)")
    # every user table is at least readable
    for (t,) in con.execute(
        "SELECT table_name FROM information_schema.tables "
        f"WHERE table_schema='{SCHEMA}'"
    ).fetchall():
        try:
            con.execute(f"SELECT * FROM {SCHEMA}.{t} LIMIT 1").fetchone()
        except Exception as e:  # noqa: BLE001
            print(f"validate: {t} unreadable: {e}")
            ok = False
    print("validate: done →", "ALL OK" if ok else "FAILURES")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    backup()
    con = duckdb.connect(str(DB))
    try:
        rebuild_catalog(con, args.dry_run)
        con.execute("CHECKPOINT")
        valid = validate(con)
    finally:
        con.close()
    sys.exit(0 if valid else 1)


if __name__ == "__main__":
    main()