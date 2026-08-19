#!/usr/bin/env python3
"""Shortlist-only intraday scanner: emit a line when a FRESH, UNFILLED, displaced
5M fair value gap appears — the mechanical precondition for an entry.

This does NOT produce a signal. It cannot judge HTF alignment, R:R against a
measured cost floor, whether the target fits inside the session's remaining
expected move, or premium/discount location. It answers one question only:
"is there something worth looking at right now?" The analyst answers the rest.

Deliberately strict, because a scanner that cries wolf gets ignored:
  - gap at least --min-width points (noise gaps are the majority)
  - formed within the last --fresh bars
  - not yet traded back into (so an entry still exists)
  - displacement leg at least --min-leg points
Each distinct gap is reported once.

Usage:
    python3 scan_nifty.py ^NSEI --interval 300 --until 09:45
"""
import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fetch_data import fetch_ohlc

IST = timezone(timedelta(hours=5, minutes=30))
BAR = timedelta(minutes=5)


def say(msg):
    print(f"[{datetime.now(timezone.utc).astimezone(IST):%H:%M IST}] {msg}", flush=True)


def closed_bars(ticker):
    now = datetime.now(timezone.utc)
    for rng, iv in (("1d", "5m"), ("5d", "5m")):
        try:
            c, _ = fetch_ohlc(ticker, rng, iv)
        except Exception:
            continue
        today = datetime.now(IST).date()
        bars = [b for b in c if b["dt"].astimezone(IST).date() == today
                and b["dt"] + BAR <= now]
        if bars:
            return bars
    return []


def find(bars, min_width, fresh, min_leg):
    """Fresh unfilled displaced FVGs, newest first."""
    out = []
    n = len(bars)
    for i in range(1, n - 1):
        if i < n - 1 - fresh:
            continue
        a, mid, z = bars[i - 1], bars[i], bars[i + 1]
        leg = abs(a["high"] - z["low"]) if a["low"] > z["high"] else abs(z["high"] - a["low"])
        if a["low"] > z["high"]:
            lo, hi, d = z["high"], a["low"], "SELL"
            leg = a["high"] - z["low"]
        elif a["high"] < z["low"]:
            lo, hi, d = a["high"], z["low"], "BUY"
            leg = z["high"] - a["low"]
        else:
            continue
        if hi - lo < min_width or leg < min_leg:
            continue
        # unfilled: nothing after the gap has traded back through it
        after = bars[i + 2:]
        if d == "SELL" and any(b["high"] >= hi for b in after):
            continue
        if d == "BUY" and any(b["low"] <= lo for b in after):
            continue
        out.append((mid["dt"].astimezone(IST).strftime("%H:%M"), d, lo, hi, hi - lo, leg))
    return out[::-1]



def find_sweeps(bars, prev_hi, prev_lo, min_wick, lookback):
    """Liquidity sweeps: a pool taken out, then price CLOSING back inside.

    Structurally the opposite of the FVG scan above -- that one wants
    continuation, this one wants rejection. A scanner that only knows
    displacement is blind to every reversal setup, which on 18 Aug was the
    entire trade.

    Pools = prior-day high/low, plus any 5M swing extreme already confirmed
    earlier in the session. The reclaim must be a CLOSE, not a wick, and the
    raid itself must clear the pool by at least `min_wick` so that a one-tick
    graze does not count.
    """
    out = []
    n = len(bars)
    # Pools must be levels somebody actually has orders resting at. A 2-bar
    # fractal is not that: inside a 20-point chop box every third bar qualifies,
    # and the scan fires constantly on levels nobody is defending. Requiring the
    # extreme to hold over +/-POOL_LOOKBACK bars (~30 min each side on 5m) keeps
    # session-scale pools and discards the noise.
    POOL_LOOKBACK = 6
    swings_hi, swings_lo = [], []
    for i in range(POOL_LOOKBACK, n - POOL_LOOKBACK):
        w = bars[i - POOL_LOOKBACK:i + POOL_LOOKBACK + 1]
        if bars[i]["high"] == max(x["high"] for x in w):
            swings_hi.append((i, bars[i]["high"]))
        if bars[i]["low"] == min(x["low"] for x in w):
            swings_lo.append((i, bars[i]["low"]))

    for i in range(1, n):
        if i < n - lookback:
            continue
        b = bars[i]
        pools_lo = ([("prev-day low", prev_lo)] if prev_lo else []) + \
                   [("5M swing low", p) for j, p in swings_lo if j < i - 1]
        pools_hi = ([("prev-day high", prev_hi)] if prev_hi else []) + \
                   [("5M swing high", p) for j, p in swings_hi if j < i - 1]
        # One bar can raid several stacked pools at once. That is a single
        # event, not three, so report only the deepest raid per bar per side --
        # three notifications for one candle is the noise that gets a monitor
        # muted.
        best = {}
        for label, p in pools_lo:
            if b["low"] < p - min_wick and b["close"] > p:
                cand = (bars[i]["dt"], "BUY", label, p, p - b["low"], b["close"])
                if "BUY" not in best or cand[4] > best["BUY"][4]:
                    best["BUY"] = cand
        for label, p in pools_hi:
            if b["high"] > p + min_wick and b["close"] < p:
                cand = (bars[i]["dt"], "SELL", label, p, b["high"] - p, b["close"])
                if "SELL" not in best or cand[4] > best["SELL"][4]:
                    best["SELL"] = cand
        out.extend(best.values())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--interval", type=float, default=300.0)
    ap.add_argument("--until", default="09:45")
    ap.add_argument("--min-width", type=float, default=8.0)
    ap.add_argument("--min-leg", type=float, default=25.0)
    ap.add_argument("--fresh", type=int, default=4)
    ap.add_argument("--stale", type=float, default=15.0)
    ap.add_argument("--min-wick", type=float, default=6.0,
                    help="points a raid must clear a pool by to count as a sweep")
    ap.add_argument("--no-sweeps", action="store_true", help="FVG continuation only")
    a = ap.parse_args()

    hh, mm = (int(x) for x in a.until.split(":"))
    now = datetime.now(timezone.utc)
    deadline = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if deadline <= now:
        deadline += timedelta(days=1)

    modes = "displaced FVG (continuation)" if a.no_sweeps else \
            "displaced FVG (continuation) + liquidity sweep & reclaim (reversal)"
    say(f"scanning {a.ticker} 5M for {modes} until {a.until}Z — "
        f"silent unless one appears. Shortlist only, not a signal.")

    prev_hi = prev_lo = None
    try:
        d, _ = fetch_ohlc(a.ticker, "5d", "1d")
        today = datetime.now(IST).date()
        prior = [x for x in d if x["dt"].astimezone(IST).date() < today]
        if prior:
            prev_hi, prev_lo = prior[-1]["high"], prior[-1]["low"]
            say(f"prior session {prior[-1]['dt'].astimezone(IST):%d-%b} "
                f"high {prev_hi:.2f} / low {prev_lo:.2f} loaded as liquidity pools.")
    except Exception as e:
        say(f"could not load prior-day pools ({e}); sweep scan limited to intraday swings.")

    seen = set()
    last_ok = datetime.now(timezone.utc)
    stale_warned = False
    while True:
        if datetime.now(timezone.utc) >= deadline:
            say("SESSION CLOSE — scan ended. Nothing further.")
            return
        bars = closed_bars(a.ticker)
        if not bars:
            gap = (datetime.now(timezone.utc) - last_ok).total_seconds() / 60
            if gap >= a.stale and not stale_warned:
                stale_warned = True
                say(f"!! FEED STALE — no closed {a.ticker} bar for {gap:.0f} min. "
                    f"Not scanning; silence means nothing.")
            time.sleep(a.interval)
            continue
        last_ok = datetime.now(timezone.utc)
        if stale_warned:
            stale_warned = False
            say("feed recovered — scanning again.")
        spot = bars[-1]["close"]
        lo_s = min(b["low"] for b in bars); hi_s = max(b["high"] for b in bars)
        for t, d, lo, hi, w, leg in find(bars, a.min_width, a.fresh, a.min_leg):
            key = (t, d, round(lo, 2), round(hi, 2))
            if key in seen:
                continue
            seen.add(key)
            dist = (lo - spot) if d == "BUY" else (hi - spot)
            say(f"CANDIDATE {d} (continuation) — fresh unfilled 5M "
                f"{'bearish' if d=='SELL' else 'bullish'} FVG {lo:.2f}-{hi:.2f} ({w:.2f}pt) "
                f"from a {leg:.2f}pt leg at {t}. Spot {spot:.2f}, session {lo_s:.2f}-{hi_s:.2f}, "
                f"array is {abs(dist):.2f}pt away. "
                f"NOT a signal — needs HTF, R:R vs cost floor, and reachability checked.")

        if not a.no_sweeps:
            for dt_, d, label, pool, wick, close in find_sweeps(
                    bars, prev_hi, prev_lo, a.min_wick, a.fresh):
                key = ("sweep", dt_.isoformat(), d, round(pool, 2))
                if key in seen:
                    continue
                seen.add(key)
                say(f"CANDIDATE {d} (reversal) — {dt_.astimezone(IST):%H:%M} swept the "
                    f"{label} at {pool:.2f} by {wick:.2f}pt and CLOSED back inside at {close:.2f}. "
                    f"Spot {spot:.2f}, session {lo_s:.2f}-{hi_s:.2f}. "
                    f"NOT a signal — needs HTF, R:R vs cost floor, and reachability checked.")
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
