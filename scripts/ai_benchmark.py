"""
Latency benchmark for the DataAgent AI features.

Runs the LLM-backed endpoints N times each and reports min / median / p95 /
max, plus whether the answer used the LLM or silently fell back to the
rule-based engine.

Usage:
    python scripts/ai_benchmark.py            # 3 runs per endpoint
    python scripts/ai_benchmark.py --runs 5
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ai_probe_common import BASE, TABLE, call, credentials, login, used_llm  # noqa: E402

USERNAME, PASSWORD = credentials()

TOKEN = ""


# name, method, path, body
FEATURES = [
    ("ask (NL question)", "POST", "/ask",
     {"question": "Which region has the highest total order amount?"}),
    ("ask-sql (NL->SQL)", "POST", "/ask-sql",
     {"question": "Show total amount by region, highest first."}),
    ("chat/stream (SSE)", "POST", "/llm/chat/stream",
     {"question": "Say hello in one short sentence."}),
    ("generate-code", "POST", "/llm/generate-code",
     {"prompt": "A Python function that returns the sum of a list.",
      "language": "python"}),
    ("rewrite", "POST", "/llm/rewrite",
     {"text": "track sales", "kind": "purpose"}),
    ("chart-recommend (LLM)", "POST", "/llm/chart-recommend",
     {"table_name": TABLE}),
    ("insights/llm (narrative)", "POST", "/analysis/insights/llm",
     {"table_name": TABLE}),
    ("insights (deterministic)", "GET", f"/analysis/insights/{TABLE}", None),
    ("auto-analyze", "POST", "/analysis/auto-analyze",
     {"table_name": TABLE}),
    ("forecast/advanced", "POST", "/analysis/forecast/advanced",
     {"table_name": TABLE, "date_col": "ordered_on", "value_col": "amount"}),
    ("regression", "POST", "/analysis/regression",
     {"table_name": TABLE, "target_col": "amount", "feature_cols": ["qty"]}),
    ("sql (direct, no LLM)", "POST", "/sql",
     {"query": "SELECT region, SUM(amount) t FROM ai_probe_sales GROUP BY region"}),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    TOKEN = login()

    # Warm up: the first LLM call loads the model into memory.
    print("warming up the model ...", flush=True)
    call("POST", "/llm/rewrite", {"text": "warm up", "kind": "purpose"}, token=TOKEN)

    print(f"\n{'FEATURE':<28} {'n':>2} {'min':>8} {'median':>8} {'max':>8}  LLM")
    print("-" * 76)

    rows = []
    for name, method, path, payload in FEATURES:
        times, llm_flags, statuses = [], [], []
        for _ in range(args.runs):
            status, ms, resp = call(method, path, payload, token=TOKEN)
            times.append(ms)
            statuses.append(status)
            llm_flags.append(used_llm(resp, status))
        times.sort()
        p95 = times[min(len(times) - 1, int(len(times) * 0.95))]
        all_llm = all(llm_flags)
        any_llm = any(llm_flags)
        mark = "yes" if all_llm else ("partial" if any_llm else "NO")
        if all(s == 200 for s in statuses):
            rows.append((name, len(times), times[0], statistics.median(times), p95, times[-1], mark))
        else:
            rows.append((name + f" [HTTP {statuses[-1]}]", len(times), times[0],
                         statistics.median(times), p95, times[-1], mark))

    for name, n, lo, med, p95, hi, mark in rows:
        print(f"{name:<28} {n:>2} {lo:>7.0f}ms {med:>7.0f}ms {hi:>7.0f}ms  {mark}")

    llm_rows = [r for r in rows if r[6] in ("yes", "partial")]
    if llm_rows:
        med = statistics.median(r[3] for r in llm_rows)
        print("-" * 76)
        print(f"LLM-backed endpoints: {len(llm_rows)}  "
              f"median {med:.0f}ms  "
              f"slowest {max(llm_rows, key=lambda r: r[3])[0]} "
              f"({max(r[3] for r in llm_rows):.0f}ms)")

    slow = [r for r in rows if r[3] > 3000]
    if slow:
        print("\nslower than 3s:")
        for name, n, lo, med, p95, hi, mark in sorted(slow, key=lambda r: -r[3]):
            print(f"  {name:<34} median {med:>7.0f}ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
