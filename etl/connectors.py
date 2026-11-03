"""
ETL Connectors Module
Database connectors for external data sources (SQLite, PostgreSQL, MySQL)
"""
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
import pandas as pd
import sqlalchemy as sa

logger = logging.getLogger(__name__)


@dataclass
class DatabaseConnection:
    """Configuration for a database connection.

    Never store passwords persistently -- accept them only at runtime.
    """
    db_type: str  # sqlite, postgresql, mysql
    host: Optional[str] = None
    port: Optional[int] = None
    database: str = ""
    username: Optional[str] = None
    password: Optional[str] = None  # never stored, passed at runtime
    connection_string: Optional[str] = None  # alternative to individual params


class DatabaseConnector:
    """Connect to external databases and read tables."""

    def __init__(self, connection: DatabaseConnection):
        self.connection = connection
        self._engine: Optional[sa.engine.Engine] = None

    def _build_connection_string(self) -> str:
        """Build a SQLAlchemy connection string from the DatabaseConnection fields."""
        if self.connection.connection_string:
            return self.connection.connection_string
        if self.connection.db_type == "sqlite":
            return f"sqlite:///{self.connection.database}"
        elif self.connection.db_type == "postgresql":
            return (
                f"postgresql://{self.connection.username}:{self.connection.password}"
                f"@{self.connection.host}:{self.connection.port or 5432}"
                f"/{self.connection.database}"
            )
        elif self.connection.db_type == "mysql":
            return (
                f"mysql+pymysql://{self.connection.username}:{self.connection.password}"
                f"@{self.connection.host}:{self.connection.port or 3306}"
                f"/{self.connection.database}"
            )
        raise ValueError(f"Unsupported database type: {self.connection.db_type}")

    def connect(self) -> bool:
        """Test the database connection. Returns True on success."""
        try:
            conn_str = self._build_connection_string()
            self._engine = sa.create_engine(conn_str, pool_pre_ping=True)
            with self._engine.connect() as conn:
                conn.execute(sa.text("SELECT 1"))
            return True
        except Exception as e:
            logger.error("Connection failed: %s", e)
            return False

    def close(self) -> None:
        """Dispose of the underlying engine and release connections."""
        if self._engine:
            self._engine.dispose()
            self._engine = None

    def list_tables(self) -> List[str]:
        """Return a list of table names in the connected database."""
        if not self._engine:
            return []
        try:
            inspector = sa.inspect(self._engine)
            return inspector.get_table_names()
        except Exception as e:
            logger.error("Failed to list tables: %s", e)
            return []

    def _quote_identifier(self, name: str) -> str:
        """Quote a table/schema name with the engine's own dialect rules."""
        return self._engine.dialect.identifier_preparer.quote(name)

    def read_table(
        self,
        table_name: str,
        schema: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        """Read an entire table (or up to *limit* rows) into a DataFrame."""
        if not self._engine:
            return pd.DataFrame()
        try:
            # pandas 3 no longer accepts an Engine for read_sql_table; a
            # Connection keeps the same transactional/lifecycle behaviour.
            with self._engine.connect() as conn:
                if limit:
                    qualified = self._quote_identifier(table_name)
                    if schema:
                        qualified = (
                            f"{self._quote_identifier(schema)}.{qualified}"
                        )
                    query = sa.text(
                        f"SELECT * FROM {qualified} LIMIT :limit"
                    )
                    return pd.read_sql(query, conn, params={"limit": int(limit)})
                return pd.read_sql_table(table_name, conn, schema=schema)
        except Exception as e:
            logger.error("Failed to read table %s: %s", table_name, e)
            return pd.DataFrame()

    def execute_query(self, query: str) -> pd.DataFrame:
        """Execute an arbitrary SQL query and return results as a DataFrame."""
        if not self._engine:
            return pd.DataFrame()
        try:
            return pd.read_sql(query, self._engine)
        except Exception as e:
            logger.error("Query failed: %s", e)
            return pd.DataFrame()

    def get_schema(self, table_name: str) -> Dict[str, Any]:
        """Return column metadata for the given table."""
        if not self._engine:
            return {}
        try:
            inspector = sa.inspect(self._engine)
            columns = inspector.get_columns(table_name)
            return {
                col["name"]: {
                    "type": str(col["type"]),
                    "nullable": col.get("nullable", True),
                }
                for col in columns
            }
        except Exception as e:
            logger.error("Failed to get schema for %s: %s", table_name, e)
            return {}
