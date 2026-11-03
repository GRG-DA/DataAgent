"""
Probe the DataAgent AI features over the real API: latency + response sanity.

Usage:  python scripts/ai_feature_probe.py
Requires the app running on :8002 (python run.py web) and a reachable LLM.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# The responses contain emoji and box-drawing characters; a cp1252 console
# cannot encode them.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ai_probe_common import BASE, TABLE, credentials  # noqa: E402

USERNAME, PASSWORD = credentials()

TOKEN = ""
RESULTS: list[dict] = []


def call(method: str, path: str, body=None, timeout=240):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            ms = (time.perf_counter() - start) * 1000
            try:
                return resp.status, ms, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, ms, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        ms = (time.perf_counter() - start) * 1000
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, ms, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, ms, raw
    except Exception as e:  # noqa: BLE001
        ms = (time.perf_counter() - start) * 1000
        return 0, ms, str(e)[:300]


def login() -> bool:
    global TOKEN
    status, _, body = call("POST", "/auth/login",
                           {"username": USERNAME, "password": PASSWORD})
    if status == 200 and isinstance(body, dict) and body.get("token"):
        TOKEN = body["token"]
        return True
    print(f"login failed: {status} {body}")
    return False


def text_of(body) -> str:
    if isinstance(body, str):
        return body
    if not isinstance(body, dict):
        return json.dumps(body, default=str)
    for key in ("answer", "response", "text", "content", "code", "sql",
                "explanation", "result", "message", "error", "detail"):
        val = body.get(key)
        if isinstance(val, str) and val.strip():
            return val
        if isinstance(val, list) and val:
            return json.dumps(val, default=str)
    return json.dumps(body, default=str)


def check(name: str, method: str, path: str, body=None, timeout=240,
          expect_keys=("answer", "success", "text", "response", "content", "code")):
    status, ms, resp = call(method, path, body, timeout=timeout)
    txt = text_of(resp)
    txt = " ".join(txt.split())
    ok = status == 200
    blob = json.dumps(resp, default=str).lower()
    fell_back = any(k in blob for k in
                    ("fallback", "rule-based", "no llm", "not configured"))
    used_llm = ok and not fell_back
    RESULTS.append({
        "name": name, "status": status, "ms": round(ms),
        "ok": ok, "llm": used_llm, "fallback": fell_back,
        "sample": txt[:260],
    })
    flag = "OK " if ok else "ERR"
    mark = "LLM" if used_llm else ("FALLBACK" if fell_back else "-")
    print(f"{flag} {name:<28} {status:>3}  {ms:>7.0f}ms  {mark:<8} {txt[:150]}")
    return resp


def run() -> int:
    if not login():
        return 1

    print("=" * 92)
    print("AI FEATURE PROBE — latency and response quality")
    print("=" * 92)

    # ---- 0. Backend health -------------------------------------------
    status, ms, body = call("GET", "/llm/providers")
    print(f"\n[backend] /llm/providers  HTTP {status}  {ms:.0f}ms")
    if isinstance(body, dict):
        for p in body.get("providers", []):
            if p.get("name") in ("ollama", "opencode", "lmstudio", "openai", "anthropic"):
                print(f"   {p['name']:<10} status={p.get('status'):<12} "
                      f"model={p.get('config', {}).get('model')}")

    # ---- 1. Core question answering ----------------------------------
    check("POST /ask", "POST", "/ask",
          {"question": "Which region has the highest total order amount? "
                       "Answer in one sentence with the number."})
    check("POST /ask (scoped)", "POST", "/ask",
          {"question": "What is the average order amount?",
           "table": TABLE})

    # ---- 2. Natural language -> SQL ----------------------------------
    check("POST /ask-sql", "POST", "/ask-sql",
          {"question": "Show total amount by region, highest first."})

    # ---- 3. Streaming chat -------------------------------------------
    check("POST /llm/chat/stream", "POST", "/llm/chat/stream",
          {"question": "Say hello in one short sentence."})

    # ---- 4. Code generation ------------------------------------------
    check("POST /llm/generate-code", "POST", "/llm/generate-code",
          {"prompt": "A Python function that returns the sum of a list of numbers.",
           "language": "python"})

    # ---- 5. Rewrite (AI Auto-Build wizard) ---------------------------
    check("POST /llm/rewrite", "POST", "/llm/rewrite",
          {"text": "track sales", "kind": "purpose"})

    # ---- 6. Chart recommendation -------------------------------------
    check("GET chart-recommend", "GET",
          f"/analysis/chart-recommend/{TABLE}")
    check("POST /llm/chart-recommend", "POST", "/llm/chart-recommend",
          {"table_name": TABLE})

    # ---- 7. LLM insights ---------------------------------------------
    check("POST /analysis/insights/llm", "POST", "/analysis/insights/llm",
          {"table_name": TABLE})

    # ---- 8. Auto-analyze ---------------------------------------------
    check("POST /analysis/auto-analyze", "POST", "/analysis/auto-analyze",
          {"table_name": TABLE})

    # ---- 9. Statistical analysis (LLM-assisted paths) ----------------
    check("GET /analysis/insights", "GET",
          f"/analysis/insights/{TABLE}")
    check("GET /analysis/relationships", "GET", "/analysis/relationships")
    check("GET /analysis/forecast", "GET", f"/analysis/forecast/{TABLE}")
    check("GET /analysis/anomalies", "GET", f"/analysis/anomalies/{TABLE}")
    check("GET /analysis/correlation", "GET", f"/analysis/correlation/{TABLE}")
    check("GET /analysis/normality", "GET", f"/analysis/normality/{TABLE}")
    check("GET /analysis/decomposition", "GET", f"/analysis/decomposition/{TABLE}")
    check("GET /analysis/survival", "GET", f"/analysis/survival/{TABLE}")
    check("GET /analysis/segmentation", "GET", f"/analysis/segmentation/{TABLE}")
    check("GET confidence-interval", "GET",
          f"/analysis/confidence-interval/{TABLE}/amount")
    check("POST /analysis/hypothesis", "POST", "/analysis/hypothesis",
          {"table_name": TABLE, "column_a": "amount", "column_b": "qty",
           "test_type": "ttest", "group_col": "region"})
    check("POST /analysis/regression", "POST", "/analysis/regression",
          {"table_name": TABLE, "target_col": "amount",
           "feature_cols": ["qty"]})
    check("POST /analysis/feature-importance", "POST",
          "/analysis/feature-importance",
          {"table_name": TABLE, "target_col": "amount"})
    check("POST /analysis/kpi", "POST", "/analysis/kpi",
          {"formula": "SELECT SUM(amount) AS total FROM ai_probe_sales",
           "name": "Total Amount"})
    check("POST /analysis/churn", "POST", "/analysis/churn",
          {"table_name": TABLE, "user_id_col": "order_id",
           "date_col": "ordered_on"})
    check("POST /analysis/cohort", "POST", "/analysis/cohort",
          {"table_name": TABLE, "user_id_col": "order_id",
           "date_col": "ordered_on", "revenue_col": "amount"})
    check("POST /analysis/bayesian", "POST", "/analysis/bayesian",
          {"table_name": TABLE, "column_a": "amount", "column_b": "qty"})
    check("POST /analysis/compare-models", "POST", "/analysis/compare-models",
          {"table_name": TABLE, "target_col": "amount"})
    check("POST /analysis/auto-select-features", "POST",
          "/analysis/auto-select-features",
          {"table_name": TABLE, "target_col": "amount"})
    check("POST /analysis/forecast/advanced", "POST",
          "/analysis/forecast/advanced",
          {"table_name": TABLE, "date_col": "ordered_on",
           "value_col": "amount"})
    check("POST /analysis/compare/periods", "POST",
          "/analysis/compare/periods",
          {"table_name": TABLE, "period_column": "ordered_on",
           "value_column": "amount"})
    check("POST /analysis/export-model", "POST", "/analysis/export-model",
          {"table_name": TABLE, "target_col": "amount",
           "feature_cols": ["qty"], "model_type": "linear"})

    # ---- 10. SQL execution (non-AI but the AI's substrate) ------------
    check("POST /sql", "POST", "/sql",
          {"query": "SELECT region, SUM(amount) AS total FROM ai_probe_sales GROUP BY region ORDER BY total DESC"})

    # ---- 11. Storytelling (premium — expected to gate) ----------------
    check("POST /analysis/storytelling", "POST", "/analysis/storytelling",
          {"table_name": TABLE})

    # ---- 12. Image -> dashboard rebuild (Dashboard Studio) -------------
    check("POST /dashstudio/analyze-image", "POST", "/dashstudio/analyze-image",
          {"image_base64": "", "table_name": TABLE}, timeout=120)

    # ---- Summary ------------------------------------------------------
    print("\n" + "=" * 92)
    print(f"{'FEATURE':<30} {'HTTP':>4} {'LATENCY':>9}  {'LLM?':<9} NOTE")
    print("-" * 92)
    for r in RESULTS:
        note = ""
        if r["fallback"]:
            note = "!! used the rule-based fallback, not the LLM"
        elif not r["ok"]:
            note = "!! failed"
        print(f"{r['name']:<30} {r['status']:>4} {r['ms']:>7}ms  "
              f"{('LLM' if r['llm'] else ('FALLBACK' if r['fallback'] else '-')):<9} {note}")

    ok = [r for r in RESULTS if r["ok"]]
    err = [r for r in RESULTS if not r["ok"]]
    fb = [r for r in RESULTS if r["fallback"]]
    times = sorted(r["ms"] for r in ok)
    print("-" * 92)
    print(f"{len(ok)} ok / {len(err)} failed / {len(fb)} silently fell back")
    if times:
        print(f"latency  min {times[0]:.0f}ms   median {times[len(times)//2]:.0f}ms   "
              f"max {times[-1]:.0f}ms")
    if err:
        print("\nfailures:")
        for r in err:
            print(f"  {r['name']}: HTTP {r['status']} — {r['sample'][:120]}")
    if fb:
        print("\nsilent fallbacks (feature answered without the LLM):")
        for r in fb:
            print(f"  {r['name']}: {r['sample'][:120]}")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    sys.exit(run())
