#!/usr/bin/env python3
"""
LLM-in-the-loop historical replay — Phase 7a. Unlike replay_check.py's
hand-coded proxy trader (RSI/MACD if-statements), this asks Stan's REAL
model for a real decision on each simulated historical day, using
strategy.md's actual current text embedded directly in the prompt.

Daily cadence only, not 5-min tick cadence: Alpaca gives ~200 days of
daily bars but only 3 days of 5-min bars, and no accumulated intraday
history exists anywhere for Stan's actual small-cap universe (checked
2026-07-23: market_data.bars_5min in the shared Postgres only covers 9
fixed megacaps, none of Stan's tickers). True tick-cadence replay needs a
real data-accumulation phase first — not this script's job.

SAFETY — no real trades, ever: every invocation instructs the model not
to call any tools or place trades; decisions are parsed from plain text
and applied to a virtual Portfolio (src/replay.py), same "cannot place
trades" boundary skills/off-hours-research.md already establishes.

SAFETY — variant/prompt-iteration testing is NOT implemented here yet,
on purpose: `openclaw agent --agent trader-stonks --message ...` always
loads the REAL AGENTS.md/TOOLS.md/etc. from disk (confirmed empirically
2026-07-23 — they're not something this script's prompt text controls).
Testing a candidate TOOLS.md would require swapping the real file on
disk, which is genuinely dangerous while live trading is active (a
concurrent live tick firing mid-swap would read the candidate/broken
file for a REAL decision). That needs a real safety mechanism (a hard
market-hours + lock-file check, at minimum) before it's built — flagged,
not built recklessly. This script is baseline-only: does Stan's real
judgment on historical data hold up, full stop.

Usage:
    python3 scripts/llm_replay.py [--days N] [--tickers TICKER ...]
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_check  # noqa: E402

sys.path.insert(0, "/home/openclaw/projects/paper-trading-rebuild")
from src.replay import Tick, TraderDecision, replay_trader  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
STRATEGY_PATH = REPO_ROOT / "strategy.md"

# Matches real observed stonks-tick cron usage, not necessarily the
# agent's own configured model.primary (which may differ — crons can
# override). Cheaper and consistent with what live trading actually runs.
DEFAULT_MODEL = "openrouter/deepseek/deepseek-v4-flash"
DEFAULT_LOOKBACK_DAYS = 25
AGENT_ID = "trader-stonks"
AGENT_TIMEOUT_SECONDS = 150


def build_replay_prompt(date_str: str, day_snapshot: Dict[str, Dict[str, Any]],
                         portfolio_state: Dict[str, Any], strategy_text: str) -> str:
    """day_snapshot: {ticker: {close, rsi_14, macd_hist, macd_line, macd_signal, ma20, ma50}}
    portfolio_state: {cash, positions: {ticker: {qty, entry_price}}}
    """
    lines = [
        "HISTORICAL REPLAY — NOT a live tick. Do not call any tools. Do not "
        "place any trades. Do not check real-time data. Everything you need "
        "is in this message. Respond with ONLY a JSON object, no other text.",
        "",
        f"Simulated date: {date_str}",
        "",
        "Your current strategy (strategy.md, as it stood when this replay was run):",
        "---",
        strategy_text,
        "---",
        "",
        "Portfolio right now:",
        f"  cash: ${portfolio_state['cash']:.2f}",
    ]
    if portfolio_state["positions"]:
        for ticker, pos in portfolio_state["positions"].items():
            lines.append(f"  {ticker}: {pos['qty']} shares @ ${pos['entry_price']:.2f} entry")
    else:
        lines.append("  (no open positions)")

    lines.append("")
    lines.append("Today's data per ticker:")
    for ticker, row in sorted(day_snapshot.items()):
        lines.append(
            f"  {ticker}: close=${row['close']:.2f} rsi14={row['rsi_14']:.1f} "
            f"macd_hist={row['macd_hist']:.3f} ma20=${row['ma20']:.2f} ma50=${row['ma50']:.2f}"
        )

    lines.append("")
    lines.append(
        "For each ticker, decide BUY / SELL / HOLD per strategy.md's rules. "
        "Respond with exactly this JSON shape and nothing else:"
    )
    lines.append(
        '{"TICKER": {"decision": "BUY|SELL|HOLD", "shares": N, '
        '"conviction": 0.0-1.0, "rationale": "short reason"}, ...}'
    )
    return "\n".join(lines)


def invoke_agent(message: str, model: str = DEFAULT_MODEL,
                  timeout: int = AGENT_TIMEOUT_SECONDS) -> Optional[str]:
    """Returns the agent's raw text reply, or None on any failure — fail
    open, same philosophy as every other best-effort call in this repo. A
    replay day that couldn't get a real decision just falls back to HOLD
    (see parse_decisions), not a crashed run.
    """
    try:
        result = subprocess.run(
            ["openclaw", "agent", "--agent", AGENT_ID, "--message", message,
             "--json", "--model", model],
            capture_output=True, text=True, timeout=timeout,
        )
        data = json.loads(result.stdout)
        return data["result"]["payloads"][0]["text"]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError, IndexError, OSError):
        return None


def parse_decisions(text: Optional[str], tickers: List[str]) -> Dict[str, TraderDecision]:
    """Extract the JSON decision object from the agent's reply. Missing/
    unparseable/unmentioned tickers default to HOLD — fail open, a replay
    day the model didn't answer cleanly shouldn't crash the whole run."""
    decisions: Dict[str, TraderDecision] = {}
    parsed: Dict[str, Any] = {}

    if text:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                parsed = {}

    for ticker in tickers:
        entry = parsed.get(ticker) if isinstance(parsed, dict) else None
        if not isinstance(entry, dict):
            decisions[ticker] = TraderDecision(ticker=ticker, decision="HOLD", conviction=0.0,
                                                 rationale="no parseable decision, defaulted to HOLD")
            continue
        decision = str(entry.get("decision", "HOLD")).upper()
        if decision not in ("BUY", "SELL", "HOLD"):
            decision = "HOLD"
        decisions[ticker] = TraderDecision(
            ticker=ticker, decision=decision,
            conviction=float(entry.get("conviction", 0.0) or 0.0),
            shares=int(entry.get("shares", 0) or 0),
            rationale=str(entry.get("rationale", "")),
        )
    return decisions


def make_llm_trader(frames, strategy_text: str, model: str = DEFAULT_MODEL):
    """One real LLM call per SIMULATED DAY (covering all tickers at once,
    matching how a real tick evaluates the whole portfolio), not one call
    per (ticker, tick) — replay_trader() calls the trader callback once
    per tick, i.e. once per ticker per day for an interleaved multi-ticker
    stream, so this memoizes per-day to avoid ~10x the real LLM-call cost.
    """
    lookup = {}
    for sym, df in frames.items():
        for _, row in df.iterrows():
            lookup[(sym, row["timestamp"])] = row

    day_cache: Dict[Any, Dict[str, TraderDecision]] = {}

    def trader(tick, portfolio):
        day = tick.timestamp.date() if hasattr(tick.timestamp, "date") else tick.timestamp

        if day not in day_cache:
            day_snapshot = {}
            for sym in frames:
                row = lookup.get((sym, tick.timestamp))
                if row is None:
                    continue
                day_snapshot[sym] = {
                    "close": float(row["close"]), "rsi_14": float(row["rsi_14"]),
                    "macd_hist": float(row["macd_hist"]), "macd_line": float(row["macd_line"]),
                    "macd_signal": float(row["macd_signal"]),
                    "ma20": float(row["ma20"]) if row["ma20"] == row["ma20"] else row["close"],
                    "ma50": float(row["ma50"]) if row["ma50"] == row["ma50"] else row["close"],
                }
            portfolio_state = {
                "cash": portfolio.cash,
                "positions": {
                    t: {"qty": p.shares, "entry_price": p.entry_price}
                    for t, p in portfolio.positions.items()
                },
            }
            prompt = build_replay_prompt(str(day), day_snapshot, portfolio_state, strategy_text)
            reply = invoke_agent(prompt, model=model)
            day_cache[day] = parse_decisions(reply, list(day_snapshot.keys()))

        return day_cache[day].get(
            tick.ticker,
            TraderDecision(ticker=tick.ticker, decision="HOLD", conviction=0.0,
                            rationale="ticker not in today's snapshot"),
        )

    return trader


def run_llm_replay(tickers: List[str], lookback_days: int = DEFAULT_LOOKBACK_DAYS,
                    model: str = DEFAULT_MODEL) -> Dict[str, Any]:
    strategy_text = STRATEGY_PATH.read_text() if STRATEGY_PATH.exists() else ""
    if not strategy_text:
        return {"error": "strategy.md missing or empty — nothing to replay against"}

    original_lookback = replay_check.LOOKBACK_DAYS
    replay_check.LOOKBACK_DAYS = lookback_days
    try:
        frames = replay_check.fetch_history(tickers)
    finally:
        replay_check.LOOKBACK_DAYS = original_lookback

    if not frames:
        return {"error": "no usable history for any ticker"}

    ticks = replay_check.build_tick_stream(frames)
    llm_trader = make_llm_trader(frames, strategy_text, model=model)
    llm_result = replay_trader(ticks, llm_trader, initial_balance=10_000.0,
                                max_position_pct=0.06, require_conviction=0.5)

    proxy_trader = replay_check.STRATEGY_BUILDERS["v1.0"](frames)
    proxy_result = replay_trader(ticks, proxy_trader, initial_balance=10_000.0,
                                  max_position_pct=0.06, require_conviction=0.5)

    return {
        "tickers_used": sorted(frames.keys()),
        "lookback_days": lookback_days,
        "model": model,
        "llm_replay": replay_check.summarize(llm_result, "real LLM judgment on historical data"),
        "hand_coded_proxy_v1_0": replay_check.summarize(proxy_result, "replay_check.py's proxy logic, same window"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--tickers", nargs="*", default=None)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    tickers = args.tickers or replay_check.load_live_universe()
    result = run_llm_replay(tickers, lookback_days=args.days, model=args.model)
    print(json.dumps(result, indent=2))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    sys.exit(main())
