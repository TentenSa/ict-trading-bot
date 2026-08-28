#!/usr/bin/env python3
"""Fire the HARD time stop on the 1-Sep 24300 CE BTST at 15:15 IST on 26-Aug.

The tested rule is "limit at 24327, else exit at tomorrow's close". The second
branch is not optional garnish -- in the 10y resimulation it is what keeps the
mean at +322/lot instead of an open-ended loss, because the unfilled cases are
the ones that keep bleeding theta and basis. The 5M trigger watcher covers the
limit; nothing covers the else-branch, so it needs its own clock.

Fires at 15:15, not 15:30, so the exit can actually be worked.
"""
import sys, time
from datetime import datetime, timedelta, timezone
sys.path.insert(0, "/home/nitin/claude/vv")
from fetch_data import fetch_ohlc

IST = timezone(timedelta(hours=5, minutes=30))
TP1, BE, ENTRY_PREM, LOT = 24327.00, 24291.00, 149.50, 65
START = datetime.now(IST).date()

def say(m): print(f"[{datetime.now(IST):%d-%b %H:%M IST}] {m}", flush=True)

while True:
    now = datetime.now(IST)
    if now.date() > START and now.weekday() < 5 and (now.hour, now.minute) >= (15, 15):
        break
    time.sleep(60)

for _ in range(20):
    try:
        b, _ = fetch_ohlc("^NSEI", "1d", "5m")
        today = [x for x in b if x["dt"].astimezone(IST).date() == datetime.now(IST).date()]
        if today:
            hi = max(x["high"] for x in today); last = today[-1]["close"]
            say(f"*** BTST TIME STOP. spot {last:.2f}, session high {hi:.2f}, TP1 {TP1}.")
            if hi >= TP1:
                say("TP1 WAS TAGGED today — the limit should already have filled. Verify the leg is flat.")
            else:
                say(f"TP1 NOT reached (high {hi:.2f}, short by {TP1-hi:.2f}). THE RULE SAYS EXIT NOW "
                    f"at the market, whatever the premium reads. Do not carry a second night — the "
                    f"edge measured was one night only, and the else-branch is what the +322/lot mean "
                    f"depends on.")
            say(f"Reference: entry {ENTRY_PREM} (Rs {ENTRY_PREM*LOT:,.0f}/lot), break-even {BE}.")
            break
    except Exception as e:
        say(f"feed error: {e}")
    time.sleep(30)
