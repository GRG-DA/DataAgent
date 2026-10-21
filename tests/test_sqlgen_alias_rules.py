"""SQL alias integrity rules in the LLM SQL generator.

Verified answer defects this locks in:

  * "Show me the key trends in this dataset" produced
    `SELECT COUNT(*) AS count, COUNT(DISTINCT user_id) AS total_value FROM
    key_redemptions` — a distinct-count mislabeled `total_value` (the answer
    then read "Total value: 1").
  * "Which source has the highest total spend?" produced
    `SUM(spend) AS total_spend, SUM(spend) AS max_value` — the same
    expression twice under different aliases ("Max value" duplicated
    "Total spend" in the answer).

Contracts:
  1. The system prompt forbids duplicate-expression aliases and
     measure-sounding names for COUNT results.
  2. When a reference query has no real aliases (e.g. `SELECT * ...`), the
     generator must NOT force the bogus fallback list
     "group_name, total_value, count" — it must ask for truthful names.
  3. When the reference query HAS aliases, the alias-preservation rules
     still apply.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import agent.sqlgen as sg


def _patch_validation(monkeypatch):
    monkeypatch.setattr(sg, "_sql_schema_context", lambda tables: "CTX")
    monkeypatch.setattr(sg, "_validate_sql", lambda sql: (True, ""))
    monkeypatch.setattr(sg, "execution_validate", lambda sql, row_limit=500: (True, [], ""))
    monkeypatch.setattr(sg, "_fix_sql_case_insensitive", lambda sql: sql)
    monkeypatch.setattr(sg, "_strip_round", lambda sql: sql)


class CapturingLLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return SimpleNamespace(message=SimpleNamespace(content=self.reply))


def _user_prompt(llm) -> str:
    first = llm.calls[0]
    return " ".join(m.content for m in first if m.role == "user")


class TestMetricAliasNormalization:
    """Deterministic post-pass: the local model sometimes ignores prompt rules
    and emits misleading metric aliases anyway (observed live:
    `COUNT(*) AS total_value`, `SUM(spend) AS max_value`). The generator must
    normalise them mechanically before returning SQL."""

    def test_count_star_mislabeled_as_total_value(self):
        sql, renames = sg._normalize_metric_aliases(
            "SELECT COUNT(*) AS count, COUNT(*) AS total_value FROM analytics.key_redemptions")
        assert "total_value" not in sql
        assert sql.upper().count("COUNT(*)") == 1
        assert renames

    def test_sum_mislabeled_as_max_value(self):
        sql, renames = sg._normalize_metric_aliases(
            "SELECT source, SUM(spend) AS max_value, COUNT(*) AS count "
            "FROM analytics.ppc_ads GROUP BY source ORDER BY max_value DESC LIMIT 1")
        assert "SUM(spend) AS total_value" in sql
        assert "ORDER BY total_value DESC" in sql
        assert "max_value" not in sql
        assert renames

    def test_count_distinct_mislabeled(self):
        sql, _ = sg._normalize_metric_aliases(
            "SELECT COUNT(DISTINCT user_id) AS total_value FROM analytics.key_redemptions")
        assert "AS distinct_user_id" in sql

    def test_min_mislabeled_as_total_value(self):
        sql, _ = sg._normalize_metric_aliases(
            "SELECT MIN(price) AS total_value FROM analytics.t")
        assert "AS min_value" in sql

    def test_truthful_aliases_untouched(self):
        q = ("SELECT source, SUM(spend) AS total_spend, COUNT(*) AS count "
             "FROM analytics.ppc_ads GROUP BY source")
        assert sg._normalize_metric_aliases(q) == (q, [])

    def test_cte_outer_select_normalized(self):
        sql, _ = sg._normalize_metric_aliases(
            "WITH x AS (SELECT 1 AS v) SELECT COUNT(*) AS total_value FROM x")
        assert "AS count" in sql
        assert "total_value" not in sql

    def test_subquery_in_select_clause_untouched(self):
        q = "SELECT (SELECT COUNT(*) FROM analytics.t2) AS total_value FROM analytics.t"
        assert sg._normalize_metric_aliases(q) == (q, [])

    def test_referenced_alias_never_broken(self):
        # total_value is referenced by ORDER BY and its truthful name (count)
        # already exists — dropping or renaming it would break the query, so
        # it must be left alone.
        q = ("SELECT COUNT(*) AS count, COUNT(*) AS total_value FROM analytics.t "
             "ORDER BY total_value")
        sql, _ = sg._normalize_metric_aliases(q)
        assert "total_value" in sql
        assert "ORDER BY total_value" in sql


class TestGenerateSqlNormalizesAliases:
    @pytest.mark.asyncio
    async def test_generate_sql_returns_normalized_aliases(self, monkeypatch):
        _patch_validation(monkeypatch)
        llm = CapturingLLM("SELECT COUNT(*) AS count, COUNT(*) AS total_value FROM analytics.t")
        sql, source = await sg.generate_sql("q", table_names=None, llm_client=llm, fallback="FB")
        assert source == "llm"
        assert "total_value" not in sql
        assert sql.upper().count("COUNT(*)") == 1


class TestSystemPromptAliasIntegrity:
    def test_no_duplicate_expression_rule(self):
        assert "same expression under two different aliases" in sg.SQL_SYSTEM_PROMPT

    def test_truthful_count_naming_rule(self):
        prompt = sg.SQL_SYSTEM_PROMPT
        assert "distinct_" in prompt, "COUNT(DISTINCT ...) must get its own alias convention"
        assert "total_value" in prompt, "the anti-pattern must be named explicitly"


class TestReferenceAliasHandling:
    @pytest.mark.asyncio
    async def test_star_reference_does_not_force_bogus_aliases(self, monkeypatch):
        _patch_validation(monkeypatch)
        monkeypatch.setattr(sg, "_extract_aliases", lambda sql: ["*"])
        llm = CapturingLLM("SELECT 1 AS count")
        await sg.generate_sql(
            "q", table_names=None, llm_client=llm, fallback="FB",
            reference_query="SELECT * FROM analytics.t LIMIT 100",
        )
        prompt = _user_prompt(llm)
        assert "MUST be exactly: group_name, total_value, count" not in prompt
        assert "truthfully" in prompt

    @pytest.mark.asyncio
    async def test_empty_reference_aliases_do_not_force_bogus_aliases(self, monkeypatch):
        _patch_validation(monkeypatch)
        monkeypatch.setattr(sg, "_extract_aliases", lambda sql: [])
        llm = CapturingLLM("SELECT 1 AS count")
        await sg.generate_sql(
            "q", table_names=None, llm_client=llm, fallback="FB",
            reference_query="WITH x AS (SELECT 1) SELECT 1 AS count FROM x",
        )
        prompt = _user_prompt(llm)
        assert "MUST be exactly: group_name, total_value, count" not in prompt
        assert "truthfully" in prompt

    @pytest.mark.asyncio
    async def test_reference_aliases_still_enforced(self, monkeypatch):
        _patch_validation(monkeypatch)
        monkeypatch.setattr(sg, "_extract_aliases", lambda sql: ["group_name", "value"])
        llm = CapturingLLM("SELECT 1 AS group_name, 2 AS value")
        await sg.generate_sql(
            "q", table_names=None, llm_client=llm, fallback="FB",
            reference_query="SELECT a AS group_name, SUM(b) AS value FROM analytics.t",
        )
        prompt = _user_prompt(llm)
        assert "MUST be exactly: group_name, value" in prompt

    @pytest.mark.asyncio
    async def test_misleading_reference_alias_has_truthfulness_escape_hatch(self, monkeypatch):
        _patch_validation(monkeypatch)
        monkeypatch.setattr(sg, "_extract_aliases", lambda sql: ["count", "total_value"])
        llm = CapturingLLM("SELECT 1 AS count, 2 AS total_value")
        await sg.generate_sql(
            "q", table_names=None, llm_client=llm, fallback="FB",
            reference_query='SELECT COUNT(*) AS count, SUM("spend") AS total_value FROM analytics.t',
        )
        prompt = _user_prompt(llm)
        assert "MISLEADING" in prompt, (
            "when the reference alias would be wrong for the actual metric, "
            "the LLM must be told to prefer a truthful alias"
        )
