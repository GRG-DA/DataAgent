#!/usr/bin/env python3
"""
Verify the AI-feature fixes end-to-end, over the real HTTP API.

Every check is a behavioural promise the fix made, checked the way a user
would hit it - through the API the browser calls. Run it with the app up:

    python run.py api                # in one shell
    python scripts/verify_ai_fixes.py # in another

Exit code 0 = every check passed, 1 = at least one failed.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8002"
USERNAME = "uitest763740"
PASSWORD = "UiTest!2345x"
TABLE = "ai_probe_sales"

# The platform-owned tables that must never be visible to, or queryable by, a
# tenant user. The platform schema is on every tenant's search path, so a
# table missing from the platform set leaks to every tenant by name.
PLATFORM_TABLES = [
    "key_redemptions",
    "coupon_redemptions",
    "api_keys",
    "invoices",
    "payments",
    "payment_events",
    "subscriptions",
    "subscription_events",
    "user_subscriptions",
    "checkout_orders",
    "plans",
    "plan_prices",
    "plan_limits",
    "plan_features",
    "usage_records",
    "usage_events",
    "query_history",
    "query_costs",
    "cost_budgets",
    "budget_alerts",
    "masked_audit",
    "consent_records",
    "privacy_requests",
    "pii_inventory",
    "user_preferences",
    "notification_preferences",
    "notifications",
    "chat_conversations",
    "chat_messages",
    "dashboards",
    "dashboard_versions",
    "measures",
    "metric_definitions",
    "data_products",
    "catalog_assets",
    "catalog_tags",
    "business_terms",
    "org_groups",
    "org_teams",
    "org_departments",
    "org_group_members",
    "sla_definitions",
    "sla_results",
    "sla_breach_events",
    "workflow_definitions",
    "workflow_instances",
    "pipeline_runs",
    "webhook_endpoints",
    "share_links",
    "approval_actions",
    "product_access_requests",
    "product_ratings",
    "comments",
    "annotations",
    "calendar",
    "masking_rules",
    "retention_policies",
    "pbi_studios",
]

RESULTS: list[tuple[str, bool, str]] = []


def _safe(text: str) -> str:
    return (text or "").encode("ascii", "replace").decode("ascii")


def api(method: str, path: str, body=None, token=None, timeout=240):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def payload(resp, key: str):
    """The block a tool's result lives in.

    The analysis endpoints put their result at the TOP level
    (``{"success": true, "normality": {...}}``); some nest it under
    ``data``. Accept either, so the check follows the VALUE and not the
    envelope.
    """
    if not isinstance(resp, dict):
        return None
    if key in resp:
        return resp.get(key)
    data = resp.get("data")
    if isinstance(data, dict):
        return data.get(key)
    return None


def login() -> str:
    status, body = api("POST", "/auth/login",
                       {"username": USERNAME, "password": PASSWORD})
    if status != 200 or not isinstance(body, dict):
        raise SystemExit(f"login failed: HTTP {status} {str(body)[:200]}")
    return body["token"]


def main() -> int:
    print("=" * 78)
    print("AI FEATURE FIX VERIFICATION")
    print("=" * 78)

    token = login()

    # ── 1. AI-generated SQL must be executable ──────────────────────
    print("\n[1] AI-generated SQL executes (it used to be rejected)")
    status, gen = api("POST", "/llm/generate-code", {
        "prompt": "Which region has the highest total order amount?",
        "language": "sql",
        "context": [TABLE],
    }, token)
    check("generate-code returns 200", status == 200, f"HTTP {status}")
    sql = ""
    if isinstance(gen, dict):
        sql = (gen.get("code") or gen.get("sql") or "").strip()
    check("the model produced SQL", bool(sql), _safe(sql[:90]))
    check("the SQL is NOT schema-qualified",
          "analytics" not in sql.lower() and "." not in sql.split("FROM")[-1].split()[0]
          if sql else False,
          _safe(sql.split("FROM")[-1].split()[0]) if sql else "")
    if sql:
        status, run = api("POST", "/sql", {"query": sql}, token)
        rows = run.get("row_count") if isinstance(run, dict) else None
        check("the generated SQL runs", status == 200, f"HTTP {status}")
        check("the generated SQL returns rows", bool(rows),
              f"row_count={rows}")
        check("no access-denied error", "Access denied" not in str(run),
              _safe(str(run)[:120]))

    # ── 2. Platform tables must not reach the user ───────────────────
    print("\n[2] Platform tables stay hidden")
    status, tables = api("GET", "/tables", token=token)
    names = list((tables or {}).get("tables", {}).keys()) if isinstance(tables, dict) else []
    system = (tables or {}).get("system_tables", []) if isinstance(tables, dict) else []
    check("key_redemptions is not offered as a user table",
          "key_redemptions" not in names, f"user tables={names}")
    check("key_redemptions is known as a platform table",
          "key_redemptions" in system)

    # Every platform table must be BOTH hidden from the table list AND
    # unreadable by name. A platform table missing from the set leaks the
    # platform schema to every tenant (the schema is on the search path).
    leaked = []
    for plat in PLATFORM_TABLES:
        if plat not in system:
            leaked.append(f"{plat}:not-listed")
            continue
        if plat in names:
            leaked.append(f"{plat}:listed-as-user")
            continue
        st, body = api("POST", "/sql",
                       {"query": f"SELECT COUNT(*) AS count FROM {plat}"}, token)
        if st == 200:
            leaked.append(f"{plat}:readable")
    check("no platform table is readable by a normal user", not leaked,
          ", ".join(leaked) if leaked else f"{len(PLATFORM_TABLES)} checked")

    status, q = api("POST", "/sql", {
        "query": "SELECT COUNT(*) AS count FROM key_redemptions",
    }, token)
    check("a platform table cannot be queried", status != 200,
          f"HTTP {status} {_safe(str(q)[:90])}")

    # ── 3. The answer's Source chip must not name a platform table ────
    print("\n[3] The 'Source:' footer names only the user's own tables")
    status, ans = api("POST", "/ask", {
        "question": "Which region has the highest total order amount?",
        "table": TABLE,
    }, token, timeout=300)
    answer = ""
    if isinstance(ans, dict):
        answer = ans.get("answer") or ""
    check("/ask returns 200", status == 200, f"HTTP {status}")
    check("the answer is not a rule-based fallback",
          "rule-based fallback" not in answer.lower() and
          "no llm configured" not in answer.lower())
    src = ""
    for part in answer.split("*Source:"):
        if len(part) > 1:
            src = part.split("*")[0].strip()
            break
    check("the Source footer names only user tables",
          (not src) or all(t.strip() in names for t in src.split(",")),
          _safe(src))
    check("the Source footer names no platform table",
          "key_redemptions" not in answer and "analytics." not in answer,
          _safe(src))

    # ── 4. The Analysis page's LLM endpoint ─────────────────────────
    print("\n[4] The LLM insights endpoint answers (the page must call it)")
    status, ins = api("POST", "/analysis/insights/llm",
                      {"table_name": TABLE}, token, timeout=300)
    check("insights/llm returns 200", status == 200, f"HTTP {status}")
    narrative = ""
    if isinstance(ins, dict):
        narrative = (ins.get("insights") or {}).get("narrative") or ""
    check("the LLM wrote a narrative", len(narrative) > 80,
          f"{len(narrative)} chars")
    check("the narrative is not the rule-based template",
          "This dataset contains" not in narrative[:60])

    # ── 5. The four mis-rendered analysis tools ──────────────────────
    print("\n[5] The analysis tools return the shapes the UI reads")
    status, nrm = api("GET", f"/analysis/normality/{TABLE}/amount?method=shapiro",
                      token=token)
    n = payload(nrm, "normality")
    check("normality carries statistic/p_value/is_normal",
          isinstance(n, dict) and n.get("statistic") is not None
          and "p_value" in n and "is_normal" in n,
          _safe(json.dumps(n)[:110]))
    check("normality reports is_normal",
          isinstance(n, dict) and n.get("is_normal") is True,
          _safe(str((n or {}).get("is_normal"))))

    status, hy = api("POST", "/analysis/hypothesis",
                     {"table_name": TABLE, "test_type": "ttest",
                      "column_a": "amount", "column_b": "qty"}, token)
    h = payload(hy, "hypothesis_test")
    check("hypothesis carries statistic/p_value/significant",
          isinstance(h, dict) and h.get("statistic") is not None
          and "p_value" in h and "significant" in h,
          _safe(json.dumps(h)[:110]))
    check("a hypothesis failure is reported, never a verdict",
          not (isinstance(hy, dict) and hy.get("success") is False
               and h and h.get("significant") is True),
          _safe(str((hy or {}).get("error"))[:80]))

    status, ci = api("GET",
                     f"/analysis/confidence-interval/{TABLE}/amount?confidence=0.95&method=t",
                     token=token)
    c = payload(ci, "confidence_interval")
    check("confidence-interval carries mean/ci_lower/ci_upper",
          isinstance(c, dict) and c.get("mean") is not None
          and "ci_lower" in c and "ci_upper" in c,
          _safe(json.dumps(c)[:110]))

    status, ab = api("POST", "/analysis/ab-test",
                     {"table_name": TABLE, "group_col": "region",
                      "metric_col": "amount", "group_a": "North",
                      "group_b": "South", "alpha": 0.05}, token)
    a = payload(ab, "ab_test")
    check("ab-test carries statistic/p_value/interpretation",
          isinstance(a, dict) and a.get("statistic") is not None
          and "p_value" in a,
          _safe(json.dumps(a)[:110]))

    # ── 6. The segment column must be honoured ───────────────────────
    print("\n[6] The segment column is honoured")
    status, seg = api("GET",
                       f"/analysis/segmentation/{TABLE}?segment_col=region&metric_col=amount",
                       token=token)
    echoed = (seg or {}).get("segment_col") if isinstance(seg, dict) else None
    check("the response echoes the requested segment column",
          echoed == "region", f"segment_col={echoed}")

    # ── 7. The Settings surfaces ─────────────────────────────────────
    print("\n[7] The LLM provider state is visible")
    status, prov = api("GET", "/llm/providers", token=token)
    oc = {}
    if isinstance(prov, dict):
        for p in prov.get("providers", []):
            if p.get("name") == "opencode":
                oc = p
    check("opencode is the default provider",
          (prov or {}).get("default_provider") == "opencode",
          str((prov or {}).get("default_provider")))
    check("the opencode model is reported",
          (oc.get("config") or {}).get("model") == "auto",
          str((oc.get("config") or {}).get("model")))
    status, ver = api("GET", "/llm/providers/opencode/version", token=token)
    check("the CLI version is reported",
          isinstance(ver, dict) and ver.get("current", "").startswith("1."),
          str((ver or {}).get("current")) if isinstance(ver, dict) else "")
    status, srv = api("GET", "/llm/providers/opencode/server", token=token)
    check("the server state is reported",
          isinstance(srv, dict) and srv.get("running") is True,
          f"server_url={(srv or {}).get('server_url')}" if isinstance(srv, dict) else "")

    # ── summary ──────────────────────────────────────────────────────
    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    failed = len(RESULTS) - passed
    print("\n" + "=" * 78)
    print(f"{passed} passed / {failed} failed / {len(RESULTS)} checks")
    if failed:
        print("\nfailed checks:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  - {name}  {detail}")
    print("=" * 78)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
