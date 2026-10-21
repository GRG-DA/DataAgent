"""
Warehouse Queries Module
Common analytical queries and KPI computations
"""

import logging
from typing import Dict, List, Optional, Any, Union
from pathlib import Path
import duckdb
import pandas as pd
from contextlib import contextmanager
from api.tenancy import tenant_search_path

logger = logging.getLogger(__name__)


class QueryLibrary:
    """Library of common analytical queries for the warehouse."""

    def __init__(
        self,
        db_path: Union[str, Path] = "data/curated/warehouse.duckdb",
        schema: str = "analytics",
    ):
        self.db_path = Path(db_path)
        self.schema = schema
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def get_connection(self):
        """Get a DuckDB connection."""
        conn = duckdb.connect(str(self.db_path))
        try:
            conn.execute(f"SET search_path = '{tenant_search_path()}'")
            yield conn
        finally:
            conn.close()

    def execute(self, sql: str, params: Optional[List] = None) -> pd.DataFrame:
        """Execute query and return DataFrame."""
        with self.get_connection() as conn:
            if params:
                return conn.execute(sql, params).df()
            return conn.execute(sql).df()

    # ============================================================
    # Sales Analysis Queries
    # ============================================================

    def monthly_revenue(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        group_by: Optional[str] = None,
    ) -> pd.DataFrame:
        """Get monthly revenue trend."""
        where_clauses = []
        params = []

        if start_date:
            where_clauses.append("order_date >= ?::DATE")
            params.append(start_date)
        if end_date:
            where_clauses.append("order_date <= ?::DATE")
            params.append(end_date)

        where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

        group_field = group_by or "DATE_TRUNC('month', order_date)"
        select_group = group_field if group_by else "DATE_TRUNC('month', order_date) as month"

        sql = f"""
        SELECT
            {select_group},
            SUM(total_amount) as revenue,
            COUNT(DISTINCT order_id) as orders,
            COUNT(*) as line_items,
            AVG(total_amount) as avg_order_value
        FROM {self.schema}.sales
        {where_sql}
        GROUP BY {group_field}
        ORDER BY {group_field}
        """
        return self.execute(sql, params if params else None)

    def revenue_by_dimension(
        self,
        dimension: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        top_n: int = 20,
    ) -> pd.DataFrame:
        """Revenue grouped by a dimension (region, channel, category, etc.)."""
        valid_dimensions = ["region", "channel", "customer_id", "product_id"]
        if dimension not in valid_dimensions:
            # Try to use it anyway, might be a custom column
            pass

        where_clauses = []
        params = []
        if start_date:
            where_clauses.append("order_date >= ?::DATE")
            params.append(start_date)
        if end_date:
            where_clauses.append("order_date <= ?::DATE")
            params.append(end_date)

        where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

        sql = f"""
        SELECT
            {dimension},
            SUM(total_amount) as revenue,
            COUNT(DISTINCT order_id) as orders,
            COUNT(*) as line_items,
            SUM(quantity) as total_quantity
        FROM {self.schema}.sales
        {where_sql}
        GROUP BY {dimension}
        ORDER BY revenue DESC
        LIMIT {top_n}
        """
        return self.execute(sql, params if params else None)

    def top_products(
        self,
        metric: str = "revenue",
        n: int = 10,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """Top products by revenue, quantity, or orders."""
        metrics = {
            "revenue": "SUM(s.total_amount)",
            "quantity": "SUM(s.quantity)",
            "orders": "COUNT(DISTINCT s.order_id)",
        }
        metric_sql = metrics.get(metric, metrics["revenue"])

        where_clauses = []
        params = []
        if start_date:
            where_clauses.append("s.order_date >= ?::DATE")
            params.append(start_date)
        if end_date:
            where_clauses.append("s.order_date <= ?::DATE")
            params.append(end_date)

        where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

        sql = f"""
        SELECT
            s.product_id,
            p.product_name,
            p.category,
            {metric_sql} as {metric}
        FROM {self.schema}.sales s
        LEFT JOIN {self.schema}.products p ON s.product_id = p.product_id
        {where_sql}
        GROUP BY s.product_id, p.product_name, p.category
        ORDER BY {metric} DESC
        LIMIT {n}
        """
        return self.execute(sql, params if params else None)

    def customer_lifetime_value(
        self,
        min_orders: int = 1,
    ) -> pd.DataFrame:
        """Calculate customer lifetime value metrics."""
        sql = f"""
        WITH customer_stats AS (
            SELECT
                s.customer_id,
                c.customer_name,
                c.segment,
                COUNT(DISTINCT s.order_id) as order_count,
                SUM(s.total_amount) as total_revenue,
                AVG(s.total_amount) as avg_order_value,
                MIN(s.order_date) as first_order,
                MAX(s.order_date) as last_order,
                DATE_DIFF('day', MIN(s.order_date), MAX(s.order_date)) as customer_lifespan_days
            FROM {self.schema}.sales s
            LEFT JOIN {self.schema}.customers c ON s.customer_id = c.customer_id
            GROUP BY s.customer_id, c.customer_name, c.segment
            HAVING COUNT(DISTINCT s.order_id) >= {min_orders}
        )
        SELECT
            *,
            total_revenue / NULLIF(order_count, 0) as avg_order_value_calc,
            CASE
                WHEN customer_lifespan_days > 0
                THEN total_revenue / (customer_lifespan_days / 365.0)
                ELSE total_revenue
            END as annual_revenue_rate
        FROM customer_stats
        ORDER BY total_revenue DESC
        """
        return self.execute(sql)

    def cohort_analysis(
        self,
        cohort_period: str = "month",
    ) -> pd.DataFrame:
        """Customer cohort retention analysis."""
        sql = f"""
        WITH first_orders AS (
            SELECT
                customer_id,
                DATE_TRUNC('{cohort_period}', MIN(order_date)) as cohort_date
            FROM {self.schema}.sales
            GROUP BY customer_id
        ),
        customer_orders AS (
            SELECT
                s.customer_id,
                fo.cohort_date,
                DATE_TRUNC('{cohort_period}', s.order_date) as order_period,
                DATE_DIFF('{cohort_period}', fo.cohort_date, DATE_TRUNC('{cohort_period}', s.order_date)) as period_number
            FROM {self.schema}.sales s
            JOIN first_orders fo ON s.customer_id = fo.customer_id
        ),
        cohort_sizes AS (
            SELECT cohort_date, COUNT(DISTINCT customer_id) as cohort_size
            FROM first_orders
            GROUP BY cohort_date
        ),
        retention AS (
            SELECT
                cohort_date,
                period_number,
                COUNT(DISTINCT customer_id) as active_customers
            FROM customer_orders
            GROUP BY cohort_date, period_number
        )
        SELECT
            r.cohort_date,
            cs.cohort_size,
            r.period_number,
            r.active_customers,
            ROUND(100.0 * r.active_customers / cs.cohort_size, 1) as retention_rate
        FROM retention r
        JOIN cohort_sizes cs ON r.cohort_date = cs.cohort_date
        ORDER BY r.cohort_date, r.period_number
        """
        return self.execute(sql)

    # ============================================================
    # KPI Queries
    # ============================================================

    def kpi_summary(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """Overall KPI summary."""
        where_clauses = []
        params = []
        if start_date:
            where_clauses.append("order_date >= ?::DATE")
            params.append(start_date)
        if end_date:
            where_clauses.append("order_date <= ?::DATE")
            params.append(end_date)

        where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

        sql = f"""
        SELECT
            'Total Revenue' as metric,
            SUM(total_amount) as value,
            'currency' as format
        FROM {self.schema}.sales
        {where_sql}

        UNION ALL

        SELECT
            'Total Orders' as metric,
            COUNT(DISTINCT order_id) as value,
            'integer' as format
        FROM {self.schema}.sales
        {where_sql}

        UNION ALL

        SELECT
            'Avg Order Value' as metric,
            AVG(total_amount) as value,
            'currency' as format
        FROM {self.schema}.sales
        {where_sql}

        UNION ALL

        SELECT
            'Total Customers' as metric,
            COUNT(DISTINCT customer_id) as value,
            'integer' as format
        FROM {self.schema}.sales
        {where_sql}

        UNION ALL

        SELECT
            'Avg Items per Order' as metric,
            AVG(quantity) as value,
            'decimal' as format
        FROM {self.schema}.sales
        {where_sql}
        """
        # {where_sql} is interpolated once per UNION ALL branch: repeat the
        # params to match every placeholder (Binder: N params, M given).
        n_branches = sql.count(where_sql) if where_sql else 0
        return self.execute(sql, (params * n_branches) if params else None)

    def growth_metrics(
        self,
        period: str = "month",
        periods_back: int = 12,
    ) -> pd.DataFrame:
        """Calculate period-over-period growth metrics."""
        sql = f"""
        WITH period_data AS (
            SELECT
                DATE_TRUNC('{period}', order_date) as period,
                SUM(total_amount) as revenue,
                COUNT(DISTINCT order_id) as orders,
                COUNT(DISTINCT customer_id) as customers
            FROM {self.schema}.sales
            GROUP BY DATE_TRUNC('{period}', order_date)
        ),
        ranked AS (
            SELECT
                *,
                ROW_NUMBER() OVER (ORDER BY period DESC) as rn
            FROM period_data
        ),
        filtered AS (
            SELECT * FROM ranked WHERE rn <= {periods_back}
        )
        SELECT
            period,
            revenue,
            orders,
            customers,
            LAG(revenue) OVER (ORDER BY period) as prev_revenue,
            LAG(orders) OVER (ORDER BY period) as prev_orders,
            LAG(customers) OVER (ORDER BY period) as prev_customers,
            CASE
                WHEN LAG(revenue) OVER (ORDER BY period) > 0
                THEN ROUND(100.0 * (revenue - LAG(revenue) OVER (ORDER BY period)) / LAG(revenue) OVER (ORDER BY period), 2)
                ELSE NULL
            END as revenue_growth_pct,
            CASE
                WHEN LAG(orders) OVER (ORDER BY period) > 0
                THEN ROUND(100.0 * (orders - LAG(orders) OVER (ORDER BY period)) / LAG(orders) OVER (ORDER BY period), 2)
                ELSE NULL
            END as orders_growth_pct
        FROM filtered
        ORDER BY period
        """
        return self.execute(sql)

    # ============================================================
    # Data Quality Queries
    # ============================================================

    def data_quality_report(self, table_name: str) -> pd.DataFrame:
        """Generate data quality report for a table (missing table -> empty)."""
        with self.get_connection() as conn:
            try:
                cols = conn.execute(f"DESCRIBE {self.schema}.{table_name}").df()
            except Exception as e:
                logger.warning(f"Quality report skipped, table missing: {table_name} ({e})")
                return pd.DataFrame()

            results = []
            for _, row in cols.iterrows():
                col = row['column_name']
                dtype = row['column_type']
                q = f"""
                SELECT
                    '{col}' as column_name,
                    '{dtype}' as data_type,
                    COUNT(*) as total_rows,
                    COUNT("{col}") as non_null_count,
                    COUNT(*) - COUNT("{col}") as null_count,
                    ROUND(100.0 * (COUNT(*) - COUNT("{col}")) / COUNT(*), 2) as null_pct,
                    COUNT(DISTINCT "{col}") as distinct_count
                FROM {self.schema}.{table_name}
                """
                try:
                    res = conn.execute(q).df()
                    results.append(res)
                except Exception as e:
                    logger.warning(f"Quality check failed for {col}: {e}")

        if results:
            return pd.concat(results, ignore_index=True)
        return pd.DataFrame()

    def find_duplicates(self, table_name: str, key_columns: List[str]) -> pd.DataFrame:
        """Find duplicate rows based on key columns."""
        key_str = ", ".join(key_columns)
        sql = f"""
        SELECT
            {key_str},
            COUNT(*) as duplicate_count
        FROM {self.schema}.{table_name}
        GROUP BY {key_str}
        HAVING COUNT(*) > 1
        ORDER BY duplicate_count DESC
        """
        return self.execute(sql)

    def outliers_iqr(self, table_name: str, numeric_column: str) -> pd.DataFrame:
        """Find outliers using IQR method."""
        sql = f"""
        WITH stats AS (
            SELECT
                PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY {numeric_column}) as q1,
                PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY {numeric_column}) as q3
            FROM {self.schema}.{table_name}
            WHERE {numeric_column} IS NOT NULL
        ),
        bounds AS (
            SELECT
                q1,
                q3,
                q3 - q1 as iqr,
                q1 - 1.5 * (q3 - q1) as lower_bound,
                q3 + 1.5 * (q3 - q1) as upper_bound
            FROM stats
        )
        SELECT t.*
        FROM {self.schema}.{table_name} t
        CROSS JOIN bounds b
        WHERE t.{numeric_column} < b.lower_bound
           OR t.{numeric_column} > b.upper_bound
        """
        return self.execute(sql)


# Convenience function
def get_query_library(
    db_path: Union[str, Path] = "data/curated/warehouse.duckdb",
    schema: str = "analytics",
) -> QueryLibrary:
    """Get a QueryLibrary instance."""
    return QueryLibrary(db_path, schema)