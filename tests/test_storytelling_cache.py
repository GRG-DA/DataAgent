"""Storytelling cache behaviour (TDD).

Covers the defect where ``POST /analysis/storytelling`` answered from a stale
rule-based cache entry, so the UI could never show an LLM-written story and
the "Regenerate" button re-served the very same cached entry:

* a cached entry is served for an unchanged request,
* a regenerate/force request bypasses (and busts) the cache,
* a rule-based (``source == "auto"``) entry is never served as a fresh story,
* the dataset name reaches the story text.

The LLM is faked and the warehouse is a throwaway DuckDB file, so the suite
never touches the network nor the real ``data/curated`` warehouse.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import duckdb
import pytest

TABLE = "ai_probe_sales"
OTHER_TABLE = "ppc_ads"


class FakeLLM:
    """Minimal async LLM double — returns a canned narrative, no network."""

    def __init__(self, content: str = "EXECUTIVE_OVERVIEW\nPaid orders dominate."):
        self.content = content
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        from agent.llm_client import CompletionResponse, Message

        return CompletionResponse(message=Message(role="assistant", content=self.content))


def _llm_stories():
    return [{"type": "trend", "title": f"AI Analysis: {TABLE}",
             "narrative": "Paid orders dominate the book.", "confidence": "high",
             "source": "llm"}]


def _auto_stories():
    return [{"type": "trend", "title": "Data Overview",
             "narrative": "This dataset contains **3 rows**.", "confidence": "high",
             "source": "auto"}]


@pytest.fixture()
def st_app(tmp_path, monkeypatch):
    """api.main wired onto a throwaway warehouse holding one small table."""
    import api.main as main_mod
    from config import settings
    from etl.load import WarehouseLoader

    db_path = str(tmp_path / "story.duckdb")
    con = duckdb.connect(db_path)
    con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    con.execute(
        "CREATE TABLE analytics.ai_probe_sales ("
        " order_id VARCHAR, region VARCHAR, status VARCHAR, amount DOUBLE)"
    )
    con.execute(
        "INSERT INTO analytics.ai_probe_sales VALUES "
        "('o1','east','paid',10.0),('o2','west','paid',20.0),('o3','east','open',30.0)"
    )
    con.execute(
        "CREATE TABLE analytics.ppc_ads ("
        " source VARCHAR, spend DOUBLE)"
    )
    con.execute(
        "INSERT INTO analytics.ppc_ads VALUES ('google',10.0),('meta',20.0)"
    )
    con.close()

    # Point the app at the throwaway warehouse.
    monkeypatch.setattr(settings.duckdb, "database_path", db_path)
    loader = WarehouseLoader(
        db_path=db_path,
        schema=settings.duckdb.schema,
        config=settings.duckdb.config,
    )
    monkeypatch.setattr(main_mod, "loader", loader)

    # Never touch the real data/curated cache file from a test.
    cache_file = tmp_path / "storytelling_cache.json"
    monkeypatch.setattr(main_mod, "_storytelling_cache_path", lambda: str(cache_file))

    # The background job must not run for real while the endpoint is tested.
    real_job = main_mod._run_storytelling_job
    started = []

    async def _fake_job(job_id, table_name, user_question, max_stories):
        started.append({"job_id": job_id, "table": table_name,
                        "question": user_question, "max_stories": max_stories})

    monkeypatch.setattr(main_mod, "_run_storytelling_job", _fake_job)

    saved_cache = dict(main_mod._STORYTELLING_CACHE)
    saved_jobs = dict(main_mod._STORYTELLING_JOBS)
    main_mod._STORYTELLING_CACHE.clear()
    main_mod._STORYTELLING_JOBS.clear()

    yield {
        "main": main_mod,
        "cache_file": cache_file,
        "db_path": db_path,
        "started": started,
        "real_job": real_job,
    }

    main_mod._STORYTELLING_CACHE.clear()
    main_mod._STORYTELLING_CACHE.update(saved_cache)
    main_mod._STORYTELLING_JOBS.clear()
    main_mod._STORYTELLING_JOBS.update(saved_jobs)


async def _let_job_start():
    """Give the endpoint's background task a chance to run."""
    import asyncio

    await asyncio.sleep(0)


def _seed_cache(st_app, stories, question: str = "", table: str = TABLE):
    """Write a cache entry whose signature matches the live table."""
    main_mod = st_app["main"]
    with main_mod.tenant_connection() as conn:
        conn.execute(f"SET search_path = '{main_mod.tenant_search_path()}'")
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        df = conn.execute(f"SELECT * FROM {table} LIMIT 20").df()
    main_mod._storytelling_cache_put({
        "table": table,
        "signature": main_mod._storytelling_signature(count, df),
        "stories": stories,
        "question": question,
        "created_at": "2026-10-22T11:00:16Z",
    })


# ─────────────────────────────────────────────────────────────
# 1. The cache still does its job for an unchanged request
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cached_entry_is_served_for_an_unchanged_request(st_app):
    """An unchanged table + question is answered from the cache."""
    main_mod = st_app["main"]
    _seed_cache(st_app, _llm_stories())

    res = await main_mod.storytelling_endpoint({"table_name": TABLE})

    assert res["success"] is True
    assert res["cached"] is True
    assert res["stories"][0]["source"] == "llm"
    # A cache hit must not start another (LLM) job.
    assert st_app["started"] == []


@pytest.mark.asyncio
async def test_cache_is_not_used_when_the_question_changed(st_app):
    """The cache is keyed by table *and* question, not by table alone."""
    main_mod = st_app["main"]
    _seed_cache(st_app, _llm_stories(), question="something else")

    res = await main_mod.storytelling_endpoint(
        {"table_name": TABLE, "user_question": "why did sales drop?"})

    assert res["success"] is True
    assert res.get("cached") is not True
    assert res.get("job_id")


# ─────────────────────────────────────────────────────────────
# 2. Regenerate busts the cache
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_regenerate_request_bypasses_the_cache(st_app):
    """A regenerate request must not be answered from the cache."""
    main_mod = st_app["main"]
    _seed_cache(st_app, _llm_stories())

    res = await main_mod.storytelling_endpoint(
        {"table_name": TABLE, "regenerate": True})
    await _let_job_start()

    assert res["success"] is True
    assert res.get("cached") is False
    assert res.get("job_id")
    # The cached story was busted, so it cannot be served any more.
    assert TABLE not in main_mod._STORYTELLING_CACHE
    assert len(st_app["started"]) == 1


@pytest.mark.asyncio
async def test_force_flag_bypasses_the_cache(st_app):
    """``force`` is the explicit regenerate switch."""
    main_mod = st_app["main"]
    _seed_cache(st_app, _llm_stories())

    res = await main_mod.storytelling_endpoint(
        {"table_name": TABLE, "force": True})

    assert res["success"] is True
    assert res.get("cached") is False
    assert res.get("job_id")
    assert TABLE not in main_mod._STORYTELLING_CACHE


@pytest.mark.asyncio
async def test_regenerate_keeps_other_tables_entries(st_app):
    """Busting one table must not evict the other tables' cached stories."""
    main_mod = st_app["main"]
    _seed_cache(st_app, _llm_stories())
    _seed_cache(st_app, [{"type": "trend", "title": "AI Analysis: ppc_ads",
                           "narrative": "Spend is up.", "confidence": "high",
                           "source": "llm"}], table=OTHER_TABLE)

    await main_mod.storytelling_endpoint({"table_name": TABLE, "force": True})

    assert OTHER_TABLE in main_mod._STORYTELLING_CACHE
    persisted = json.loads(st_app["cache_file"].read_text(encoding="utf-8"))
    assert OTHER_TABLE in persisted
    assert TABLE not in persisted


# ─────────────────────────────────────────────────────────────
# 3. A rule-based entry is never served as a fresh story
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rule_based_entry_is_not_served_as_a_fresh_story(st_app):
    """An ``auto``-only entry means the LLM never ran: refresh it."""
    main_mod = st_app["main"]
    _seed_cache(st_app, _auto_stories())

    res = await main_mod.storytelling_endpoint({"table_name": TABLE})
    await _let_job_start()

    assert res["success"] is True
    assert res.get("cached") is not True
    assert res.get("job_id")
    assert len(st_app["started"]) == 1


@pytest.mark.asyncio
async def test_rule_based_entry_is_not_served_even_with_the_same_signature(st_app):
    """The cached stories are rule-based, so they are never handed back."""
    main_mod = st_app["main"]
    _seed_cache(st_app, _auto_stories())

    res = await main_mod.storytelling_endpoint({"table_name": TABLE})

    assert "stories" not in res


# ─────────────────────────────────────────────────────────────
# 4. The dataset name reaches the story text
# ─────────────────────────────────────────────────────────────

def test_auto_insights_carry_the_dataset_name():
    """The rule-based overview names the dataset instead of "unnamed"."""
    import pandas as pd

    from analysis.insights import generate_auto_insights

    df = pd.DataFrame({"order_id": ["o1", "o2"], "region": ["east", "west"],
                       "status": ["paid", "open"], "amount": [10.0, 20.0]})
    out = generate_auto_insights(df, table_name=TABLE)

    assert "unnamed" not in out["summary"]
    assert TABLE in out["summary"]


@pytest.mark.asyncio
async def test_dataset_name_reaches_the_generated_story(st_app, monkeypatch):
    """Both the overview and the AI story name the dataset."""
    main_mod = st_app["main"]
    monkeypatch.setattr(main_mod, "orchestrator",
                        SimpleNamespace(llm_client=FakeLLM()))

    job_id = "job-dataset-name"
    main_mod._STORYTELLING_JOBS[job_id] = {
        "job_id": job_id, "table": TABLE, "status": "running"}
    await st_app["real_job"](job_id, TABLE, "How are sales?", 10)

    job = main_mod._STORYTELLING_JOBS[job_id]
    assert job["status"] == "done", job.get("error")
    stories = job["stories"]

    overview = [s for s in stories if s["title"].startswith("Data Overview")]
    assert overview, "expected a rule-based overview story"
    assert "unnamed" not in overview[0]["narrative"]
    assert TABLE in overview[0]["narrative"]

    llm_stories = [s for s in stories if s["source"] == "llm"]
    assert llm_stories, "expected an LLM-written story"
    assert TABLE in llm_stories[0]["title"]

    # The finished job is cached, and the cache knows it holds an LLM story.
    entry = main_mod._STORYTELLING_CACHE[TABLE]
    assert entry["has_llm"] is True
    assert entry["question"] == "How are sales?"
