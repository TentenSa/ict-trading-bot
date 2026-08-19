#!/usr/bin/env python3
"""Render an OHLC session as an ASCII candlestick chart for a terminal.

No plotting library (pip is unavailable here), and none needed: one column per
bar, one row per price bucket. Levels passed with --level are drawn across the
chart so structure can be read against them rather than guessed at.
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fetch_data import fetch_ohlc

IST = timezone(timedelta(hours=5, minutes=30))


def render(bars, rows, levels, title):
    lo = min(b["low"] for b in bars)
    hi = max(b["high"] for b in bars)
    for _, p in levels:
        lo, hi = min(lo, p), max(hi, p)
    pad = (hi - lo) * 0.04 or 1.0
    lo, hi = lo - pad, hi + pad
    step = (hi - lo) / rows

    def row_of(p):
        return min(rows - 1, max(0, int((hi - p) / step)))

    grid = [[" "] * len(bars) for _ in range(rows)]
    for x, b in enumerate(bars):
        r_hi, r_lo = row_of(b["high"]), row_of(b["low"])
        rb_top, rb_bot = row_of(max(b["open"], b["close"])), row_of(min(b["open"], b["close"]))
        up = b["close"] >= b["open"]
        for r in range(r_hi, r_lo + 1):
            grid[r][x] = "│"
        for r in range(rb_top, rb_bot + 1):
            grid[r][x] = "█" if up else "▒"

    lvl_rows = {}
    for name, p in levels:
        lvl_rows.setdefault(row_of(p), []).append((name, p))

    print(f"\n{title}")
    print("─" * (12 + len(bars) + 22))
    for r in range(rows):
        price = hi - r * step - step / 2
        line = "".join(grid[r])
        if r in lvl_rows:
            line = "".join(ch if ch != " " else "·" for ch in line)
            tag = "  ← " + ", ".join(f"{n} {p:,.2f}" for n, p in lvl_rows[r])
        else:
            tag = ""
        print(f"{price:10,.1f} │{line}{tag}")
    print(" " * 10 + " └" + "─" * len(bars))
    ticks = [" "] * len(bars)
    for x, b in enumerate(bars):
        t = b["dt"].astimezone(IST)
        if t.minute % 30 == 0:
            lab = f"{t:%H:%M}"
            for k, ch in enumerate(lab):
                if x + k < len(ticks):
                    ticks[x + k] = ch
    print(" " * 11 + " " + "".join(ticks))
    print("\n  █ up candle   ▒ down candle   │ wick   · level row")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--interval", default="5m")
    ap.add_argument("--range", default="1d", dest="rng")
    ap.add_argument("--rows", type=int, default=26)
    ap.add_argument("--today-only", action="store_true")
    ap.add_argument("--until-ist", default=None,
                    help="HH:MM IST — truncate here, to show a setup as it looked at decision time")
    ap.add_argument("--level", action="append", default=[], help="name:price")
    a = ap.parse_args()

    bars, meta = fetch_ohlc(a.ticker, a.rng, a.interval)
    if a.today_only:
        d = datetime.now(IST).date()
        bars = [b for b in bars if b["dt"].astimezone(IST).date() == d]
    if a.until_ist:
        hh, mm = (int(x) for x in a.until_ist.split(":"))
        bars = [b for b in bars
                if (b["dt"].astimezone(IST).hour, b["dt"].astimezone(IST).minute) <= (hh, mm)]
    levels = []
    for spec in a.level:
        n, _, p = spec.rpartition(":")
        levels.append((n, float(p)))
    t0, t1 = bars[0]["dt"].astimezone(IST), bars[-1]["dt"].astimezone(IST)
    title = (f"{a.ticker}  {a.interval}  {t0:%d-%b %H:%M} → {t1:%H:%M IST}   "
             f"last {bars[-1]['close']:,.2f}   "
             f"range {min(b['low'] for b in bars):,.2f}–{max(b['high'] for b in bars):,.2f}")
    render(bars, a.rows, levels, title)


if __name__ == "__main__":
    main()
