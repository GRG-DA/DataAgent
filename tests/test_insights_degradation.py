"""Regression tests for two correctness bugs.

1. A failed LLM call used to look exactly like a successful one to a
   programmatic caller: the payload said ``source == "rule-based"`` but the
   failure was only logged at ``debug`` level, so the HTTP layer answered 200
   with a success payload and nothing in the payload said "degraded". The
   fallback path now carries ``degraded: True`` plus a short
   ``degraded_reason``, and the LLM path carries ``degraded: False``.

2. ``ConfidenceIntervalResult`` had no ``margin_of_error`` field even though
   the web UI derived one client-side as ``(ci_upper - ci_lower) / 2``. The
   backend now computes and publishes it, so there is a single authoritative
   value.

No network, no warehouse access: the LLM client is a local double and the
statistics are computed from in-memory pandas series.
"""

import asyncio
import dataclasses
import logging

import pandas as pd
import pytest

from analysis.insights import generate_llm_insights
from analysis.statistical import compute_confidence_interval


# ── test doubles ────────────────────────────────────────────────────────

def _small_df(n: int = 10) -> pd.DataFrame:
    """A frame the rule-based engine has something to say about.

    One missing cell and one heavy outlier so ``generate_auto_insights``
    produces real findings instead of an empty list.
    """
    sales = [float(i) * 1.5 for i in range(1, n + 1)]
    sales[-1] = 500.0  # outlier -> skew finding
    qty = [float(i % 4 + 1) for i in range(n)]
    qty[0] = None  # missing value -> missingness finding
    return pd.DataFrame({
        "sales": sales,
        "qty": qty,
        "cat": ["A" if i % 2 else "B" for i in range(n)],
    })


class _BoomLLM:
    """LLM double that always fails, like a provider outage or rate limit."""

    def __init__(self, message: str = "llm provider unavailable"):
        self.message = message

    async def chat(self, *args, **kwargs):
        raise RuntimeError(self.message)


class _FakeLLM:
    """LLM double that answers with a canned narrative."""

    async def chat(self, *args, **kwargs):
        class _Msg:
            content = "EXECUTIVE_OVERVIEW: sales are up KEY_FINDINGS: - steady"

        class _Resp:
            message = _Msg()

        return _Resp()


def _run(coro):
    return asyncio.run(coro)


# ── Bug 1: degraded LLM insights ───────────────────────────────────────

class TestLLMInsightsDegradation:

    def test_llm_path_is_not_degraded(self):
        out = _run(generate_llm_insights(_small_df(), table_name="t", client=_FakeLLM()))
        assert out["source"] == "llm"
        assert not out.get("degraded")
        assert not out.get("degraded_reason")

    def test_fallback_marks_payload_as_degraded(self):
        out = _run(generate_llm_insights(_small_df(), table_name="t", client=_BoomLLM()))
        assert out["source"] == "rule-based"
        assert out["degraded"] is True
        assert isinstance(out.get("degraded_reason"), str)
        assert out["degraded_reason"].strip(), "degraded_reason must not be empty"

    def test_fallback_still_returns_usable_narrative(self):
        out = _run(generate_llm_insights(_small_df(), table_name="t", client=_BoomLLM()))
        assert out.get("narrative"), "fallback must still return a narrative"
        assert out.get("summary"), "fallback must still return a summary"
        assert isinstance(out.get("findings"), list) and out["findings"]
        assert isinstance(out.get("recommendations"), list) and out["recommendations"]
        assert out.get("confidence") in ("high", "medium", "low")

    def test_fallback_is_logged_at_warning_level(self, caplog):
        with caplog.at_level(logging.WARNING, logger="analysis.insights"):
            _run(generate_llm_insights(_small_df(), table_name="my_table", client=_BoomLLM()))

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings, "a failed LLM call must be logged at warning level"

        text = "\n".join(r.getMessage() for r in warnings)
        assert "my_table" in text, "the log must name the table that degraded"
        assert "llm" in text.lower()

        # It must not be a debug-only log anymore.
        debug_only = [r for r in caplog.records
                      if r.levelno == logging.DEBUG and "llm" in r.getMessage().lower()]
        assert not debug_only, "the failure must not be logged at debug level only"

    def test_failure_log_does_not_leak_secrets(self, caplog):
        secret = "sk-super-secret-value"
        with caplog.at_level(logging.WARNING, logger="analysis.insights"):
            _run(generate_llm_insights(
                _small_df(), table_name="t",
                client=_BoomLLM(f"provider rejected request: api_key={secret}"),
            ))
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert secret not in text, "secrets must never reach the log"
        # The secret must not leak into the payload either.
        out = _run(generate_llm_insights(
            _small_df(), table_name="t",
            client=_BoomLLM(f"provider rejected request: api_key={secret}"),
        ))
        assert secret not in str(out)


# ── Bug 2: margin_of_error on the backend ──────────────────────────────

class TestConfidenceIntervalMarginOfError:

    def test_result_dataclass_exposes_margin_of_error(self):
        from analysis.statistical import ConfidenceIntervalResult

        names = {f.name for f in dataclasses.fields(ConfidenceIntervalResult)}
        assert "margin_of_error" in names

    def test_margin_of_error_matches_half_width(self):
        r = compute_confidence_interval(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]), "c", method="t")
        assert r.margin_of_error == pytest.approx((r.ci_upper - r.ci_lower) / 2)

    def test_known_interval_values_are_unchanged(self):
        r = compute_confidence_interval(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]), "c", method="t")
        assert r.mean == 3.0
        assert r.ci_lower == pytest.approx(1.0368, abs=1e-4)
        assert r.ci_upper == pytest.approx(4.9632, abs=1e-4)
        assert r.method == "Student's t"
        assert r.confidence_level == 0.95
        assert r.sample_size == 5
        assert r.interpretation

    def test_known_normal_interval_values_are_unchanged(self):
        r = compute_confidence_interval(pd.Series(range(40)), "c", method="normal")
        assert r.mean == 19.5
        assert r.ci_lower == pytest.approx(15.8772, abs=1e-4)
        assert r.ci_upper == pytest.approx(23.1228, abs=1e-4)
        assert r.method == "Normal (Z)"
        assert r.sample_size == 40
        assert r.margin_of_error == pytest.approx(3.6228, abs=1e-4)

    def test_margin_of_error_at_non_95_percent_level(self):
        series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        r90 = compute_confidence_interval(series, "c", confidence=0.90, method="t")
        r95 = compute_confidence_interval(series, "c", confidence=0.95, method="t")
        assert r90.confidence_level == 0.90
        assert r90.margin_of_error == pytest.approx((r90.ci_upper - r90.ci_lower) / 2)
        assert r90.margin_of_error == pytest.approx(1.5074, abs=1e-4)
        # A narrower confidence level must give a narrower half-width.
        assert r90.margin_of_error < r95.margin_of_error

    def test_margin_of_error_for_degenerate_interval(self):
        r = compute_confidence_interval(pd.Series([1.0]), "c")
        assert r.ci_lower == r.ci_upper == 1.0
        assert r.margin_of_error == 0.0

    def test_margin_of_error_for_bootstrap_interval(self):
        r = compute_confidence_interval(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]), "c", method="bootstrap")
        assert r.method == "Bootstrap"
        assert r.margin_of_error == pytest.approx((r.ci_upper - r.ci_lower) / 2)
