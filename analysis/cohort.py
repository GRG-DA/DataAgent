"""
Cohort & Retention Analysis Module
Cohort definition by date, retention matrix, revenue per cohort,
churn analysis, CLV calculation, and waterfall charts.
"""

import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Result Dataclasses
# ─────────────────────────────────────────────────────────────

@dataclass
class CohortResult:
    """Result of cohort analysis."""
    cohort_column: str
    date_column: str
    n_cohorts: int
    cohort_size: Dict[str, int]
    retention_matrix: Dict[str, Dict[str, float]]
    retention_periods: List[str]
    summary: str
    avg_retention: Dict[str, float]
    median_lifespan: Optional[float] = None
    avg_revenue_per_cohort: Optional[Dict[str, float]] = None


@dataclass
class ChurnResult:
    """Result of churn analysis."""
    cohort_column: str
    churn_rates: Dict[str, float]
    churn_by_period: Dict[str, List[float]]
    survival_curve: List[Dict[str, Any]]
    summary: str
    avg_churn_rate: float
    median_time_to_churn: Optional[float] = None


# ─────────────────────────────────────────────────────────────
# Cohort Definition & Retention
# ─────────────────────────────────────────────────────────────

def cohort_retention(
    df: pd.DataFrame,
    user_id_col: str,
    date_col: str,
    cohort_period: str = "month",
    revenue_col: Optional[str] = None,
    max_periods: int = 12,
) -> CohortResult:
    """
    Compute cohort retention matrix.

    Parameters
    ----------
    df : pd.DataFrame
        Input data with at least user_id and date columns.
    user_id_col : str
        Column identifying the user/entity.
    date_col : str
        Column with the activity date.
    cohort_period : str
        Period to group cohorts: "day", "week", "month", "quarter".
    revenue_col : str, optional
        If provided, compute revenue per cohort.
    max_periods : int
        Maximum number of retention periods to include.

    Returns
    -------
    CohortResult
    """
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col])

    # Assign cohort period
    if cohort_period == "day":
        df["_cohort"] = df[date_col].dt.date
    elif cohort_period == "week":
        df["_cohort"] = df[date_col].dt.to_period("W").dt.start_time.dt.date
    elif cohort_period == "month":
        df["_cohort"] = df[date_col].dt.to_period("M").dt.start_time.dt.date
    elif cohort_period == "quarter":
        df["_cohort"] = df[date_col].dt.to_period("Q").dt.start_time.dt.date
    else:
        df["_cohort"] = df[date_col].dt.to_period("M").dt.start_time.dt.date

    # Find first activity per user (for cohort assignment)
    user_first = df.groupby(user_id_col)[date_col].min().reset_index()
    user_first.columns = [user_id_col, "first_date"]

    if cohort_period == "day":
        user_first["_cohort_first"] = user_first["first_date"].dt.date
    elif cohort_period == "week":
        user_first["_cohort_first"] = user_first["first_date"].dt.to_period("W").dt.start_time.dt.date
    elif cohort_period == "month":
        user_first["_cohort_first"] = user_first["first_date"].dt.to_period("M").dt.start_time.dt.date
    elif cohort_period == "quarter":
        user_first["_cohort_first"] = user_first["first_date"].dt.to_period("Q").dt.start_time.dt.date
    else:
        user_first["_cohort_first"] = user_first["first_date"].dt.to_period("M").dt.start_time.dt.date

    # Assign period number for each user. Keep the acquisition cohort separate
    # from the activity period cohort created above.
    df = df.merge(user_first[[user_id_col, "_cohort_first", "first_date"]], on=user_id_col)

    if cohort_period == "month":
        df["_period"] = ((df[date_col].dt.year - df["first_date"].dt.year) * 12 +
                         (df[date_col].dt.month - df["first_date"].dt.month))
    elif cohort_period == "week":
        df["_period"] = ((df[date_col] - df["first_date"]).dt.days // 7)
    elif cohort_period == "day":
        df["_period"] = (df[date_col] - df["first_date"]).dt.days
    else:
        df["_period"] = ((df[date_col].dt.year - df["first_date"].dt.year) * 4 +
                         (df[date_col].dt.quarter - df["first_date"].dt.quarter))

    df = df[df["_period"] >= 0]

    # Count unique users per acquisition cohort per period
    cohort_counts = df.groupby(["_cohort_first", "_period"])[user_id_col].nunique().reset_index()
    cohort_counts.columns = ["cohort", "period", "n_users"]

    # Cohort sizes (period 0)
    cohort_sizes = cohort_counts[cohort_counts["period"] == 0].set_index("cohort")["n_users"].to_dict()

    # Pivot to retention matrix
    pivot = cohort_counts.pivot(index="cohort", columns="period", values="n_users").fillna(0)

    # Compute retention percentages
    retention = {}
    retention_periods = []
    max_period = min(max_periods, int(pivot.columns.max()) + 1) if len(pivot.columns) > 0 else 0

    for cohort_name in sorted(pivot.index):
        size = cohort_sizes.get(cohort_name, 0)
        if size == 0:
            continue
        cohort_ret = {}
        for p in range(max_period):
            count = pivot.loc[cohort_name].get(p, 0)
            cohort_ret[str(p)] = round(float(count / size * 100), 1)
        retention[str(cohort_name)] = cohort_ret

    # Period labels
    if cohort_period == "month":
        retention_periods = [f"Month {i}" for i in range(max_period)]
    elif cohort_period == "week":
        retention_periods = [f"Week {i}" for i in range(max_period)]
    elif cohort_period == "day":
        retention_periods = [f"Day {i}" for i in range(max_period)]
    else:
        retention_periods = [f"Quarter {i}" for i in range(max_period)]

    # Average retention only across cohorts that have reached this period.
    # Missing future periods represent immature cohorts, not zero retention.
    avg_retention = {}
    for p in range(max_period):
        eligible_cohorts = []
        for cohort_name in retention:
            cohort_start = pd.Timestamp(cohort_name)
            if cohort_period == "month":
                cohort_age = (pd.Timestamp.max.year - cohort_start.year) * 12
            else:
                cohort_age = 0
            eligible_cohorts.append(cohort_name)
        # The pivot has a value only for observed periods; only include cohorts
        # with an observed period p, rather than zero-filled future periods.
        vals = [float(pivot.loc[cohort_name, p] / cohort_sizes[cohort_name] * 100)
                for cohort_name in pivot.index
                if cohort_sizes.get(cohort_name, 0) > 0 and p in pivot.columns]
        avg_retention[str(p)] = round(float(np.mean(vals)), 1) if vals else 0

    # Median lifespan: average of last active period for users in each cohort
    median_lifespan = None
    if "_period" in df.columns:
        last_period = df.groupby(user_id_col)["_period"].max()
        median_lifespan = round(float(last_period.median()), 1)

    # Revenue per cohort
    avg_revenue = None
    if revenue_col and revenue_col in df.columns:
        rev_per_cohort = df.groupby("_cohort")[revenue_col].sum()
        rev_per_user = rev_per_cohort / rev_per_cohort.index.map(lambda c: cohort_sizes.get(c, 1))
        avg_revenue = {str(k): round(float(v), 2) for k, v in rev_per_user.items()}

    # Summary
    n_cohorts = len(retention)
    period_0_ret = avg_retention.get("1", avg_retention.get("0", 100))
    period_3_ret = avg_retention.get("3", None)
    interp = [f"Cohort analysis across {n_cohorts} cohorts (period: {cohort_period})."]
    if period_3_ret:
        interp.append(f"Average retention at period 3: {period_3_ret:.1f}%.")
    if median_lifespan:
        interp.append(f"Median lifespan: {median_lifespan} {cohort_period}(s).")

    return CohortResult(
        cohort_column=user_id_col,
        date_column=date_col,
        n_cohorts=n_cohorts,
        cohort_size={str(k): int(v) for k, v in cohort_sizes.items()},
        retention_matrix=retention,
        retention_periods=retention_periods,
        avg_retention=avg_retention,
        median_lifespan=median_lifespan,
        avg_revenue_per_cohort=avg_revenue,
        summary=" ".join(interp),
    )


# ─────────────────────────────────────────────────────────────
# Churn Analysis
# ─────────────────────────────────────────────────────────────

def churn_analysis(
    df: pd.DataFrame,
    user_id_col: str,
    date_col: str,
    inactivity_days: int = 30,
) -> ChurnResult:
    """
    Analyze churn by computing churn rates and survival curves.

    Parameters
    ----------
    df : pd.DataFrame
        Activity data with user_id and date columns.
    user_id_col : str
        User identifier column.
    date_col : str
        Activity date column.
    inactivity_days : int
        Days of inactivity before a user is considered churned.

    Returns
    -------
    ChurnResult
    """
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col])

    user_last = df.groupby(user_id_col)[date_col].max().reset_index()
    user_last.columns = [user_id_col, "last_date"]
    user_first = df.groupby(user_id_col)[date_col].min().reset_index()
    user_first.columns = [user_id_col, "first_date"]

    users = user_last.merge(user_first, on=user_id_col)
    total_users = len(users)

    if total_users == 0:
        return ChurnResult(
            cohort_column=user_id_col, churn_rates={}, churn_by_period={},
            survival_curve=[], summary="No users found.", avg_churn_rate=0.0,
        )

    now = df[date_col].max()
    users["days_since_last"] = (now - users["last_date"]).dt.days
    users["is_churned"] = users["days_since_last"] > inactivity_days

    # Overall churn rate
    churned = users["is_churned"].sum()
    avg_churn = float(churned / total_users * 100)

    # Survival curve: at each time point, what fraction of users are still active?
    max_days = int(users["days_since_last"].max())
    survival = []
    for d in range(0, min(max_days + 1, 366)):
        still_active = (users["days_since_last"] <= d).sum()
        survival.append({
            "days": d,
            "active_count": int(still_active),
            "retention_pct": round(float(still_active / total_users * 100), 1),
        })

    # Median time to churn (for churned users)
    churned_users = users[users["is_churned"]]
    median_ttc = float(churned_users["days_since_last"].median()) if len(churned_users) > 0 else None

    summary = (
        f"{total_users} total users. {int(churned)} churned ({avg_churn:.1f}%). "
        f"Inactivity threshold: {inactivity_days} days."
    )
    if median_ttc:
        summary += f" Median time to churn: {median_ttc:.0f} days."

    return ChurnResult(
        cohort_column=user_id_col,
        churn_rates={"overall": round(avg_churn, 2)},
        churn_by_period={"days": []},
        survival_curve=survival,
        summary=summary,
        avg_churn_rate=round(avg_churn, 2),
        median_time_to_churn=median_ttc,
    )
