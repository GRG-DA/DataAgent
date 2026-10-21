"""
Auto-ML Module
Automated feature selection, regression model comparison,
and end-to-end automated analysis pipeline.
"""

import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Re-use existing analysis functions from the statistical module
from analysis.statistical import (
    fit_regression,
    compute_feature_importance,
    RegressionResult,
)


# ─────────────────────────────────────────────────────────────
# Result Dataclasses
# ─────────────────────────────────────────────────────────────

@dataclass
class FeatureSelectionResult:
    """Result of automated feature selection."""
    target_col: str
    method: str
    n_features_original: int
    n_features_selected: int
    selected_features: List[str]
    dropped_features: List[str]
    feature_scores: Dict[str, float]
    correlation_matrix: Dict[str, Dict[str, float]]
    interpretation: str


@dataclass
class RegressionComparisonResult:
    """Result of comparing linear vs polynomial regression."""
    target_col: str
    feature_cols: List[str]
    linear_result: Dict[str, Any]
    polynomial_result: Dict[str, Any]
    recommended_model: str
    recommendation_reason: str
    improvement_in_r_squared: float
    improvement_in_adj_r_squared: float
    improvement_in_rmse: float
    interpretation: str


@dataclass
class AutoAnalysisResult:
    """Result of a full automated analysis pipeline."""
    dataframe_info: Dict[str, Any]
    feature_selection: Dict[str, Any]
    regression_comparison: Dict[str, Any]
    feature_importance: Dict[str, Any]
    recommended_model: str
    recommended_features: List[str]
    summary: str
    all_steps: List[Dict[str, Any]]


# ─────────────────────────────────────────────────────────────
# Feature Selection
# ─────────────────────────────────────────────────────────────

def auto_select_features(
    df: pd.DataFrame,
    target_col: str,
    n_features: Optional[int] = None,
    correlation_threshold: float = 0.05,
    multicollinearity_threshold: float = 0.90,
) -> FeatureSelectionResult:
    """
    Automatically select the most relevant features for predicting a target.

    Strategy:
        1. Compute absolute Pearson correlation of every numeric feature with the target.
        2. Rank features by |correlation| and keep the top `n_features` (default: all
           features with |r| > correlation_threshold).
        3. Among the selected features, remove one of any pair whose inter-feature
           correlation exceeds `multicollinearity_threshold` (keeping the one that
           correlates more strongly with the target).

    Parameters
    ----------
    df : pd.DataFrame
        Input data.
    target_col : str
        Target variable to predict.
    n_features : int, optional
        Exact number of features to keep.  If None, kept features are those with
        |r| > correlation_threshold (after deduplication).
    correlation_threshold : float
        Minimum absolute correlation with the target for a feature to be retained
        when n_features is not specified (default 0.05).
    multicollinearity_threshold : float
        Maximum inter-feature correlation allowed (default 0.90).

    Returns
    -------
    FeatureSelectionResult
        Selected and dropped features with scores.
    """
    numeric_df = df.select_dtypes(include=["number"])
    all_feature_cols = [c for c in numeric_df.columns if c != target_col]

    if not all_feature_cols:
        return FeatureSelectionResult(
            target_col=target_col,
            method="correlation_filter",
            n_features_original=0,
            n_features_selected=0,
            selected_features=[],
            dropped_features=[],
            feature_scores={},
            correlation_matrix={},
            interpretation="No numeric features found for selection.",
        )

    if target_col not in numeric_df.columns:
        return FeatureSelectionResult(
            target_col=target_col,
            method="correlation_filter",
            n_features_original=len(all_feature_cols),
            n_features_selected=0,
            selected_features=[],
            dropped_features=all_feature_cols,
            feature_scores={},
            correlation_matrix={},
            interpretation=f"Target column '{target_col}' not found in numeric columns.",
        )

    # Step 1: Compute correlations with target
    target_vals = numeric_df[target_col]
    scores: Dict[str, float] = {}
    for col in all_feature_cols:
        valid = numeric_df[[col, target_col]].dropna()
        if len(valid) < 3:
            scores[col] = 0.0
        else:
            corr = valid[col].corr(valid[target_col])
            scores[col] = abs(float(corr)) if not np.isnan(corr) else 0.0

    # Sort by absolute correlation descending
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    # Step 2: Filter by threshold or keep top n
    if n_features is not None:
        n_features = min(n_features, len(ranked))
        kept = [col for col, _ in ranked[:n_features]]
    else:
        kept = [col for col, score in ranked if score > correlation_threshold]

    if len(kept) == 0:
        kept = [col for col, _ in ranked[:min(3, len(ranked))]]

    dropped = [col for col in all_feature_cols if col not in kept]

    # Step 3: Remove multicollinear features
    # Compute inter-feature correlation matrix
    if len(kept) > 1:
        corr_sub = numeric_df[kept].corr()
        to_remove: set = set()

        for i in range(len(kept)):
            if kept[i] in to_remove:
                continue
            for j in range(i + 1, len(kept)):
                if kept[j] in to_remove:
                    continue
                inter_corr = abs(float(corr_sub.iloc[i, j]))
                if inter_corr > multicollinearity_threshold:
                    # Remove the feature with lower target correlation
                    if scores[kept[i]] >= scores[kept[j]]:
                        to_remove.add(kept[j])
                    else:
                        to_remove.add(kept[i])

        if to_remove:
            dropped.extend(to_remove)
            kept = [c for c in kept if c not in to_remove]

    # Build correlation matrix for the kept features
    if len(kept) > 1:
        corr_full = numeric_df[kept].corr()
        corr_dict = {
            col: {row: round(float(corr_full.loc[row, col]), 4) for row in kept}
            for col in kept
        }
    else:
        corr_dict = {kept[0]: {kept[0]: 1.0}} if kept else {}

    label = target_col
    interpretation = (
        f"Feature selection for '{label}': started with {len(all_feature_cols)} numeric features, "
        f"selected {len(kept)} features. "
        f"Selected: {', '.join(kept) if kept else 'none'}. "
        f"Dropped: {', '.join(dropped) if dropped else 'none'}."
    )

    return FeatureSelectionResult(
        target_col=target_col,
        method="correlation_filter",
        n_features_original=len(all_feature_cols),
        n_features_selected=len(kept),
        selected_features=kept,
        dropped_features=dropped,
        feature_scores={k: round(v, 4) for k, v in scores.items()},
        correlation_matrix=corr_dict,
        interpretation=interpretation,
    )


# ─────────────────────────────────────────────────────────────
# Regression Comparison
# ─────────────────────────────────────────────────────────────

def compare_regressions(
    df: pd.DataFrame,
    target_col: str,
    feature_cols: Optional[List[str]] = None,
    threshold_r2_delta: float = 0.02,
) -> RegressionComparisonResult:
    """
    Fit both linear and polynomial (degree 2) regressions and recommend the
    better model.

    Recommendation criteria (in order of priority):
        1. The polynomial model must have higher adjusted R-squared.
           The improvement must exceed `threshold_r2_delta` to be considered
           meaningful (avoids over-fitting with marginal gains).
        2. If the polynomial model does not meaningfully improve adj-R2,
           the simpler linear model is recommended.

    Parameters
    ----------
    df : pd.DataFrame
        Input data.
    target_col : str
        Target variable.
    feature_cols : list of str, optional
        Feature columns. If None, all numeric columns except the target are used.
    threshold_r2_delta : float
        Minimum improvement in adjusted R-squared for the polynomial model
        to be recommended over the linear model (default 0.02).

    Returns
    -------
    RegressionComparisonResult
        Both regression results plus a recommendation.
    """
    numeric_df = df.select_dtypes(include=["number"])
    if feature_cols is None:
        feature_cols = [c for c in numeric_df.columns if c != target_col]

    if not feature_cols:
        empty = _empty_reg_comparison(target_col, [], "No numeric features available.")
        return empty

    # Fit linear regression
    lin_result = fit_regression(df, target_col, feature_cols, regression_type="linear")
    poly_result = fit_regression(df, target_col, feature_cols, regression_type="polynomial")

    # Convert dataclass results to dicts for easier comparison
    lin_dict = lin_result.__dict__.copy() if hasattr(lin_result, "__dict__") else {}
    poly_dict = poly_result.__dict__.copy() if hasattr(poly_result, "__dict__") else {}

    lin_r2 = lin_result.adj_r_squared if hasattr(lin_result, "adj_r_squared") else 0.0
    poly_r2 = poly_result.adj_r_squared if hasattr(poly_result, "adj_r_squared") else 0.0
    lin_rmse = lin_result.rmse if hasattr(lin_result, "rmse") else float("inf")
    poly_rmse = poly_result.rmse if hasattr(poly_result, "rmse") else float("inf")

    delta_r2 = poly_r2 - lin_r2
    delta_adj_r2 = poly_r2 - lin_r2
    delta_rmse = lin_rmse - poly_rmse  # positive = polynomial is better

    # Recommendation
    if delta_adj_r2 > threshold_r2_delta and poly_r2 > lin_r2:
        recommended = "polynomial"
        reason = (
            f"The polynomial model provides a meaningful improvement in adjusted R-squared "
            f"(+{delta_adj_r2:.4f}, threshold={threshold_r2_delta:.4f}) "
            f"and lower RMSE ({poly_rmse:.4f} vs {lin_rmse:.4f})."
        )
    else:
        recommended = "linear"
        if delta_adj_r2 <= threshold_r2_delta:
            reason = (
                f"The polynomial model's improvement in adjusted R-squared is marginal "
                f"(+{delta_adj_r2:.4f}, threshold={threshold_r2_delta:.4f}). "
                f"Recommending the simpler linear model to avoid overfitting."
            )
        else:
            reason = (
                f"The linear model has higher adjusted R-squared "
                f"({lin_r2:.4f} vs {poly_r2:.4f}). Recommending the linear model."
            )

    interpretation = (
        f"Regression comparison for '{target_col}' with {len(feature_cols)} features. "
        f"Linear: R2={lin_result.r_squared:.4f}, adj-R2={lin_r2:.4f}, RMSE={lin_rmse:.4f}. "
        f"Polynomial: R2={poly_result.r_squared:.4f}, adj-R2={poly_r2:.4f}, RMSE={poly_rmse:.4f}. "
        f"Recommendation: {recommended.upper()}. {reason}"
    )

    return RegressionComparisonResult(
        target_col=target_col,
        feature_cols=feature_cols,
        linear_result=lin_dict,
        polynomial_result=poly_dict,
        recommended_model=recommended,
        recommendation_reason=reason,
        improvement_in_r_squared=round(float(poly_result.r_squared - lin_result.r_squared), 6),
        improvement_in_adj_r_squared=round(float(delta_adj_r2), 6),
        improvement_in_rmse=round(float(delta_rmse), 6),
        interpretation=interpretation,
    )


def _empty_reg_comparison(target: str, features: List[str], reason: str) -> RegressionComparisonResult:
    """Return an empty RegressionComparisonResult."""
    return RegressionComparisonResult(
        target_col=target,
        feature_cols=features,
        linear_result={},
        polynomial_result={},
        recommended_model="linear",
        recommendation_reason=reason,
        improvement_in_r_squared=0.0,
        improvement_in_adj_r_squared=0.0,
        improvement_in_rmse=0.0,
        interpretation=reason,
    )


# ─────────────────────────────────────────────────────────────
# Full Automated Analysis Pipeline
# ─────────────────────────────────────────────────────────────

def auto_analyze(
    df: pd.DataFrame,
    target_col: str,
    n_features: Optional[int] = None,
    correlation_threshold: float = 0.05,
    multicollinearity_threshold: float = 0.90,
    threshold_r2_delta: float = 0.02,
    importance_method: str = "correlation",
) -> AutoAnalysisResult:
    """
    Run a complete automated analysis pipeline:

        1. Data overview (shape, dtypes, missing values).
        2. Feature selection via correlation filtering.
        3. Regression model comparison (linear vs polynomial).
        4. Feature importance ranking on the selected features.

    Parameters
    ----------
    df : pd.DataFrame
        Input data.
    target_col : str
        Target variable to predict.
    n_features : int, optional
        Number of features to select.
    correlation_threshold : float
        Minimum |correlation| with target for feature retention.
    multicollinearity_threshold : float
        Maximum inter-feature correlation allowed.
    threshold_r2_delta : float
        Minimum adj-R2 improvement for polynomial to be recommended.
    importance_method : str
        Feature importance method ("correlation" or "mutual_info").

    Returns
    -------
    AutoAnalysisResult
        All intermediate results plus a summary and recommendation.
    """
    steps: List[Dict[str, Any]] = []
    summary_parts: List[str] = []

    # ── Step 1: Data overview ──
    numeric_df = df.select_dtypes(include=["number"])
    n_rows, n_cols = df.shape
    n_numeric = len(numeric_df.columns)
    missing_total = int(df.isnull().sum().sum())
    missing_pct = round(missing_total / (n_rows * n_cols) * 100, 2) if n_rows * n_cols > 0 else 0.0

    dataframe_info = {
        "n_rows": n_rows,
        "n_columns": n_cols,
        "n_numeric_columns": n_numeric,
        "total_missing_values": missing_total,
        "missing_percentage": missing_pct,
        "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
        "numeric_columns": list(numeric_df.columns),
    }
    steps.append({"step": "data_overview", "status": "complete", "result": dataframe_info})
    summary_parts.append(
        f"Dataset has {n_rows} rows, {n_cols} columns ({n_numeric} numeric), "
        f"with {missing_pct:.1f}% missing values."
    )

    # ── Step 2: Feature selection ──
    selection_result = auto_select_features(
        df,
        target_col=target_col,
        n_features=n_features,
        correlation_threshold=correlation_threshold,
        multicollinearity_threshold=multicollinearity_threshold,
    )
    selected_features = selection_result.selected_features
    steps.append({
        "step": "feature_selection",
        "status": "complete",
        "result": selection_result.__dict__.copy() if hasattr(selection_result, "__dict__") else {},
    })
    summary_parts.append(
        f"Feature selection: {selection_result.n_features_selected}/{selection_result.n_features_original} "
        f"features selected."
    )

    if not selected_features:
        summary_parts.append("No features were selected; automatic regression comparison skipped.")

    # ── Step 3: Regression comparison (only if we have selected features) ──
    reg_comparison: Optional[RegressionComparisonResult] = None
    if selected_features and target_col in numeric_df.columns:
        reg_comparison = compare_regressions(
            df,
            target_col=target_col,
            feature_cols=selected_features,
            threshold_r2_delta=threshold_r2_delta,
        )
        steps.append({
            "step": "regression_comparison",
            "status": "complete",
            "result": reg_comparison.__dict__.copy() if hasattr(reg_comparison, "__dict__") else {},
        })
        summary_parts.append(
            f"Regression comparison: recommended model is {reg_comparison.recommended_model.upper()}. "
            f"{reg_comparison.recommendation_reason}"
        )
    else:
        steps.append({
            "step": "regression_comparison",
            "status": "skipped",
            "result": {"reason": "No selected features or target not numeric."},
        })

    # ── Step 4: Feature importance ──
    importance_result: Dict[str, Any] = {}
    if selected_features and target_col in numeric_df.columns:
        # Build a sub-frame with only the selected features + target for importance
        sub_df = numeric_df[selected_features + [target_col]].dropna()
        if len(sub_df) >= 3:
            fi = compute_feature_importance(sub_df, target_col, method=importance_method)
            importance_result = fi.__dict__.copy() if hasattr(fi, "__dict__") else {}
            steps.append({
                "step": "feature_importance",
                "status": "complete",
                "result": importance_result,
            })
            top_features = [f["feature"] for f in fi.features[:3]] if fi.features else []
            summary_parts.append(
                f"Feature importance ({importance_method}): top features are "
                f"{', '.join(top_features) if top_features else 'none'}."
            )
        else:
            steps.append({
                "step": "feature_importance",
                "status": "insufficient_data",
                "result": {},
            })
    else:
        steps.append({
            "step": "feature_importance",
            "status": "skipped",
            "result": {"reason": "No selected features or target not numeric."},
        })

    # ── Final recommendation ──
    recommended_model = reg_comparison.recommended_model if reg_comparison else "linear"
    recommended_features = selected_features

    summary = " | ".join(summary_parts)

    return AutoAnalysisResult(
        dataframe_info=dataframe_info,
        feature_selection=selection_result.__dict__.copy() if hasattr(selection_result, "__dict__") else {},
        regression_comparison=reg_comparison.__dict__.copy() if reg_comparison and hasattr(reg_comparison, "__dict__") else {},
        feature_importance=importance_result,
        recommended_model=recommended_model,
        recommended_features=recommended_features,
        summary=summary,
        all_steps=steps,
    )
