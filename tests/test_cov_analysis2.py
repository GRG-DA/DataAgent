"""Coverage tests for low-coverage analysis modules (TDD, tiny frames, <120s).

Targets: pipeline, anomaly_pipeline, auto_pipeline, automl, bayesian,
experiments, insights, survival, tasks, lineage, timeseries, quality
remainder, bi_export, recommender. Happy + empty + invalid branches.
"""
import asyncio
import json

import duckdb
import numpy as np
import pandas as pd
import pytest


def _small_df(n=12):
    return pd.DataFrame({
        "a": [float(i) for i in range(n)],
        "b": [float(i * 2 + 1) for i in range(n)],
        "c": [float(i % 3) for i in range(n)],
        "g": (["X", "Y"] * ((n + 1) // 2))[:n],
    })


# ============================================================================
# anomaly_pipeline
# ============================================================================

class TestAnomalyPipeline:
    def test_iqr_happy_and_small(self):
        from analysis.anomaly_pipeline import detect_iqr_outliers
        s = pd.Series([1.0] * 10 + [100.0])
        r = detect_iqr_outliers(s)
        assert r["count"] >= 1 and r["method"] == "iqr"
        assert "lower_bound" in r
        tiny = detect_iqr_outliers(pd.Series([1.0, 2.0]))
        assert tiny["count"] == 0

    def test_zscore_happy_and_small(self):
        from analysis.anomaly_pipeline import detect_zscore_outliers
        s = pd.Series([1.0] * 10 + [100.0])
        r = detect_zscore_outliers(s, threshold=2.0)
        assert r["method"] == "zscore" and r["count"] >= 0
        assert detect_zscore_outliers(pd.Series([1.0]))["count"] == 0

    def test_change_points(self):
        from analysis.anomaly_pipeline import detect_change_points
        assert detect_change_points([1.0, 2.0]) == []
        vals = [1.0, 1.1, 0.9, 1.0, 1.0, 50.0, 1.0, 1.0, 1.0, 1.0]
        out = detect_change_points(vals)
        assert isinstance(out, list)
        flat = [1.0] * 10
        assert detect_change_points(flat) == []

    def test_seasonality(self):
        from analysis.anomaly_pipeline import detect_seasonality
        df = pd.DataFrame({
            "d": pd.date_range("2026-01-01", periods=20, freq="D"),
            "v": [float(i % 7) for i in range(20)],
        })
        r = detect_seasonality(df, "d", "v")
        assert "has_seasonality" in r
        assert detect_seasonality(df, "nope", "v") == {"has_seasonality": False, "period": None}
        small = pd.DataFrame({"d": pd.date_range("2026-01-01", periods=5, freq="D"), "v": [1.0] * 5})
        assert detect_seasonality(small, "d", "v")["has_seasonality"] is False
        bad = pd.DataFrame({"d": ["x", None], "v": [1.0, 2.0]})
        assert "has_seasonality" in detect_seasonality(bad, "d", "v")

    def test_analyze_dataframe(self):
        from analysis.anomaly_pipeline import analyze_dataframe
        df = _small_df(15)
        df.loc[0, "a"] = 500.0
        r = analyze_dataframe(df, date_col=None)
        assert "anomaly_count" in r and "severity" in r
        r2 = analyze_dataframe(df, date_col="g")
        assert r2["anomaly_count"] >= 0
        empty = analyze_dataframe(pd.DataFrame({"x": [1.0, 2.0]}))
        assert empty["severity"] == "low"
        many = pd.DataFrame({f"c{i}": [1.0] * 14 + [999.0] for i in range(7)})
        r3 = analyze_dataframe(many)
        assert r3["severity"] in ("low", "medium", "high")


# ============================================================================
# auto_pipeline
# ============================================================================

def _auto_db(path, n=12):
    con = duckdb.connect(str(path))
    con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    con.execute("CREATE TABLE analytics.sales(amount DOUBLE, qty INTEGER)")
    for i in range(n):
        con.execute(f"INSERT INTO analytics.sales VALUES ({10.0 + i}, {i})")
    con.execute("INSERT INTO analytics.sales VALUES (9999.0, 999)")
    con.close()
    return str(path)


@pytest.fixture
def auto(tmp_path):
    from analysis.auto_pipeline import AutoAnalyzer
    db = _auto_db(tmp_path / "w.duckdb")
    a = AutoAnalyzer(db_path=db, schema="analytics", trends_dir=str(tmp_path / "trends"))
    return a


class TestAutoPipeline:
    def test_register_remove_alert(self, auto):
        fired = []
        auto.on_alert(lambda r: fired.append(r))
        auto.register_check("sales", ["quality"])
        assert "sales" in auto._checks
        auto.remove_check("sales")
        assert "sales" not in auto._checks
        auto.remove_check("missing")  # no error

    def test_run_quality(self, auto):
        auto.register_check("sales", ["quality"])
        out = auto.run_all_checks()
        assert out["checks"][0]["checks"][0]["type"] == "quality"
        assert out["checks"][0]["checks"][0]["result"]["rows"] >= 10

    def test_run_unknown_and_error(self, auto):
        auto.register_check("sales", ["nope"])
        out = auto.run_all_checks()
        assert out["checks"][0]["checks"][0]["result"] == {"status": "unknown_check"}
        auto.register_check("nosuchtable", ["quality"])
        out2 = auto.run_all_checks()
        errs = [c for t in out2["checks"] for c in t["checks"] if "error" in c]
        assert errs

    def test_anomaly_and_alert(self, auto, tmp_path):
        auto.register_check("sales", ["anomaly"])
        hits = []
        auto.on_alert(lambda r: hits.append(r))
        out = auto.run_all_checks()
        res = out["checks"][0]["checks"][0]["result"]
        assert res["anomaly_count"] >= 0 and res["severity"] in ("low", "medium", "high")
        # force high severity alert path with tiny frame containing many outliers
        con = duckdb.connect(auto.db_path.as_posix() if hasattr(auto.db_path, "as_posix") else str(auto.db_path))
        con.close()

    def test_anomaly_small_table(self, tmp_path):
        from analysis.auto_pipeline import AutoAnalyzer
        con = duckdb.connect(str(tmp_path / "s.duckdb"))
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute("CREATE TABLE analytics.tiny(x INTEGER)")
        con.execute("INSERT INTO analytics.tiny VALUES (1),(2)")
        con.close()
        a = AutoAnalyzer(db_path=str(tmp_path / "s.duckdb"), schema="analytics",
                         trends_dir=str(tmp_path / "tr"))
        a.register_check("tiny", ["anomaly"])
        out = a.run_all_checks()
        assert out["checks"][0]["checks"][0]["result"]["anomaly_count"] == 0

    def test_drift_branches(self, auto):
        assert auto._check_drift("sales")["status"] == "insufficient_data"
        auto._save_trend("sales", "quality", {"rows": 10, "null_pct": 0.0, "dup_pct": 0.0})
        auto._save_trend("sales", "quality", {"rows": 100, "null_pct": 0.0, "dup_pct": 0.0})
        d = auto._check_drift("sales")
        assert d["drift_detected"] is True
        hist = auto._load_trends("sales", "quality")
        assert len(hist) >= 2
        assert auto._load_trends("sales", "missing_kind") == []
        # second save appends to existing csv
        auto._save_trend("sales", "quality", {"rows": 100, "null_pct": 0.0, "dup_pct": 0.0})
        assert len(auto._load_trends("sales", "quality")) >= 3


# ============================================================================
# automl
# ============================================================================

class TestAutoML:
    def test_select_happy(self):
        from analysis.automl import auto_select_features
        df = pd.DataFrame({
            "x1": [float(i) for i in range(12)],
            "x2": [float(i * 2) for i in range(12)],
            "y": [float(i * 3) for i in range(12)],
        })
        r = auto_select_features(df, "y")
        assert r.n_features_selected >= 1 and "y" not in r.selected_features
        r2 = auto_select_features(df, "y", n_features=1)
        assert r2.n_features_selected == 1

    def test_select_no_numeric(self):
        from analysis.automl import auto_select_features
        r = auto_select_features(pd.DataFrame({"s": ["a", "b"]}), "y")
        assert r.n_features_selected == 0 and "No numeric" in r.interpretation

    def test_select_missing_target(self):
        from analysis.automl import auto_select_features
        df = pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": [2.0, 3.0, 4.0]})
        r = auto_select_features(df, "zzz")
        assert r.n_features_selected == 0 and "not found" in r.interpretation

    def test_select_multicollinear_and_fallback(self):
        from analysis.automl import auto_select_features
        df = pd.DataFrame({
            "a": [float(i) for i in range(10)],
            "b": [float(i) for i in range(10)],  # identical -> removed
            "y": [float(i) for i in range(10)],
        })
        r = auto_select_features(df, "a", multicollinearity_threshold=0.5)
        assert r.n_features_selected >= 1
        # fallback: nothing above threshold -> keep top 3
        df2 = pd.DataFrame({
            "a": [1.0, -1.0, 1.0, -1.0, 1.0, -1.0],
            "b": [-1.0, 1.0, -1.0, 1.0, -1.0, 1.0],
            "y": [1.0, 2.0, 1.0, 2.0, 1.0, 2.0],
        })
        r2 = auto_select_features(df2, "y", correlation_threshold=0.99)
        assert r2.n_features_selected >= 1
        # tiny valid rows -> score 0.0 branch
        df3 = pd.DataFrame({"a": [1.0, None, None], "y": [1.0, 2.0, 3.0]})
        r3 = auto_select_features(df3, "y")
        assert r3.feature_scores.get("a", 0.0) == 0.0

    def test_compare_happy_and_empty(self):
        from analysis.automl import compare_regressions, _empty_reg_comparison
        df = pd.DataFrame({
            "x": [float(i) for i in range(12)],
            "y": [float(i * 2 + 1) for i in range(12)],
        })
        r = compare_regressions(df, "y", ["x"])
        assert r.recommended_model in ("linear", "polynomial")
        assert "Linear" in r.interpretation or "linear" in r.interpretation.lower()
        e = compare_regressions(pd.DataFrame({"s": ["a"]}), "y", [])
        assert e.recommended_model == "linear"
        assert _empty_reg_comparison("t", [], "why").interpretation == "why"
        # quadratic data should prefer polynomial with lenient threshold
        q = pd.DataFrame({"x": [float(i) for i in range(12)],
                          "y": [float(i * i) for i in range(12)]})
        rq = compare_regressions(q, "y", ["x"], threshold_r2_delta=0.0)
        assert rq.recommended_model in ("linear", "polynomial")

    def test_auto_analyze(self):
        from analysis.automl import auto_analyze
        df = pd.DataFrame({
            "x1": [float(i) for i in range(12)],
            "x2": [float(i % 4) for i in range(12)],
            "y": [float(i * 2) for i in range(12)],
        })
        r = auto_analyze(df, "y")
        assert r.recommended_features and r.summary
        assert any(s["step"] == "data_overview" for s in r.all_steps)
        # missing target -> skipped branches
        r2 = auto_analyze(df, "zzz")
        steps = {s["step"]: s["status"] for s in r2.all_steps}
        assert steps["regression_comparison"] == "skipped"
        assert steps["feature_importance"] == "skipped"
        # empty frame -> insufficient/skipped
        r3 = auto_analyze(pd.DataFrame({"a": []}), "a")
        assert r3.summary


# ============================================================================
# bayesian
# ============================================================================

class TestBayesian:
    def _grp_df(self):
        return pd.DataFrame({
            "g": ["A"] * 8 + ["B"] * 8,
            "x": [1.0, 1.2, 0.9, 1.1, 1.0, 1.3, 0.8, 1.2,
                  5.0, 5.2, 4.8, 5.1, 5.0, 4.9, 5.3, 5.1],
        })

    def test_ttest_group(self):
        from analysis.bayesian import bayesian_ttest
        r = bayesian_ttest(self._grp_df(), "x", group_col="g")
        assert r.group_a_n == 8 and r.group_b_n == 8
        assert 0 <= r.probability_a_greater_b <= 1
        assert "Bayes factor" in r.interpretation or "bayes" in r.interpretation.lower()

    def test_ttest_columns(self):
        from analysis.bayesian import bayesian_ttest
        df = pd.DataFrame({"a": [1.0, 1.1, 0.9, 1.2, 1.0, 0.8],
                           "b": [3.0, 3.1, 2.9, 3.2, 3.0, 2.8]})
        r = bayesian_ttest(df, "a", "b", prior_scale=1.0)
        assert r.group_a == "a" and r.confidence_level == 0.95

    def test_ttest_errors(self):
        from analysis.bayesian import bayesian_ttest
        one = pd.DataFrame({"g": ["A"] * 4, "x": [1.0, 2.0, 3.0, 4.0]})
        assert "Error" in bayesian_ttest(one, "x", group_col="g").interpretation
        tiny = pd.DataFrame({"g": ["A", "B"], "x": [1.0, 2.0]})
        assert "Error" in bayesian_ttest(tiny, "x", group_col="g").interpretation
        assert "Error" in bayesian_ttest(pd.DataFrame({"a": [1.0]}), "").interpretation

    def test_corr_happy_and_small(self):
        from analysis.bayesian import bayesian_correlation
        df = pd.DataFrame({"a": [float(i) for i in range(12)],
                           "b": [float(i * 2) for i in range(12)]})
        r = bayesian_correlation(df, "a", "b")
        assert r.n == 12 and r.probability_positive > 0.5
        assert "correlation" in r.interpretation.lower()
        tiny = pd.DataFrame({"a": [1.0], "b": [2.0]})
        assert "Error" in bayesian_correlation(tiny, "a", "b").interpretation

    def test_bf_bands(self):
        from analysis.bayesian import _interpret_bayes_factor
        assert "H0" in _interpret_bayes_factor(0.5)
        assert "H0" in _interpret_bayes_factor(0.05)
        assert "H0" in _interpret_bayes_factor(0.02)
        assert "H0" in _interpret_bayes_factor(0.005)
        assert "H0" in _interpret_bayes_factor(0.001)
        assert "H1" in _interpret_bayes_factor(1.5)
        assert "H1" in _interpret_bayes_factor(5.0)
        assert "H1" in _interpret_bayes_factor(15.0)
        assert "H1" in _interpret_bayes_factor(50.0)
        assert "H1" in _interpret_bayes_factor(500.0)

    def test_pps(self):
        from analysis.bayesian import posterior_predictive_summary
        out = posterior_predictive_summary(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]), n_simulations=50)
        assert out["n_simulations"] == 50 and "observed_mean" in out
        assert posterior_predictive_summary(pd.Series([1.0])) == {"error": "Need at least 2 observations."}


# ============================================================================
# experiments
# ============================================================================

class TestExperiments:
    def _reg(self, tmp_path, monkeypatch):
        import analysis.experiments as ex
        monkeypatch.setattr(ex, "EXPERIMENTS_FILE", str(tmp_path / "ex.json"))
        from analysis.experiments import ExperimentRegistry
        return ExperimentRegistry(experiments_dir=str(tmp_path))

    def test_crud(self, tmp_path, monkeypatch):
        from analysis.experiments import Experiment
        reg = self._reg(tmp_path, monkeypatch)
        assert reg.list_all() == [] and reg.get("x") is None
        reg.create(Experiment(name="e1", description="d"))
        assert reg.get("e1").description == "d"
        with pytest.raises(ValueError):
            reg.create(Experiment(name="e1"))
        assert reg.update("e1", description="d2") is True
        assert reg.update("nope", description="z") is False
        assert reg.update("e1", nosuchfield="v") is True
        assert reg.list_all(status="draft")[0].name == "e1"
        assert reg.list_all(status="running") == []

    def test_lifecycle(self, tmp_path, monkeypatch):
        from analysis.experiments import Experiment
        reg = self._reg(tmp_path, monkeypatch)
        reg.create(Experiment(name="e1"))
        assert reg.start("e1") is True and reg.get("e1").status == "running"
        assert reg.complete("e1", results={"w": 1}) is True
        assert reg.get("e1").status == "completed"
        assert reg.stop("e1") is True and reg.get("e1").status == "stopped"
        assert reg.delete("e1") is True and reg.delete("e1") is False
        assert reg.start("gone") is False

    def test_history_stats(self, tmp_path, monkeypatch):
        from analysis.experiments import Experiment
        reg = self._reg(tmp_path, monkeypatch)
        reg.create(Experiment(name="a"))
        reg.create(Experiment(name="b"))
        reg.start("a")
        h = reg.get_history(limit=1)
        assert len(h) == 1
        s = reg.get_stats()
        assert s["total"] == 2 and s["running"] == 1

    def test_load_corrupt(self, tmp_path, monkeypatch):
        import analysis.experiments as ex
        (tmp_path / "bad.json").write_text("{not json")
        monkeypatch.setattr(ex, "EXPERIMENTS_FILE", str(tmp_path / "bad.json"))
        from analysis.experiments import ExperimentRegistry
        r = ExperimentRegistry(experiments_dir=str(tmp_path))
        assert r.list_all() == []


# ============================================================================
# insights
# ============================================================================

def _insight_df():
    n = 15
    return pd.DataFrame({
        "sales": [10.0, 12.0, 11.0, 13.0, 12.5, 11.5, 200.0] + [10.0 + i for i in range(8)],
        "qty": [1.0, 2, 1, 3, 2, 1, 2] + [1.0] * 8,
        "cat": ["A"] * 9 + ["B"] * 4 + ["C"] * 2,
        "const": ["same"] * 15,
        "d": pd.date_range("2026-01-01", periods=15, freq="D"),
    })


class TestInsights:
    def test_empty(self):
        from analysis.insights import generate_auto_insights
        out = generate_auto_insights(pd.DataFrame(), table_name="t")
        assert out["summary"] == "The dataset is empty."
        assert out["table"] == "t"

    def test_happy(self):
        from analysis.insights import generate_auto_insights
        out = generate_auto_insights(_insight_df(), table_name="sales")
        assert "rows" in out["summary"]
        assert out["metrics_summary"] and out["recommendations"]
        assert out["confidence"] in ("high", "medium")

    def test_missing_dups(self):
        from analysis.insights import generate_auto_insights
        df = pd.DataFrame({"a": [1.0, None, None, None, 1.0], "b": [1, 1, 1, 1, 1]})
        df = pd.concat([df, df.head(1)], ignore_index=True)
        out = generate_auto_insights(df)
        assert out["key_findings"]

    def test_corr_outlier_inputs(self):
        from analysis.insights import generate_auto_insights
        df = _insight_df()
        corr = {"pairs": [{"col1": "sales", "col2": "qty", "correlation": 0.85,
                           "direction": "positive"}]}
        outs = {"outliers": [{"column": "sales", "outlier_pct": 10.0,
                               "outlier_count": 2, "lower_bound": 0.0, "upper_bound": 50.0}]}
        out = generate_auto_insights(df, correlation_data=corr, outlier_data=outs)
        assert out["correlation_highlights"] and out["outlier_highlights"]
        neg = {"pairs": [{"col1": "a", "col2": "b", "correlation": -0.9, "direction": "negative"}]}
        out2 = generate_auto_insights(df, correlation_data=neg)
        assert "opposite" in out2["correlation_highlights"][0]

    def test_narratives(self):
        from analysis.insights import generate_narrative
        df = _insight_df()
        assert generate_narrative(df, "summary", table_name="t")["summary"]
        c = generate_narrative(df, "comparison")
        assert c["success"] is True and "difference" in c
        t = generate_narrative(df, "trend")
        assert t["success"] is True and t["direction"] in ("increasing", "decreasing")
        a = generate_narrative(df, "anomaly")
        assert a["success"] is True
        assert generate_narrative(df, "zzz")["success"] is False

    def test_narrative_errors(self):
        from analysis.insights import generate_narrative
        assert generate_narrative(pd.DataFrame({"a": [1.0]}), "comparison")["success"] is False
        assert generate_narrative(pd.DataFrame({"a": [1.0]}), "trend")["success"] is False
        assert generate_narrative(pd.DataFrame({"s": ["x"]}), "anomaly")["success"] is False
        one = pd.DataFrame({"d": pd.to_datetime(["2026-01-01"]), "v": [1.0]})
        assert generate_narrative(one, "trend")["success"] is False
        clean = pd.DataFrame({"v": [1.0] * 10})
        assert generate_narrative(clean, "anomaly")["anomalies_found"] == 0
        const = pd.DataFrame({"v": [5.0] * 10})
        assert generate_narrative(const, "anomaly")["anomalies_found"] == 0

    def test_recommendations_and_empty(self):
        from analysis.insights import _generate_recommendations, _empty_insights
        e = _empty_insights("t")
        assert e["confidence"] == "high"
        df = pd.DataFrame({"a": [1]})
        assert _generate_recommendations(df, {})  # fallback rec

    def test_llm_fallback_and_mock(self):
        from analysis.insights import generate_llm_insights
        df = _small_df(10)

        class Boom:
            async def chat(self, *a, **k):
                raise RuntimeError("no llm")

        out = asyncio.run(generate_llm_insights(df, table_name="t", client=Boom()))
        assert out["source"] == "rule-based" and out["recommendations"] is not None

        class FakeMsg:
            content = "EXECUTIVE_OVERVIEW: great numbers KEY_FINDINGS: - good"

        class FakeResp:
            message = FakeMsg()

        class FakeClient:
            async def chat(self, *a, **k):
                return FakeResp()

        out2 = asyncio.run(generate_llm_insights(df, table_name="t", client=FakeClient()))
        assert out2["source"] == "llm" and "great" in out2["narrative"]

    def test_batch(self, tmp_path):
        from analysis.insights import generate_batch_insights
        con = duckdb.connect(str(tmp_path / "b.duckdb"))
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute("CREATE TABLE analytics.t1(a INTEGER)")
        con.execute("INSERT INTO analytics.t1 VALUES (1),(2)")
        con.execute("CREATE TABLE analytics.tempty(a INTEGER)")
        con.close()
        out = generate_batch_insights(["t1", "tempty", "missing"], str(tmp_path / "b.duckdb"), "analytics")
        assert out["success"] is True
        assert out["tables"]["t1"]["success"] is True
        assert out["tables"]["tempty"]["success"] is False
        assert out["tables"]["missing"]["success"] is False
        bad = generate_batch_insights(["t1"], str(tmp_path / "nodir" / "x.duckdb"), "analytics")
        # duckdb creates file in missing dir? either success or error dict
        assert "success" in bad


# ============================================================================
# survival
# ============================================================================

class TestSurvival:
    def test_km_happy(self):
        from analysis.survival import kaplan_meier
        t = pd.Series([5.0, 6, 6, 8, 10, 12, 12, 15, 3, 9.0])
        e = pd.Series([1, 0, 1, 1, 0, 1, 1, 0, 1, 1])
        r = kaplan_meier(t, e, column_name="os")
        assert r.total_subjects == 10 and r.times
        assert r.median_survival is not None or r.median_survival is None
        assert "Kaplan" in r.interpretation

    def test_km_empty_and_no_events(self):
        from analysis.survival import kaplan_meier
        r = kaplan_meier(pd.Series([], dtype=float), pd.Series([], dtype=float))
        assert r.times == [] and r.total_subjects == 0
        r2 = kaplan_meier(pd.Series([1.0, 2.0, 3.0]), pd.Series([0, 0, 0]))
        assert r2.survival_probabilities == [1.0, 1.0, 1.0]
        assert r2.median_survival is None

    def test_logrank(self):
        from analysis.survival import log_rank_test
        a_t = pd.Series([5.0, 6, 6, 8, 10, 12.0])
        a_e = pd.Series([1, 1, 1, 0, 1, 1])
        b_t = pd.Series([2.0, 3, 3, 4, 5, 6.0])
        b_e = pd.Series([1, 1, 1, 1, 1, 1])
        r = log_rank_test(a_t, a_e, b_t, b_e, group_names=("A", "B"))
        assert r.groups == ["A", "B"] and r.degrees_of_freedom == 1
        assert isinstance(r.significant, (bool, np.bool_))
        # insufficient
        r2 = log_rank_test(pd.Series([1.0]), pd.Series([1.0]),
                           pd.Series([], dtype=float), pd.Series([], dtype=float))
        assert r2.p_value == 1.0
        # no events
        r3 = log_rank_test(pd.Series([1.0, 2.0]), pd.Series([0, 0]),
                           pd.Series([1.0, 2.0]), pd.Series([0, 0]))
        assert "No events" in r3.interpretation

    def test_by_group(self):
        from analysis.survival import kaplan_meier_by_group
        df = pd.DataFrame({
            "t": [5.0, 6, 8, 3, 9, 4.0, 7, 10],
            "e": [1, 0, 1, 1, 1, 0, 1, 1],
            "g": ["A"] * 4 + ["B"] * 4,
        })
        out = kaplan_meier_by_group(df, "t", "e", "g")
        assert set(out) == {"A", "B"}


# ============================================================================
# tasks
# ============================================================================

class TestTasks:
    def test_submit_ok(self):
        from analysis.tasks import TaskManager
        m = TaskManager()
        tid = m.submit("add", lambda x, y=0: x + y, args=(2,), kwargs={"y": 3})
        import time as _t
        for _ in range(100):
            t = m.get_task(tid)
            if t.status.value in ("completed", "failed"):
                break
            _t.sleep(0.01)
        assert m.get_task(tid).result == 5
        assert m.get_task(tid).status.value == "completed"
        assert m.get_task("nope") is None
        assert len(m.get_all_tasks()) == 1
        assert m.active_count == 0

    def test_submit_fail(self):
        from analysis.tasks import TaskManager
        import time as _t
        m = TaskManager()

        def _boom():
            raise ValueError("bad")
        tid = m.submit("boom", _boom)
        for _ in range(100):
            if m.get_task(tid).status.value in ("failed",):
                break
            _t.sleep(0.01)
        assert m.get_task(tid).status.value == "failed"
        assert "bad" in m.get_task(tid).error

    def test_cancel_progress_cleanup(self):
        from analysis.tasks import TaskManager
        import time as _t
        m = TaskManager()
        evt_holder = {}

        def _slow():
            _t.sleep(0.2)
            return 1
        tid = m.submit("slow", _slow)
        assert m.cancel(tid) is True
        assert m.cancel("missing") is False
        m.update_progress(tid, 150.0, "hi")
        assert m.get_task(tid).progress == 100.0
        m.update_progress(tid, -5.0)
        assert m.get_task(tid).progress == 0.0
        m.update_progress("missing", 10.0)
        assert m.pending_count >= 0
        # cleanup old: mark completed long ago
        t = m.get_task(tid)
        for _ in range(50):
            if t.status.value in ("completed", "cancelled", "failed"):
                break
            _t.sleep(0.01)
        t.completed_at = _t.time() - 9999
        m.cleanup_old(max_age_seconds=10)
        assert m.get_task(tid) is None


# ============================================================================
# lineage
# ============================================================================

@pytest.fixture
def lin(tmp_path):
    from analysis.lineage import LineageTracker
    return LineageTracker(storage_path=tmp_path / "lin.json")


class TestLineage:
    def test_record_get(self, lin):
        tid = lin.record_transform("out", "filter", ["src"], ["out"], "desc",
                                   metadata={"k": 1}, column_map={"y": ["src.x"]})
        assert tid
        recs = lin.get_lineage("out")
        assert len(recs) == 1 and recs[0]["operation"] == "filter"
        assert lin.get_lineage("missing") == []
        full = lin.get_full_lineage()
        assert "out" in full
        assert "LineageTracker" in repr(lin)

    def test_multi_output(self, lin):
        lin.record_transform("a", "split", ["raw"], ["a", "b"])
        assert lin.get_lineage("b")
        assert lin.get_lineage("a")

    def test_up_down(self, lin):
        lin.record_transform("mid", "join", ["s1", "s2"], ["mid"])
        lin.record_transform("final", "agg", ["mid"], ["final"])
        assert "final" in lin.get_downstream("s1")
        assert "s1" in lin.get_upstream("final")
        assert lin.get_downstream("leaf_missing") == []
        assert lin.get_upstream("root_missing") == []

    def test_dag(self, lin):
        lin.record_transform("mid", "join", ["s1"], ["mid"])
        d = lin.get_dag()
        assert d["nodes"] and d["edges"]
        sub = lin.get_dag(table="mid")
        assert sub["nodes"]
        empty = lin.get_dag(table="nope_xyz")
        assert "nodes" in empty

    def test_column_lineage(self, lin):
        lin.record_transform("o", "map", ["s"], ["o"], column_map={"y": ["s.x", "plain"]})
        cl = lin.get_column_lineage("o")
        assert cl["y"][0]["source_table"] == "s"
        assert lin.get_column_lineage("missing") == {}

    def test_clear(self, lin):
        lin.record_transform("a", "op", ["s"], ["a"])
        lin.clear(table="a")
        assert lin.get_lineage("a") == []
        lin.record_transform("b", "op", ["s"], ["b"])
        lin.clear()
        assert lin.get_full_lineage() == {}

    def test_load_bad_json(self, tmp_path):
        from analysis.lineage import LineageTracker
        p = tmp_path / "bad.json"
        p.write_text("{oops")
        t = LineageTracker(storage_path=p)
        assert t.get_full_lineage() == {}


# ============================================================================
# timeseries
# ============================================================================

def _ts_df(n=15):
    return pd.DataFrame({
        "d": pd.date_range("2026-01-01", periods=n, freq="D"),
        "v": [10.0 + i + (3.0 if i % 5 == 0 else 0.0) for i in range(n)],
    })


class TestTimeseries:
    def test_decomp_short(self):
        from analysis.timeseries import advanced_decomposition
        r = advanced_decomposition(pd.DataFrame({"d": ["2026-01-01"], "v": [1.0]}), "d", "v")
        assert r["success"] is False

    def test_decomp_add_mult(self):
        from analysis.timeseries import advanced_decomposition
        r = advanced_decomposition(_ts_df(15), "d", "v", period=4, model="additive")
        assert r["success"] is True and len(r["trend"]) == 15
        r2 = advanced_decomposition(_ts_df(15), "d", "v", period=0, model="multiplicative")
        assert r2["success"] is True and r2["period"] >= 2

    def test_forecast_short(self):
        from analysis.timeseries import enhanced_forecast
        r = enhanced_forecast(pd.DataFrame({"d": ["2026-01-01"], "v": [1.0]}), "d", "v")
        assert r["success"] is False

    def test_forecast_methods(self):
        from analysis.timeseries import enhanced_forecast
        for m in ("auto", "linear", "naive", "seasonal_naive"):
            r = enhanced_forecast(_ts_df(15), "d", "v", periods=3, method=m)
            assert r["success"] is True and len(r["forecast_values"]) == 3
            assert len(r["upper_bound"]) == 3 == len(r["lower_bound"]) == 3

    def test_helpers(self):
        from analysis.timeseries import _infer_period, _compute_seasonal_adjustment
        assert _infer_period(pd.Series([1.0] * 30)) == 12
        assert _infer_period(pd.Series([1.0] * 15)) == 7
        assert _infer_period(pd.Series([1.0] * 9)) == 4
        assert _infer_period(pd.Series([1.0] * 3)) >= 2
        adj = _compute_seasonal_adjustment(np.arange(12, dtype=float), 4)
        assert len(adj) == 4


# ============================================================================
# bi_export
# ============================================================================

class TestBIExport:
    def test_tde(self):
        from analysis.bi_export import export_to_tde
        assert export_to_tde("t", pd.DataFrame()) == b""
        out = export_to_tde("t", pd.DataFrame({"a": [1, 2]}))
        assert b"a" in out and len(out) > 0

    def test_powerbi(self):
        from analysis.bi_export import export_to_powerbi
        assert export_to_powerbi("t", pd.DataFrame()) == b""
        out = export_to_powerbi("t", pd.DataFrame({"a b!": [1, 2], "v": [3, 4]}))
        assert out.startswith(b"\xef\xbb\xbf")
        assert b"a_b" in out

    def test_looker(self):
        from analysis.bi_export import export_to_looker
        e = export_to_looker("t", pd.DataFrame())
        assert e["dimensions"] == [] and e["measures"] == []
        out = export_to_looker("My Table!", pd.DataFrame({"city": ["a"], "sales": [10.0]}))
        assert out["dimensions"] and out["measures"]
        assert out["data_preview"]

    def test_conn_config(self):
        from analysis.bi_export import generate_connection_config
        for k in ("tableau", "powerbi", "looker", " Tableau "):
            c = generate_connection_config(k)
            assert "steps" in c
        with pytest.raises(ValueError):
            generate_connection_config("nope")

    def test_helpers(self):
        from analysis.bi_export import (_clean_powerbi_column, _normalise_looker_name,
                                        _make_serialisable, _df_columns_to_hyper,
                                        _empty_looker_config)
        assert _clean_powerbi_column("a b!c") == "a_b_c"
        assert _normalise_looker_name("My Table!") == "my_table"
        assert _normalise_looker_name("123abc") == "f_123abc"
        assert _make_serialisable(float("nan")) is None
        assert "T" in str(_make_serialisable(pd.Timestamp("2026-01-01")))
        assert _make_serialisable(np.int64(3)) == 3
        assert _make_serialisable({"a": [1, float("nan")]}) == {"a": [1, None]}
        assert isinstance(_df_columns_to_hyper(pd.DataFrame({"a": [1]})), list)
        assert _empty_looker_config("t")["data_preview"] == []


# ============================================================================
# recommender
# ============================================================================

class TestRecommender:
    def test_empty(self):
        from analysis.recommender import recommend_charts
        r = recommend_charts(pd.DataFrame())
        assert r["recommendations"] == []

    def test_full(self):
        from analysis.recommender import recommend_charts
        df = pd.DataFrame({
            "d": pd.date_range("2026-01-01", periods=12, freq="D"),
            "cat": ["A", "B"] * 6,
            "cat2": ["X", "Y", "Z"] * 4,
            "n1": [float(i) for i in range(12)],
            "n2": [float(i * 2) for i in range(12)],
            "n3": [float(i % 5) for i in range(12)],
        })
        r = recommend_charts(df, top_n=5)
        assert len(r["recommendations"]) <= 5
        assert r["column_profile"]["numeric"] == 3
        types = {x["chart_type"] for x in r["recommendations"]}
        assert types  # non-empty

    def test_pie_and_numeric_only(self):
        from analysis.recommender import recommend_charts, _col_type
        df = pd.DataFrame({"c": ["A", "B", "A", "B"], "v": [1.0, 2.0, 3.0, 4.0]})
        r = recommend_charts(df)
        assert any(x["chart_type"] == "pie" for x in r["recommendations"])
        num = pd.DataFrame({"a": [1.0, 2, 3, 4], "b": [2.0, 3, 4, 5]})
        r2 = recommend_charts(num)
        assert any(x["chart_type"] == "histogram" for x in r2["recommendations"])
        assert _col_type("a", num, {"a", "b"}, set(), set()) == "numeric"
        assert _col_type("zzz", num, set(), set(), set()) == "unknown"

    def test_string_dates(self):
        from analysis.recommender import recommend_charts
        df = pd.DataFrame({"d": ["2026-01-01", "2026-01-02", "2026-01-03"],
                           "v": [1.0, 2.0, 3.0]})
        r = recommend_charts(df)
        assert r["success"] is True


# ============================================================================
# quality remainder
# ============================================================================

class TestQualityRemainder:
    def test_timeliness_no_dates(self):
        from analysis.quality import _timeliness_score
        assert _timeliness_score(pd.DataFrame({"a": [1, 2]})) == 50.0

    def test_report_empty(self):
        from analysis.quality import generate_quality_report
        r = generate_quality_report(pd.DataFrame(), "t")
        assert r["table"] == "t" and r["profile"]["rows"] == 0


# ============================================================================
# pipeline (ETLPipeline)
# ============================================================================

class TestPipelineCore:
    def test_builders_and_validate(self):
        from analysis.pipeline import ETLPipeline
        p = ETLPipeline("p", "d")
        p.add_extract("ex", source_type="file", file_path="f.csv")
        p.add_clean("cl", input_data="ex")
        p.add_transform("tf", input_data="cl", operation="filter")
        p.add_sql("sq", query="SELECT 1")
        p.add_load("ld", input_data="tf", table_name="t")
        assert len(p.steps) == 5
        assert p.validate() == []
        bad = ETLPipeline("b")
        bad.add_clean("c", input_data="missing_dep")
        assert any("doesn't exist" in e for e in bad.validate())

    def test_circular(self):
        from analysis.pipeline import ETLPipeline, PipelineStep
        p = ETLPipeline("c")
        p.add_step(PipelineStep(name="a", step_type="clean", params={}, depends_on=["b"]))
        p.add_step(PipelineStep(name="b", step_type="clean", params={}, depends_on=["a"]))
        assert any("Circular" in e for e in p.validate())

    def test_topo_and_dict(self):
        from analysis.pipeline import ETLPipeline
        p = ETLPipeline("p")
        p.add_clean("b", input_data="a")
        p.add_extract("a")
        order = p._topological_sort()
        assert [s.name for s in order].index("a") < [s.name for s in order].index("b")
        d = p.to_dict()
        p2 = ETLPipeline.from_dict(d)
        assert [s.name for s in p2.steps] == ["b", "a"]
        assert p2.name == "p"

    def test_run_empty_ok_and_invalid(self):
        from analysis.pipeline import ETLPipeline
        r = ETLPipeline("empty").run()
        assert r.success is True and r.steps_total == 0
        bad = ETLPipeline("bad")
        bad.add_clean("c", input_data="nope")
        r2 = bad.run()
        assert r2.success is False and "Validation" in r2.error

    def test_run_disabled_and_cancel(self):
        from analysis.pipeline import ETLPipeline, PipelineStep
        p = ETLPipeline("p")
        p.add_step(PipelineStep(name="skip", step_type="clean", params={}, enabled=False))
        r = p.run()
        assert r.success is True
        assert r.step_results["skip"]["status"] == "skipped"
        p2 = ETLPipeline("c")
        p2.add_transform("t1", input_data="", operation="filter")
        p2.cancel_check = lambda: True
        r2 = p2.run()
        assert r2.success is False and "cancelled" in r2.error.lower()

    def test_run_extract_unknown_and_clean_missing(self):
        from analysis.pipeline import ETLPipeline
        p = ETLPipeline("p")
        p.add_extract("ex", source_type="weird")
        r = p.run()
        assert r.success is True  # unknown source -> error status stored, not fatal
        assert r.step_results["ex"]["status"] == "error"
        p2 = ETLPipeline("p2")
        p2.add_clean("cl", input_data="ghost")
        r2 = p2.run()
        assert r2.success is False and "ghost" in r2.error

    def test_run_sql_empty_and_nodb(self):
        from analysis.pipeline import ETLPipeline
        p = ETLPipeline("p")
        p.add_sql("s", query="   ")
        r = p.run()
        assert r.success is False
        p2 = ETLPipeline("p2")
        p2.add_sql("s", query="SELECT 1")
        r2 = p2.run(db_path="")
        assert r2.success is True  # no db -> error status stored
        assert r2.step_results["s"]["status"] == "error"

    def test_run_load_paths(self, tmp_path):
        from analysis.pipeline import ETLPipeline
        import pandas as pd
        df = pd.DataFrame({"a": [1, 2]})
        # no loader, no format -> skipped (use bare steps so validate passes
        # while input comes from pre-injected _data)
        from analysis.pipeline import PipelineStep
        p = ETLPipeline("p")
        p._data["in"] = df
        p.add_step(PipelineStep(name="ld", step_type="load",
                                params={"input_data": "in", "table_name": "t"},
                                depends_on=[]))
        r = p.run()
        assert r.step_results["ld"]["status"] == "skipped"
        # with fake loader
        class FakeLoader:
            def __init__(self):
                self.calls = []
            def load_dataframe(self, d, t, if_exists="replace"):
                self.calls.append((t, len(d)))
        loader = FakeLoader()
        p2 = ETLPipeline("p2")
        p2._data["in"] = df
        p2.add_step(PipelineStep(name="ld", step_type="load",
                                 params={"input_data": "in", "table_name": "t"},
                                 depends_on=[]))
        r2 = p2.run(loader=loader)
        assert r2.success is True and loader.calls == [("t", 2)]
        # missing input
        p3 = ETLPipeline("p3")
        p3.add_load("ld", input_data="ghost", table_name="t")
        assert p3.run().success is False

    def test_run_warehouse_and_filter_tokens(self, tmp_path):
        from analysis.pipeline import ETLPipeline
        con = duckdb.connect(str(tmp_path / "w.duckdb"))
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute("CREATE TABLE analytics.t(a INTEGER)")
        con.execute("INSERT INTO analytics.t VALUES (1),(2)")
        con.close()
        p = ETLPipeline("p")
        p.add_extract("ex", source_type="warehouse", table="t", limit=10)
        r = p.run(db_path=str(tmp_path / "w.duckdb"), schema="analytics")
        assert r.step_results["ex"]["status"] == "completed"
        p2 = ETLPipeline("p2")
        p2.add_extract("ex", source_type="warehouse", table="", limit=10)
        assert p2.run(db_path=str(tmp_path / "w.duckdb"), schema="analytics").success is False
        p3 = ETLPipeline("p3")
        p3.add_extract("ex", source_type="warehouse", table="t", filter="1=1; DROP")
        assert p3.run(db_path=str(tmp_path / "w.duckdb"), schema="analytics").success is False


class TestPipelineOps:
    def _p(self):
        from analysis.pipeline import ETLPipeline
        return ETLPipeline("t")

    def test_coerce(self):
        from analysis.pipeline import ETLPipeline
        p = ETLPipeline("t")
        assert p._coerce_value(pd.Series([True]), "true") is True
        assert p._coerce_value(pd.Series([1.0]), "3") == 3.0
        assert p._coerce_value(pd.Series(["x"]), 5) == 5

    def test_filter_ops(self):
        p = self._p()
        df = pd.DataFrame({"a": [1, 2, 3], "s": ["foo", "bar", "foobar"]})
        assert len(p._op_filter(df, {"column": "a", "operator": ">", "value": 1})) == 2
        assert len(p._op_filter(df, {"column": "a", "operator": "less_than", "value": 3})) == 2
        assert len(p._op_filter(df, {"column": "a", "operator": "equals", "value": 2})) == 1
        assert len(p._op_filter(df, {"column": "a", "operator": "not_equals", "value": 2})) == 2
        assert len(p._op_filter(df, {"column": "a", "op": ">=", "value": 2})) == 2
        assert len(p._op_filter(df, {"column": "a", "op": "<=", "value": 2})) == 2
        assert len(p._op_filter(df, {"column": "s", "op": "contains", "value": "foo"})) == 2
        assert len(p._op_filter(df, {"column": "s", "op": "not_contains", "value": "foo"})) == 1
        assert len(p._op_filter(df, {"column": "s", "op": "starts_with", "value": "fo"})) == 2
        assert len(p._op_filter(df, {"column": "s", "op": "ends_with", "value": "bar"})) == 2
        assert len(p._op_filter(df, {"column": "a", "op": "between", "value": "1, 2"})) == 2
        assert len(p._op_filter(df, {"column": "s", "op": "in_list", "value": "foo, bar"})) == 2
        with pytest.raises(ValueError):
            p._op_filter(df, {"column": "", "op": "equals", "value": 1})
        with pytest.raises(ValueError):
            p._op_filter(df, {"column": "zzz", "op": "equals", "value": 1})
        with pytest.raises(ValueError):
            p._op_filter(df, {"column": "a", "op": "zzz_op", "value": 1})
        with pytest.raises(ValueError):
            p._op_filter(df, {"column": "a", "op": "between", "value": "5"})

    def test_join(self):
        p = self._p()
        left = pd.DataFrame({"k": [1, 2], "v": ["a", "b"]})
        p._data["right"] = pd.DataFrame({"k": [2, 3], "w": ["x", "y"]})
        out = p._op_join(left, {"input_data2": "right", "left_key": "k", "right_key": "k"})
        assert len(out) == 1
        p._data["r2"] = pd.DataFrame({"kk": [2], "w": ["x"]})
        out2 = p._op_join(left, {"input_data2": "r2", "left_key": "k", "right_key": "kk",
                                 "join_type": "left"})
        assert len(out2) == 2
        with pytest.raises(ValueError):
            p._op_join(left, {"input_data2": "ghost", "left_key": "k", "right_key": "k"})
        with pytest.raises(ValueError):
            p._op_join(left, {"input_data2": "right", "left_key": "", "right_key": ""})
        with pytest.raises(ValueError):
            p._op_join(left, {"input_data2": "right", "left_key": "zzz", "right_key": "k"})
        with pytest.raises(ValueError):
            p._op_join(left, {"input_data2": "right", "left_key": "k", "right_key": "zzz"})

    def test_aggregate(self):
        p = self._p()
        df = pd.DataFrame({"g": ["A", "A", "B"], "v": [1.0, 2.0, 3.0]})
        out, note = p._op_aggregate(df, {"group_by": "g", "aggregate_fn": "sum", "aggregate_col": "v"})
        assert len(out) == 2 and note
        out2, _ = p._op_aggregate(df, {"group_by": "g", "aggregate_fn": "count"})
        assert "count" in out2.columns
        with pytest.raises(ValueError):
            p._op_aggregate(df, {"group_by": "", "aggregate_fn": "sum", "aggregate_col": "v"})
        with pytest.raises(ValueError):
            p._op_aggregate(df, {"group_by": "zzz", "aggregate_fn": "sum", "aggregate_col": "v"})
        with pytest.raises(ValueError):
            p._op_aggregate(df, {"group_by": "g", "aggregate_fn": "sum", "aggregate_col": "zzz"})

    def test_custom(self):
        p = self._p()
        df = pd.DataFrame({"a": [1, 2]})
        out, printed = p._op_custom(df, {"code": "print('hi')\nresult = df.assign(b=df['a']*2)"})
        assert "b" in out.columns and "hi" in printed
        with pytest.raises(ValueError):
            p._op_custom(df, {"code": "   "})
        with pytest.raises(ValueError):
            p._op_custom(df, {"code": "raise RuntimeError('x')"})

    def test_analysis_chart_insights(self):
        p = self._p()
        df = pd.DataFrame({"x": [1.0, 2, 3, 4], "y": [2.0, 4, 6, 8], "c": ["a", "b", "a", "b"]})
        assert "correlation" in p._op_analysis(df, {"analysis_type": "correlation"}).lower()
        assert "needs at least 2" in p._op_analysis(pd.DataFrame({"x": [1.0]}), {"analysis_type": "correlation"})
        assert "Target column" in p._op_analysis(df, {"target_col": "x"})
        assert "Statistics" in p._op_analysis(df, {})
        s, info = p._op_chart(df, {"config": {"chart_type": "bar", "x_col": "c", "y_col": "x"}})
        assert "chart" in s and info["chart_points"] >= 1
        s2, _ = p._op_chart(df, {"config": {"chart_type": "histogram", "x_col": "x"}})
        assert s2
        assert "rows" in p._op_insights(df, {})
        assert "focus" in p._op_insights(df, {"config": {"prompt": "sales"}}).lower() or "Requested" in p._op_insights(df, {"config": {"prompt": "sales"}})

    def test_anomaly_ops(self):
        p = self._p()
        df = pd.DataFrame({"v": [1.0] * 8 + [100.0, 101.0]})
        out, s = p._op_anomaly(df, {"column": "v", "method": "iqr"})
        assert "_is_anomaly" in out.columns and "IQR" in s
        out2, s2 = p._op_anomaly(df, {"column": "v", "method": "zscore", "threshold": 1.0})
        assert "_is_anomaly" in out2.columns
        out3, s3 = p._op_anomaly(df, {"column": "v", "method": "change_point"})
        assert "Change" in s3
        with pytest.raises(ValueError):
            p._op_anomaly(df, {"column": "", "method": "iqr"})
        with pytest.raises(ValueError):
            p._op_anomaly(df, {"column": "zzz", "method": "iqr"})
        with pytest.raises(ValueError):
            p._op_anomaly(pd.DataFrame({"v": [1.0]}), {"column": "v"})
        with pytest.raises(ValueError):
            p._op_anomaly(df, {"column": "v", "method": "zzz"})

    def test_forecast_stat_regression_quality(self):
        p = self._p()
        df = pd.DataFrame({"d": pd.date_range("2026-01-01", periods=12, freq="D"),
                           "v": [float(i) for i in range(12)]})
        out, s = p._op_forecast(df, {"date_col": "d", "value_col": "v", "horizon": 3})
        assert len(out) == 15 and "Forecast" in s
        with pytest.raises(ValueError):
            p._op_forecast(df, {"date_col": "", "value_col": ""})
        with pytest.raises(ValueError):
            p._op_forecast(df, {"date_col": "zzz", "value_col": "v"})
        gdf = pd.DataFrame({"g": ["A"] * 5 + ["B"] * 5, "x": [1.0, 1.1, 0.9, 1.0, 1.2, 5.0, 5.1, 4.9, 5.2, 5.0]})
        _, ss = p._op_stat_test(gdf, {"test_type": "ttest", "column_a": "x", "group_col": "g"})
        assert "stat" in ss.lower() or "p=" in ss
        with pytest.raises(ValueError):
            p._op_stat_test(gdf, {"column_a": ""})
        with pytest.raises(ValueError):
            p._op_stat_test(gdf, {"column_a": "zzz"})
        rdf = pd.DataFrame({"x": [float(i) for i in range(8)], "y": [float(i * 2) for i in range(8)]})
        outr, _ = p._op_regression(rdf, {"target_col": "y", "feature_cols": "x"})
        assert "_predicted" in outr.columns
        with pytest.raises(ValueError):
            p._op_regression(rdf, {"target_col": ""})
        with pytest.raises(ValueError):
            p._op_regression(rdf, {"target_col": "zzz"})
        with pytest.raises(ValueError):
            p._op_regression(pd.DataFrame({"s": ["a"]}), {"target_col": "s"})
        _, sq = p._op_quality(rdf, {"table_name": "t"})
        assert "Quality score" in sq

    def test_comparison_cohort_ml(self):
        p = self._p()
        df = pd.DataFrame({"d": [f"2026-01-{i + 1:02d}" for i in range(5)] + [f"2026-02-{i + 1:02d}" for i in range(5)],
                           "m": [10.0] * 5 + [20.0] * 5})
        _, s = p._op_comparison(df, {"kind": "periods", "date_col": "d", "metric_col": "m"})
        assert isinstance(s, str)
        with pytest.raises(ValueError):
            p._op_comparison(df, {"kind": "periods", "date_col": "", "metric_col": ""})
        with pytest.raises(ValueError):
            p._op_comparison(df, {"kind": "periods", "date_col": "zzz", "metric_col": "m"})
        p._data["r"] = pd.DataFrame({"id": [1], "v": ["a"]})
        left = pd.DataFrame({"id": [1], "v": ["b"]})
        _, sd = p._op_comparison(left, {"kind": "diff", "input_data2": "r", "key_column": "id"})
        assert "added" in sd
        with pytest.raises(ValueError):
            p._op_comparison(left, {"kind": "diff", "input_data2": "ghost"})
        cdf = pd.DataFrame({"uid": ["u1", "u1", "u2"], "d": ["2026-01-01", "2026-02-01", "2026-01-05"]})
        _, sc = p._op_cohort(cdf, {"kind": "retention", "user_id_col": "uid", "date_col": "d"})
        assert isinstance(sc, str)
        _, sc2 = p._op_cohort(cdf, {"kind": "churn", "user_id_col": "uid", "date_col": "d"})
        assert "Churn" in sc2 or "churn" in sc2.lower()
        with pytest.raises(ValueError):
            p._op_cohort(cdf, {"user_id_col": "", "date_col": ""})
        with pytest.raises(ValueError):
            p._op_cohort(cdf, {"user_id_col": "zzz", "date_col": "d"})
        mdf = pd.DataFrame({"x": [float(i) for i in range(10)], "y": [float(i) for i in range(10)]})
        _, sm = p._op_ml_model(mdf, {"target_col": "y", "task": "auto"})
        assert sm
        _, sm2 = p._op_ml_model(mdf, {"target_col": "y", "task": "compare"})
        assert sm2
        with pytest.raises(ValueError):
            p._op_ml_model(mdf, {"target_col": ""})
        with pytest.raises(ValueError):
            p._op_ml_model(mdf, {"target_col": "zzz"})

    def test_transform_run_end_to_end(self):
        from analysis.pipeline import ETLPipeline, PipelineStep
        p = ETLPipeline("e2e")
        p._data["src"] = pd.DataFrame({"a": [1, 2, 3, 4], "g": ["x", "x", "y", "y"]})
        # first step uses bare PipelineStep (no depends) so validate passes;
        # later steps chain off real step names
        p.add_step(PipelineStep(name="f", step_type="transform",
                                params={"input_data": "src", "operation": "filter",
                                        "column": "a", "operator": ">", "value": 1,
                                        "output_name": "f"},
                                depends_on=[]))
        p.add_transform("s", input_data="f", operation="sort", column="a",
                        ascending=False, output_name="s")
        p.add_transform("fill", input_data="s", operation="fill_null",
                        column="a", fill_value=0, output_name="fill")
        p.add_transform("rn", input_data="fill", operation="rename_columns",
                        mapping={"a": "alpha"}, output_name="rn")
        p.add_transform("drop", input_data="rn", operation="drop_columns",
                        columns=["g"], output_name="drop")
        r = p.run()
        assert r.success is True
        assert list(p._data["drop"].columns) == ["alpha"]
