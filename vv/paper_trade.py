#!/usr/bin/env python3
"""Paper-trade tracker for the 28-Aug intraday NIFTY fade.

One command, `python3 paper_trade.py`, prints the whole state of the idea: where
the morning range stands, whether a trigger has fired on a CLOSED 5M bar, and -
once filled - the live premium and P&L of the paper leg.

State lives in paper_trade_27aug.json so every 9-minute check reads the same
book rather than re-deriving it. The trigger is judged on a 5M CLOSE, never a
wick: on 26-Aug price wicked through the level at 11:30 and closed back above,
and treating that as a fill would have been a phantom trade.
"""
import json, sys, pathlib
from datetime import datetime, timedelta, timezone
sys.path.insert(0, "/home/nitin/claude/vv")
from fetch_data import fetch_ohlc
import upstox

IST = timezone(timedelta(hours=5, minutes=30))
STATE = pathlib.Path(__file__).parent / "paper_trade_28aug.json"
RANGE_END = (11, 20)          # morning range is 09:15 -> 11:20
STOP_PTS, TGT_PTS = 50.0, 35.0
LOT = 65

def load():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"status": "WAITING", "fills": [], "range_locked": False}

def save(s): STATE.write_text(json.dumps(s, indent=2))

def bars():
    b, _ = fetch_ohlc("^NSEI", "1d", "5m")
    today = b[-1]["dt"].astimezone(IST).date()
    return [x for x in b if x["dt"].astimezone(IST).date() == today]

def hm(x): return x["dt"].astimezone(IST).strftime("%H:%M")

def leg_quote(strike, kind):
    try:
        ch = upstox.option_chain("NIFTY", "2026-09-01")
        for r in ch:
            if r["strike_price"] == strike:
                md = r[("call_options" if kind == "CE" else "put_options")]["market_data"]
                return md["bid_price"], md["ask_price"]
    except Exception as e:
        print(f"   (chain unavailable: {e})")
    return None, None

def main():
    s = load()
    t = bars()
    now = datetime.now(IST)
    closed = [x for x in t if x["dt"].astimezone(IST) + timedelta(minutes=5) <= now]
    if not closed:
        print("no closed 5M bar yet"); return
    pre = [x for x in closed if (x["dt"].astimezone(IST).hour, x["dt"].astimezone(IST).minute) <= RANGE_END]
    rhi = max(x["high"] for x in pre); rlo = min(x["low"] for x in pre)
    locked = (now.hour, now.minute) > RANGE_END
    last = closed[-1]
    spot = t[-1]["close"]
    print(f"[{now:%H:%M IST}] NIFTY {spot:,.2f}   last closed 5M {hm(last)} C{last['close']:,.2f}")
    print(f"   morning range {rlo:,.2f}-{rhi:,.2f} ({rhi-rlo:.1f} pts){'  [LOCKED]' if locked else '  [still forming until 11:20]'}")

    if s["status"] == "WAITING":
        if not locked:
            print(f"   WAITING - range not locked. Down-trigger ~{rlo:,.2f}, up-trigger ~{rhi:,.2f}")
            print(f"   to down-trigger {last['close']-rlo:+.2f} | to up-trigger {last['close']-rhi:+.2f}")
            save(s); return
        fired = None
        for x in [c for c in closed if (c["dt"].astimezone(IST).hour, c["dt"].astimezone(IST).minute) > RANGE_END]:
            if x["close"] < rlo: fired = ("BUY", x); break
            if x["close"] > rhi: fired = ("SELL", x); break
        if not fired:
            print(f"   ARMED, no trigger. down {rlo:,.2f} ({last['close']-rlo:+.2f}) | up {rhi:,.2f} ({last['close']-rhi:+.2f})")
            save(s); return
        d, bar = fired
        px = bar["close"]
        strike = round((px + (50 if d == "BUY" else -50)) / 50) * 50
        kind = "CE" if d == "BUY" else "PE"
        bid, ask = leg_quote(strike, kind)
        s.update(status=d, entry=px, entry_bar=hm(bar),
                 stop=px - STOP_PTS if d == "BUY" else px + STOP_PTS,
                 target=px + TGT_PTS if d == "BUY" else px - TGT_PTS,
                 strike=strike, kind=kind, entry_premium=ask, range_lo=rlo, range_hi=rhi)
        print(f"   *** TRIGGER {d} at {hm(bar)} close {px:,.2f} - PAPER FILL {strike}{kind} @ {ask}")
        save(s); return

    if s["status"] in ("BUY", "SELL"):
        d = s["status"]
        hi = max(x["high"] for x in closed if hm(x) > s["entry_bar"]) if any(hm(x) > s["entry_bar"] for x in closed) else spot
        lo = min(x["low"] for x in closed if hm(x) > s["entry_bar"]) if any(hm(x) > s["entry_bar"] for x in closed) else spot
        hit_t = (hi >= s["target"]) if d == "BUY" else (lo <= s["target"])
        hit_s = (lo <= s["stop"]) if d == "BUY" else (hi >= s["stop"])
        bid, ask = leg_quote(s["strike"], s["kind"])
        pnl = (bid - s["entry_premium"]) * LOT if bid else None
        print(f"   PAPER {d} from {s['entry']:,.2f} ({s['entry_bar']})  |  {s['strike']}{s['kind']} in {s['entry_premium']} now {bid}/{ask}")
        if pnl is not None:
            print(f"   P&L Rs {pnl:+,.0f}/lot ({100*(bid-s['entry_premium'])/s['entry_premium']:+.1f}%)")
        print(f"   target {s['target']:,.2f} ({'HIT' if hit_t else f'{abs(spot-s[chr(116)+chr(97)+chr(114)+chr(103)+chr(101)+chr(116)]):.1f} away'})  |  stop {s['stop']:,.2f} ({'HIT' if hit_s else f'{abs(spot-s[chr(115)+chr(116)+chr(111)+chr(112)]):.1f} away'})")
        if hit_s: s["status"] = "CLOSED"; s["result"] = "STOP"; print("   *** STOPPED - paper trade closed")
        elif hit_t: s["status"] = "CLOSED"; s["result"] = "TARGET"; print("   *** TARGET - paper trade closed")
        save(s); return
    print(f"   CLOSED - {s.get('result')}")

main()
