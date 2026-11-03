"""
Analyst-claim benchmark: "manual toolkit vs DataAgent agent + verify".

Empirically tests the claim made in the analyst-tool comparison:

    "the analyst uses the software, then verifies - minutes for 90%
     confidence, vs ~4 hours manual for the same confidence"

One analytical question is answered three independent ways on the same
table, each timed:

  G  ground truth   - direct deterministic SQL via POST /sql (no LLM)
  A  manual path    - scripted analyst phases (SQL extract -> pandas clean
                      -> analysis -> chart -> narrative report)
  B  agent path     - natural-language question via POST /ask (LLM or
                      rule-fallback, flagged by llm_used)
  S  spot check     - a second, differently-shaped SQL aggregate
                      (the "analyst verifies" step)

Confidence model: G, A and S are three independent computations of the
same numbers; agreement between them is the confidence evidence. The
agent's numbers are compared against G for correctness, and its wall
time is the "minutes" half of the claim.

Usage:
    python scripts/bench_analyst_vs_agent.py            # 3 rounds
    python scripts/bench_analyst_vs_agent.py --rounds 5
    python scripts/bench_analyst_vs_agent.py --csv data/raw/sample_sales.csv \
        --upload-table bench_sales

The tenant table must exist for the probe account (ai_probe_setup.py
seeds a 12-row ai_probe_sales; --csv + --upload-table uploads a bigger
file as its own table before benchmarking). Artifacts land in
outputs/bench/.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.request
import uuid
from collections import OrderedDict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ai_probe_common import BASE, call, login  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "bench"

# Published analyst norms used ONLY for the human-time estimate column
# (machine timings below are not comparable to human effort).
PHASES_HUMAN_MINUTES = {
    "sql_extract": (45, 60),
    "pandas_clean": (30, 45),
    "analysis": (45, 60),
    "chart": (60, 90),
    "report": (30, 30),
}

QUESTION = (
    "Which region drove the month-over-month revenue change in {table}? "
    "Compare the two most recent months and name the region with the "
    "largest revenue change, with the numbers."
)


def table_exists(token: str, table: str) -> bool:
    status, _, body = call("GET", "/tables", token=token)
    if status != 200 or not isinstance(body, dict):
        return False
    return table in (body.get("tables") or {})


def upload_csv(token: str, table: str, csv_path: Path) -> bool:
    """Upload a CSV through the app's own /upload + /load endpoints.

    The app process is the single warehouse writer (one-writer rule);
    this script never touches warehouse.duckdb directly.
    """
    payload = csv_path.read_bytes()
    boundary = "----bench" + uuid.uuid4().hex
    parts = [
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; '
        f'filename="{csv_path.name}"\r\n'.encode(),
        b"Content-Type: text/csv\r\n\r\n",
        payload,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    req = urllib.request.Request(
        BASE + "/upload", data=b"".join(parts), method="POST")
    req.add_header("Content-Type",
                   f"multipart/form-data; boundary={boundary}")
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=120) as resp:
        if resp.status != 200:
            return False
    status, _, body = call("POST", "/load", token=token, body={
        "filename": csv_path.name, "sheet": "default", "table_name": table,
    })
    return status == 200 and isinstance(body, dict) and bool(body.get("success"))


def ensure_table(token: str, table: str, csv_path: Path | None) -> bool:
    if table_exists(token, table):
        return True
    if csv_path is None or not csv_path.exists():
        return False
    return upload_csv(token, table, csv_path)


def sql_rows(token: str, query: str):
    status, ms, body = call("POST", "/sql", token=token, body={"query": query})
    if status != 200:
        raise RuntimeError(f"/sql failed {status}: {str(body)[:200]}")
    if not isinstance(body, dict):
        return [], ms
    if body.get("success") is False:
        raise RuntimeError(
            f"/sql error: {body.get('error', '?')!s:.200}")
    rows = body.get("rows")
    if rows is None:
        rows = body.get("data")
    return rows or [], ms


def mom_deltas(grid, months) -> dict:
    """Per-region MoM change for the latest complete month."""
    prev, cur = months[-2], months[-1]
    regions = sorted({r for r, _ in grid})
    delta = {r: round(grid.get((r, cur), 0.0) - grid.get((r, prev), 0.0), 2)
             for r in regions}
    total_delta = round(sum(delta.values()), 2)
    contribution = {}
    for r in regions:
        contribution[r] = round(
            (delta[r] / total_delta * 100.0)
            if abs(total_delta) > 1e-9 else 0.0, 1)
    top = max(regions, key=lambda r: abs(delta[r]))
    return {
        "prev_month": prev, "cur_month": cur, "delta": delta,
        "total_delta": total_delta, "contribution_pct": contribution,
        "top_region": top,
    }


# --- Path A: manual analyst workflow (scripted, each phase timed) ----------


def run_manual(token: str, table: str) -> dict:
    """Run every manual analyst phase, timed.

    Scripted execution measures machine time per phase; the human-effort
    column in the report comes from published analyst norms instead, so
    the two are never conflated.
    """
    import pandas as pd

    phases: "OrderedDict[str, float]" = OrderedDict()
    # Aggregate in SQL, not row-by-row: /sql caps inline rows at 1000
    # (row_count reports the true n), so the analyst pattern here is the
    # standard GROUP BY extract — exactly what a human analyst does with
    # a capped client.
    rows, ms = sql_rows(
        token,
        f"SELECT substr(CAST(order_date AS VARCHAR), 1, 7) AS m, region, "
        f"ROUND(SUM(revenue), 2) AS rev FROM {table} GROUP BY 1, 2 "
        f"ORDER BY 1, 2",
    )
    phases["sql_extract"] = ms / 1000.0

    t0 = time.perf_counter()
    df = pd.DataFrame(rows)
    df["rev"] = pd.to_numeric(df["rev"])
    df["region"] = df["region"].astype(str).str.strip()
    df = df.dropna()
    phases["pandas_clean"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    pivot = df.pivot_table(index="m", columns="region",
                           values="rev", aggfunc="sum").sort_index()
    pivot = pivot.fillna(0.0)
    prev, cur = str(pivot.index[-2]), str(pivot.index[-1])
    delta = (pivot.loc[cur] - pivot.loc[prev]).round(2)
    total_delta = round(float(delta.sum()), 2)
    contribution = {
        r: (round(float(delta[r]) / total_delta * 100, 1)
            if abs(total_delta) > 1e-9 else 0.0)
        for r in pivot.columns
    }
    analysis = {
        "prev_month": prev, "cur_month": cur,
        "delta": {str(k): round(float(v), 2) for k, v in delta.items()},
        "total_delta": total_delta,
        "contribution_pct": contribution,
        "top_region": str(delta.abs().idxmax()),
        "pivot_json": {
            str(m): {r: round(float(pivot.loc[m, r]), 2)
                     for r in pivot.columns}
            for m in pivot.index},
    }
    phases["analysis"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    chart_path = phase_chart(pivot, list(pivot.columns))
    phases["chart"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    best = analysis["top_region"]
    report_text = (
        f"Report: comparing {analysis['prev_month']} with "
        f"{analysis['cur_month']}, total revenue changed by "
        f"{analysis['total_delta']:,.2f}. The largest single driver was "
        f"{best} ({analysis['delta'][best]:,.2f}; "
        f"{analysis['contribution_pct'][best]:,.1f}% of the net change). "
        f"Per-region changes: "
        + ", ".join(f"{r} {v:+,.2f}"
                    for r, v in sorted(analysis["delta"].items())) + "."
    )
    phases["report"] = time.perf_counter() - t0

    return {"phases": dict(phases), "analysis": analysis,
            "chart": chart_path, "report": report_text,
            "total_s": round(sum(phases.values()), 3)}


def phase_chart(pivot, regions) -> str:
    """Render a grouped bar chart of revenue by region x month."""
    months = [str(m) for m in pivot.index]
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "manual_chart.png"
    try:
        import plotly.graph_objects as go

        fig = go.Figure()
        for r in regions:
            fig.add_bar(x=months,
                        y=[float(pivot.loc[m, r]) for m in pivot.index],
                        name=r)
        fig.update_layout(barmode="group",
                          title="Revenue by region x month",
                          xaxis_title="Month", yaxis_title="Revenue")
        fig.write_image(str(path), scale=2)
    except Exception:  # noqa: BLE001  (matplotlib fallback)
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        width = 0.8 / max(1, len(regions))
        fig, ax = plt.subplots(figsize=(10, 5))
        for i, r in enumerate(regions):
            ys = [float(pivot.loc[m, r]) for m in pivot.index]
            xs = [j + i * width for j in range(len(months))]
            ax.bar(xs, ys, width=width, label=r)
        ax.set_xticks([j + (len(regions) - 1) * width / 2
                       for j in range(len(months))])
        ax.set_xticklabels(months)
        ax.legend()
        fig.savefig(str(path), dpi=150, bbox_inches="tight")
    return str(path)


# --- Path B: agent + verify -------------------------------------------------


def agent_ask(token: str, table: str) -> dict:
    question = QUESTION.format(table=table)
    status, ms, body = call("POST", "/ask", token=token,
                            body={"question": question, "table": table})
    out = {"status": status, "http_s": round(ms / 1000.0, 3)}
    if status == 200 and isinstance(body, dict):
        out.update({
            "execution_time_ms": body.get("execution_time_ms"),
            "llm_used": bool(body.get("llm_used")),
            "success": bool(body.get("success")),
            "steps": len(body.get("steps") or []),
            "n_viz": len(body.get("visualizations") or []),
            "answer": str(body.get("answer", "")),
            "error": body.get("error"),
        })
    else:
        out["error"] = str(body)[:250]
    return out


def spot_check(token: str, table: str, months) -> dict:
    """The "analyst verifies" step: recompute with a different query shape."""
    prev, cur = months[-2], months[-1]
    q = (
        f"SELECT region, "
        f"SUM(CASE WHEN substr(CAST(order_date AS VARCHAR),1,7) = '{prev}' "
        f"THEN revenue ELSE 0 END) AS prev_rev, "
        f"SUM(CASE WHEN substr(CAST(order_date AS VARCHAR),1,7) = '{cur}' "
        f"THEN revenue ELSE 0 END) AS cur_rev "
        f"FROM {table} GROUP BY region"
    )
    rows, ms = sql_rows(token, q)
    delta = {str(r.get("region")): round(
        float(r.get("cur_rev")) - float(r.get("prev_rev")), 2) for r in rows}
    top = max(delta, key=lambda k: abs(delta[k]))
    return {"delta": delta, "total_delta": round(sum(delta.values()), 2),
            "top_region": top, "spot_s": round(ms / 1000.0, 3)}


def agree(a: dict, b: dict, tol: float = 0.01) -> bool:
    same_region = a.get("top_region") == b.get("top_region")
    da, db = a.get("delta", {}), b.get("delta", {})
    keys = set(da) & set(db)
    if not keys:
        return False
    back = all(abs(da[k] - db[k]) <= tol for k in keys)
    return bool(same_region and back)


def agent_answer_checks(answer: str, truth: dict) -> dict:
    """Did the agent actually answer the MoM question asked?

    Two independent signals:
      - month reference: the answer mentions the compared months
      - truth value: the answer contains one of the real per-region deltas
    A region name alone is NOT enough: in this dataset the largest
    region by total revenue and the largest MoM change happen to be the
    same region, so a totals answer passes a name-only check by luck.
    """
    text = (answer or "").lower()
    months = [truth["prev_month"], truth["cur_month"]]
    months_mentioned = any(m.lower() in text for m in months)
    values = [f"{v:,.2f}" for v in truth["delta"].values()]
    truth_value_mentioned = any(v in (answer or "") for v in values)
    return {
        "months_mentioned": months_mentioned,
        "truth_value_mentioned": truth_value_mentioned,
        "answers_mom_question": bool(months_mentioned or truth_value_mentioned),
    }


def verify_agent(token: str, table: str, truth: dict) -> float:
    """Human-style verification of the agent answer: a spot-check query
    against the truth, timed (the 'analyst verifies' step)."""
    spot = spot_check(token, table,
                      [truth["prev_month"], truth["cur_month"]])
    okay = agree(truth, spot)
    print(f"  verify: spot-check agrees with ground truth = {okay} "
          f"({spot['spot_s']*1000:.0f} ms)")
    return spot["spot_s"]


def human_minutes(label: str) -> str:
    lo, hi = PHASES_HUMAN_MINUTES[label]
    return f"{lo}-{hi}m"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default="ai_probe_sales")
    ap.add_argument("--csv", default=str(
        ROOT / "data" / "raw" / "sample_sales.csv"))
    ap.add_argument("--upload-table", default="bench_sales",
                    help="table name created from --csv when --table "
                         "is missing (empty string disables upload)")
    ap.add_argument("--rounds", type=int, default=3)
    args = ap.parse_args()

    token = login()
    table = args.table
    if not table_exists(token, table):
        up = args.upload_table
        csv_path = Path(args.csv) if args.csv else None
        if not up:
            print(f"table {table!r} missing and upload disabled; abort")
            return 2
        ok = ensure_table(token, up, csv_path)
        print(f"upload {Path(args.csv).name} -> {up}: "
              f"{'ok' if ok else 'FAILED'}")
        if not ok:
            return 2
        table = up

    # G: ground truth
    grid, months, ground_ms = (None, None, None)
    rows, gms = sql_rows(
        token,
        f"SELECT region, substr(CAST(order_date AS VARCHAR), 1, 7) AS m, "
        f"ROUND(SUM(revenue), 2) AS rev FROM {table} "
        f"GROUP BY 1, 2 ORDER BY 1, 2",
    )
    grid = {(str(r.get("region")), str(r.get("m"))): round(
        float(r.get("rev")), 2) for r in rows}
    months = sorted({m for _, m in grid})
    ground_ms = gms
    if len(months) < 3:
        print(f"need >= 3 months, got {len(months)}: {months}")
        return 3
    truth = mom_deltas(grid, months)
    print(f"\nground truth: table={table} months={months[0]}..{months[-1]} "
          f"cells={len(grid)} sql_ms={ground_ms:.0f}")
    print(f"  {truth['prev_month']} -> {truth['cur_month']} "
          f"total_delta={truth['total_delta']:+,.2f} "
          f"top={truth['top_region']}")

    # S: the verification spot-check (also used for every round's verify step)
    spot = spot_check(token, table, months)
    spot_ok = agree(truth, spot)
    print(f"spot check against truth: agree={spot_ok} "
          f"({spot['spot_s']*1000:.0f} ms)\n")

    rounds = []
    for i in range(1, args.rounds + 1):
        manual = run_manual(token, table)
        manual_ok = agree(truth, manual["analysis"])
        agent = agent_ask(token, table)
        checks = {"months_mentioned": None, "truth_value_mentioned": None,
                  "answers_mom_question": None}
        if agent.get("status") == 200:
            checks = agent_answer_checks(agent.get("answer", ""), truth)
            verify_s = verify_agent(token, table, truth)
        else:
            verify_s = 0.0
            print(f"  verify: agent call failed "
                  f"{agent.get('status')} - skipped")
        print(f"round {i}: agent http={agent.get('http_s', 0):>6.2f}s "
              f"pipeline={((agent.get('execution_time_ms') or 0) / 1000):>6.2f}s "
              f"llm={'Y' if agent.get('llm_used') else 'n'} "
              f"steps={agent.get('steps', 0)} "
              f"manual_ok={manual_ok} "
              f"agent_mom_answer={checks['answers_mom_question']}")
        rounds.append({
            "round": i,
            "manual": manual,
            "manual_matches_truth": manual_ok,
            "agent": {**agent, "answer": str(agent.get("answer", ""))[:400]},
            "agent_answer_checks": checks,
            "verify_s": verify_s,
        })

    def med(xs):
        vals = sorted(xs)
        return statistics.median(vals) if vals else 0.0

    mt = med([r["manual"]["total_s"] for r in rounds])
    at = med([r["agent"].get("http_s", 0) for r in rounds])
    vt = med([r["verify_s"] for r in rounds])
    manual_ok_all = all(r["manual_matches_truth"] for r in rounds)
    agent_answered = any(
        (r.get("agent_answer_checks") or {}).get("answers_mom_question")
        for r in rounds)
    llm_used = any(r["agent"].get("llm_used") for r in rounds)
    agent_times = [r["agent"].get("http_s", 0) for r in rounds]
    min_a, max_a = (min(agent_times), max(agent_times)) if agent_times \
        else (0.0, 0.0)

    human_total_min = sum(
        (lo + hi) / 2 for lo, hi in PHASES_HUMAN_MINUTES.values())
    hm, hf = divmod(int(human_total_min), 60)

    print("\n=== RESULT ===")
    print(f"{'phase':<14}{'machine median':>18}{'human estimate':>17}")
    for name, secs in rounds[0]["manual"]["phases"].items():
        print(f"{name:<14}{secs:>15.2f}s{human_minutes(name):>17}")
    print(f"{'MANUAL total':<14}{mt:>15.2f}s{f'{hm}h{hf:02d}m':>17}"
          f"  (scripted = machine time; human from norms)")
    print(f"agent /ask: median {at:.2f}s (n={len(rounds)}, "
          f"min {min_a:.2f}s max {max_a:.2f}s)")
    print(f"agent verify step (spot-check): median {vt*1000:.0f}ms")
    print(f"agent total (ask + verify): "
          f"{at + (med([r2.get('verify_s', 0) for r2 in rounds]) if rounds else 0):.2f}s")
    print(f"manual matches ground truth, all rounds: {manual_ok_all}")
    print(f"spot check matches ground truth: {spot_ok}")
    print(f"agent actually answered the MoM question: {agent_answered}")
    if not agent_answered:
        print("  -> agent under-answered (summary/totals instead of MoM "
              "comparison); the verify step is what catches this")
    if not llm_used:
        print("NOTE: llm=false on every round -> rule-engine fallback "
              "(timings are NOT 'AI speed')")
    corridor = ("3-of-3 independent computations agree"
                if manual_ok_all and spot_ok else "agreement incomplete")
    print(f"confidence corridor: {corridor}")

    OUT.mkdir(parents=True, exist_ok=True)
    result_path = OUT / "bench_analyst_vs_agent.json"
    result_path.write_text(
        json.dumps({
            "base": BASE,
            "table": table,
            "months": months,
            "ground_truth": truth,
            "ground_truth_sql_s": ground_ms / 1000.0,
            "spot_check": spot,
            "spot_matches_truth": spot_ok,
            "rounds": rounds,
            "manual_total_median_s": mt,
            "agent_http_median_s": at,
            "verify_median_s": vt,
            "agent_total_median_s": round(at + vt, 3),
            "agent_llm_used_any_round": llm_used,
            "manual_matches_truth_all_rounds": manual_ok_all,
            "agent_answered_mom_any_round": agent_answered,
            "human_estimate_minutes": {
                k: list(v) for k, v in PHASES_HUMAN_MINUTES.items()},
            "human_total_estimate_minutes": human_total_min,
            "corridor": corridor,
        }, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\nwrote {result_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
