"""
ETL Load Module
Load cleaned DataFrames into DuckDB warehouse
"""

import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
import pandas as pd
import duckdb
from contextlib import contextmanager

from .pandas_compat import register_dataframe

logger = logging.getLogger(__name__)


class WarehouseLockedError(Exception):
    """The warehouse file is locked by another process (DuckDB single-writer rule)."""


# Process-wide write serialization: DuckDB allows a single writer per database
# file. Readers stay lock-free; every mutating method below takes this lock so
# concurrent in-process writes queue instead of colliding. Cross-process
# collisions (a second `python run.py`, Windows "file in use") cannot be
# locked away — they surface as WarehouseLockedError with a human message.
_warehouse_write_lock = threading.RLock()

# Substrings of duckdb.IOException messages that mean "another process (or an
# incompatible connection) holds the file", not a bug in the query.
_LOCK_CONFLICT_MARKERS = (
    "Cannot open file",
    "used by another",
    "Could not set lock",
    "Could not acquire lock",
    "different configuration",
    "file in use",
    "being used by another process",
)


def _friendly_lock_error(db_path: object, err: Exception) -> WarehouseLockedError:
    return WarehouseLockedError(
        "Could not write to the warehouse — the database file is locked by "
        f"another process (DuckDB allows one writer per file): {db_path}. "
        "Close the other DataAgent process (or stop the server) and try again. "
        f"Last error: {err}"
    )


def _is_lock_conflict(err: Exception) -> bool:
    msg = str(err)
    return any(marker in msg for marker in _LOCK_CONFLICT_MARKERS)


def _qi(identifier: object) -> str:
    """Quote a SQL identifier (table/column/index names are interpolated).

    Upload filenames and request bodies flow into these names — embedded
    quotes must be doubled so they cannot break out of the identifier.
    """
    return '"' + str(identifier).replace('"', '""') + '"'


class WarehouseLoader:
    """Load DataFrames into DuckDB warehouse with schema management."""

    def __init__(
        self,
        db_path: Union[str, Path] = "data/curated/warehouse.duckdb",
        schema: str = "analytics",
        config: Optional[Dict[str, Any]] = None,
    ):
        self.db_path = Path(db_path)
        self.schema = schema
        self.config = config or {}
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Ensure database exists
        self._init_database()

    def _init_database(self):
        """Initialize database and schema."""
        with _warehouse_write_lock, self.get_connection() as conn:
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
            logger.info(f"Database initialized at {self.db_path}, schema: {self.schema}")

    @contextmanager
    def get_connection(self, tenant_id: Optional[str] = None):
        """Get a DuckDB connection scoped to the caller's tenant.

        The tenant's own schema is put first on the search path, so an
        unqualified table name resolves to that tenant's copy and nothing
        else (ADR-009 D1).  ``search_path`` is a *per-connection* DuckDB
        setting, so two tenants in the same process never interfere.

        A connection whose search path was never set has no schema in scope,
        so unqualified names fail — the default is fail-closed.

        With the opt-in per-tenant database mode on, the connection opens
        the *tenant's own file* (``data/tenants/<slug>/warehouse.duckdb``)
        instead of the shared warehouse, so even a hostile DuckDB client
        that opens the file directly cannot reach another tenant's data.
        The platform schema stays in the shared file.
        """
        from api.tenancy import (
            apply_tenant_search_path,
            current_tenant_id,
            tenant_database_path,
            tenant_owns_own_file,
        )

        tid = tenant_id if tenant_id is not None else current_tenant_id()
        db_path = Path(tenant_database_path(tid, shared_path=str(self.db_path)))
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = duckdb.connect(str(db_path))
        except duckdb.Error as e:
            if _is_lock_conflict(e):
                raise _friendly_lock_error(db_path, e) from e
            raise
        try:
            # Apply configuration
            threads = self.config.get("threads", 4)
            memory_limit = self.config.get("memory_limit", "2GB")
            conn.execute(f"PRAGMA threads={threads}")
            conn.execute(f"PRAGMA memory_limit='{memory_limit}'")
            # Create the schemas first, then point the session at the
            # tenant's.  `SET search_path` fails when the named schema does
            # not exist yet, so both must be ensured up front.
            schema = self.schema_for(tid)
            if tenant_owns_own_file(tid):
                # The tenant's file holds only the tenant's schema; the
                # platform schema stays in the shared warehouse.
                conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            else:
                conn.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
                if schema != self.schema:
                    conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            apply_tenant_search_path(conn, tid)
            yield conn
        finally:
            conn.close()

    def schema_for(self, tenant_id: Optional[str] = None) -> str:
        """Return the schema that owns *tenant_id*'s data tables."""
        from api.tenancy import tenant_schema

        return tenant_schema(tenant_id)

    def load_dataframe(
        self,
        df: pd.DataFrame,
        table_name: str,
        if_exists: str = "replace",
        primary_key: Optional[str] = None,
        indexes: Optional[List[str]] = None,
        tenant_id: Optional[str] = None,
    ) -> int:
        """
        Load a DataFrame into DuckDB.

        Args:
            df: DataFrame to load
            table_name: Target table name
            if_exists: 'replace', 'append', or 'fail'
            primary_key: Column to use as primary key
            indexes: List of columns to index
            tenant_id: Owning tenant; the table is created in that tenant's
                schema, so two tenants can each own a table of the same name.

        Returns:
            Number of rows loaded
        """
        if df.empty:
            msg = f"Cannot load empty DataFrame into {table_name}"
            logger.error(msg)
            raise ValueError(msg)
        if if_exists not in ("replace", "append", "fail"):
            raise ValueError(f"Invalid if_exists: {if_exists!r}")
        from api.tenancy import current_tenant_id

        tid = tenant_id if tenant_id is not None else current_tenant_id()
        target_schema = self.schema_for(tid)
        qt = f"{target_schema}.{_qi(table_name)}"

        # Normalize pandas 3.0 string dtype (StringDtype) to object so DuckDB's
        # pandas integration can map the column types ("Data type 'str' not
        # recognized" otherwise).
        if hasattr(pd, "StringDtype"):
            str_cols = [c for c in df.columns if isinstance(df[c].dtype, pd.StringDtype)]
            if str_cols:
                df = df.copy()
                for c in str_cols:
                    df[c] = df[c].astype(object)

        with _warehouse_write_lock, self.get_connection(tid) as conn:
            # Handle if_exists
            if if_exists == "replace":
                conn.execute(f"DROP TABLE IF EXISTS {qt}")
            elif if_exists == "fail":
                exists = conn.execute(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = ? AND table_name = ?",
                    [target_schema, table_name],
                ).fetchone()
                if exists:
                    raise ValueError(f"Table {table_name} already exists")

            # Create table from DataFrame
            register_dataframe(conn, "temp_df", df)
            conn.execute(f"CREATE TABLE {qt} AS SELECT * FROM temp_df")
            conn.unregister("temp_df")

            # Add primary key constraint if specified
            if primary_key and primary_key in df.columns:
                try:
                    conn.execute(
                        f"ALTER TABLE {qt} "
                        f"ADD PRIMARY KEY ({_qi(primary_key)})"
                    )
                except Exception as e:
                    logger.warning(f"Could not add primary key: {e}")

            # Create indexes
            if indexes:
                for idx_col in indexes:
                    if idx_col in df.columns:
                        try:
                            conn.execute(
                                f"CREATE INDEX IF NOT EXISTS {_qi(f'idx_{table_name}_{idx_col}')} "
                                f"ON {qt}({_qi(idx_col)})"
                            )
                        except Exception as e:
                            logger.warning(f"Could not create index on {idx_col}: {e}")

            # Get row count
            count = conn.execute(
                f"SELECT COUNT(*) FROM {qt}"
            ).fetchone()[0]

        logger.info(f"Loaded {count} rows into {target_schema}.{table_name}")
        return count

    def load_multiple(
        self,
        dataframes: Dict[str, pd.DataFrame],
        table_mapping: Optional[Dict[str, str]] = None,
        if_exists: str = "replace",
    ) -> Dict[str, int]:
        """
        Load multiple DataFrames.

        Args:
            dataframes: Dict of name -> DataFrame
            table_mapping: Optional mapping from name to table_name
            if_exists: How to handle existing tables

        Returns:
            Dict of table_name -> row count
        """
        results = {}
        for name, df in dataframes.items():
            table_name = table_mapping.get(name, name) if table_mapping else name
            results[table_name] = self.load_dataframe(df, table_name, if_exists)
        return results

    def execute_sql(self, sql: str, params: Optional[List] = None,
                    read_only: bool = False,
                    tenant_id: Optional[str] = None) -> pd.DataFrame:
        """Execute arbitrary SQL and return as DataFrame.

        ETL callers use the default (writes persist). API routes executing
        user-supplied SQL pass ``read_only=True`` so the statement runs inside
        a rolled-back transaction: even a write that slips past validation
        can never persist (defense in depth).

        The statement runs on the caller's tenant search path, so it cannot
        reach another tenant's tables.
        """
        from api.tenancy import current_tenant_id

        tid = tenant_id if tenant_id is not None else current_tenant_id()
        if not read_only:
            with self.get_connection(tid) as conn:
                if params:
                    return conn.execute(sql, params).df()
                return conn.execute(sql).df()

        from etl.readonly import readonly_transaction

        with self.get_connection(tid) as conn:
            with readonly_transaction(conn):
                if params:
                    return conn.execute(sql, params).df()
                return conn.execute(sql).df()

    def query(self, sql: str, params: Optional[List] = None,
              tenant_id: Optional[str] = None) -> pd.DataFrame:
        """Execute SQL and return as DataFrame (always rolled back)."""
        from api.tenancy import current_tenant_id
        from etl.readonly import readonly_transaction

        tid = tenant_id if tenant_id is not None else current_tenant_id()
        with self.get_connection(tid) as conn:
            with readonly_transaction(conn):
                if params:
                    return conn.execute(sql, params).df()
                return conn.execute(sql).df()

    def get_table_info(self, table_name: str) -> Dict[str, Any]:
        """Get table schema information for the caller's tenant."""
        # Defense in depth: reject unsafe identifiers before interpolation.
        # Routes also call validate_table_name at the API boundary.
        from api.security_paths import validate_table_name
        from api.tenancy import current_tenant_id

        validate_table_name(table_name)
        tid = current_tenant_id()
        schema = self.schema_for(tid)
        with self.get_connection(tid) as conn:
            # Column info
            cols = conn.execute(
                f"DESCRIBE {schema}.{_qi(table_name)}"
            ).df()

            # Row count
            count = conn.execute(
                f"SELECT COUNT(*) FROM {schema}.{_qi(table_name)}"
            ).fetchone()[0]

            # Sample data
            sample = conn.execute(
                f"SELECT * FROM {schema}.{_qi(table_name)} LIMIT 5"
            ).df()

            return {
                "table_name": table_name,
                "schema": schema,
                "columns": cols.to_dict("records"),
                "row_count": count,
                "sample": sample.to_dict("records"),
            }

    def list_tables(self, tenant_id: Optional[str] = None) -> List[str]:
        """List the caller's tenant tables (never another tenant's)."""
        from api.tenancy import current_tenant_id

        tid = tenant_id if tenant_id is not None else current_tenant_id()
        schema = self.schema_for(tid)
        with self.get_connection(tid) as conn:
            result = conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = ? ORDER BY table_name",
                [schema],
            ).fetchall()
            return [r[0] for r in result]

    def drop_table(self, table_name: str, tenant_id: Optional[str] = None) -> bool:
        """Drop a table from the caller's tenant schema."""
        from api.tenancy import current_tenant_id

        tid = tenant_id if tenant_id is not None else current_tenant_id()
        schema = self.schema_for(tid)
        with _warehouse_write_lock, self.get_connection(tid) as conn:
            conn.execute(f"DROP TABLE IF EXISTS {schema}.{_qi(table_name)}")
        logger.info(f"Dropped table {schema}.{table_name}")
        return True

    def table_exists(self, table_name: str, tenant_id: Optional[str] = None) -> bool:
        """Check if a table exists in the caller's tenant schema."""
        from api.tenancy import current_tenant_id

        tid = tenant_id if tenant_id is not None else current_tenant_id()
        schema = self.schema_for(tid)
        with self.get_connection(tid) as conn:
            result = conn.execute(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = ? AND table_name = ?)",
                [schema, table_name],
            ).fetchone()[0]
        return bool(result)

    def sample_table(self, table_name: str, n: int = 10,
                     tenant_id: Optional[str] = None) -> pd.DataFrame:
        """Get sample rows from a table in the caller's tenant schema."""
        from api.tenancy import current_tenant_id

        tid = tenant_id if tenant_id is not None else current_tenant_id()
        schema = self.schema_for(tid)
        if not self.table_exists(table_name, tid):
            logger.warning(f"Table {schema}.{table_name} does not exist")
            return pd.DataFrame()

        n = max(1, min(int(n or 10), 1000))
        with self.get_connection(tid) as conn:
            return conn.execute(
                f"SELECT * FROM {schema}.{_qi(table_name)} LIMIT {n}"
            ).df()

    def drop_all_tables(self) -> int:
        """Drop all *user* tables in the schema. Returns count of dropped tables.

        Platform-owned tables (billing, notifications, dashboards, chat, org,
        catalog, ...) are preserved — see ``warehouse.system_tables``. Dropping
        them would break the running app (e.g. the notification poller and the
        billing/subscription stack) until the next restart.
        """
        from warehouse.system_tables import is_system_table
        tables = self.list_tables()
        count = 0
        with _warehouse_write_lock:
            for table_name in tables:
                if is_system_table(table_name):
                    continue
                try:
                    with self.get_connection() as conn:
                        conn.execute(f"DROP TABLE IF EXISTS {self.schema}.{_qi(table_name)}")
                    count += 1
                except Exception as e:
                    logger.warning(f"Failed to drop {table_name}: {e}")
        logger.info(f"Dropped {count} user tables from {self.schema}")
        return count

    def create_view(self, view_name: str, sql: str) -> bool:
        """Create a view."""
        with _warehouse_write_lock, self.get_connection() as conn:
            conn.execute(f"CREATE OR REPLACE VIEW {self.schema}.{_qi(view_name)} AS {sql}")
        logger.info(f"Created view {self.schema}.{view_name}")
        return True

    def vacuum(self):
        """Vacuum the database to reclaim space."""
        with _warehouse_write_lock, self.get_connection() as conn:
            conn.execute("VACUUM")
        logger.info("Database vacuumed")


def load_to_warehouse(
    df: pd.DataFrame,
    table_name: str,
    db_path: Union[str, Path] = "data/curated/warehouse.duckdb",
    schema: str = "analytics",
    **kwargs,
) -> int:
    """Convenience function to load a DataFrame to warehouse."""
    loader = WarehouseLoader(db_path, schema)
    return loader.load_dataframe(df, table_name, **kwargs)