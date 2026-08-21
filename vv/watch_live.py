#!/usr/bin/env python3
"""Level watcher for an armed signal, driven by EXCHANGE time, not the local clock.

"now" comes from the feed's own regularMarketTime (via fetch_data.market_now),
so the watcher is correct no matter what the host clock says -- this VM has been
observed 7 hours behind the exchange, which makes a host-clock bar filter discard
the whole session and alert nothing, silently.

Touch levels (entry fill, targets) are judged on 1M highs/lows, because a resting
limit fills on a touch. Close levels (stop, structural breaks) are judged on
CLOSED 5M bars only -- a wick through a level is not a close through it.

Usage:
    python3 watch_live.py ^NSEI --entry 24214 --side SELL --stop 24242 \
        --tp 24172.85:SHELF --tp 24151.75:TP1 --tp 24100:TP2 \
        --break-below 24185.10:OPENING-RANGE-LOW --until 15:30 --interval 45
"""
import argparse, shutil, subprocess, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from fetch_data import fetch_ohlc, market_now, exchange_now

IST = timezone(timedelta(hours=5, minutes=30))
BAR5 = timedelta(minutes=5)


CRITICAL = ("***", "BREAK")

# Set by --opt; the contract whose live quote gets stamped onto every critical
# alert. Without this the log records the underlying level but not the price you
# actually transacted at, and the option entry has to be reconstructed afterwards
# from whatever quote happened to be sampled next -- which is a guess wearing a
# decimal point.
OPT = None


def parse_opt(spec):
    """SYMBOL:EXPIRY:STRIKE:CE|PE  e.g. NIFTY:2026-08-25:24300:CE"""
    sym, expiry, strike, kind = spec.split(":")
    kind = kind.upper()
    if kind not in ("CE", "PE"):
        raise ValueError("option type must be CE or PE")
    return {"symbol": sym, "expiry": expiry, "strike": float(strike), "kind": kind}


def option_quote():
    """Live bid/ask for the tracked contract, or None.

    Every failure path returns None rather than raising: an expired broker token
    or a slow chain call must degrade the alert to underlying-only, never stop
    the watcher from reporting that the stop was hit.
    """
    if not OPT:
        return None
    try:
        import upstox
        chain = upstox.option_chain(OPT["symbol"], OPT["expiry"])
        row = next(r for r in chain if r["strike_price"] == OPT["strike"])
        leg = row["call_options" if OPT["kind"] == "CE" else "put_options"]
        md, gk = leg["market_data"], leg.get("option_greeks", {})
        return {"bid": md.get("bid_price"), "ask": md.get("ask_price"),
                "ltp": md.get("ltp"), "iv": gk.get("iv"), "delta": gk.get("delta")}
    except Exception:
        return None


def opt_stamp(side, event):
    """Render the quote, naming the side of the spread this event transacts at.

    The position is LONG PREMIUM either way: a bullish underlying view is a bought
    call, a bearish one is a bought put. So entry always pays the ASK and exit
    always hits the BID, regardless of the underlying direction -- `side` chooses
    CE vs PE, it does not flip which side of the spread gets crossed. Recording
    the mid instead would flatter every fill by half the spread.
    """
    q = option_quote()
    if not q:
        return "  [option quote unavailable]" if OPT else ""
    if event == "fill":
        px, which, verb = q["ask"], "ask", "paid"
    else:
        px, which, verb = q["bid"], "bid", "get"
    tag = f'{OPT["symbol"]} {OPT["expiry"]} {OPT["strike"]:.0f}{OPT["kind"]}'
    extra = f' iv {q["iv"]}' if q.get("iv") is not None else ""
    return (f'  [{tag}  {q["bid"]}/{q["ask"]}  {verb} {px} ({which})'
            f'  ltp {q["ltp"]}{extra}]')


def desktop_alert(title, body):
    """Fire a desktop notification straight at the user.

    The stdout line only reaches whoever is reading the log. A fill or a stop is
    worth interrupting for, so it also goes to the desktop -- independently of
    any agent or tail being alive to relay it.
    """
    if not shutil.which("notify-send"):
        return
    try:
        subprocess.run(["notify-send", "--urgency=critical", "--app-name=NIFTY",
                        title, body], timeout=5, check=False)
    except Exception:
        pass  # an alert channel failing must never take the watcher down


def say(now, msg, stamp=""):
    line = f"[{now.astimezone(IST):%H:%M IST}] {msg}{stamp}"
    print(line, flush=True)
    if any(k in msg for k in CRITICAL):
        desktop_alert(f"NIFTY {now.astimezone(IST):%H:%M IST}", msg + stamp)


def parse_lvl(s):
    p, _, lab = s.partition(":")
    return float(p), (lab or p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--entry", type=float, required=True)
    ap.add_argument("--side", choices=["BUY", "SELL"], required=True)
    ap.add_argument("--stop", type=float, required=True)
    ap.add_argument("--tp", action="append", default=[], help="level:LABEL")
    ap.add_argument("--break-below", dest="brk", action="append", default=[])
    ap.add_argument("--until", default="15:30", help="IST HH:MM to stop")
    ap.add_argument("--interval", type=float, default=45.0)
    ap.add_argument("--near", type=float, default=10.0)
    ap.add_argument("--opt", default=None,
                    help="contract to stamp onto critical alerts, "
                         "SYMBOL:EXPIRY:STRIKE:CE|PE (e.g. NIFTY:2026-08-25:24300:CE)")
    ap.add_argument("--assume-filled", action="store_true",
                    help="resume with the position already open (after a restart), "
                         "so the fill is not re-announced")
    ap.add_argument("--since", default=None,
                    help="IST HH:MM the signal was armed. Only prints at/after this "
                         "time can fill the entry -- a high made before the limit "
                         "existed is not a fill.")
    a = ap.parse_args()

    global OPT
    if a.opt:
        OPT = parse_opt(a.opt)
    tps = [parse_lvl(s) for s in a.tp]
    brks = [parse_lvl(s) for s in a.brk]
    hh, mm = (int(x) for x in a.until.split(":"))
    since = tuple(int(x) for x in a.since.split(":")) if a.since else None
    short = a.side == "SELL"

    filled, stopped = a.assume_filled, False
    hit, warned, broke = set(), set(), set()
    last5 = None

    while True:
        try:
            c1, meta = fetch_ohlc(a.ticker, "1d", "1m")
        except Exception as e:
            time.sleep(a.interval); continue
        now = market_now(meta)
        ist = now.astimezone(IST)
        if not c1:
            time.sleep(a.interval); continue

        today = [b for b in c1 if b["dt"].astimezone(IST).date() == ist.date()]
        if not today:
            time.sleep(a.interval); continue
        px = today[-1]["close"]
        hi = max(b["high"] for b in today)
        lo = min(b["low"] for b in today)
        # Fills and targets may only be judged on prints made at or after the
        # moment the order was armed. Session extremes set earlier are history.
        armed = [b for b in today
                 if since is None
                 or (b["dt"].astimezone(IST).hour, b["dt"].astimezone(IST).minute) >= since]
        a_hi = max((b["high"] for b in armed), default=px)
        a_lo = min((b["low"] for b in armed), default=px)

        # --- entry fill: a resting limit fills on a touch ---
        if not filled:
            touched = a_hi >= a.entry if short else a_lo <= a.entry
            if touched:
                filled = True
                fill_lo, fill_hi = a_lo, a_hi
                say(now, f"*** FILLED *** {a.side} {a.ticker} @ {a.entry:.2f} "
                         f"(since-arm range {a_lo:.2f}-{a_hi:.2f}, spot {px:.2f}). Position is LIVE.",
                    opt_stamp(a.side, "fill"))
            elif abs(a.entry - px) <= a.near and "entry" not in warned:
                warned.add("entry")
                say(now, f"approaching ENTRY {a.entry:.2f} — spot {px:.2f}, "
                         f"{abs(a.entry-px):.2f}pt away.")

        # --- closed 5M bars for close-based triggers ---
        try:
            c5, m5 = fetch_ohlc(a.ticker, "1d", "5m")
            closed = [b for b in c5 if b["dt"].astimezone(IST).date() == ist.date()
                      and b["dt"] + BAR5 <= now]
        except Exception:
            closed = []
        if closed:
            b = closed[-1]
            if b["dt"] != last5:
                last5 = b["dt"]
                # stop: judged on a CLOSE, and only once we are in
                if filled and not stopped:
                    if (short and b["close"] > a.stop) or (not short and b["close"] < a.stop):
                        stopped = True
                        say(now, f"*** STOP *** 5M CLOSED {b['close']:.2f} beyond {a.stop:.2f} "
                                 f"— premise dead, exit at market.",
                            opt_stamp(a.side, "exit"))
                # structural break before the fill
                if not filled:
                    for lv, lab in brks:
                        if lv not in broke and b["close"] < lv:
                            broke.add(lv)
                            say(now, f"BREAK {lab} — 5M CLOSED {b['close']:.2f} below {lv:.2f} "
                                     f"BEFORE the {a.entry:.2f} limit filled. Market left without the "
                                     f"retest; re-cut, do not chase.")

        # --- targets (touch) ---
        if filled and not stopped:
            for lv, lab in tps:
                if lv in hit: continue
                if (short and a_lo <= lv) or (not short and a_hi >= lv):
                    hit.add(lv)
                    ext = f"low {a_lo:.2f}" if short else f"high {a_hi:.2f}"
                    say(now, f"*** {lab} HIT *** {lv:.2f} touched (post-arm {ext}).",
                        opt_stamp(a.side, "exit"))
                elif abs(px - lv) <= a.near and lv not in warned:
                    warned.add(lv)
                    say(now, f"approaching {lab} {lv:.2f} — spot {px:.2f}.")

        if stopped or (tps and all(lv in hit for lv, _ in tps)):
            say(now, "watcher done — position resolved."); return
        if (ist.hour, ist.minute) >= (hh, mm):
            state = "FILLED, unresolved" if filled else "never filled"
            say(now, f"SESSION END — {state}. Session {lo:.2f}-{hi:.2f}, last {px:.2f}.")
            return
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
