"""Regression tests: writes on analytics tables whose PK/UNIQUE constraints
were stripped at boot (api.db.strip_constraint_indexes) must not rely on
upsert semantics (INSERT OR REPLACE / ON CONFLICT).

Production state mirrored here: table created with PRIMARY KEY, then rebuilt
without it so row writes go through the heap. Two write paths assumed the
constraint still existed:

  * api.sla.SLAManager.start_pipeline_run — ``ON CONFLICT (id) DO UPDATE``
    (BinderError: conflict target not backed by a UNIQUE/PRIMARY KEY).
  * api.premium._set_setting — ``INSERT OR REPLACE`` degrades to a plain
    INSERT and duplicates keys.

Also covers api.sla.SLAManager.get_pipeline_runs returning real column names
(previously captured ``conn.description`` after the COUNT query, so every run
dict was keyed by the count alias instead of its columns).
"""

import duckdb
import pytest

# --- Ensure global warehouse schema exists BEFORE importing api.sla ---
# api.sla creates a global SLAManager() at import which does
# SET search_path='analytics' — fails on a fresh temp warehouse.
try:
    from config import settings as _settings

    _c = duckdb.connect(_settings.duckdb.database_path)
    try:
        _c.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    finally:
        _c.close()
except Exception:
    pass

PIPELINE_COLUMNS = {
    "id",
    "pipeline_name",
    "status",
    "started_at",
    "finished_at",
    "rows_processed",
    "error_count",
    "error_message",
}


def _stripped_pipeline_db(tmp_path, name="pipeline_stripped.duckdb"):
    """A warehouse file whose pipeline_runs lost its PK at boot, with the
    secondary index surviving (matches post-strip production state)."""
    path = str(tmp_path / name)
    con = duckdb.connect(path)
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute(
            """CREATE TABLE analytics.pipeline_runs (
                id VARCHAR,
                pipeline_name VARCHAR NOT NULL,
                status VARCHAR DEFAULT 'running',
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMP,
                rows_processed BIGINT DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                error_message VARCHAR DEFAULT ''
            )"""
        )
        con.execute(
            "CREATE INDEX idx_pipeline_runs_name ON analytics.pipeline_runs(pipeline_name)"
        )
    finally:
        con.close()
    from api.sla import SLAManager

    return path, SLAManager(db_path=path)


class TestPipelineRunsOnStrippedTable:
    def test_start_pipeline_run_records_row(self, tmp_path):
        path, m = _stripped_pipeline_db(tmp_path)
        rid = m.start_pipeline_run("pipe_x")
        assert rid
        runs = m.get_pipeline_runs()
        assert runs["total"] == 1
        assert len(runs["runs"]) == 1
        assert runs["runs"][0]["pipeline_name"] == "pipe_x"
        assert runs["runs"][0]["status"] == "running"

    def test_start_pipeline_run_same_run_id_is_idempotent(self, tmp_path):
        path, m = _stripped_pipeline_db(tmp_path)
        rid = m.start_pipeline_run("pipe_y", run_id="fixed-run")
        again = m.start_pipeline_run("pipe_y", run_id="fixed-run")
        assert rid == again == "fixed-run"
        runs = m.get_pipeline_runs()
        assert runs["total"] == 1
        m.finish_pipeline_run("fixed-run", status="success", rows_processed=7)
        run = m.get_pipeline_runs()["runs"][0]
        assert run["status"] == "success"
        assert run["rows_processed"] == 7

    def test_get_pipeline_runs_returns_real_column_names(self, tmp_path):
        path, m = _stripped_pipeline_db(tmp_path)
        m.start_pipeline_run("pipe_z", run_id="col-check")
        run = m.get_pipeline_runs()["runs"][0]
        assert set(run) == PIPELINE_COLUMNS
        assert run["id"] == "col-check"
        filtered = m.get_pipeline_runs(pipeline_name="pipe_z")
        assert filtered["total"] == 1
        assert set(filtered["runs"][0]) == PIPELINE_COLUMNS


class TestPremiumSettingsOnStrippedTable:
    def test_set_setting_does_not_duplicate_key(self, tmp_path):
        from api.premium import _set_setting

        path = str(tmp_path / "premium_stripped.duckdb")
        con = duckdb.connect(path)
        try:
            con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
            con.execute(
                "CREATE TABLE analytics.premium_settings (key VARCHAR, value VARCHAR)"
            )
            _set_setting(con, "license_key", "KEY-A")
            _set_setting(con, "license_key", "KEY-B")
            rows = con.execute(
                "SELECT value FROM analytics.premium_settings WHERE key = 'license_key'"
            ).fetchall()
        finally:
            con.close()
        assert rows == [("KEY-B",)]
