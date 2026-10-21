"""
Data Quality Module
Quality scoring, PII detection, and completeness analysis
"""

import logging
import re
from typing import Dict, List, Optional, Any, Tuple
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# PII Detection Patterns
# ─────────────────────────────────────────────────────────────

PII_PATTERNS = {
    "email": r'[\w\.-]+@[\w\.-]+\.\w+',
    "phone": r'(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}',
    "ssn": r'\b\d{3}-\d{2}-\d{4}\b',
    "credit_card": r'\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b',
    "ip_address": r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b',
    "zip_code": r'\b\d{5}(?:-\d{4})?\b',
}

PII_COLUMN_NAMES = {
    "email": ["email", "e-mail", "mail"],
    "phone": ["phone", "telephone", "mobile", "contact_no", "phone_number", "cell"],
    "ssn": ["ssn", "social_security", "socialsecurity"],
    "credit_card": ["credit_card", "cc_number", "card_number", "card"],
    "ip_address": ["ip", "ip_address", "ipaddress"],
    "name": ["name", "full_name", "customer_name", "first_name", "last_name"],
    "address": ["address", "street", "street_address"],
}

PII_TYPE_DESCRIPTIONS = {
    "email": "Email address (PII - can identify individuals)",
    "phone": "Phone number (PII - can identify individuals)",
    "ssn": "Social Security Number (Sensitive PII - requires protection)",
    "credit_card": "Credit card number (PCI-DSS regulated)",
    "ip_address": "IP address (may be considered PII in some jurisdictions)",
    "zip_code": "ZIP/Postal code (location data)",
    "name": "Person name (PII - can identify individuals)",
    "address": "Street address (PII - can identify individuals)",
}


# ─────────────────────────────────────────────────────────────
# Quality Scoring
# ─────────────────────────────────────────────────────────────

def compute_quality_score(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Compute a comprehensive data quality score (0-100).

    Dimensions:
    - Completeness: Missing value ratio
    - Uniqueness: Duplicate ratio
    - Consistency: Type consistency, value ranges
    - Validity: Format adherence, anomaly count
    - Timeliness: Recency of data (if date columns exist)

    Returns a dict with overall score, dimension scores, and issues.
    """
    if df.empty:
        return {
            "overall_score": 0.0,
            "dimension_scores": {
                "completeness": 0.0,
                "uniqueness": 0.0,
                "consistency": 0.0,
                "validity": 0.0,
                "timeliness": 0.0,
            },
            "column_scores": [],
            "issues": [],
            "recommendations": ["DataFrame is empty — no quality assessment possible."],
        }

    n_rows, n_cols = df.shape
    issues = []
    column_scores = []

    for col in df.columns:
        series = df[col]
        non_null = series.dropna()
        col_issues = []
        col_score = 100.0

        # Completeness
        null_pct = series.isnull().mean()
        completeness = 1 - null_pct
        col_completeness = round(completeness * 100, 1)
        if null_pct > 0.5:
            col_issues.append({"severity": "high", "column": col, "issue": f"High null ratio: {null_pct:.1%}", "suggestion": "Consider dropping or imputing this column"})
            col_score -= 20
        elif null_pct > 0.1:
            col_issues.append({"severity": "medium", "column": col, "issue": f"Moderate null ratio: {null_pct:.1%}", "suggestion": "Consider imputing missing values"})
            col_score -= 10
        elif null_pct > 0:
            col_issues.append({"severity": "low", "column": col, "issue": f"Low null ratio: {null_pct:.1%}", "suggestion": ""})
            col_score -= 2

        # Uniqueness
        if len(non_null) > 0:
            unique_pct = non_null.nunique() / len(series)
            if unique_pct > 0.99:
                # High cardinality — might be an ID column
                col_issues.append({"severity": "info", "column": col, "issue": "High cardinality — likely an ID/key column", "suggestion": ""})
            elif non_null.nunique() <= 1:
                # A single distinct value is suspicious; a handful of distinct
                # values (Region, Category, Status...) is a normal dimension.
                col_issues.append({"severity": "low", "column": col, "issue": "Constant column — only one distinct value", "suggestion": "Consider dropping this column"})

        # Duplicates across all columns
        dup_pct = df.duplicated().mean()
        uniqueness = 1 - dup_pct

        # Consistency
        consistency = 1.0
        if pd.api.types.is_numeric_dtype(series.dtype):
            # Check for negative values where not expected (e.g., counts)
            if col.lower() in ("count", "quantity", "age", "salary", "price"):
                neg_pct = (non_null < 0).mean()
                if neg_pct > 0:
                    consistency -= neg_pct * 0.5
                    col_issues.append({"severity": col_score > 80 and "medium" or "high", "column": col, "issue": f"{neg_pct:.1%} negative values in '{col}'", "suggestion": "Negative values may indicate data entry errors"})

            # Check for outliers (3-sigma)
            if len(non_null) > 3:
                mean, std = non_null.mean(), non_null.std()
                if std > 0:
                    outlier_mask = np.abs(non_null - mean) > 3 * std
                    outlier_pct = outlier_mask.mean()
                    if outlier_pct > 0.05:
                        col_issues.append({"severity": "medium", "column": col, "issue": f"{outlier_pct:.1%} extreme outliers (3-sigma)", "suggestion": "Review extreme values — may indicate data errors"})

        elif pd.api.types.is_datetime64_any_dtype(series.dtype):
            # Check for future dates
            now = pd.Timestamp.now()
            future = (non_null > now + pd.Timedelta(days=1)).mean()
            if future > 0.05:
                col_issues.append({"severity": "medium", "column": col, "issue": f"{future:.1%} dates in the future", "suggestion": "Future dates may indicate data entry errors"})
        else:
            # String/categorical
            if len(non_null) > 0:
                # Check for empty strings
                if non_null.dtype == "object":
                    empty_pct = (non_null.astype(str).str.strip() == "").mean()
                    if empty_pct > 0.05:
                        col_issues.append({"severity": "medium", "column": col, "issue": f"{empty_pct:.1%} empty strings", "suggestion": "Empty strings should be treated as missing values"})
                        col_score -= 10

        col_score = max(0, col_score)
        column_scores.append({
            "column": col,
            "dtype": str(series.dtype),
            "score": round(col_score, 1),
            "completeness": round(completeness * 100, 1),
            "issues": col_issues,
        })

    # Dimension scores
    completeness_score = _avg_completeness(df)
    uniqueness_score = (1 - dup_pct) * 100
    consistency_score = _consistency_score(df, column_scores)
    validity_score = _validity_score(df, column_scores)
    timeliness_score = _timeliness_score(df)

    overall = round(
        0.30 * completeness_score
        + 0.20 * uniqueness_score
        + 0.20 * consistency_score
        + 0.15 * validity_score
        + 0.15 * timeliness_score
    )

    # Aggregate issues
    all_issues = []
    for cs in column_scores:
        all_issues.extend(cs["issues"])

    # Recommendations
    recommendations = []
    if completeness_score < 70:
        recommendations.append("Improve completeness: Handle columns with high null ratios via imputation or removal.")
    if uniqueness_score < 95:
        recommendations.append(f"Remove {int(dup_pct * n_rows)} duplicate rows to improve data quality.")
    if consistency_score < 80:
        recommendations.append("Check for value range violations and data entry errors.")
    if not recommendations:
        recommendations.append("Data quality is good across all dimensions. Continue monitoring.")

    return {
        "overall_score": overall,
        "dimension_scores": {
            "completeness": round(completeness_score, 1),
            "uniqueness": round(uniqueness_score, 1),
            "consistency": round(consistency_score, 1),
            "validity": round(validity_score, 1),
            "timeliness": round(timeliness_score, 1),
        },
        "column_scores": column_scores,
        "issues": all_issues,
        "recommendations": recommendations,
        "row_count": n_rows,
        "column_count": n_cols,
        "duplicate_count": int(df.duplicated().sum()),
        "total_missing_cells": int(df.isnull().sum().sum()),
    }


def generate_quality_report(df: pd.DataFrame, table_name: str = "") -> Dict[str, Any]:
    """
    Generate a complete quality report combining profiling with quality scoring.
    """
    quality = compute_quality_score(df)
    profile_summary = {
        "table": table_name,
        "rows": len(df),
        "columns": len(df.columns),
        "memory_mb": round(df.memory_usage(deep=True).sum() / 1024 ** 2, 3),
    }

    return {
        "table": table_name,
        "profile": profile_summary,
        "quality": quality,
    }


# ─────────────────────────────────────────────────────────────
# PII Detection
# ─────────────────────────────────────────────────────────────

def detect_pii(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Detect PII (Personally Identifiable Information) in columns.

    Uses two methods:
    1. Column name matching against known PII column names
    2. Content scanning using regex patterns

    Returns detected columns with confidence levels.
    """
    detected = []
    risk_level = "low"
    matches_found = 0

    for col in df.columns:
        col_lower = col.lower().replace("_", " ").replace("-", " ")
        col_matches = []

        # Method 1: Column name matching
        for pii_type, names in PII_COLUMN_NAMES.items():
            if any(name in col_lower for name in names):
                col_matches.append({
                    "pii_type": pii_type,
                    "method": "column_name",
                    "confidence": "high",
                    "description": PII_TYPE_DESCRIPTIONS.get(pii_type, pii_type),
                })
                break  # One name match per type

        # Method 2: Content scanning (sample up to 1000 non-null values)
        series = df[col].dropna().astype(str)
        if len(series) > 0:
            sample = series.head(1000)
            for pii_type, pattern in PII_PATTERNS.items():
                matches = sample.str.contains(pattern, regex=True, na=False)
                match_count = matches.sum()
                if match_count > 0:
                    match_pct = match_count / len(sample)
                    if match_pct > 0.5:
                        confidence = "high"
                    elif match_pct > 0.1:
                        confidence = "medium"
                    else:
                        confidence = "low"

                    col_matches.append({
                        "pii_type": pii_type,
                        "method": "content_scan",
                        "confidence": confidence,
                        "match_rate": round(match_pct, 3),
                        "sample_matches": sample[matches].head(3).tolist(),
                        "description": PII_TYPE_DESCRIPTIONS.get(pii_type, pii_type),
                    })

        if col_matches:
            # Determine max confidence for this column
            confidences = [m["confidence"] for m in col_matches]
            max_conf = "high" if "high" in confidences else ("medium" if "medium" in confidences else "low")
            types = list(set(m["pii_type"] for m in col_matches))

            detected.append({
                "column": col,
                "dtype": str(df[col].dtype),
                "pii_types": types,
                "confidence": max_conf,
                "matches": col_matches,
                "risk": "high" if max_conf == "high" and "ssn" in types or "credit_card" in types
                         else ("medium" if max_conf == "high" else max_conf),
            })
            matches_found += 1

    # Overall risk level
    if any(d.get("risk") == "high" for d in detected):
        risk_level = "high"
    elif any(d.get("risk") == "medium" for d in detected):
        risk_level = "medium"
    elif matches_found > 0:
        risk_level = "low"

    recommendations = []
    if risk_level == "high":
        recommendations.append("Sensitive PII detected (SSN, credit card). Consider anonymization or secure storage.")
    if any(d.get("risk") in ("high", "medium") for d in detected):
        recommendations.append("Review PII handling policies for detected columns.")
    if not recommendations:
        recommendations.append("No significant PII detected.")

    return {
        "columns_detected": detected,
        "total_detected": matches_found,
        "total_columns": len(df.columns),
        "risk_level": risk_level,
        "recommendations": recommendations,
    }


# ─────────────────────────────────────────────────────────────
# Helper Functions
# ─────────────────────────────────────────────────────────────

def _avg_completeness(df: pd.DataFrame) -> float:
    """Average completeness across all columns."""
    return (1 - df.isnull().mean()).mean() * 100


def _consistency_score(df: pd.DataFrame, column_scores: List[Dict]) -> float:
    """Compute consistency dimension score."""
    # Count issues per severity
    all_issues = [i for cs in column_scores for i in cs.get("issues", [])]
    high = sum(1 for i in all_issues if i.get("severity") == "high")
    medium = sum(1 for i in all_issues if i.get("severity") == "medium")
    return max(0, 100 - high * 15 - medium * 5)


def _validity_score(df: pd.DataFrame, column_scores: List[Dict]) -> float:
    """Compute validity dimension score based on type consistency."""
    score = 100.0
    for cs in column_scores:
        if cs["score"] < 50:
            score -= 5
    return max(0, score)


def _timeliness_score(df: pd.DataFrame) -> float:
    """Compute timeliness score based on date columns."""
    date_cols = df.select_dtypes(include=["datetime64"]).columns
    if len(date_cols) == 0:
        return 50.0  # Neutral score when no date data

    # Check recency
    now = pd.Timestamp.now()
    scores = []
    for col in date_cols:
        series = df[col].dropna()
        if len(series) > 0:
            latest = series.max()
            days_ago = (now - latest).days
            if days_ago < 7:
                scores.append(100)
            elif days_ago < 30:
                scores.append(80)
            elif days_ago < 90:
                scores.append(60)
            elif days_ago < 365:
                scores.append(40)
            else:
                scores.append(20)

    return float(np.mean(scores)) if scores else 50.0
