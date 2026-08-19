#!/usr/bin/env python3
"""Project an option's premium at future UNDERLYING levels, off the parity forward.

Three corrections over a naive Black-Scholes call on spot, each one a mistake
this project actually made:

  1. NSE index/stock options price off the FUTURES FORWARD, not cash spot. The
     forward is backed out of put-call parity, F = K + CEmid - PEmid, averaged
     over the liquid strikes, then priced Black-76 style (r=0 on F).
  2. The broker's published `iv` refreshes on a different cadence than its own
     bid/ask, so pairing them mispriced by 2-4%. IV is solved from the live mid
     instead, which makes the model agree with the market by construction.
  3. The basis decays to zero at expiry, so a projection at some future date
     must scale it by the remaining fraction of the tenor -- not hold it flat.

IV at the projected spot is read off the SOLVED skew at the same moneyness, so a
move down the strip picks up the skew rather than assuming flat vol.

Usage:
    python3 opt_project.py NIFTY 2026-08-25 24300 PE --at 24320:1 24136.75:3
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import bs
import upstox

IST = timezone(timedelta(hours=5, minutes=30))


def forward_and_skew(chain, spot, T, width=250):
    """Parity forward, and the put skew as [(K - spot, solved_iv)] sorted."""
    rows = {r["strike_price"]: r for r in chain}
    Fs = []
    for k, r in rows.items():
        if abs(k - spot) > width:
            continue
        c, p = r["call_options"]["market_data"], r["put_options"]["market_data"]
        if not (c["bid_price"] and c["ask_price"] and p["bid_price"] and p["ask_price"]):
            continue
        Fs.append(k + (c["bid_price"] + c["ask_price"]) / 2 - (p["bid_price"] + p["ask_price"]) / 2)
    if not Fs:
        raise SystemExit("no two-sided quotes near the money — cannot back out the forward")
    F = sum(Fs) / len(Fs)

    skew = []
    for k, r in sorted(rows.items()):
        if abs(k - spot) > 3 * width:
            continue
        p = r["put_options"]["market_data"]
        if not (p["bid_price"] and p["ask_price"]):
            continue
        iv = bs.implied_vol(F, k, T, "PE", (p["bid_price"] + p["ask_price"]) / 2, r=0.0)
        if iv:
            skew.append((k - spot, iv))
    return F, skew, max(Fs) - min(Fs)


def iv_on_skew(skew, moneyness):
    if not skew:
        return None
    if moneyness <= skew[0][0]:
        return skew[0][1]
    if moneyness >= skew[-1][0]:
        return skew[-1][1]
    for a, b in zip(skew, skew[1:]):
        if a[0] <= moneyness <= b[0]:
            return a[1] + (moneyness - a[0]) / (b[0] - a[0]) * (b[1] - a[1])
    return skew[-1][1]


def project(strike, kind, spot_now, F_now, T_now, skew, spot_at, days_ahead):
    """Premium at underlying `spot_at`, `days_ahead` days from now."""
    d = max(T_now * 365 - days_ahead, 0.02)
    F = spot_at + (F_now - spot_now) * (d / (T_now * 365))
    iv = iv_on_skew(skew, strike - spot_at)
    return bs.price(F, strike, d / 365, iv, kind, r=0.0), iv, d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol"); ap.add_argument("expiry")
    ap.add_argument("strike", type=float); ap.add_argument("kind", choices=["CE", "PE"])
    ap.add_argument("--at", nargs="+", required=True,
                    help="underlying:days_ahead pairs, e.g. 24320:1 24136.75:3")
    ap.add_argument("--lot", type=int, default=None)
    ap.add_argument("--close", default="15:30", help="expiry-day close, IST HH:MM")
    a = ap.parse_args()

    chain = upstox.option_chain(a.symbol, a.expiry)
    spot = chain[0]["underlying_spot_price"]
    y, m, d = (int(x) for x in a.expiry.split("-"))
    hh, mm = (int(x) for x in a.close.split(":"))
    now = datetime.now(IST)
    T = (datetime(y, m, d, hh, mm, tzinfo=IST) - now).total_seconds() / 86400 / 365
    F, skew, spread = forward_and_skew(chain, spot, T)

    row = {r["strike_price"]: r for r in chain}[a.strike][
        "call_options" if a.kind == "CE" else "put_options"]
    md, gk = row["market_data"], row["option_greeks"]
    mid = (md["bid_price"] + md["ask_price"]) / 2
    lot = a.lot or next((c.get("lot_size") for c in upstox.contracts(a.symbol) if c.get("lot_size")), 1)

    print(f"{a.symbol} {a.strike:.0f}{a.kind} {a.expiry}   {now:%d-%b %H:%M IST}")
    print(f"  spot {spot:,.2f}   parity forward {F:,.2f} ({F-spot:+.2f}, {100*(F-spot)/spot:+.3f}%)"
          f"   F-spread across strikes {spread:.2f}   DTE {T*365:.2f}d   lot {lot}")
    print(f"  live {md['bid_price']:.2f}/{md['ask_price']:.2f} (mid {mid:.2f})   oi {md['oi']:,.0f}"
          f"   vol {md['volume']:,.0f}   broker iv {gk['iv']:.2f}")

    solved = bs.implied_vol(F, a.strike, T, a.kind, mid, r=0.0)
    print(f"  solved iv {solved*100:.2f}% vs broker-published {gk['iv']:.2f}% "
          f"-> repricing error using the published field would be "
          f"{100*(bs.price(F,a.strike,T,gk['iv']/100,a.kind,r=0.0)/mid-1):+.2f}%")
    print(f"  CALIBRATION at solved iv: {100*(bs.price(F,a.strike,T,solved,a.kind,r=0.0)/mid-1):+.4f}%\n")

    base = None
    print(f"  {'underlying':>12} {'+days':>6} {'DTE':>6} {'iv':>7} {'premium':>9} "
          f"{'P&L/lot':>10} {'vs entry':>9}")
    for spec in a.at:
        s_str, _, d_str = spec.partition(":")
        S = float(s_str); dd = float(d_str or 0)
        p, iv, dte = project(a.strike, a.kind, spot, F, T, skew, S, dd)
        if base is None:
            base = p
        print(f"  {S:12,.2f} {dd:6.1f} {dte:6.2f} {iv*100:6.2f}% {p:9.2f} "
              f"{(p-base)*lot:+10,.0f} {100*(p/base-1):+8.1f}%")
    print(f"\n  debit/lot Rs {base*lot:,.0f}")


if __name__ == "__main__":
    main()
