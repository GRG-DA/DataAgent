# ARCHIVED — trading-bot research (does not apply to DataAgent)

> This file describes a `trading-agent` demo (`collector.py`, `trader.py`,
> backtests) that does not exist in this tree. Kept for history only.
> Current product: local-first LLM data analytics agent — see `README.md` and
> `docs/decisions/0001-frontend-and-docs-baseline.md`.

---

# Research verdict: will this plan work?

## Original question: 2 scripts (history + live) + pattern + local LLM + wallet = profit?

**Technically YES for demo, NO guarantee for profit.** Split verdict:

| Part | Verdict | Evidence |
|---|---|---|
| History collector (CCXT public OHLCV) | Works, free | CCXT `fetch_ohlcv`, `set_sandbox_mode(True)` verified in /ccxt/ccxt docs; Binance public has no key for market data |
| Live watcher | Works via polling | Websocket possible but polling every 60s chosen for reliability on home internet/power cuts in India |
| Pattern (EMA20/50 + RSI14) | Works mechanically, not edge | Standard, crowded. **Confirmed by measurement, see below** |
| Local LLM RTX 5050 8GB | Works for 7-8B Q4 | Llama 3.1 8B ~4.9GB/6.2GB VRAM, Mistral 7B ~4.5GB, 45-52 tok/s; 14B needs 12GB — excluded. Ollama `chat(..., format="json")` verified in /ollama/ollama-python docs |
| Wallet auto-buy + profit | Works technically, DANGEROUS live | web3.py + approvals risk unlimited drain; drainers/AI-agent key risks documented. Hence: DRY_RUN + Testnet only, LLM never signs |
| Budget 500 Rs | Feasible at Rs 0 | DuckDB local, CCXT free, Ollama free, Testnet faucet free. Kite Connect stocks (~2000/mo) excluded deliberately |

## Measured result (added after the honest rewrite)

The first version of this repo had a **fiction backtest**: it sized 10% per signal,
ignored cooldown/LLM/daily-stop, and charged no fees. Rewritten so the backtest and
live share `trader.run_cycle()` — ATR stops, fees + slippage, next-bar fills, modelled
gates. Real data: 2,877 hourly candles (~120 days) per symbol.

| Metric | BTC/USDT | ETH/USDT |
|---|---|---|
| Strategy return, default params | −6.82% | −10.60% |
| Buy & hold | +38.49% | +66.31% |
| Closed trades / win rate | 83 / 31.3% | 96 / 29.2% |
| Profit factor | 0.58 | 0.57 |
| Fees paid (on 1000 USDT start) | 40.25 | 45.54 |

Walk-forward BTC (4 folds, 162-combo parameter grid, out-of-sample only):
mean **−1.08%**, median −1.11%, min −2.49%, max +0.38%, **1/4 folds positive**,
1/4 folds beat buy & hold.

**Verdict: the EMA+RSI pattern has no edge after costs.** That is not a bug; it is the
expected outcome for a crowded signal, and the reason the tool exists — to tell you so
with numbers instead of hope. Do not go live on this signal. The honest next step is a
different hypothesis (regime filter, volume, funding/basis), tested the same way:
same-rules backtest → walk-forward → paper on Testnet.

## Bugs fixed in the rewrite (each would have cost real money)

- Daily stop was never armed (`set_day_pnl` never called) — now wired from the equity curve.
- No stop-loss or take-profit existed anywhere — now ATR-based, exits evaluated first.
- Cooldown was checked for *all* actions, so it could block an exit — exits are exempt.
- LLM gate only blocked on `disagree`, so a `hold` passed — `LLM_VETO_MODE` now controls it.
- Testnet reported `filled=True` without checking the order — fill is verified by polling.
- Paper and Testnet state were mixed — now a single portfolio with explicit migration.
- Synthetic data used `abs(hash(symbol))` — non-reproducible across runs; fixed.
- RSI returned 50 when there were no losses (parabolic uptrend looked neutral and was
  bought) — RSI is now 100 when all moves are gains.
- No `.gitignore` while `.env` held keys — added.
- Every script opened DuckDB read-write (Windows "file in use") — analysis reads read-only.

## Why spot CEX Testnet (not DEX / futures / NSE) for you

- Futures = liquidation risk, unsuitable for a Rs 500 learner.
- DEX mempool/MEV needs <300ms gRPC/Kafka infra — overkill for a laptop.
- NSE paper via Yahoo delayed is OK for learning but execution needs a paid broker API.
- Binance Spot Testnet: same CCXT code path as live, free faucet,
  `set_sandbox_mode(True)` -> testnet.binance.vision.

## What to measure before even thinking live

1. `python backtest.py --symbol X` — return %, trade count, **profit factor > 1.3**, and
   does it beat buy & hold? The script prints the verdict either way.
2. `python backtest.py --walk-forward --folds 4` — out-of-sample mean must be positive in
   most folds, not just in-sample. This is the test that kills most "profitable" ideas.
3. 2–4 weeks `run_demo.py` / `paper_loop.py`, then `python analyze_trades.py` — win rate,
   max drawdown, expectancy from the DuckDB `trades` table.
4. LLM agree-rate vs signal; if the LLM disagrees >40%, the signal is weak.
5. Fees/slippage: 0.1% + 0.05% per side is already charged — if the measured margin is
   under ~2%, it is noise.

If the backtest is flat/negative: normal. Next step is a new *hypothesis* (regime filter,
volume spike, BTC dominance) — not a bigger LLM and not more leverage.
