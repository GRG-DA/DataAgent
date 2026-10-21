"""
Chart Recommendation Module
Auto-detect best chart types based on data type analysis
"""

import logging
from typing import Dict, List, Optional, Any, Tuple
import pandas as pd

logger = logging.getLogger(__name__)


def recommend_charts(df: pd.DataFrame, top_n: int = 5) -> Dict[str, Any]:
    """
    Recommend best chart types based on the data's column types and characteristics.

    Returns a list of chart suggestions with scores and reasoning.
    """
    if df.empty:
        return {"success": True, "recommendations": [], "message": "Empty DataFrame — no charts to recommend."}

    numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()
    cat_cols = df.select_dtypes(include=["object", "category"]).columns.tolist()
    date_cols = df.select_dtypes(include=["datetime64"]).columns.tolist()
    bool_cols = df.select_dtypes(include=["bool"]).columns.tolist()

    # Also check for string columns that look like dates
    for col in df.columns:
        if col in cat_cols:
            try:
                sample = df[col].dropna().head(10)
                if len(sample) > 0:
                    pd.to_datetime(sample)
                    date_cols.append(col)
                    cat_cols.remove(col) if col in cat_cols else None
            except (ValueError, TypeError):
                pass

    all_numeric = set(numeric_cols)
    all_categorical = set(cat_cols)
    all_dates = set(date_cols)

    recommendations = []
    used_combos = set()

    def _add_rec(chart_type: str, x: str, y: Optional[str], color: Optional[str],
                 score: int, reasoning: str):
        key = f"{chart_type}:{x}:{y or ''}"
        if key not in used_combos:
            used_combos.add(key)
            recommendations.append({
                "chart_type": chart_type,
                "x_axis": x,
                "y_axis": y,
                "color": color,
                "score": score,
                "reasoning": reasoning,
            })

    # 1. Time series trends (date + numeric → line chart)
    if all_dates and all_numeric:
        for dcol in list(all_dates)[:1]:
            for ncol in list(all_numeric)[:3]:
                _add_rec(
                    "line", dcol, ncol, None,
                    score=95,
                    reasoning=f"Time series of '{ncol}' over '{dcol}' — ideal for trend analysis."
                )
                # Add with color dimension if categorical column exists
                if all_categorical:
                    _add_rec(
                        "line", dcol, ncol, str(list(all_categorical)[0]),
                        score=90,
                        reasoning=f"Time series of '{ncol}' by '{list(all_categorical)[0]}' — compare trends across categories."
                    )

    # 2. Category vs numeric (bar chart)
    if all_categorical and all_numeric:
        for ccol in list(all_categorical)[:2]:
            for ncol in list(all_numeric)[:2]:
                _add_rec(
                    "bar", ccol, ncol, None,
                    score=90,
                    reasoning=f"Compare '{ncol}' across '{ccol}' categories — bar chart is clearest for comparisons."
                )

    # 3. Distribution of single numeric (histogram)
    for ncol in list(all_numeric)[:3]:
        _add_rec(
            "histogram", ncol, None, None,
            score=85,
            reasoning=f"Distribution of '{ncol}' — histogram shows the shape and spread of the data."
        )

    # 4. Two numeric columns (scatter)
    if len(all_numeric) >= 2:
        for i, n1 in enumerate(list(all_numeric)[:3]):
            for n2 in list(all_numeric)[i + 1:3]:
                _add_rec(
                    "scatter", n1, n2, None,
                    score=80,
                    reasoning=f"Relationship between '{n1}' and '{n2}' — scatter plot reveals correlation patterns."
                )
                # With color
                if all_categorical:
                    _add_rec(
                        "scatter", n1, n2, str(list(all_categorical)[0]),
                        score=85,
                        reasoning=f"Relationship between '{n1}' and '{n2}' colored by '{list(all_categorical)[0]}'."
                    )

    # 5. Data distribution (box plot)
    if all_categorical and all_numeric:
        for ccol in list(all_categorical)[:2]:
            for ncol in list(all_numeric)[:2]:
                _add_rec(
                    "box", ccol, ncol, None,
                    score=75,
                    reasoning=f"Distribution of '{ncol}' across '{ccol}' — box plot shows quartiles and outliers."
                )

    # 6. Proportions (pie chart — for low cardinality)
    if all_categorical:
        for ccol in list(all_categorical)[:2]:
            nunique = df[ccol].nunique()
            if 2 <= nunique <= 6:
                _add_rec(
                    "pie", ccol, None, None,
                    score=70,
                    reasoning=f"Proportion breakdown of '{ccol}' — pie chart shows composition clearly at {nunique} categories."
                )

    # 7. Numeric vs numeric (area)
    if all_dates and all_numeric:
        for dcol in list(all_dates)[:1]:
            for ncol in list(all_numeric)[:2]:
                _add_rec(
                    "area", dcol, ncol, None,
                    score=70,
                    reasoning=f"Area chart of '{ncol}' over '{dcol}' — emphasizes magnitude of change over time."
                )

    # 8. Correlation heatmap (when 3+ numeric columns)
    if len(all_numeric) >= 3:
        _add_rec(
            "heatmap", list(all_numeric)[0], list(all_numeric)[1], None,
            score=88,
            reasoning=f"Correlation heatmap of {min(6, len(all_numeric))} numeric columns — "
                       f"quickly identifies relationships across all variables."
        )

    # 9. Two categorical columns (stacked bar / heatmap)
    if len(all_categorical) >= 2:
        c1, c2 = list(all_categorical)[:2]
        _add_rec(
            "heatmap", c1, c2, None,
            score=65,
            reasoning=f"Cross-tabulation of '{c1}' and '{c2}' — heatmap shows the relationship between two categorical variables."
        )

    # Sort by score descending, limit to top_n
    recommendations.sort(key=lambda r: r["score"], reverse=True)
    recommendations = recommendations[:top_n]

    # Add metadata
    for r in recommendations:
        r["x_type"] = _col_type(r["x_axis"], df, all_numeric, all_categorical, all_dates)
        if r.get("y_axis"):
            r["y_type"] = _col_type(r["y_axis"], df, all_numeric, all_categorical, all_dates)

    return {
        "success": True,
        "recommendations": recommendations,
        "column_profile": {
            "numeric": len(all_numeric),
            "categorical": len(all_categorical),
            "datetime": len(all_dates),
            "total": len(df.columns),
        },
    }


def _col_type(col: str, df: pd.DataFrame, numeric: set, categorical: set, dates: set) -> str:
    """Get the data type category for a column."""
    if col in numeric:
        return "numeric"
    if col in categorical:
        return "categorical"
    if col in dates:
        return "datetime"
    return str(df[col].dtype) if col in df.columns else "unknown"
