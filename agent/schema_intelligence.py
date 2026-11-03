"""
Schema Intelligence Module
Column classification engine: distinguishes dimensions, measures, and unique identifiers
from database schema context and sample data.

Provides SchemaProfile objects that the planner and orchestrator use to make
column-aware analytical decisions — never grouping by unique IDs.
"""

import re
import logging
from typing import Dict, List, Optional, Set, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SchemaProfile:
    """Profiled schema with classified columns."""
    dimensions: List[str] = field(default_factory=list)
    measures: List[str] = field(default_factory=list)
    identifiers: Set[str] = field(default_factory=set)
    date_cols: List[str] = field(default_factory=list)
    all_columns: Dict[str, str] = field(default_factory=dict)  # name -> type
    table_name: str = ""
    row_count: int = 0
    cardinality_hint: Dict[str, int] = field(default_factory=dict)
    # Enrichment: distinct values for dimensions, min/max ranges for measures
    distinct_values: Dict[str, List[str]] = field(default_factory=dict)  # dimension -> [values]
    measure_ranges: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # measure -> {min, max, avg}

    @property
    def has_dimensions(self) -> bool:
        return len(self.dimensions) > 0

    @property
    def has_measures(self) -> bool:
        return len(self.measures) > 0

    def best_dimension(self, exclude: Optional[Set[str]] = None) -> Optional[str]:
        """Pick the most meaningful dimension for GROUP BY."""
        exclude = exclude or set()
        candidates = [d for d in self.dimensions if d not in exclude]
        if candidates:
            return candidates[0]
        return None

    def best_measure(self) -> Optional[str]:
        """Pick the primary measure (revenue/sales/amount preferred).

        Uses priority ranking from _measure_priority to select the best one.
        Measures are already sorted by priority in classify_schema, so
        measures[0] is the best choice.
        """
        if not self.measures:
            return None
        return self.measures[0]

    def summarize(self) -> str:
        """Human-readable summary of the schema profile."""
        parts = [
            f"Table: {self.table_name} ({self.row_count} rows)",
            f"Dimensions: {', '.join(self.dimensions) if self.dimensions else 'none'}",
            f"Measures: {', '.join(self.measures) if self.measures else 'none'}",
        ]
        if self.identifiers:
            parts.append(f"Identifiers (never group by): {', '.join(sorted(self.identifiers))}")
        if self.date_cols:
            parts.append(f"Date columns: {', '.join(self.date_cols)}")
        return " | ".join(parts)
    
    def enrich_with_values(self, sample_data: List[Dict[str, Any]]) -> None:
        """Enrich the profile with distinct values for dimensions and ranges for measures.
        
        Args:
            sample_data: Sample rows from the table to extract values/ranges.
        """
        if not sample_data:
            return
        
        # Extract distinct values for dimensions (up to 20 values each)
        for dim in self.dimensions:
            values = set()
            for row in sample_data:
                val = row.get(dim)
                if val is not None:
                    values.add(str(val))
                    if len(values) >= 20:
                        break
            if values:
                self.distinct_values[dim] = sorted(values)
        
        # Extract min/max/avg for measures
        for measure in self.measures:
            nums = []
            for row in sample_data:
                val = row.get(measure)
                if val is not None:
                    try:
                        nums.append(float(val))
                    except (ValueError, TypeError):
                        pass
            if nums:
                self.measure_ranges[measure] = {
                    "min": min(nums),
                    "max": max(nums),
                    "avg": sum(nums) / len(nums)
                }


# ── Business-dimension keywords (names that signal a categorical group-by column) ──
DIMENSION_KEYWORDS = [
    "region", "country", "state", "city", "province", "territory",
    "category", "subcategory", "sub_category",
    "segment", "department", "division", "team",
    "channel", "store", "branch", "location", "site",
    "gender", "age_group", "agegroup", "age bracket",
    "product", "product_name", "productname",
    "status", "type", "class", "group", "family",
    "brand", "vendor", "supplier", "manufacturer",
    "payment_method", "paymentmethod", "method",
    "shipping_method", "ship_mode", "shipmode",
    "customer_segment", "customersegment",
    "salesperson", "sales_rep", "salesrep", "representative",
    "education", "occupation", "industry", "sector",
    "marital_status", "maritalstatus",
    "campaign", "source", "medium", "referrer",
    "browser", "device", "platform", "os",
    "quarter", "year", "month_name", "day_name",
    "weekday", "weekend",
    "category_name", "product_line", "productline",
    "order_priority", "priority",
]

# ── Unique identifier keywords (columns that are IDs, never group-by) ──
IDENTIFIER_PATTERNS = [
    r'_id$', r'^id$', r'^pk_', r'_pk$',
    r'^id_', r'id$',
    r'_number$', r'^num_',
    r'_code$', r'^code_',
    r'_key$',
    r'^sk_', r'^dk_',
]

# Specific column names that are identifiers
IDENTIFIER_NAMES = {
    "customer_id", "order_id", "product_id", "transaction_id",
    "employee_id", "user_id", "account_id", "subscription_id",
    "invoice_id", "payment_id", "shipment_id", "vendor_id",
    "supplier_id", "store_id", "category_id", "address_id",
    "session_id", "event_id", "item_id", "sku", "upc",
    "customerid", "orderid", "productid", "transactionid",
    "employeeid", "userid",
}


def classify_schema(
    table_name: str = "",
    columns: Optional[Dict[str, str]] = None,
    sample_data: Optional[List[Dict[str, Any]]] = None,
    row_count: int = 0,
) -> SchemaProfile:
    """
    Classify columns into dimensions, measures, and identifiers.

    Args:
        table_name: Name of the table being profiled.
        columns: Dict mapping column_name -> type_string (e.g., "VARCHAR", "DOUBLE").
        sample_data: Optional sample rows for cardinality estimation.
        row_count: Total row count for cardinality estimation.

    Returns:
        SchemaProfile with classified columns.
    """
    profile = SchemaProfile(
        table_name=table_name,
        row_count=row_count,
        all_columns=dict(columns) if columns else {},
    )

    if not columns:
        return profile

    # Estimate cardinality from sample data
    cardinality = _estimate_cardinality(columns, sample_data, row_count)

    for col_name, col_type in columns.items():
        col_lower = col_name.lower()
        type_upper = col_type.upper()

        # ── Step 1: Detect unique identifiers ──
        if _is_identifier(col_name, col_lower, type_upper, cardinality):
            profile.identifiers.add(col_name)
            continue

        # ── Step 2: Detect date/time columns ──
        if _is_date_column(col_name, col_lower, type_upper):
            profile.date_cols.append(col_name)
            continue

        # ── Step 3: Detect measures (numeric) ──
        if _is_measure_type(type_upper) and not _is_id_column(col_lower):
            profile.measures.append(col_name)
            continue

        # ── Step 4: Detect dimensions (categorical / VARCHAR) ──
        if _is_dimension_type(type_upper):
            # Check if it's a business dimension by name
            if _is_dimension_by_name(col_lower):
                profile.dimensions.append(col_name)
            # Low-cardinality VARCHAR is a dimension candidate
            elif col_name in cardinality and cardinality[col_name] >= 2:
                if sample_data is not None and len(sample_data) > 0:
                    # Only treat as dimension if cardinality is reasonable relative to row count
                    n = row_count or len(sample_data)
                    ratio = cardinality[col_name] / max(n, 1)
                    if ratio < 0.3:  # Less than 30% unique values -> likely a dimension
                        profile.dimensions.append(col_name)

    # Final pass: remove any identifiers that snuck into dimensions
    profile.dimensions = [d for d in profile.dimensions if d not in profile.identifiers]

    # Sort dimensions by preference (known business dimensions first)
    profile.dimensions.sort(key=lambda d: _dimension_priority(d))

    # Sort measures by preference (revenue/sales/amount first)
    profile.measures.sort(key=lambda m: _measure_priority(m), reverse=True)

    logger.info(
        f"Schema profile: {len(profile.dimensions)} dimensions, "
        f"{len(profile.measures)} measures, {len(profile.identifiers)} identifiers, "
        f"{len(profile.date_cols)} date columns"
    )

    return profile


def classify_schema_from_context(schema_context: str) -> SchemaProfile:
    """
    Parse the freeform schema_context string (used in planner) into a SchemaProfile.

    The schema context format is:
        TABLE schema.table_name (N rows):
          - column_name: TYPE
          - column_name: TYPE
    """
    if not schema_context:
        return SchemaProfile()

    columns: Dict[str, str] = {}
    table_name = ""
    row_count = 0

    lines = schema_context.strip().split("\n")
    current_table = None

    for line in lines:
        # Match table header: "TABLE name (N rows):" — the schema prefix is
        # optional ("TABLE analytics_t_x.name (N rows):" is still accepted).
        table_match = re.match(r'TABLE\s+(?:\S+\.)?(\w+)\s+\((\d+)\s+rows\):?', line, re.IGNORECASE)
        if table_match:
            current_table = table_match.group(1)
            table_name = current_table
            row_count = int(table_match.group(2))
            continue

        # Match column line: "  - column_name: TYPE" (with optional leading whitespace)
        col_match = re.match(r'\s*-\s+(.+?):\s+(\w+)', line)
        if col_match:
            col_name = col_match.group(1).strip()
            col_type = col_match.group(2).strip()
            columns[col_name] = col_type

    return classify_schema(
        table_name=table_name,
        columns=columns,
        row_count=row_count,
    )


def _is_identifier(col_name: str, col_lower: str, col_type: str, cardinality: Dict[str, int]) -> bool:
    """Check if a column is a unique identifier."""
    # Check by specific name
    if col_lower in IDENTIFIER_NAMES:
        return True

    # Check by pattern
    for pat in IDENTIFIER_PATTERNS:
        if re.search(pat, col_lower):
            # For *_code columns, only treat as identifier if cardinality is very high
            if '_code' in col_lower or 'code_' in col_lower:
                if col_name in cardinality and cardinality[col_name] <= 10:
                    return False  # Low-cardinality codes are dimensions
            return True

    # High cardinality + VARCHAR + name like a code -> likely identifier
    if col_type.upper() in ("VARCHAR", "TEXT", "STRING"):
        if col_name in cardinality:
            if cardinality[col_name] >= 50:
                prefix_patterns = ['id_', 'num_', 'cd_', 'code_']
                if any(col_lower.startswith(p) for p in prefix_patterns):
                    return True

    return False


def _is_date_column(col_name: str, col_lower: str, col_type: str) -> bool:
    """Check if a column is a date/timestamp.

    Types are matched by prefix because DuckDB reports TIMESTAMP_NS /
    TIMESTAMP_MS / TIMESTAMP_S (and TIMESTAMP WITH TIME ZONE) — an exact
    match on "TIMESTAMP" misses every one of them.
    """
    date_keywords = ["date", "time", "timestamp", "datetime", "year", "month",
                     "quarter", "day", "week"]
    if any(kw in col_lower for kw in date_keywords):
        return True
    if any(t in col_type.upper() for t in ("DATE", "TIMESTAMP", "DATETIME")):
        return True
    return False


def _is_measure_type(col_type: str) -> bool:
    """Check if the column type is numeric (a potential measure)."""
    return col_type.upper() in (
        "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "HUGEINT",
        "FLOAT", "DOUBLE", "REAL",
        "DECIMAL", "NUMERIC", "MONEY",
    )


def _is_dimension_type(col_type: str) -> bool:
    """Check if the column type is categorical (a potential dimension)."""
    return col_type.upper() in (
        "VARCHAR", "TEXT", "STRING", "CHAR", "BPCHAR",
        "BOOLEAN", "BOOL",
        "ENUM",
    )


def _is_id_column(col_lower: str) -> bool:
    """Check if a column name suggests it's an ID."""
    return any(col_lower.endswith(suffix) for suffix in ["_id", "id"]) or col_lower in IDENTIFIER_NAMES


def _is_dimension_by_name(col_lower: str) -> bool:
    """Check if a column name matches known business dimension keywords."""
    # Strip common prefixes/suffixes for matching
    clean = re.sub(r'^(dim_|d_|fact_|f_)|(_dim|_d)$', '', col_lower)
    return any(kw in clean for kw in DIMENSION_KEYWORDS)


def _dimension_priority(col_name: str) -> int:
    """Rank dimensions: lower number = higher priority for GROUP BY."""
    col_lower = col_name.lower()
    high_priority = [
        "region", "channel", "category", "segment", "department",
        "gender", "status", "country", "type",
    ]
    for i, hp in enumerate(high_priority):
        if hp in col_lower:
            return i
    return 100  # Low priority


def _measure_priority(col_name: str) -> int:
    """Rank measures: higher number = better choice for primary metric."""
    col_lower = col_name.lower()
    # Highest priority: revenue/spending/amount metrics (the main "value")
    if any(kw in col_lower for kw in ["spend", "spent", "total_spent", "total spent"]):
        return 110
    if any(kw in col_lower for kw in ["totalrevenue", "total_revenue", "total revenue"]):
        return 105
    if any(kw in col_lower for kw in ["total amount", "total_amount", "revenue", "sales"]):
        return 100
    if any(kw in col_lower for kw in ["amount", "total", "value", "price"]):
        # Disambiguate: "total_spent" > "total_orders" (orders is a count, not a value)
        if "order" in col_lower or "count" in col_lower or "qty" in col_lower or "quantity" in col_lower:
            return 40  # counts/quantities are lower priority
        return 80
    if any(kw in col_lower for kw in ["quantity", "count", "qty", "orders"]):
        return 60
    if any(kw in col_lower for kw in ["profit", "margin", "cost"]):
        return 50
    return 10


def _estimate_cardinality(
    columns: Dict[str, str],
    sample_data: Optional[List[Dict[str, Any]]],
    row_count: int,
) -> Dict[str, int]:
    """Estimate cardinality (distinct values) from sample data."""
    cardinality: Dict[str, int] = {}
    if not sample_data:
        return cardinality

    for col in columns:
        values = [row.get(col) for row in sample_data if row.get(col) is not None]
        unique = len(set(values))
        cardinality[col] = unique

    return cardinality


def format_schema_for_prompt(profile: SchemaProfile) -> str:
    """Format a SchemaProfile into a prompt-friendly string."""
    parts = [f"Table: {profile.table_name} ({profile.row_count} rows)"]

    if profile.dimensions:
        parts.append(f"Dimensions (for GROUP BY): {', '.join(profile.dimensions)}")
    if profile.measures:
        parts.append(f"Measures (SUM/AVG): {', '.join(profile.measures)}")
    if profile.date_cols:
        parts.append(f"Dates: {', '.join(profile.date_cols)}")
    if profile.identifiers:
        parts.append(f"AVOID: {', '.join(sorted(profile.identifiers))} — these are unique IDs, never use for GROUP BY")

    return "\n".join(parts)
