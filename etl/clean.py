"""
ETL Clean Module
Data cleaning pipeline with DataCleaner class for deduplication, null handling, type standardization
"""

import logging
from typing import Dict, List, Optional, Any, Union, Callable
from pathlib import Path
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class CleaningStrategy(Enum):
    """Strategies for handling missing values."""
    DROP = "drop"
    FILL_MEAN = "fill_mean"
    FILL_MEDIAN = "fill_median"
    FILL_MODE = "fill_mode"
    FILL_CONSTANT = "fill_constant"
    FILL_FORWARD = "fill_forward"
    FILL_BACKWARD = "fill_backward"
    INTERPOLATE = "interpolate"


class DeduplicationStrategy(Enum):
    """Strategies for deduplication."""
    FIRST = "first"
    LAST = "last"
    FALSE = "false"  # Keep all duplicates


@dataclass
class ColumnCleaningRule:
    """Cleaning rule for a specific column."""
    column: str
    strategy: CleaningStrategy = CleaningStrategy.DROP
    fill_value: Any = None
    dtype: Optional[str] = None
    date_format: Optional[str] = None
    category_mapping: Optional[Dict[str, str]] = None
    custom_transform: Optional[Callable] = None


@dataclass
class CleaningPlan:
    """Complete cleaning plan for a DataFrame."""
    deduplication: DeduplicationStrategy = DeduplicationStrategy.FIRST
    deduplication_subset: Optional[List[str]] = None
    column_rules: Dict[str, ColumnCleaningRule] = field(default_factory=dict)
    global_null_strategy: CleaningStrategy = CleaningStrategy.DROP
    drop_columns: List[str] = field(default_factory=list)
    rename_columns: Dict[str, str] = field(default_factory=dict)


class DataCleaner:
    """
    Comprehensive data cleaning pipeline.

    Features:
    - Deduplication with configurable strategy
    - Missing value handling with multiple strategies
    - Type conversion and standardization
    - Date parsing with multiple format support
    - Categorical value mapping/standardization
    - Custom transformations
    - Cleaning report generation
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.date_formats = self.config.get(
            "date_formats",
            ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S"],
        )
        self.report: Dict[str, Any] = {}

    def clean(self, df: pd.DataFrame, plan: Optional[CleaningPlan] = None) -> pd.DataFrame:
        """
        Apply cleaning plan to DataFrame.

        Args:
            df: Input DataFrame
            plan: CleaningPlan with rules. If None, auto-infer basic plan.

        Returns:
            Cleaned DataFrame
        """
        self.report = {
            "original_shape": df.shape,
            "steps": [],
            "column_stats": {},
        }

        df = df.copy()

        # Auto-infer plan if not provided
        if plan is None:
            plan = self._infer_plan(df)

        # Apply cleaning steps
        df = self._apply_renaming(df, plan)
        df = self._apply_deduplication(df, plan)
        df = self._apply_column_rules(df, plan)
        df = self._apply_global_null_handling(df, plan)
        df = self._apply_drop_columns(df, plan)
        df = self._apply_type_conversion(df, plan)

        self.report["final_shape"] = df.shape
        self.report["rows_removed"] = self.report["original_shape"][0] - df.shape[0]
        self.report["cols_removed"] = self.report["original_shape"][1] - df.shape[1]

        logger.info(f"Cleaning complete: {self.report['original_shape']} -> {df.shape}")
        return df

    def _infer_plan(self, df: pd.DataFrame) -> CleaningPlan:
        """Auto-infer a basic cleaning plan from DataFrame."""
        plan = CleaningPlan()

        for col in df.columns:
            rule = ColumnCleaningRule(column=col)

            # Infer strategy based on dtype and null percentage
            null_pct = df[col].isnull().mean()
            dtype = df[col].dtype

            if null_pct >= 1.0:
                # Entirely empty column — drop it as a column instead of
                # letting the global dropna wipe every row in the table.
                plan.drop_columns.append(col)
                continue

            if null_pct > 0.5:
                rule.strategy = CleaningStrategy.DROP
            elif null_pct > 0:
                if pd.api.types.is_numeric_dtype(dtype):
                    rule.strategy = CleaningStrategy.FILL_MEDIAN
                elif pd.api.types.is_datetime64_any_dtype(dtype):
                    rule.strategy = CleaningStrategy.FILL_FORWARD
                else:
                    rule.strategy = CleaningStrategy.FILL_MODE

            # Infer target dtype
            if pd.api.types.is_object_dtype(dtype):
                # Try to parse as datetime
                sample = df[col].dropna().head(20)
                if len(sample) > 0:
                    for fmt in self.date_formats:
                        try:
                            pd.to_datetime(sample, format=fmt, errors="raise")
                            rule.dtype = "datetime64[ns]"
                            rule.date_format = fmt
                            break
                        except (ValueError, TypeError):
                            continue

            plan.column_rules[col] = rule

        return plan

    def _apply_renaming(self, df: pd.DataFrame, plan: CleaningPlan) -> pd.DataFrame:
        """Apply column renaming."""
        if plan.rename_columns:
            df = df.rename(columns=plan.rename_columns)
            self.report["steps"].append({
                "step": "rename_columns",
                "details": plan.rename_columns,
            })
        return df

    def _apply_deduplication(self, df: pd.DataFrame, plan: CleaningPlan) -> pd.DataFrame:
        """Apply deduplication."""
        original_rows = len(df)

        if plan.deduplication == DeduplicationStrategy.FALSE:
            return df

        subset = plan.deduplication_subset
        keep = plan.deduplication.value if plan.deduplication != DeduplicationStrategy.FALSE else False

        if keep != False:
            df = df.drop_duplicates(subset=subset, keep=keep)

        removed = original_rows - len(df)
        self.report["steps"].append({
            "step": "deduplication",
            "strategy": plan.deduplication.value,
            "subset": subset,
            "rows_removed": removed,
        })
        return df

    def _apply_column_rules(self, df: pd.DataFrame, plan: CleaningPlan) -> pd.DataFrame:
        """Apply column-specific cleaning rules."""
        for col, rule in plan.column_rules.items():
            if col not in df.columns:
                continue

            original_nulls = df[col].isnull().sum()

            # Handle nulls
            if original_nulls > 0:
                df[col] = self._handle_nulls(df[col], rule, df)
                new_nulls = df[col].isnull().sum()
                filled = original_nulls - new_nulls

                self.report["steps"].append({
                    "step": "handle_nulls",
                    "column": col,
                    "strategy": rule.strategy.value,
                    "nulls_filled": int(filled),
                    "nulls_remaining": int(new_nulls),
                })

            # Apply category mapping
            if rule.category_mapping:
                df[col] = df[col].replace(rule.category_mapping)
                self.report["steps"].append({
                    "step": "category_mapping",
                    "column": col,
                    "mapping": rule.category_mapping,
                })

            # Apply custom transform
            if rule.custom_transform:
                try:
                    df[col] = df[col].apply(rule.custom_transform)
                    self.report["steps"].append({
                        "step": "custom_transform",
                        "column": col,
                    })
                except Exception as e:
                    logger.warning(f"Custom transform failed for {col}: {e}")

            # Store column stats
            self.report["column_stats"][col] = {
                "original_nulls": int(original_nulls),
                "final_nulls": int(df[col].isnull().sum()),
                "dtype_before": str(df[col].dtype),
            }

        return df

    def _handle_nulls(self, series: pd.Series, rule: ColumnCleaningRule, df: pd.DataFrame) -> pd.Series:
        """Handle null values in a series based on rule."""
        if rule.strategy == CleaningStrategy.DROP:
            # Note: dropping rows happens at DataFrame level
            return series

        elif rule.strategy == CleaningStrategy.FILL_MEAN:
            if pd.api.types.is_numeric_dtype(series):
                return series.fillna(series.mean())

        elif rule.strategy == CleaningStrategy.FILL_MEDIAN:
            if pd.api.types.is_numeric_dtype(series):
                return series.fillna(series.median())

        elif rule.strategy == CleaningStrategy.FILL_MODE:
            mode_val = series.mode()
            if len(mode_val) > 0:
                return series.fillna(mode_val.iloc[0])

        elif rule.strategy == CleaningStrategy.FILL_CONSTANT:
            return series.fillna(rule.fill_value)

        elif rule.strategy == CleaningStrategy.FILL_FORWARD:
            return series.ffill()

        elif rule.strategy == CleaningStrategy.FILL_BACKWARD:
            return series.bfill()

        elif rule.strategy == CleaningStrategy.INTERPOLATE:
            if pd.api.types.is_numeric_dtype(series):
                return series.interpolate()

        return series

    def _apply_global_null_handling(self, df: pd.DataFrame, plan: CleaningPlan) -> pd.DataFrame:
        """Apply global null handling for remaining nulls."""
        if plan.global_null_strategy == CleaningStrategy.DROP:
            original_rows = len(df)
            # Only drop rows based on columns that will be retained. Columns
            # slated for removal (e.g. entirely empty) must not nuke the table.
            subset = [c for c in df.columns if c not in plan.drop_columns]
            df = df.dropna(subset=subset)
            removed = original_rows - len(df)
            if removed > 0:
                self.report["steps"].append({
                    "step": "global_dropna",
                    "rows_removed": removed,
                })
        return df

    def _apply_drop_columns(self, df: pd.DataFrame, plan: CleaningPlan) -> pd.DataFrame:
        """Drop specified columns."""
        existing = [c for c in plan.drop_columns if c in df.columns]
        if existing:
            df = df.drop(columns=existing)
            self.report["steps"].append({
                "step": "drop_columns",
                "columns": existing,
            })
        return df

    def _apply_type_conversion(self, df: pd.DataFrame, plan: CleaningPlan) -> pd.DataFrame:
        """Apply type conversions."""
        for col, rule in plan.column_rules.items():
            if col not in df.columns:
                continue

            if rule.dtype:
                try:
                    if rule.dtype == "datetime64[ns]" and rule.date_format:
                        df[col] = pd.to_datetime(df[col], format=rule.date_format, errors="coerce")
                    else:
                        df[col] = df[col].astype(rule.dtype)

                    self.report["column_stats"][col]["dtype_after"] = str(df[col].dtype)
                    self.report["steps"].append({
                        "step": "type_conversion",
                        "column": col,
                        "target_dtype": rule.dtype,
                    })
                except Exception as e:
                    logger.warning(f"Type conversion failed for {col} to {rule.dtype}: {e}")

        return df

    def get_report(self) -> Dict[str, Any]:
        """Get the cleaning report."""
        return self.report

    def generate_cleaning_code(self, plan: CleaningPlan, df_name: str = "df") -> str:
        """Generate pandas code for the cleaning plan."""
        lines = [
            f"# Auto-generated cleaning code",
            f"{df_name} = {df_name}.copy()",
            "",
        ]

        # Renaming
        if plan.rename_columns:
            lines.append(f"{df_name} = {df_name}.rename(columns={plan.rename_columns})")
            lines.append("")

        # Deduplication
        if plan.deduplication != DeduplicationStrategy.FALSE:
            subset = plan.deduplication_subset
            keep = plan.deduplication.value
            if subset:
                lines.append(f"{df_name} = {df_name}.drop_duplicates(subset={subset}, keep='{keep}')")
            else:
                lines.append(f"{df_name} = {df_name}.drop_duplicates(keep='{keep}')")
            lines.append("")

        # Column rules
        for col, rule in plan.column_rules.items():
            if rule.strategy != CleaningStrategy.DROP:
                if rule.strategy == CleaningStrategy.FILL_MEAN:
                    lines.append(f"{df_name}['{col}'] = {df_name}['{col}'].fillna({df_name}['{col}'].mean())")
                elif rule.strategy == CleaningStrategy.FILL_MEDIAN:
                    lines.append(f"{df_name}['{col}'] = {df_name}['{col}'].fillna({df_name}['{col}'].median())")
                elif rule.strategy == CleaningStrategy.FILL_MODE:
                    lines.append(f"{df_name}['{col}'] = {df_name}['{col}'].fillna({df_name}['{col}'].mode().iloc[0])")
                elif rule.strategy == CleaningStrategy.FILL_CONSTANT:
                    lines.append(f"{df_name}['{col}'] = {df_name}['{col}'].fillna({repr(rule.fill_value)})")
                elif rule.strategy == CleaningStrategy.FILL_FORWARD:
                    lines.append(f"{df_name}['{col}'] = {df_name}['{col}'].ffill()")
                elif rule.strategy == CleaningStrategy.FILL_BACKWARD:
                    lines.append(f"{df_name}['{col}'] = {df_name}['{col}'].bfill()")
                elif rule.strategy == CleaningStrategy.INTERPOLATE:
                    lines.append(f"{df_name}['{col}'] = {df_name}['{col}'].interpolate()")

            if rule.category_mapping:
                lines.append(f"{df_name}['{col}'] = {df_name}['{col}'].replace({rule.category_mapping})")

            if rule.dtype:
                if rule.dtype == "datetime64[ns]" and rule.date_format:
                    lines.append(f"{df_name}['{col}'] = pd.to_datetime({df_name}['{col}'], format='{rule.date_format}', errors='coerce')")
                else:
                    lines.append(f"{df_name}['{col}'] = {df_name}['{col}'].astype('{rule.dtype}')")

            lines.append("")

        # Drop columns
        if plan.drop_columns:
            lines.append(f"{df_name} = {df_name}.drop(columns={plan.drop_columns})")
            lines.append("")

        # Global dropna
        if plan.global_null_strategy == CleaningStrategy.DROP:
            lines.append(f"{df_name} = {df_name}.dropna()")

        return "\n".join(lines)


def clean_dataframe(
    df: pd.DataFrame,
    plan: Optional[CleaningPlan] = None,
    config: Optional[Dict[str, Any]] = None,
) -> tuple[pd.DataFrame, Dict[str, Any]]:
    """Convenience function to clean a DataFrame."""
    cleaner = DataCleaner(config)
    cleaned = cleaner.clean(df, plan)
    return cleaned, cleaner.get_report()