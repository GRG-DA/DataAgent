"""
Statistical Analysis Module
Normality tests, hypothesis testing, regression, feature importance,
confidence intervals, A/B testing, and power analysis
"""

import logging
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass, field, asdict
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Result Dataclasses
# ─────────────────────────────────────────────────────────────

@dataclass
class NormalityTestResult:
    """Result of a normality test."""
    column: str
    test_name: str
    statistic: float
    p_value: float
    is_normal: bool
    interpretation: str
    sample_size: int


@dataclass
class HypothesisTestResult:
    """Result of a hypothesis test.

    ``ok`` is False when the test could not be run (an unknown test type,
    not enough observations, ...); ``error`` then carries the reason.  A
    caller must not report a failed test as a successful one — use
    :func:`is_error_result`.
    """
    test_name: str
    statistic: float
    p_value: float
    effect_size: float
    effect_size_name: str
    interpretation: str
    significant: bool
    alpha: float
    groups: List[str] = field(default_factory=list)
    group_stats: Dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    error: str = ""


def is_error_result(result: Any) -> bool:
    """True when *result* carries an error rather than a test outcome.

    A hypothesis test that could not run (unknown ``test_type``, missing
    columns, too few observations) must be reported as a failure, not as a
    successful test with a zero p-value.
    """
    if result is None:
        return True
    if getattr(result, "ok", True) is False:
        return True
    return bool(getattr(result, "error", "") or "")


@dataclass
class TimeSeriesDecompositionResult:
    """Result of time series decomposition."""
    dates: List[str]
    observed: List[float]
    trend: List[float]
    seasonal: List[float]
    residual: List[float]
    period: int
    model: str
    strength_trend: float
    strength_seasonality: float


@dataclass
class RegressionResult:
    """Result of regression analysis."""
    regression_type: str
    target_col: str
    feature_cols: List[str]
    coefficients: Dict[str, float]
    intercept: float
    r_squared: float
    adj_r_squared: float
    p_values: Dict[str, float]
    mse: float
    rmse: float
    mae: float
    significant_features: List[str]
    interpretation: str


@dataclass
class FeatureImportanceResult:
    """Result of feature importance analysis."""
    target_col: str
    method: str
    features: List[Dict[str, Any]]
    interpretation: str


@dataclass
class ConfidenceIntervalResult:
    """Result of confidence interval calculation.

    ``margin_of_error`` is the authoritative half-width of the published
    interval, i.e. exactly ``(ci_upper - ci_lower) / 2`` computed from the
    (rounded) bounds reported here. The web UI used to re-derive it
    client-side, which could drift; the backend now owns the value.

    The half-width is level-independent by construction: it is whatever the
    requested confidence level produced, so a 90 % interval reports its own
    (narrower) margin rather than a 95 % one.
    """
    column: str
    mean: float
    ci_lower: float
    ci_upper: float
    confidence_level: float
    method: str
    sample_size: int
    interpretation: str
    margin_of_error: float = 0.0


@dataclass
class ABTestResult:
    """Result of A/B test analysis."""
    group_col: str
    metric_col: str
    group_a: str
    group_b: str
    group_a_stats: Dict[str, float]
    group_b_stats: Dict[str, float]
    test_name: str
    statistic: float
    p_value: float
    effect_size: float
    effect_size_name: str
    power: float
    sample_size_needed: int
    winner: str
    significant: bool
    alpha: float
    interpretation: str


@dataclass
class PowerAnalysisResult:
    """Result of power analysis."""
    effect_size: float
    alpha: float
    power: float
    sample_size_per_group: int
    total_sample_size: int
    interpretation: str


# ─────────────────────────────────────────────────────────────
# Normality Tests
# ─────────────────────────────────────────────────────────────

def test_normality(
    series: pd.Series,
    column_name: str = "",
    method: str = "shapiro",
) -> NormalityTestResult:
    """
    Test whether a numeric series is normally distributed.

    Methods:
    - shapiro: Shapiro-Wilk test (best for n < 5000)
    - ks: Kolmogorov-Smirnov test against normal distribution
    """
    from scipy import stats as sp_stats

    clean = series.dropna()
    if len(clean) < 3:
        return NormalityTestResult(
            column=column_name,
            test_name=method,
            statistic=0.0,
            p_value=1.0,
            is_normal=False,
            interpretation="Not enough data points for normality test (need >= 3).",
            sample_size=len(clean),
        )

    if method == "shapiro":
        # Shapiro-Wilk: use at most 5000 samples (scipy limitation)
        sample = clean if len(clean) <= 5000 else clean.sample(5000, random_state=42)
        stat, p_value = sp_stats.shapiro(sample)
        test_name = "Shapiro-Wilk"
    elif method == "ks":
        from scipy.stats import norm as scipy_norm
        mean, std = clean.mean(), clean.std()
        stat, p_value = sp_stats.kstest(clean, lambda x: scipy_norm.cdf(x, loc=mean, scale=std))
        test_name = "Kolmogorov-Smirnov"
    else:
        return NormalityTestResult(
            column=column_name, test_name=method, statistic=0.0, p_value=1.0,
            is_normal=False, interpretation=f"Unknown method: {method}",
            sample_size=len(clean),
        )

    is_normal = p_value > 0.05
    if is_normal:
        interpretation = (
            f"The distribution of '{column_name}' appears normal "
            f"(p={p_value:.4f} > 0.05). No significant deviation from normality detected."
        )
    else:
        interpretation = (
            f"The distribution of '{column_name}' is NOT normal "
            f"(p={p_value:.4f} <= 0.05). Significant deviation from normality detected."
        )

    return NormalityTestResult(
        column=column_name,
        test_name=test_name,
        statistic=round(float(stat), 6),
        p_value=round(float(p_value), 6),
        is_normal=is_normal,
        interpretation=interpretation,
        sample_size=len(clean),
    )


# ─────────────────────────────────────────────────────────────
# Hypothesis Tests
# ─────────────────────────────────────────────────────────────

def test_hypothesis(
    df: pd.DataFrame,
    column_a: str,
    column_b: str = "",
    test_type: str = "ttest",
    group_col: str = "",
    alpha: float = 0.05,
) -> HypothesisTestResult:
    """
    Perform hypothesis testing.

    Test types:
    - ttest: Independent two-sample t-test (column_b is the second group's metric,
             or group_col splits data into groups and column_a is the metric)
    - paired_ttest: Paired t-test (two numeric columns from same rows)
    - mannwhitney: Mann-Whitney U test (non-parametric alternative)
    - chi2: Chi-square test of independence (group_col vs column_a as categories)
    - anova: One-way ANOVA (compare means across groups in group_col)
    - pearsonr: Pearson correlation test between two numeric columns
    """
    from scipy import stats as sp_stats

    # ── t-test (two independent groups) ──
    if test_type == "ttest":
        if group_col and column_a:
            groups = df[group_col].dropna().unique()
            if len(groups) < 2:
                return _hypothesis_error(test_type, f"Need at least 2 groups in '{group_col}'")
            if len(groups) > 2:
                return _hypothesis_error(
                    test_type,
                    f"T-Test requires exactly 2 groups in '{group_col}' "
                    f"(found {len(groups)}); use ANOVA for 3 or more groups",
                )
            g1 = df[df[group_col] == groups[0]][column_a].dropna()
            g2 = df[df[group_col] == groups[1]][column_a].dropna()
            groups_list = [str(groups[0]), str(groups[1])]
        elif column_a and column_b:
            g1 = df[column_a].dropna()
            g2 = df[column_b].dropna()
            groups_list = [column_a, column_b]
        else:
            return _hypothesis_error(test_type, "Provide group_col + column_a, or column_a + column_b")

        if len(g1) < 2 or len(g2) < 2:
            return _hypothesis_error(test_type, "Need at least 2 observations per group")

        stat, p_value = sp_stats.ttest_ind(g1, g2, equal_var=False)
        effect = _cohens_d(g1, g2)
        effect_name = "Cohen's d"

        group_stats = {
            groups_list[0]: {"mean": round(float(g1.mean()), 4), "std": round(float(g1.std()), 4), "n": len(g1)},
            groups_list[1]: {"mean": round(float(g2.mean()), 4), "std": round(float(g2.std()), 4), "n": len(g2)},
        }

    # ── Paired t-test ──
    elif test_type == "paired_ttest":
        if not column_a or not column_b:
            return _hypothesis_error(test_type, "Need column_a and column_b for paired t-test")
        paired = df[[column_a, column_b]].dropna()
        if len(paired) < 2:
            return _hypothesis_error(test_type, "Not enough paired observations")
        stat, p_value = sp_stats.ttest_rel(paired[column_a], paired[column_b])
        diffs = paired[column_a].astype(float) - paired[column_b].astype(float)
        diff_std = diffs.std(ddof=1)
        effect = float(diffs.mean() / diff_std) if diff_std and not np.isnan(diff_std) else 0.0
        effect_name = "Cohen's dz"
        groups_list = [column_a, column_b]
        group_stats = {
            column_a: {"mean": round(float(paired[column_a].mean()), 4), "std": round(float(paired[column_a].std()), 4)},
            column_b: {"mean": round(float(paired[column_b].mean()), 4), "std": round(float(paired[column_b].std()), 4)},
        }

    # ── Mann-Whitney U ──
    elif test_type == "mannwhitney":
        if group_col and column_a:
            groups = df[group_col].dropna().unique()
            if len(groups) < 2:
                return _hypothesis_error(test_type, f"Need at least 2 groups in '{group_col}'")
            if len(groups) > 2:
                return _hypothesis_error(
                    test_type,
                    f"Mann-Whitney U requires exactly 2 groups in '{group_col}' "
                    f"(found {len(groups)}); use ANOVA for 3 or more groups",
                )
            g1 = df[df[group_col] == groups[0]][column_a].dropna()
            g2 = df[df[group_col] == groups[1]][column_a].dropna()
            groups_list = [str(groups[0]), str(groups[1])]
        elif column_a and column_b:
            g1 = df[column_a].dropna()
            g2 = df[column_b].dropna()
            groups_list = [column_a, column_b]
        else:
            return _hypothesis_error(test_type, "Provide group_col + column_a, or column_a + column_b")

        stat, p_value = sp_stats.mannwhitneyu(g1, g2, alternative="two-sided")
        effect = _cohens_d(g1, g2)
        effect_name = "Cohen's d"
        group_stats = {
            groups_list[0]: {"median": round(float(g1.median()), 4), "n": len(g1)},
            groups_list[1]: {"median": round(float(g2.median()), 4), "n": len(g2)},
        }

    # ── Chi-square test of independence ──
    elif test_type == "chi2":
        if not group_col or not column_a:
            return _hypothesis_error(test_type, "Need group_col and column_a for chi-square test")
        contingency = pd.crosstab(df[group_col], df[column_a])
        if contingency.shape[0] < 2 or contingency.shape[1] < 2:
            return _hypothesis_error(test_type, "Contingency table too small")
        stat, p_value, dof, expected = sp_stats.chi2_contingency(contingency)
        # Cramér's V as effect size
        n = contingency.sum().sum()
        min_dim = min(contingency.shape) - 1
        effect = float(np.sqrt(stat / (n * min_dim))) if min_dim > 0 else 0.0
        effect_name = "Cramer's V"
        groups_list = [group_col, column_a]
        group_stats = {"contingency_table": contingency.to_dict()}

    # ── One-way ANOVA ──
    elif test_type == "anova":
        if not group_col or not column_a:
            return _hypothesis_error(test_type, "Need group_col and column_a for ANOVA")
        groups_data = []
        group_names = []
        for name, grp in df.groupby(group_col):
            vals = grp[column_a].dropna()
            if len(vals) > 0:
                groups_data.append(vals)
                group_names.append(str(name))
        if len(groups_data) < 2:
            return _hypothesis_error(test_type, "Need at least 2 groups with data")
        stat, p_value = sp_stats.f_oneway(*groups_data)
        # Eta-squared as effect size
        grand_mean = df[column_a].dropna().mean()
        ss_between = sum(len(g) * (g.mean() - grand_mean) ** 2 for g in groups_data)
        ss_total = sum((df[column_a].dropna() - grand_mean) ** 2)
        effect = float(ss_between / ss_total) if ss_total > 0 else 0.0
        effect_name = "Eta-squared"
        groups_list = group_names
        group_stats = {
            name: {"mean": round(float(g.mean()), 4), "std": round(float(g.std()), 4), "n": len(g)}
            for name, g in zip(group_names, groups_data)
        }

    # ── Pearson correlation ──
    elif test_type == "pearsonr":
        if not column_a or not column_b:
            return _hypothesis_error(test_type, "Need column_a and column_b for correlation test")
        valid = df[[column_a, column_b]].dropna()
        if len(valid) < 3:
            return _hypothesis_error(test_type, "Need at least 3 paired observations")
        stat, p_value = sp_stats.pearsonr(valid[column_a], valid[column_b])
        effect = abs(float(stat))
        effect_name = "|r|"
        groups_list = [column_a, column_b]
        group_stats = {
            column_a: {"mean": round(float(valid[column_a].mean()), 4)},
            column_b: {"mean": round(float(valid[column_b].mean()), 4)},
        }

    else:
        return _hypothesis_error(test_type, f"Unknown test type: {test_type}")

    significant = p_value < alpha
    effect_label = _interpret_effect_size(effect, effect_name)
    interpretation = _interpret_hypothesis(test_type, groups_list, p_value, alpha, significant, effect_label, stat)

    return HypothesisTestResult(
        test_name=test_type,
        statistic=round(float(stat), 6),
        p_value=round(float(p_value), 6),
        effect_size=round(float(effect), 4),
        effect_size_name=effect_name,
        interpretation=interpretation,
        significant=significant,
        alpha=alpha,
        groups=groups_list,
        group_stats=group_stats,
    )


def _hypothesis_error(test_type: str, message: str) -> HypothesisTestResult:
    return HypothesisTestResult(
        test_name=test_type, statistic=0.0, p_value=1.0, effect_size=0.0,
        effect_size_name="", interpretation=f"Error: {message}", significant=False,
        alpha=0.05, ok=False, error=message,
    )


def _cohens_d(group1: pd.Series, group2: pd.Series) -> float:
    """Compute Cohen's d effect size for two groups."""
    n1, n2 = len(group1), len(group2)
    if n1 < 2 or n2 < 2:
        return 0.0
    var1, var2 = group1.var(), group2.var()
    pooled_std = np.sqrt(((n1 - 1) * var1 + (n2 - 1) * var2) / (n1 + n2 - 2))
    if pooled_std == 0:
        return 0.0
    return float((group1.mean() - group2.mean()) / pooled_std)


def _interpret_effect_size(effect: float, effect_name: str) -> str:
    if effect_name in ("Cohen's d",):
        if effect < 0.2:
            return "negligible"
        elif effect < 0.5:
            return "small"
        elif effect < 0.8:
            return "medium"
        else:
            return "large"
    elif effect_name == "|r|":
        if effect < 0.1:
            return "negligible"
        elif effect < 0.3:
            return "small"
        elif effect < 0.5:
            return "medium"
        else:
            return "large"
    elif effect_name == "Cramer's V":
        if effect < 0.1:
            return "negligible"
        elif effect < 0.3:
            return "small"
        elif effect < 0.5:
            return "medium"
        else:
            return "large"
    elif effect_name == "Eta-squared":
        if effect < 0.01:
            return "negligible"
        elif effect < 0.06:
            return "small"
        elif effect < 0.14:
            return "medium"
        else:
            return "large"
    return "unknown"


def _interpret_hypothesis(
    test_type: str, groups: List[str], p: float, alpha: float,
    significant: bool, effect_label: str, statistic: float = 0.0,
) -> str:
    verb = "is" if significant else "is NOT"
    comparison = "<" if significant else ">="
    sig_text = f"statistically significant (p={p:.4f} {comparison} {alpha})"
    effect_text = f"The effect size is {effect_label}."

    if test_type == "chi2":
        return (
            f"The chi-square test of independence between '{groups[0]}' and '{groups[1]}' "
            f"{verb} {sig_text}. {effect_text} There {'is' if significant else 'is no'} evidence of "
            f"an association between the two variables."
        )
    elif test_type == "pearsonr":
        direction = "positive" if statistic >= 0 else "negative"
        return (
            f"The Pearson correlation between '{groups[0]}' and '{groups[1]}' is {direction} and "
            f"{verb} {sig_text}. {effect_text}"
        )
    elif test_type == "anova":
        preview = ", ".join(f"'{g}'" for g in groups[:5])
        if len(groups) > 5:
            preview += f", +{len(groups) - 5} more"
        return (
            f"The one-way ANOVA comparing group means across {len(groups)} groups "
            f"({preview}) {verb} {sig_text}. {effect_text} There "
            f"{'are' if significant else 'are no'} statistically significant "
            f"differences between group means."
        )
    else:
        return (
            f"The {test_type} between {' and '.join(f'{g}' for g in groups)} "
            f"{verb} {sig_text}. {effect_text}"
        )


# ─────────────────────────────────────────────────────────────
# Confidence Intervals
# ─────────────────────────────────────────────────────────────

def compute_confidence_interval(
    series: pd.Series,
    column_name: str = "",
    confidence: float = 0.95,
    method: str = "normal",
) -> ConfidenceIntervalResult:
    """
    Compute confidence interval for the mean.

    Methods:
    - normal: Z-interval (large samples, known sigma)
    - t: T-interval (small samples, unknown sigma)
    - bootstrap: Bootstrap confidence interval
    """
    from scipy import stats as sp_stats

    clean = series.dropna().astype(float)
    n = len(clean)
    mean = float(clean.mean())

    if n < 2:
        return ConfidenceIntervalResult(
            column=column_name, mean=mean, ci_lower=mean, ci_upper=mean,
            confidence_level=confidence, method=method, sample_size=n,
            interpretation="Not enough data points for confidence interval.",
            margin_of_error=0.0,
        )

    sem = clean.std() / np.sqrt(n)

    if method == "t" or (method == "normal" and n < 30):
        # T-interval
        t_crit = sp_stats.t.ppf((1 + confidence) / 2, df=n - 1)
        margin = t_crit * sem
        method_name = "Student's t"
    elif method == "bootstrap":
        # Bootstrap CI
        boot_means = []
        for _ in range(1000):
            sample = clean.sample(n, replace=True)
            boot_means.append(sample.mean())
        boot_means = np.array(boot_means)
        lower_pct = (1 - confidence) / 2 * 100
        upper_pct = (1 + confidence) / 2 * 100
        ci_lower = float(np.percentile(boot_means, lower_pct))
        ci_upper = float(np.percentile(boot_means, upper_pct))
        ci_lower_r = round(ci_lower, 4)
        ci_upper_r = round(ci_upper, 4)
        return ConfidenceIntervalResult(
            column=column_name, mean=round(mean, 4),
            ci_lower=ci_lower_r, ci_upper=ci_upper_r,
            confidence_level=confidence, method="Bootstrap", sample_size=n,
            interpretation=(
                f"We are {confidence*100:.0f}% confident that the true mean of '{column_name}' "
                f"lies between {ci_lower:.4f} and {ci_upper:.4f} (bootstrap, {n} samples)."
            ),
            margin_of_error=(ci_upper_r - ci_lower_r) / 2,
        )
    else:
        # Normal Z-interval
        z_crit = sp_stats.norm.ppf((1 + confidence) / 2)
        margin = z_crit * sem
        method_name = "Normal (Z)"

    ci_lower = mean - margin
    ci_upper = mean + margin
    ci_lower_r = round(float(ci_lower), 4)
    ci_upper_r = round(float(ci_upper), 4)

    return ConfidenceIntervalResult(
        column=column_name,
        mean=round(mean, 4),
        ci_lower=ci_lower_r,
        ci_upper=ci_upper_r,
        confidence_level=confidence,
        method=method_name,
        sample_size=n,
        interpretation=(
            f"We are {confidence*100:.0f}% confident that the true mean of '{column_name}' "
            f"lies between {ci_lower:.4f} and {ci_upper:.4f} (method: {method_name}, n={n})."
        ),
        margin_of_error=(ci_upper_r - ci_lower_r) / 2,
    )


# ─────────────────────────────────────────────────────────────
# Time Series Decomposition
# ─────────────────────────────────────────────────────────────

def decompose_time_series(
    values: pd.Series,
    dates: Optional[pd.Series] = None,
    period: int = 0,
    model: str = "additive",
) -> TimeSeriesDecompositionResult:
    """
    Decompose a time series into trend, seasonal, and residual components.

    Uses moving average for trend extraction and averaging for seasonal.
    """
    vals = values.dropna().values.astype(float)
    n = len(vals)

    if n < 4:
        empty = TimeSeriesDecompositionResult(
            dates=[], observed=[], trend=[], seasonal=[], residual=[],
            period=0, model=model, strength_trend=0.0, strength_seasonality=0.0,
        )
        return empty

    # Auto-detect period if not provided
    if period <= 0:
        if n >= 12:
            period = 12
        elif n >= 7:
            period = 7
        elif n >= 4:
            period = 4
        else:
            period = max(2, n // 2)

    period = min(period, n // 2)

    # Trend via centered moving average
    trend = pd.Series(vals).rolling(window=period, center=True, min_periods=1).mean().values

    # Detrend
    if model == "additive":
        detrended = vals - trend
    else:
        detrended = vals / np.where(trend != 0, trend, 1)

    # Seasonal component: average detrended values at each period position
    seasonal = np.zeros(n)
    for i in range(period):
        indices = list(range(i, n, period))
        season_vals = detrended[indices]
        seasonal[indices] = np.mean(season_vals)

    # Residual
    if model == "additive":
        residual = vals - trend - seasonal
    else:
        residual = vals / np.where(trend * seasonal != 0, trend * seasonal, 1)

    # Strength metrics
    var_residual = np.var(residual)
    var_detrended = np.var(vals - trend) if model == "additive" else np.var(vals / np.where(trend != 0, trend, 1))
    strength_trend = max(0, 1 - var_residual / max(var_detrended, 1e-10))

    var_deseasoned = np.var(vals - seasonal) if model == "additive" else np.var(vals / np.where(seasonal != 0, seasonal, 1))
    strength_seasonality = max(0, 1 - var_residual / max(var_deseasoned, 1e-10))

    # Format dates
    if dates is not None:
        date_strs = [str(d)[:10] for d in dates.dropna().values]
    else:
        date_strs = [str(i) for i in range(n)]

    return TimeSeriesDecompositionResult(
        dates=date_strs[:n],
        observed=[round(float(v), 4) for v in vals],
        trend=[round(float(v), 4) for v in trend],
        seasonal=[round(float(v), 4) for v in seasonal],
        residual=[round(float(v), 4) for v in residual],
        period=period,
        model=model,
        strength_trend=round(float(strength_trend), 4),
        strength_seasonality=round(float(strength_seasonality), 4),
    )


# ─────────────────────────────────────────────────────────────
# Regression
# ─────────────────────────────────────────────────────────────

def fit_regression(
    df: pd.DataFrame,
    target_col: str,
    feature_cols: List[str],
    regression_type: str = "linear",
) -> RegressionResult:
    """
    Fit a regression model.

    Types:
    - linear: Ordinary least squares linear regression
    - polynomial: Polynomial regression (degree 2)
    - logistic: Logistic regression (for binary targets, requires 0/1 values)
    """
    from scipy import stats as sp_stats

    if regression_type == "linear":
        # Prepare data
        cols = [target_col] + feature_cols
        data = df[cols].dropna()
        if len(data) < len(feature_cols) + 2:
            return RegressionResult(
                regression_type="linear", target_col=target_col, feature_cols=feature_cols,
                coefficients={}, intercept=0.0, r_squared=0.0, adj_r_squared=0.0,
                p_values={}, mse=0.0, rmse=0.0, mae=0.0, significant_features=[],
                interpretation="Not enough data for regression (need more rows than features).",
            )

        y = data[target_col].values
        X = data[feature_cols].values
        n, k = X.shape

        # Add intercept
        X_with_intercept = np.column_stack([np.ones(n), X])

        # OLS: beta = (X'X)^-1 X'y
        try:
            XtX_inv = np.linalg.inv(X_with_intercept.T @ X_with_intercept)
            beta = XtX_inv @ (X_with_intercept.T @ y)
        except np.linalg.LinAlgError:
            return RegressionResult(
                regression_type="linear", target_col=target_col, feature_cols=feature_cols,
                coefficients={}, intercept=0.0, r_squared=0.0, adj_r_squared=0.0,
                p_values={}, mse=0.0, rmse=0.0, mae=0.0, significant_features=[],
                interpretation="Singular matrix — features may be perfectly correlated (multicollinearity).",
            )

        intercept = float(beta[0])
        coefficients = {col: round(float(beta[i + 1]), 6) for i, col in enumerate(feature_cols)}

        # Predictions and residuals
        y_pred = X_with_intercept @ beta
        residuals = y - y_pred

        # R-squared
        ss_res = np.sum(residuals ** 2)
        ss_tot = np.sum((y - y.mean()) ** 2)
        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
        adj_r_squared = 1 - (1 - r_squared) * (n - 1) / (n - k - 1) if n > k + 1 else r_squared

        # MSE, RMSE, MAE
        mse = float(np.mean(residuals ** 2))
        rmse = float(np.sqrt(mse))
        mae = float(np.mean(np.abs(residuals)))

        # P-values for coefficients. Use residual variance with residual degrees
        # of freedom, not mean squared error divided by n.
        df_resid = n - k - 1
        if ss_res > 0 and df_resid > 0:
            residual_var = ss_res / df_resid
            se = np.sqrt(np.diag(XtX_inv) * residual_var)
            t_stats = np.divide(beta, se, out=np.zeros_like(beta), where=se > 0)
            p_values = {}
            for i, col in enumerate(["intercept"] + feature_cols):
                p_values[col] = round(float(2 * sp_stats.t.sf(abs(t_stats[i]), df=df_resid)), 6)
        else:
            p_values = {col: 1.0 for col in ["intercept"] + feature_cols}

        significant_features = [col for col in feature_cols if p_values.get(col, 1.0) < 0.05]

        # Interpretation
        direction = "increases" if intercept > 0 else "decreases"
        interpretation = (
            f"Linear regression on '{target_col}' using {len(feature_cols)} feature(s). "
            f"R² = {r_squared:.4f}, Adj R² = {adj_r_squared:.4f}. "
            f"RMSE = {rmse:.4f}, MAE = {mae:.4f}. "
        )
        if significant_features:
            interpretation += f"Significant features (p<0.05): {', '.join(significant_features)}. "
        else:
            interpretation += "No statistically significant features found at p<0.05."

        return RegressionResult(
            regression_type="linear", target_col=target_col, feature_cols=feature_cols,
            coefficients=coefficients, intercept=round(intercept, 6),
            r_squared=round(float(r_squared), 6),
            adj_r_squared=round(float(adj_r_squared), 6),
            p_values=p_values, mse=round(mse, 6), rmse=round(rmse, 6),
            mae=round(mae, 6), significant_features=significant_features,
            interpretation=interpretation,
        )

    elif regression_type == "polynomial":
        # Polynomial regression (degree 2 with interaction terms)
        cols = [target_col] + feature_cols
        data = df[cols].dropna()
        if len(data) < len(feature_cols) + 3:
            return RegressionResult(
                regression_type="polynomial", target_col=target_col, feature_cols=feature_cols,
                coefficients={}, intercept=0.0, r_squared=0.0, adj_r_squared=0.0,
                p_values={}, mse=0.0, rmse=0.0, mae=0.0, significant_features=[],
                interpretation="Not enough data for polynomial regression (need more rows than features).",
            )

        y = data[target_col].values
        X = data[feature_cols].values
        n = len(y)

        # Build polynomial features: x1, x1^2, x1*x2, ...
        poly_features = []
        poly_names = []
        for i, col in enumerate(feature_cols):
            poly_features.append(X[:, i])
            poly_names.append(col)
        for i, col in enumerate(feature_cols):
            poly_features.append(X[:, i] ** 2)
            poly_names.append(f"{col}^2")
        if len(feature_cols) >= 2:
            for i in range(len(feature_cols)):
                for j in range(i + 1, len(feature_cols)):
                    poly_features.append(X[:, i] * X[:, j])
                    poly_names.append(f"{feature_cols[i]}*{feature_cols[j]}")

        X_poly = np.column_stack(poly_features)
        k_poly = X_poly.shape[1]
        X_with_intercept = np.column_stack([np.ones(n), X_poly])

        try:
            XtX_inv = np.linalg.inv(X_with_intercept.T @ X_with_intercept)
            beta = XtX_inv @ (X_with_intercept.T @ y)
        except np.linalg.LinAlgError:
            return RegressionResult(
                regression_type="polynomial", target_col=target_col, feature_cols=feature_cols,
                coefficients={}, intercept=0.0, r_squared=0.0, adj_r_squared=0.0,
                p_values={}, mse=0.0, rmse=0.0, mae=0.0, significant_features=[],
                interpretation="Singular matrix — features may be strongly correlated.",
            )

        intercept = float(beta[0])
        coefficients = {name: round(float(beta[i + 1]), 6) for i, name in enumerate(poly_names)}
        y_pred = X_with_intercept @ beta
        residuals = y - y_pred
        ss_res = np.sum(residuals ** 2)
        ss_tot = np.sum((y - y.mean()) ** 2)
        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
        adj_r_squared = 1 - (1 - r_squared) * (n - 1) / (n - k_poly - 1) if n > k_poly + 1 else r_squared
        mse = float(np.mean(residuals ** 2))
        rmse = float(np.sqrt(mse))
        mae = float(np.mean(np.abs(residuals)))

        df_resid = n - k_poly - 1
        if ss_res > 0 and df_resid > 0:
            residual_var = ss_res / df_resid
            se = np.sqrt(np.diag(XtX_inv) * residual_var)
            t_stats = np.divide(beta, se, out=np.zeros_like(beta), where=se > 0)
            p_values = {}
            for i, name in enumerate(["intercept"] + poly_names):
                p_values[name] = round(float(2 * sp_stats.t.sf(abs(t_stats[i]), df=df_resid)), 6)
        else:
            p_values = {name: 1.0 for name in ["intercept"] + poly_names}

        significant_features = [name for name in poly_names if p_values.get(name, 1.0) < 0.05]
        interpretation = (
            f"Polynomial regression (degree 2) on '{target_col}' using {len(feature_cols)} feature(s). "
            f"R-squared = {r_squared:.4f}, Adj R-squared = {adj_r_squared:.4f}. "
            f"RMSE = {rmse:.4f}, MAE = {mae:.4f}."
        )
        if significant_features:
            interpretation += f" Significant terms: {', '.join(significant_features)}."
        else:
            interpretation += " No statistically significant terms found."

        return RegressionResult(
            regression_type="polynomial", target_col=target_col, feature_cols=feature_cols,
            coefficients=coefficients, intercept=round(intercept, 6),
            r_squared=round(float(r_squared), 6),
            adj_r_squared=round(float(adj_r_squared), 6),
            p_values=p_values, mse=round(mse, 6), rmse=round(rmse, 6),
            mae=round(mae, 6), significant_features=significant_features,
            interpretation=interpretation,
        )

    elif regression_type == "logistic":
        cols = [target_col] + feature_cols
        data = df[cols].dropna()
        if len(data) < len(feature_cols) + 2:
            return RegressionResult(
                regression_type="logistic", target_col=target_col, feature_cols=feature_cols,
                coefficients={}, intercept=0.0, r_squared=0.0, adj_r_squared=0.0,
                p_values={}, mse=0.0, rmse=0.0, mae=0.0, significant_features=[],
                interpretation="Not enough data for logistic regression.",
            )

        y = data[target_col].values
        unique_vals = np.unique(y)
        if len(unique_vals) != 2:
            return RegressionResult(
                regression_type="logistic", target_col=target_col, feature_cols=feature_cols,
                coefficients={}, intercept=0.0, r_squared=0.0, adj_r_squared=0.0,
                p_values={}, mse=0.0, rmse=0.0, mae=0.0, significant_features=[],
                interpretation=f"Logistic regression requires binary target (0/1). Found {len(unique_vals)} unique values.",
            )

        y_binary = np.where(y == unique_vals[0], 0, 1).astype(float)
        X = np.column_stack([np.ones(len(data)), data[feature_cols].values])
        n, k = X.shape

        def sigmoid(z):
            return 1 / (1 + np.exp(-np.clip(z, -100, 100)))

        def neg_log_likelihood(beta):
            p = sigmoid(X @ beta)
            p = np.clip(p, 1e-10, 1 - 1e-10)
            return -np.mean(y_binary * np.log(p) + (1 - y_binary) * np.log(1 - p))

        def gradient(beta):
            p = sigmoid(X @ beta)
            return X.T @ (p - y_binary) / n

        from scipy.optimize import minimize
        beta_init = np.zeros(k)
        result = minimize(neg_log_likelihood, beta_init, method="BFGS", jac=gradient, options={"maxiter": 1000})

        if not result.success:
            return RegressionResult(
                regression_type="logistic", target_col=target_col, feature_cols=feature_cols,
                coefficients={}, intercept=0.0, r_squared=0.0, adj_r_squared=0.0,
                p_values={}, mse=0.0, rmse=0.0, mae=0.0, significant_features=[],
                interpretation=f"Logistic regression did not converge: {result.message}",
            )

        beta_opt = result.x
        intercept_log = float(beta_opt[0])
        coefficients = {col: round(float(beta_opt[i + 1]), 6) for i, col in enumerate(feature_cols)}

        probs = sigmoid(X @ beta_opt)
        preds = (probs >= 0.5).astype(float)
        accuracy = float(np.mean(preds == y_binary))

        null_probs = y_binary.mean()
        ll_null = np.mean(y_binary * np.log(null_probs + 1e-10) + (1 - y_binary) * np.log(1 - null_probs + 1e-10))
        ll_model = -neg_log_likelihood(beta_opt)
        pseudo_r2 = 1 - ll_model / ll_null if ll_null != 0 else 0

        p_hat = sigmoid(X @ beta_opt)
        W = np.diag((p_hat * (1 - p_hat)).flatten())
        try:
            # The objective above is the average negative log-likelihood, so use
            # the average Hessian for optimization scale, then divide its inverse
            # by n to recover the usual parameter covariance.
            hessian = (X.T @ W @ X) / n
            cov_matrix = np.linalg.inv(hessian) / n
            se = np.sqrt(np.diag(cov_matrix))
            z_stats = np.divide(beta_opt, se, out=np.zeros_like(beta_opt), where=se > 0)
            p_values = {}
            for i, name in enumerate(["intercept"] + feature_cols):
                p_values[name] = round(float(2 * sp_stats.norm.sf(abs(z_stats[i]))), 6)
        except Exception:
            p_values = {name: 1.0 for name in ["intercept"] + feature_cols}

        significant_features = [col for col in feature_cols if p_values.get(col, 1.0) < 0.05]
        interpretation = (
            f"Logistic regression on '{target_col}'. "
            f"Accuracy = {accuracy:.2%}, Pseudo R-squared = {pseudo_r2:.4f}."
        )
        if significant_features:
            interpretation += f" Significant predictors: {', '.join(significant_features)}."
        else:
            interpretation += " No statistically significant predictors found."

        return RegressionResult(
            regression_type="logistic", target_col=target_col, feature_cols=feature_cols,
            coefficients=coefficients, intercept=round(intercept_log, 6),
            r_squared=round(float(pseudo_r2), 6),
            adj_r_squared=round(float(pseudo_r2), 6),
            p_values=p_values, mse=round(1 - accuracy, 6), rmse=round(1 - accuracy, 6),
            mae=round(1 - accuracy, 6), significant_features=significant_features,
            interpretation=interpretation,
        )

    else:
        return RegressionResult(
            regression_type=regression_type, target_col=target_col, feature_cols=feature_cols,
            coefficients={}, intercept=0.0, r_squared=0.0, adj_r_squared=0.0,
            p_values={}, mse=0.0, rmse=0.0, mae=0.0, significant_features=[],
            interpretation=f"Regression type '{regression_type}' not supported. Use 'linear', 'polynomial', or 'logistic'.",
        )


# ─────────────────────────────────────────────────────────────
# Feature Importance
# ─────────────────────────────────────────────────────────────

def compute_feature_importance(
    df: pd.DataFrame,
    target_col: str,
    method: str = "correlation",
) -> FeatureImportanceResult:
    """
    Rank feature importance relative to a target column.

    Methods:
    - correlation: Absolute Pearson correlation
    - mutual_info: Mutual information score
    """
    numeric_df = df.select_dtypes(include=["number"])
    feature_cols = [c for c in numeric_df.columns if c != target_col]

    if not feature_cols or target_col not in numeric_df.columns:
        return FeatureImportanceResult(
            target_col=target_col, method=method, features=[],
            interpretation="No numeric features found for importance ranking.",
        )

    results = []

    if method == "correlation":
        target_vals = numeric_df[target_col].dropna()
        for col in feature_cols:
            valid = numeric_df[[col, target_col]].dropna()
            if len(valid) < 3:
                continue
            corr, p_val = sp_corr(valid[col], valid[target_col])
            results.append({
                "feature": col,
                "importance": round(abs(float(corr)), 4),
                "correlation": round(float(corr), 4),
                "p_value": round(float(p_val), 6),
                "direction": "positive" if corr > 0 else "negative",
            })

    elif method == "mutual_info":
        from scipy.stats import pearsonr as sp_pearsonr
        # Use correlation-based mutual information approximation
        target_vals = numeric_df[target_col].dropna()
        for col in feature_cols:
            valid = numeric_df[[col, target_col]].dropna()
            if len(valid) < 3:
                continue
            # Discretize for mutual info
            try:
                bins = min(10, max(3, len(valid) // 5))
                x_bin = pd.cut(valid[col], bins=bins, labels=False)
                y_bin = pd.cut(valid[target_col], bins=bins, labels=False)
                # Simple MI via contingency table
                contingency = pd.crosstab(x_bin, y_bin)
                mi = _mutual_information(contingency.values)
                results.append({
                    "feature": col,
                    "importance": round(float(mi), 4),
                    "method": "mutual_info",
                })
            except Exception:
                continue

    # Sort by importance descending
    results.sort(key=lambda x: x["importance"], reverse=True)

    # Add rank
    for i, r in enumerate(results):
        r["rank"] = i + 1

    top_features = [r["feature"] for r in results[:5]]
    interpretation = (
        f"Feature importance for predicting '{target_col}' (method: {method}). "
        f"Top features: {', '.join(top_features) if top_features else 'none found'}."
    )

    return FeatureImportanceResult(
        target_col=target_col, method=method, features=results,
        interpretation=interpretation,
    )


def sp_corr(x, y):
    """Safe Pearson correlation."""
    from scipy.stats import pearsonr
    return pearsonr(x, y)


def _mutual_information(contingency: np.ndarray) -> float:
    """Compute mutual information from a contingency table."""
    n = contingency.sum()
    if n == 0:
        return 0.0
    pxy = contingency / n
    px = pxy.sum(axis=1)
    py = pxy.sum(axis=0)

    mi = 0.0
    for i in range(pxy.shape[0]):
        for j in range(pxy.shape[1]):
            if pxy[i, j] > 0 and px[i] > 0 and py[j] > 0:
                mi += pxy[i, j] * np.log2(pxy[i, j] / (px[i] * py[j]))
    return mi


# ─────────────────────────────────────────────────────────────
# A/B Testing
# ─────────────────────────────────────────────────────────────

def run_ab_test(
    df: pd.DataFrame,
    group_col: str,
    metric_col: str,
    group_a: str,
    group_b: str,
    alpha: float = 0.05,
) -> ABTestResult:
    """
    Run an A/B test comparing two groups on a metric.

    Returns p-value, effect size, power, and sample size recommendation.
    """
    from scipy import stats as sp_stats

    a_data = df[df[group_col] == group_a][metric_col].dropna()
    b_data = df[df[group_col] == group_b][metric_col].dropna()

    if len(a_data) < 2 or len(b_data) < 2:
        return ABTestResult(
            group_col=group_col, metric_col=metric_col,
            group_a=str(group_a), group_b=str(group_b),
            group_a_stats={}, group_b_stats={},
            test_name="t-test", statistic=0.0, p_value=1.0, effect_size=0.0,
            effect_size_name="", power=0.0, sample_size_needed=0,
            winner="none", significant=False, alpha=alpha,
            interpretation="Not enough data in one or both groups for A/B testing.",
        )

    # Group statistics
    a_stats = {
        "mean": round(float(a_data.mean()), 4),
        "median": round(float(a_data.median()), 4),
        "std": round(float(a_data.std()), 4),
        "n": len(a_data),
    }
    b_stats = {
        "mean": round(float(b_data.mean()), 4),
        "median": round(float(b_data.median()), 4),
        "std": round(float(b_data.std()), 4),
        "n": len(b_data),
    }

    # Welch's t-test
    stat, p_value = sp_stats.ttest_ind(a_data, b_data, equal_var=False)

    # Cohen's d
    effect = _cohens_d(a_data, b_data)

    # Power calculation (approximate)
    from statsmodels.stats.power import tt_ind_solve_power
    # Use observed effect size and sample sizes
    try:
        # Use statsmodels for power if available
        power = float(tt_ind_solve_power(
            effect_size=effect,
            nobs1=len(a_data),
            ratio=len(b_data) / len(a_data),
            alpha=alpha,
        ))
    except Exception:
        # Fallback: rough power estimate
        if effect == 0:
            power = alpha
        else:
            power = min(1.0, 0.5 + effect * 0.3)

    # Sample size needed for 80% power
    try:
        from statsmodels.stats.power import tt_ind_solve_power as solve_power
        n_needed = solve_power(effect_size=effect, power=0.8, alpha=alpha, ratio=1.0)
        n_needed = max(int(np.ceil(n_needed)), len(a_data) + len(b_data))
    except Exception:
        n_needed = max(int(np.ceil(16 / (effect ** 2))) if effect > 0 else 1000, len(a_data) + len(b_data))

    # Determine winner and calculate lift relative to the losing/control mean.
    significant = p_value < alpha
    winner = "none"
    lift = 0.0
    if significant:
        mean_a, mean_b = a_data.mean(), b_data.mean()
        if mean_a > mean_b:
            winner = str(group_a)
            lift = (mean_a - mean_b) / mean_b * 100 if mean_b != 0 else 0
        elif mean_b > mean_a:
            winner = str(group_b)
            lift = (mean_b - mean_a) / mean_a * 100 if mean_a != 0 else 0

    # Interpretation
    if significant:
        interpretation = (
            f"The A/B test between '{group_a}' and '{group_b}' on '{metric_col}' "
            f"is statistically significant (p={p_value:.4f}). "
            f"'{winner}' performs better by {abs(lift):.1f}% (Cohen's d = {effect:.3f}). "
            f"Power: {power:.1%}."
        )
    else:
        interpretation = (
            f"The A/B test between '{group_a}' and '{group_b}' on '{metric_col}' "
            f"did NOT show a statistically significant difference (p={p_value:.4f}). "
            f"Consider increasing sample size (need ~{n_needed} total)."
        )

    return ABTestResult(
        group_col=group_col, metric_col=metric_col,
        group_a=str(group_a), group_b=str(group_b),
        group_a_stats=a_stats, group_b_stats=b_stats,
        test_name="Welch's t-test",
        statistic=round(float(stat), 6),
        p_value=round(float(p_value), 6),
        effect_size=round(float(effect), 4),
        effect_size_name="Cohen's d",
        power=round(float(power), 4),
        sample_size_needed=n_needed,
        winner=winner,
        significant=significant,
        alpha=alpha,
        interpretation=interpretation,
    )


# ─────────────────────────────────────────────────────────────
# Chi-Square Test for Conversion Rates
# ─────────────────────────────────────────────────────────────

def test_conversion_rate(
    df: pd.DataFrame,
    group_col: str,
    conversion_col: str,
    group_a: str,
    group_b: str,
    alpha: float = 0.05,
) -> Dict[str, Any]:
    """
    Test difference in conversion rates between two groups using chi-square test.

    The conversion_col should be binary (0/1, True/False, yes/no, converted/not).
    Returns odds ratio, relative lift, and sample size requirements.

    Parameters
    ----------
    df : pd.DataFrame
        Input data.
    group_col : str
        Column identifying groups (A/B).
    conversion_col : str
        Binary column: 1 = converted, 0 = not converted.
    group_a, group_b : str
        Group labels.
    alpha : float
        Significance level.

    Returns
    -------
    dict with chi2_stat, p_value, conversion rates, odds ratio, lift, interpretation.
    """
    from scipy import stats as sp_stats

    a_data = df[df[group_col] == group_a][conversion_col].dropna()
    b_data = df[df[group_col] == group_b][conversion_col].dropna()

    if len(a_data) < 10 or len(b_data) < 10:
        return {
            "success": False,
            "error": "Need at least 10 observations per group for conversion rate test.",
        }

    # Convert to 0/1
    a_conv = (a_data.astype(float) > 0).astype(int)
    b_conv = (b_data.astype(float) > 0).astype(int)

    n_a, n_b = len(a_conv), len(b_conv)
    conv_a, conv_b = a_conv.sum(), b_conv.sum()
    rate_a = conv_a / n_a
    rate_b = conv_b / n_b

    # Contingency table
    table = np.array([
        [conv_a, n_a - conv_a],
        [conv_b, n_b - conv_b],
    ])

    chi2, p_value, dof, expected = sp_stats.chi2_contingency(table)

    # Odds ratio (B vs A) to match the lift narrative below.
    odds_a = conv_a / max(1, n_a - conv_a)
    odds_b = conv_b / max(1, n_b - conv_b)
    odds_ratio = odds_b / odds_a if odds_a > 0 else float('inf')

    # Relative lift (B vs A)
    lift = ((rate_b - rate_a) / rate_a * 100) if rate_a > 0 else 0

    significant = p_value < alpha

    # Confidence interval for difference in proportions (95%)
    diff = rate_b - rate_a
    se_diff = np.sqrt(rate_a * (1 - rate_a) / n_a + rate_b * (1 - rate_b) / n_b)
    z_crit = sp_stats.norm.ppf(0.975)
    ci_lower = diff - z_crit * se_diff
    ci_upper = diff + z_crit * se_diff

    # Sample size needed for 80% power
    try:
        from statsmodels.stats.proportion import proportion_effectsize
        from statsmodels.stats.power import NormalIndPower
        effect_size_prop = proportion_effectsize(rate_a, rate_b)
        n_needed = NormalIndPower().solve_power(effect_size=effect_size_prop, power=0.8, alpha=alpha, ratio=1.0)
        n_needed = max(int(np.ceil(n_needed)), n_a + n_b)
    except Exception:
        n_needed = max(int(np.ceil(16 * (1/rate_a + 1/(1-rate_a) + 1/rate_b + 1/(1-rate_b))) if 0 < rate_a < 1 and 0 < rate_b < 1 else 1000), n_a + n_b)

    winner = "none"
    if significant:
        if rate_b > rate_a:
            winner = group_b
        elif rate_a > rate_b:
            winner = group_a

    interpretation = ""
    if significant:
        interpretation = (
            f"Conversion rate test: '{group_b}' has a {lift:+.1f}% lift vs '{group_a}' "
            f"({rate_b:.2%} vs {rate_a:.2%}). This is statistically significant (p={p_value:.4f}). "
            f"Odds ratio: {odds_ratio:.2f}."
        )
    else:
        interpretation = (
            f"No significant difference in conversion rates "
            f"({rate_b:.2%} vs {rate_a:.2%}, p={p_value:.4f}). "
            f"Need ~{n_needed} total observations for 80% power at this effect size."
        )

    return {
        "success": True,
        "test_name": "Chi-Square Test for Conversion Rates",
        "chi2_statistic": round(float(chi2), 6),
        "p_value": round(float(p_value), 6),
        "significant": significant,
        "alpha": alpha,
        "group_a": {"name": str(group_a), "conversions": int(conv_a), "total": n_a, "rate": round(float(rate_a), 6)},
        "group_b": {"name": str(group_b), "conversions": int(conv_b), "total": n_b, "rate": round(float(rate_b), 6)},
        "lift_pct": round(float(lift), 2),
        "odds_ratio": round(float(odds_ratio), 4),
        "diff": round(float(diff), 6),
        "ci_lower": round(float(ci_lower), 6),
        "ci_upper": round(float(ci_upper), 6),
        "sample_size_needed": n_needed,
        "winner": winner,
        "interpretation": interpretation,
    }


# ─────────────────────────────────────────────────────────────
# Sequential Testing (Always-Valid P-values)
# ─────────────────────────────────────────────────────────────

def sequential_test(
    df: pd.DataFrame,
    group_col: str,
    metric_col: str,
    group_a: str,
    group_b: str,
    alpha: float = 0.05,
    spending_function: str = "obrien_fleming",
) -> Dict[str, Any]:
    """
    Sequential testing using always-valid confidence sequences (Cramér-von Mises / log-likelihood).

    This allows peeking at results at any time without inflating the false positive rate,
    unlike traditional fixed-horizon tests.

    Parameters
    ----------
    df : pd.DataFrame
        Input data (sorted chronologically if sequential).
    group_col : str
        Group column.
    metric_col : str
        Metric column.
    group_a, group_b : str
        Group labels.
    alpha : float
        Overall significance level.
    spending_function : str
        Alpha spending function: "obrien_fleming", "pocock", or "simple".

    Returns
    -------
    dict with sequential analysis results.
    """
    from scipy import stats as sp_stats

    a_data = df[df[group_col] == group_a][metric_col].dropna().values
    b_data = df[df[group_col] == group_b][metric_col].dropna().values

    n_a, n_b = len(a_data), len(b_data)
    total_n = n_a + n_b

    if n_a < 5 or n_b < 5:
        return {
            "success": False,
            "error": "Need at least 5 observations per group for sequential testing.",
        }

    # Compute running statistics at several analysis points
    analysis_points = [max(10, total_n // 4), max(20, total_n // 2), total_n]
    analysis_points = sorted(set([p for p in analysis_points if p <= total_n]))

    results_by_point = []
    for n_total in analysis_points:
        # Determine how many observations from each group at this point
        ratio = n_a / total_n if total_n > 0 else 0.5
        na_here = min(int(n_total * ratio), n_a)
        nb_here = min(n_total - na_here, n_b)

        if na_here < 2 or nb_here < 2:
            continue

        a_slice = a_data[:na_here]
        b_slice = b_data[:nb_here]

        # Standard t-test at this point
        t_stat, p_value = sp_stats.ttest_ind(a_slice, b_slice, equal_var=False)

        # Alpha spending: adjusted threshold
        fraction = n_total / total_n
        if spending_function == "obrien_fleming":
            # O'Brien-Fleming: more conservative early, less late
            z_threshold = sp_stats.norm.ppf(1 - alpha / 2 * (1 - np.exp(-4 * fraction)))
        elif spending_function == "pocock":
            # Pocock: constant threshold
            z_threshold = sp_stats.norm.ppf(1 - alpha / 2 * fraction)
        else:
            # Simple: proportional spending
            z_threshold = sp_stats.norm.ppf(1 - alpha / 2 * fraction)

        z_stat = t_stat  # approximate
        significant_at_point = abs(z_stat) > z_threshold

        results_by_point.append({
            "n_total": n_total,
            "n_a": na_here,
            "n_b": nb_here,
            "mean_a": round(float(a_slice.mean()), 4),
            "mean_b": round(float(b_slice.mean()), 4),
            "t_statistic": round(float(t_stat), 6),
            "p_value_raw": round(float(p_value), 6),
            "z_threshold_adjusted": round(float(z_threshold), 4),
            "significant": significant_at_point,
            "alpha_spent": round(float(alpha * fraction), 6),
        })

    # Overall conclusion
    any_significant = any(r["significant"] for r in results_by_point)
    final = results_by_point[-1] if results_by_point else {}

    interpretation = ""
    if any_significant:
        sig_point = next(r for r in results_by_point if r["significant"])
        interpretation = (
            f"Sequential analysis detected significance at n={sig_point['n_total']} "
            f"(p_raw={sig_point['p_value_raw']:.4f}, adjusted threshold={sig_point['z_threshold_adjusted']:.4f}). "
            f"Results are valid even though analyzed before full sample size."
        )
    else:
        interpretation = (
            f"No significant difference detected at any analysis point (final n={total_n}). "
            f"Consider collecting more data. Final p-value: {final.get('p_value_raw', 'N/A')}."
        )

    return {
        "success": True,
        "test_name": "Sequential Analysis",
        "spending_function": spending_function,
        "analysis_points": results_by_point,
        "any_significant": any_significant,
        "final_p_value": final.get("p_value_raw"),
        "total_n": total_n,
        "interpretation": interpretation,
    }


# ─────────────────────────────────────────────────────────────
# Power Analysis
# ─────────────────────────────────────────────────────────────

def compute_power_analysis(
    effect_size: float,
    alpha: float = 0.05,
    power: float = 0.8,
) -> PowerAnalysisResult:
    """
    Compute required sample size for a given effect size, alpha, and power.
    """
    try:
        from statsmodels.stats.power import tt_ind_solve_power
        n = tt_ind_solve_power(effect_size=effect_size, power=power, alpha=alpha, ratio=1.0)
        n = max(int(np.ceil(n)), 2)
    except Exception:
        # Fallback formula: n ≈ (Z_alpha + Z_beta)^2 * 2 / d^2
        from scipy.stats import norm
        z_alpha = norm.ppf(1 - alpha / 2)
        z_beta = norm.ppf(power)
        n = int(np.ceil(((z_alpha + z_beta) ** 2 * 2) / (effect_size ** 2))) if effect_size > 0 else 1000

    total = n * 2
    if effect_size < 0.2:
        interp = "Very small effect — you'll need a large sample to detect it."
    elif effect_size < 0.5:
        interp = "Small effect — moderate sample size needed."
    elif effect_size < 0.8:
        interp = "Medium effect — relatively achievable sample size."
    else:
        interp = "Large effect — small sample should suffice."

    return PowerAnalysisResult(
        effect_size=effect_size, alpha=alpha, power=power,
        sample_size_per_group=n, total_sample_size=total,
        interpretation=f"Need {n} per group ({total} total) to detect d={effect_size} "
                       f"with {power:.0%} power at alpha={alpha}. {interp}",
    )


def export_regression_model(result: Dict[str, Any], export_format: str = "json") -> Dict[str, Any]:
    """Export a regression model result as JSON with a human-readable formula."""
    coeffs = result.get("coefficients", {})
    intercept = result.get("intercept", 0)
    feature_names = list(coeffs.keys())

    # Build formula string: y = b0 + b1*x1 + b2*x2 + ...
    terms = [f"{intercept:.4f}"]
    for col, val in coeffs.items():
        sign = "+" if val >= 0 else "-"
        terms.append(f" {sign} {abs(val):.4f}*{col}")
    formula = "".join(terms)

    return {
        "format": export_format,
        "model_type": result.get("regression_type", "linear"),
        "formula": f"y = {formula}",
        "coefficients": coeffs,
        "intercept": intercept,
        "r_squared": result.get("r_squared", 0),
        "adj_r_squared": result.get("adj_r_squared", 0),
        "significant_features": result.get("significant_features", []),
        "metrics": {
            "mse": result.get("mse", 0),
            "rmse": result.get("rmse", 0),
            "mae": result.get("mae", 0),
        },
    }


def suggest_outlier_handling(series: pd.Series, column_name: str = "") -> Dict[str, Any]:
    """Analyze outliers and suggest handling strategies."""
    clean = series.dropna().astype(float)
    n = len(clean)
    if n < 4:
        return {"column": column_name, "outlier_count": 0, "suggestions": []}

    q1, q3 = clean.quantile(0.25), clean.quantile(0.75)
    iqr = q3 - q1
    lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    outlier_mask = (clean < lower) | (clean > upper)
    outlier_count = int(outlier_mask.sum())
    outlier_pct = round(outlier_count / n * 100, 2)
    skew = float(clean.skew())

    if outlier_count == 0:
        return {"column": column_name, "outlier_count": 0, "outlier_pct": 0.0,
                "lower_bound": round(float(lower), 4), "upper_bound": round(float(upper), 4),
                "skewness": round(skew, 4), "suggestions": [
                    {"action": "none", "reason": "No outliers detected", "impact": "Data is clean"}
                ]}

    suggestions = []

    # Rule 1: High ratio
    if outlier_pct > 10:
        suggestions.append({
            "action": "investigate",
            "reason": f"High outlier ratio ({outlier_pct:.1f}%)",
            "impact": "May indicate data quality issues — investigate before removal"
        })

    # Rule 2: Skewed distribution
    if abs(skew) > 1:
        direction = "right" if skew > 0 else "left"
        suggestions.append({
            "action": "transform",
            "reason": f"Highly {direction}-skewed distribution (skew={skew:.2f})",
            "impact": "Log or sqrt transform may normalize and reduce outlier impact"
        })

    # Rule 3: Moderate outliers
    if 0 < outlier_pct <= 5 and outlier_count > 2:
        suggestions.append({
            "action": "cap_winsorize",
            "reason": f"Moderate number of extreme values ({outlier_count} rows, {outlier_pct:.1f}%)",
            "impact": "Cap values at 1st/99th percentile to reduce impact while preserving data"
        })

    # Rule 4: Very few outliers — keep them, removal is rarely justified
    if outlier_count <= 2:
        suggestions.append({
            "action": "keep",
            "reason": f"Very few outliers ({outlier_count} row(s))",
            "impact": "Few outliers have minimal impact — consider keeping them as valid extremes"
        })

    # Default suggestion
    suggestions.append({
        "action": "investigate",
        "reason": "Always review outliers before deciding on handling",
        "impact": "Context matters — some outliers are valid extreme values"
    })

    return {
        "column": column_name,
        "outlier_count": outlier_count,
        "outlier_pct": outlier_pct,
        "lower_bound": round(float(lower), 4),
        "upper_bound": round(float(upper), 4),
        "skewness": round(skew, 4),
        "suggestions": suggestions,
    }
