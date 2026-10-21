"""
Auto Insights Module
Generates natural-language narrative insights from data analysis
"""

import logging
from typing import Dict, List, Optional, Any, Tuple
import numpy as np
import pandas as pd
from api.tenancy import tenant_search_path

logger = logging.getLogger(__name__)


def generate_auto_insights(
    df: pd.DataFrame,
    profile: Optional[Dict[str, Any]] = None,
    correlation_data: Optional[Dict[str, Any]] = None,
    outlier_data: Optional[Dict[str, Any]] = None,
    table_name: str = "",
) -> Dict[str, Any]:
    """
    Generate auto-magical insights about a dataset.

    Combines profile stats, correlations, outliers, and distributions
    into a structured narrative with key findings.
    """
    if df.empty:
        return _empty_insights(table_name)

    insights = {
        "table": table_name,
        "summary": "",
        "key_findings": [],
        "metrics_summary": [],
        "correlation_highlights": [],
        "outlier_highlights": [],
        "distributions": [],
        "recommendations": [],
        "confidence": "high",
    }

    #  Dataset overview 
    n_rows, n_cols = df.shape
    insights["summary"] = (
        f"This dataset contains **{n_rows:,} rows** and **{n_cols} columns**. "
    )

    #  Column type breakdown 
    numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()
    cat_cols = df.select_dtypes(include=["object", "category"]).columns.tolist()
    date_cols = df.select_dtypes(include=["datetime64"]).columns.tolist()

    type_breakdown = []
    if numeric_cols:
        type_breakdown.append(f"{len(numeric_cols)} numeric")
    if cat_cols:
        type_breakdown.append(f"{len(cat_cols)} categorical/text")
    if date_cols:
        type_breakdown.append(f"{len(date_cols)} date/time")

    if type_breakdown:
        insights["summary"] += f"Contains {', '.join(type_breakdown)} columns. "

    #  Missing data 
    total_null = df.isnull().sum().sum()
    null_pct = total_null / (n_rows * n_cols) * 100
    if null_pct > 20:
        insights["key_findings"].append(f" **{null_pct:.1f}%** of cells are missing  data quality may be a concern.")
        insights["confidence"] = "medium"
    elif null_pct > 0:
        insights["key_findings"].append(f" **{null_pct:.1f}%** missing values  low missingness.")

    #  Duplicates 
    dup_count = int(df.duplicated().sum())
    if dup_count > 0:
        dup_pct = dup_count / n_rows * 100
        if dup_pct > 10:
            insights["key_findings"].append(f" **{dup_pct:.1f}%** of rows are duplicates ({dup_count:,} rows).")
        else:
            insights["key_findings"].append(f" **{dup_count:,}** duplicate rows found ({dup_pct:.1f}%).")

    #  Numeric column insights 
    for col in numeric_cols[:5]:  # Top 5 numeric columns
        series = df[col].dropna()
        if len(series) < 2:
            continue

        mean = series.mean()
        median = series.median()
        std = series.std()
        skew = series.skew() if len(series) > 2 else 0

        # Direction of skew
        if abs(skew) > 0.5:
            skew_dir = "right-skewed" if skew > 0 else "left-skewed"
            note = f"{col} is **{skew_dir}** (skew={skew:.2f})"
            if skew > 0:
                note += f"  most values are lower than the mean of {mean:.2f}"
            else:
                note += f"  most values are higher than the mean of {mean:.2f}"
            insights["distributions"].append(note)

        # CV for variability
        if mean != 0:
            cv = abs(std / mean)
            if cv > 1:
                insights["key_findings"].append(f" **{col}** has very high variability (CV={cv:.2f}).")

        # Range
        insights["metrics_summary"].append({
            "column": col,
            "mean": round(mean, 2),
            "median": round(median, 2),
            "std": round(std, 2),
            "min": round(series.min(), 2),
            "max": round(series.max(), 2),
        })

    #  Categorical column insights 
    for col in cat_cols[:5]:
        series = df[col].dropna()
        if len(series) == 0:
            continue

        top_val = series.value_counts().index[0]
        top_pct = series.value_counts().iloc[0] / len(series) * 100
        nunique = series.nunique()

        if nunique == 1:
            insights["key_findings"].append(f" **{col}** is constant: all values are '{top_val}'.")
        elif nunique <= 5 and top_pct > 50:
            insights["key_findings"].append(f" **{col}** is dominated by '{top_val}' ({top_pct:.0f}% of values).")
        elif nunique > 50:
            insights["key_findings"].append(f" **{col}** has high cardinality ({nunique} unique values).")

    #  Date range insights 
    for col in date_cols[:2]:
        series = df[col].dropna()
        if len(series) > 0:
            min_date = series.min()
            max_date = series.max()
            span = (max_date - min_date).days
            insights["key_findings"].append(
                f" **{col}** spans from {min_date.date()} to {max_date.date()} "
                f"({span} days)."
            )

    #  Correlation highlights 
    if correlation_data and correlation_data.get("pairs"):
        strong = [p for p in correlation_data["pairs"] if abs(p.get("correlation", 0)) > 0.7]
        for pair in strong[:3]:
            c1, c2 = pair["col1"], pair["col2"]
            corr = pair["correlation"]
            dir_text = "rise together" if corr > 0 else "move in opposite directions"
            insights["correlation_highlights"].append(
                f" Strong {pair['direction']} correlation ({corr:.2f}) between **{c1}** and **{c2}**  "
                f"these variables tend to {dir_text}."
            )

    #  Outlier highlights 
    if outlier_data and outlier_data.get("outliers"):
        for o in outlier_data["outliers"][:3]:
            o_pct = o.get("outlier_pct", 0)
            o_col = o.get("column", "")
            if o_pct > 5:
                insights["outlier_highlights"].append(
                    f" **{o_col}** has {o_pct:.1f}% outliers ({o.get('outlier_count', 0)} values) "
                    f"outside [{o.get('lower_bound', 0):.2f}, {o.get('upper_bound', 0):.2f}]."
                )

    #  Recommendations 
    insights["recommendations"] = _generate_recommendations(df, insights)

    #  Summary 
    insights["summary"] += f"\n\n**Dataset name:** {table_name or getattr(df, 'name', None) or 'unnamed'}"

    return insights


def generate_narrative(
    df: pd.DataFrame,
    analysis_type: str = "summary",
    table_name: str = "",
) -> Dict[str, Any]:
    """
    Generate a specific type of narrative.

    Types:
    - summary: General data overview
    - comparison: Compare two groups
    - trend: Temporal trend analysis
    - anomaly: Anomaly explanation
    """
    if analysis_type == "summary":
        return generate_auto_insights(df, table_name=table_name)
    elif analysis_type == "comparison":
        return _comparison_narrative(df)
    elif analysis_type == "trend":
        return _trend_narrative(df)
    elif analysis_type == "anomaly":
        return _anomaly_narrative(df)
    else:
        return {"success": False, "error": f"Unknown narrative type: {analysis_type}"}


# 
# Internal Helpers
# 

def _empty_insights(table_name: str) -> Dict[str, Any]:
    return {
        "table": table_name,
        "summary": "The dataset is empty.",
        "key_findings": ["The dataset contains no rows."],
        "metrics_summary": [],
        "correlation_highlights": [],
        "outlier_highlights": [],
        "distributions": [],
        "recommendations": ["Upload or import data to begin analysis."],
        "confidence": "high",
    }


def _comparison_narrative(df: pd.DataFrame) -> Dict[str, Any]:
    """Generate comparison narrative."""
    cat_cols = df.select_dtypes(include=["object", "category"]).columns.tolist()
    num_cols = df.select_dtypes(include=["number"]).columns.tolist()

    if len(cat_cols) < 1 or len(num_cols) < 1:
        return {"success": False, "error": "Need at least one categorical and one numeric column for comparison."}

    group_col = cat_cols[0]
    metric_col = num_cols[0]
    groups = df.groupby(group_col)[metric_col]

    top_group = groups.mean().idxmax()
    bottom_group = groups.mean().idxmin()
    top_mean = groups.mean().max()
    bottom_mean = groups.mean().min()

    return {
        "success": True,
        "title": f"Comparing '{metric_col}' across '{group_col}'",
        "summary": (
            f"Among **{group_col}**, **{top_group}** has the highest average {metric_col} "
            f"({top_mean:.2f}), while **{bottom_group}** has the lowest ({bottom_mean:.2f}). "
            f"The ratio between top and bottom is {top_mean / bottom_mean:.2f}x."
        ),
        "top_group": str(top_group),
        "bottom_group": str(bottom_group),
        "difference": round(float(top_mean - bottom_mean), 2),
        "ratio": round(float(top_mean / bottom_mean), 2) if bottom_mean != 0 else None,
    }


def _trend_narrative(df: pd.DataFrame) -> Dict[str, Any]:
    """Generate trend narrative."""
    date_cols = df.select_dtypes(include=["datetime64"]).columns.tolist()
    num_cols = df.select_dtypes(include=["number"]).columns.tolist()

    if len(date_cols) < 1 or len(num_cols) < 1:
        return {"success": False, "error": "Need date and numeric columns for trend analysis."}

    date_col = date_cols[0]
    metric_col = num_cols[0]
    ts = df.groupby(date_col)[metric_col].sum().sort_index()

    if len(ts) < 2:
        return {"success": False, "error": "Need at least 2 time periods for trend."}

    first, last = ts.iloc[0], ts.iloc[-1]
    total_change = last - first
    pct_change = (total_change / first * 100) if first != 0 else 0

    # Monotonicity
    diffs = np.diff(ts.values)
    up_count = (diffs > 0).sum()
    down_count = (diffs < 0).sum()
    direction = "increasing" if up_count > down_count else "decreasing"

    return {
        "success": True,
        "title": f"Trend Analysis: {metric_col} over time",
        "summary": (
            f"From {ts.index[0].date()} to {ts.index[-1].date()}, **{metric_col}** "
            f"{direction}d overall by **{pct_change:.1f}%** "
            f"(from {first:.2f} to {last:.2f}). "
            f"Values {direction}d in {max(up_count, down_count)} out of {len(diffs)} periods "
            f"({max(up_count, down_count) / len(diffs):.0%} of the time)."
        ),
        "first_value": round(float(first), 2),
        "last_value": round(float(last), 2),
        "absolute_change": round(float(total_change), 2),
        "percentage_change": round(float(pct_change), 2),
        "direction": direction,
        "periods_analyzed": len(ts),
    }


def _anomaly_narrative(df: pd.DataFrame) -> Dict[str, Any]:
    """Generate anomaly narrative."""
    num_cols = df.select_dtypes(include=["number"]).columns.tolist()
    if not num_cols:
        return {"success": False, "error": "Need numeric columns for anomaly detection."}

    anomalies_found = 0
    anomaly_details = []

    for col in num_cols[:3]:
        series = df[col].dropna()
        if len(series) < 3:
            continue
        mean, std = series.mean(), series.std()
        if std == 0:
            continue

        z_scores = (series - mean) / std
        anomaly_mask = z_scores.abs() > 2
        count = anomaly_mask.sum()

        if count > 0:
            anomalies_found += count
            anomaly_details.append(f"**{col}**: {count} anomalies (Z-score > 2)")

    if anomalies_found == 0:
        return {
            "success": True,
            "summary": "No significant anomalies detected in the numeric columns. All values fall within expected ranges.",
            "anomalies_found": 0,
        }

    return {
        "success": True,
        "summary": (
            f"Detected **{anomalies_found}** potential anomaly(ies) across {len(anomaly_details)} column(s). "
            + " ".join(anomaly_details)
        ),
        "anomalies_found": anomalies_found,
        "details": anomaly_details,
    }


def _generate_recommendations(df: pd.DataFrame, insights: Dict) -> List[str]:
    """Generate actionable recommendations."""
    recs = []

    n_rows, n_cols = df.shape
    numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()
    cat_cols = df.select_dtypes(include=["object", "category"]).columns.tolist()
    date_cols = df.select_dtypes(include=["datetime64"]).columns.tolist()

    # Recommend visualizations
    if date_cols and numeric_cols:
        recs.append(f" Visualize **{numeric_cols[0]}** trend over time with a line chart against **{date_cols[0]}**.")
    if cat_cols and numeric_cols:
        recs.append(f" Compare **{numeric_cols[0]}** across **{cat_cols[0]}** groups with a bar chart.")
    if len(numeric_cols) >= 2:
        recs.append(f" Explore correlation between **{numeric_cols[0]}** and **{numeric_cols[1]}** with a scatter plot.")

    # Recommend statistical tests
    if cat_cols and numeric_cols:
        recs.append(f" Run a statistical test to check if **{numeric_cols[0]}** differs significantly across **{cat_cols[0]}** groups.")
    if cat_cols and len(df[cat_cols[0]].unique()) <= 10:
        recs.append(f" Use segmentation analysis to compare all groups in **{cat_cols[0]}**.")

    if not recs:
        recs.append(" Dataset ready for analysis. Apply filters or upload additional data for deeper insights.")

    return recs[:5]


async def generate_llm_insights(
    df: pd.DataFrame,
    table_name: str = "",
    user_question: str = "",
    client=None,
) -> Dict[str, Any]:
    """
    Generate AI-powered narrative insights using the LLM client.

    Combines statistical profiling with LLM-generated business narrative.
    Falls back to rule-based insights if LLM is not available.

    `client` is an optional LLMClient instance to reuse (e.g. the app's shared
    orchestrator client). When omitted, a configured LLMClient is created.

    Returns a dict with:
        - narrative: LLM-generated business narrative
        - findings: list of key findings
        - recommendations: list of actionable recommendations
        - contradictions: list of contradictory patterns found
        - confidence: overall confidence level
        - source: "llm" when generated by the model, "rule-based" on fallback
    """
    # Build context summary for the LLM
    n_rows, n_cols = df.shape
    numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()
    cat_cols = df.select_dtypes(include=["object", "category"]).columns.tolist()
    date_cols = df.select_dtypes(include=["datetime64"]).columns.tolist()

    # Date range (when a proper datetime column exists)
    date_range = ""
    if date_cols:
        dc = date_cols[0]
        s = df[dc].dropna()
        if len(s) > 0 and hasattr(s.dt, "min"):
            try:
                amin, amax = s.min(), s.max()
                date_range = f"{dc}: {amin:%Y-%m-%d} to {amax:%Y-%m-%d} (span {(amax - amin).days} days)"
            except Exception:
                date_range = ""

    # Build a compact data summary
    stats_lines = []
    for col in numeric_cols[:6]:
        s = df[col].dropna()
        if len(s) > 0:
            stats_lines.append(f"  {col}: mean={s.mean():.2f}, median={s.median():.2f}, std={s.std():.2f}, min={s.min():.2f}, max={s.max():.2f}")

    # Segment contribution highlights: for the first numeric measure, show the
    # top categorical values by aggregate contribution so the model can name
    # concrete leaders / laggards instead of speaking in vague generalities.
    seg_lines = []
    if numeric_cols and cat_cols:
        try:
            mcol = numeric_cols[0]
            col = cat_cols[0]
            grp = df.groupby(col)[mcol].sum().sort_values(ascending=False)
            total = float(grp.sum())
            top = grp.head(4)
            if total and total > 0:
                for k, v in top.items():
                    seg_lines.append(f"    {col} '{k}': {v:,.0f} ({v / total * 100:.0f}% of {mcol})")
        except Exception:
            pass

    for col in cat_cols[:4]:
        vc = df[col].value_counts()
        top3 = ", ".join(f"{k}({v})" for k, v in vc.head(3).items())
        stats_lines.append(f"  {col}: {vc.nunique()} unique values, top: {top3}")

    data_summary = "\n".join(stats_lines) if stats_lines else "No numeric or categorical data available."
    if date_range:
        data_summary = f"  {date_range}\n" + data_summary
    if seg_lines:
        data_summary += "\nTop segments by contribution:\n" + "\n".join(seg_lines)

    prompt = f"""You are a senior BI analyst preparing an executive dashboard narrative in the style of a Power BI report. Analyze the dataset below and write a crisp, specific narrative.

Dataset: {table_name or 'unnamed'} ({n_rows:,} rows  {n_cols} columns)
Numeric columns: {', '.join(numeric_cols[:8])}
Categorical columns: {', '.join(cat_cols[:6])}
Date columns: {', '.join(date_cols[:4])}

Key Statistics:
{data_summary}

{f'User question: {user_question}' if user_question else ''}

Every count, mean, percentage and segment figure above is EXACT and measured. Use the numbers verbatim. Never invent, estimate, approximate or round row counts, percentages, means or segment contributions beyond what is given, and do not claim a metric you do not see in the statistics.

Write the narrative in 4 clearly-labelled sections (plain text, short headings):
EXECUTIVE_OVERVIEW: 2-3 sentences giving the overall picture with at least two specific numbers (e.g. total volume, headline percentage, top segment share).
KEY_FINDINGS: 3-5 bullet points, each starting with "-", naming the actual top/leading segments and laggards with their real values, and noting distributions, skew or variability shown above.
ANOMALIES_AND_CONCERNS: 1-2 bullets on outliers, high-variability measures, missing data, or segment concentration that warrant attention (only if the stats support it).
RECOMMENDATIONS: 2-3 specific, actionable next steps tied to the numbers you cited.

Keep it under ~180 words. Be concrete and decisive, never generic."""


    try:
        # Reuse a provided client (e.g. the app's shared orchestrator client),
        # otherwise create one from the configured settings.
        if client is None:
            from agent.llm_client import LLMClient
            client = LLMClient()

        from agent.llm_client import Message
        msgs = [
            Message(role="system", content="You are an expert BI analyst writing executive dashboard narratives in a professional Power BI report style. Always cite the exact numbers provided in the data statistics verbatim — never estimate, round, or invent figures, counts, percentages, or segment values. Be decisive and structured."),
            Message(role="user", content=prompt),
        ]
        # think: False disables the qwen3 silent reasoning phase, otherwise the
        # model returns empty content and insights would fall back to rule-based.
        resp = await client.chat(msgs, options={"temperature": 0.3, "max_tokens": 1200, "think": False})
        if hasattr(resp, "message"):
            narrative = resp.message.content
        else:
            narrative = str(resp)

        if not narrative:
            raise RuntimeError("LLM returned empty response")

        # Pair the LLM narrative with deterministic, verified facts so the
        # response always carries exact numbers even if the model misstates
        # some counts in prose.
        auto = generate_auto_insights(df, table_name=table_name)
        return {
            "narrative": narrative,
            "summary": narrative,
            "findings": auto.get("key_findings", []),
            "key_findings": auto.get("key_findings", []),
            "recommendations": auto.get("recommendations", []),
            "metrics_summary": auto.get("metrics_summary", []),
            "correlation_highlights": auto.get("correlation_highlights", []),
            "outlier_highlights": auto.get("outlier_highlights", []),
            "contradictions": [],
            "confidence": "high",
            "source": "llm",
        }

    except Exception as e:
        logger.debug(f"LLM insights unavailable ({e}), using rule-based insights")
        # Fall back to rule-based insights
        result = generate_auto_insights(df, table_name=table_name)
        return {
            "narrative": result.get("summary", ""),
            "findings": result.get("key_findings", []),
            "recommendations": result.get("recommendations", []),
            "contradictions": [],
            "confidence": result.get("confidence", "medium"),
            "source": "rule-based",
        }

def generate_batch_insights(tables, db_path="data/curated/warehouse.duckdb", schema="analytics"):
    """Generate insights for multiple tables in batch mode."""
    import duckdb
    import pandas as pd
    results = {}
    try:
        conn = duckdb.connect(str(db_path))
        conn.execute(f"SET search_path = '{tenant_search_path()}'")
        for table in tables:
            try:
                df = conn.execute(f"SELECT * FROM {schema}.{table}").df()
                if not df.empty:
                    insights = generate_auto_insights(df, table)
                    results[table] = {"success": True, "insights": insights}
                else:
                    results[table] = {"success": False, "error": "Empty table"}
            except Exception as e:
                results[table] = {"success": False, "error": str(e)}
        conn.close()
    except Exception as e:
        return {"success": False, "error": str(e)}
    return {"success": True, "tables": results}