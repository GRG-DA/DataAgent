"""Chat Pro task executors: persisted cleaning, dashboards, forecasting.

The streamed chat runs its side effects inside the request, so the RBAC
middleware gate on ``/llm/chat`` (``query:execute``) does not cover them.
Each mutating executor therefore checks the caller's permission itself and
fails closed when the role is not allowed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

_MAX_FORECAST_POINTS = 100_000


@dataclass
class TaskResult:
    """Result of a task step (clean / dashboard / forecast)."""

    success: bool
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    message: str = ""
    row_count: int = 0
    visualizations: List[Dict[str, Any]] = field(default_factory=list)


def _user_identifier(user: Any) -> str:
    if user is None:
        return ""
    return str(
        getattr(user, "user_id", "")
        or getattr(user, "id", "")
        or getattr(user, "username", "")
        or getattr(user, "email", "")
        or ""
    )


def _require_permission(user: Any, permission: str) -> Optional[str]:
    """Return an error message when *user* lacks *permission*, else None."""
    if user is None:
        return None
    from api.rbac import Permission, role_manager

    try:
        perm = Permission(permission)
    except ValueError:
        return None
    if role_manager.user_has_permission(getattr(user, "role", "user"), perm):
        return None
    if permission in (getattr(user, "scopes", None) or set()):
        return None
    return (
        f"Your role ({getattr(user, 'role', 'unknown')}) is not allowed to "
        f"{permission}."
    )


def _loader():
    import api.state as state

    return state.loader


def _quote(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _read_table(table: str, tenant_id: Optional[str]) -> pd.DataFrame:
    loader = _loader()
    safe = _quote(table)
    with loader.get_connection(tenant_id=tenant_id) as conn:
        return conn.execute(f"SELECT * FROM {safe}").df()


def clean_table(
    table_name: str,
    *,
    user: Any = None,
    tenant_id: Optional[str] = None,
    output_table: Optional[str] = None,
    dedup: bool = True,
    null_strategy: str = "drop",
    trim_whitespace: bool = True,
    empty_to_null: bool = True,
) -> TaskResult:
    """Clean *table_name* into a NEW table; the source is never modified."""
    denied = _require_permission(user, "data:write")
    if denied:
        return TaskResult(success=False, error=denied)

    from api.routes.data import (
        _MAX_CLEAN_APPLY_ROWS,
        _clean_dataframe_ops,
        _free_output_table,
    )
    from api.security_paths import validate_table_name

    try:
        table = validate_table_name(table_name)
        loader = _loader()
        if not loader.table_exists(table, tenant_id=tenant_id):
            return TaskResult(success=False, error=f"Table '{table}' not found")

        df_before = _read_table(table, tenant_id)
        if len(df_before) > _MAX_CLEAN_APPLY_ROWS:
            return TaskResult(
                success=False,
                error=(
                    "Table is too large to clean in chat "
                    f"(max {_MAX_CLEAN_APPLY_ROWS} rows)"
                ),
            )

        df_after, changes = _clean_dataframe_ops(
            df_before,
            dedup=dedup,
            null_strategy=null_strategy,
            drop_columns=[],
            trim_whitespace=trim_whitespace,
            empty_to_null=empty_to_null,
        )
        if df_after.empty:
            return TaskResult(
                success=False,
                error="No rows left after cleaning; source table left unchanged",
            )

        if output_table:
            target = validate_table_name(output_table)
            if target == table:
                return TaskResult(
                    success=False,
                    error="Refusing to overwrite the source table",
                )
        else:
            target = _free_output_table(table, tenant_id=tenant_id)

        loader.load_dataframe(
            df_after, target, if_exists="fail", tenant_id=tenant_id
        )

        from api.lineage_service import identity_column_map, record_persisted_transform
        from api.tenancy import current_tenant_id

        record_persisted_transform(
            operation="clean",
            inputs=[table],
            outputs=[target],
            table=target,
            description=f"Cleaned {table} into {target} from chat",
            metadata={
                "rows_before": len(df_before),
                "rows_after": len(df_after),
                "changes": changes,
                "dedup": dedup,
                "null_strategy": null_strategy,
            },
            column_map=identity_column_map(
                table,
                [col for col in df_after.columns if col in df_before.columns],
            ),
            tenant_id=tenant_id or current_tenant_id(),
        )

        rows_before = len(df_before)
        rows_after = len(df_after)
        change_bits: List[str] = []
        if rows_before != rows_after:
            change_bits.append(f"{rows_before - rows_after} rows removed")
        if changes:
            change_bits.extend(str(c) for c in changes[:4])
        detail = f" ({'; '.join(change_bits)})" if change_bits else ""
        return TaskResult(
            success=True,
            data={
                "source_table": table,
                "output_table": target,
                "rows_before": rows_before,
                "rows_after": rows_after,
                "changes": changes,
                "artifact": {"kind": "table", "name": target},
            },
            message=(
                f"✅ Cleaned **{table}** → **{target}**: "
                f"{rows_before} → {rows_after} rows{detail}."
            ),
            row_count=rows_after,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Task clean_table failed for %s: %s", table_name, e)
        return TaskResult(success=False, error=f"Cleaning failed: {e}")


def build_dashboard(
    table_name: str,
    *,
    user: Any = None,
    tenant_id: Optional[str] = None,
    owner_id: str = "",
    owner: str = "",
    name: Optional[str] = None,
) -> TaskResult:
    """Auto-plan *table_name* and save the resulting dashboard."""
    denied = _require_permission(user, "dashboard:create")
    if denied:
        return TaskResult(success=False, error=denied)

    from api.dashboard_build import create_dashboard_for_table
    from api.security_paths import validate_table_name

    try:
        table = validate_table_name(table_name)
        out = create_dashboard_for_table(
            table,
            owner_id=owner_id or _user_identifier(user),
            owner=owner or str(getattr(user, "username", "") or ""),
            name=name,
            tenant_id=tenant_id,
        )
        return TaskResult(
            success=True,
            data={
                **out,
                "artifact": {
                    "kind": "dashboard",
                    "id": out["id"],
                    "name": out["name"],
                    "url": out["url"],
                },
            },
            message=(
                f"📊 Dashboard **{out['name']}** saved with "
                f"{out['widget_count']} widgets — [open it]({out['url']})."
            ),
            row_count=out.get("widget_count", 0),
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Task build_dashboard failed for %s: %s", table_name, e)
        return TaskResult(success=False, error=f"Dashboard build failed: {e}")


def _detect_date_value_columns(df: pd.DataFrame):
    date_col = next(
        (c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])),
        None,
    )
    if date_col is None:
        for c in df.columns:
            if not pd.api.types.is_string_dtype(df[c]):
                continue
            try:
                pd.to_datetime(df[c].dropna().head(5), errors="raise")
                date_col = c
                break
            except Exception:  # noqa: BLE001
                continue
    value_col = next(
        (
            c
            for c in df.columns
            if c != date_col and pd.api.types.is_numeric_dtype(df[c])
        ),
        None,
    )
    return date_col, value_col


def forecast_table(
    table_name: str,
    *,
    user: Any = None,
    tenant_id: Optional[str] = None,
    periods: int = 6,
) -> TaskResult:
    """Forecast the table's primary time series (advanced model, auto method)."""
    from api.security_paths import validate_table_name

    try:
        table = validate_table_name(table_name)
        loader = _loader()
        if not loader.table_exists(table):
            return TaskResult(success=False, error=f"Table '{table}' not found")

        df = _read_table(table, tenant_id)
        if df.empty:
            return TaskResult(success=False, error=f"Table '{table}' has no rows")
        if len(df) > _MAX_FORECAST_POINTS:
            return TaskResult(
                success=False,
                error=(
                    "Table is too large to forecast in chat "
                    f"(max {_MAX_FORECAST_POINTS} rows)"
                ),
            )

        date_col, value_col = _detect_date_value_columns(df)
        if not date_col or not value_col:
            return TaskResult(
                success=False,
                error=(
                    "Could not detect a date column and a numeric measure in "
                    f"'{table}' (columns: {list(df.columns)})"
                ),
            )

        periods = max(1, min(int(periods or 6), 24))
        from analysis import advanced_forecast

        result = advanced_forecast(
            df,
            date_col,
            value_col,
            periods=periods,
            method="auto",
            confidence=0.95,
            seasonality_mode="multiplicative",
        )
        payload = _as_dict(result)
        forecast_values = payload.get("forecast_values") or []
        forecast_dates = payload.get("forecast_dates") or []
        if not forecast_values:
            return TaskResult(
                success=False,
                error="The forecast model returned no values",
            )

        lines = [
            f"📈 **Forecast for {table}** — {value_col} by {date_col}, "
            f"next {len(forecast_values)} periods:",
            "",
            "| Period | Forecast |",
            "| --- | --- |",
        ]
        for d, v in zip(forecast_dates, forecast_values):
            lines.append(f"| {d} | {_fmt(v)} |")
        accuracy = payload.get("accuracy") or payload.get("metrics") or {}
        if isinstance(accuracy, dict) and accuracy:
            bits = [
                f"{k.upper()} {_fmt(v)}"
                for k, v in accuracy.items()
                if isinstance(v, (int, float))
            ]
            if bits:
                lines += ["", "*Accuracy: " + " · ".join(bits) + "*"]

        return TaskResult(
            success=True,
            data={
                "table": table,
                "date_col": date_col,
                "value_col": value_col,
                "forecast_dates": forecast_dates,
                "forecast_values": forecast_values,
                "forecast": payload,
            },
            message="\n".join(lines),
            row_count=len(forecast_values),
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Task forecast failed for %s: %s", table_name, e)
        return TaskResult(success=False, error=f"Forecast failed: {e}")


def _as_dict(obj: Any) -> Dict[str, Any]:
    from dataclasses import asdict, is_dataclass

    if isinstance(obj, dict):
        return obj
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    return {}


def _fmt(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}"
