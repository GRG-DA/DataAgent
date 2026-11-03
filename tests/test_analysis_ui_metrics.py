"""UI contract for the analysis metrics and the AI chart recommendation card.

Two user-visible bugs, both of them "a value that is really 0 (or a field
that is really spelled differently) is treated as if it were missing":

**Bug 1 — a zero value falls through to the wrong metric.**
``web/js/pages/analysis.js`` charted the Segment Breakdown with
``s.total || s.mean || s.count``.  ``||`` treats a legitimate ``0`` as
falsy, so a group whose total really is ``0`` (a genuine zero-sum group)
silently fell through to ``mean`` and then to ``count`` — the chart then
showed the row COUNT under a metric label.  The metric is now chosen
explicitly, in priority order (``total`` -> ``mean`` -> ``count``), with
an explicit ``typeof x === 'number'`` / ``Number.isFinite()`` test, and
the chart is labelled with the metric it actually plotted.

**Bug 2 — the chart-recommendation reasoning is dropped.**
``web/js/pages/blankdash.js`` read ``r.why || r.reason`` while the API
returns the explanation under ``reasoning`` (verified against
``POST /llm/chart-recommend``), so every recommendation rendered as a
bare title.

These are source-contract checks plus a small Node harness (the same
pattern as ``tests/test_chat_persistence.py``) that executes the real
helper functions, so "a zero-valued metric is not treated as absent" is
a behavioural guarantee and not just a grep.

Run with::

    python -m pytest tests/test_analysis_ui_metrics.py -q --no-cov -p no:cacheprovider
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_JS = ROOT / "web" / "js" / "pages" / "analysis.js"
BLANKDASH_JS = ROOT / "web" / "js" / "pages" / "blankdash.js"

#: The field the chart-recommendation API actually returns the explanation
#: under.  ``POST /llm/chart-recommend`` answers with
#: ``{"success": true, "charts": [{"chart_type", "x_axis", "y_axis",
#: "color", "title", "reasoning"}], "table": ...}`` and the rule-based
#: ``GET /analysis/chart-recommend`` uses the same key.
REASONING_KEY = "reasoning"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _analysis_src() -> str:
    return _read(ANALYSIS_JS)


def _blankdash_src() -> str:
    return _read(BLANKDASH_JS)


def _extract(src: str, start: str, end: str, label: str) -> str:
    s = src.find(start)
    assert s != -1, f"{label}: start marker {start!r} not found"
    e = src.find(end, s)
    assert e != -1, f"{label}: end marker {end!r} not found"
    return src[s:e]


def _run_node(tmp_path: Path, body: str) -> str:
    script = tmp_path / "case.js"
    script.write_text(body, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(script)], capture_output=True, text=True, timeout=30,
        cwd=str(ROOT),
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, f"node harness failed:\n{out}"
    return out


def _assert_ok(out: str) -> None:
    assert "OK" in out, f"node harness reported failure:\n{out}"


requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed"
)


# --------------------------------------------------------------------------
# Bug 1 — the segment metric must not fall through on a zero
# --------------------------------------------------------------------------


def test_segment_metric_does_not_use_a_falsy_or_chain():
    """``s.total || s.mean || s.count`` must be gone from analysis.js.

    ``||`` cannot tell "absent" from "0", so the chain silently plotted
    the row count whenever a group's total was really 0.
    """
    src = _analysis_src()
    assert "s.total || s.mean || s.count" not in src, (
        "the segment metric must not be picked with `||` — a genuine 0 "
        "falls through to the count and the chart lies about the metric"
    )
    for chain in (
        "total || s.mean",
        "s.total ||",
        "s.mean || s.count",
    ):
        assert chain not in src, (
            f"analysis.js still contains the falsy metric chain {chain!r}"
        )


def test_segment_metric_uses_an_explicit_numeric_check():
    """The metric pick must test the type explicitly, not by truthiness."""
    src = _analysis_src()
    assert "typeof" in src and "number" in src, (
        "analysis.js must use an explicit `typeof x === 'number'` check"
    )
    assert "Number.isFinite" in src, (
        "the numeric check must also reject NaN/Infinity"
    )
    # The metric keys are still the three the backend returns, in priority
    # order, but they are handed to a helper instead of an `||` chain.
    assert re.search(r"\['total',\s*'mean',\s*'count'\]", src), (
        "the segment metric priority must stay total -> mean -> count"
    )


def test_segment_chart_is_labelled_with_the_metric_it_plots():
    """The chart title must name the metric that was actually plotted."""
    src = _analysis_src()
    body = _extract(
        src, "async _runSegmentBreakdown()", "TAB 7: Custom KPI", "segment tab"
    )
    assert "metricKey" in body, (
        "the segment breakdown must remember which metric it plotted"
    )
    for label in ("'total'", "'mean'", "'count'"):
        assert label in body, (
            f"the segment breakdown must map the {label} metric to a label"
        )
    assert "row count" in body, (
        "a metric-less (count) breakdown must be labelled as a row count"
    )
    assert "chartTitle" in body, (
        "the chart title must be built from the metric that was plotted"
    )
    # The label must reach the chart plugin, not just a local variable.
    assert re.search(r"text:\s*chartTitle", body), (
        "the chart title must be the one that carries the metric label"
    )


def test_no_falsy_numeric_fallbacks_remain_in_analysis_js():
    """``|| 0`` / ``|| '-'`` on a numeric field is the same trap."""
    src = _analysis_src()
    offenders = [
        f"{i}: {line.strip()}"
        for i, line in enumerate(src.splitlines(), start=1)
        if re.search(r"\|\|\s*0\b", line) or re.search(r"\|\|\s*'-'", line)
    ]
    # The only `|| '-'` left must be on string fields (dtype, method,
    # test_name, winner) — those are legitimately "empty means unknown".
    numeric_offenders = [
        o for o in offenders
        if not re.search(r"dtype|method|test_name|winner", o)
    ]
    assert not numeric_offenders, (
        "a numeric field must not be formatted with `||` (0 is a value): "
        + "; ".join(numeric_offenders)
    )


@requires_node
def test_a_zero_valued_metric_is_not_treated_as_absent(tmp_path):
    """Behavioural check: the real helpers keep a 0 and drop the chain."""
    src = _analysis_src()
    helpers = _extract(
        src, "function pickNumericField(", "const AnalysisPage = {", "helpers"
    )
    body = f"""
{helpers}
const segments = [
  {{ segment: 'North', count: 3, total: 0, mean: 0 }},
  {{ segment: 'South', count: 2, total: 0, mean: 0 }},
  {{ segment: 'East',  count: 4, total: 120, mean: 30 }},
];

// 1. The metric pick must choose `total` — a real 0 is a value.
const key = pickNumericField(segments, ['total', 'mean', 'count']);
if (key !== 'total') {{
  console.error('FAIL: metric pick chose ' + key + ' instead of total');
  process.exit(1);
}}

// 2. The plotted value for the zero group must be 0, not its row count.
const values = segments.map(s => numericValue(s, key, 0));
if (values[0] !== 0 || values[1] !== 0) {{
  console.error('FAIL: zero group plotted as ' + JSON.stringify(values));
  process.exit(1);
}}
if (values[0] === segments[0].count) {{
  console.error('FAIL: the row count was plotted under the total label');
  process.exit(1);
}}
if (values[2] !== 120) {{
  console.error('FAIL: non-zero group plotted as ' + values[2]);
  process.exit(1);
}}

// 3. A group with no total at all must not borrow the count either.
const countOnly = pickNumericField([{{ count: 7 }}], ['total', 'mean', 'count']);
if (countOnly !== 'count') {{
  console.error('FAIL: metric-less breakdown chose ' + countOnly);
  process.exit(1);
}}

// 4. Nothing numeric at all -> no metric (never a silent fallback).
if (pickNumericField([{{ segment: 'x' }}], ['total', 'mean', 'count']) !== null) {{
  console.error('FAIL: a breakdown without any metric must return null');
  process.exit(1);
}}

// 5. numericValue must return the fallback for a missing field, and 0 for 0.
if (numericValue({{ total: 0 }}, 'total', 999) !== 0) {{
  console.error('FAIL: numericValue dropped a real 0');
  process.exit(1);
}}
if (numericValue({{}}, 'total', 999) !== 999) {{
  console.error('FAIL: numericValue must fall back for a missing field');
  process.exit(1);
}}

console.log('OK');
"""
    _assert_ok(_run_node(tmp_path, body))


# --------------------------------------------------------------------------
# Bug 2 — the recommendation card must show the reasoning
# --------------------------------------------------------------------------


def test_blankdash_reads_the_reasoning_field_the_api_returns():
    """The card must read ``reasoning`` — the key the API actually sends."""
    src = _blankdash_src()
    assert re.search(r"\.reasoning\b", src), (
        "blankdash.js must read the `reasoning` field returned by "
        f"{REASONING_KEY!r}"
    )
    assert "r.why || r.reason" not in src, (
        "the old `r.why || r.reason` read can never see the API's "
        "`reasoning` field — every card rendered as a bare title"
    )


def test_blankdash_uses_a_first_non_empty_string_helper():
    """Accepting several spellings must not reintroduce the falsy bug."""
    src = _blankdash_src()
    assert "_firstNonEmptyString" in src, (
        "blankdash.js must pick the explanation with a first-non-empty "
        "string helper (an empty string must not shadow a real value)"
    )
    body = _extract(
        src, "function _firstNonEmptyString(", "/* Convert a legacy grid",
        "first-non-empty helper",
    )
    assert "trim()" in body, (
        "the helper must skip blank strings, not only empty ones"
    )


@requires_node
def test_first_non_empty_string_prefers_the_real_reasoning(tmp_path):
    """Behavioural check of the helper + the card's field order."""
    src = _blankdash_src()
    helper = _extract(
        src, "function _firstNonEmptyString(", "/* Convert a legacy grid",
        "first-non-empty helper",
    )
    body = f"""
{helper}
// The API sends `reasoning`; the card must show it.
const rec = {{ title: 'Revenue by Region', reasoning: 'Compares total sales' }};
const why = _firstNonEmptyString(rec.reasoning, rec.why, rec.reason);
if (why !== 'Compares total sales') {{
  console.error('FAIL: reasoning not read, got ' + JSON.stringify(why));
  process.exit(1);
}}

// An empty `why` must not shadow a real `reasoning`.
const mixed = {{ reasoning: 'real', why: '', reason: '' }};
if (_firstNonEmptyString(mixed.reasoning, mixed.why, mixed.reason) !== 'real') {{
  console.error('FAIL: an empty string shadowed the real explanation');
  process.exit(1);
}}

// Nothing to show -> empty string, not "undefined".
if (_firstNonEmptyString(undefined, null, '', '   ') !== '') {{
  console.error('FAIL: empty explanation must render as an empty string');
  process.exit(1);
}}

console.log('OK');
"""
    _assert_ok(_run_node(tmp_path, body))
