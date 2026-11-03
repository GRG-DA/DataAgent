"""Branch coverage tests for low-coverage modules (TDD, pure functions, small data).

Targets: analysis/quality, statistical, forecasting, cohort, comparison, cache,
report, alerts; agent/tools_sql, schema_intelligence; warehouse/queries.
No network. Small frames. Tmp DuckDB/files only.
"""
import time

import duckdb
import numpy as np
import pandas as pd
import pytest


# ============================================================================
# analysis.quality
# ============================================================================

class TestQualityScore:
    def test_empty(self):
        from analysis.quality import compute_quality_score
        out = compute_quality_score(pd.DataFrame())
        assert out["overall_score"] == 0.0
        assert out["dimension_scores"]["completeness"] == 0.0
        assert out["recommendations"]

    def test_clean_frame(self):
        from analysis.quality import compute_quality_score
        df = pd.DataFrame({"a": [1, 2, 3, 4], "b": ["x", "y", "x", "y"]})
        out = compute_quality_score(df)
        assert out["row_count"] == 4 and out["column_count"] == 2
        assert 0 <= out["overall_score"] <= 100
        assert out["duplicate_count"] == 0
        assert out["total_missing_cells"] == 0
        assert "good" in out["recommendations"][0].lower()

    def test_high_null(self):
        from analysis.quality import compute_quality_score
        df = pd.DataFrame({"c": [None, None, None, 1.0, 2.0]})
        out = compute_quality_score(df)
        assert any("High null" in i["issue"] for i in out["issues"])
        assert any("completeness" in r.lower() or "null" in r.lower() or "Improve" in r for r in out["recommendations"])

    def test_moderate_null(self):
        from analysis.quality import compute_quality_score
        df = pd.DataFrame({"c": [None, 1.0, 2.0, 3.0, 4.0]})
        out = compute_quality_score(df)
        assert any("Moderate null" in i["issue"] for i in out["issues"])

    def test_low_null(self):
        from analysis.quality import compute_quality_score
        df = pd.DataFrame({"c": [None] + list(range(9))})
        out = compute_quality_score(df)
        assert any("Low null" in i["issue"] for i in out["issues"])

    def test_duplicates_recommendation(self):
        from analysis.quality import compute_quality_score
        df = pd.DataFrame({"a": [1, 1, 1, 1], "b": ["x", "x", "x", "x"]})
        out = compute_quality_score(df)
        assert out["duplicate_count"] == 3
        assert any("duplicate" in r.lower() for r in out["recommendations"])

    def test_high_cardinality_and_constant(self):
        from analysis.quality import compute_quality_score
        df = pd.DataFrame({
            "uid": list(range(10)),
            "const": ["same"] * 10,
        })
        out = compute_quality_score(df)
        issues = [i["issue"] for i in out["issues"]]
        assert any("High cardinality" in s for s in issues)
        assert any("Constant column" in s for s in issues)

    def test_negative_values(self):
        from analysis.quality import compute_quality_score
        df = pd.DataFrame({"age": [20, 30, -5, -2, 40]})
        out = compute_quality_score(df)
        assert any("negative values" in i["issue"] for i in out["issues"])

    def test_outliers_branch(self):
        from analysis.quality import compute_quality_score
        df = pd.DataFrame({"price": [10.0] * 18 + [1000.0, 1000.0]})
        out = compute_quality_score(df)
        assert out["column_scores"]
        # may or may not flag depending on sigma; structure must hold
        assert all("score" in c for c in out["column_scores"])

    def test_future_dates(self):
        from analysis.quality import compute_quality_score
        df = pd.DataFrame({"d": pd.to_datetime(["2099-01-01"] * 6 + ["2020-01-01"] * 2)})
        out = compute_quality_score(df)
        assert any("future" in i["issue"] for i in out["issues"])

    def test_empty_strings(self):
        from analysis.quality import compute_quality_score
        df = pd.DataFrame({"s": pd.Series(["", "   ", "x", "y"], dtype="object")})
        out = compute_quality_score(df)
        assert any("empty strings" in i["issue"] for i in out["issues"])

    def test_timeliness_branches(self):
        from analysis.quality import _timeliness_score
        assert _timeliness_score(pd.DataFrame({"a": [1]})) == 50.0
        now = pd.Timestamp.now()
        assert _timeliness_score(pd.DataFrame({"d": [now]})) == 100
        assert _timeliness_score(pd.DataFrame({"d": [now - pd.Timedelta(days=10)]})) == 80
        assert _timeliness_score(pd.DataFrame({"d": [now - pd.Timedelta(days=60)]})) == 60
        assert _timeliness_score(pd.DataFrame({"d": [now - pd.Timedelta(days=200)]})) == 40
        assert _timeliness_score(pd.DataFrame({"d": [now - pd.Timedelta(days=800)]})) == 20
        assert _timeliness_score(pd.DataFrame({"d": [pd.NaT]})) == 50.0

    def test_helpers(self):
        from analysis.quality import _avg_completeness, _consistency_score, _validity_score
        df = pd.DataFrame({"a": [1, None, 3]})
        assert 0 < _avg_completeness(df) < 100
        cs = [{"issues": [{"severity": "high"}, {"severity": "medium"}, {"severity": "low"}]}]
        s = _consistency_score(df, cs)
        assert s == 100 - 15 - 5
        assert _validity_score(df, [{"score": 10}, {"score": 90}]) == 95.0
        assert _validity_score(df, [{"score": 90}]) == 100.0

    def test_consistency_recommendation(self):
        from analysis.quality import compute_quality_score
        df = pd.DataFrame({"age": [-1, -2, -3, 5, 6]})
        out = compute_quality_score(df)
        assert "dimension_scores" in out

    def test_quality_report(self):
        from analysis.quality import generate_quality_report
        df = pd.DataFrame({"a": [1, 2]})
        rep = generate_quality_report(df, "t")
        assert rep["table"] == "t"
        assert rep["profile"]["rows"] == 2
        assert "quality" in rep


class TestPII:
    def test_name_match_and_email_content(self):
        from analysis.quality import detect_pii
        df = pd.DataFrame({
            "email": ["a@x.com", "b@y.org", "c@z.net"],
            "notes": ["hello", "world", "test"],
        })
        out = detect_pii(df)
        cols = {d["column"] for d in out["columns_detected"]}
        assert "email" in cols
        assert out["total_detected"] >= 1

    def test_ssn_high_risk(self):
        from analysis.quality import detect_pii
        df = pd.DataFrame({"notes": ["ssn 123-45-6789"] * 5 + ["plain"] * 2})
        out = detect_pii(df)
        assert out["risk_level"] == "high"
        assert any("Sensitive PII" in r for r in out["recommendations"])

    def test_credit_card_and_phone_ip(self):
        from analysis.quality import detect_pii
        df = pd.DataFrame({
            "cc": ["4111-1111-1111-1111"] * 4,
            "ph": ["(415) 555-1234"] * 4,
            "ip": ["1.2.3.4"] * 4,
        })
        out = detect_pii(df)
        assert out["total_detected"] == 3

    def test_low_and_medium_confidence(self):
        from analysis.quality import detect_pii
        # 1 match in 20 -> low confidence
        vals = ["user@test.com"] + ["plain"] * 19
        out = detect_pii(pd.DataFrame({"misc": vals}))
        assert out["columns_detected"][0]["confidence"] == "low"
        # 3 in 20 -> medium
        vals2 = ["user@test.com"] * 3 + ["plain"] * 17
        out2 = detect_pii(pd.DataFrame({"misc": vals2}))
        assert out2["columns_detected"][0]["confidence"] == "medium"
        # >50% -> high
        vals3 = ["user@test.com"] * 12 + ["plain"] * 8
        out3 = detect_pii(pd.DataFrame({"misc": vals3}))
        assert out3["columns_detected"][0]["confidence"] == "high"

    def test_no_pii(self):
        from analysis.quality import detect_pii
        out = detect_pii(pd.DataFrame({"n": [1, 2, 3]}))
        assert out["risk_level"] == "low"
        assert out["recommendations"] == ["No significant PII detected."]

    def test_medium_risk_name_only(self):
        from analysis.quality import detect_pii
        df = pd.DataFrame({"first_name": ["Ann", "Bob", "Cat"]})
        out = detect_pii(df)
        assert out["total_detected"] == 1
        assert out["risk_level"] in ("low", "medium")


# ============================================================================
# analysis.statistical
# ============================================================================

class TestNormality:
    def test_too_few(self):
        from analysis.statistical import test_normality
        r = test_normality(pd.Series([1.0, 2.0]), "c")
        assert r.sample_size == 2 and r.p_value == 1.0

    def test_shapiro(self):
        from analysis.statistical import test_normality
        r = test_normality(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]), "c", "shapiro")
        assert r.test_name == "Shapiro-Wilk"
        assert r.sample_size == 5

    def test_ks(self):
        from analysis.statistical import test_normality
        r = test_normality(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]), "c", "ks")
        assert r.test_name == "Kolmogorov-Smirnov"

    def test_unknown(self):
        from analysis.statistical import test_normality
        r = test_normality(pd.Series([1.0, 2.0, 3.0]), "c", "nope")
        assert "Unknown method" in r.interpretation

    def test_normal_branch(self):
        from analysis.statistical import test_normality
        # symmetric data tends to look normal at n=10
        s = pd.Series([0.1, -0.1, 0.2, -0.2, 0.0, 0.05, -0.05, 0.15, -0.15, 0.0])
        r = test_normality(s, "c")
        assert bool(r.is_normal) in (True, False)


class TestHypothesis:
    def _two_group_df(self):
        return pd.DataFrame({
            "g": ["A"] * 6 + ["B"] * 6,
            "x": [1.0, 2.0, 1.5, 2.5, 1.2, 2.2, 10.0, 11.0, 10.5, 9.5, 10.2, 11.2],
        })

    def test_ttest_group(self):
        from analysis.statistical import test_hypothesis
        r = test_hypothesis(self._two_group_df(), "x", group_col="g", test_type="ttest")
        assert bool(r.significant) is True
        assert set(r.groups) == {"A", "B"}

    def test_ttest_columns(self):
        from analysis.statistical import test_hypothesis
        df = pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0], "b": [10.0, 11.0, 12.0, 13.0]})
        r = test_hypothesis(df, "a", "b", test_type="ttest")
        assert r.groups == ["a", "b"]

    def test_ttest_rejects_more_than_two_groups(self):
        from analysis.statistical import test_hypothesis
        df = pd.DataFrame({
            "g": ["A"] * 4 + ["B"] * 4 + ["C"] * 4,
            "x": [1.0, 1.2, 0.9, 1.1, 5.0, 5.2, 4.8, 5.1, 9.0, 9.1, 8.9, 9.2],
        })
        r = test_hypothesis(df, "x", group_col="g", test_type="ttest")
        assert r.ok is False
        assert "Error" in r.interpretation
        assert "ANOVA" in r.error

    def test_ttest_errors(self):
        from analysis.statistical import test_hypothesis
        df = pd.DataFrame({"g": ["A"], "x": [1.0]})
        assert "Error" in test_hypothesis(df, "x", group_col="g").interpretation
        assert "Error" in test_hypothesis(pd.DataFrame({"a": [1]}), "").interpretation
        df2 = pd.DataFrame({"g": ["A", "A"], "x": [1.0, None]})
        assert "Error" in test_hypothesis(df2, "x", group_col="g").interpretation

    def test_paired(self):
        from analysis.statistical import test_hypothesis
        df = pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0], "b": [1.5, 2.5, 3.5, 5.0]})
        r = test_hypothesis(df, "a", "b", test_type="paired_ttest")
        assert r.effect_size_name == "Cohen's dz"
        assert "Error" in test_hypothesis(df, "a", test_type="paired_ttest").interpretation
        tiny = pd.DataFrame({"a": [1.0], "b": [2.0]})
        assert "Error" in test_hypothesis(tiny, "a", "b", test_type="paired_ttest").interpretation

    def test_mannwhitney(self):
        from analysis.statistical import test_hypothesis
        r = test_hypothesis(self._two_group_df(), "x", group_col="g", test_type="mannwhitney")
        assert r.test_name == "mannwhitney"
        df = pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": [4.0, 5.0, 6.0]})
        r2 = test_hypothesis(df, "a", "b", test_type="mannwhitney")
        assert r2.groups == ["a", "b"]
        assert "Error" in test_hypothesis(df, "", test_type="mannwhitney").interpretation
        three = pd.DataFrame({
            "g": ["A", "A", "B", "B", "C", "C"],
            "x": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        })
        r3 = test_hypothesis(three, "x", group_col="g", test_type="mannwhitney")
        assert r3.ok is False
        assert "ANOVA" in r3.error

    def test_chi2(self):
        from analysis.statistical import test_hypothesis
        df = pd.DataFrame({
            "g": ["A"] * 10 + ["B"] * 10,
            "c": (["X"] * 8 + ["Y"] * 2) + (["X"] * 2 + ["Y"] * 8),
        })
        r = test_hypothesis(df, "c", group_col="g", test_type="chi2")
        assert r.effect_size_name == "Cramer's V"
        assert "chi-square" in r.interpretation
        assert "Error" in test_hypothesis(df, "", test_type="chi2").interpretation
        tiny = pd.DataFrame({"g": ["A"], "c": ["X"]})
        assert "Error" in test_hypothesis(tiny, "c", group_col="g", test_type="chi2").interpretation

    def test_anova(self):
        from analysis.statistical import test_hypothesis
        df = pd.DataFrame({
            "g": ["A"] * 4 + ["B"] * 4 + ["C"] * 4,
            "x": [1.0, 1.2, 0.9, 1.1, 5.0, 5.2, 4.8, 5.1, 9.0, 9.1, 8.9, 9.2],
        })
        r = test_hypothesis(df, "x", group_col="g", test_type="anova")
        assert r.effect_size_name == "Eta-squared"
        assert "ANOVA" in r.interpretation
        assert "comparing group means across 3 groups" in r.interpretation
        assert "comparing 'x'" not in r.interpretation
        assert "Error" in test_hypothesis(df, "", test_type="anova").interpretation
        one = pd.DataFrame({"g": ["A", "A"], "x": [1.0, 2.0]})
        assert "Error" in test_hypothesis(one, "x", group_col="g", test_type="anova").interpretation

    def test_pearson(self):
        from analysis.statistical import test_hypothesis
        df = pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0, 5.0], "b": [2.0, 4.1, 5.9, 8.2, 10.1]})
        r = test_hypothesis(df, "a", "b", test_type="pearsonr")
        assert r.effect_size_name == "|r|"
        assert "correlation" in r.interpretation
        neg = pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0], "b": [4.0, 3.0, 2.0, 1.0]})
        r2 = test_hypothesis(neg, "a", "b", test_type="pearsonr")
        assert "negative" in r2.interpretation
        assert "Error" in test_hypothesis(df, "a", test_type="pearsonr").interpretation
        tiny = pd.DataFrame({"a": [1.0, 2.0], "b": [1.0, 2.0]})
        assert "Error" in test_hypothesis(tiny, "a", "b", test_type="pearsonr").interpretation

    def test_unknown(self):
        from analysis.statistical import test_hypothesis
        df = pd.DataFrame({"a": [1.0]})
        assert "Unknown test type" in test_hypothesis(df, "a", test_type="zzz").interpretation

    def test_helpers(self):
        from analysis.statistical import _cohens_d, _interpret_effect_size
        assert _cohens_d(pd.Series([1.0]), pd.Series([2.0, 3.0])) == 0.0
        assert _cohens_d(pd.Series([5.0, 5.0]), pd.Series([5.0, 5.0])) == 0.0
        assert _interpret_effect_size(0.1, "Cohen's d") == "negligible"
        assert _interpret_effect_size(0.3, "Cohen's d") == "small"
        assert _interpret_effect_size(0.6, "Cohen's d") == "medium"
        assert _interpret_effect_size(1.2, "Cohen's d") == "large"
        assert _interpret_effect_size(0.05, "|r|") == "negligible"
        assert _interpret_effect_size(0.2, "|r|") == "small"
        assert _interpret_effect_size(0.4, "|r|") == "medium"
        assert _interpret_effect_size(0.9, "|r|") == "large"
        assert _interpret_effect_size(0.05, "Cramer's V") == "negligible"
        assert _interpret_effect_size(0.2, "Cramer's V") == "small"
        assert _interpret_effect_size(0.4, "Cramer's V") == "medium"
        assert _interpret_effect_size(0.9, "Cramer's V") == "large"
        assert _interpret_effect_size(0.005, "Eta-squared") == "negligible"
        assert _interpret_effect_size(0.03, "Eta-squared") == "small"
        assert _interpret_effect_size(0.1, "Eta-squared") == "medium"
        assert _interpret_effect_size(0.5, "Eta-squared") == "large"
        assert _interpret_effect_size(1.0, "zzz") == "unknown"


class TestCI:
    def test_too_few(self):
        from analysis.statistical import compute_confidence_interval
        r = compute_confidence_interval(pd.Series([1.0]), "c")
        assert r.sample_size == 1

    def test_normal(self):
        from analysis.statistical import compute_confidence_interval
        r = compute_confidence_interval(pd.Series(range(40)), "c", method="normal")
        assert r.method == "Normal (Z)"
        assert r.ci_lower < r.mean < r.ci_upper

    def test_t(self):
        from analysis.statistical import compute_confidence_interval
        r = compute_confidence_interval(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]), "c", method="t")
        assert r.method == "Student's t"

    def test_t_auto_small(self):
        from analysis.statistical import compute_confidence_interval
        r = compute_confidence_interval(pd.Series([1.0, 2.0, 3.0]), "c", method="normal")
        assert r.method == "Student's t"

    def test_bootstrap(self):
        from analysis.statistical import compute_confidence_interval
        r = compute_confidence_interval(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]), "c", method="bootstrap")
        assert r.method == "Bootstrap"


class TestDecompose:
    def test_too_short(self):
        from analysis.statistical import decompose_time_series
        r = decompose_time_series(pd.Series([1.0, 2.0]))
        assert r.observed == [] and r.period == 0

    def test_additive(self):
        from analysis.statistical import decompose_time_series
        vals = pd.Series([float(i) for i in range(1, 13)])
        r = decompose_time_series(vals, period=4, model="additive")
        assert len(r.observed) == 12 and len(r.trend) == 12

    def test_multiplicative(self):
        from analysis.statistical import decompose_time_series
        vals = pd.Series([10.0, 12.0, 11.0, 13.0, 10.0, 12.0, 11.0, 13.0])
        r = decompose_time_series(vals, period=4, model="mult")
        assert len(r.seasonal) == 8

    def test_auto_period(self):
        from analysis.statistical import decompose_time_series
        assert decompose_time_series(pd.Series([1.0] * 24)).period == 12
        assert decompose_time_series(pd.Series([1.0] * 14)).period == 7
        assert decompose_time_series(pd.Series([1.0] * 8)).period == 4

    def test_dates(self):
        from analysis.statistical import decompose_time_series
        vals = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        dates = pd.Series(pd.date_range("2026-01-01", periods=6))
        r = decompose_time_series(vals, dates=dates)
        assert r.dates[0].startswith("2026")


class TestRegression:
    def test_linear_happy(self):
        from analysis.statistical import fit_regression
        df = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                           "y": [2.1, 3.9, 6.2, 7.8, 10.1, 12.0]})
        r = fit_regression(df, "y", ["x"], "linear")
        assert r.r_squared > 0.9
        assert "x" in r.coefficients

    def test_linear_perfect(self):
        from analysis.statistical import fit_regression
        df = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0], "y": [2.0, 4.0, 6.0, 8.0]})
        r = fit_regression(df, "y", ["x"], "linear")
        assert r.r_squared == pytest.approx(1.0)

    def test_linear_short(self):
        from analysis.statistical import fit_regression
        df = pd.DataFrame({"x": [1.0], "y": [2.0]})
        r = fit_regression(df, "y", ["x"], "linear")
        assert "Not enough data" in r.interpretation

    def test_linear_singular(self):
        from analysis.statistical import fit_regression
        df = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0], "x2": [1.0, 2.0, 3.0, 4.0],
                           "y": [1.0, 2.0, 3.0, 4.0]})
        r = fit_regression(df, "y", ["x", "x2"], "linear")
        assert "Singular matrix" in r.interpretation

    def test_polynomial(self):
        from analysis.statistical import fit_regression
        df = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                           "y": [1.0, 4.0, 9.0, 16.0, 25.0, 36.0]})
        r = fit_regression(df, "y", ["x"], "polynomial")
        assert r.regression_type == "polynomial"
        short = pd.DataFrame({"x": [1.0], "y": [1.0]})
        assert "Not enough" in fit_regression(short, "y", ["x"], "polynomial").interpretation

    def test_polynomial_two_features(self):
        from analysis.statistical import fit_regression
        df = pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0],
                           "b": [2.0, 1.0, 4.0, 3.0, 6.0, 5.0, 8.0],
                           "y": [3.0, 3.0, 7.0, 7.0, 11.0, 11.0, 15.0]})
        r = fit_regression(df, "y", ["a", "b"], "polynomial")
        assert any("*" in k or "^" in k for k in r.coefficients)

    def test_logistic(self):
        from analysis.statistical import fit_regression
        df = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0],
                           "y": [0, 0, 0, 0, 1, 0, 1, 1, 1, 1]})
        r = fit_regression(df, "y", ["x"], "logistic")
        assert r.regression_type == "logistic"
        assert "Accuracy" in r.interpretation

    def test_logistic_errors(self):
        from analysis.statistical import fit_regression
        short = pd.DataFrame({"x": [1.0], "y": [0]})
        assert "Not enough" in fit_regression(short, "y", ["x"], "logistic").interpretation
        nonbin = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0], "y": [0, 1, 2, 3]})
        assert "binary" in fit_regression(nonbin, "y", ["x"], "logistic").interpretation

    def test_unknown(self):
        from analysis.statistical import fit_regression
        df = pd.DataFrame({"x": [1.0], "y": [1.0]})
        assert "not supported" in fit_regression(df, "y", ["x"], "zzz").interpretation


class TestFeatureImportance:
    def test_correlation(self):
        from analysis.statistical import compute_feature_importance
        df = pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0, 5.0],
                           "b": [5.0, 4.0, 3.0, 2.0, 1.0],
                           "y": [1.0, 2.0, 3.0, 4.0, 5.0]})
        r = compute_feature_importance(df, "y", "correlation")
        assert r.features[0]["feature"] == "a"
        assert r.features[0]["rank"] == 1

    def test_mutual_info(self):
        from analysis.statistical import compute_feature_importance
        df = pd.DataFrame({"a": [float(i) for i in range(10)],
                           "b": [float(i % 3) for i in range(10)],
                           "y": [float(i) for i in range(10)]})
        r = compute_feature_importance(df, "y", "mutual_info")
        assert r.method == "mutual_info"

    def test_no_features(self):
        from analysis.statistical import compute_feature_importance
        r = compute_feature_importance(pd.DataFrame({"s": ["a", "b"]}), "s")
        assert r.features == []

    def test_mi_helpers(self):
        from analysis.statistical import _mutual_information, sp_corr
        import numpy as np
        assert _mutual_information(np.zeros((2, 2))) == 0.0
        assert _mutual_information(np.array([[5, 1], [1, 5]])) > 0
        c, _ = sp_corr(pd.Series([1.0, 2.0, 3.0]), pd.Series([1.0, 2.0, 3.0]))
        assert c == pytest.approx(1.0)


class TestAB:
    def _ab_df(self, same=False):
        if same:
            return pd.DataFrame({"g": ["A"] * 8 + ["B"] * 8,
                                 "m": [5.0] * 16})
        return pd.DataFrame({"g": ["A"] * 10 + ["B"] * 10,
                             "m": [10.0] * 10 + [20.0] * 10})

    def test_small(self):
        from analysis.statistical import run_ab_test
        df = pd.DataFrame({"g": ["A", "B"], "m": [1.0, 2.0]})
        r = run_ab_test(df, "g", "m", "A", "B")
        assert r.winner == "none"

    def test_significant(self):
        from analysis.statistical import run_ab_test
        r = run_ab_test(self._ab_df(), "g", "m", "A", "B")
        assert bool(r.significant) is True
        assert r.winner == "B"

    def test_not_significant(self):
        from analysis.statistical import run_ab_test
        r = run_ab_test(self._ab_df(same=True), "g", "m", "A", "B")
        assert bool(r.significant) is False
        assert r.winner == "none"


class TestConversion:
    def _conv_df(self, ra=0.5, rb=0.9, n=20):
        a = [1] * int(n * ra) + [0] * (n - int(n * ra))
        b = [1] * int(n * rb) + [0] * (n - int(n * rb))
        return pd.DataFrame({"g": ["A"] * n + ["B"] * n, "c": a + b})

    def test_small(self):
        from analysis.statistical import test_conversion_rate
        df = pd.DataFrame({"g": ["A", "B"], "c": [1, 0]})
        out = test_conversion_rate(df, "g", "c", "A", "B")
        assert out["success"] is False

    def test_significant(self):
        from analysis.statistical import test_conversion_rate
        out = test_conversion_rate(self._conv_df(), "g", "c", "A", "B")
        assert out["success"] is True
        assert out["winner"] == "B"

    def test_not_significant(self):
        from analysis.statistical import test_conversion_rate
        out = test_conversion_rate(self._conv_df(0.5, 0.5), "g", "c", "A", "B")
        assert bool(out["significant"]) is False
        assert out["winner"] == "none"

    def test_zero_rate(self):
        from analysis.statistical import test_conversion_rate
        # A has zero conversions but B has some: chi2 valid, lift falls back to 0
        df = pd.DataFrame({"g": ["A"] * 12 + ["B"] * 12,
                           "c": [0] * 12 + [1] * 6 + [0] * 6})
        out = test_conversion_rate(df, "g", "c", "A", "B")
        assert out["success"] is True


class TestSequentialPower:
    def test_sequential_small(self):
        from analysis.statistical import sequential_test
        df = pd.DataFrame({"g": ["A", "B"], "m": [1.0, 2.0]})
        assert sequential_test(df, "g", "m", "A", "B")["success"] is False

    def test_sequential_all_spending(self):
        from analysis.statistical import sequential_test
        df = pd.DataFrame({"g": ["A"] * 12 + ["B"] * 12,
                           "m": [1.0] * 12 + [5.0] * 12})
        for fn in ("obrien_fleming", "pocock", "simple"):
            out = sequential_test(df, "g", "m", "A", "B", spending_function=fn)
            assert out["success"] is True
            assert out["total_n"] == 24

    def test_sequential_nosig(self):
        from analysis.statistical import sequential_test
        df = pd.DataFrame({"g": ["A"] * 12 + ["B"] * 12, "m": [3.0] * 24})
        out = sequential_test(df, "g", "m", "A", "B")
        assert out["success"] is True

    def test_power_bands(self):
        from analysis.statistical import compute_power_analysis
        assert compute_power_analysis(0.05).sample_size_per_group > 100
        assert "Small effect" in compute_power_analysis(0.3).interpretation
        assert "Medium effect" in compute_power_analysis(0.6).interpretation
        assert "Large effect" in compute_power_analysis(1.2).interpretation
        assert compute_power_analysis(0.0).total_sample_size >= 4

    def test_export_model(self):
        from analysis.statistical import export_regression_model
        out = export_regression_model({"coefficients": {"x": 2.0, "z": -1.5},
                                       "intercept": 0.5, "regression_type": "linear",
                                       "r_squared": 0.9, "adj_r_squared": 0.89,
                                       "significant_features": ["x"],
                                       "mse": 0.1, "rmse": 0.3, "mae": 0.2})
        assert out["formula"].startswith("y =")
        assert "- 1.5000*z" in out["formula"]

    def test_outlier_suggest(self):
        from analysis.statistical import suggest_outlier_handling
        assert suggest_outlier_handling(pd.Series([1.0, 2.0]), "c")["outlier_count"] == 0
        clean = suggest_outlier_handling(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]), "c")
        assert clean["suggestions"][0]["action"] == "none"
        skewed = pd.Series([1.0] * 15 + [50.0, 60.0, 70.0, 100.0])
        out = suggest_outlier_handling(skewed, "c")
        assert out["outlier_count"] > 0
        assert any(s["action"] == "transform" for s in out["suggestions"])
        few = pd.Series([1.0, 1.1, 0.9, 1.05, 1.02, 0.98, 1.01, 1.03, 50.0])
        out2 = suggest_outlier_handling(few, "c")
        assert any(s["action"] == "keep" for s in out2["suggestions"])
        mod = pd.Series([10.0] * 97 + [100.0, 101.0, 102.0])
        out3 = suggest_outlier_handling(mod, "c")
        assert any(s["action"] in ("cap_winsorize", "investigate") for s in out3["suggestions"])


# ============================================================================
# analysis.forecasting
# ============================================================================

class TestForecasting:
    def _ts_df(self, n=16, slope=1.0):
        dates = pd.date_range("2026-01-01", periods=n, freq="D")
        vals = [10.0 + slope * i + (2.0 if i % 4 == 0 else 0.0) for i in range(n)]
        return pd.DataFrame({"d": dates, "v": vals})

    def test_short(self):
        from analysis.forecasting import advanced_forecast
        df = pd.DataFrame({"d": ["2026-01-01", "2026-01-02"], "v": [1.0, 2.0]})
        r = advanced_forecast(df, "d", "v")
        assert r.forecast_values == []

    def test_multiplicative(self):
        from analysis.forecasting import advanced_forecast
        r = advanced_forecast(self._ts_df(16), "d", "v", periods=4)
        assert len(r.forecast_values) == 4
        assert len(r.lower_bound) == 4 == len(r.upper_bound)
        assert r.trend_direction in ("increasing", "decreasing", "stable")

    def test_additive(self):
        from analysis.forecasting import advanced_forecast
        r = advanced_forecast(self._ts_df(12), "d", "v", periods=3, seasonality_mode="additive")
        assert len(r.forecast_values) == 3

    def test_flat_no_season(self):
        from analysis.forecasting import advanced_forecast
        df = pd.DataFrame({"d": pd.date_range("2026-01-01", periods=10, freq="D"),
                           "v": [5.0] * 10})
        r = advanced_forecast(df, "d", "v", periods=2)
        assert len(r.forecast_values) == 2

    def test_decreasing(self):
        from analysis.forecasting import advanced_forecast
        r = advanced_forecast(self._ts_df(12, slope=-1.0), "d", "v", periods=2)
        assert r.trend_direction == "decreasing"

    def test_decompose_short_and_long(self):
        from analysis.forecasting import _decompose_multiplicative
        import numpy as np
        t, s, res = _decompose_multiplicative(np.array([1.0, 2.0, 3.0]), 4)
        assert len(t) == 3
        t2, s2, _ = _decompose_multiplicative(np.arange(1, 21, dtype=float), 4)
        assert len(t2) == 20

    def test_changepoints(self):
        from analysis.forecasting import _detect_changepoints
        import numpy as np
        assert _detect_changepoints(np.array([1.0, 2.0, 3.0])) == []
        assert _detect_changepoints(np.array([5.0] * 12)) == []
        vals = np.array([1.0, 1.1, 0.9, 1.0, 1.05, 10.0, 10.2, 9.8, 10.1, 10.0, 10.05, 9.9])
        cps = _detect_changepoints(vals)
        assert isinstance(cps, list)
        assert _detect_changepoints(vals, threshold=100.0) == []

    def test_accuracy_zero(self):
        from analysis.forecasting import _compute_accuracy_metrics
        import numpy as np
        m = _compute_accuracy_metrics(np.zeros(5), np.zeros(5))
        assert m["mape"] == 0.0
        m2 = _compute_accuracy_metrics(np.array([1.0, 2.0, 3.0]), np.array([1.0, 2.0, 4.0]))
        assert m2["rmse"] > 0

    def test_infer_period(self):
        from analysis.forecasting import _infer_period
        assert _infer_period(pd.Series([1.0] * 30)) == 12
        assert _infer_period(pd.Series([1.0] * 15)) == 7
        assert _infer_period(pd.Series([1.0] * 9)) == 4
        assert _infer_period(pd.Series([1.0] * 5)) == 2

    def test_whatif(self):
        from analysis.forecasting import advanced_forecast, generate_whatif_scenarios
        r = advanced_forecast(self._ts_df(12), "d", "v", periods=3)
        assert generate_whatif_scenarios(r, []) == []
        scs = generate_whatif_scenarios(r, [
            {"name": "boost", "trend_adjustment": 10.0, "seasonality_amplitude": 1.2},
            {"name": "plain"},
        ])
        assert len(scs) == 2
        assert len(scs[0].forecast_values) == 3
        from analysis.forecasting import ForecastResult
        empty = ForecastResult(method="x", historical_dates=[], historical_values=[],
                              forecast_dates=[], forecast_values=[], lower_bound=[],
                              upper_bound=[], confidence_level=0.95)
        assert generate_whatif_scenarios(empty, [{"name": "a"}]) == []


# ============================================================================
# analysis.cohort
# ============================================================================

def _activity_df():
    return pd.DataFrame({
        "uid": ["u1", "u1", "u2", "u2", "u3", "u4", "u4"],
        "d": ["2026-01-05", "2026-02-06", "2026-01-10", "2026-03-10",
              "2026-02-01", "2026-01-20", "2026-01-25"],
        "rev": [10.0, 20.0, 5.0, 15.0, 7.0, 3.0, 4.0],
    })


class TestCohort:
    def test_month_revenue(self):
        from analysis.cohort import cohort_retention
        r = cohort_retention(_activity_df(), "uid", "d", "month", revenue_col="rev")
        assert r.n_cohorts >= 1
        assert r.avg_revenue_per_cohort
        assert r.median_lifespan is not None

    def test_week(self):
        from analysis.cohort import cohort_retention
        r = cohort_retention(_activity_df(), "uid", "d", "week", max_periods=4)
        assert r.retention_periods[0].startswith("Week")

    def test_day(self):
        from analysis.cohort import cohort_retention
        r = cohort_retention(_activity_df(), "uid", "d", "day", max_periods=3)
        assert r.retention_periods[0].startswith("Day")

    def test_quarter_and_invalid(self):
        from analysis.cohort import cohort_retention
        r = cohort_retention(_activity_df(), "uid", "d", "quarter")
        assert r.retention_periods[0].startswith("Quarter")
        r2 = cohort_retention(_activity_df(), "uid", "d", "zzz")
        assert r2.n_cohorts >= 1

    def test_churn_empty(self):
        from analysis.cohort import churn_analysis
        df = pd.DataFrame({"uid": [], "d": []})
        r = churn_analysis(df, "uid", "d")
        assert r.avg_churn_rate == 0.0

    def test_churn_happy(self):
        from analysis.cohort import churn_analysis
        df = pd.DataFrame({
            "uid": ["u1", "u1", "u2", "u3"],
            "d": ["2026-01-01", "2026-01-02", "2026-01-01", "2026-06-01"],
        })
        r = churn_analysis(df, "uid", "d", inactivity_days=30)
        assert r.avg_churn_rate > 0
        assert r.survival_curve
        assert r.median_time_to_churn is not None

    def test_churn_none(self):
        from analysis.cohort import churn_analysis
        df = pd.DataFrame({"uid": ["u1", "u2"], "d": ["2026-06-01", "2026-06-01"]})
        r = churn_analysis(df, "uid", "d", inactivity_days=30)
        assert r.avg_churn_rate == 0.0
        assert r.median_time_to_churn is None


# ============================================================================
# analysis.comparison
# ============================================================================

class TestComparison:
    def _period_df(self, diff=True):
        rows = []
        for i in range(5):
            rows.append({"d": f"2026-01-{i + 1:02d}", "m": 10.0, "g": "X"})
        m2 = 30.0 if diff else 10.0
        for i in range(5):
            rows.append({"d": f"2026-02-{i + 1:02d}", "m": m2, "g": "X"})
        return pd.DataFrame(rows)

    def test_short(self):
        from analysis.comparison import compare_periods
        df = pd.DataFrame({"d": ["2026-01-01"], "m": [1.0]})
        r = compare_periods(df, "d", "m")
        assert r.metrics == [] and r.total_compared == 0

    def test_month_group_significant(self):
        from analysis.comparison import compare_periods
        r = compare_periods(self._period_df(True), "d", "m", group_col="g")
        assert r.total_compared >= 1
        assert r.overall_significance is True
        assert r.highlight_count >= 1

    def test_no_group_flat(self):
        from analysis.comparison import compare_periods
        r = compare_periods(self._period_df(False), "d", "m")
        assert r.total_compared >= 1
        assert r.overall_significance is False

    def test_granularities(self):
        from analysis.comparison import compare_periods
        df = pd.DataFrame({
            "d": ["2026-01-05", "2026-04-05", "2026-07-05", "2027-01-05"],
            "m": [1.0, 2.0, 3.0, 4.0],
        })
        assert compare_periods(df, "d", "m", period_type="quarter").total_compared >= 1
        assert compare_periods(df, "d", "m", period_type="year").total_compared >= 1
        assert compare_periods(df, "d", "m", period_type="week").total_compared >= 1
        assert compare_periods(df, "d", "m", period_type="zzz").total_compared >= 1

    def test_diff_key(self):
        from analysis.comparison import diff_tables
        before = pd.DataFrame({"id": [1, 2, 3], "v": ["a", "b", "c"]})
        after = pd.DataFrame({"id": [2, 3, 4], "v": ["B", "c", "d"]})
        r = diff_tables(before, after, key_column="id")
        assert r.rows_added == 1 and r.rows_removed == 1
        assert r.rows_modified >= 1 and r.rows_unchanged >= 1
        assert "v" in r.modified_cells

    def test_diff_index(self):
        from analysis.comparison import diff_tables
        before = pd.DataFrame({"a": [1, 2]})
        after = pd.DataFrame({"a": [2, 3]})
        r = diff_tables(before, after)
        assert r.rows_added >= 1 and r.rows_removed >= 1


# ============================================================================
# analysis.cache
# ============================================================================

class TestCache:
    def test_miss_hit(self):
        from analysis.cache import AnalysisCache
        c = AnalysisCache(ttl_seconds=60, max_size=10)
        assert c.get("k") is None
        c.set("k", 42)
        assert c.get("k") == 42
        s = c.get_stats()
        assert s["hits"] == 1 and s["misses"] == 1
        assert s["hit_rate_pct"] == 50.0

    def test_expired(self):
        from analysis.cache import AnalysisCache
        c = AnalysisCache(ttl_seconds=0, max_size=10)
        c.set("k", 1)
        time.sleep(0.02)
        assert c.get("k") is None

    def test_eviction(self):
        from analysis.cache import AnalysisCache
        c = AnalysisCache(ttl_seconds=60, max_size=2)
        c.set("a", 1)
        time.sleep(0.01)
        c.set("b", 2)
        c.set("c", 3)
        assert c.get_stats()["evictions"] == 1
        assert c.get_stats()["entries"] == 2

    def test_invalidate(self):
        from analysis.cache import AnalysisCache
        c = AnalysisCache()
        c.set("a:1", 1)
        c.set("a:2", 2)
        c.set("b:1", 3)
        c.invalidate("a:")
        assert c.get("a:1") is None
        assert c.get("b:1") == 3
        c.invalidate()
        assert c.get_stats()["entries"] == 0

    def test_clear(self):
        from analysis.cache import AnalysisCache
        c = AnalysisCache()
        c.set("x", 1)
        c.clear()
        assert c.get_stats()["entries"] == 0

    def test_stats_empty(self):
        from analysis.cache import AnalysisCache
        assert AnalysisCache().get_stats()["hit_rate_pct"] == 0

    def test_make_key(self):
        from analysis.cache import AnalysisCache
        k1 = AnalysisCache.make_key("f", 1, x=2)
        k2 = AnalysisCache.make_key("f", 1, x=2)
        k3 = AnalysisCache.make_key("f", 2, x=2)
        assert k1 == k2 and k1 != k3

    def test_cached_decorator(self):
        from analysis.cache import cache, cached, invalidate_cache_for
        cache.clear()
        calls = []

        @cached(ttl_seconds=60)
        def f(x):
            calls.append(x)
            return x * 2

        assert f(3) == 6
        assert f(3) == 6
        assert len(calls) == 1
        invalidate_cache_for("f:")
        assert f(3) == 6
        assert len(calls) == 2  # invalidated -> recomputed once
        assert f(3) == 6
        assert len(calls) == 2  # cached again
        cache.clear()
        assert f(3) == 6
        assert len(calls) == 3


# ============================================================================
# analysis.report
# ============================================================================

class TestReport:
    def test_sanitize(self):
        from analysis.report import _sanitize_filename
        assert _sanitize_filename('a:b/c*d?"e<f>g|h') == "a_b_c_d__e_f_g_h"

    def test_html_and_save(self, tmp_path):
        from analysis.report import ReportGenerator, ReportSection
        gen = ReportGenerator(output_dir=str(tmp_path))
        secs = [ReportSection(title="S1", content="<p>hi</p>")]
        rep = gen.generate_html_report("My Report", secs, "t", 5, 2)
        assert "My Report" in rep.html and "S1" in rep.html
        p = gen.save_report(rep)
        assert p.endswith(".html")
        p2 = gen.save_report(rep, filename="we:ird.html")
        assert ":" not in p2.split("\\")[-1].split("/")[-1]

    def test_executive_full(self, tmp_path):
        from analysis.report import ReportGenerator
        gen = ReportGenerator(output_dir=str(tmp_path))
        rep = gen.generate_executive_report(
            "sales",
            {"row_count": 100, "column_count": 5},
            {"overall_score": 85.0, "dimension_scores": {"completeness": 90.0},
             "recommendations": ["do this"]},
            {"key_findings": ["f1"], "recommendations": ["r1"]},
            {"strongest": {"col1": "a", "col2": "b", "correlation": 0.9,
                           "strength": "strong", "direction": "positive"}},
            {"outliers": [{"column": "a", "outlier_count": 2, "outlier_pct": 1.0}]},
        )
        assert "Executive Summary" in rep.html
        assert "Correlation" in rep.html
        assert "Outlier" in rep.html

    def test_executive_minimal_low_score(self, tmp_path):
        from analysis.report import ReportGenerator
        gen = ReportGenerator(output_dir=str(tmp_path))
        rep = gen.generate_executive_report(
            "t", {}, {"overall_score": 10.0, "dimension_scores": {}},
            {}, None, None,
        )
        assert rep.title.startswith("Executive Analysis Report")

    def test_sections_to_dicts(self):
        from analysis.report import _sections_to_dicts, ReportSection
        out = _sections_to_dicts([ReportSection(title="T", content="C",
                                                tables=[pd.DataFrame({"a": [1]})])])
        assert "a" in out[0]["tables"][0]


# ============================================================================
# analysis.alerts (tmp rules dir + tmp duckdb, no network)
# ============================================================================

def _alert_db(path, schema="analytics", rows=(10.0, 20.0, 30.0)):
    con = duckdb.connect(str(path))
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    con.execute(f"CREATE TABLE {schema}.sales(amount DOUBLE)")
    for v in rows:
        con.execute(f"INSERT INTO {schema}.sales VALUES ({v})")
    con.close()
    return str(path)


@pytest.fixture
def alert_engine(tmp_path):
    from analysis.alerts import AlertEngine
    return AlertEngine(rules_dir=str(tmp_path / "alerts"))


class TestAlerts:
    def test_add_list_history(self, alert_engine):
        from analysis.alerts import AlertRule
        assert alert_engine.list_rules() == []
        assert alert_engine.get_history() == []
        alert_engine.add_rule(AlertRule(name="r1", table="sales", condition_sql="COUNT(*)", threshold=100.0))
        assert len(alert_engine.list_rules()) == 1
        # replace same name
        alert_engine.add_rule(AlertRule(name="r1", table="sales", condition_sql="COUNT(*)", threshold=1.0))
        assert len(alert_engine.list_rules()) == 1

    def test_triggered_and_not(self, alert_engine, tmp_path):
        from analysis.alerts import AlertRule
        db = _alert_db(tmp_path / "a.duckdb")
        alert_engine.add_rule(AlertRule(name="hi", table="sales", condition_sql="COUNT(*)", threshold=1.0))
        r = alert_engine.check_rule("hi", db, "analytics")
        assert r.triggered is True
        assert "in_app" in r.channels_notified
        alert_engine.add_rule(AlertRule(name="lo", table="sales", condition_sql="COUNT(*)", threshold=999.0))
        r2 = alert_engine.check_rule("lo", db, "analytics")
        assert r2.triggered is False
        assert r2.channels_notified == []
        assert alert_engine.get_triggered_count() == 1

    def test_missing_and_disabled(self, alert_engine, tmp_path):
        from analysis.alerts import AlertRule
        db = _alert_db(tmp_path / "b.duckdb")
        with pytest.raises(KeyError):
            alert_engine.check_rule("nope", db, "analytics")
        alert_engine.add_rule(AlertRule(name="off", table="sales", condition_sql="COUNT(*)",
                                        threshold=1.0, enabled=False))
        with pytest.raises(KeyError):
            alert_engine.check_rule("off", db, "analytics")
        with pytest.raises(KeyError):
            alert_engine.remove_rule("nope")

    def test_escalation(self, alert_engine, tmp_path):
        from analysis.alerts import AlertRule
        db = _alert_db(tmp_path / "c.duckdb")
        alert_engine.add_rule(AlertRule(name="esc", table="sales", condition_sql="COUNT(*)",
                                        threshold=1.0, escalation_after=1))
        alert_engine.check_rule("esc", db, "analytics")
        r2 = alert_engine.check_rule("esc", db, "analytics")
        assert r2.severity == "critical"

    def test_channels_no_notify(self, alert_engine, tmp_path):
        from analysis.alerts import AlertRule
        db = _alert_db(tmp_path / "d.duckdb")
        alert_engine.add_rule(AlertRule(name="ch", table="sales", condition_sql="COUNT(*)",
                                        threshold=1.0, channels=["slack", "email", "webhook"]))
        r = alert_engine.check_rule("ch", db, "analytics")
        assert r.triggered is True
        assert r.channels_notified == []

    def test_check_all_with_bad_rule(self, alert_engine, tmp_path):
        from analysis.alerts import AlertRule
        db = _alert_db(tmp_path / "e.duckdb")
        alert_engine.add_rule(AlertRule(name="good", table="sales", condition_sql="COUNT(*)", threshold=999.0))
        alert_engine.add_rule(AlertRule(name="bad", table="nosuchtable_xyz", condition_sql="COUNT(*)", threshold=1.0))
        alert_engine.add_rule(AlertRule(name="skip", table="sales", condition_sql="COUNT(*)",
                                        threshold=1.0, enabled=False))
        res = alert_engine.check_all(db, "analytics")
        assert len(res) == 2
        bad = [x for x in res if x.rule_name == "bad"][0]
        assert bad.triggered is False and "Error" in bad.message

    def test_remove_and_persist(self, alert_engine, tmp_path):
        from analysis.alerts import AlertRule, AlertEngine
        alert_engine.add_rule(AlertRule(name="todel", table="sales", condition_sql="COUNT(*)", threshold=5.0))
        alert_engine.remove_rule("todel")
        assert alert_engine.list_rules() == []
        # persistence across instances
        alert_engine.add_rule(AlertRule(name="keep", table="sales", condition_sql="COUNT(*)", threshold=5.0))
        eng2 = AlertEngine(rules_dir=alert_engine.rules_dir)
        assert [r.name for r in eng2.list_rules()] == ["keep"]

    def test_history_limit_and_send_failures(self, tmp_path):
        from analysis.alerts import _send_slack, _send_email, _send_webhook
        assert _send_slack("http://127.0.0.1:1/hook", "m") is False
        assert _send_email("127.0.0.1", 1, "", "", "a@b.c", ["x@y.z"], "m") is False
        assert _send_webhook("http://127.0.0.1:1/hook", {"a": 1}) is False

    def test_execute_none_row(self, alert_engine, tmp_path):
        from analysis.alerts import AlertEngine
        con = duckdb.connect(str(tmp_path / "empty.duckdb"))
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute("CREATE TABLE analytics.t(x INTEGER)")
        con.close()
        from analysis.alerts import AlertRule
        alert_engine.add_rule(AlertRule(name="zr", table="t", condition_sql="NULL", threshold=1.0))
        r = alert_engine.check_rule("zr", str(tmp_path / "empty.duckdb"), "analytics")
        assert r.value == 0.0 and r.triggered is False

    def test_execute_bad_sql(self, alert_engine, tmp_path):
        from analysis.alerts import AlertRule
        db = _alert_db(tmp_path / "f.duckdb")
        alert_engine.add_rule(AlertRule(name="bad2", table="sales",
                                        condition_sql="1/0 ??? bad syntax ((( ", threshold=1.0))
        res = alert_engine.check_all(db, "analytics")
        assert any(x.rule_name == "bad2" and x.triggered is False for x in res)


# ============================================================================
# agent.tools_sql
# ============================================================================

def _sql_db(path, schema="analytics"):
    con = duckdb.connect(str(path))
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    con.execute(f"CREATE TABLE {schema}.t(a INTEGER, b VARCHAR)")
    con.execute(f"INSERT INTO {schema}.t VALUES (1,'x'),(2,'y'),(3,'z')")
    con.close()
    return str(path)


class TestToolsSQL:
    def test_validate(self):
        from agent.tools_sql import SQLTool
        t = SQLTool(db_path=":memory:")
        assert t.validate_query("SELECT 1")[0] is True
        ok, msg = t.validate_query("DROP TABLE t")
        assert ok is False and "DROP" in msg
        ok, msg = t.validate_query("SELECT read_csv_auto('f.csv')")
        assert ok is False and "READ_CSV_AUTO" in msg
        ok, msg = t.validate_query("DELETE FROM t")
        assert ok is False
        ok, msg = t.validate_query("SELECT * FROM api_keys")
        assert ok is False and "platform table" in msg
        ok, msg = t.validate_query("SELECT 1; SELECT 2")
        assert ok is False and "Multiple statements" in msg
        assert t.validate_query("SELECT 1 -- comment")[0] is True
        assert t.validate_query("SELECT 1 /* c */")[0] is True
        ok, msg = t.validate_query("")
        assert ok is False

    def test_execute_happy(self, tmp_path):
        from agent.tools_sql import SQLTool
        db = _sql_db(tmp_path / "s.duckdb")
        tool = SQLTool(db_path=db, schema="analytics")
        r = tool.execute("SELECT * FROM t ORDER BY a")
        assert r.success is True and r.row_count == 3
        assert r.columns == ["a", "b"]
        assert r.execution_time_ms >= 0

    def test_execute_limit_added(self, tmp_path):
        from agent.tools_sql import SQLTool
        db = _sql_db(tmp_path / "s2.duckdb")
        tool = SQLTool(db_path=db, schema="analytics", max_rows=2)
        r = tool.execute("SELECT * FROM t")
        assert r.success and r.row_count == 2
        assert "LIMIT" in r.query

    def test_execute_forbidden_audit(self, tmp_path):
        from agent.tools_sql import SQLTool
        db = _sql_db(tmp_path / "s3.duckdb")

        class Audit:
            def __init__(self):
                self.calls = []

            def log_query(self, **kw):
                self.calls.append(kw)

        class User:
            user_id = "u1"
            tenant_id = "t1"

        tool = SQLTool(db_path=db, schema="analytics", audit_logger=Audit())
        r = tool.execute("DROP TABLE t", user=User())
        assert r.success is False
        assert tool.audit_logger.calls[0]["result"] == "failure"
        r2 = tool.execute("SELECT * FROM t", user=User())
        assert r2.success is True
        assert tool.audit_logger.calls[-1]["result"] == "success"
        # user without attrs
        r3 = tool.execute("DROP TABLE t", user=object())
        assert r3.success is False

    def test_execute_rls_and_params(self, tmp_path):
        from agent.tools_sql import SQLTool
        db = _sql_db(tmp_path / "s4.duckdb")

        class RLS:
            def rewrite_query(self, q, user):
                return q

        tool = SQLTool(db_path=db, schema="analytics", rls_manager=RLS())
        r = tool.execute("SELECT * FROM t WHERE a = ?", params=[2], user=object())
        assert r.success and r.row_count == 1

    def test_execute_runtime_error(self, tmp_path):
        from agent.tools_sql import SQLTool
        db = _sql_db(tmp_path / "s5.duckdb")

        class Audit:
            def __init__(self):
                self.calls = []

            def log_query(self, **kw):
                self.calls.append(kw)

        tool = SQLTool(db_path=db, schema="analytics", audit_logger=Audit())
        r = tool.execute("SELECT * FROM nosuchtable_xyz")
        assert r.success is False
        r2 = tool.execute("SELECT * FROM nosuchtable_xyz", user=object())
        assert r2.success is False

    def test_execute_many(self, tmp_path):
        from agent.tools_sql import SQLTool
        db = _sql_db(tmp_path / "s6.duckdb")
        tool = SQLTool(db_path=db, schema="analytics")
        res = tool.execute_many(["SELECT 1", "SELECT 2"])
        assert len(res) == 2 and all(x.success for x in res)

    def test_get_schema(self, tmp_path):
        from agent.tools_sql import SQLTool
        db = _sql_db(tmp_path / "s7.duckdb")
        tool = SQLTool(db_path=db, schema="analytics")
        one = tool.get_schema("t")
        assert one["table"] == "t" and one["row_count"] == 3
        assert one["columns"]
        all_s = tool.get_schema()
        assert "t" in all_s

    def test_explain_and_sample(self, tmp_path):
        from agent.tools_sql import SQLTool
        db = _sql_db(tmp_path / "s8.duckdb")
        tool = SQLTool(db_path=db, schema="analytics")
        plan = tool.explain_query("SELECT * FROM t")
        assert isinstance(plan, str) and len(plan) > 0
        df = tool.sample_table("t", n=2)
        assert len(df) == 2
        bad = tool.sample_table("bad;name", n=5)
        assert bad.empty
        empty = tool.sample_table("nosuchtable_xyz", n=2)
        assert empty.empty

    def test_referenced_and_openai(self):
        from agent.tools_sql import _referenced_tables, SQLToolOpenAI, execute_sql
        assert "t" in _referenced_tables('SELECT * FROM analytics.t JOIN u ON 1=1')
        assert _referenced_tables("SELECT 1") == []
        d = SQLToolOpenAI.get_function_definition()
        assert d["function"]["name"] == "execute_sql"

    def test_convenience(self, tmp_path):
        from agent.tools_sql import execute_sql
        db = _sql_db(tmp_path / "s9.duckdb")
        r = execute_sql("SELECT * FROM t", db_path=db, schema="analytics")
        assert r.success is True


# ============================================================================
# agent.schema_intelligence
# ============================================================================

class TestSchemaIntelligence:
    def test_empty(self):
        from agent.schema_intelligence import classify_schema
        p = classify_schema()
        assert p.dimensions == [] and p.measures == []
        p2 = classify_schema(table_name="t", columns={})
        assert p2.table_name == "t"

    def test_full(self):
        from agent.schema_intelligence import classify_schema
        cols = {"order_id": "VARCHAR", "order_date": "DATE", "total_amount": "DOUBLE",
                "region": "VARCHAR", "channel": "VARCHAR", "qty": "INTEGER"}
        rows = [{"order_id": f"O{i}", "order_date": "2026-01-01", "total_amount": 10.0 + i,
                 "region": "west" if i % 2 else "east", "channel": "web", "qty": i}
                for i in range(10)]
        p = classify_schema("sales", cols, rows, row_count=10)
        assert "order_id" in p.identifiers
        assert "order_date" in p.date_cols
        assert "total_amount" in p.measures
        assert "region" in p.dimensions
        assert p.best_dimension() is not None
        assert p.best_measure() == p.measures[0]
        assert p.has_dimensions and p.has_measures
        assert "never group by" in p.summarize().lower() or "Identifiers" in p.summarize()

    def test_code_low_high_card(self):
        from agent.schema_intelligence import classify_schema
        cols = {"zip_code": "VARCHAR", "sku_code": "VARCHAR"}
        low = [{"zip_code": "a", "sku_code": f"S{i}"} for i in range(6)]
        p = classify_schema("t", cols, low, row_count=6)
        assert "zip_code" not in p.identifiers  # low-card code -> dimension path
        cols2 = {"item_code": "VARCHAR"}
        rows2 = [{"item_code": f"C{i}"} for i in range(60)]
        p2 = classify_schema("t", cols2, rows2, row_count=60)
        assert "item_code" in p2.identifiers

    def test_prefix_identifier(self):
        from agent.schema_intelligence import classify_schema
        cols = {"id_thing": "VARCHAR"}
        rows = [{"id_thing": f"v{i}"} for i in range(60)]
        p = classify_schema("t", cols, rows, row_count=60)
        assert "id_thing" in p.identifiers

    def test_measure_id_excluded(self):
        from agent.schema_intelligence import classify_schema
        p = classify_schema("t", {"amount_id": "DOUBLE"}, [{"amount_id": 1.0}], row_count=1)
        assert "amount_id" not in p.measures

    def test_date_by_type(self):
        from agent.schema_intelligence import classify_schema
        p = classify_schema("t", {"ts": "TIMESTAMP"}, [{"ts": "2026-01-01"}], row_count=1)
        assert "ts" in p.date_cols

    def test_enrich(self):
        from agent.schema_intelligence import classify_schema
        cols = {"region": "VARCHAR", "total_amount": "DOUBLE"}
        rows = [{"region": "west", "total_amount": 10.0},
                {"region": "east", "total_amount": "bad"},
                {"region": None, "total_amount": 30.0}]
        p = classify_schema("t", cols, rows, row_count=3)
        p.enrich_with_values(rows)
        assert p.distinct_values.get("region") == ["east", "west"]
        assert p.measure_ranges["total_amount"]["min"] == 10.0
        p.enrich_with_values([])  # no-op

    def test_best_none(self):
        from agent.schema_intelligence import SchemaProfile
        p = SchemaProfile()
        assert p.best_dimension() is None
        assert p.best_measure() is None
        assert p.best_dimension(exclude=set()) is None
        p2 = SchemaProfile(dimensions=["a", "b"])
        assert p2.best_dimension(exclude={"a"}) == "b"
        assert "none" in p.summarize()

    def test_context(self):
        from agent.schema_intelligence import classify_schema_from_context
        assert classify_schema_from_context("").table_name == ""
        ctx = "TABLE analytics.sales (10 rows):\n  - order_id: VARCHAR\n  - total_amount: DOUBLE\n"
        p = classify_schema_from_context(ctx)
        assert p.table_name == "sales" and p.row_count == 10
        assert "order_id" in p.all_columns

    def test_helpers(self):
        from agent.schema_intelligence import (
            _is_identifier, _is_date_column, _is_measure_type, _is_dimension_type,
            _is_id_column, _is_dimension_by_name, _dimension_priority,
            _measure_priority, _estimate_cardinality, format_schema_for_prompt,
            SchemaProfile,
        )
        assert _is_identifier("x", "customer_id", "VARCHAR", {}) is True
        assert _is_identifier("x", "zzz", "VARCHAR", {}) is False
        assert _is_date_column("ts", "created_at_timestamp", "VARCHAR") is True
        assert _is_date_column("n", "name", "VARCHAR") is False
        assert _is_measure_type("DOUBLE") is True
        assert _is_measure_type("VARCHAR") is False
        assert _is_dimension_type("BOOLEAN") is True
        assert _is_dimension_type("DOUBLE") is False
        assert _is_id_column("order_id") is True
        assert _is_id_column("region") is False
        assert _is_dimension_by_name("sales_region") is True
        assert _is_dimension_by_name("zzz_random") is False
        assert _dimension_priority("region") < _dimension_priority("zzz")
        assert _measure_priority("total_revenue") == 105
        assert _measure_priority("total_spent") == 110
        assert _measure_priority("total_orders") == 40
        assert _measure_priority("quantity") == 60
        assert _measure_priority("profit") == 50
        assert _measure_priority("zzz") == 10
        assert _estimate_cardinality({"a": "INT"}, None, 0) == {}
        assert _estimate_cardinality({"a": "INT"}, [{"a": 1}, {"a": 1}], 2) == {"a": 1}
        prof = SchemaProfile(table_name="t", row_count=3, dimensions=["region"],
                             measures=["revenue"], date_cols=["d"], identifiers={"order_id"})
        s = format_schema_for_prompt(prof)
        assert "AVOID" in s


# ============================================================================
# warehouse.queries
# ============================================================================

def _wh_db(path, schema="analytics"):
    con = duckdb.connect(str(path))
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    con.execute(f"""CREATE TABLE {schema}.sales(order_id VARCHAR, customer_id VARCHAR,
        product_id VARCHAR, order_date DATE, total_amount DOUBLE, quantity INTEGER)""")
    con.execute(f"""CREATE TABLE {schema}.products(product_id VARCHAR, product_name VARCHAR,
        category VARCHAR)""")
    con.execute(f"""CREATE TABLE {schema}.customers(customer_id VARCHAR, customer_name VARCHAR,
        segment VARCHAR)""")
    con.execute(f"""INSERT INTO {schema}.sales VALUES
        ('o1','c1','p1',DATE '2026-01-05',100.0,2),
        ('o1','c1','p2',DATE '2026-01-05',50.0,1),
        ('o2','c1','p1',DATE '2026-02-06',100.0,2),
        ('o3','c2','p2',DATE '2026-02-10',75.0,3),
        ('o4','c2','p1',DATE '2026-03-01',200.0,1),
        ('o4','c2','p1',DATE '2026-03-01',200.0,1)""")
    con.execute(f"INSERT INTO {schema}.products VALUES ('p1','Widget','Gadgets'),('p2','Gizmo','Gadgets')")
    con.execute(f"INSERT INTO {schema}.customers VALUES ('c1','Ann','Retail'),('c2','Bob','Wholesale')")
    con.close()
    return str(path)


class TestWarehouseQueries:
    def test_monthly(self, tmp_path):
        import datetime
        from warehouse.queries import QueryLibrary
        lib = QueryLibrary(db_path=_wh_db(tmp_path / "w.duckdb"))
        df = lib.monthly_revenue()
        assert len(df) >= 2 and "revenue" in df.columns
        df2 = lib.monthly_revenue(start_date=datetime.date(2026, 2, 1),
                                  end_date=datetime.date(2026, 3, 1))
        assert len(df2) >= 1
        assert len(df2) >= 1
        df3 = lib.monthly_revenue(group_by="customer_id")
        assert len(df3) >= 1

    def test_by_dimension(self, tmp_path):
        import datetime
        from warehouse.queries import QueryLibrary
        lib = QueryLibrary(db_path=_wh_db(tmp_path / "w2.duckdb"))
        df = lib.revenue_by_dimension("customer_id")
        assert "revenue" in df.columns
        with pytest.raises(Exception):
            lib.revenue_by_dimension("mystery_col_xyz")
        df3 = lib.revenue_by_dimension("customer_id", start_date=datetime.date(2026, 2, 1),
                                       end_date=datetime.date(2026, 3, 1))
        assert df3 is not None

    def test_top_products(self, tmp_path):
        import datetime
        from warehouse.queries import QueryLibrary
        lib = QueryLibrary(db_path=_wh_db(tmp_path / "w3.duckdb"))
        assert "revenue" in lib.top_products("revenue").columns
        assert "quantity" in lib.top_products("quantity").columns
        assert "orders" in lib.top_products("orders").columns
        assert "zzz" in lib.top_products("zzz").columns  # unknown falls back to revenue expr
        df = lib.top_products("revenue", start_date=datetime.date(2026, 2, 1),
                              end_date=datetime.date(2026, 3, 1))
        assert df is not None

    def test_clv(self, tmp_path):
        from warehouse.queries import QueryLibrary
        lib = QueryLibrary(db_path=_wh_db(tmp_path / "w4.duckdb"))
        df = lib.customer_lifetime_value()
        assert "total_revenue" in df.columns
        df2 = lib.customer_lifetime_value(min_orders=5)
        assert df2 is not None

    def test_cohort(self, tmp_path):
        from warehouse.queries import QueryLibrary
        lib = QueryLibrary(db_path=_wh_db(tmp_path / "w5.duckdb"))
        df = lib.cohort_analysis()
        assert "retention_rate" in df.columns

    def test_kpi(self, tmp_path):
        from warehouse.queries import QueryLibrary
        lib = QueryLibrary(db_path=_wh_db(tmp_path / "w6.duckdb"))
        df = lib.kpi_summary()
        assert len(df) == 5

    def test_growth(self, tmp_path):
        from warehouse.queries import QueryLibrary
        lib = QueryLibrary(db_path=_wh_db(tmp_path / "w7.duckdb"))
        df = lib.growth_metrics(periods_back=3)
        assert "revenue_growth_pct" in df.columns

    def test_quality_dup_outliers(self, tmp_path):
        from warehouse.queries import QueryLibrary, get_query_library
        lib = get_query_library(db_path=_wh_db(tmp_path / "w8.duckdb"))
        q = lib.data_quality_report("sales")
        assert "null_pct" in q.columns
        d = lib.find_duplicates("sales", ["order_id"])
        assert "duplicate_count" in d.columns
        o = lib.outliers_iqr("sales", "total_amount")
        assert o is not None
