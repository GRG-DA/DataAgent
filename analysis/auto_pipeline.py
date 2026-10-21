"""
Auto Insights Pipeline — Background analysis scheduler

Runs configured checks on a timer: quality scoring, anomaly detection,
drift monitoring, and forecast deviation tracking.
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable

import duckdb
import pandas as pd
from api.tenancy import tenant_search_path

logger = logging.getLogger(__name__)


class AutoAnalyzer:
    """Background analysis engine that runs configured checks on tables."""

    def __init__(self, db_path: str = "data/curated/warehouse.duckdb",
                 schema: str = "analytics",
                 trends_dir: str = "data/quality_trends"):
        self.db_path = Path(db_path)
        self.schema = schema
        self.trends_dir = Path(trends_dir)
        self.trends_dir.mkdir(parents=True, exist_ok=True)
        self._checks = {}  # {table_name: [check_types]}
        self._running = False
        self._callbacks = []

    def register_check(self, table: str, check_types: List[str]):
        """Register a table for automated checks."""
        self._checks[table] = check_types

    def remove_check(self, table: str):
        """Remove a table from automated checks."""
        self._checks.pop(table, None)

    def on_alert(self, callback: Callable):
        """Register an alert callback for anomaly detection."""
        self._callbacks.append(callback)

    def run_all_checks(self) -> Dict[str, Any]:
        """Run all registered checks and return results."""
        results = {"timestamp": datetime.now().isoformat(), "checks": []}
        for table, check_types in self._checks.items():
            table_result = {"table": table, "checks": []}
            for check_type in check_types:
                try:
                    if check_type == "quality":
                        result = self._check_quality(table)
                    elif check_type == "anomaly":
                        result = self._check_anomaly(table)
                    elif check_type == "drift":
                        result = self._check_drift(table)
                    else:
                        result = {"status": "unknown_check"}
                    table_result["checks"].append({"type": check_type, "result": result})
                except Exception as e:
                    table_result["checks"].append({"type": check_type, "error": str(e)})
            results["checks"].append(table_result)
        return results

    def _get_connection(self):
        conn = duckdb.connect(str(self.db_path))
        conn.execute(f"SET search_path = '{tenant_search_path()}'")
        return conn

    def _check_quality(self, table: str) -> Dict[str, Any]:
        """Run quality checks on a table."""
        conn = self._get_connection()
        try:
            df = conn.execute(f"SELECT * FROM {self.schema}.{table}").df()
            total_rows = len(df)
            total_cols = len(df.columns)
            null_cells = int(df.isnull().sum().sum())
            null_pct = round(null_cells / (total_rows * total_cols) * 100, 2) if total_rows > 0 else 0
            dup_rows = int(df.duplicated().sum())
            dup_pct = round(dup_rows / total_rows * 100, 2) if total_rows > 0 else 0
            # Save trend
            trend = {
                "timestamp": datetime.now().isoformat(),
                "table": table,
                "rows": total_rows,
                "cols": total_cols,
                "null_pct": null_pct,
                "dup_pct": dup_pct,
            }
            self._save_trend(table, "quality", trend)
            return trend
        finally:
            conn.close()

    def _check_anomaly(self, table: str) -> Dict[str, Any]:
        """Detect anomalies in numeric columns using IQR method."""
        conn = self._get_connection()
        try:
            df = conn.execute(f"SELECT * FROM {self.schema}.{table}").df()
            numeric_cols = df.select_dtypes(include=["number"]).columns
            anomalies = []
            for col in numeric_cols[:5]:  # Limit to 5 columns
                series = df[col].dropna()
                if len(series) < 10:
                    continue
                q1 = series.quantile(0.25)
                q3 = series.quantile(0.75)
                iqr = q3 - q1
                lower = q1 - 1.5 * iqr
                upper = q3 + 1.5 * iqr
                outliers = series[(series < lower) | (series > upper)]
                if len(outliers) > 0:
                    anomalies.append({
                        "column": col,
                        "outlier_count": int(len(outliers)),
                        "outlier_pct": round(len(outliers) / len(series) * 100, 2),
                        "lower_bound": round(lower, 2),
                        "upper_bound": round(upper, 2),
                        "min": round(float(series.min()), 2),
                        "max": round(float(series.max()), 2),
                    })
            result = {
                "timestamp": datetime.now().isoformat(),
                "table": table,
                "anomalies": anomalies,
                "anomaly_count": len(anomalies),
                "severity": "high" if len(anomalies) > 3 else "medium" if len(anomalies) > 0 else "low",
            }
            self._save_trend(table, "anomaly", result)
            # Fire alerts for high severity
            if result["severity"] == "high":
                for cb in self._callbacks:
                    try:
                        cb(result)
                    except Exception:
                        pass
            return result
        finally:
            conn.close()

    def _check_drift(self, table: str) -> Dict[str, Any]:
        """Check for distribution drift compared to historical baseline."""
        history = self._load_trends(table, "quality")
        if len(history) < 2:
            return {"status": "insufficient_data", "message": "Need at least 2 data points for drift detection"}
        latest = history[-1]
        previous = history[-2]
        drift_indicators = []
        for metric in ["rows", "null_pct", "dup_pct"]:
            if metric in latest and metric in previous:
                change = abs(latest[metric] - previous[metric])
                if previous[metric] and change / max(previous[metric], 1) > 0.5:
                    drift_indicators.append({"metric": metric, "change": round(change, 2), "severity": "high"})
        return {
            "timestamp": datetime.now().isoformat(),
            "table": table,
            "drift_detected": len(drift_indicators) > 0,
            "indicators": drift_indicators,
        }

    def _save_trend(self, table: str, check_type: str, data: dict):
        """Save trend data point to CSV."""
        trend_file = self.trends_dir / f"{table}_{check_type}.csv"
        df = pd.DataFrame([data])
        if trend_file.exists():
            existing = pd.read_csv(trend_file)
            df = pd.concat([existing, df], ignore_index=True)
            if len(df) > 1000:
                df = df.tail(1000)
        df.to_csv(trend_file, index=False)

    def _load_trends(self, table: str, check_type: str) -> List[dict]:
        """Load historical trend data for a table."""
        trend_file = self.trends_dir / f"{table}_{check_type}.csv"
        if trend_file.exists():
            df = pd.read_csv(trend_file)
            return df.to_dict("records")
        return []
