#!/usr/bin/env python3
"""Watch a NO_TRADE signal's stated triggers and alert when one actually fires.

A NO_TRADE is not "nothing to do" -- it is a trade with an unmet condition. This
watches those conditions so standing aside does not mean looking away.

Triggers are evaluated on CLOSED bars only. A wick through a level is not a
close through it, and the whole point of requiring a close is to ignore the wick,
so an in-progress bar is never judged. Every line on stdout is an alert.

Usage:
    python3 watch_triggers.py ^NSEI --below 24208.15:BEAR --above 24250:BULL \
        --interval 60 --until 10:00
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
    """Today's 5m bars, excluding any bar that has not finished printing."""
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--below", action="append", default=[], help="level:LABEL")
    ap.add_argument("--above", action="append", default=[], help="level:LABEL")
    ap.add_argument("--interval", type=float, default=60.0)
    ap.add_argument("--until", default="10:00", help="UTC HH:MM to stop")
    ap.add_argument("--near", type=float, default=12.0, help="points for the approach warning")
    ap.add_argument("--stale", type=float, default=10.0,
                    help="minutes without a closed bar before warning the feed is dead")
    a = ap.parse_args()

    trigs = []
    for spec in a.below:
        lvl, _, lab = spec.partition(":")
        trigs.append(("below", float(lvl), lab or "BELOW"))
    for spec in a.above:
        lvl, _, lab = spec.partition(":")
        trigs.append(("above", float(lvl), lab or "ABOVE"))
    if not trigs:
        raise SystemExit("no triggers given")

    hh, mm = (int(x) for x in a.until.split(":"))
    now = datetime.now(timezone.utc)
    deadline = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if deadline <= now:
        deadline += timedelta(days=1)

    say(f"watching {a.ticker} for " +
        ", ".join(f"{lab} = 5M close {d} {lvl}" for d, lvl, lab in trigs) +
        f" until {a.until}Z — silent unless one fires")

    warned = set()
    seen = None
    last_ok = datetime.now(timezone.utc)
    stale_warned = False
    while True:
        if datetime.now(timezone.utc) >= deadline:
            say(f"SESSION CLOSE — no trigger fired. NO_TRADE stood. "
                f"Last close {seen if seen is not None else 'n/a'}.")
            return

        bars = closed_bars(a.ticker)
        if not bars:
            # Silence must never be ambiguous. A dead feed produces exactly the
            # same output as a quiet market -- no lines at all -- so an alert
            # that only fires on the happy path would let a broken watcher look
            # like a calm session for hours.
            gap_min = (datetime.now(timezone.utc) - last_ok).total_seconds() / 60
            if gap_min >= a.stale and not stale_warned:
                stale_warned = True
                say(f"!! FEED STALE — no closed {a.ticker} bar retrieved for {gap_min:.0f} min. "
                    f"Triggers are NOT being evaluated. Silence right now means nothing.")
        else:
            last_ok = datetime.now(timezone.utc)
            if stale_warned:
                stale_warned = False
                say(f"feed recovered — resuming trigger evaluation on {a.ticker}.")
            last = bars[-1]
            seen = round(last["close"], 2)
            hi = max(b["high"] for b in bars)
            lo = min(b["low"] for b in bars)
            for direction, lvl, lab in trigs:
                fired = last["close"] < lvl if direction == "below" else last["close"] > lvl
                if fired:
                    say(f"*** {lab} TRIGGER FIRED — 5M closed {seen} {direction} {lvl} "
                        f"at {last['dt'].astimezone(IST):%H:%M IST}. Session {lo:.2f}-{hi:.2f}. "
                        f"The NO_TRADE no longer applies; re-analyse for the entry.")
                    return
                gap = (last["close"] - lvl) if direction == "below" else (lvl - last["close"])
                if 0 <= gap <= a.near and lab not in warned:
                    warned.add(lab)
                    say(f"approaching {lab} — last 5M close {seen}, {gap:.2f} points from {lvl} "
                        f"(needs a CLOSE {direction}, not a wick).")
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
