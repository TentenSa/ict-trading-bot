#!/usr/bin/env python3
"""Execution-quality review — separates "was the read right" from "did the order fill".

`backtest.py report` scores trades that happened. It is silent on the failure
mode that dominated 14-17 Aug 2026: signals that were directionally correct and
never traded, because a pending limit that never fills is not a win or a loss
and so never enters the win-rate at all.

This splits the two layers:

  ANALYSIS  — from spot at as_of, did price reach TP1 before the stop?
              Answers "was the bias right", independent of order placement.
  EXECUTION — did the entry actually trade? How far was it from spot at issue?
              How close did price come?

A big gap between them is the diagnosis: good reads, unreachable orders.

Usage:
    python3 review.py              # all real signals
    python3 review.py --since 2026-08-14
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fetch_data import fetch_ohlc

LOG_PATH = Path(__file__).parent / "signals_log.jsonl"


def _bars(ticker, interval, rng):
    try:
        c, _ = fetch_ohlc(ticker, rng, interval)
        return c
    except Exception:
        return []


def walk(bars, direction, entry, stop, tp):
    """Return (filled, hit, best_approach) walking bars in order.

    hit is 'target', 'stop' or None. Same-bar ambiguity resolves to the stop,
    the conservative reading.
    """
    is_sell = direction == "SELL"
    filled = False
    best = None
    for b in bars:
        approach = b["high"] if is_sell else b["low"]
        best = approach if best is None else (max(best, approach) if is_sell else min(best, approach))
        if not filled:
            if (b["high"] >= entry) if is_sell else (b["low"] <= entry):
                filled = True
            else:
                continue
        hit_stop = (b["high"] >= stop) if is_sell else (b["low"] <= stop)
        hit_tp = (b["low"] <= tp) if is_sell else (b["high"] >= tp)
        if hit_stop:
            return filled, "stop", best
        if hit_tp:
            return filled, "target", best
    return filled, None, best


def review(since=None):
    rows = []
    for line in LOG_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        e = json.loads(line)
        if e.get("is_hypothetical") or e.get("status") == "unresolvable":
            continue
        if e.get("entry") is None or e.get("stop_loss") is None or e.get("take_profit") is None:
            continue
        as_of = datetime.fromisoformat(e["as_of"].replace("Z", "+00:00"))
        if since and as_of.date() < since:
            continue

        interval = e.get("resolution_interval", "5m")
        rng = "5d" if interval in ("1m", "5m", "15m") else "1mo"
        # Bound the walk by the signal's OWN horizon. Without this an intraday
        # idea gets scored against days of later data it was never meant to
        # survive into: the 18 Aug ^NSEI short closed its 5h horizon 18.15
        # points short of target, then reached it the NEXT session, and the
        # unbounded walk reported that as a win. That inflates the hit rate in
        # exactly the direction that flatters the analyst.
        horizon = e.get("horizon_hours")
        deadline = as_of + timedelta(hours=horizon) if horizon else None
        bars = [b for b in _bars(e["ticker"], interval, rng)
                if b["dt"] >= as_of and (deadline is None or b["dt"] <= deadline)]
        if not bars:
            rows.append({**e, "note": "no bars in retention window"})
            continue

        d, entry, stop, tp = e["trade_direction"], e["entry"], e["stop_loss"], e["take_profit"]
        spot0 = bars[0]["open"]
        filled, hit, best = walk(bars, d, entry, stop, tp)
        # Analysis layer: same idea entered at market, at signal time.
        _, hit_at_market, _ = walk(bars, d, spot0, stop, tp)

        rows.append({
            "ticker": e["ticker"], "as_of": e["as_of"], "dir": d,
            "entry": entry, "spot0": spot0,
            "dist_pct": abs(entry - spot0) / spot0 * 100,
            "filled": filled, "hit": hit, "best": best,
            "miss_pct": None if filled else abs(entry - best) / entry * 100,
            "at_market": hit_at_market,
        })
    return rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--since", default=None)
    a = p.parse_args()
    since = datetime.fromisoformat(a.since).date() if a.since else None

    rows = review(since)
    real = [r for r in rows if "note" not in r]
    print("=== Execution review (signals_log.jsonl) ===\n")
    print(f"{'ticker':14s} {'dir':5s} {'entry':>10s} {'spot@issue':>11s} {'dist':>7s} "
          f"{'filled':>7s} {'outcome':>8s} {'missed by':>10s} {'at-market':>10s}")
    for r in real:
        print(f"{r['ticker']:14s} {r['dir']:5s} {r['entry']:10.2f} {r['spot0']:11.2f} "
              f"{r['dist_pct']:6.2f}% {str(r['filled']):>7s} {str(r['hit'] or 'open'):>8s} "
              f"{('-' if r['filled'] else format(r['miss_pct'],'.2f')+'%'):>10s} "
              f"{str(r['at_market'] or 'open'):>10s}")
    for r in rows:
        if "note" in r:
            print(f"{r['ticker']:14s} -- {r['note']}")

    if not real:
        print("\nNo resolvable signals in window.")
        return
    n = len(real)
    fills = sum(1 for r in real if r["filled"])
    won = sum(1 for r in real if r["hit"] == "target")
    lost = sum(1 for r in real if r["hit"] == "stop")
    mkt_won = sum(1 for r in real if r["at_market"] == "target")
    mkt_lost = sum(1 for r in real if r["at_market"] == "stop")
    med = sorted(r["dist_pct"] for r in real)[n // 2]

    print(f"\nEXECUTION  fill rate {fills}/{n} ({fills/n*100:.0f}%)   "
          f"median entry distance {med:.2f}% of spot")
    print(f"           of filled: {won} target, {lost} stop")
    decided = mkt_won + mkt_lost
    if decided:
        print(f"ANALYSIS   entered at market instead: {mkt_won}/{decided} reached TP1 first "
              f"({mkt_won/decided*100:.0f}%)")
        print(f"\nGAP        {mkt_won} of {n} signals were directionally right; "
              f"{fills} actually traded.")
        if fills < mkt_won:
            print("           The read is not the problem. Order placement is.")
    else:
        print("ANALYSIS   nothing decided yet at market either.")


if __name__ == "__main__":
    main()
