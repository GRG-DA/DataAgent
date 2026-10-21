"""
Survival Analysis Module
Kaplan-Meier estimator, confidence intervals, median survival,
and log-rank test approximation for group comparison.
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
class KaplanMeierResult:
    """Result of Kaplan-Meier survival analysis."""
    times: List[float]
    survival_probabilities: List[float]
    ci_lower: List[float]
    ci_upper: List[float]
    confidence_level: float
    median_survival: Optional[float]
    n_at_risk: List[int]
    n_events: List[int]
    censored_count: int
    total_subjects: int
    interpretation: str


@dataclass
class LogRankResult:
    """Result of log-rank test comparing two survival curves."""
    test_name: str
    statistic: float
    p_value: float
    degrees_of_freedom: int
    groups: List[str]
    group_medians: Dict[str, Optional[float]]
    significant: bool
    alpha: float
    interpretation: str


# ─────────────────────────────────────────────────────────────
# Kaplan-Meier Estimator
# ─────────────────────────────────────────────────────────────

def kaplan_meier(
    times: pd.Series,
    events: pd.Series,
    confidence: float = 0.95,
    column_name: str = "",
) -> KaplanMeierResult:
    """
    Compute Kaplan-Meier survival curve with Greenwoods confidence intervals.

    Parameters
    ----------
    times : pd.Series
        Observed times (duration to event or censoring).
    events : pd.Series
        Event indicators: 1 = event occurred, 0 = censored.
    confidence : float
        Confidence level for the CI bands (default 0.95).
    column_name : str
        Label for the survival variable (used in interpretation text).

    Returns
    -------
    KaplanMeierResult
        Times, survival probabilities, CI bounds, median survival,
        at-risk counts, event counts, and a human-readable interpretation.
    """
    from scipy import stats as sp_stats

    # Align and clean
    df = pd.DataFrame({"time": times, "event": events}).dropna()
    df["time"] = df["time"].astype(float)
    df["event"] = df["event"].astype(float)

    if len(df) == 0:
        return _empty_km_result(confidence, column_name, "No valid (time, event) pairs provided.")

    # Sort by time ascending
    df = df.sort_values("time").reset_index(drop=True)
    n_total = len(df)

    # Unique event times (only times where at least one event occurred)
    event_mask = df["event"] == 1
    unique_times = sorted(df.loc[event_mask, "time"].unique())

    if len(unique_times) == 0:
        # No events observed -- survival is 1.0 throughout
        all_times = sorted(df["time"].unique())
        return KaplanMeierResult(
            times=[float(t) for t in all_times],
            survival_probabilities=[1.0] * len(all_times),
            ci_lower=[1.0] * len(all_times),
            ci_upper=[1.0] * len(all_times),
            confidence_level=confidence,
            median_survival=None,
            n_at_risk=[n_total - int((df["time"] < t).sum()) for t in all_times],
            n_events=[0] * len(all_times),
            censored_count=int((df["event"] == 0).sum()),
            total_subjects=n_total,
            interpretation=(
                f"No events observed in {n_total} subjects over the follow-up period. "
                f"Median survival is not reached."
            ),
        )

    # KM computation
    km_times: List[float] = []
    km_surv: List[float] = []
    km_var: List[float] = []  # variance of log(-log(S))
    n_at_risk_list: List[int] = []
    n_events_list: List[int] = []

    current_survival = 1.0
    current_log_var = 0.0  # cumulative variance for Greenwood on log scale

    z_crit = sp_stats.norm.ppf((1 + confidence) / 2)

    for t in unique_times:
        # Subjects at risk just before time t
        n_at_risk = int((df["time"] >= t).sum())
        n_events_at_t = int(((df["time"] == t) & (df["event"] == 1)).sum())

        if n_at_risk == 0:
            break

        # KM step
        current_survival *= (1 - n_events_at_t / n_at_risk)

        # Greenwood variance of S(t): Var(S) = S^2 * sum(d_j / (n_j*(n_j - d_j)))
        if n_at_risk > 1:
            current_log_var += n_events_at_t / (n_at_risk * (n_at_risk - 1))

        km_times.append(float(t))
        km_surv.append(float(current_survival))
        km_var.append(current_log_var)
        n_at_risk_list.append(n_at_risk)
        n_events_list.append(n_events_at_t)

    # Confidence intervals using log-log transformation (more accurate near 0 and 1)
    ci_lower: List[float] = []
    ci_upper: List[float] = []

    for s, var_log in zip(km_surv, km_var):
        if s <= 0 or s >= 1:
            ci_lower.append(s)
            ci_upper.append(s)
            continue

        log_surv = np.log(-np.log(s))
        se_log = np.sqrt(var_log)

        lower_log_log = log_surv - z_crit * se_log
        upper_log_log = log_surv + z_crit * se_log

        ci_lower.append(float(np.exp(-np.exp(upper_log_log))))
        ci_upper.append(float(np.exp(-np.exp(lower_log_log))))

    # Median survival: first time where S(t) <= 0.5
    median_survival: Optional[float] = None
    for t, s in zip(km_times, km_surv):
        if s <= 0.5:
            median_survival = t
            break

    censored_count = int((df["event"] == 0).sum())

    # Interpretation
    label = column_name if column_name else "the survival variable"
    if median_survival is not None:
        median_text = f"The median survival time is {median_survival:.4f}."
    else:
        median_text = "The median survival time was not reached (survival remained above 50%)."

    final_surv = km_surv[-1] if km_surv else 1.0
    interpretation = (
        f"Kaplan-Meier analysis of '{label}': {n_total} subjects, "
        f"{int(df['event'].sum())} events, {censored_count} censored. "
        f"Final survival probability: {final_surv:.4f}. "
        f"{median_text} "
        f"95% CI at final time: [{ci_lower[-1]:.4f}, {ci_upper[-1]:.4f}]."
    )

    return KaplanMeierResult(
        times=km_times,
        survival_probabilities=[round(s, 6) for s in km_surv],
        ci_lower=[round(cl, 6) for cl in ci_lower],
        ci_upper=[round(cu, 6) for cu in ci_upper],
        confidence_level=confidence,
        median_survival=round(median_survival, 4) if median_survival is not None else None,
        n_at_risk=n_at_risk_list,
        n_events=n_events_list,
        censored_count=censored_count,
        total_subjects=n_total,
        interpretation=interpretation,
    )


def _empty_km_result(confidence: float, column_name: str, reason: str) -> KaplanMeierResult:
    """Return a empty KaplanMeierResult with the given reason."""
    return KaplanMeierResult(
        times=[], survival_probabilities=[], ci_lower=[], ci_upper=[],
        confidence_level=confidence, median_survival=None,
        n_at_risk=[], n_events=[], censored_count=0, total_subjects=0,
        interpretation=reason,
    )


# ─────────────────────────────────────────────────────────────
# Log-Rank Test (approximation via observed vs expected events)
# ─────────────────────────────────────────────────────────────

def log_rank_test(
    times_a: pd.Series,
    events_a: pd.Series,
    times_b: pd.Series,
    events_b: pd.Series,
    group_names: Optional[Tuple[str, str]] = None,
    alpha: float = 0.05,
) -> LogRankResult:
    """
    Perform a log-rank test comparing two survival distributions.

    Uses the standard log-rank (Mantel-Haenszel) statistic, which follows
    a chi-squared distribution with 1 degree of freedom under H0.

    Parameters
    ----------
    times_a, events_a : pd.Series
        Times and event indicators for group A.
    times_b, events_b : pd.Series
        Times and event indicators for group B.
    group_names : tuple of str, optional
        Labels for the two groups.
    alpha : float
        Significance level (default 0.05).

    Returns
    -------
    LogRankResult
        Test statistic, p-value, degrees of freedom, group medians,
        significance flag, and interpretation.
    """
    from scipy import stats as sp_stats

    group_a_label = group_names[0] if group_names else "Group A"
    group_b_label = group_names[1] if group_names else "Group B"

    # Combine into a single DataFrame
    df_a = pd.DataFrame({"time": times_a, "event": events_a, "group": 0}).dropna()
    df_b = pd.DataFrame({"time": times_b, "event": events_b, "group": 1}).dropna()
    df_a["time"] = df_a["time"].astype(float)
    df_a["event"] = df_a["event"].astype(float)
    df_b["time"] = df_b["time"].astype(float)
    df_b["event"] = df_b["event"].astype(float)

    df = pd.concat([df_a, df_b], ignore_index=True)

    if len(df) < 2:
        return _empty_log_rank(alpha, group_a_label, group_b_label,
                               "Not enough data for log-rank test.")

    # Unique event times across both groups
    event_times = sorted(df.loc[df["event"] == 1, "time"].unique())

    if len(event_times) == 0:
        return _empty_log_rank(alpha, group_a_label, group_b_label,
                               "No events observed in either group.")

    # Log-rank statistic computation
    # O_j = observed events in group A at time j
    # E_j = expected events in group A at time j = n_A_j * d_j / n_j
    numerator = 0.0
    variance = 0.0

    n_a_total = len(df_a)
    n_b_total = len(df_b)

    for t in event_times:
        # At risk just before t
        risk_a = int((df_a["time"] >= t).sum())
        risk_b = int((df_b["time"] >= t).sum())
        n_total = risk_a + risk_b

        if n_total == 0:
            continue

        # Events at time t
        events_a_t = int(((df_a["time"] == t) & (df_a["event"] == 1)).sum())
        events_b_t = int(((df_b["time"] == t) & (df_b["event"] == 1)).sum())
        d_j = events_a_t + events_b_t

        # Expected events in group A
        e_a = risk_a * d_j / n_total

        numerator += events_a_t - e_a

        # Variance (hypergeometric)
        if n_total > 1:
            variance += (risk_a * risk_b * d_j * (n_total - d_j)) / (n_total ** 2 * (n_total - 1))

    # Test statistic
    if variance > 0:
        chi2_stat = numerator ** 2 / variance
    else:
        chi2_stat = 0.0

    p_value = 1 - sp_stats.chi2.cdf(chi2_stat, df=1)

    # Compute medians for each group
    km_a = kaplan_meier(times_a, events_a, column_name=group_a_label)
    km_b = kaplan_meier(times_b, events_b, column_name=group_b_label)

    significant = p_value < alpha

    if significant:
        if km_a.median_survival is not None and km_b.median_survival is not None:
            if km_a.median_survival > km_b.median_survival:
                better = group_a_label
            else:
                better = group_b_label
            interp = (
                f"The log-rank test shows a statistically significant difference "
                f"in survival between '{group_a_label}' and '{group_b_label}' "
                f"(chi2 = {chi2_stat:.4f}, p = {p_value:.4f}). "
                f"'{better}' has longer survival."
            )
        else:
            interp = (
                f"The log-rank test shows a statistically significant difference "
                f"in survival between '{group_a_label}' and '{group_b_label}' "
                f"(chi2 = {chi2_stat:.4f}, p = {p_value:.4f})."
            )
    else:
        interp = (
            f"The log-rank test does NOT show a statistically significant difference "
            f"in survival between '{group_a_label}' and '{group_b_label}' "
            f"(chi2 = {chi2_stat:.4f}, p = {p_value:.4f})."
        )

    return LogRankResult(
        test_name="Log-Rank (Mantel-Haenszel)",
        statistic=round(float(chi2_stat), 6),
        p_value=round(float(p_value), 6),
        degrees_of_freedom=1,
        groups=[group_a_label, group_b_label],
        group_medians={
            group_a_label: km_a.median_survival,
            group_b_label: km_b.median_survival,
        },
        significant=significant,
        alpha=alpha,
        interpretation=interp,
    )


def _empty_log_rank(alpha: float, ga: str, gb: str, reason: str) -> LogRankResult:
    """Return an empty LogRankResult."""
    return LogRankResult(
        test_name="Log-Rank (Mantel-Haenszel)",
        statistic=0.0, p_value=1.0, degrees_of_freedom=1,
        groups=[ga, gb],
        group_medians={ga: None, gb: None},
        significant=False, alpha=alpha, interpretation=reason,
    )


# ─────────────────────────────────────────────────────────────
# Multi-group KM helper
# ─────────────────────────────────────────────────────────────

def kaplan_meier_by_group(
    df: pd.DataFrame,
    time_col: str,
    event_col: str,
    group_col: str,
    confidence: float = 0.95,
) -> Dict[str, KaplanMeierResult]:
    """
    Compute separate Kaplan-Meier curves for each level of a grouping variable.

    Returns a dict mapping group label to KaplanMeierResult.

    Missing time/event/group columns yield an empty dict (no raise) so
    callers get a plottable empty result instead of a KeyError.
    """
    results: Dict[str, KaplanMeierResult] = {}
    for col in (time_col, event_col, group_col):
        if not col or col not in df.columns:
            return results
    for name, grp in df.groupby(group_col):
        km = kaplan_meier(
            grp[time_col], grp[event_col],
            confidence=confidence, column_name=str(name),
        )
        results[str(name)] = km
    return results
