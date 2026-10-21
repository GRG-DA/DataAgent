"""
Warehouse Schema Module
Manage DuckDB schema, tables, and metadata
"""

import logging
from typing import Dict, List, Optional, Any, Union
from pathlib import Path
import duckdb
import pandas as pd
from dataclasses import dataclass, field
from contextlib import contextmanager
from api.tenancy import tenant_search_path

logger = logging.getLogger(__name__)


@dataclass
class ColumnDef:
    """Column definition for schema."""
    name: str
    dtype: str
    nullable: bool = True
    primary_key: bool = False
    default: Optional[str] = None
    description: str = ""


@dataclass
class TableSchema:
    """Table schema definition."""
    name: str
    columns: List[ColumnDef]
    indexes: List[str] = field(default_factory=list)
    description: str = ""


class SchemaManager:
    """Manage warehouse schema definitions and migrations."""

    # Default analytical schema
    DEFAULT_TABLES = {
        "sales": TableSchema(
            name="sales",
            description="Sales transaction fact table",
            columns=[
                ColumnDef("order_id", "VARCHAR", nullable=False, primary_key=True, description="Unique order identifier"),
                ColumnDef("order_date", "DATE", nullable=False, description="Order date"),
                ColumnDef("customer_id", "VARCHAR", nullable=False, description="Customer foreign key"),
                ColumnDef("product_id", "VARCHAR", nullable=False, description="Product foreign key"),
                ColumnDef("quantity", "INTEGER", nullable=False, default="1", description="Quantity sold"),
                ColumnDef("unit_price", "DECIMAL(10,2)", nullable=False, description="Unit price"),
                ColumnDef("discount", "DECIMAL(5,2)", default="0", description="Discount percentage"),
                ColumnDef("total_amount", "DECIMAL(12,2)", nullable=False, description="Line total"),
                ColumnDef("region", "VARCHAR", description="Sales region"),
                ColumnDef("channel", "VARCHAR", description="Sales channel"),
                ColumnDef("created_at", "TIMESTAMP", default="CURRENT_TIMESTAMP", description="Record creation time"),
            ],
            indexes=["customer_id", "product_id", "order_date", "region"],
        ),
        "customers": TableSchema(
            name="customers",
            description="Customer dimension table",
            columns=[
                ColumnDef("customer_id", "VARCHAR", nullable=False, primary_key=True, description="Unique customer identifier"),
                ColumnDef("customer_name", "VARCHAR", nullable=False, description="Customer name"),
                ColumnDef("email", "VARCHAR", description="Email address"),
                ColumnDef("phone", "VARCHAR", description="Phone number"),
                ColumnDef("address", "VARCHAR", description="Address"),
                ColumnDef("city", "VARCHAR", description="City"),
                ColumnDef("state", "VARCHAR", description="State/Province"),
                ColumnDef("country", "VARCHAR", description="Country"),
                ColumnDef("postal_code", "VARCHAR", description="Postal code"),
                ColumnDef("segment", "VARCHAR", description="Customer segment"),
                ColumnDef("registration_date", "DATE", description="Registration date"),
                ColumnDef("is_active", "BOOLEAN", default="TRUE", description="Active status"),
            ],
            indexes=["segment", "city", "country"],
        ),
        "products": TableSchema(
            name="products",
            description="Product dimension table",
            columns=[
                ColumnDef("product_id", "VARCHAR", nullable=False, primary_key=True, description="Unique product identifier"),
                ColumnDef("product_name", "VARCHAR", nullable=False, description="Product name"),
                ColumnDef("category", "VARCHAR", description="Product category"),
                ColumnDef("subcategory", "VARCHAR", description="Product subcategory"),
                ColumnDef("brand", "VARCHAR", description="Brand"),
                ColumnDef("unit_cost", "DECIMAL(10,2)", description="Unit cost"),
                ColumnDef("unit_price", "DECIMAL(10,2)", description="List price"),
                ColumnDef("currency", "VARCHAR", default="'USD'", description="Currency"),
                ColumnDef("is_active", "BOOLEAN", default="TRUE", description="Active status"),
            ],
            indexes=["category", "brand"],
        ),
        "orders": TableSchema(
            name="orders",
            description="Order header fact table",
            columns=[
                ColumnDef("order_id", "VARCHAR", nullable=False, primary_key=True, description="Unique order identifier"),
                ColumnDef("order_date", "DATE", nullable=False, description="Order date"),
                ColumnDef("customer_id", "VARCHAR", nullable=False, description="Customer foreign key"),
                ColumnDef("status", "VARCHAR", description="Order status"),
                ColumnDef("total_amount", "DECIMAL(12,2)", description="Order total"),
                ColumnDef("shipping_cost", "DECIMAL(10,2)", default="0", description="Shipping cost"),
                ColumnDef("tax_amount", "DECIMAL(10,2)", default="0", description="Tax amount"),
                ColumnDef("payment_method", "VARCHAR", description="Payment method"),
                ColumnDef("shipping_address", "VARCHAR", description="Shipping address"),
            ],
            indexes=["customer_id", "order_date", "status"],
        ),
    }

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
        self._tables: Dict[str, TableSchema] = {}
        # Default tables are available but NOT auto-loaded
        # Users upload their own data

    def _load_default_tables(self):
        """Load default table definitions."""
        self._tables = self.DEFAULT_TABLES.copy()

    def register_table(self, table: TableSchema):
        """Register a custom table schema."""
        self._tables[table.name] = table

    def get_table(self, name: str) -> Optional[TableSchema]:
        """Get table schema by name."""
        return self._tables.get(name)

    def list_tables(self) -> List[str]:
        """List all registered table names."""
        return list(self._tables.keys())

    @contextmanager
    def get_connection(self):
        """Get a DuckDB connection."""
        conn = duckdb.connect(str(self.db_path))
        try:
            threads = self.config.get("threads", 4)
            memory_limit = self.config.get("memory_limit", "2GB")
            conn.execute(f"PRAGMA threads={threads}")
            conn.execute(f"PRAGMA memory_limit='{memory_limit}'")
            # Create schema if not exists before setting search_path
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
            conn.execute(f"SET search_path = '{tenant_search_path()}'")
            yield conn
        finally:
            conn.close()

    def create_schema(self):
        """Create the schema and all registered tables."""
        with self.get_connection() as conn:
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
            for table in self._tables.values():
                self._create_table(conn, table)
            logger.info(f"Created schema {self.schema} with {len(self._tables)} tables")

    def _create_table(self, conn: duckdb.DuckDBPyConnection, table: TableSchema):
        """Create a single table from schema."""
        # Build column definitions
        col_defs = []
        for col in table.columns:
            parts = [col.name, col.dtype]
            if not col.nullable:
                parts.append("NOT NULL")
            if col.default:
                parts.append(f"DEFAULT {col.default}")
            col_defs.append(" ".join(parts))

        # Primary key
        pk_cols = [c.name for c in table.columns if c.primary_key]
        if pk_cols:
            col_defs.append(f"PRIMARY KEY ({', '.join(pk_cols)})")

        sql = f"""
        CREATE TABLE IF NOT EXISTS {self.schema}.{table.name} (
            {', '.join(col_defs)}
        )
        """
        conn.execute(sql)

        # Create indexes
        for idx_col in table.indexes:
            if any(c.name == idx_col for c in table.columns):
                try:
                    conn.execute(
                        f"CREATE INDEX IF NOT EXISTS idx_{table.name}_{idx_col} "
                        f"ON {self.schema}.{table.name}({idx_col})"
                    )
                except Exception as e:
                    logger.warning(f"Could not create index on {table.name}.{idx_col}: {e}")

    def create_table(self, table_name: str) -> bool:
        """Create a specific table."""
        table = self._tables.get(table_name)
        if not table:
            logger.error(f"Table {table_name} not registered")
            return False

        with self.get_connection() as conn:
            self._create_table(conn, table)
        return True

    def drop_table(self, table_name: str) -> bool:
        """Drop a table."""
        with self.get_connection() as conn:
            conn.execute(f"DROP TABLE IF EXISTS {self.schema}.{table_name}")
        logger.info(f"Dropped table {self.schema}.{table_name}")
        return True

    def table_exists(self, table_name: str) -> bool:
        """Check if table exists in database."""
        with self.get_connection() as conn:
            result = conn.execute(
                f"SELECT 1 FROM information_schema.tables "
                f"WHERE table_schema = '{self.schema}' AND table_name = '{table_name}'"
            ).fetchone()
        return result is not None

    def get_table_info(self, table_name: str) -> Dict[str, Any]:
        """Get detailed table information."""
        with self.get_connection() as conn:
            # Column info
            cols = conn.execute(f"DESCRIBE {self.schema}.{table_name}").df()

            # Row count
            count = conn.execute(
                f"SELECT COUNT(*) FROM {self.schema}.{table_name}"
            ).fetchone()[0]

            # Sample
            sample = conn.execute(
                f"SELECT * FROM {self.schema}.{table_name} LIMIT 3"
            ).df()

            return {
                "name": table_name,
                "schema": self.schema,
                "columns": cols.to_dict("records"),
                "row_count": count,
                "sample": sample.to_dict("records"),
            }

    def infer_schema_from_dataframe(self, df: pd.DataFrame, table_name: str) -> TableSchema:
        """Infer schema from a DataFrame."""
        columns = []
        for col in df.columns:
            dtype = df[col].dtype
            # Map pandas dtypes to DuckDB
            if pd.api.types.is_integer_dtype(dtype):
                db_type = "BIGINT"
            elif pd.api.types.is_float_dtype(dtype):
                db_type = "DOUBLE"
            elif pd.api.types.is_bool_dtype(dtype):
                db_type = "BOOLEAN"
            elif pd.api.types.is_datetime64_any_dtype(dtype):
                db_type = "TIMESTAMP"
            else:
                db_type = "VARCHAR"

            columns.append(ColumnDef(
                name=col,
                dtype=db_type,
                nullable=df[col].isnull().any(),
            ))

        return TableSchema(name=table_name, columns=columns)

    def generate_ddl(self, table_name: str) -> str:
        """Generate DDL for a table."""
        table = self._tables.get(table_name)
        if not table:
            return ""

        col_defs = []
        for col in table.columns:
            parts = [col.name, col.dtype]
            if not col.nullable:
                parts.append("NOT NULL")
            if col.default:
                parts.append(f"DEFAULT {col.default}")
            col_defs.append(" ".join(parts))

        pk_cols = [c.name for c in table.columns if c.primary_key]
        if pk_cols:
            col_defs.append(f"PRIMARY KEY ({', '.join(pk_cols)})")

        sep = ",\n    "
        return f"CREATE TABLE {self.schema}.{table.name} (\n    {sep.join(col_defs)}\n);"

    def get_all_ddl(self) -> str:
        """Get DDL for all tables."""
        ddls = []
        for table in self._tables.values():
            ddls.append(self.generate_ddl(table.name))
        return "\n\n".join(ddls)


# Convenience function
def create_warehouse_schema(
    db_path: Union[str, Path] = "data/curated/warehouse.duckdb",
    schema: str = "analytics",
) -> SchemaManager:
    """Create and initialize warehouse schema."""
    manager = SchemaManager(db_path, schema)
    manager.create_schema()
    return manager