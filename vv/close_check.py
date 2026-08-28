#!/usr/bin/env python3
"""Report the BANKNIFTY cash close against the thesis level, once, after the bell.

The 24-Aug short rests on a DAILY structure break: today must close below the
20-Aug low of 57431.80 or the higher-low sequence never broke and the premise is
void regardless of where the 5M bars went. That verdict only exists at 15:30, so
it needs its own check -- the 5M trigger watchers stop at the bell.
"""
import sys, time
from datetime import datetime, timedelta, timezone
sys.path.insert(0, "/home/nitin/claude/vv")
from fetch_data import fetch_ohlc, exchange_now

IST = timezone(timedelta(hours=5, minutes=30))
THESIS = 57431.80   # 20-Aug low
ENTRY  = 57323.19
STOP   = 57430.00

def say(m): print(f"[{datetime.now(IST):%H:%M IST}] {m}", flush=True)

# wait for the bell plus settle time for the feed to stamp the daily bar
while datetime.now(IST) < datetime.now(IST).replace(hour=15, minute=32, second=0, microsecond=0):
    time.sleep(30)

for attempt in range(20):
    try:
        d, _ = fetch_ohlc("^NSEBANK", "5d", "1d")
        bar = d[-1]
        if bar["dt"].astimezone(IST).date() == datetime.now(IST).date():
            c = bar["close"]
            verdict = ("THESIS HOLDS" if c < THESIS else "THESIS VOID")
            say(f"*** BANKNIFTY CASH CLOSE {c:.2f} vs 20-Aug low {THESIS} -> {verdict}. "
                f"Day {bar['low']:.2f}-{bar['high']:.2f}. Position entry {ENTRY} "
                f"({c-ENTRY:+.2f} pts). Stop {STOP} {'INTACT' if c < STOP else 'BREACHED ON CLOSE'}.")
            if c >= THESIS:
                say("!! The daily lower low did NOT print. The premise the trade was taken on is gone. "
                    "Exit at tomorrow's open rather than holding for the 57001.75 target.")
            else:
                say("Lower low stands: 24-Aug closed below the 20-Aug low, first since 19-Aug. "
                    "Hold is justified; theta on the 29-Sep leg is ~Rs 343/lot/day.")
            break
    except Exception as e:
        say(f"close fetch failed ({type(e).__name__}), retrying")
    time.sleep(45)
else:
    say("!! could not retrieve today's daily close after 20 attempts — verdict UNKNOWN, check manually.")
