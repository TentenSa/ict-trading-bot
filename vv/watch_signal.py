#!/usr/bin/env python3
"""Live papertrade watcher for one logged signal — emits state transitions only.

Designed to be driven by a background monitor: every line on stdout is an alert,
so it stays silent while nothing happens and speaks only when the trade's state
actually changes.

State machine, evaluated on bars at or after the signal's as_of:
    PENDING  -> APPROACHING (price within --near of entry, emitted once)
             -> FILLED      (entry touched)
    FILLED   -> TARGET | STOP  (terminal, exits)

A pending limit that never fills is not a win or a loss, so it is reported as
NEVER_FILLED at the session close rather than scored.

Usage:
    python3 watch_signal.py signal_nifty_intraday.json
    python3 watch_signal.py signal_nifty_intraday.json --interval 60 --until 10:00
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fetch_data import fetch_ohlc

IST = timezone(timedelta(hours=5, minutes=30))


def say(msg):
    """One line = one alert. Flush so the monitor sees it immediately."""
    now = datetime.now(timezone.utc)
    print(f"[{now.astimezone(IST):%H:%M IST}] {msg}", flush=True)


def bars_since(ticker, as_of):
    for rng, iv in (("1d", "1m"), ("1d", "5m"), ("5d", "15m")):
        try:
            c, _ = fetch_ohlc(ticker, rng, iv)
            out = [x for x in c if x["dt"] >= as_of]
            if out:
                return out
        except Exception:
            continue
    return []


def main():
    p = argparse.ArgumentParser()
    p.add_argument("signal")
    p.add_argument("--interval", type=float, default=60.0, help="seconds between polls")
    p.add_argument("--until", default="10:00", help="UTC HH:MM to stop (market close)")
    p.add_argument("--near", type=float, default=0.001, help="approach alert threshold")
    p.add_argument("--filled", action="store_true",
                   help="position is ALREADY open (entry taken outside this watcher). Starts in the "
                        "FILLED state so stop and target are live immediately. Without this, a real "
                        "position whose entry price is never revisited would be reported NEVER_FILLED "
                        "at the close -- reporting no trade existed when one does.")
    a = p.parse_args()

    s = json.load(open(a.signal))
    tkr = s["ticker"]
    direction = s.get("signal")
    entry = float(s["entry"])
    stop = float(s["stop_loss"])
    tps = s.get("take_profits") or []
    tp1 = float(tps[0])
    as_of = datetime.fromisoformat(s["as_of"].replace("Z", "+00:00"))

    hh, mm = (int(x) for x in a.until.split(":"))
    now = datetime.now(timezone.utc)
    deadline = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if deadline <= now:
        deadline += timedelta(days=1)

    is_sell = direction == "SELL"
    say(f"watching {tkr} {direction} entry {entry} / SL {stop} / TP1 {tp1} "
        f"until {a.until}Z — silent unless state changes")

    filled = a.filled
    approached = False
    best = None
    if filled:
        say(f"position already OPEN at {entry} (declared filled) — tracking SL {stop} / TP1 {tp1} "
            f"from here; entry state not re-evaluated.")

    while True:
        if datetime.now(timezone.utc) >= deadline:
            if filled:
                say(f"SESSION CLOSE — still open, last {best:.2f}. "
                    f"Position carries; horizon {s.get('horizon_hours')}h.")
            else:
                say(f"SESSION CLOSE — NEVER_FILLED. Entry {entry} was not touched "
                    f"(best approach {best:.2f}). No trade existed; not a win or a loss.")
            return

        bars = bars_since(tkr, as_of)
        if bars:
            hi = max(x["high"] for x in bars)
            lo = min(x["low"] for x in bars)
            last = bars[-1]["close"]
            best = hi if is_sell else lo

            if not filled:
                touched = hi >= entry if is_sell else lo <= entry
                if touched:
                    filled = True
                    say(f"ENTRY FILLED — {tkr} touched {entry} (last {last:.2f}). "
                        f"Now live: SL {stop}, TP1 {tp1}.")
                elif not approached:
                    gap = abs(last - entry) / entry
                    if gap <= a.near:
                        approached = True
                        say(f"APPROACHING entry — {tkr} {last:.2f}, {abs(last-entry):.2f} "
                            f"points from {entry} ({gap*100:.2f}%).")

            if filled:
                # Conservative ordering: if both are touched in the same poll
                # window, assume the stop came first.
                hit_stop = hi >= stop if is_sell else lo <= stop
                hit_tp = lo <= tp1 if is_sell else hi >= tp1
                if hit_stop:
                    say(f"STOP HIT — {tkr} reached {stop}. Loss of "
                        f"{abs(entry-stop):.2f} points (-1R). Papertrade closed.")
                    return
                if hit_tp:
                    rr = abs(tp1 - entry) / abs(entry - stop)
                    say(f"TARGET HIT — {tkr} reached {tp1}. Gain of "
                        f"{abs(tp1-entry):.2f} points (+{rr:.2f}R). Papertrade closed.")
                    return

        time.sleep(a.interval)


if __name__ == "__main__":
    main()
