"""
Time Series Analysis Module
Enhanced decomposition and forecasting beyond the basic linear regression approach
"""

import logging
from typing import Dict, List, Optional, Any, Union
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def advanced_decomposition(
    df: pd.DataFrame,
    date_col: str,
    value_col: str,
    period: int = 0,
    model: str = "additive",
) -> Dict[str, Any]:
    """
    Advanced time series decomposition with additive/multiplicative models.

    Returns trend, seasonal, residual components along with strength metrics.
    """
    # Parse dates and aggregate
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col])
    ts = df.groupby(date_col)[value_col].sum().sort_index()

    if len(ts) < 4:
        return {"success": False, "error": "Need at least 4 data points for decomposition"}

    # Infer period if not provided
    if period <= 0:
        period = _infer_period(ts)
    period = max(2, min(period, len(ts) // 2))

    # Decompose
    vals = ts.values
    n = len(vals)

    # Trend via centered moving average
    trend_arr = pd.Series(vals).rolling(window=period, center=True, min_periods=1).mean().values

    # Detrend
    if model == "additive":
        detrended = vals - trend_arr
    else:  # multiplicative
        detrended = vals / np.where(np.abs(trend_arr) > 1e-10, trend_arr, 1.0)

    # Seasonal: average detrended at each period position
    seasonal_arr = np.zeros(n)
    for i in range(period):
        indices = list(range(i, n, period))
        if indices:
            seasonal_arr[indices] = np.nanmean(detrended[indices])

    # Center seasonal component
    if model == "additive":
        seasonal_arr -= np.nanmean(seasonal_arr)
    else:
        seasonal_arr /= np.nanmean(seasonal_arr) if np.nanmean(seasonal_arr) != 0 else 1.0

    # Residual
    if model == "additive":
        residual_arr = vals - trend_arr - seasonal_arr
    else:
        residual_arr = vals / np.where(np.abs(trend_arr * seasonal_arr) > 1e-10, trend_arr * seasonal_arr, 1.0)
        residual_arr = np.clip(residual_arr, -1e6, 1e6)

    # Fill NaN residuals (especially at edges) with 0 or 1
    if model == "additive":
        residual_arr = np.nan_to_num(residual_arr, nan=0.0)
    else:
        residual_arr = np.nan_to_num(residual_arr, nan=1.0)

    # Strength metrics
    var_residual = np.nanvar(residual_arr)
    var_detrended = np.nanvar(detrended)
    var_deseasoned = np.nanvar(
        vals - seasonal_arr if model == "additive" else vals / np.where(np.abs(seasonal_arr) > 1e-10, seasonal_arr, 1.0)
    )

    strength_trend = max(0.0, 1.0 - var_residual / max(var_detrended, 1e-10))
    strength_seasonality = max(0.0, 1.0 - var_residual / max(var_deseasoned, 1e-10))

    dates_formatted = [str(d)[:10] for d in ts.index]

    return {
        "success": True,
        "table": "",
        "date_col": date_col,
        "value_col": value_col,
        "period": period,
        "model": model,
        "dates": dates_formatted,
        "observed": [round(float(v), 4) for v in vals],
        "trend": [round(float(v), 4) for v in trend_arr],
        "seasonal": [round(float(v), 4) for v in seasonal_arr],
        "residual": [round(float(v), 4) for v in residual_arr],
        "strength_trend": round(float(strength_trend), 4),
        "strength_seasonality": round(float(strength_seasonality), 4),
    }


def enhanced_forecast(
    df: pd.DataFrame,
    date_col: str,
    value_col: str,
    periods: int = 6,
    method: str = "auto",
    confidence: float = 0.95,
) -> Dict[str, Any]:
    """
    Enhanced forecasting with multiple methods.

    Methods:
    - auto: Decompose first, forecast each component, recombine
    - linear: Linear regression (original approach)
    - naive: Same value as last observation
    - seasonal_naive: Same season as last period
    """
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col])
    ts = df.groupby(date_col)[value_col].sum().sort_index()

    if len(ts) < 3:
        return {"success": False, "error": "Need at least 3 data points for forecasting"}

    vals = ts.values
    dates = ts.index
    n = len(vals)

    period = _infer_period(ts)
    last_date = dates[-1]
    freq = pd.infer_freq(dates) or "D"
    future_dates = pd.date_range(start=last_date, periods=periods + 1, freq=freq)[1:]

    if method == "seasonal_naive" and period > 0 and n >= period + 1:
        # Use seasonal naive: same as observation at same point last period
        forecast_vals = vals[-period:][:periods]
        # Pad if not enough
        while len(forecast_vals) < periods:
            forecast_vals = np.append(forecast_vals, vals[-(periods - len(forecast_vals)):])
        forecast_vals = forecast_vals[:periods]
    elif method == "naive":
        forecast_vals = np.full(periods, vals[-1])
    else:
        # Linear regression (default/linear/auto)
        x = np.arange(n).astype(float)
        coeffs = np.polyfit(x, vals, 1)
        forecast_x = np.arange(n, n + periods)
        forecast_vals = np.polyval(coeffs, forecast_x)

        # Adjust for seasonality if period detected
        if period > 0 and n >= period * 2:
            seasonal_adj = _compute_seasonal_adjustment(vals, period)
            for i in range(periods):
                season_idx = (n + i) % period
                forecast_vals[i] += seasonal_adj[season_idx]

    # Compute standard deviation for confidence intervals
    resid = vals - np.polyval(np.polyfit(np.arange(n).astype(float), vals, 1), np.arange(n))
    std = float(np.std(resid)) if len(resid) > 0 else 0.0

    from scipy.stats import norm
    z = norm.ppf((1 + confidence) / 2)
    margin = z * std * np.sqrt(1 + 1 / n) if std > 0 else 0.0

    return {
        "success": True,
        "date_col": date_col,
        "value_col": value_col,
        "method": method,
        "historical_dates": [str(d)[:10] for d in dates],
        "historical_values": [round(float(v), 4) for v in vals],
        "forecast_dates": [str(d)[:10] for d in future_dates],
        "forecast_values": [round(float(v), 4) for v in forecast_vals],
        "upper_bound": [round(float(v + margin), 4) for v in forecast_vals],
        "lower_bound": [round(float(v - margin), 4) for v in forecast_vals],
        "confidence_level": confidence,
        "period": period,
    }


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


def _compute_seasonal_adjustment(vals: np.ndarray, period: int) -> np.ndarray:
    """Compute seasonal adjustment factors."""
    n = len(vals)
    x = np.arange(n).astype(float)
    coeffs = np.polyfit(x, vals, 1)
    trend = np.polyval(coeffs, x)
    detrended = vals - trend
    seasonal = np.zeros(period)
    for i in range(period):
        indices = list(range(i, n, period))
        seasonal[i] = np.mean(detrended[indices])
    seasonal -= np.mean(seasonal)
    return seasonal
