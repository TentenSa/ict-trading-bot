#!/usr/bin/env python3
"""9-minute NIFTY expiry-day pulse: spot, structure, level distances, ATM marks.

Emits one block every 9 minutes so the trade can be checked in on cadence without
polling by hand. Stops at the bell.
"""
import sys, time
from datetime import datetime, timedelta, timezone
sys.path.insert(0, "/home/nitin/claude/vv")
from fetch_data import closed_bars
import upstox, bs, opt_project as op

IST = timezone(timedelta(hours=5, minutes=30))
P90 = 19.80
LEVELS = [("24-Aug close / gap origin", 24219.05), ("MAX PAIN 53.2m wall", 24200.00),
          ("18-Aug low", 24154.90), ("24-Aug low (swept)", 24144.30),
          ("19-Aug close", 24078.30), ("19-Aug low", 24025.65)]

def pulse():
    b = closed_bars("^NSEI")
    d0 = b[-1]["dt"].astimezone(IST).date()
    t = [x for x in b if x["dt"].astimezone(IST).date() == d0]
    hi = max(x["high"] for x in t); lo = min(x["low"] for x in t)
    last = t[-1]
    ch = upstox.option_chain("NIFTY", "2026-08-25")
    spot = ch[0]["underlying_spot_price"]
    rows = {r["strike_price"]: r for r in ch}
    atmk = min(rows, key=lambda k: abs(k - spot))
    a = rows[atmk]
    # Marks the OPEN position. Update POS when the book changes -- a pulse that
    # reports a stale leg is worse than one that reports none.
    POS = {"strike": 24100, "kind": "PE", "entry": 19.60, "lot": 65,
           "stop": 24155.00, "wp": 24078.30, "tp": 24025.65, "dir": "SHORT"}
    T = (datetime(2026, 8, 25, 15, 30, tzinfo=IST) - datetime.now(IST)).total_seconds() / 86400 / 365
    try:
        F, skew, _ = op.forward_and_skew(ch, spot, T)
        key = "call_options" if POS["kind"] == "CE" else "put_options"
        m = rows[POS["strike"]][key]["market_data"]
        mid = (m["bid_price"] + m["ask_price"]) / 2
        iv = bs.implied_vol(F, POS["strike"], T, POS["kind"], mid, r=0.0) * 100
        pnl = (m["bid_price"] - POS["entry"]) * POS["lot"]
        leg = (f"{POS['dir']} {POS['strike']}{POS['kind']} {m['bid_price']:.2f}/{m['ask_price']:.2f} "
               f"vs {POS['entry']:.2f} -> {pnl:+,.0f}/lot ({100*(m['bid_price']/POS['entry']-1):+.1f}%) | IV {iv:.2f}%")
        leg += (f"\n   stop {POS['stop']:,.2f} ({POS['stop']-spot:+.1f}) | "
                f"waypoint {POS['wp']:,.2f} ({spot-POS['wp']:+.1f}) | TP1 {POS['tp']:,.2f} ({spot-POS['tp']:+.1f})")
    except Exception as e:
        leg = f"leg mark failed: {type(e).__name__}: {e}"
    strad = 0.0
    rng = last["high"] - last["low"]
    pos = (last["close"] - last["low"]) / rng if rng else 0.5
    disp = " DISPLACEMENT" if rng > P90 and (pos < 0.35 or pos > 0.65) else ""
    out = [f"PULSE {datetime.now(IST):%H:%M IST} | spot {spot:,.2f} | session {lo:,.2f}-{hi:,.2f} ({hi-lo:.1f} pts)",
           f"   last 5M {last['dt'].astimezone(IST):%H:%M} C {last['close']:,.2f} rng {rng:.2f} pos {pos:.2f}{disp}",
           f"   {leg}"]
    out.append("   " + " | ".join(f"{n} {v:,.2f} ({spot-v:+.0f})" for n, v in LEVELS[:3]))
    out.append("   " + " | ".join(f"{n} {v:,.2f} ({spot-v:+.0f})" for n, v in LEVELS[3:]))
    print("\n".join(out), flush=True)

while True:
    now = datetime.now(IST)
    if now.hour * 60 + now.minute >= 15 * 60 + 30:
        print(f"PULSE {now:%H:%M IST} — session over, pulse standing down.", flush=True)
        break
    try:
        pulse()
    except Exception as e:
        print(f"PULSE {datetime.now(IST):%H:%M IST} !! pulse failed: {type(e).__name__}: {e}", flush=True)
    time.sleep(540)
