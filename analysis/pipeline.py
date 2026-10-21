"""
ETL Pipeline Module
Simple DAG-based ETL pipeline builder for chaining extract → clean → load steps
"""

import base64
import io
import logging
import re
import uuid
import contextlib
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np
import pandas as pd
from datetime import datetime

from etl.pandas_compat import register_dataframe
from api.tenancy import tenant_search_path

logger = logging.getLogger(__name__)


@dataclass
class PipelineStep:
    """A single step in an ETL pipeline."""
    name: str
    step_type: str  # extract, clean, transform, load, sql
    params: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)
    enabled: bool = True


@dataclass
class PipelineResult:
    """Result of a pipeline execution."""
    success: bool
    pipeline_name: str
    steps_completed: int
    steps_total: int
    step_results: Dict[str, Any] = field(default_factory=dict)
    output_tables: List[str] = field(default_factory=list)
    error: Optional[str] = None
    execution_time_ms: float = 0
    started_at: str = ""
    completed_at: str = ""


class ETLPipeline:
    """Simple ETL pipeline builder that chains operations."""

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.steps: List[PipelineStep] = []
        self._data: Dict[str, pd.DataFrame] = {}
        # Optional callable returning True when the caller requests cancellation.
        # Checked between steps; a cancelled run returns success=False.
        self.cancel_check: Optional[Callable[[], bool]] = None

    def add_step(self, step: PipelineStep) -> "ETLPipeline":
        """Add a step to the pipeline."""
        self.steps.append(step)
        return self

    def add_extract(self, name: str, source_type: str = "file", **kwargs) -> "ETLPipeline":
        """Add an extraction step."""
        return self.add_step(PipelineStep(name=name, step_type="extract",
                                          params={"source_type": source_type, **kwargs}))

    def add_clean(self, name: str, input_data: str = "", **kwargs) -> "ETLPipeline":
        """Add a cleaning step."""
        return self.add_step(PipelineStep(name=name, step_type="clean",
                                          params={"input_data": input_data, **kwargs},
                                          depends_on=[input_data] if input_data else []))

    def add_transform(self, name: str, input_data: str = "", operation: str = "", **kwargs) -> "ETLPipeline":
        """Add a transform step."""
        return self.add_step(PipelineStep(name=name, step_type="transform",
                                          params={"input_data": input_data, "operation": operation, **kwargs},
                                          depends_on=[input_data] if input_data else []))

    def add_sql(self, name: str, query: str, connection: str = "warehouse", **kwargs) -> "ETLPipeline":
        """Add a SQL query step."""
        return self.add_step(PipelineStep(name=name, step_type="sql",
                                          params={"query": query, "connection": connection, **kwargs}))

    def add_load(self, name: str, input_data: str = "", table_name: str = "", **kwargs) -> "ETLPipeline":
        """Add a load step."""
        return self.add_step(PipelineStep(name=name, step_type="load",
                                          params={"input_data": input_data, "table_name": table_name, **kwargs},
                                          depends_on=[input_data] if input_data else []))

    def validate(self) -> List[str]:
        """Validate the pipeline DAG (check for circular deps)."""
        errors = []
        step_names = {s.name for s in self.steps}

        for step in self.steps:
            for dep in set(step.depends_on):
                if dep not in step_names:
                    errors.append(f"Step '{step.name}' depends on '{dep}' which doesn't exist")

        # Check for circular dependencies (simplified)
        visited = set()
        path = []

        def dfs(node):
            if node in path:
                cycle = path[path.index(node):] + [node]
                errors.append(f"Circular dependency detected: {' → '.join(cycle)}")
                return
            if node in visited:
                return
            visited.add(node)
            path.append(node)
            step = next((s for s in self.steps if s.name == node), None)
            if step:
                for dep in step.depends_on:
                    dfs(dep)
            path.pop()

        for s in self.steps:
            if s.name not in visited:
                dfs(s.name)

        return errors

    def run(self, loader: Optional[Any] = None, db_path: str = "", schema: str = "") -> PipelineResult:
        """Execute the pipeline steps in dependency order."""
        import time
        from etl.extract import DataExtractor
        from etl.clean import DataCleaner

        start = time.time()
        started_at = datetime.now().isoformat()
        errors = self.validate()

        if errors:
            return PipelineResult(
                success=False, pipeline_name=self.name,
                steps_completed=0, steps_total=len(self.steps),
                error=f"Validation errors: {'; '.join(errors)}",
                started_at=started_at,
            )

        # Topological sort
        sorted_steps = self._topological_sort()
        step_results = {}
        output_tables = []

        extractor = DataExtractor()
        cleaner = DataCleaner()

        for step in sorted_steps:
            if not step.enabled:
                step_results[step.name] = {"status": "skipped"}
                continue

            # Honour cancellation requests between steps
            if self.cancel_check is not None and self.cancel_check():
                logger.info(f"Pipeline '{self.name}' cancelled by user before step '{step.name}'")
                return PipelineResult(
                    success=False, pipeline_name=self.name,
                    steps_completed=sum(1 for r in step_results.values() if r.get("status") == "completed"),
                    steps_total=len(self.steps),
                    step_results=step_results, output_tables=output_tables,
                    error="Pipeline cancelled by user",
                    execution_time_ms=round((time.time() - start) * 1000, 2),
                    started_at=started_at, completed_at=datetime.now().isoformat(),
                )

            logger.info(f"Running pipeline step: {step.name} ({step.step_type})")
            step_start = time.time()
            try:
                if step.step_type == "extract":
                    source = step.params.get("source_type", "file")
                    if source == "file":
                        file_path = step.params.get("file_path", "")
                        file_path = Path(file_path)
                        data = extractor.extract(file_path)
                        for sheet_name, df in data.items():
                            data_key = step.params.get("output_name", step.name)
                            self._data[data_key] = df
                            step_results[step.name] = {
                                "status": "completed", "rows": len(df), "columns": list(df.columns),
                                "sheets": list(data.keys()),
                            }
                    elif source == "warehouse":
                        table = step.params.get("table", "")
                        if not table:
                            raise ValueError("Data Source node has no table selected")
                        limit = int(step.params.get("limit", 1000) or 1000)
                        row_filter = str(step.params.get("filter", "") or "").strip()
                        for token in (";", "--", "/*"):
                            if token in row_filter:
                                raise ValueError(f"Data Source filter contains forbidden SQL sequence: {token}")
                        ident = '"' + table.replace('"', '""') + '"'
                        query = f"SELECT * FROM {ident}"
                        if row_filter:
                            query += f" WHERE {row_filter}"
                        query += f" LIMIT {limit}"
                        import duckdb
                        conn = duckdb.connect(db_path)
                        conn.execute(f"SET search_path = '{tenant_search_path()}'")
                        df = conn.execute(query).df()
                        conn.close()
                        data_key = step.params.get("output_name", step.name)
                        self._data[data_key] = df
                        step_results[step.name] = {"status": "completed", "rows": len(df),
                                                   "columns": list(df.columns), "query": query}
                    else:
                        step_results[step.name] = {"status": "error", "error": f"Unknown source: {source}"}
                elif step.step_type == "clean":
                    input_key = step.params.get("input_data", "")
                    df = self._data.get(input_key)
                    if df is None:
                        raise ValueError(f"Input data '{input_key}' not found")
                    cleaning_plan = step.params.get("plan", None)
                    if cleaning_plan and isinstance(cleaning_plan, dict):
                        # Convert dict plan to CleaningPlan object
                        from etl.clean import CleaningPlan, ColumnCleaningRule, CleaningStrategy, DeduplicationStrategy
                        plan_obj = CleaningPlan()
                        if "deduplication" in cleaning_plan:
                            try:
                                plan_obj.deduplication = DeduplicationStrategy(cleaning_plan["deduplication"])
                            except ValueError:
                                plan_obj.deduplication = DeduplicationStrategy.FIRST
                        if "null_strategy" in cleaning_plan:
                            try:
                                plan_obj.global_null_strategy = CleaningStrategy(cleaning_plan["null_strategy"])
                            except ValueError:
                                plan_obj.global_null_strategy = CleaningStrategy.DROP
                        if "drop_columns" in cleaning_plan:
                            plan_obj.drop_columns = cleaning_plan["drop_columns"]
                        if "column_rules" in cleaning_plan and isinstance(cleaning_plan["column_rules"], dict):
                            for col, rule in cleaning_plan["column_rules"].items():
                                if isinstance(rule, dict):
                                    strategy = CleaningStrategy(rule.get("strategy", "drop"))
                                    plan_obj.column_rules[col] = ColumnCleaningRule(
                                        column=col, strategy=strategy,
                                        fill_value=rule.get("fill_value"),
                                        dtype=rule.get("dtype"),
                                    )
                        cleaned = cleaner.clean(df, plan_obj)
                    elif cleaning_plan:
                        cleaned = cleaner.clean(df, cleaning_plan)
                    else:
                        cleaned = cleaner.clean(df)
                    output_key = step.params.get("output_name", step.name)
                    self._data[output_key] = cleaned
                    step_results[step.name] = {"status": "completed", "rows_before": len(df), "rows_after": len(cleaned)}

                elif step.step_type == "transform":
                    input_key = step.params.get("input_data", "")
                    df = self._data.get(input_key)
                    if df is None:
                        raise ValueError(f"Input data '{input_key}' not found")
                    operation = step.params.get("operation", "")
                    output_key = step.params.get("output_name", step.name)
                    rows_before = len(df)

                    if operation == "drop_columns":
                        cols = step.params.get("columns", [])
                        df = df.drop(columns=[c for c in cols if c in df.columns])
                    elif operation == "rename_columns":
                        mapping = step.params.get("mapping", {})
                        df = df.rename(columns=mapping)
                    elif operation == "filter":
                        df = self._op_filter(df, step.params)
                    elif operation == "join":
                        df = self._op_join(df, step.params)
                    elif operation == "aggregate":
                        df, agg_note = self._op_aggregate(df, step.params)
                    elif operation == "custom":
                        df, printed = self._op_custom(df, step.params)
                    elif operation == "analysis":
                        summary = self._op_analysis(df, step.params)
                    elif operation == "chart":
                        summary, chart_info = self._op_chart(df, step.params)
                    elif operation == "insights":
                        summary = self._op_insights(df, step.params)
                    elif operation == "fill_null":
                        col = step.params.get("column", "")
                        fill_val = step.params.get("fill_value", 0)
                        if col and col in df.columns:
                            df[col] = df[col].fillna(fill_val)
                        else:
                            df = df.fillna(fill_val)
                    elif operation == "sort":
                        col = step.params.get("column", "")
                        ascending = step.params.get("ascending", True)
                        if col in df.columns:
                            df = df.sort_values(col, ascending=ascending)
                    elif operation == "anomaly":
                        df, summary = self._op_anomaly(df, step.params)
                    elif operation == "forecast":
                        df, summary = self._op_forecast(df, step.params)
                    elif operation == "stat_test":
                        df, summary = self._op_stat_test(df, step.params)
                    elif operation == "regression":
                        df, summary = self._op_regression(df, step.params)
                    elif operation == "quality":
                        df, summary = self._op_quality(df, step.params)
                    elif operation == "comparison":
                        df, summary = self._op_comparison(df, step.params)
                    elif operation == "cohort":
                        df, summary = self._op_cohort(df, step.params)
                    elif operation == "ml_model":
                        df, summary = self._op_ml_model(df, step.params)

                    self._data[output_key] = df
                    result_entry: Dict[str, Any] = {"status": "completed", "rows": len(df), "operation": operation}
                    if rows_before != len(df):
                        result_entry["rows_before"] = rows_before
                    if operation == "custom" and printed:
                        result_entry["print_output"] = printed[:4000]
                    if operation in ("analysis", "insights", "chart", "anomaly", "forecast",
                                     "stat_test", "regression", "quality", "comparison",
                                     "cohort", "ml_model") and summary:
                        result_entry["summary"] = summary
                    if operation == "chart" and chart_info:
                        result_entry.update(chart_info)
                    if operation == "aggregate" and agg_note:
                        result_entry["summary"] = agg_note
                    step_results[step.name] = result_entry

                elif step.step_type == "sql":
                    query = step.params.get("query", "").strip()
                    if not query:
                        raise ValueError("SQL node has an empty query")
                    if db_path:
                        import duckdb
                        conn = duckdb.connect(str(db_path))
                        if schema:
                            conn.execute(f"SET search_path = '{tenant_search_path()}'")

                        # Register upstream data so queries can use `input` (and a
                        # second connected input as `input2`) directly as table names.
                        input_key = step.params.get("input_data", "")
                        input_df = self._data.get(input_key)
                        if input_df is not None:
                            register_dataframe(conn, "pipeline_input", input_df)
                            register_dataframe(conn, "input", input_df)
                        input2_key = step.params.get("input_data2", "")
                        input2_df = self._data.get(input2_key)
                        if input2_df is not None:
                            register_dataframe(conn, "input2", input2_df)
                        try:
                            df = conn.execute(query.rstrip(";")).df()
                        finally:
                            conn.close()
                        limit = int(step.params.get("limit", 0) or 0)
                        if limit > 0:
                            df = df.head(limit)
                        output_key = step.params.get("output_name", step.name)
                        self._data[output_key] = df
                        step_results[step.name] = {"status": "completed", "rows": len(df)}
                    else:
                        step_results[step.name] = {"status": "error", "error": "No database connection"}

                elif step.step_type == "load":
                    input_key = step.params.get("input_data", step.name)
                    df = self._data.get(input_key)
                    if df is None:
                        raise ValueError(f"Input data '{input_key}' not found")
                    table_name = step.params.get("table_name", step.name)
                    file_format = str(step.params.get("format", "") or "").strip().lower()

                    if file_format:
                        # Export node: write the DataFrame to a downloadable file
                        ext_map = {"csv": "csv", "xlsx": "xlsx", "excel": "xlsx",
                                   "parquet": "parquet", "json": "json"}
                        ext = ext_map.get(file_format)
                        if not ext:
                            raise ValueError(f"Unsupported export format: {file_format}")
                        filename = step.params.get("filename") or f"{input_key}_export"
                        safe_filename = re.sub(r"[^A-Za-z0-9_\-]", "_", str(filename))
                        out_dir = Path("outputs/exports")
                        out_dir.mkdir(parents=True, exist_ok=True)
                        file_path = out_dir / f"{safe_filename}.{ext}"
                        if ext == "csv":
                            df.to_csv(file_path, index=False)
                        elif ext == "xlsx":
                            df.to_excel(file_path, index=False)
                        elif ext == "parquet":
                            df.to_parquet(file_path, index=False)
                        else:
                            df.to_json(file_path, orient="records", indent=2)
                        step_results[step.name] = {
                            "status": "completed", "file": str(file_path), "rows": len(df),
                            "format": ext,
                            "summary": f"Exported {len(df)} rows to {file_path}",
                        }
                    elif loader:
                        if_exists = step.params.get("if_exists", "replace")
                        loader.load_dataframe(df, table_name, if_exists=if_exists)
                        output_tables.append(table_name)
                        step_results[step.name] = {"status": "completed", "table": table_name, "rows": len(df)}
                    else:
                        step_results[step.name] = {"status": "skipped", "reason": "No loader provided"}

            except Exception as e:
                logger.error(f"Pipeline step '{step.name}' failed: {e}")
                step_results[step.name] = {"status": "error", "error": str(e)}
                return PipelineResult(
                    success=False, pipeline_name=self.name,
                    steps_completed=sum(1 for r in step_results.values() if r.get("status") == "completed"),
                    steps_total=len(self.steps),
                    step_results=step_results, output_tables=output_tables,
                    error=f"Step '{step.name}' failed: {e}",
                    execution_time_ms=round((time.time() - start) * 1000, 2),
                    started_at=started_at, completed_at=datetime.now().isoformat(),
                )
            else:
                entry = step_results.get(step.name)
                if isinstance(entry, dict):
                    entry["duration_ms"] = round((time.time() - step_start) * 1000, 2)

        return PipelineResult(
            success=True, pipeline_name=self.name,
            steps_completed=sum(1 for r in step_results.values() if r.get("status") == "completed"),
            steps_total=len(self.steps),
            step_results=step_results, output_tables=output_tables,
            execution_time_ms=round((time.time() - start) * 1000, 2),
            started_at=started_at, completed_at=datetime.now().isoformat(),
        )

    # ------------------------------------------------------------------
    # Transform operation helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _coerce_value(series: pd.Series, value: Any) -> Any:
        """Best-effort cast of a scalar filter value to the column's dtype."""
        try:
            if pd.api.types.is_bool_dtype(series):
                if isinstance(value, str):
                    return value.strip().lower() in ("true", "1", "yes")
                return bool(value)
            if pd.api.types.is_numeric_dtype(series):
                return float(value)
            if pd.api.types.is_datetime64_any_dtype(series):
                return pd.to_datetime(value)
        except (TypeError, ValueError):
            pass
        return value

    def _op_filter(self, df: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame:
        col = str(params.get("column", "") or "")
        raw_op = str(params.get("operator") or params.get("op") or "equals").strip()
        val = params.get("value", "")
        symbol_map = {">": "greater_than", "<": "less_than", ">=": "greater_equal",
                      "<=": "less_equal", "==": "equals", "=": "equals", "!=": "not_equals"}
        op = symbol_map.get(raw_op, raw_op)

        if not col:
            raise ValueError("Filter node has no column configured")
        if col not in df.columns:
            raise ValueError(f"Filter column '{col}' not found. Available: {', '.join(map(str, df.columns[:20]))}")

        s = df[col]
        v = self._coerce_value(s, val)
        if op == "equals":
            mask = s == v
        elif op == "not_equals":
            mask = s != v
        elif op == "greater_than":
            mask = s > v
        elif op == "less_than":
            mask = s < v
        elif op == "greater_equal":
            mask = s >= v
        elif op == "less_equal":
            mask = s <= v
        elif op == "contains":
            mask = s.astype(str).str.contains(str(val), case=False, na=False, regex=False)
        elif op == "not_contains":
            mask = ~s.astype(str).str.contains(str(val), case=False, na=False, regex=False)
        elif op == "starts_with":
            mask = s.astype(str).str.lower().str.startswith(str(val).lower(), na=False)
        elif op == "ends_with":
            mask = s.astype(str).str.lower().str.endswith(str(val).lower(), na=False)
        elif op == "between":
            parts = [p.strip() for p in str(val).split(",") if p.strip()]
            if len(parts) < 2:
                raise ValueError("Between filter needs two comma-separated values, e.g. '10, 50'")
            lo, hi = self._coerce_value(s, parts[0]), self._coerce_value(s, parts[1])
            mask = (s >= lo) & (s <= hi)
        elif op == "in_list":
            candidates = [p.strip() for p in str(val).split(",") if p.strip()]
            coerced = {str(self._coerce_value(s, c)) for c in candidates} | set(candidates)
            mask = s.astype(str).isin(coerced)
        else:
            raise ValueError(f"Unsupported filter operator: '{raw_op}'")

        try:
            return df[mask.fillna(False)]
        except AttributeError:
            return df[mask.astype(bool)]

    def _op_join(self, df: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame:
        right_key_name = str(params.get("input_data2", "") or "")
        right = self._data.get(right_key_name)
        if right is None:
            raise ValueError(
                f"Join node needs a second connected input (got '{right_key_name or 'none'}'). "
                "Connect another Data Source/step into the Join node."
            )
        join_type = str(params.get("join_type", "inner") or "inner").lower()
        left_key = str(params.get("left_key", "") or "")
        right_key = str(params.get("right_key", "") or "")
        if not left_key or not right_key:
            raise ValueError("Join node needs both Left Key and Right Key columns")
        if left_key not in df.columns:
            raise ValueError(f"Left key '{left_key}' not found in left input. Available: {', '.join(map(str, df.columns[:20]))}")
        if right_key not in right.columns:
            raise ValueError(f"Right key '{right_key}' not found in second input. Available: {', '.join(map(str, right.columns[:20]))}")

        how = {"inner": "inner", "left": "left", "right": "right",
               "outer": "outer", "full": "outer", "full_outer": "outer"}.get(join_type, "inner")
        if left_key == right_key:
            merged = df.merge(right, how=how, on=left_key, suffixes=("", "_2"))
        else:
            merged = df.merge(right, how=how, left_on=left_key, right_on=right_key, suffixes=("", "_2"))
            merged = merged.drop(columns=[right_key], errors="ignore")
        return merged

    def _op_aggregate(self, df: pd.DataFrame, params: Dict[str, Any]):
        group_by_raw = str(params.get("group_by", "") or "")
        agg_fn = str(params.get("aggregate_fn", "sum") or "sum").lower()
        agg_cols_raw = str(params.get("aggregate_col", "") or "")

        group_cols = [c.strip() for c in group_by_raw.split(",") if c.strip()]
        agg_cols = [c.strip() for c in agg_cols_raw.split(",") if c.strip()]
        if not group_cols:
            raise ValueError("Aggregate node needs a Group By column")
        missing = [c for c in group_cols if c not in df.columns]
        if missing:
            raise ValueError(f"Group By column(s) not found: {', '.join(missing)}")

        fn_map = {"sum": "sum", "avg": "mean", "mean": "mean", "count": "count",
                  "min": "min", "max": "max"}
        fn = fn_map.get(agg_fn, "sum")

        if fn == "count" and not agg_cols:
            out = df.groupby(group_cols, dropna=False).size().reset_index(name="count")
            note = f"Counted rows grouped by {', '.join(group_cols)}"
            return out, note

        missing = [c for c in agg_cols if c not in df.columns]
        if missing:
            raise ValueError(f"Aggregate column(s) not found: {', '.join(missing)}")

        out = df.groupby(group_cols, dropna=False).agg({c: fn for c in agg_cols}).reset_index()
        rename = {c: f"{c}_{fn}" for c in agg_cols}
        out = out.rename(columns=rename)
        first_metric = rename[agg_cols[0]]
        out = out.sort_values(first_metric, ascending=False)
        note = f"{fn.upper()} of {', '.join(agg_cols)} grouped by {', '.join(group_cols)}"
        return out, note

    def _op_custom(self, df: pd.DataFrame, params: Dict[str, Any]):
        code = str(params.get("code", "") or "")
        if not code.strip():
            raise ValueError("Python node has no code to execute")
        buf = io.StringIO()
        gl = {"df": df.copy(), "pd": pd, "np": np, "result": None}
        loc: Dict[str, Any] = {}
        try:
            with contextlib.redirect_stdout(buf):
                exec(compile(code, "<pipeline-python-node>", "exec"), gl, loc)  # noqa: S102
        except Exception as exc:
            raise ValueError(f"Python code error: {exc}") from exc

        var_name = str(params.get("var_name", "df") or "df")
        out = None
        for scope in (loc, gl):
            cand = scope.get(var_name)
            if not isinstance(cand, pd.DataFrame):
                cand = scope.get("result")
            if isinstance(cand, pd.DataFrame):
                out = cand
                break
        if out is None:
            out = gl["df"]
        printed = buf.getvalue()
        return out, printed

    def _op_analysis(self, df: pd.DataFrame, params: Dict[str, Any]) -> str:
        analysis_type = str(params.get("analysis_type", "statistics") or "statistics")
        target = str(params.get("target_col", "") or "")
        num = df.select_dtypes(include=[np.number])

        if analysis_type == "correlation":
            if num.shape[1] < 2:
                return "Correlation needs at least 2 numeric columns"
            corr = num.corr(numeric_only=True).round(3)
            pairs = (corr.where(~np.eye(len(corr), dtype=bool))
                     .stack().sort_values(key=abs, ascending=False).head(5))
            lines = [f"{a} vs {b}: r={v}" for (a, b), v in pairs.items()]
            return "Top correlations:\n" + "\n".join(lines)

        if target and target in df.columns:
            s = df[target]
            lines = [f"Target column: {target}",
                     f"count={s.notna().sum()}, mean={s.mean():.3f}, std={s.std():.3f}",
                     f"min={s.min()}, max={s.max()}"]
            if pd.api.types.is_numeric_dtype(s):
                q1, med, q3 = s.quantile([0.25, 0.5, 0.75])
                lines.append(f"Q1={q1:.3f}, median={med:.3f}, Q3={q3:.3f}")
            return "\n".join(lines)

        lines = [f"Statistics over {len(num.select_dtypes(include=[np.number]).columns)} numeric columns:"]
        for col in list(num.columns)[:8]:
            s = num[col]
            lines.append(f"  {col}: mean={s.mean():.3f}, std={s.std():.3f}, min={s.min()}, max={s.max()}")
        return "\n".join(lines)

    def _op_chart(self, df: pd.DataFrame, params: Dict[str, Any]):
        cfg = params.get("config", {}) or {}
        chart_type = str(cfg.get("chart_type", "bar") or "bar").lower()
        x_col = str(cfg.get("x_col", "") or "")
        y_col = str(cfg.get("y_col", "") or "")
        group_col = str(cfg.get("group_col", "") or "")
        title = str(cfg.get("title", "") or "")

        plot_df = df
        if x_col and x_col in df.columns and y_col and y_col in df.columns \
                and pd.api.types.is_numeric_dtype(df[y_col]):
            if group_col and group_col in df.columns:
                plot_df = df.groupby([x_col, group_col], dropna=False)[y_col].sum().reset_index()
            else:
                plot_df = df.groupby(x_col, dropna=False)[y_col].sum().reset_index()
            plot_df = plot_df.sort_values(y_col, ascending=False).head(20)

        summary = f"{chart_type} chart"
        if y_col:
            summary += f" of {y_col}"
        if x_col:
            summary += f" by {x_col}"
        if group_col:
            summary += f" split by {group_col}"
        summary += f" ({len(plot_df)} plotted rows)"

        chart_info: Dict[str, Any] = {"chart_points": len(plot_df)}
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            fig, ax = plt.subplots(figsize=(7.0, 4.0), dpi=100)
            if chart_type == "pie" and x_col and y_col and x_col in plot_df.columns and y_col in plot_df.columns:
                top = plot_df.head(10)
                ax.pie(top[y_col], labels=top[x_col].astype(str), autopct="%1.1f%%")
                ax.set_title(title or summary, fontsize=10)
            elif chart_type in ("line", "area") and x_col and y_col and x_col in plot_df.columns and y_col in plot_df.columns:
                ax.fill_between(range(len(plot_df)), plot_df[y_col]) if chart_type == "area" else None
                ax.plot(range(len(plot_df)), plot_df[y_col], marker="o", linewidth=1.5)
                ax.set_xticks(range(len(plot_df)))
                ax.set_xticklabels(plot_df[x_col].astype(str), rotation=45, ha="right", fontsize=7)
                ax.set_title(title or summary, fontsize=10)
            elif chart_type == "scatter" and x_col and y_col and x_col in plot_df.columns and y_col in plot_df.columns:
                ax.scatter(plot_df[x_col], plot_df[y_col], s=18, alpha=0.7)
                ax.set_title(title or summary, fontsize=10)
            elif chart_type == "histogram":
                col = y_col if (y_col and y_col in df.columns) else (x_col if x_col in df.columns else None)
                if col and pd.api.types.is_numeric_dtype(df[col]):
                    ax.hist(df[col].dropna(), bins=30, color="#6366f1", alpha=0.85)
                    ax.set_title(title or f"Distribution of {col}", fontsize=10)
                else:
                    ax.text(0.5, 0.5, "Histogram needs a numeric column", ha="center", va="center")
            elif x_col and y_col and x_col in plot_df.columns and y_col in plot_df.columns:
                ax.bar(plot_df[x_col].astype(str), plot_df[y_col], color="#6366f1", alpha=0.9)
                ax.tick_params(axis="x", rotation=45, labelsize=7)
                for t in ax.get_xticklabels():
                    t.set_ha("right")
                ax.set_title(title or summary, fontsize=10)
            else:
                num = plot_df.select_dtypes(include=[np.number])
                if not num.empty:
                    num.head(6).plot(kind="bar", ax=ax)
                    ax.tick_params(axis="x", labelsize=7)
                else:
                    ax.text(0.5, 0.5, "Nothing to plot", ha="center", va="center")
            fig.tight_layout()

            out_dir = Path("outputs/exports/charts")
            out_dir.mkdir(parents=True, exist_ok=True)
            fname = f"chart_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}.png"
            file_path = out_dir / fname
            fig.savefig(file_path)
            plt.close(fig)
            b64 = base64.b64encode(file_path.read_bytes()).decode("ascii")
            chart_info["chart_png_base64"] = b64
            chart_info["chart_path"] = str(file_path)
        except Exception as exc:  # matplotlib optional — aggregation still returned
            logger.warning("Chart rendering failed: %s", exc)
            chart_info["chart_error"] = str(exc)

        return summary, chart_info

    def _op_insights(self, df: pd.DataFrame, params: Dict[str, Any]) -> str:
        prompt = str((params.get("config", {}) or {}).get("prompt", "") or "")
        num = df.select_dtypes(include=[np.number])
        cat = df.select_dtypes(exclude=[np.number])

        lines = [f"Dataset snapshot: {len(df):,} rows × {df.shape[1]} columns"]
        if not num.empty:
            lines.append(f"Numeric columns: {len(num.columns)}")
            for col in list(num.columns)[:6]:
                s = num[col]
                lines.append(f"  {col}: mean={s.mean():.2f}, min={s.min():.2f}, max={s.max():.2f}")
        for col in list(cat.columns)[:3]:
            top = cat[col].value_counts(dropna=False).head(3)
            tops = ", ".join(f"{k} ({v})" for k, v in top.items())
            lines.append(f"Top values in {col}: {tops}")
        null_cells = int(df.isna().sum().sum())
        dup_rows = int(df.duplicated().sum())
        lines.append(f"Missing cells: {null_cells:,}; duplicate rows: {dup_rows:,}")
        if prompt:
            lines.append(f"(Requested focus: {prompt})")
        return "\n".join(lines)

    def _op_anomaly(self, df: pd.DataFrame, params: Dict[str, Any]):
        from analysis.anomaly_pipeline import detect_iqr_outliers, detect_zscore_outliers, detect_change_points
        col = str(params.get("column", "") or "")
        method = str(params.get("method", "iqr") or "iqr").lower()
        threshold = float(params.get("threshold", 1.5) or 1.5)
        if not col:
            raise ValueError("Anomaly Detection node needs a column")
        if col not in df.columns:
            raise ValueError(f"Anomaly column '{col}' not found. Available: {', '.join(map(str, df.columns[:20]))}")
        s = pd.to_numeric(df[col], errors="coerce")
        if s.notna().sum() < 4:
            raise ValueError(f"Anomaly Detection needs at least 4 numeric values in '{col}'")
        out = df.copy()
        if method in ("iqr", "auto"):
            res = detect_iqr_outliers(s.dropna(), threshold=threshold)
            out["_is_anomaly"] = False
            out.loc[s.index.isin(res["outliers"]), "_is_anomaly"] = True
            summary = (f"IQR anomaly detection on '{col}': {res['count']} outliers "
                       f"({res.get('pct', 0)}%), bounds [{res.get('lower_bound')}, {res.get('upper_bound')}]")
        elif method == "zscore":
            res = detect_zscore_outliers(s.dropna(), threshold=threshold)
            std = s.std()
            if std and not np.isnan(std):
                z = np.abs((s - s.mean()) / std)
                out["_is_anomaly"] = z > threshold
            else:
                out["_is_anomaly"] = False
            summary = f"Z-score anomaly detection on '{col}': {res['count']} outliers ({res.get('pct', 0)}%), threshold={threshold}"
        elif method == "change_point":
            changes = detect_change_points(s.dropna().tolist(), min_deviation=threshold)
            out["_is_anomaly"] = False
            for c in changes:
                idx = s.dropna().index[c["index"]]
                out.loc[idx, "_is_anomaly"] = True
            summary = f"Change-point detection on '{col}': {len(changes)} change points (min deviation {threshold})"
        else:
            raise ValueError(f"Unknown anomaly method: {method}")
        return out, summary

    def _op_forecast(self, df: pd.DataFrame, params: Dict[str, Any]):
        from analysis.forecasting import advanced_forecast
        date_col = str(params.get("date_col", "") or "")
        value_col = str(params.get("value_col", "") or "")
        horizon = int(params.get("horizon", 12) or 12)
        method = str(params.get("method", "auto") or "auto")
        if not date_col or not value_col:
            raise ValueError("Forecast node needs both a Date column and a Value column")
        if date_col not in df.columns or value_col not in df.columns:
            raise ValueError(f"Forecast columns not found. Available: {', '.join(map(str, df.columns[:20]))}")
        res = advanced_forecast(df, date_col, value_col, periods=horizon, method=method)
        if not res.forecast_dates:
            raise ValueError(res.interpretation or "Forecast produced no results")
        hist = pd.DataFrame({date_col: res.historical_dates, value_col: res.historical_values})
        hist["_series"] = "actual"
        fcst = pd.DataFrame({date_col: res.forecast_dates, value_col: res.forecast_values})
        fcst["_series"] = "forecast"
        out = pd.concat([hist, fcst], ignore_index=True)
        summary = (f"Forecast of '{value_col}' over {horizon} period(s) using {res.method} "
                   f"(MAPE={res.mape:.2f}%, RMSE={res.rmse:.2f}, trend={res.trend_direction})")
        return out, summary

    def _op_stat_test(self, df: pd.DataFrame, params: Dict[str, Any]):
        from analysis.statistical import test_hypothesis
        test_type = str(params.get("test_type", "ttest") or "ttest")
        column_a = str(params.get("column_a", "") or "")
        column_b = str(params.get("column_b", "") or "")
        group_col = str(params.get("group_col", "") or "")
        alpha = float(params.get("alpha", 0.05) or 0.05)
        if not column_a:
            raise ValueError("Statistical Test node needs a column")
        if column_a not in df.columns:
            raise ValueError(f"Test column '{column_a}' not found. Available: {', '.join(map(str, df.columns[:20]))}")
        res = test_hypothesis(df, column_a, column_b, test_type, group_col, alpha)
        summary = (f"{res.test_name}: stat={res.statistic}, p={res.p_value}, "
                   f"significant={res.significant}. {res.interpretation}")
        return df, summary

    def _op_regression(self, df: pd.DataFrame, params: Dict[str, Any]):
        from analysis.statistical import fit_regression
        target = str(params.get("target_col", "") or "")
        features_raw = params.get("feature_cols", "") or ""
        reg_type = str(params.get("regression_type", "linear") or "linear")
        if not target:
            raise ValueError("Regression node needs a Target column")
        if target not in df.columns:
            raise ValueError(f"Target column '{target}' not found. Available: {', '.join(map(str, df.columns[:20]))}")
        if isinstance(features_raw, (list, tuple)):
            feature_cols = [str(c).strip() for c in features_raw if str(c).strip()]
        else:
            feature_cols = [c.strip() for c in str(features_raw).split(",") if c.strip()]
        if not feature_cols:
            feature_cols = [c for c in df.select_dtypes(include=[np.number]).columns if c != target][:5]
        if not feature_cols:
            raise ValueError("Regression node needs at least one numeric feature column")
        res = fit_regression(df, target, feature_cols, regression_type=reg_type)
        out = df.copy()
        if res.coefficients:
            beta = np.array([res.coefficients[c] for c in feature_cols])
            out["_predicted"] = res.intercept + out[feature_cols].values @ beta
        return out, res.interpretation

    def _op_quality(self, df: pd.DataFrame, params: Dict[str, Any]):
        from analysis.quality import compute_quality_score
        table_name = str(params.get("table_name", "") or "")
        report = compute_quality_score(df)
        score = report.get("overall_score", 0.0)
        dims = report.get("dimension_scores", {})
        issues = report.get("issues", [])
        summary = (f"Quality score: {score:.1f}/100. "
                   f"Completeness={dims.get('completeness', 0):.1f}, Uniqueness={dims.get('uniqueness', 0):.1f}, "
                   f"Consistency={dims.get('consistency', 0):.1f}, Validity={dims.get('validity', 0):.1f}, "
                   f"Timeliness={dims.get('timeliness', 0):.1f}. Issues: {len(issues)}")
        if table_name:
            summary += f" (table: {table_name})"
        return df, summary

    def _op_comparison(self, df: pd.DataFrame, params: Dict[str, Any]):
        kind = str(params.get("kind", "periods") or "periods").lower()
        if kind == "diff":
            from analysis.comparison import diff_tables
            right_key = str(params.get("input_data2", "") or "")
            right = self._data.get(right_key)
            if right is None:
                raise ValueError("Comparison (diff) node needs a second connected input")
            key_column = str(params.get("key_column", "") or "") or None
            res = diff_tables(df, right, key_column)
            summary = (f"Table diff: +{res.rows_added} added, -{res.rows_removed} removed, "
                       f"{res.rows_modified} modified, {res.rows_unchanged} unchanged")
            return df, summary
        from analysis.comparison import compare_periods
        date_col = str(params.get("date_col", "") or "")
        metric_col = str(params.get("metric_col", "") or "")
        group_col = str(params.get("group_col", "") or "")
        period_type = str(params.get("period_type", "month") or "month")
        if not date_col or not metric_col:
            raise ValueError("Comparison node needs a Date column and a Metric column")
        if date_col not in df.columns or metric_col not in df.columns:
            raise ValueError(f"Comparison columns not found. Available: {', '.join(map(str, df.columns[:20]))}")
        res = compare_periods(df, date_col, metric_col, group_col, period_type)
        return df, res.summary

    def _op_cohort(self, df: pd.DataFrame, params: Dict[str, Any]):
        kind = str(params.get("kind", "retention") or "retention").lower()
        user_id_col = str(params.get("user_id_col", "") or "")
        date_col = str(params.get("date_col", "") or "")
        if not user_id_col or not date_col:
            raise ValueError("Cohort node needs a User ID column and a Date column")
        if user_id_col not in df.columns or date_col not in df.columns:
            raise ValueError(f"Cohort columns not found. Available: {', '.join(map(str, df.columns[:20]))}")
        if kind == "churn":
            from analysis.cohort import churn_analysis
            inactivity_days = int(params.get("inactivity_days", 30) or 30)
            res = churn_analysis(df, user_id_col, date_col, inactivity_days)
            summary = f"Churn analysis: avg churn rate {res.avg_churn_rate:.1f}%. {res.summary}"
            return df, summary
        from analysis.cohort import cohort_retention
        cohort_period = str(params.get("cohort_period", "month") or "month")
        revenue_col = str(params.get("revenue_col", "") or "") or None
        max_periods = int(params.get("max_periods", 12) or 12)
        res = cohort_retention(df, user_id_col, date_col, cohort_period, revenue_col, max_periods)
        return df, res.summary

    def _op_ml_model(self, df: pd.DataFrame, params: Dict[str, Any]):
        from analysis.automl import auto_analyze, compare_regressions
        target = str(params.get("target_col", "") or "")
        task = str(params.get("task", "auto") or "auto").lower()
        if not target:
            raise ValueError("AutoML node needs a Target column")
        if target not in df.columns:
            raise ValueError(f"Target column '{target}' not found. Available: {', '.join(map(str, df.columns[:20]))}")
        if task == "compare":
            res = compare_regressions(df, target)
            summary = res.interpretation
        else:
            res = auto_analyze(df, target)
            summary = res.summary
        return df, summary

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "description": self.description,
            "steps": [{"name": s.name, "type": s.step_type, "params": s.params, "depends_on": s.depends_on}
                      for s in self.steps],
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ETLPipeline":
        p = cls(data["name"], data.get("description", ""))
        for s in data.get("steps", []):
            p.add_step(PipelineStep(name=s["name"], step_type=s["type"],
                                     params=s.get("params", {}), depends_on=s.get("depends_on", [])))
        return p

    def _topological_sort(self) -> List[PipelineStep]:
        """Topological sort of steps for execution order."""
        from collections import deque

        step_map = {s.name: s for s in self.steps}
        dep_sets = {s.name: set(s.depends_on) for s in self.steps}
        in_degree = {s.name: 0 for s in self.steps}
        for s in self.steps:
            for dep in dep_sets[s.name]:
                if dep in in_degree and dep != s.name:
                    in_degree[s.name] += 1

        queue = deque([name for name, deg in in_degree.items() if deg == 0])
        sorted_steps = []

        while queue:
            name = queue.popleft()
            sorted_steps.append(step_map[name])
            for s in self.steps:
                if name in dep_sets[s.name] and name != s.name:
                    in_degree[s.name] -= 1
                    if in_degree[s.name] == 0:
                        queue.append(s.name)

        # Append remaining (steps with missing deps get skipped)
        remaining = [step_map[name] for name in in_degree if in_degree[name] > 0]
        sorted_steps.extend(remaining)

        return sorted_steps
