"""Chat-task executors for the feature-menu operations.

Every executor validates identifiers, enforces RBAC, and checks plan
entitlements before touching data. Gated features fail closed with a clear
upgrade message instead of silently skipping the operation.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from agent.tools_tasks import (
    TaskResult,
    _read_table,
    _require_permission,
    _user_identifier,
)

_MAX_ROWS = 500
_EXPORT_FORMATS = {
    "csv": ("export:csv", "csv"),
    "parquet": ("export:parquet", "parquet"),
    "excel": ("export:excel", "xlsx"),
    "xlsx": ("export:excel", "xlsx"),
    "bi": ("export:bi", "bi"),
    "markdown": ("export:csv", "markdown"),
    "html": ("export:csv", "html"),
    "pdf": ("export:csv", "pdf"),
}


def _tenant(tenant_id: Optional[str]) -> Optional[str]:
    if tenant_id:
        return tenant_id
    try:
        from api.tenancy import current_tenant_id

        return current_tenant_id()
    except Exception:  # noqa: BLE001 - standalone analysis checkout
        return None


def _db_path() -> str:
    from config import settings

    return settings.duckdb.database_path


def _validate(table: str) -> str:
    from api.security_paths import validate_table_name

    return validate_table_name(table)


def _require_feature(user: Any, feature_key: str, label: str) -> Optional[str]:
    from api.billing.entitlements import has_feature

    if has_feature(_user_identifier(user), feature_key):
        return None
    return (
        f"{label} requires a plan that includes this feature. "
        "Upgrade in Billing to use it from chat."
    )


def _trends_dir() -> Path:
    path = Path(
        os.environ.get("DATAAGENT_QUALITY_TRENDS_DIR", "data/quality_trends")
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def _chart(
    chart_type: str,
    data: List[Dict[str, Any]],
    layout: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a Plotly spec Chat Pro can render as a per-step chart."""
    return {
        "chart_type": chart_type,
        "spec": {"data": data, "layout": layout or {}},
    }


def _format_value(value: Any) -> str:
    """Render a cell value for a chat bullet without numpy noise."""
    if value is None:
        return "—"
    try:
        if pd.isna(value):
            return "—"
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return str(value)


def _format_timestamp(value: Any) -> str:
    if not value:
        return ""
    return str(value)[:16].replace("T", " ")


def _format_anomaly_columns(anomalies: Dict[str, Any]) -> str:
    lines = []
    for column, item in anomalies.items():
        examples = [
            _format_value(record.get(column))
            for record in (item.get("examples") or [])[:3]
            if column in record
        ]
        line = (
            f"- **{column}** — {item.get('count', 0)} outlier(s) "
            f"({_format_value(item.get('pct', 0))}%)"
        )
        if examples:
            line += f" · e.g. {', '.join(examples)}"
        lines.append(line)
    return "\n".join(lines)


def _format_quality_snapshots(rows: List[Dict[str, Any]]) -> str:
    return "\n".join(
        f"- {_format_timestamp(row.get('timestamp'))} — "
        f"{_format_value(row.get('overall_score'))}/100"
        for row in rows[-10:]
    )


def _format_dimension_scores(scores: Dict[str, Any]) -> str:
    return "\n".join(
        f"- {str(name).replace('_', ' ').capitalize()}: {_format_value(value)}"
        for name, value in (scores or {}).items()
    )


def _format_group_stats(group_stats: Dict[str, Any]) -> str:
    lines = []
    for group, stats in (group_stats or {}).items():
        if not isinstance(stats, dict):
            continue
        label = "mean" if "mean" in stats else ("median" if "median" in stats else "")
        if not label:
            continue
        line = f"- **{group}**: {label} {_format_value(stats.get(label))}"
        if stats.get("n") is not None:
            line += f", n={stats['n']}"
        lines.append(line)
    return "\n".join(lines)


def _format_table_changes(modified_cells: Dict[str, Any]) -> str:
    lines = []
    for column, cells in list((modified_cells or {}).items())[:5]:
        shown = [
            f"{_format_value(cell.get('key'))}: "
            f"{_format_value(cell.get('before'))} → "
            f"{_format_value(cell.get('after'))}"
            for cell in (cells or [])[:3]
        ]
        line = f"- **{column}** — {len(cells or [])} cell(s)"
        if shown:
            line += f" · {'; '.join(shown)}"
        lines.append(line)
    return "\n".join(lines)


def _format_period_changes(metrics: List[Dict[str, Any]]) -> str:
    lines = []
    for item in (metrics or [])[:10]:
        pct = float(item.get("pct_change", 0) or 0)
        arrow = "▲" if pct > 0 else ("▼" if pct < 0 else "→")
        group = item.get("group")
        prefix = f"**{group}** · " if group else ""
        line = (
            f"- {prefix}{item.get('period_prev')} → {item.get('period_curr')}: "
            f"{_format_value(item.get('mean_prev'))} → "
            f"{_format_value(item.get('mean_curr'))} "
            f"({arrow} {_format_value(abs(pct))}%)"
        )
        if item.get("significant"):
            line += " · significant"
        lines.append(line)
    return "\n".join(lines)


def _format_notifications(items: List[Dict[str, Any]]) -> str:
    lines = []
    for item in (items or [])[:10]:
        title = item.get("title") or item.get("type") or "Notification"
        state = "read" if item.get("is_read") else "unread"
        line = f"- **{title}** ({item.get('type') or 'info'}, {state})"
        stamp = _format_timestamp(item.get("created_at"))
        if stamp:
            line += f" · {stamp}"
        body = item.get("body") or item.get("message")
        if body:
            line += f" — {body}"
        lines.append(line)
    return "\n".join(lines)


def _format_catalog_matches(results: Dict[str, Any]) -> str:
    labels = (("assets", "Assets"), ("terms", "Terms"), ("metrics", "Metrics"))
    lines = []
    for key, label in labels:
        items = (results or {}).get(key) or []
        if not items:
            continue
        names = []
        for item in items[:5]:
            name = item.get("name") or item.get("term") or item.get("id")
            if key == "assets" and item.get("certification_status"):
                name = f"{name} ({item['certification_status']})"
            names.append(str(name))
        lines.append(f"- **{label}**: {', '.join(names)}")
    return "\n".join(lines)


def _load_table(table_name: str, tenant_id: Optional[str]) -> pd.DataFrame:
    return _read_table(_validate(table_name), _tenant(tenant_id))


def _free_output(base: str, tenant_id: Optional[str]) -> str:
    from api.security_paths import sanitize_table_name

    loader = _loader()
    candidate = sanitize_table_name(base)
    suffix = 2
    while loader.table_exists(candidate, tenant_id=tenant_id):
        candidate = f"{sanitize_table_name(base)}_{suffix}"
        suffix += 1
    return candidate


def _loader():
    import api.state as state

    return state.loader


def detect_anomalies(
    table_name: str,
    *,
    user: Any = None,
    tenant_id: Optional[str] = None,
    method: str = "iqr",
    threshold: float = 1.5,
) -> TaskResult:
    denied = _require_permission(user, "data:read")
    if denied:
        return TaskResult(success=False, error=denied)
    try:
        df = _load_table(table_name, tenant_id)
        if df.empty:
            return TaskResult(success=False, error="Table is empty")
        if method not in {"iqr", "zscore"}:
            method = "iqr"
        anomalies: Dict[str, Any] = {}
        for col in df.select_dtypes(include=["number"]).columns:
            series = df[col].dropna()
            if len(series) < 10:
                continue
            mask = pd.Series(False, index=df.index)
            if method == "iqr":
                q1, q3 = series.quantile(0.25), series.quantile(0.75)
                spread = q3 - q1
                mask = (df[col] < q1 - threshold * spread) | (
                    df[col] > q3 + threshold * spread
                )
            else:
                std = series.std()
                if std > 0:
                    mask = ((df[col] - series.mean()).abs() / std) > threshold
            count = int(mask.sum())
            if count:
                anomalies[str(col)] = {
                    "count": count,
                    "pct": round(count / len(df) * 100, 2),
                    "examples": df.loc[df.index[mask][:10], [col]]
                    .reset_index()
                    .to_dict("records"),
                }
        total = sum(item["count"] for item in anomalies.values())
        visualizations = []
        if anomalies:
            columns = list(anomalies)
            visualizations.append(
                _chart(
                    "bar",
                    [
                        {
                            "type": "bar",
                            "x": columns,
                            "y": [anomalies[col]["count"] for col in columns],
                            "marker": {"color": "#ef4444"},
                        }
                    ],
                    {
                        "title": {"text": f"Outliers by column — {table_name}"},
                        "xaxis": {"title": {"text": "Column"}},
                        "yaxis": {"title": {"text": "Outlier count"}},
                    },
                )
            )
        return TaskResult(
            success=True,
            data={
                "table": table_name,
                "summary": {
                    "total_rows": len(df),
                    "method": method,
                    "threshold": threshold,
                    "anomaly_columns": len(anomalies),
                    "total_outliers": total,
                },
                "anomalies": anomalies,
            },
            message=(
                f"Found {total} outlier(s) across {len(anomalies)} column(s) "
                f"in **{table_name}** ({method}, threshold "
                f"{_format_value(threshold)})."
                + (f"\n\n{_format_anomaly_columns(anomalies)}" if anomalies else "")
            ),
            row_count=total,
            visualizations=visualizations,
        )
    except Exception as exc:  # noqa: BLE001
        return TaskResult(success=False, error=f"Anomaly scan failed: {exc}")


def quality_trend(
    table_name: str, *, user: Any = None, days: int = 30
) -> TaskResult:
    denied = _require_permission(user, "data:read")
    if denied:
        return TaskResult(success=False, error=denied)
    try:
        table = _validate(table_name)
        trend_file = _trends_dir() / f"{table}.json"
        data: List[Dict[str, Any]] = []
        if trend_file.is_file():
            data = json.loads(trend_file.read_text(encoding="utf-8"))
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
        ).isoformat()
        filtered = [row for row in data if row.get("timestamp", "") >= cutoff]
        visualizations = []
        if filtered:
            visualizations.append(
                _chart(
                    "line",
                    [
                        {
                            "type": "scatter",
                            "mode": "lines+markers",
                            "x": [row.get("timestamp", "") for row in filtered],
                            "y": [
                                row.get("overall_score", 0) for row in filtered
                            ],
                            "line": {"color": "#3b82f6"},
                        }
                    ],
                    {
                        "title": {"text": f"Quality score — {table}"},
                        "yaxis": {"title": {"text": "Score"}, "range": [0, 100]},
                    },
                )
            )
        message = (
            f"**{table}** has {len(filtered)} snapshot(s) in the last "
            f"{days} day(s) ({len(data)} total)."
        )
        if filtered:
            message += "\n\n" + _format_quality_snapshots(filtered)
        if len(filtered) >= 2:
            delta = float(filtered[-1].get("overall_score", 0)) - float(
                filtered[-2].get("overall_score", 0)
            )
            arrow = "▲" if delta > 0 else ("▼" if delta < 0 else "→")
            message += (
                f"\nLatest: {_format_value(filtered[-1].get('overall_score', 0))}"
                f"/100 ({arrow} {_format_value(abs(delta))} vs previous)."
            )
        return TaskResult(
            success=True,
            data={
                "table": table,
                "trend": filtered,
                "total_snapshots": len(data),
            },
            message=message,
            row_count=len(filtered),
            visualizations=visualizations,
        )
    except Exception as exc:  # noqa: BLE001
        return TaskResult(success=False, error=f"Quality trend failed: {exc}")


def record_quality_snapshot(
    table_name: str, *, user: Any = None, tenant_id: Optional[str] = None
) -> TaskResult:
    denied = _require_permission(user, "data:write")
    if denied:
        return TaskResult(success=False, error=denied)
    try:
        table = _validate(table_name)
        df = _load_table(table, tenant_id)
        if df.empty:
            return TaskResult(success=False, error="Table is empty")
        from analysis.quality import compute_quality_score

        quality = compute_quality_score(df)
        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "overall_score": quality.get("overall_score", 0),
            "dimension_scores": quality.get("dimension_scores", {}),
            "row_count": len(df),
            "column_count": len(df.columns),
            "duplicate_count": quality.get("duplicate_count", 0),
            "missing_cells": quality.get("total_missing_cells", 0),
        }
        trend_file = _trends_dir() / f"{table}.json"
        history: List[Dict[str, Any]] = []
        if trend_file.is_file():
            history = json.loads(trend_file.read_text(encoding="utf-8"))
        history = (history + [snapshot])[-365:]
        trend_file.write_text(
            json.dumps(history, indent=2), encoding="utf-8"
        )
        dimensions = _format_dimension_scores(snapshot["dimension_scores"])
        message = (
            f"Recorded a quality snapshot for **{table}** "
            f"(score {_format_value(snapshot['overall_score'])})."
        )
        if dimensions:
            message += "\n\n" + dimensions
        message += (
            f"\nRows: {snapshot['row_count']} · "
            f"Columns: {snapshot['column_count']} · "
            f"Duplicates: {snapshot['duplicate_count']} · "
            f"Missing cells: {snapshot['missing_cells']}"
        )
        return TaskResult(
            success=True,
            data={"table": table, "snapshot": snapshot},
            message=message,
            visualizations=[
                _chart(
                    "indicator",
                    [
                        {
                            "type": "indicator",
                            "mode": "gauge+number",
                            "value": snapshot["overall_score"],
                            "gauge": {
                                "axis": {"range": [0, 100]},
                                "bar": {"color": "#10b981"},
                            },
                        }
                    ],
                    {"title": {"text": f"Quality score — {table}"}},
                )
            ],
        )
    except Exception as exc:  # noqa: BLE001
        return TaskResult(success=False, error=f"Snapshot failed: {exc}")


def run_stats(
    table_name: str,
    *,
    user: Any = None,
    tenant_id: Optional[str] = None,
    column_a: str = "",
    column_b: str = "",
    test_type: str = "ttest",
    group_col: str = "",
    alpha: float = 0.05,
) -> TaskResult:
    denied = _require_permission(user, "query:execute")
    if denied:
        return TaskResult(success=False, error=denied)
    try:
        df = _load_table(table_name, tenant_id)
        numeric = list(df.select_dtypes(include=["number"]).columns)
        if not column_a:
            column_a = numeric[0] if numeric else ""
        if not group_col and test_type == "ttest":
            for col in df.columns:
                if col not in numeric and 2 <= df[col].nunique(dropna=True) <= 2:
                    group_col = str(col)
                    break
        from analysis.statistical import is_error_result, test_hypothesis

        result = test_hypothesis(
            df,
            column_a=column_a,
            column_b=column_b,
            test_type=test_type,
            group_col=group_col,
            alpha=alpha,
        )
        if is_error_result(result):
            return TaskResult(
                success=False,
                error=getattr(result, "error", "Statistical test failed"),
            )
        stat_rows = {
            str(group): float(stats.get("mean", stats.get("median", 0)))
            for group, stats in (result.group_stats or {}).items()
            if isinstance(stats, dict)
            and ("mean" in stats or "median" in stats)
        }
        visualizations = []
        if stat_rows:
            visualizations.append(
                _chart(
                    "bar",
                    [
                        {
                            "type": "bar",
                            "x": list(stat_rows),
                            "y": list(stat_rows.values()),
                            "marker": {"color": "#8b5cf6"},
                        }
                    ],
                    {
                        "title": {"text": f"{column_a} by group"},
                        "xaxis": {"title": {"text": group_col or "Group"}},
                        "yaxis": {"title": {"text": column_a}},
                    },
                )
            )
        message = (
            f"**{result.test_name}**: statistic = "
            f"{_format_value(result.statistic)}, p = {result.p_value:.4g} "
            f"({result.interpretation})."
        )
        if result.effect_size_name:
            message += (
                f"\nEffect size: {result.effect_size_name} = "
                f"{_format_value(result.effect_size)}."
            )
        group_lines = _format_group_stats(result.group_stats)
        if group_lines:
            message += "\n\n" + group_lines
        return TaskResult(
            success=True,
            data=asdict(result),
            message=message,
            visualizations=visualizations,
        )
    except Exception as exc:  # noqa: BLE001
        return TaskResult(success=False, error=f"Statistical test failed: {exc}")


def compare_tables(
    table_before: str,
    table_after: str,
    *,
    user: Any = None,
    tenant_id: Optional[str] = None,
    key_column: str = "",
) -> TaskResult:
    denied = _require_permission(user, "data:read")
    if denied:
        return TaskResult(success=False, error=denied)
    try:
        before = _load_table(table_before, tenant_id)
        after = _load_table(table_after, tenant_id)
        from analysis.comparison import diff_tables

        key = key_column or None
        if key is None:
            shared = [c for c in before.columns if c in after.columns]
            key = shared[0] if shared else None
        diff = diff_tables(before, after, key_column=key)
        message = (
            f"Compared **{table_before}** → **{table_after}**: "
            f"{diff.rows_modified} modified, {diff.rows_added} added, "
            f"{diff.rows_removed} removed."
        )
        changes = _format_table_changes(diff.modified_cells)
        if changes:
            message += "\n\n" + changes
        return TaskResult(
            success=True,
            data={
                "table_before": table_before,
                "table_after": table_after,
                "key_column": key or "",
                "rows_added": diff.rows_added,
                "rows_removed": diff.rows_removed,
                "rows_changed": diff.rows_modified,
                "rows_unchanged": diff.rows_unchanged,
                "modified_cells": diff.modified_cells,
                "summary": diff.summary,
            },
            message=message,
            row_count=diff.rows_modified,
            visualizations=[
                _chart(
                    "bar",
                    [
                        {
                            "type": "bar",
                            "x": ["modified", "added", "removed"],
                            "y": [
                                diff.rows_modified,
                                diff.rows_added,
                                diff.rows_removed,
                            ],
                            "marker": {
                                "color": ["#f59e0b", "#10b981", "#ef4444"]
                            },
                        }
                    ],
                    {
                        "title": {
                            "text": (
                                f"{table_before} → {table_after}"
                            )
                        },
                        "yaxis": {"title": {"text": "Rows"}},
                    },
                )
            ],
        )
    except Exception as exc:  # noqa: BLE001
        return TaskResult(success=False, error=f"Table comparison failed: {exc}")


def compare_periods(
    table_name: str,
    *,
    user: Any = None,
    tenant_id: Optional[str] = None,
    date_col: str = "",
    metric_col: str = "",
    group_col: str = "",
    period_type: str = "month",
    agg_func: str = "sum",
) -> TaskResult:
    denied = _require_permission(user, "data:read")
    if denied:
        return TaskResult(success=False, error=denied)
    try:
        df = _load_table(table_name, tenant_id)
        if not date_col:
            dates = [c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])]
            date_col = str(dates[0]) if dates else ""
        if not metric_col:
            numeric = list(df.select_dtypes(include=["number"]).columns)
            metric_col = str(numeric[0]) if numeric else ""
        from analysis.comparison import compare_periods as _compare

        result = _compare(
            df,
            date_col=date_col,
            metric_col=metric_col,
            group_col=group_col,
            period_type=period_type,
            agg_func=agg_func,
        )
        by_group: Dict[str, List[Dict[str, Any]]] = {}
        for item in result.metrics:
            group = str(item.get("group") or metric_col)
            by_group.setdefault(group, []).append(item)
        visualizations = []
        if by_group:
            traces = []
            for group, rows in by_group.items():
                rows = sorted(
                    rows, key=lambda row: row.get("period_curr", "")
                )
                traces.append(
                    {
                        "type": "scatter",
                        "mode": "lines+markers",
                        "name": group,
                        "x": [row.get("period_curr", "") for row in rows],
                        "y": [row.get("mean_curr", 0) for row in rows],
                    }
                )
            visualizations.append(
                _chart(
                    "line",
                    traces,
                    {
                        "title": {
                            "text": f"{metric_col} by {period_type}"
                        },
                        "yaxis": {"title": {"text": metric_col}},
                    },
                )
            )
        message = result.summary
        changes = _format_period_changes(result.metrics)
        if changes:
            message += "\n\n" + changes
        return TaskResult(
            success=True,
            data={
                "table": table_name,
                "date_col": date_col,
                "metric_col": metric_col,
                "period_type": period_type,
                "summary": result.summary,
                "periods": result.metrics,
                "significant": result.overall_significance,
                "narrative": result.narrative,
            },
            message=message,
            row_count=result.total_compared,
            visualizations=visualizations,
        )
    except Exception as exc:  # noqa: BLE001
        return TaskResult(success=False, error=f"Period comparison failed: {exc}")


def _format_lineage_record(record: Dict[str, Any]) -> str:
    parts = [f"- **{record.get('operation') or 'transform'}**"]
    timestamp = record.get("timestamp")
    if timestamp:
        parts.append(f" · {str(timestamp)[:16].replace('T', ' ')} UTC")
    inputs = ", ".join(record.get("inputs") or [])
    outputs = ", ".join(record.get("outputs") or [])
    if inputs or outputs:
        parts.append(f" · {inputs or '—'} → {outputs or '—'}")
    rows = (record.get("metadata") or {}).get("rows")
    if isinstance(rows, int):
        parts.append(f" · {rows} rows")
    description = record.get("description")
    if description:
        parts.append(f"\n  {description}")
    return "".join(parts)


def lineage_lookup(
    table_name: str, *, user: Any = None, tenant_id: Optional[str] = None
) -> TaskResult:
    denied = _require_permission(user, "data:read")
    if denied:
        return TaskResult(success=False, error=denied)
    try:
        from analysis.lineage import LineageTracker

        table = _validate(table_name)
        tracker = LineageTracker(tenant_id=_tenant(tenant_id))
        records = tracker.get_lineage(table)
        return TaskResult(
            success=True,
            data={
                "table": table,
                "lineage": records,
                "upstream": tracker.get_upstream(table),
                "downstream": tracker.get_downstream(table),
            },
            message=(
                f"**{table}** has {len(records)} lineage record(s):\n\n"
                + "\n".join(_format_lineage_record(r) for r in records)
                if records
                else f"No lineage recorded for **{table}** yet."
            ),
            row_count=len(records),
        )
    except Exception as exc:  # noqa: BLE001
        return TaskResult(success=False, error=f"Lineage lookup failed: {exc}")


def merge_tables(
    left_table: str,
    right_table: str,
    *,
    user: Any = None,
    tenant_id: Optional[str] = None,
    left_key: str = "",
    right_key: str = "",
    join_type: str = "inner",
    output_table: str = "",
) -> TaskResult:
    denied = _require_permission(user, "data:write")
    if denied:
        return TaskResult(success=False, error=denied)
    try:
        left = _load_table(left_table, tenant_id)
        right = _load_table(right_table, tenant_id)
        if join_type not in {"inner", "left", "right", "outer"}:
            return TaskResult(success=False, error=f"Invalid join type: {join_type}")
        shared = [c for c in left.columns if c in right.columns]
        left_key = left_key or (shared[0] if shared else "")
        right_key = right_key or left_key
        if not left_key:
            return TaskResult(
                success=False,
                error=(
                    "These tables share no column to join on. "
                    "Name the key column, for example: merge A and B on id."
                ),
            )
        if left_key not in left.columns or right_key not in right.columns:
            return TaskResult(
                success=False,
                error=(
                    f"Join key '{left_key}' is missing from one of the tables."
                ),
            )
        merged = left.merge(
            right,
            left_on=left_key,
            right_on=right_key,
            how=join_type,
            suffixes=("", "_right"),
        )
        tid = _tenant(tenant_id)
        target = (
            _validate(output_table)
            if output_table
            else _free_output(f"{left_table}_merged", tid)
        )
        _loader().load_dataframe(merged, target, if_exists="fail", tenant_id=tid)
        from api.lineage_service import record_persisted_transform

        record_persisted_transform(
            operation="merge",
            inputs=[left_table, right_table],
            outputs=[target],
            table=target,
            description=f"Merged {left_table} and {right_table} from chat",
            metadata={
                "join_type": join_type,
                "left_key": left_key,
                "right_key": right_key,
                "rows": len(merged),
            },
            tenant_id=tid,
        )
        return TaskResult(
            success=True,
            data={
                "left_table": left_table,
                "right_table": right_table,
                "output_table": target,
                "key": left_key,
                "join_type": join_type,
                "rows": len(merged),
                "columns": list(merged.columns),
                "artifact": {"kind": "table", "name": target},
            },
            message=(
                f"Merged **{left_table}** + **{right_table}** on "
                f"`{left_key}` → **{target}** ({len(merged)} rows × "
                f"{len(merged.columns)} columns)."
            ),
            row_count=len(merged),
        )
    except Exception as exc:  # noqa: BLE001
        return TaskResult(success=False, error=f"Merge failed: {exc}")


def notifications_list(
    *, user: Any = None, unread_only: bool = False, limit: int = 20
) -> TaskResult:
    denied = _require_permission(user, "notification:read")
    if denied:
        return TaskResult(success=False, error=denied)
    try:
        from api.notifications import NotificationManager

        manager = NotificationManager(db_path=_db_path())
        result = manager.get_notifications(
            _user_identifier(user),
            limit=min(max(1, limit), 100),
            unread_only=unread_only,
        )
        items = result.get("notifications", [])
        message = (
            f"You have {len(items)} notification(s)"
            + (" (unread only)." if unread_only else ".")
        )
        if items:
            message += "\n\n" + _format_notifications(items)
        return TaskResult(
            success=True,
            data={
                "notifications": items,
                "unread": sum(1 for item in items if not item.get("is_read")),
                "total": result.get("total", len(items)),
            },
            message=message,
            row_count=len(items),
        )
    except Exception as exc:  # noqa: BLE001
        return TaskResult(success=False, error=f"Notifications failed: {exc}")


def notifications_mark_read(
    *, user: Any = None, notification_id: str = "", all: bool = True
) -> TaskResult:
    denied = _require_permission(user, "notification:read")
    if denied:
        return TaskResult(success=False, error=denied)
    try:
        from api.notifications import NotificationManager

        manager = NotificationManager(db_path=_db_path())
        uid = _user_identifier(user)
        if notification_id and not all:
            changed = 1 if manager.mark_as_read(notification_id, uid) else 0
        else:
            changed = manager.mark_all_as_read(uid)
        return TaskResult(
            success=True,
            data={"marked": changed},
            message=f"Marked {changed} notification(s) as read.",
            row_count=changed,
        )
    except Exception as exc:  # noqa: BLE001
        return TaskResult(success=False, error=f"Could not mark read: {exc}")


def marketplace_products(
    *, user: Any = None, category: str = "", limit: int = 20
) -> TaskResult:
    denied = _require_feature(
        user, "marketplace", "The data marketplace"
    ) or _require_permission(user, "data:read")
    if denied:
        return TaskResult(success=False, error=denied)
    try:
        from api.marketplace import MarketplaceManager

        result = MarketplaceManager(db_path=_db_path()).list_products(
            category=category, limit=min(max(1, limit), 100)
        )
        products = result.get("products", [])
        message = f"Found {len(products)} published data product(s)."
        if products:
            lines = []
            for product in products[:10]:
                name = product.get("name") or product.get("id")
                details = []
                if product.get("domain"):
                    details.append(str(product["domain"]))
                if product.get("quality_score") is not None:
                    details.append(
                        f"quality {_format_value(product['quality_score'])}"
                    )
                line = f"- **{name}**"
                if details:
                    line += f" — {', '.join(details)}"
                lines.append(line)
            message += "\n\n" + "\n".join(lines)
        return TaskResult(
            success=True,
            data=result,
            message=message,
            row_count=len(products),
        )
    except Exception as exc:  # noqa: BLE001
        return TaskResult(success=False, error=f"Marketplace failed: {exc}")


def marketplace_request(
    product_id: str, *, user: Any = None, reason: str = ""
) -> TaskResult:
    denied = _require_feature(
        user, "marketplace", "Requesting marketplace access"
    )
    if denied:
        return TaskResult(success=False, error=denied)
    try:
        from api.marketplace import MarketplaceManager

        manager = MarketplaceManager(db_path=_db_path())
        request = manager.request_access(
            product_id,
            _user_identifier(user),
            requester_name=str(getattr(user, "username", "") or ""),
            reason=reason,
        )
        return TaskResult(
            success=True,
            data=asdict(request) if is_dataclass(request) else dict(request),
            message=f"Access request submitted for product `{product_id}`.",
        )
    except Exception as exc:  # noqa: BLE001
        return TaskResult(success=False, error=f"Access request failed: {exc}")


def org_tree(*, user: Any = None) -> TaskResult:
    denied = _require_feature(
        user, "organization", "The organization view"
    ) or _require_permission(user, "org:view")
    if denied:
        return TaskResult(success=False, error=denied)
    try:
        from api.org import OrgManager

        tree = OrgManager(db_path=_db_path()).get_org_tree()
        departments = tree.get("departments", [])
        teams = tree.get("teams", [])
        groups = tree.get("groups", [])
        message = f"Organization has {len(departments)} department(s)."
        if departments:
            lines = [
                f"- **{dept.get('name') or dept.get('id')}** "
                f"({len(dept.get('teams') or [])} teams)"
                for dept in departments[:10]
            ]
            message += "\n\n" + "\n".join(lines)
        message += (
            f"\n{len(teams)} team(s), {len(groups)} group(s) in total."
        )
        return TaskResult(
            success=True,
            data=tree,
            message=message,
            row_count=len(departments),
        )
    except Exception as exc:  # noqa: BLE001
        return TaskResult(success=False, error=f"Org view failed: {exc}")


def catalog_search(
    query: str, *, user: Any = None, tenant_id: Optional[str] = None
) -> TaskResult:
    denied = _require_permission(user, "catalog:read")
    if denied:
        return TaskResult(success=False, error=denied)
    try:
        from api.catalog import CatalogManager

        results = CatalogManager(db_path=_db_path()).search(
            query, tenant_id=_tenant(tenant_id)
        )
        count = sum(len(items) for items in results.values())
        message = f"Found {count} catalog match(es) for “{query}”."
        matches = _format_catalog_matches(results)
        if matches:
            message += "\n\n" + matches
        return TaskResult(
            success=True,
            data=results,
            message=message,
            row_count=count,
        )
    except Exception as exc:  # noqa: BLE001
        return TaskResult(success=False, error=f"Catalog search failed: {exc}")


def catalog_sync(
    *, user: Any = None, tenant_id: Optional[str] = None
) -> TaskResult:
    denied = _require_feature(
        user, "enterprise", "Catalog sync"
    ) or _require_permission(user, "catalog:manage")
    if denied:
        return TaskResult(success=False, error=denied)
    try:
        from api.catalog import CatalogManager

        result = CatalogManager(db_path=_db_path()).sync_from_warehouse(
            tenant_id=_tenant(tenant_id)
        )
        return TaskResult(
            success=True,
            data=result,
            message=(
                f"Catalog sync complete: {result.get('added', 0)} added, "
                f"{result.get('updated', 0)} updated."
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return TaskResult(success=False, error=f"Catalog sync failed: {exc}")


def catalog_certify(
    asset_id: str, status: str = "certified", *, user: Any = None
) -> TaskResult:
    denied = _require_permission(user, "catalog:certify")
    if denied:
        return TaskResult(success=False, error=denied)
    try:
        from api.catalog import CatalogManager

        ok = CatalogManager(db_path=_db_path()).certify_asset(asset_id, status)
        if not ok:
            return TaskResult(
                success=False,
                error=f"Could not certify asset '{asset_id}'.",
            )
        return TaskResult(
            success=True,
            data={"asset_id": asset_id, "status": status},
            message=f"Asset `{asset_id}` marked **{status}**.",
        )
    except Exception as exc:  # noqa: BLE001
        return TaskResult(success=False, error=f"Certification failed: {exc}")


def catalog_add_term(
    term: str,
    definition: str,
    *,
    user: Any = None,
    domain: str = "general",
) -> TaskResult:
    denied = _require_permission(user, "catalog:write")
    if denied:
        return TaskResult(success=False, error=denied)
    try:
        from api.catalog import CatalogManager

        created = CatalogManager(db_path=_db_path()).create_term(
            term=term, definition=definition, domain=domain
        )
        return TaskResult(
            success=True,
            data=asdict(created) if is_dataclass(created) else dict(created),
            message=f"Added business term **{term}** to the catalog.",
        )
    except Exception as exc:  # noqa: BLE001
        return TaskResult(success=False, error=f"Could not add term: {exc}")


def catalog_add_metric(
    name: str,
    formula: str,
    *,
    user: Any = None,
    description: str = "",
    domain: str = "general",
) -> TaskResult:
    denied = _require_permission(user, "catalog:write")
    if denied:
        return TaskResult(success=False, error=denied)
    try:
        from api.catalog import CatalogManager

        created = CatalogManager(db_path=_db_path()).create_metric(
            name=name,
            description=description,
            formula=formula,
            domain=domain,
        )
        return TaskResult(
            success=True,
            data=asdict(created) if is_dataclass(created) else dict(created),
            message=f"Added metric **{name}** to the catalog.",
        )
    except Exception as exc:  # noqa: BLE001
        return TaskResult(success=False, error=f"Could not add metric: {exc}")


def pipeline_run(name: str, *, user: Any = None) -> TaskResult:
    denied = _require_feature(
        user, "automation", "Running pipelines"
    ) or _require_permission(user, "pipeline:run")
    if denied:
        return TaskResult(success=False, error=denied)
    if not name:
        return TaskResult(success=False, error="Which pipeline should I run?")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name):
        return TaskResult(success=False, error="Invalid pipeline name")
    try:
        path = Path("config/pipelines") / f"{name}.json"
        if not path.is_file():
            return TaskResult(success=False, error=f"Pipeline '{name}' not found")
        definition = json.loads(path.read_text(encoding="utf-8"))
        from analysis.pipeline import ETLPipeline
        from config import settings

        pipeline = ETLPipeline.from_dict(definition)
        errors = pipeline.validate()
        if errors:
            return TaskResult(
                success=False, error=f"Validation errors: {'; '.join(errors)}"
            )
        result = pipeline.run(
            loader=_loader(),
            db_path=settings.duckdb.database_path,
            schema=settings.duckdb.schema,
        )
        if not result.success:
            return TaskResult(
                success=False, error=result.error or "Pipeline failed"
            )
        message = (
            f"Pipeline **{result.pipeline_name}** completed "
            f"{result.steps_completed}/{result.steps_total} steps"
        )
        if result.execution_time_ms:
            message += f" in {result.execution_time_ms / 1000:.1f}s"
        message += "."
        if result.output_tables:
            message += f"\nOutputs: {', '.join(result.output_tables)}."
        return TaskResult(
            success=True,
            data={
                "pipeline_name": result.pipeline_name,
                "steps_completed": result.steps_completed,
                "steps_total": result.steps_total,
                "output_tables": result.output_tables,
                "execution_time_ms": result.execution_time_ms,
            },
            message=message,
        )
    except Exception as exc:  # noqa: BLE001
        return TaskResult(success=False, error=f"Pipeline run failed: {exc}")


def workflow_pending(*, user: Any = None) -> TaskResult:
    denied = _require_feature(
        user, "automation", "Workflow approvals"
    )
    if denied:
        return TaskResult(success=False, error=denied)
    try:
        from api.workflows import WorkflowManager

        manager = WorkflowManager(db_path=_db_path())
        pending = manager.get_pending_approvals(
            _user_identifier(user), str(getattr(user, "role", "") or "")
        )
        message = f"You have {len(pending)} workflow approval(s) pending."
        if pending:
            lines = []
            for inst in pending[:10]:
                name = (
                    inst.get("workflow_name")
                    or inst.get("workflow_def_id")
                    or inst.get("id")
                )
                line = f"- **{name}** (`{inst.get('id')}`)"
                total = inst.get("total_steps")
                if total:
                    line += (
                        f" — step {int(inst.get('current_step', 0)) + 1}/{total}"
                    )
                if inst.get("initiator_name"):
                    line += f", requested by {inst['initiator_name']}"
                lines.append(line)
            message += "\n\n" + "\n".join(lines)
        return TaskResult(
            success=True,
            data={"pending": pending},
            message=message,
            row_count=len(pending),
        )
    except Exception as exc:  # noqa: BLE001
        return TaskResult(success=False, error=f"Workflow lookup failed: {exc}")


def workflow_approve(
    instance_id: str, *, user: Any = None, comment: str = ""
) -> TaskResult:
    denied = _require_feature(
        user, "automation", "Workflow approvals"
    )
    if denied:
        return TaskResult(success=False, error=denied)
    try:
        from api.workflows import WorkflowManager

        manager = WorkflowManager(db_path=_db_path())
        ok = manager.approve(
            instance_id,
            actor_id=_user_identifier(user),
            actor_name=str(getattr(user, "username", "") or ""),
            comment=comment,
        )
        if not ok:
            return TaskResult(
                success=False,
                error=f"Workflow '{instance_id}' is not awaiting approval.",
            )
        return TaskResult(
            success=True,
            data={"instance_id": instance_id},
            message=f"Approved workflow `{instance_id}`.",
        )
    except Exception as exc:  # noqa: BLE001
        return TaskResult(success=False, error=f"Approval failed: {exc}")


def export_table(
    table_name: str, *, user: Any = None, fmt: str = "csv"
) -> TaskResult:
    denied = _require_feature(
        user, "export_page", "Exporting tables"
    )
    if denied:
        return TaskResult(success=False, error=denied)
    try:
        table = _validate(table_name)
        key = str(fmt or "csv").lower().lstrip(".")
        if key not in _EXPORT_FORMATS:
            return TaskResult(
                success=False,
                error=(
                    f"Unsupported export format '{fmt}'. "
                    f"Choose from: {', '.join(sorted(_EXPORT_FORMATS))}."
                ),
            )
        permission, endpoint = _EXPORT_FORMATS[key]
        denied = _require_permission(user, permission)
        if denied:
            return TaskResult(success=False, error=denied)
        url = f"/export/{endpoint}/{table}"
        return TaskResult(
            success=True,
            data={"table": table, "format": key, "url": url},
            message=f"Export **{table}** as {key.upper()}: [{url}]({url})",
        )
    except Exception as exc:  # noqa: BLE001
        return TaskResult(success=False, error=f"Export failed: {exc}")


def connections_list(*, user: Any = None) -> TaskResult:
    return TaskResult(
        success=False,
        error=(
            "Data connections hold credentials and are managed by an admin "
            "in the Connections page; they are not readable from chat."
        ),
    )
