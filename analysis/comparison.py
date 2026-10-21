"""
Data Comparison / Diff Engine
Period-over-period comparison with significance, table diff highlighting,
automated "what changed" narratives, and side-by-side visual comparison.
"""

import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
import numpy as np
import pandas as pd
from scipy import stats as sp_stats

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Result Dataclasses
# ─────────────────────────────────────────────────────────────

@dataclass
class ComparisonResult:
    """Result of a data comparison."""
    comparison_type: str
    summary: str
    metrics: List[Dict[str, Any]]
    overall_significance: bool
    overall_alpha: float
    highlight_count: int
    total_compared: int
    narrative: str


@dataclass
class TableDiffResult:
    """Result of a table diff."""
    columns: List[str]
    rows_added: int
    rows_removed: int
    rows_modified: int
    rows_unchanged: int
    modified_cells: Dict[str, List[Dict[str, Any]]]
    summary: str


# ─────────────────────────────────────────────────────────────
# Period-over-Period Comparison
# ─────────────────────────────────────────────────────────────

def compare_periods(
    df: pd.DataFrame,
    date_col: str,
    metric_col: str,
    group_col: str = "",
    period_type: str = "month",
    alpha: float = 0.05,
    agg_func: str = "sum",
) -> ComparisonResult:
    """
    Compare metric values across time periods.

    Parameters
    ----------
    df : pd.DataFrame
        Input data.
    date_col : str
        Date column for period grouping.
    metric_col : str
        Numeric column to compare.
    group_col : str, optional
        Optional grouping column (e.g., region, channel).
    period_type : str
        Period granularity: "month", "quarter", "year", "week".
    alpha : float
        Significance level for statistical tests.

    Returns
    -------
    ComparisonResult
    """
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col])

    # Create period labels
    if period_type == "month":
        df["_period"] = df[date_col].dt.to_period("M")
    elif period_type == "quarter":
        df["_period"] = df[date_col].dt.to_period("Q")
    elif period_type == "year":
        df["_period"] = df[date_col].dt.to_period("Y")
    elif period_type == "week":
        df["_period"] = df[date_col].dt.to_period("W")
    else:
        df["_period"] = df[date_col].dt.to_period("M")

    # Aggregate by period. Both stats are computed because downstream
    # comparisons report mean AND sum side by side (additive measures such as
    # revenue read "sum"; rates/scores read "mean").
    if group_col and group_col in df.columns:
        agg = df.groupby(["_period", group_col])[metric_col].agg(["mean", "sum"]).reset_index()
        agg.columns = ["period", "group", "mean", "sum"]
        _group_col = group_col
    else:
        agg = df.groupby("_period")[metric_col].agg(["mean", "sum"]).reset_index()
        agg.columns = ["period", "mean", "sum"]
        agg["group"] = "all"
        _group_col = None

    def compute_pct_change(curr, prev):
        if prev == 0:
            return 100.0 if curr > 0 else (-100.0 if curr < 0 else 0.0)
        return (curr - prev) / abs(prev) * 100

    periods_sorted = sorted(agg["period"].unique())
    if len(periods_sorted) < 2:
        return ComparisonResult(
            comparison_type=f"{period_type}-over-{period_type}",
            summary=f"Need at least 2 periods for comparison. Found {len(periods_sorted)}.",
            metrics=[], overall_significance=False, overall_alpha=alpha,
            highlight_count=0, total_compared=0, narrative="Insufficient data for comparison.",
        )

    # Compare consecutive pairs
    metrics = []
    highlights = []
    significant_count = 0
    total_compared = 0

    for i in range(1, len(periods_sorted)):
        p_prev = periods_sorted[i - 1]
        p_curr = periods_sorted[i]

        d_prev = agg[agg["period"] == p_prev]
        d_curr = agg[agg["period"] == p_curr]

        for group in d_curr["group"].unique():
            vals_prev = d_prev[d_prev["group"] == group]
            vals_curr = d_curr[d_curr["group"] == group]

            mean_prev = vals_prev["mean"].iloc[0] if len(vals_prev) > 0 else 0
            mean_curr = vals_curr["mean"].iloc[0] if len(vals_curr) > 0 else 0
            sum_prev = vals_prev["sum"].iloc[0] if len(vals_prev) > 0 else 0
            sum_curr = vals_curr["sum"].iloc[0] if len(vals_curr) > 0 else 0

            # Compute change
            abs_change = mean_curr - mean_prev
            pct_change = (abs_change / mean_prev * 100) if mean_prev != 0 else 0

            # Statistical significance (t-test between raw values in each period)
            if _group_col and group != "all":
                raw_prev = df[(df["_period"] == p_prev) & (df[_group_col] == group)][metric_col].dropna()
                raw_curr = df[(df["_period"] == p_curr) & (df[_group_col] == group)][metric_col].dropna()
            else:
                raw_prev = df[df["_period"] == p_prev][metric_col].dropna()
                raw_curr = df[df["_period"] == p_curr][metric_col].dropna()

            # Simplify: just use aggregate means for now
            p_value = 1.0
            significant = False
            if len(raw_prev) >= 2 and len(raw_curr) >= 2:
                try:
                    _, p_value = sp_stats.ttest_ind(raw_prev, raw_curr, equal_var=False)
                    significant = p_value < alpha
                except Exception:
                    pass

            total_compared += 1
            if significant:
                significant_count += 1
                highlights.append({
                    "period_prev": str(p_prev),
                    "period_curr": str(p_curr),
                    "group": str(group) if group != "all" else metric_col,
                    "change_pct": round(float(pct_change), 2),
                    "direction": "up" if pct_change > 0 else "down",
                })

            metrics.append({
                "period_prev": str(p_prev),
                "period_curr": str(p_curr),
                "group": str(group) if group != "all" else None,
                "mean_prev": round(float(mean_prev), 4),
                "mean_curr": round(float(mean_curr), 4),
                "sum_prev": round(float(sum_prev), 2),
                "sum_curr": round(float(sum_curr), 2),
                "abs_change": round(float(abs_change), 4),
                "pct_change": round(float(pct_change), 2),
                "p_value": round(float(p_value), 6),
                "significant": significant,
                "direction": "up" if pct_change > 0 else ("down" if pct_change < 0 else "flat"),
            })

    # Build narrative
    narrative_parts = [f"Compared {total_compared} period-over-period changes (type: {period_type})."]
    if significant_count > 0:
        biggest = max(metrics, key=lambda x: abs(x.get("pct_change", 0)))
        narrative_parts.append(
            f"{significant_count} significant changes detected (p<{alpha}). "
            f"Biggest: {biggest['group'] or metric_col} {biggest['direction']} {abs(biggest['pct_change']):.1f}% "
            f"from {biggest['period_prev']} to {biggest['period_curr']}."
        )
    else:
        narrative_parts.append("No statistically significant changes detected across periods.")

    return ComparisonResult(
        comparison_type=f"{period_type}-over-{period_type}",
        summary=f"Compared {total_compared} changes. {significant_count} significant.",
        metrics=metrics,
        overall_significance=significant_count > 0,
        overall_alpha=alpha,
        highlight_count=significant_count,
        total_compared=total_compared,
        narrative=" ".join(narrative_parts),
    )


# ─────────────────────────────────────────────────────────────
# Table Diff
# ─────────────────────────────────────────────────────────────

def diff_tables(
    df_before: pd.DataFrame,
    df_after: pd.DataFrame,
    key_column: Optional[str] = None,
) -> TableDiffResult:
    """
    Compute a diff between two DataFrames.

    Parameters
    ----------
    df_before : pd.DataFrame
        First (old) DataFrame.
    df_after : pd.DataFrame
        Second (new) DataFrame.
    key_column : str, optional
        Primary key column for matching rows. If None, compare by index.

    Returns
    -------
    TableDiffResult
    """
    columns = list(set(list(df_before.columns) + list(df_after.columns)))

    if key_column and key_column in df_before.columns and key_column in df_after.columns:
        # Join-based diff
        before_keys = set(df_before[key_column].dropna())
        after_keys = set(df_after[key_column].dropna())

        added_keys = after_keys - before_keys
        removed_keys = before_keys - after_keys
        common_keys = before_keys & after_keys

        # Compare common rows
        modified_cells = {}
        modified_count = 0
        unchanged_count = 0

        df_b = df_before.set_index(key_column)
        df_a = df_after.set_index(key_column)

        for key in common_keys:
            row_b = df_b.loc[key] if key in df_b.index else pd.Series()
            row_a = df_a.loc[key] if key in df_a.index else pd.Series()
            if len(row_b.shape) > 0:
                row_b = row_b.iloc[0] if isinstance(row_b, pd.DataFrame) else row_b
            if len(row_a.shape) > 0:
                row_a = row_a.iloc[0] if isinstance(row_a, pd.DataFrame) else row_a

            for col in columns:
                val_b = row_b.get(col) if isinstance(row_b, pd.Series) else None
                val_a = row_a.get(col) if isinstance(row_a, pd.Series) else None
                if val_b != val_a:
                    if col not in modified_cells:
                        modified_cells[col] = []
                    modified_cells[col].append({
                        "key": key,
                        "before": val_b,
                        "after": val_a,
                        "change": f"{val_b} → {val_a}",
                    })
                    modified_count += 1
            else:
                unchanged_count += 1

        return TableDiffResult(
            columns=columns,
            rows_added=len(added_keys),
            rows_removed=len(removed_keys),
            rows_modified=modified_count,
            rows_unchanged=unchanged_count,
            modified_cells=modified_cells,
            summary=(
                f"Before: {len(df_before)} rows × {len(df_before.columns)} cols. "
                f"After: {len(df_after)} rows × {len(df_after.columns)} cols. "
                f"Added: {len(added_keys)}. Removed: {len(removed_keys)}. "
                f"Modified: {modified_count} cells. Unchanged: {unchanged_count} rows."
            ),
        )

    else:
        # Index-based diff
        before_set = set(df_before.apply(tuple, axis=1))
        after_set = set(df_after.apply(tuple, axis=1))

        added = after_set - before_set
        removed = before_set - after_set

        return TableDiffResult(
            columns=columns,
            rows_added=len(added),
            rows_removed=len(removed),
            rows_modified=0,
            rows_unchanged=0,
            modified_cells={},
            summary=f"Before: {len(df_before)} rows. After: {len(df_after)} rows. Added: {len(added)}. Removed: {len(removed)}.",
        )
