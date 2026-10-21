"""
Pandas Tool Module
DataFrame operations, statistical analysis, and transformations for the agent
"""

import logging
from typing import Dict, List, Optional, Any, Union, Callable
import pandas as pd
import numpy as np
from dataclasses import dataclass
from io import StringIO
import json

logger = logging.getLogger(__name__)


@dataclass
class PandasResult:
    """Result of pandas operation."""
    success: bool
    data: Optional[pd.DataFrame] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = None
    code_generated: str = ""

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class PandasTool:
    """
    Pandas operations tool for data analysis.

    Provides safe DataFrame operations:
    - Filtering, grouping, aggregation
    - Statistical computations
    - Pivot tables
    - Time series operations
    - DataFrame info and profiling
    """

    def __init__(
        self,
        max_rows: int = 10000,
        allowed_operations: Optional[List[str]] = None,
    ):
        self.max_rows = max_rows
        self.allowed_operations = allowed_operations or [
            "head", "tail", "sample", "describe", "info", "dtypes",
            "groupby", "agg", "pivot_table", "crosstab",
            "merge", "join", "concat", "sort_values",
            "query", "filter", "apply", "transform",
            "resample", "rolling", "ewm", "shift", "diff",
            "fillna", "dropna", "astype", "rename",
            "to_datetime", "to_numeric", "cut", "qcut",
            "value_counts", "nunique", "corr", "cov",
        ]
        self._dataframes: Dict[str, pd.DataFrame] = {}

    def register_dataframe(self, name: str, df: pd.DataFrame) -> str:
        """Register a DataFrame for use in operations."""
        if len(df) > self.max_rows:
            df = df.head(self.max_rows)
            logger.warning(f"DataFrame {name} truncated to {self.max_rows} rows")
        self._dataframes[name] = df
        return f"Registered DataFrame '{name}' with shape {df.shape}"

    def get_dataframe(self, name: str) -> Optional[pd.DataFrame]:
        """Get a registered DataFrame."""
        return self._dataframes.get(name)

    def list_dataframes(self) -> Dict[str, Dict]:
        """List all registered DataFrames with metadata."""
        return {
            name: {
                "shape": df.shape,
                "columns": list(df.columns),
                "dtypes": {c: str(t) for c, t in df.dtypes.items()},
                "memory_mb": df.memory_usage(deep=True).sum() / 1024**2,
            }
            for name, df in self._dataframes.items()
        }

    def execute_code(self, code: str, df_name: str = "df") -> PandasResult:
        """
        Execute pandas code safely.

        Args:
            code: Python code to execute (has access to 'df' variable)
            df_name: Name of registered DataFrame to use as 'df'

        Returns:
            PandasResult with result DataFrame or error
        """
        df = self._dataframes.get(df_name)
        if df is None:
            return PandasResult(
                success=False,
                error=f"DataFrame '{df_name}' not registered. Available: {list(self._dataframes.keys())}",
            )

        # Create safe execution environment
        safe_globals = {
            "pd": pd,
            "np": np,
            "df": df.copy(),  # Work on copy
            "DataFrame": pd.DataFrame,
            "Series": pd.Series,
        }

        try:
            # Execute code
            local_vars = {}
            exec(code, safe_globals, local_vars)

            # Find result (last expression or variable named 'result')
            result = local_vars.get("result", local_vars.get("df", None))

            if isinstance(result, pd.DataFrame):
                # Limit rows
                if len(result) > self.max_rows:
                    result = result.head(self.max_rows)
                return PandasResult(
                    success=True,
                    data=result,
                    metadata={"shape": result.shape, "columns": list(result.columns)},
                    code_generated=code,
                )
            elif isinstance(result, pd.Series):
                result_df = result.to_frame()
                return PandasResult(
                    success=True,
                    data=result_df,
                    metadata={"shape": result_df.shape, "columns": list(result_df.columns)},
                    code_generated=code,
                )
            else:
                # Convert scalar to DataFrame
                result_df = pd.DataFrame({"result": [result]})
                return PandasResult(
                    success=True,
                    data=result_df,
                    metadata={"type": type(result).__name__},
                    code_generated=code,
                )

        except Exception as e:
            logger.error(f"Pandas execution failed: {e}")
            return PandasResult(
                success=False,
                error=str(e),
                code_generated=code,
            )

    # ============================================================
    # High-level operations (safer than raw code execution)
    # ============================================================

    def describe(
        self,
        df_name: str,
        include: str = "all",
        percentiles: Optional[List[float]] = None,
    ) -> PandasResult:
        """Get statistical summary."""
        df = self._get_df(df_name)
        if df is None:
            return self._error(f"DataFrame '{df_name}' not found")

        result = df.describe(include=include, percentiles=percentiles)
        return PandasResult(success=True, data=result, metadata={"operation": "describe"})

    def info(self, df_name: str) -> PandasResult:
        """Get DataFrame info."""
        df = self._get_df(df_name)
        if df is None:
            return self._error(f"DataFrame '{df_name}' not found")

        buf = StringIO()
        df.info(buf=buf)
        info_str = buf.getvalue()

        result_df = pd.DataFrame({"info": [info_str]})
        return PandasResult(success=True, data=result_df, metadata={"operation": "info"})

    def head(self, df_name: str, n: int = 10) -> PandasResult:
        """Get first n rows."""
        df = self._get_df(df_name)
        if df is None:
            return self._error(f"DataFrame '{df_name}' not found")

        return PandasResult(success=True, data=df.head(n), metadata={"operation": "head", "n": n})

    def tail(self, df_name: str, n: int = 10) -> PandasResult:
        """Get last n rows."""
        df = self._get_df(df_name)
        if df is None:
            return self._error(f"DataFrame '{df_name}' not found")

        return PandasResult(success=True, data=df.tail(n), metadata={"operation": "tail", "n": n})

    def sample(self, df_name: str, n: int = 10, random_state: int = 42) -> PandasResult:
        """Get random sample."""
        df = self._get_df(df_name)
        if df is None:
            return self._error(f"DataFrame '{df_name}' not found")

        n = min(n, len(df))
        return PandasResult(
            success=True,
            data=df.sample(n=n, random_state=random_state),
            metadata={"operation": "sample", "n": n},
        )

    def groupby_agg(
        self,
        df_name: str,
        by: Union[str, List[str]],
        agg: Dict[str, Union[str, List[str]]],
        as_index: bool = False,
    ) -> PandasResult:
        """Group by and aggregate."""
        df = self._get_df(df_name)
        if df is None:
            return self._error(f"DataFrame '{df_name}' not found")

        try:
            result = df.groupby(by, as_index=as_index).agg(agg).reset_index()
            return PandasResult(
                success=True,
                data=result,
                metadata={"operation": "groupby_agg", "by": by, "agg": agg},
            )
        except Exception as e:
            return self._error(f"Groupby failed: {e}")

    def pivot_table(
        self,
        df_name: str,
        values: str,
        index: Union[str, List[str]],
        columns: Optional[str] = None,
        aggfunc: Union[str, List[str]] = "mean",
        fill_value: Any = None,
    ) -> PandasResult:
        """Create pivot table."""
        df = self._get_df(df_name)
        if df is None:
            return self._error(f"DataFrame '{df_name}' not found")

        try:
            bench = df
            idx = [index] if isinstance(index, str) else list(index or [])
            if columns and columns in idx:
                # A grouper cannot sit on both axes: pandas 3 raises
                # "Grouper for '<col>' not 1-dimensional" for some frames.
                # Project a private alias for the columns axis instead.
                alias = "__pivot_cols__"
                bench = df.assign(**{alias: df[columns]})
                columns = alias
            result = pd.pivot_table(
                bench,
                values=values,
                index=index,
                columns=columns,
                aggfunc=aggfunc,
                fill_value=fill_value,
            ).reset_index()
            return PandasResult(
                success=True,
                data=result,
                metadata={"operation": "pivot_table", "values": values, "index": index},
            )
        except Exception as e:
            return self._error(f"Pivot table failed: {e}")

    def crosstab(
        self,
        df_name: str,
        index: Union[str, List[str]],
        columns: Union[str, List[str]],
        values: Optional[str] = None,
        aggfunc: Optional[str] = None,
        normalize: bool = False,
    ) -> PandasResult:
        """Create cross-tabulation."""
        df = self._get_df(df_name)
        if df is None:
            return self._error(f"DataFrame '{df_name}' not found")

        try:
            result = pd.crosstab(
                df[index] if isinstance(index, str) else df[index],
                df[columns] if isinstance(columns, str) else df[columns],
                values=df[values] if values else None,
                aggfunc=aggfunc,
                normalize="all" if normalize else False,
            ).reset_index()
            return PandasResult(
                success=True,
                data=result,
                metadata={"operation": "crosstab", "index": index, "columns": columns},
            )
        except Exception as e:
            return self._error(f"Crosstab failed: {e}")

    def merge(
        self,
        left_name: str,
        right_name: str,
        on: Union[str, List[str]],
        how: str = "inner",
        left_on: Optional[Union[str, List[str]]] = None,
        right_on: Optional[Union[str, List[str]]] = None,
    ) -> PandasResult:
        """Merge two DataFrames."""
        left = self._get_df(left_name)
        right = self._get_df(right_name)
        if left is None:
            return self._error(f"Left DataFrame '{left_name}' not found")
        if right is None:
            return self._error(f"Right DataFrame '{right_name}' not found")

        try:
            result = pd.merge(
                left, right,
                on=on,
                how=how,
                left_on=left_on,
                right_on=right_on,
            )
            return PandasResult(
                success=True,
                data=result,
                metadata={"operation": "merge", "how": how, "left_shape": left.shape, "right_shape": right.shape},
            )
        except Exception as e:
            return self._error(f"Merge failed: {e}")

    def sort_values(
        self,
        df_name: str,
        by: Union[str, List[str]],
        ascending: Union[bool, List[bool]] = True,
    ) -> PandasResult:
        """Sort DataFrame."""
        df = self._get_df(df_name)
        if df is None:
            return self._error(f"DataFrame '{df_name}' not found")

        result = df.sort_values(by=by, ascending=ascending)
        return PandasResult(
            success=True,
            data=result,
            metadata={"operation": "sort_values", "by": by, "ascending": ascending},
        )

    def query(self, df_name: str, expr: str) -> PandasResult:
        """Filter with query expression."""
        df = self._get_df(df_name)
        if df is None:
            return self._error(f"DataFrame '{df_name}' not found")

        try:
            result = df.query(expr)
            return PandasResult(
                success=True,
                data=result,
                metadata={"operation": "query", "expr": expr, "rows_before": len(df), "rows_after": len(result)},
            )
        except Exception as e:
            return self._error(f"Query failed: {e}")

    def value_counts(self, df_name: str, column: str, normalize: bool = False, top_n: int = 20) -> PandasResult:
        """Get value counts for a column."""
        df = self._get_df(df_name)
        if df is None:
            return self._error(f"DataFrame '{df_name}' not found")

        if column not in df.columns:
            return self._error(f"Column '{column}' not found")

        vc = df[column].value_counts(normalize=normalize).head(top_n).reset_index()
        vc.columns = [column, "proportion" if normalize else "count"]
        return PandasResult(
            success=True,
            data=vc,
            metadata={"operation": "value_counts", "column": column, "normalize": normalize},
        )

    def correlation(self, df_name: str, method: str = "pearson", min_periods: int = 1) -> PandasResult:
        """Compute correlation matrix."""
        df = self._get_df(df_name)
        if df is None:
            return self._error(f"DataFrame '{df_name}' not found")

        numeric = df.select_dtypes(include=[np.number])
        if numeric.empty:
            return self._error("No numeric columns for correlation")

        corr = numeric.corr(method=method, min_periods=min_periods)
        return PandasResult(
            success=True,
            data=corr,
            metadata={"operation": "correlation", "method": method, "columns": list(corr.columns)},
        )

    def time_series_resample(
        self,
        df_name: str,
        date_column: str,
        rule: str,
        value_column: str,
        agg: str = "sum",
    ) -> PandasResult:
        """Resample time series data."""
        df = self._get_df(df_name)
        if df is None:
            return self._error(f"DataFrame '{df_name}' not found")

        if date_column not in df.columns:
            return self._error(f"Date column '{date_column}' not found")
        if value_column not in df.columns:
            return self._error(f"Value column '{value_column}' not found")

        try:
            df_ts = df.copy()
            df_ts[date_column] = pd.to_datetime(df_ts[date_column])
            df_ts = df_ts.set_index(date_column)

            result = df_ts[value_column].resample(rule).agg(agg).reset_index()
            return PandasResult(
                success=True,
                data=result,
                metadata={"operation": "resample", "rule": rule, "agg": agg},
            )
        except Exception as e:
            return self._error(f"Resample failed: {e}")

    def rolling_stats(
        self,
        df_name: str,
        column: str,
        window: int,
        stats: List[str] = None,
    ) -> PandasResult:
        """Compute rolling statistics."""
        df = self._get_df(df_name)
        if df is None:
            return self._error(f"DataFrame '{df_name}' not found")

        if column not in df.columns:
            return self._error(f"Column '{column}' not found")

        stats = stats or ["mean", "std", "min", "max"]
        try:
            rolling = df[column].rolling(window=window)
            result = pd.DataFrame({f"{column}_{s}": getattr(rolling, s)() for s in stats})
            result = pd.concat([df, result], axis=1)
            return PandasResult(
                success=True,
                data=result,
                metadata={"operation": "rolling", "window": window, "stats": stats},
            )
        except Exception as e:
            return self._error(f"Rolling stats failed: {e}")

    def profile_column(self, df_name: str, column: str) -> PandasResult:
        """Detailed column profiling."""
        df = self._get_df(df_name)
        if df is None:
            return self._error(f"DataFrame '{df_name}' not found")

        if column not in df.columns:
            return self._error(f"Column '{column}' not found")

        series = df[column]
        profile = {
            "column": column,
            "dtype": str(series.dtype),
            "count": int(series.count()),
            "null_count": int(series.isnull().sum()),
            "null_pct": float(series.isnull().mean() * 100),
            "unique_count": int(series.nunique()),
            "unique_pct": float(series.nunique() / len(series) * 100),
        }

        if pd.api.types.is_numeric_dtype(series):
            profile.update({
                "mean": float(series.mean()),
                "std": float(series.std()),
                "min": float(series.min()),
                "max": float(series.max()),
                "median": float(series.median()),
                "skew": float(series.skew()),
                "kurtosis": float(series.kurtosis()),
            })
        elif pd.api.types.is_datetime64_any_dtype(series):
            profile.update({
                "min": str(series.min()),
                "max": str(series.max()),
                "range_days": int((series.max() - series.min()).days),
            })
        else:
            # Top values for categorical
            top = series.value_counts().head(10)
            profile["top_values"] = top.to_dict()

        result_df = pd.DataFrame([profile])
        return PandasResult(success=True, data=result_df, metadata={"operation": "profile_column"})

    def _get_df(self, name: str) -> Optional[pd.DataFrame]:
        return self._dataframes.get(name)

    def _error(self, msg: str) -> PandasResult:
        return PandasResult(success=False, error=msg)


class PandasToolOpenAI:
    """OpenAI function calling format for Pandas tool."""

    @staticmethod
    def get_function_definitions() -> List[Dict[str, Any]]:
        """Get OpenAI function definitions."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "pandas_describe",
                    "description": "Get statistical summary of a DataFrame",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "df_name": {"type": "string", "description": "Registered DataFrame name"},
                            "include": {"type": "string", "default": "all"},
                        },
                        "required": ["df_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "pandas_groupby_agg",
                    "description": "Group by columns and aggregate",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "df_name": {"type": "string"},
                            "by": {"type": "array", "items": {"type": "string"}},
                            "agg": {"type": "object", "additionalProperties": {"type": "string"}},
                        },
                        "required": ["df_name", "by", "agg"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "pandas_pivot_table",
                    "description": "Create pivot table",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "df_name": {"type": "string"},
                            "values": {"type": "string"},
                            "index": {"type": "array", "items": {"type": "string"}},
                            "columns": {"type": "string"},
                            "aggfunc": {"type": "string", "default": "mean"},
                        },
                        "required": ["df_name", "values", "index"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "pandas_merge",
                    "description": "Merge two DataFrames",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "left_name": {"type": "string"},
                            "right_name": {"type": "string"},
                            "on": {"type": "array", "items": {"type": "string"}},
                            "how": {"type": "string", "default": "inner"},
                        },
                        "required": ["left_name", "right_name", "on"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "pandas_query",
                    "description": "Filter DataFrame with query expression",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "df_name": {"type": "string"},
                            "expr": {"type": "string"},
                        },
                        "required": ["df_name", "expr"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "pandas_profile_column",
                    "description": "Profile a column in detail",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "df_name": {"type": "string"},
                            "column": {"type": "string"},
                        },
                        "required": ["df_name", "column"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "pandas_correlation",
                    "description": "Compute correlation matrix",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "df_name": {"type": "string"},
                            "method": {"type": "string", "default": "pearson"},
                        },
                        "required": ["df_name"],
                    },
                },
            },
        ]