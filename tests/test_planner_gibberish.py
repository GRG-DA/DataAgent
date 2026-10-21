"""Meaningless / gibberish chat input must never reach the SQL step.

Issue: a keystroke like "h" (or typed nonsense such as "asdsadj") rendered a
meaningless data table instead of the "I couldn't understand" reply.

Two root causes, both in `agent.planner`:

1. `_is_meaningless_question()` returned False whenever the question produced
   no content words at all — the regex `[a-z][a-z_\\-']{2,}` needs >=3 chars, so
   "h", "x", "12", "!!" all fell through to `_rule_based_plan()` →
   `intent=general` → `SELECT * ... LIMIT 100` → markdown table.
2. The chat UI prefixes the selected table
   (`Using the "chocolate_sales" table: h`, web/js/pages/chat.js:799). That
   injected table name (and the word "table") is always in the vocabulary, so
   the guard saw a "known" word and never flagged the user's own garbage text.

Guarantees:
  1. Bare gibberish (too short, unknown, digits, punctuation) plans nothing.
  2. Gibberish behind the UI's table-scope prefix plans nothing.
  3. Smalltalk behind the prefix is still conversational, not a query.
  4. Real questions — with or without the prefix — still plan real steps.
"""

from __future__ import annotations

import pytest

from agent.planner import Planner

SCHEMA = (
    "TABLE analytics.chocolate_sales (4 rows):\n"
    "  - region: VARCHAR\n"
    "  - product: VARCHAR\n"
    "  - amount: DOUBLE\n"
    "  - boxes: INTEGER\n"
)

TABLES = ["chocolate_sales"]

PREFIX = 'Using the "chocolate_sales" table: '


def _planner() -> Planner:
    planner = Planner()
    planner.set_schema_context(SCHEMA)
    return planner


def _plan(question: str):
    return _planner().create_plan(question, TABLES)


def _assert_unrecognized(question: str):
    plan = _plan(question)
    assert plan.steps == [], f"{question!r} planned {plan.steps!r}"
    assert "unrecognized" in (plan.reasoning or "").lower(), plan.reasoning


# ─────────────────────────────────────────────────────────────
# 1. Bare gibberish → unrecognized plan (no SQL step)
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("question", [
    "",            # empty / prefix-only request
    "h",            # single keystroke
    "x",
    "q",
    "12",           # no letters at all
    "!!",
    "abc",          # 3-letter unknown token
    "xyz",
    "asdsadj",      # typed nonsense
    "qwertyuiop zzzz",
])
def test_bare_gibberish_is_unrecognized(question):
    _assert_unrecognized(question)


# ─────────────────────────────────────────────────────────────
# 2. Same garbage behind the UI's table-scope prefix
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["h", "x", "abc", "xyz", "asdsadj"])
def test_prefixed_gibberish_is_unrecognized(text):
    _assert_unrecognized(PREFIX + text)


# ─────────────────────────────────────────────────────────────
# 3. Smalltalk behind the prefix stays conversational
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["hi", "hello", "thanks"])
@pytest.mark.parametrize("separator", [":", "-", "—"])
def test_prefixed_smalltalk_is_conversational(separator, text):
    scoped = f'Using the "chocolate_sales" table {separator} {text}'
    plan = _plan(scoped)
    assert plan.steps == [], f"{scoped!r} planned {plan.steps!r}"
    assert "conversational" in (plan.reasoning or "").lower(), plan.reasoning


def test_prefix_only_request_is_unrecognized():
    _assert_unrecognized('Using the "chocolate_sales" table: ')


# ─────────────────────────────────────────────────────────────
# 4. Real questions still plan real steps
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("question", [
    "show revenue by region",
    PREFIX + "show revenue by region",
    "what are the top 3 products by amount",
    PREFIX + "summarize this dataset",
])
def test_real_questions_still_plan_steps(question):
    plan = _plan(question)
    assert plan.steps, f"no steps for {question!r} (reasoning: {plan.reasoning})"


def test_explicit_table_request_still_plans():
    plan = _plan("show me the tables")
    assert plan.steps, plan.reasoning
