"""
Anomaly Detection Pipeline

Statistical methods for detecting anomalies in data:
- IQR-based outlier detection
- Z-score for distribution anomalies
- Change point detection for time series
- Seasonality decomposition for periodic data
"""

import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime

import numpy as np
import pandas as pd
from scipy import stats

logger = logging.getLogger(__name__)


def detect_iqr_outliers(series: pd.Series, threshold: float = 1.5) -> Dict[str, Any]:
    """Detect outliers using the Interquartile Range method."""
    if len(series) < 4:
        return {"outliers": [], "count": 0, "method": "iqr"}
    q1 = series.quantile(0.25)
    q3 = series.quantile(0.75)
    iqr = q3 - q1
    lower = q1 - threshold * iqr
    upper = q3 + threshold * iqr
    outliers = series[(series < lower) | (series > upper)]
    return {
        "outliers": outliers.index.tolist(),
        "count": len(outliers),
        "pct": round(len(outliers) / len(series) * 100, 2) if len(series) > 0 else 0,
        "lower_bound": round(lower, 2),
        "upper_bound": round(upper, 2),
        "method": "iqr",
    }


def detect_zscore_outliers(series: pd.Series, threshold: float = 3.0) -> Dict[str, Any]:
    """Detect outliers using Z-score method."""
    if len(series) < 4:
        return {"outliers": [], "count": 0, "method": "zscore"}
    z = np.abs(stats.zscore(series.dropna()))
    outlier_indices = np.where(z > threshold)[0]
    return {
        "count": len(outlier_indices),
        "pct": round(len(outlier_indices) / len(series) * 100, 2) if len(series) > 0 else 0,
        "threshold": threshold,
        "method": "zscore",
    }


def detect_change_points(values: List[float], min_deviation: float = 2.0) -> List[Dict[str, Any]]:
    """Detect significant change points in a time series."""
    if len(values) < 5:
        return []
    series = pd.Series(values)
    rolling_mean = series.rolling(window=3, min_periods=1).mean()
    rolling_std = series.rolling(window=3, min_periods=1).std().fillna(0)
    changes = []
    for i in range(1, len(series)):
        if rolling_std[i] > 0:
            z = abs(series[i] - rolling_mean[i - 1]) / max(rolling_std[i - 1], 0.001)
            if z > min_deviation:
                changes.append({
                    "index": i,
                    "value": float(series[i]),
                    "expected": float(rolling_mean[i - 1]),
                    "deviation": round(float(z), 2),
                    "direction": "up" if series[i] > rolling_mean[i - 1] else "down",
                })
    return changes


def detect_seasonality(df: pd.DataFrame, date_col: str, value_col: str) -> Dict[str, Any]:
    """Detect seasonal patterns using autocorrelation."""
    if date_col not in df or value_col not in df:
        return {"has_seasonality": False, "period": None}
    try:
        series = df.set_index(date_col)[value_col]
        if len(series) < 14:
            return {"has_seasonality": False, "period": None, "reason": "Not enough data"}
        # Simple autocorrelation-based seasonality detection
        autocorr = pd.Series([series.autocorr(lag=i) for i in range(1, min(30, len(series) // 2))])
        peak_lag = autocorr.idxmax() + 1 if autocorr.max() > 0.3 else None
        return {
            "has_seasonality": peak_lag is not None,
            "period": peak_lag,
            "confidence": round(float(autocorr.max()), 2) if peak_lag else 0,
        }
    except Exception as e:
        return {"has_seasonality": False, "period": None, "error": str(e)}


def analyze_dataframe(df: pd.DataFrame, date_col: Optional[str] = None) -> Dict[str, Any]:
    """Run all anomaly detection methods on a DataFrame and return results."""
    results = {"timestamp": datetime.now().isoformat(), "anomalies": [], "severity": "low"}
    numeric_cols = df.select_dtypes(include=["number"]).columns
    for col in numeric_cols:
        series = df[col].dropna()
        if len(series) < 10:
            continue
        iqr_result = detect_iqr_outliers(series)
        if iqr_result["count"] > 0:
            results["anomalies"].append({
                "column": col,
                "method": "iqr",
                "count": iqr_result["count"],
                "pct": iqr_result["pct"],
                "bounds": {"lower": iqr_result["lower_bound"], "upper": iqr_result["upper_bound"]},
            })
    if date_col and date_col in df.columns:
        for col in numeric_cols:
            series = df.sort_values(date_col)[col]
            changes = detect_change_points(series.tolist())
            for c in changes:
                results["anomalies"].append({
                    "column": col,
                    "method": "change_point",
                    "index": c["index"],
                    "value": c["value"],
                    "expected": c["expected"],
                    "deviation": c["deviation"],
                    "direction": c["direction"],
                })
    results["anomaly_count"] = len(results["anomalies"])
    if results["anomaly_count"] > 5:
        results["severity"] = "high"
    elif results["anomaly_count"] > 0:
        results["severity"] = "medium"
    return results
