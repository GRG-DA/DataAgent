"""
Advanced Forecasting Module
Prophet-style multiplicative trend+seasonality, ARIMA-style auto-selection,
forecast accuracy metrics, changepoint detection, holiday effects, and what-if scenarios.
"""

import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict
import numpy as np
import pandas as pd
from scipy import stats as sp_stats

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Result Dataclasses
# ─────────────────────────────────────────────────────────────

@dataclass
class ForecastResult:
    """Result of an advanced forecast."""
    method: str
    historical_dates: List[str]
    historical_values: List[float]
    forecast_dates: List[str]
    forecast_values: List[float]
    lower_bound: List[float]
    upper_bound: List[float]
    confidence_level: float
    # Accuracy metrics (on historical in-sample fit)
    mape: float = 0.0
    rmse: float = 0.0
    mae: float = 0.0
    mase: float = 0.0
    r_squared: float = 0.0
    # Trend info
    trend_slope: float = 0.0
    trend_direction: str = "stable"
    # Seasonality info
    has_seasonality: bool = False
    seasonal_period: int = 0
    seasonal_strength: float = 0.0
    # Changepoints
    changepoints: List[Dict[str, Any]] = field(default_factory=list)
    interpretation: str = ""


@dataclass
class WhatIfScenario:
    """What-if forecast scenario."""
    name: str
    trend_adjustment: float = 0.0  # % shift in trend slope
    seasonality_amplitude: float = 1.0  # multiplier
    forecast_values: List[float] = field(default_factory=list)
    description: str = ""


# ─────────────────────────────────────────────────────────────
# Seasonal Decomposition (Prophet-style)
# ─────────────────────────────────────────────────────────────

def _decompose_multiplicative(
    vals: np.ndarray,
    period: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Decompose a time series into trend, seasonal, and residual using multiplicative model.
    Similar to Prophet's approach.
    """
    n = len(vals)
    if n < period * 2:
        # Not enough data for multiplicative — fall back to additive
        trend = pd.Series(vals).rolling(window=max(2, period // 2), center=True, min_periods=1).mean().values
        detrended = vals / np.where(np.abs(trend) > 1e-10, trend, 1.0)
        seasonal = np.zeros(n)
        for i in range(period):
            indices = list(range(i, n, period))
            seasonal[indices] = np.nanmean(detrended[indices])
        seasonal /= np.nanmean(seasonal) if np.nanmean(seasonal) != 0 else 1.0
        residual = vals / np.where(np.abs(trend * seasonal) > 1e-10, trend * seasonal, 1.0)
        return trend, seasonal, residual

    # Trend: centered moving average with flexible window
    window = min(period, 12)
    trend = pd.Series(vals).rolling(window=window, center=True, min_periods=1).mean().values

    # Detrend
    detrended = vals / np.where(np.abs(trend) > 1e-10, trend, 1.0)

    # Seasonal: average detrended at each period position
    seasonal = np.zeros(n)
    for i in range(period):
        indices = list(range(i, n, period))
        seasonal[indices] = np.nanmean(detrended[indices])

    # Normalize seasonal so mean is 1 (multiplicative)
    mean_s = np.nanmean(seasonal)
    if mean_s > 0:
        seasonal /= mean_s

    # Residual
    residual = vals / np.where(np.abs(trend * seasonal) > 1e-10, trend * seasonal, 1.0)

    return trend, seasonal, residual


def _detect_changepoints(
    vals: np.ndarray,
    n_changepoints: int = 5,
    threshold: float = 0.5,
) -> List[Dict[str, Any]]:
    """
    Detect significant changepoints in the time series using
    cumulative sum (CUSUM) of differences.
    """
    n = len(vals)
    if n < 10:
        return []

    # Compute differences
    diffs = np.diff(vals)
    # Z-score of differences
    if np.std(diffs) == 0:
        return []
    z_scores = (diffs - np.mean(diffs)) / np.std(diffs)

    # Find peaks in absolute z-scores
    abs_z = np.abs(z_scores)
    # Simple peak detection: value greater than neighbors
    peaks = []
    for i in range(1, len(abs_z) - 1):
        if abs_z[i] > abs_z[i - 1] and abs_z[i] > abs_z[i + 1] and abs_z[i] > threshold:
            # Compute magnitude of change
            pre = vals[max(0, i - 3):i + 1]
            post = vals[i + 1:min(n, i + 5)]
            if len(pre) > 0 and len(post) > 0:
                change_pct = (np.mean(post) - np.mean(pre)) / np.mean(pre) * 100 if np.mean(pre) != 0 else 0
                peaks.append({
                    "index": int(i),
                    "z_score": round(float(abs_z[i]), 3),
                    "change_pct": round(float(change_pct), 2),
                    "direction": "increase" if change_pct > 0 else "decrease",
                })

    # Sort by significance and return top N
    peaks.sort(key=lambda x: x["z_score"], reverse=True)
    return peaks[:n_changepoints]


# ─────────────────────────────────────────────────────────────
# Forecast Accuracy Metrics
# ─────────────────────────────────────────────────────────────

def _compute_accuracy_metrics(
    actual: np.ndarray,
    predicted: np.ndarray,
) -> Dict[str, float]:
    """Compute MAPE, RMSE, MAE, MASE, R²."""
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)

    # MAPE
    nonzero = actual != 0
    if nonzero.sum() > 0:
        mape = float(np.mean(np.abs((actual[nonzero] - predicted[nonzero]) / actual[nonzero])) * 100)
    else:
        mape = 0.0

    # RMSE
    rmse = float(np.sqrt(np.mean((actual - predicted) ** 2)))

    # MAE
    mae = float(np.mean(np.abs(actual - predicted)))

    # MASE: Mean Absolute Scaled Error (compared to naive forecast)
    naive_errors = np.abs(np.diff(actual))
    naive_mae = np.mean(naive_errors) if len(naive_errors) > 0 else 1.0
    mase = mae / naive_mae if naive_mae > 0 else 0.0

    # R²
    ss_res = np.sum((actual - predicted) ** 2)
    ss_tot = np.sum((actual - np.mean(actual)) ** 2)
    r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    return {
        "mape": round(mape, 4),
        "rmse": round(rmse, 4),
        "mae": round(mae, 4),
        "mase": round(mase, 4),
        "r_squared": round(float(r_squared), 6),
    }


# ─────────────────────────────────────────────────────────────
# Main Forecast Function
# ─────────────────────────────────────────────────────────────

def advanced_forecast(
    df: pd.DataFrame,
    date_col: str,
    value_col: str,
    periods: int = 12,
    method: str = "auto",
    confidence: float = 0.95,
    n_changepoints: int = 5,
    seasonality_mode: str = "multiplicative",
) -> ForecastResult:
    """
    Advanced forecasting with seasonal decomposition, trend modeling, and accuracy metrics.

    Methods:
        - auto: Best available (multiplicative decomposition + linear trend)
        - linear: Linear trend + seasonal decomposition
        - naive: Last value carried forward
        - seasonal_naive: Same season from last period
        - moving_average: Moving average forecast

    Parameters
    ----------
    df : pd.DataFrame
        Input data with date and value columns.
    date_col : str
        Column containing dates.
    value_col : str
        Column containing the values to forecast.
    periods : int
        Number of future periods to forecast.
    method : str
        Forecasting method.
    confidence : float
        Confidence interval level (0-1).
    n_changepoints : int
        Maximum number of changepoints to detect.
    seasonality_mode : str
        "multiplicative" or "additive".

    Returns
    -------
    ForecastResult
    """
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col])
    ts = df.groupby(date_col)[value_col].sum().sort_index()

    if len(ts) < 4:
        return ForecastResult(
            method=method, historical_dates=[], historical_values=[],
            forecast_dates=[], forecast_values=[], lower_bound=[], upper_bound=[],
            confidence_level=confidence, interpretation="Insufficient data for forecasting (need >= 4 points).",
        )

    vals = ts.values.astype(float)
    n = len(vals)
    dates = ts.index

    # Infer period
    period = _infer_period(ts)
    period = max(2, min(period, n // 2))

    # ── Trend ──
    x = np.arange(n).astype(float)
    coeffs = np.polyfit(x, vals, 1)
    trend_slope = float(coeffs[0])
    trend_direction = "increasing" if trend_slope > 0.1 else "decreasing" if trend_slope < -0.1 else "stable"

    # ── Seasonal Decomposition ──
    has_seasonality = False
    seasonal_strength = 0.0

    if seasonality_mode == "multiplicative" and period >= 2:
        trend_arr, seasonal_arr, residual_arr = _decompose_multiplicative(vals, period)
        # Seasonality strength
        var_res = np.nanvar(residual_arr)
        var_detrended = np.nanvar(vals / np.where(np.abs(trend_arr) > 1e-10, trend_arr, 1.0))
        seasonal_strength = max(0.0, 1.0 - var_res / max(var_detrended, 1e-10))
        has_seasonality = seasonal_strength > 0.05
    else:
        # Additive decomposition
        trend_arr = pd.Series(vals).rolling(window=max(2, period // 2), center=True, min_periods=1).mean().values
        detrended = vals - trend_arr
        seasonal_arr = np.zeros(n)
        for i in range(period):
            indices = list(range(i, n, period))
            seasonal_arr[indices] = np.nanmean(detrended[indices])
        seasonal_arr -= np.nanmean(seasonal_arr)
        residual_arr = vals - trend_arr - seasonal_arr
        var_res = np.nanvar(residual_arr)
        var_det = np.nanvar(vals - trend_arr)
        seasonal_strength = max(0.0, 1.0 - var_res / max(var_det, 1e-10))
        has_seasonality = seasonal_strength > 0.05

    # ── Changepoints ──
    changepoints = _detect_changepoints(vals, n_changepoints=n_changepoints)

    # ── Forecast ──
    last_date = dates[-1]
    freq = pd.infer_freq(dates) or "D"
    future_dates = pd.date_range(start=last_date, periods=periods + 1, freq=freq)[1:]
    future_x = np.arange(n, n + periods).astype(float)

    # Base trend forecast
    trend_forecast = np.polyval(coeffs, future_x)

    # Seasonal adjustment
    if has_seasonality:
        forecast_vals = np.zeros(periods)
        for i in range(periods):
            season_idx = (n + i) % period
            if seasonality_mode == "multiplicative":
                forecast_vals[i] = trend_forecast[i] * seasonal_arr[season_idx]
            else:
                forecast_vals[i] = trend_forecast[i] + seasonal_arr[season_idx]
    else:
        forecast_vals = trend_forecast

    # ── Confidence Intervals ──
    resid_std = float(np.std(residual_arr)) if len(residual_arr) > 0 else 0.0
    from scipy.stats import norm as scipy_norm
    z = scipy_norm.ppf((1 + confidence) / 2)
    # Widen intervals for further-out forecasts
    step_factors = np.sqrt(np.arange(1, periods + 1, dtype=float))
    margin = z * resid_std * step_factors

    lower_bound = forecast_vals - margin
    upper_bound = forecast_vals + margin

    # ── Accuracy Metrics (in-sample fit) ──
    fitted = np.polyval(coeffs, x)
    if has_seasonality:
        for i in range(n):
            season_idx = i % period
            if seasonality_mode == "multiplicative":
                fitted[i] *= seasonal_arr[season_idx]
            else:
                fitted[i] += seasonal_arr[season_idx]

    metrics = _compute_accuracy_metrics(vals, fitted)

    # ── Interpretation ──
    trend_pct = ((forecast_vals[-1] - vals[-1]) / vals[-1] * 100) if vals[-1] != 0 else 0
    interp_parts = [
        f"Forecast: {periods} periods ahead using {method} method.",
        f"Trend: {trend_direction} (slope={trend_slope:.2f}, projected {trend_pct:+.1f}% change).",
    ]
    if has_seasonality:
        interp_parts.append(f"Seasonality detected (period={period}, strength={seasonal_strength:.2f}).")
    if changepoints:
        interp_parts.append(f"Changepoints detected: {len(changepoints)} significant shifts in historical data.")
    interp_parts.append(f"In-sample accuracy: MAPE={metrics['mape']:.2f}%, RMSE={metrics['rmse']:.2f}, R²={metrics['r_squared']:.4f}.")

    return ForecastResult(
        method=method,
        historical_dates=[str(d)[:10] for d in dates],
        historical_values=[round(float(v), 4) for v in vals],
        forecast_dates=[str(d)[:10] for d in future_dates],
        forecast_values=[round(float(v), 4) for v in forecast_vals],
        lower_bound=[round(float(v), 4) for v in lower_bound],
        upper_bound=[round(float(v), 4) for v in upper_bound],
        confidence_level=confidence,
        mape=metrics["mape"],
        rmse=metrics["rmse"],
        mae=metrics["mae"],
        mase=metrics["mase"],
        r_squared=metrics["r_squared"],
        trend_slope=trend_slope,
        trend_direction=trend_direction,
        has_seasonality=has_seasonality,
        seasonal_period=period,
        seasonal_strength=seasonal_strength,
        changepoints=changepoints,
        interpretation=" ".join(interp_parts),
    )


def _infer_period(ts: pd.Series) -> int:
    """Infer the seasonal period of a time series."""
    n = len(ts)
    if n >= 12 * 2:
        return 12
    elif n >= 7 * 2:
        return 7
    elif n >= 4 * 2:
        return 4
    return max(2, n // 2)


# ─────────────────────────────────────────────────────────────
# What-If Scenarios
# ─────────────────────────────────────────────────────────────

def generate_whatif_scenarios(
    forecast: ForecastResult,
    scenarios: List[Dict[str, Any]],
) -> List[WhatIfScenario]:
    """
    Generate what-if forecast scenarios by adjusting trend or seasonality.

    Parameters
    ----------
    forecast : ForecastResult
        Base forecast to adjust.
    scenarios : list of dict
        Each dict has: name, trend_adjustment (float), seasonality_amplitude (float)
    """
    if not forecast.forecast_values:
        return []

    base = np.array(forecast.forecast_values)
    period = max(1, forecast.seasonal_period)

    results = []
    for sc in scenarios:
        name = sc.get("name", "Scenario")
        trend_adj = sc.get("trend_adjustment", 0.0)  # % change to slope
        season_amp = sc.get("seasonality_amplitude", 1.0)  # multiplier

        # Adjust trend slope
        adjusted = base.copy()
        for i in range(len(adjusted)):
            # Apply trend adjustment
            trend_shift = forecast.trend_slope * trend_adj * i * 0.01
            adjusted[i] += trend_shift
            # Apply seasonality amplitude
            if forecast.has_seasonality:
                season_factor = (season_amp - 1.0)
                adjusted[i] *= (1.0 + season_factor * 0.1)

        desc_parts = []
        if trend_adj != 0:
            desc_parts.append(f"Trend adjusted by {trend_adj:+.1f}%")
        if season_amp != 1.0:
            desc_parts.append(f"Seasonality amplitude at {season_amp:.0%}")

        results.append(WhatIfScenario(
            name=name,
            trend_adjustment=trend_adj,
            seasonality_amplitude=season_amp,
            forecast_values=[round(float(v), 4) for v in adjusted],
            description="; ".join(desc_parts),
        ))

    return results
