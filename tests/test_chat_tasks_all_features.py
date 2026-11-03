"""Chat Pro feature-menu task operations (planner + executors).

Covers the sidebar features that were previously only reachable through
dedicated pages: anomalies, quality trend/snapshot, stats, compare, lineage,
merge, notifications, marketplace, org tree, catalog, pipelines, workflows,
exports, and connections.
"""

import json
from types import SimpleNamespace

import pandas as pd
import pytest

from agent import tools_features
from agent.planner import Planner


class User(SimpleNamespace):
    def __init__(self, role="user", user_id="u1", scopes=None):
        super().__init__(
            role=role,
            user_id=user_id,
            username=user_id,
            scopes=scopes or set(),
        )


def _plan(question, tables):
    planner = Planner()
    if tables:
        lines = []
        for table in tables:
            lines.append(f"TABLE analytics.{table} (10 rows):")
            lines.extend(
                [
                    "  - id: INTEGER",
                    "  - amount: DOUBLE",
                    "  - region: VARCHAR",
                    "  - sold_at: TIMESTAMP",
                ]
            )
        planner.set_schema_context("\n".join(lines))
    return planner.create_plan(question, available_tables=list(tables))


def _task_step(plan):
    assert plan.steps, f"expected a task plan, got: {plan.reasoning}"
    step = plan.steps[0]
    assert step.tool.value == "task"
    return step.params


@pytest.fixture
def seeded(billing_db):
    loader = billing_db
    ids = list(range(1, 13))
    sales = pd.DataFrame(
        {
            "id": ids,
            "product": ["a", "b"] * 6,
            "region": ["west", "east"] * 6,
            "amount": [10.0, 12.0, 30.0, 32.0, 11.0, 31.0,
                       10.0, 12.0, 11.0, 31.0, 10.0, 500.0],
            "score": [1.0, 2.0, 8.0, 9.0, 1.5, 8.5,
                      1.0, 2.0, 1.5, 8.5, 1.0, 90.0],
            "sold_at": pd.to_datetime(
                ["2026-01-01", "2026-01-02", "2026-02-01", "2026-02-02",
                 "2026-03-01", "2026-03-02", "2026-01-03", "2026-01-04",
                 "2026-02-03", "2026-02-04", "2026-03-03", "2026-03-04"]
            ),
        }
    )
    regions = pd.DataFrame({"id": ids, "area": ["n", "s"] * 6})
    after = sales.copy()
    after.loc[0, "amount"] = 99.0
    loader.load_dataframe(sales, "sales", if_exists="replace")
    loader.load_dataframe(regions, "regions", if_exists="replace")
    loader.load_dataframe(after, "sales_after", if_exists="replace")
    return loader


@pytest.fixture
def entitlements(monkeypatch):
    allowed = set()

    def fake_has_feature(user_id, feature_key, plan=None):
        return feature_key in allowed

    monkeypatch.setattr(
        "api.billing.entitlements.has_feature", fake_has_feature
    )
    return allowed


@pytest.fixture
def lineage_path(tmp_path, monkeypatch):
    path = tmp_path / "lineage.json"
    monkeypatch.setattr(
        "analysis.lineage.lineage_storage_path", lambda tenant_id=None: path
    )
    return path


@pytest.fixture
def trends_dir(tmp_path, monkeypatch):
    path = tmp_path / "quality_trends"
    path.mkdir()
    monkeypatch.setenv("DATAAGENT_QUALITY_TRENDS_DIR", str(path))
    return path


# ── planner routing ──────────────────────────────────────────────


def test_planner_anomalies():
    params = _task_step(_plan("detect anomalies in sales", ["sales"]))
    assert params["operation"] == "anomalies"
    assert params["table"] == "sales"


def test_planner_quality_trend():
    params = _task_step(_plan("show the quality trend for sales", ["sales"]))
    assert params["operation"] == "quality_trend"
    assert params["table"] == "sales"


def test_planner_quality_snapshot():
    params = _task_step(_plan("record a quality snapshot for sales", ["sales"]))
    assert params["operation"] == "quality_snapshot"


def test_planner_stats():
    params = _task_step(
        _plan("run a t-test on sales comparing amount by region", ["sales"])
    )
    assert params["operation"] == "stats"
    assert params["test_type"] == "ttest"
    assert params["column_a"] == "amount"
    assert params["group_col"] == "region"


def test_planner_compare_two_tables():
    params = _task_step(
        _plan("compare sales with sales_after", ["sales", "sales_after"])
    )
    assert params["operation"] == "compare_tables"
    assert params["table_before"] == "sales"
    assert params["table_after"] == "sales_after"


def test_planner_compare_periods():
    params = _task_step(_plan("compare periods of sales by month", ["sales"]))
    assert params["operation"] == "compare_periods"
    assert params["period_type"] == "month"


def test_planner_compare_needs_two_tables():
    plan = _plan("compare revenue by region", ["sales"])
    assert not (plan.steps and plan.steps[0].tool.value == "task")


def test_planner_lineage():
    params = _task_step(_plan("show lineage of sales", ["sales"]))
    assert params["operation"] == "lineage"


def test_planner_merge():
    params = _task_step(
        _plan("merge sales and regions on id", ["sales", "regions"])
    )
    assert params["operation"] == "merge_tables"
    assert params["left_table"] == "sales"
    assert params["right_table"] == "regions"
    assert params["left_key"] == "id"


def test_planner_notifications():
    assert _task_step(_plan("show my notifications", []))["operation"] == (
        "notifications_list"
    )
    assert _task_step(_plan("mark all notifications read", []))["operation"] == (
        "notifications_read"
    )


def test_planner_marketplace():
    assert _task_step(_plan("list marketplace products", []))["operation"] == (
        "marketplace_products"
    )
    assert _task_step(
        _plan("request access to product prod-1 in the marketplace", [])
    )["operation"] == "marketplace_request"


def test_planner_org_tree():
    assert _task_step(_plan("show the org tree", []))["operation"] == "org_tree"


def test_planner_catalog():
    assert _task_step(_plan("search the catalog for revenue", []))[
        "operation"
    ] == "catalog_search"
    assert _task_step(_plan("sync the data catalog", []))["operation"] == (
        "catalog_sync"
    )
    assert _task_step(_plan("certify asset asset-1 in the catalog", []))[
        "operation"
    ] == "catalog_certify"
    assert _task_step(_plan("add a business term revenue to the catalog", []))[
        "operation"
    ] == "catalog_add_term"
    assert _task_step(_plan("add a metric margin to the catalog", []))[
        "operation"
    ] == "catalog_add_metric"


def test_planner_pipeline():
    params = _task_step(_plan("run pipeline nightly_load", []))
    assert params["operation"] == "pipeline_run"
    assert params["name"] == "nightly_load"


def test_planner_workflows():
    assert _task_step(_plan("show pending workflow approvals", []))[
        "operation"
    ] == "workflow_pending"
    assert _task_step(_plan("approve workflow instance wf-1", []))[
        "operation"
    ] == "workflow_approve"


def test_planner_export():
    params = _task_step(_plan("export sales as csv", ["sales"]))
    assert params["operation"] == "export_table"
    assert params["fmt"] == "csv"


def test_planner_connections():
    assert _task_step(_plan("list my data connections", []))["operation"] == (
        "connections_list"
    )


# ── executors ────────────────────────────────────────────────────


def test_detect_anomalies(seeded, entitlements):
    result = tools_features.detect_anomalies("sales", user=User())
    assert result.success, result.error
    assert result.data["summary"]["total_rows"] == 12
    assert "amount" in result.data["anomalies"]


def test_quality_snapshot_and_trend(seeded, entitlements, trends_dir):
    snapshot = tools_features.record_quality_snapshot("sales", user=User())
    assert snapshot.success, snapshot.error
    trend = tools_features.quality_trend("sales", user=User())
    assert trend.success, trend.error
    assert trend.data["total_snapshots"] == 1
    assert trend.data["trend"][0]["overall_score"] >= 0


def test_run_stats(seeded, entitlements):
    result = tools_features.run_stats(
        "sales",
        user=User(),
        column_a="amount",
        group_col="region",
        test_type="ttest",
    )
    assert result.success, result.error
    assert result.data["p_value"] is not None


def test_compare_tables(seeded, entitlements):
    result = tools_features.compare_tables(
        "sales", "sales_after", user=User(), key_column="id"
    )
    assert result.success, result.error
    assert result.data["rows_changed"] == 1


def test_compare_periods(seeded, entitlements):
    result = tools_features.compare_periods(
        "sales",
        user=User(),
        date_col="sold_at",
        metric_col="amount",
        period_type="month",
    )
    assert result.success, result.error
    assert result.data["periods"]


def test_lineage_lookup(seeded, entitlements, lineage_path):
    from analysis.lineage import LineageTracker

    LineageTracker(tenant_id=None).record_transform(
        table="sales",
        operation="clean",
        inputs=["raw_sales"],
        outputs=["sales"],
    )
    result = tools_features.lineage_lookup("sales", user=User())
    assert result.success, result.error
    assert result.data["lineage"][0]["operation"] == "clean"


def test_lineage_lookup_message_lists_record_details(seeded, entitlements, lineage_path):
    from analysis.lineage import LineageTracker

    LineageTracker(tenant_id=None).record_transform(
        table="sales",
        operation="merge",
        inputs=["raw_sales", "regions"],
        outputs=["sales"],
        description="inner join on id",
        metadata={"rows": 12},
    )
    result = tools_features.lineage_lookup("sales", user=User())
    assert result.success, result.error
    message = result.message
    assert "**sales** has 1 lineage record(s)" in message
    assert "merge" in message
    assert "raw_sales, regions → sales" in message
    assert "inner join on id" in message
    assert "12 rows" in message


def test_merge_tables_infers_shared_key(seeded, entitlements, lineage_path):
    result = tools_features.merge_tables(
        "sales", "regions", user=User(), output_table="cpx_merge_test"
    )
    assert result.success, result.error
    assert result.data["key"] == "id"
    assert result.data["rows"] == 12
    assert "area" in result.data["columns"]
    assert lineage_path.exists()


def test_notifications(seeded, entitlements):
    from api.notifications import get_notification_manager

    manager = get_notification_manager()
    manager.send_notification("u1", "info", "Hello", "Body")
    listed = tools_features.notifications_list(user=User())
    assert listed.success, listed.error
    assert listed.data["notifications"]
    marked = tools_features.notifications_mark_read(user=User(), all=True)
    assert marked.success, marked.error
    assert marked.data["marked"] >= 1


def test_marketplace_products_requires_entitlement(seeded, entitlements):
    denied = tools_features.marketplace_products(user=User())
    assert not denied.success
    assert "plan" in denied.error.lower()
    entitlements.add("marketplace")
    allowed = tools_features.marketplace_products(user=User())
    assert allowed.success, allowed.error


def test_org_tree_requires_entitlement(seeded, entitlements):
    denied = tools_features.org_tree(user=User())
    assert not denied.success
    entitlements.add("organization")
    allowed = tools_features.org_tree(user=User())
    assert allowed.success, allowed.error


def test_catalog_search_requires_permission(seeded, entitlements):
    viewer = User(role="viewer")
    denied = tools_features.catalog_search("revenue", user=viewer)
    assert not denied.success
    allowed = tools_features.catalog_search("revenue", user=User())
    assert allowed.success, allowed.error


def test_catalog_sync_fails_closed(seeded, entitlements):
    denied = tools_features.catalog_sync(user=User())
    assert not denied.success
    assert "plan" in denied.error.lower()


def test_pipeline_run_fails_closed(seeded, entitlements):
    denied = tools_features.pipeline_run("nightly", user=User())
    assert not denied.success
    assert "plan" in denied.error.lower()


def test_workflow_pending_fails_closed(seeded, entitlements):
    denied = tools_features.workflow_pending(user=User())
    assert not denied.success
    assert "plan" in denied.error.lower()


def test_export_requires_entitlement(seeded, entitlements):
    denied = tools_features.export_table("sales", user=User())
    assert not denied.success
    assert "plan" in denied.error.lower()
    entitlements.add("export_page")
    allowed = tools_features.export_table("sales", user=User(), fmt="csv")
    assert allowed.success, allowed.error
    assert allowed.data["url"] == "/export/csv/sales"


def test_connections_fail_closed(seeded, entitlements):
    result = tools_features.connections_list(user=User())
    assert not result.success
    assert "admin" in result.error.lower()


def test_merge_refuses_viewer(seeded, entitlements, lineage_path):
    result = tools_features.merge_tables(
        "sales", "regions", user=User(role="viewer")
    )
    assert not result.success


# ── response detail ─────────────────────────────────────────────


def test_anomalies_message_lists_columns_and_examples(seeded, entitlements):
    result = tools_features.detect_anomalies("sales", user=User())
    assert result.success, result.error
    assert "**amount**" in result.message
    assert "outlier(s)" in result.message
    assert "e.g. 500" in result.message


def test_quality_snapshot_message_lists_dimensions(seeded, entitlements, trends_dir):
    result = tools_features.record_quality_snapshot("sales", user=User())
    assert result.success, result.error
    assert "Completeness:" in result.message
    assert "Rows: 12" in result.message
    assert "Columns: 6" in result.message


def test_quality_trend_message_lists_scores(seeded, entitlements, trends_dir):
    tools_features.record_quality_snapshot("sales", user=User())
    tools_features.record_quality_snapshot("sales", user=User())
    result = tools_features.quality_trend("sales", user=User())
    assert result.success, result.error
    assert "/100" in result.message
    assert "Latest:" in result.message


def test_run_stats_message_lists_groups(seeded, entitlements):
    result = tools_features.run_stats(
        "sales",
        user=User(),
        column_a="amount",
        group_col="region",
        test_type="ttest",
    )
    assert result.success, result.error
    assert "Effect size: Cohen's d" in result.message
    assert "- **west**: mean" in result.message
    assert "- **east**: mean" in result.message


def test_compare_tables_message_lists_changed_cells(seeded, entitlements):
    result = tools_features.compare_tables(
        "sales", "sales_after", user=User(), key_column="id"
    )
    assert result.success, result.error
    assert "- **amount**" in result.message
    assert "1: 10 → 99" in result.message


def test_compare_periods_message_lists_changes(seeded, entitlements):
    result = tools_features.compare_periods(
        "sales",
        user=User(),
        date_col="sold_at",
        metric_col="amount",
        period_type="month",
    )
    assert result.success, result.error
    assert "2026-01 → 2026-02" in result.message
    assert "▲" in result.message


def test_merge_message_lists_shape(seeded, entitlements, lineage_path):
    result = tools_features.merge_tables(
        "sales", "regions", user=User(), output_table="cpx_merge_detail"
    )
    assert result.success, result.error
    assert "12 rows × 7 columns" in result.message


def test_notifications_message_lists_titles(seeded, entitlements):
    from api.notifications import get_notification_manager

    get_notification_manager().send_notification("u1", "info", "Hello", "Body")
    result = tools_features.notifications_list(user=User())
    assert result.success, result.error
    assert "**Hello**" in result.message
    assert "Body" in result.message


def test_catalog_search_message_lists_matches(seeded, entitlements):
    from api.catalog import CatalogManager
    from config import settings

    CatalogManager(db_path=settings.duckdb.database_path).register_asset(
        "sales", name="Sales data"
    )
    result = tools_features.catalog_search("sales", user=User())
    assert result.success, result.error
    assert "**Assets**:" in result.message
    assert "Sales data" in result.message


def test_marketplace_message_lists_products(seeded, entitlements, monkeypatch):
    entitlements.add("marketplace")

    class FakeMarketplace:
        def __init__(self, *args, **kwargs):
            pass

        def list_products(self, category="", limit=20):
            return {
                "products": [
                    {
                        "id": "p1",
                        "name": "Sales Pack",
                        "domain": "sales",
                        "quality_score": 92.5,
                    }
                ],
                "total": 1,
            }

    monkeypatch.setattr("api.marketplace.MarketplaceManager", FakeMarketplace)
    result = tools_features.marketplace_products(user=User())
    assert result.success, result.error
    assert "- **Sales Pack**" in result.message
    assert "quality 92.5" in result.message


def test_org_message_lists_departments(seeded, entitlements, monkeypatch):
    entitlements.add("organization")

    class FakeOrg:
        def __init__(self, *args, **kwargs):
            pass

        def get_org_tree(self):
            return {
                "departments": [
                    {"id": "d1", "name": "Data", "teams": [{"id": "t1"}, {"id": "t2"}]}
                ],
                "teams": [{"id": "t1"}, {"id": "t2"}],
                "groups": [],
            }

    monkeypatch.setattr("api.org.OrgManager", FakeOrg)
    result = tools_features.org_tree(user=User())
    assert result.success, result.error
    assert "- **Data** (2 teams)" in result.message
    assert "2 team(s)" in result.message


def test_workflow_message_lists_pending(seeded, entitlements, monkeypatch):
    entitlements.add("automation")

    class FakeWorkflows:
        def __init__(self, *args, **kwargs):
            pass

        def get_pending_approvals(self, user_id, role=""):
            return [
                {
                    "id": "wf1",
                    "workflow_name": "Expense approval",
                    "current_step": 1,
                    "total_steps": 3,
                    "initiator_name": "alice",
                }
            ]

    monkeypatch.setattr("api.workflows.WorkflowManager", FakeWorkflows)
    result = tools_features.workflow_pending(user=User())
    assert result.success, result.error
    assert "**Expense approval**" in result.message
    assert "step 2/3" in result.message
    assert "requested by alice" in result.message


def test_pipeline_message_lists_outputs(seeded, entitlements, monkeypatch, tmp_path):
    entitlements.add("automation")
    pipelines = tmp_path / "config" / "pipelines"
    pipelines.mkdir(parents=True)
    (pipelines / "nightly.json").write_text(json.dumps({"name": "nightly"}))
    monkeypatch.chdir(tmp_path)

    class FakePipeline:
        @classmethod
        def from_dict(cls, definition):
            return cls()

        def validate(self):
            return []

        def run(self, **kwargs):
            return SimpleNamespace(
                success=True,
                pipeline_name="nightly",
                steps_completed=2,
                steps_total=2,
                output_tables=["nightly_out"],
                execution_time_ms=1500,
            )

    monkeypatch.setattr("analysis.pipeline.ETLPipeline", FakePipeline)
    result = tools_features.pipeline_run("nightly", user=User(role="admin"))
    assert result.success, result.error
    assert "2/2 steps" in result.message
    assert "Outputs: nightly_out" in result.message


# ── charts ──────────────────────────────────────────────────────


def _charts(result):
    return [v for v in (result.visualizations or []) if v.get("spec")]


def test_anomalies_emit_bar_chart(seeded, entitlements):
    result = tools_features.detect_anomalies("sales", user=User())
    assert result.success, result.error
    charts = _charts(result)
    assert charts and charts[0]["chart_type"] == "bar"
    trace = charts[0]["spec"]["data"][0]
    assert trace["type"] == "bar"
    assert trace["x"] == list(result.data["anomalies"])
    assert trace["y"] == [
        result.data["anomalies"][col]["count"] for col in trace["x"]
    ]


def test_anomalies_skip_chart_when_clean(seeded, entitlements):
    clean = pd.DataFrame(
        {"id": list(range(12)), "amount": [float(i) for i in range(12)]}
    )
    seeded.load_dataframe(clean, "clean_table", if_exists="replace")
    result = tools_features.detect_anomalies("clean_table", user=User())
    assert result.success, result.error
    assert result.visualizations == []


def test_quality_snapshot_emits_gauge(seeded, entitlements, trends_dir):
    result = tools_features.record_quality_snapshot("sales", user=User())
    assert result.success, result.error
    chart = _charts(result)[0]
    assert chart["chart_type"] == "indicator"
    trace = chart["spec"]["data"][0]
    assert trace["type"] == "indicator"
    assert trace["value"] == result.data["snapshot"]["overall_score"]


def test_quality_trend_emits_line(seeded, entitlements, trends_dir):
    tools_features.record_quality_snapshot("sales", user=User())
    result = tools_features.quality_trend("sales", user=User())
    assert result.success, result.error
    chart = _charts(result)[0]
    assert chart["chart_type"] == "line"
    trace = chart["spec"]["data"][0]
    assert trace["type"] == "scatter"
    assert trace["y"] == [result.data["trend"][0]["overall_score"]]


def test_stats_emits_group_bar(seeded, entitlements):
    result = tools_features.run_stats(
        "sales",
        user=User(),
        column_a="amount",
        group_col="region",
        test_type="ttest",
    )
    assert result.success, result.error
    trace = _charts(result)[0]["spec"]["data"][0]
    assert trace["type"] == "bar"
    assert trace["x"] == list(result.data["group_stats"])
    assert trace["y"] == [
        result.data["group_stats"][group]["mean"] for group in trace["x"]
    ]


def test_compare_tables_emits_counts_bar(seeded, entitlements):
    result = tools_features.compare_tables(
        "sales", "sales_after", user=User(), key_column="id"
    )
    assert result.success, result.error
    trace = _charts(result)[0]["spec"]["data"][0]
    assert trace["x"] == ["modified", "added", "removed"]
    assert trace["y"] == [
        result.data["rows_changed"],
        result.data["rows_added"],
        result.data["rows_removed"],
    ]


def test_compare_periods_emits_line(seeded, entitlements):
    result = tools_features.compare_periods(
        "sales",
        user=User(),
        date_col="sold_at",
        metric_col="amount",
        period_type="month",
    )
    assert result.success, result.error
    chart = _charts(result)[0]
    assert chart["chart_type"] == "line"
    trace = chart["spec"]["data"][0]
    assert trace["type"] == "scatter"
    assert trace["x"]


def test_execute_plan_emits_task_charts(monkeypatch):
    import asyncio

    from agent.orchestrator import Orchestrator, StepResult
    from agent.planner import ExecutionPlan, ToolType
    from agent.tools_tasks import TaskResult

    orch = object.__new__(Orchestrator)
    orch.llm_client = None
    viz = {
        "chart_type": "bar",
        "spec": {"data": [{"type": "bar", "x": ["a"], "y": [1]}], "layout": {}},
    }

    async def fake_execute(step, outputs, context, user):
        return StepResult(
            step_id=1,
            tool=ToolType.TASK,
            success=True,
            result=TaskResult(success=True, visualizations=[viz]),
            execution_time_ms=1.0,
        )

    monkeypatch.setattr(orch, "_execute_step", fake_execute)
    step = SimpleNamespace(
        step_id=1,
        tool=ToolType.TASK,
        description="detect anomalies",
        metadata={},
        params={},
        depends_on=[],
    )
    plan = ExecutionPlan(
        question="q", steps=[step], estimated_steps=1, reasoning=""
    )
    events = []
    asyncio.run(orch._execute_plan(plan, {}, None, progress=events.append))

    viz_events = [e for e in events if e.get("kind") == "viz"]
    assert viz_events and viz_events[0]["result"] is viz


def test_build_ask_response_serializes_feature_chart(monkeypatch):
    from api.main import _build_ask_response

    monkeypatch.setattr("api.main._llm_available", lambda force=False: True)
    viz = {
        "chart_type": "bar",
        "spec": {"data": [{"type": "bar", "x": ["a"], "y": [1]}], "layout": {}},
    }
    response = SimpleNamespace(
        success=True,
        final_answer="answer",
        step_results=[],
        plan=SimpleNamespace(steps=[]),
        total_time_ms=1.0,
        error=None,
        visualizations=[viz],
    )
    out = _build_ask_response(
        SimpleNamespace(question="q"), response, "session-1"
    )
    assert out["visualizations"][0]["chart_type"] == "bar"
    assert out["visualizations"][0]["spec"] == viz["spec"]


def test_build_ask_response_handles_task_step(monkeypatch):
    from agent.orchestrator import StepResult
    from agent.planner import ToolType
    from agent.tools_tasks import TaskResult
    from api.main import _build_ask_response

    monkeypatch.setattr("api.main._llm_available", lambda force=False: True)
    viz = {
        "chart_type": "bar",
        "spec": {"data": [{"type": "bar", "x": ["a"], "y": [1]}], "layout": {}},
    }
    step = StepResult(
        step_id=1,
        tool=ToolType.TASK,
        success=True,
        result=TaskResult(
            success=True, data={"table": "sales"}, visualizations=[viz]
        ),
        execution_time_ms=2.0,
    )
    response = SimpleNamespace(
        success=True,
        final_answer="answer",
        step_results=[step],
        plan=SimpleNamespace(steps=[]),
        total_time_ms=2.0,
        error=None,
        visualizations=[viz],
    )
    out = _build_ask_response(
        SimpleNamespace(question="q"), response, "session-1"
    )
    assert out["visualizations"][0]["spec"] == viz["spec"]
    assert out["data_preview"] is None
    assert out["steps"][0]["result_rows"] == 0
