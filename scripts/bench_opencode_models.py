"""
Time ONE opencode completion per (server, model) so the numbers are comparable.

The provider is the app's own (dialect detection, auth, session handling), so
what is measured here is exactly what the Data Agent AI would pay.

Usage:
    python scripts/bench_opencode_models.py --runs 1 --server http://127.0.0.1:3100
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.llm_client import (  # noqa: E402
    OpencodeConfig,
    OpencodeProvider,
)

# The free/no-key tier as the opencode server lists it right now. The tier
# rotates, so this is a snapshot; the "-free" suffix is the real gate.
FREE_MODELS = [
    "big-pickle",
    "beam-free",
    "flint-40b-free",
    "minimax-m3.1-flash-free",
    "nemotron-3.5-lightning-free",
    "nemotron-3-ultra-free",
    "nemotron-3.5-super-vl-free",
    "mimo-v2.6-flash-free",
    "deepseek-v4.1-flash-free",
    "glm-5.3-flash-free",
]

QUESTION = (
    "How many orders are in the sales table, and which region has the highest "
    "total amount? Answer in two short sentences."
)


def _safe(text: str) -> str:
    """ASCII-safe text (the Windows console is cp1252 and a model answer can
    carry characters it cannot encode)."""
    return (text or "").encode("ascii", "replace").decode("ascii")


async def probe(provider: OpencodeProvider, model: str, timeout: int) -> dict:
    """One timed round-trip. Returns a result dict (never raises)."""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": QUESTION}],
        "temperature": 0.1,
        "max_tokens": 256,
        "think": False,
        "timeout_ms": timeout * 1000,
    }
    started = time.perf_counter()
    try:
        resp = await provider._server_chat_once(body)
        elapsed = time.perf_counter() - started
        text = (resp.message.content or "").strip()
        return {
            "model": model,
            "ok": True,
            "seconds": round(elapsed, 2),
            "chars": len(text),
            "preview": text[:160].replace("\n", " "),
        }
    except Exception as exc:  # noqa: BLE001 - a benchmark must survive anything
        elapsed = time.perf_counter() - started
        return {
            "model": model,
            "ok": False,
            "seconds": round(elapsed, 2),
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
        }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--server", default="http://127.0.0.1:3000")
    ap.add_argument("--model", action="append", default=None,
                    help="restrict to these model ids (repeatable)")
    ap.add_argument("--out", default="outputs/opencode_model_bench.json")
    args = ap.parse_args()

    models = args.model or FREE_MODELS
    provider = OpencodeProvider(OpencodeConfig(
        server_url=args.server,
        model=models[0],
        auto_proxy_pool=False,
        auto_warp_reset=False,
    ))
    # Make sure the server is up before timing anything.
    await provider._ensure_server_started()

    results = []
    for model in models:
        samples = []
        for i in range(args.runs):
            r = await probe(provider, model, args.timeout)
            samples.append(r)
            status = "OK " if r["ok"] else "ERR"
            print(f"  [{status}] {model:<26} run {i + 1}/{args.runs}  "
                  f"{r['seconds']:>7.2f}s  {_safe(r.get('error', r.get('preview', '')))[:88]}",
                  flush=True)
            if not r["ok"]:
                break  # a failed model does not get retried into more 429s
            await asyncio.sleep(1.0)  # be gentle with the per-IP free quota
        ok = [s for s in samples if s["ok"]]
        results.append({
            "server": args.server,
            "model": model,
            "runs_ok": len(ok),
            "runs_failed": len(samples) - len(ok),
            "median_seconds": round(statistics.median([s["seconds"] for s in ok]), 2) if ok else None,
            "min_seconds": round(min(s["seconds"] for s in ok), 2) if ok else None,
            "error": next((s.get("error") for s in samples if not s["ok"]), None),
            "sample_preview": ok[0].get("preview") if ok else None,
        })

    await provider.close()

    print("\n" + "=" * 78)
    print(f"{'MODEL':<28}{'MEDIAN s':>10}{'MIN s':>9}{'OK':>5}  NOTE")
    print("-" * 78)
    for r in sorted(results, key=lambda x: (x["median_seconds"] is None, x["median_seconds"] or 0)):
        med = f"{r['median_seconds']:.2f}" if r["median_seconds"] is not None else "-"
        mn = f"{r['min_seconds']:.2f}" if r["min_seconds"] is not None else "-"
        note = (r["error"] or "ok")[:24]
        print(f"{r['model']:<28}{med:>10}{mn:>9}{r['runs_ok']:>5}  {_safe(note)}")
    print("=" * 78)

    winners = [r for r in results if r["median_seconds"] is not None]
    if winners:
        best = min(winners, key=lambda x: x["median_seconds"])
        print(f"FASTEST: {best['model']}  ({best['median_seconds']}s median)")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")

    # A plain-text report next to the JSON: the console may not be able to
    # show everything, but the file always can.
    report = out.with_suffix(".txt")
    lines = [f"server: {args.server}", f"{'MODEL':<28}{'MEDIAN s':>10}{'MIN s':>9}{'OK':>5}  NOTE",
             "-" * 78]
    for r in sorted(results, key=lambda x: (x["median_seconds"] is None, x["median_seconds"] or 0)):
        med = f"{r['median_seconds']:.2f}" if r["median_seconds"] is not None else "-"
        mn = f"{r['min_seconds']:.2f}" if r["min_seconds"] is not None else "-"
        lines.append(f"{r['model']:<28}{med:>10}{mn:>9}{r['runs_ok']:>5}  {(r['error'] or 'ok')[:60]}")
    if winners:
        best = min(winners, key=lambda x: x["median_seconds"])
        lines.append("")
        lines.append(f"FASTEST: {best['model']}  ({best['median_seconds']}s median)")
        lines.append(f"answer sample: {best.get('sample_preview') or ''}")
    report.write_text("\n".join(lines), encoding="utf-8")

    print(f"wrote {out}")
    print(f"wrote {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
