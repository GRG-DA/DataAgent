"""Ranking rule: "Which source has the highest total spend?" must group by the
dimension and SUM the measure — never return a scalar MAX.

Live defect (UI test, 2026-10-17): the LLM SQL upgrade timed out and the rule
fallback produced `SELECT MAX("spend") AS max_value FROM analytics.ppc_ads`,
answering "Max value: 1,467.10" — no source named, wrong number. Root cause:
"source" was missing from the scalar-MAX guard's dimension-noun list, so the
question was misclassified as a scalar MAX request.
"""

from __future__ import annotations

import re

from agent.planner import Planner

SCHEMA = (
    "TABLE analytics.ppc_ads (100 rows):\n"
    "  - date: VARCHAR\n"
    "  - source: VARCHAR\n"
    "  - campaign: VARCHAR\n"
    "  - impressions: BIGINT\n"
    "  - clicks: BIGINT\n"
    "  - spend: DOUBLE\n"
)


def _planner() -> Planner:
    p = Planner()
    p.set_schema_context(SCHEMA)
    return p


class TestSourceRankingRule:
    def test_source_ranking_groups_and_sums(self):
        sql = _planner()._generate_top_n_query(
            "Which source has the highest total spend?", "ppc_ads", 10)
        up = sql.upper()
        assert "GROUP BY" in up, sql
        assert "SUM(" in up, sql
        assert re.search(r'\bsource\b', sql), sql
        assert "ORDER BY" in up, sql
        assert not re.match(r"\s*SELECT\s+MAX\(", up), sql

    def test_scalar_max_still_used_without_dimension_noun(self):
        sql = _planner()._generate_top_n_query(
            "What is the highest single spend value?", "ppc_ads", 10)
        assert re.match(r"\s*SELECT\s+MAX\(", sql.upper()), sql
