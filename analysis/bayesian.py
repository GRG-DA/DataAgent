"""
Bayesian Inference Module
Bayesian two-sample t-test, Bayesian correlation with Fisher z-transform,
posterior summaries, credible intervals, and Bayes factor computation.
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
class BayesianTTestResult:
    """Result of a Bayesian two-sample t-test."""
    test_name: str
    group_a: str
    group_b: str
    group_a_mean: float
    group_b_mean: float
    group_a_std: float
    group_b_std: float
    group_a_n: int
    group_b_n: int
    posterior_mean: float
    posterior_std: float
    credible_interval_lower: float
    credible_interval_upper: float
    confidence_level: float
    bayes_factor: float
    bayes_factor_interpretation: str
    probability_a_greater_b: float
    effect_size: float
    interpretation: str


@dataclass
class BayesianCorrelationResult:
    """Result of a Bayesian correlation analysis."""
    column_a: str
    column_b: str
    n: int
    sample_correlation: float
    fisher_z: float
    posterior_z_mean: float
    posterior_z_std: float
    posterior_correlation_mean: float
    posterior_correlation_std: float
    credible_interval_lower: float
    credible_interval_upper: float
    confidence_level: float
    bayes_factor: float
    bayes_factor_interpretation: str
    probability_positive: float
    interpretation: str


# ─────────────────────────────────────────────────────────────
# Prior defaults
# ─────────────────────────────────────────────────────────────

# Default prior for mean difference: N(0, Jeffreys-scale)
# We use a weakly informative prior with scale proportional to
# the pooled standard deviation (Cauchy prior on effect size,
# approximated as Normal for conjugate computation).

JEFFREYSCALE_CAUCHY_PRIOR_SCALE = 1 / np.sqrt(2)  # ~0.707, classic Jeffreys


# ─────────────────────────────────────────────────────────────
# Bayesian Two-Sample T-Test
# ─────────────────────────────────────────────────────────────

def bayesian_ttest(
    df: pd.DataFrame,
    column_a: str,
    column_b: str = "",
    group_col: str = "",
    prior_scale: Optional[float] = None,
    confidence: float = 0.95,
) -> BayesianTTestResult:
    """
    Perform a Bayesian two-sample t-test using conjugate Normal-Inverse-Gamma priors.

    The posterior for the difference in means is approximated as:
        delta = mean_a - mean_b
        posterior_std ~ sqrt(var_a/n_a + var_b/n_b + prior_scale^2)

    The Bayes factor is computed via the Savage-Dickey density ratio
    (ratio of posterior to prior density at delta=0).

    Parameters
    ----------
    df : pd.DataFrame
        Data containing the groups.
    column_a : str
        Metric column name (or first group column name).
    column_b : str
        Second group column name (used when group_col is not provided).
    group_col : str
        Column that defines the two groups (first two unique values).
    prior_scale : float, optional
        Scale of the prior on the mean difference.  If None, defaults to
        Jeffreys' scale times the pooled standard deviation.
    confidence : float
        Credible interval level (default 0.95).

    Returns
    -------
    BayesianTTestResult
        Posterior mean/std, credible interval, Bayes factor, P(delta > 0).
    """
    from scipy import stats as sp_stats

    # Extract the two groups
    if group_col and column_a:
        groups = df[group_col].dropna().unique()
        if len(groups) < 2:
            return _bttest_error(f"Need at least 2 groups in '{group_col}'.")
        g1 = df[df[group_col] == groups[0]][column_a].dropna().astype(float)
        g2 = df[df[group_col] == groups[1]][column_a].dropna().astype(float)
        label_a = str(groups[0])
        label_b = str(groups[1])
    elif column_a and column_b:
        g1 = df[column_a].dropna().astype(float)
        g2 = df[column_b].dropna().astype(float)
        label_a = column_a
        label_b = column_b
    else:
        return _bttest_error("Provide group_col + column_a, or column_a + column_b.")

    n_a, n_b = len(g1), len(g2)
    if n_a < 2 or n_b < 2:
        return _bttest_error("Each group must have at least 2 observations.")

    mean_a = float(g1.mean())
    mean_b = float(g2.mean())
    std_a = float(g1.std(ddof=1))
    std_b = float(g2.std(ddof=1))

    # Observed difference
    delta_obs = mean_a - mean_b

    # Pooled standard deviation for prior scale
    pooled_std = np.sqrt(((n_a - 1) * std_a ** 2 + (n_b - 1) * std_b ** 2) / (n_a + n_b - 2))
    if prior_scale is None:
        prior_scale = JEFFREYSCALE_CAUCHY_PRIOR_SCALE * pooled_std

    # Posterior approximation for delta (normal approximation to posterior)
    # Sampling variance of the difference
    sampling_var = std_a ** 2 / n_a + std_b ** 2 / n_b

    # Posterior variance (precision = 1/sampling_var + 1/prior_scale^2)
    posterior_precision = 1 / sampling_var + 1 / prior_scale ** 2
    posterior_var = 1 / posterior_precision

    # Posterior mean: precision-weighted average of data and prior (prior mean = 0)
    posterior_mean = (delta_obs / sampling_var) / posterior_precision
    posterior_std = np.sqrt(posterior_var)

    # Credible interval
    z_crit = sp_stats.norm.ppf((1 + confidence) / 2)
    ci_lower = posterior_mean - z_crit * posterior_std
    ci_upper = posterior_mean + z_crit * posterior_std

    # P(delta > 0) under the posterior
    prob_a_greater_b = float(sp_stats.norm.sf(0, loc=posterior_mean, scale=posterior_std))

    # Bayes factor (Savage-Dickey ratio at delta = 0)
    # BF_10 = p(data | H1) / p(data | H0) approx = posterior_density(0) / prior_density(0)
    # For normal posterior and Cauchy prior this simplifies to:
    prior_density_at_0 = sp_stats.norm.pdf(0, loc=0, scale=prior_scale)
    posterior_density_at_0 = sp_stats.norm.pdf(0, loc=posterior_mean, scale=posterior_std)
    bayes_factor = posterior_density_at_0 / prior_density_at_0 if prior_density_at_0 > 0 else 1.0

    bf_interp = _interpret_bayes_factor(bayes_factor)

    # Effect size (posterior standardized difference)
    effect_size = posterior_mean / pooled_std if pooled_std > 0 else 0.0

    # Interpretation
    interpretation = (
        f"Bayesian t-test comparing '{label_a}' (M={mean_a:.4f}, SD={std_a:.4f}, n={n_a}) "
        f"vs '{label_b}' (M={mean_b:.4f}, SD={std_b:.4f}, n={n_b}). "
        f"Posterior mean difference: {posterior_mean:.4f} (95% CI: [{ci_lower:.4f}, {ci_upper:.4f}]). "
        f"P({label_a} > {label_b}) = {prob_a_greater_b:.4f}. "
        f"Bayes factor (BF10): {bayes_factor:.2f} -- {bf_interp}."
    )

    return BayesianTTestResult(
        test_name="Bayesian Two-Sample T-Test",
        group_a=label_a,
        group_b=label_b,
        group_a_mean=round(mean_a, 6),
        group_b_mean=round(mean_b, 6),
        group_a_std=round(std_a, 6),
        group_b_std=round(std_b, 6),
        group_a_n=n_a,
        group_b_n=n_b,
        posterior_mean=round(float(posterior_mean), 6),
        posterior_std=round(float(posterior_std), 6),
        credible_interval_lower=round(float(ci_lower), 6),
        credible_interval_upper=round(float(ci_upper), 6),
        confidence_level=confidence,
        bayes_factor=round(float(bayes_factor), 4),
        bayes_factor_interpretation=bf_interp,
        probability_a_greater_b=round(float(prob_a_greater_b), 6),
        effect_size=round(float(effect_size), 4),
        interpretation=interpretation,
    )


def _bttest_error(reason: str) -> BayesianTTestResult:
    """Return a BayesianTTestResult indicating an error."""
    return BayesianTTestResult(
        test_name="Bayesian Two-Sample T-Test",
        group_a="", group_b="",
        group_a_mean=0.0, group_b_mean=0.0,
        group_a_std=0.0, group_b_std=0.0,
        group_a_n=0, group_b_n=0,
        posterior_mean=0.0, posterior_std=0.0,
        credible_interval_lower=0.0, credible_interval_upper=0.0,
        confidence_level=0.95, bayes_factor=1.0,
        bayes_factor_interpretation="inconclusive",
        probability_a_greater_b=0.5, effect_size=0.0,
        interpretation=f"Error: {reason}",
    )


# ─────────────────────────────────────────────────────────────
# Bayesian Correlation
# ─────────────────────────────────────────────────────────────

def bayesian_correlation(
    df: pd.DataFrame,
    column_a: str,
    column_b: str,
    prior_z_std: float = 1.0,
    confidence: float = 0.95,
) -> BayesianCorrelationResult:
    """
    Compute a Bayesian correlation using the Fisher z-transform.

    Under the Fisher z-transform, the sampling distribution of z = arctanh(r)
    is approximately Normal with mean z_true and variance 1/(n-3).
    Using a Normal prior on z_true with standard deviation `prior_z_std`,
    the posterior is also Normal (conjugate).

    The posterior mean/std on the correlation scale is obtained by inverse
    Fisher z-transform of the posterior mean and credible bounds.

    Parameters
    ----------
    df : pd.DataFrame
        Data with the two columns.
    column_a : str
        First variable.
    column_b : str
        Second variable.
    prior_z_std : float
        Prior standard deviation on the Fisher z scale (default 1.0,
        representing a weakly informative prior).
    confidence : float
        Credible interval level (default 0.95).

    Returns
    -------
    BayesianCorrelationResult
        Posterior correlation mean/std, credible interval, Bayes factor,
        P(r > 0), and interpretation.
    """
    from scipy import stats as sp_stats

    valid = df[[column_a, column_b]].dropna()
    n = len(valid)
    if n < 3:
        return _bcorr_error(column_a, column_b, "Need at least 3 paired observations.")

    x = valid[column_a].astype(float).values
    y = valid[column_b].astype(float).values

    # Sample Pearson correlation
    sample_corr, _ = sp_stats.pearsonr(x, y)
    sample_corr = float(sample_corr)

    # Handle extreme correlations
    r_clamp = np.clip(sample_corr, -0.999, 0.999)

    # Fisher z-transform
    fisher_z = float(np.arctanh(r_clamp))

    # Sampling variance on the z scale
    sampling_var_z = 1 / (n - 3) if n > 3 else 1e6

    # Prior: Normal(0, prior_z_std^2)
    prior_var_z = prior_z_std ** 2

    # Posterior (Normal-Normal conjugate)
    posterior_precision = 1 / sampling_var_z + 1 / prior_var_z
    posterior_var_z = 1 / posterior_precision
    posterior_mean_z = (fisher_z / sampling_var_z) / posterior_precision
    posterior_std_z = np.sqrt(posterior_var_z)

    # Credible interval on z scale
    z_crit = sp_stats.norm.ppf((1 + confidence) / 2)
    ci_z_lower = posterior_mean_z - z_crit * posterior_std_z
    ci_z_upper = posterior_mean_z + z_crit * posterior_std_z

    # Convert back to correlation scale via inverse Fisher z
    def inv_fisher_z(z):
        return float(np.tanh(z))

    post_corr_mean = inv_fisher_z(posterior_mean_z)
    post_corr_std = posterior_std_z  # approximation; exact transform is complex
    ci_lower_corr = inv_fisher_z(ci_z_lower)
    ci_upper_corr = inv_fisher_z(ci_z_upper)

    # P(r > 0) = P(z > 0) under posterior
    prob_positive = float(sp_stats.norm.sf(0, loc=posterior_mean_z, scale=posterior_std_z))

    # Bayes factor via Savage-Dickey at z = 0 (which corresponds to r = 0)
    prior_density_at_0 = sp_stats.norm.pdf(0, loc=0, scale=prior_z_std)
    posterior_density_at_0 = sp_stats.norm.pdf(0, loc=posterior_mean_z, scale=posterior_std_z)
    bayes_factor = posterior_density_at_0 / prior_density_at_0 if prior_density_at_0 > 0 else 1.0

    bf_interp = _interpret_bayes_factor(bayes_factor)

    interpretation = (
        f"Bayesian correlation between '{column_a}' and '{column_b}' (n={n}). "
        f"Sample r = {sample_corr:.4f}. "
        f"Posterior correlation mean: {post_corr_mean:.4f} "
        f"(95% CI: [{ci_lower_corr:.4f}, {ci_upper_corr:.4f}]). "
        f"P(r > 0) = {prob_positive:.4f}. "
        f"Bayes factor (BF10): {bayes_factor:.2f} -- {bf_interp}."
    )

    return BayesianCorrelationResult(
        column_a=column_a,
        column_b=column_b,
        n=n,
        sample_correlation=round(sample_corr, 6),
        fisher_z=round(float(fisher_z), 6),
        posterior_z_mean=round(float(posterior_mean_z), 6),
        posterior_z_std=round(float(posterior_std_z), 6),
        posterior_correlation_mean=round(post_corr_mean, 6),
        posterior_correlation_std=round(post_corr_std, 6),
        credible_interval_lower=round(ci_lower_corr, 6),
        credible_interval_upper=round(ci_upper_corr, 6),
        confidence_level=confidence,
        bayes_factor=round(float(bayes_factor), 4),
        bayes_factor_interpretation=bf_interp,
        probability_positive=round(float(prob_positive), 6),
        interpretation=interpretation,
    )


def _bcorr_error(col_a: str, col_b: str, reason: str) -> BayesianCorrelationResult:
    """Return a BayesianCorrelationResult indicating an error."""
    return BayesianCorrelationResult(
        column_a=col_a, column_b=col_b, n=0,
        sample_correlation=0.0, fisher_z=0.0,
        posterior_z_mean=0.0, posterior_z_std=0.0,
        posterior_correlation_mean=0.0, posterior_correlation_std=0.0,
        credible_interval_lower=0.0, credible_interval_upper=0.0,
        confidence_level=0.95, bayes_factor=1.0,
        bayes_factor_interpretation="inconclusive",
        probability_positive=0.5,
        interpretation=f"Error: {reason}",
    )


# ─────────────────────────────────────────────────────────────
# Bayes Factor interpretation helper
# ─────────────────────────────────────────────────────────────

def _interpret_bayes_factor(bf: float) -> str:
    """
    Interpret a Bayes factor (BF10) using Jeffreys' / Lee & Wagenmakers scale.

    BF10 > 1 favours H1 (effect present); BF10 < 1 favours H0.
    """
    if bf < 1:
        bf_inv = 1 / bf
        if bf_inv < 3:
            return "anecdotal evidence for H0 (no effect)"
        elif bf_inv < 10:
            return "moderate evidence for H0 (no effect)"
        elif bf_inv < 30:
            return "strong evidence for H0 (no effect)"
        elif bf_inv < 100:
            return "very strong evidence for H0 (no effect)"
        else:
            return "decisive evidence for H0 (no effect)"
    else:
        if bf < 3:
            return "anecdotal evidence for H1 (effect present)"
        elif bf < 10:
            return "moderate evidence for H1 (effect present)"
        elif bf < 30:
            return "strong evidence for H1 (effect present)"
        elif bf < 100:
            return "very strong evidence for H1 (effect present)"
        else:
            return "decisive evidence for H1 (effect present)"


# ─────────────────────────────────────────────────────────────
# Utility: posterior predictive check summary
# ─────────────────────────────────────────────────────────────

def posterior_predictive_summary(
    data: pd.Series,
    n_simulations: int = 1000,
) -> Dict[str, Any]:
    """
    Generate a posterior predictive summary by drawing from the
    Normal posterior and comparing to observed data.

    Useful as a quick model check.
    """
    clean = data.dropna().astype(float)
    n = len(clean)
    if n < 2:
        return {"error": "Need at least 2 observations."}

    sample_mean = float(clean.mean())
    sample_var = float(clean.var(ddof=1))

    # Non-conjugate prior: Normal-Inverse-Gamma (use conjugate Normal-Normal for mean)
    # Prior on mu: N(0, large); Prior on sigma^2: scaled inverse chi-sq (weak)
    # Posterior for mu: N(sample_mean, sample_var / n)
    posterior_mu_mean = sample_mean
    posterior_mu_std = np.sqrt(sample_var / n)

    # Draw from posterior
    simulated_means = np.random.normal(posterior_mu_mean, posterior_mu_std, n_simulations)
    simulated_stds = np.random.uniform(
        clean.std() * 0.5, clean.std() * 1.5, n_simulations
    )

    # Simulated data
    simulated_data = np.random.normal(
        simulated_means[:, None],
        simulated_stds[:, None],
        (n_simulations, n),
    )

    # Compare statistics
    obs_mean = sample_mean
    obs_std = clean.std()
    obs_median = float(clean.median())
    obs_skew = float(clean.skew())

    sim_means = simulated_data.mean(axis=1)
    sim_stds = simulated_data.std(axis=1, ddof=1)
    sim_medians = np.median(simulated_data, axis=1)

    # Posterior predictive p-values (two-tailed)
    p_mean = float(np.mean(np.abs(sim_means - obs_mean) >= np.abs(obs_mean - sample_mean)))
    p_std = float(np.mean(np.abs(sim_stds - obs_std) >= np.abs(obs_std - clean.std())))
    p_median = float(np.mean(np.abs(sim_medians - obs_median) >= np.abs(obs_median - obs_median)))

    return {
        "observed_mean": round(obs_mean, 4),
        "observed_std": round(obs_std, 4),
        "observed_median": round(obs_median, 4),
        "observed_skewness": round(obs_skew, 4),
        "posterior_predictive_p_mean": round(p_mean, 4),
        "posterior_predictive_p_std": round(p_std, 4),
        "posterior_predictive_p_median": round(p_median, 4),
        "n_simulations": n_simulations,
        "interpretation": (
            "Posterior predictive p-values close to 0.5 indicate good model fit. "
            f"p(mean)={p_mean:.4f}, p(std)={p_std:.4f}."
        ),
    }
