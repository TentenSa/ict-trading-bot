#!/usr/bin/env python3
"""Watch for a HIGHER LOW forming on the LTF — the moment a down-leg stops being
impulsive.

A trend that is still working makes lower lows. The first confirmed swing low
that sits ABOVE the previous one says the impulse has gone out of the move, and
for a long-premium position that is the point where theta and vol decay start
costing more than the move pays. Inverted with --up for a long.

A swing low here is a fractal: a bar whose low is the minimum of the two bars
either side of it. That needs two bars to CLOSE after it, so confirmation lags
the actual low by ~10 minutes on 5m. That lag is the price of not crying wolf on
every one-bar wiggle, and it is deliberate.

If price instead takes out the reference low, the leg is still impulsive: the
reference ratchets down and the watch continues, silently.

Usage:
    python3 watch_structure.py ^NSEI --ref-low 24028.20 --interval 60 --until 09:45
"""
import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fetch_data import fetch_ohlc, closed_bars, market_now, exchange_now, detect_swings

IST = timezone(timedelta(hours=5, minutes=30))
BAR = timedelta(minutes=5)


def say(msg):
    print(f"[{exchange_now().astimezone(IST):%H:%M IST}] {msg}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--ref-low", type=float, required=True)
    ap.add_argument("--up", action="store_true", help="watch for a LOWER HIGH instead")
    ap.add_argument("--interval", type=float, default=60.0)
    ap.add_argument("--until", default="09:45")
    ap.add_argument("--stale", type=float, default=10.0)
    a = ap.parse_args()

    hh, mm = (int(x) for x in a.until.split(":"))
    now = exchange_now()
    deadline = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if deadline <= now:
        deadline += timedelta(days=1)
    # regularMarketTime freezes at the close, so exchange_now() can stall just
    # short of a deadline set at/after it. monotonic elapsed time cannot stall
    # and cannot be moved by a clock jump, so it backstops the exit.
    started = time.monotonic()
    max_run = (deadline - now).total_seconds() + 1800

    ref = a.ref_low
    word = "LOWER HIGH" if a.up else "HIGHER LOW"
    say(f"watching {a.ticker} 5M for a confirmed {word} vs {ref:.2f} until {a.until}Z — "
        f"silent unless the leg loses impulse (or the reference ratchets)")

    last_ok = time.monotonic()
    stale_warned = False
    while True:
        if exchange_now() >= deadline or time.monotonic() - started > max_run:
            say(f"SESSION CLOSE — no {word} confirmed. Leg stayed impulsive; reference {ref:.2f}.")
            return

        bars = closed_bars(a.ticker)
        if not bars:
            gap = (time.monotonic() - last_ok) / 60
            if gap >= a.stale and not stale_warned:
                stale_warned = True
                say(f"!! FEED STALE — no closed {a.ticker} bar for {gap:.0f} min. "
                    f"Structure is NOT being evaluated; silence means nothing.")
            time.sleep(a.interval)
            continue

        last_ok = time.monotonic()
        if stale_warned:
            stale_warned = False
            say("feed recovered — resuming structure watch.")

        # Only structure formed AFTER the reference extreme counts. Swing lows
        # printed earlier in the session are higher than the reference by
        # definition -- the leg made them on its way down -- so counting them
        # would fire the alert instantly and mean nothing.
        ref_i = 0
        for i, b in enumerate(bars):
            if (b["high"] >= ref) if a.up else (b["low"] <= ref):
                ref_i = i
        swing_at = {}
        for i in range(2, len(bars) - 2):
            w = bars[i - 2:i + 3]
            if bars[i]["high"] == max(x["high"] for x in w):
                swing_at.setdefault(i, {})["high"] = bars[i]["high"]
            if bars[i]["low"] == min(x["low"] for x in w):
                swing_at.setdefault(i, {})["low"] = bars[i]["low"]

        after = {i: v for i, v in swing_at.items() if i > ref_i}
        if a.up:
            newext = [v["high"] for v in after.values() if "high" in v and v["high"] > ref]
            if newext:
                ref = max(newext)
                continue
            lh = [v["high"] for v in after.values() if "high" in v and v["high"] < ref]
            if lh:
                say(f"*** LOWER HIGH CONFIRMED at {max(lh):.2f}, below the {ref:.2f} reference "
                    f"(last {bars[-1]['close']:.2f}). The up-leg has lost its impulse.")
                return
        else:
            newlow = [v["low"] for v in after.values() if "low" in v and v["low"] < ref]
            if newlow:
                ref = min(newlow)     # leg still impulsive, ratchet down quietly
                continue
            hl = [v["low"] for v in after.values() if "low" in v and v["low"] > ref]
            if hl:
                say(f"*** HIGHER LOW CONFIRMED at {min(hl):.2f}, above the {ref:.2f} reference "
                    f"(last {bars[-1]['close']:.2f}). The down-leg has lost its impulse — from here "
                    f"theta and IV decay cost more than the move pays. Decision point.")
                return
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
