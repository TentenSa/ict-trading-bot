#!/usr/bin/env python3
"""Underlying-equivalent round-trip cost for an option position.

`round_trip_cost_pct` in a signal is expressed against the UNDERLYING, because
that is where the backtester measures stop and target. But the cost is actually
paid on the option: you cross the bid/ask twice and pay fees on premium. Those
premium-denominated costs convert to underlying points by dividing by delta --
an option with delta 0.5 needs the underlying to move 2 points to recover 1
point of premium.

Using a flat 0.001 for everything is wrong in both directions. On a liquid index
it overstates the cost by ~5x, which forces `verify_signal.py` check 14 to demand
a stop ten times that -- roughly 1% of the index -- when real structure sits far
closer. On a thin stock option it can understate it.

Fee model is Upstox-style discount-broker equity/index OPTIONS, per leg unless
noted (verify against your own contract note; these are the published rates):
  brokerage     Rs 20 per order
  STT           0.10% of premium, SELL side only
  exchange txn  0.05% of premium (NSE options)
  SEBI          Rs 10 per crore of premium
  stamp duty    0.003% of premium, BUY side only
  GST           18% on (brokerage + exchange txn + SEBI)

Usage:
    python3 costs.py NIFTY 2026-09-29 24500 PE
    python3 costs.py OBEROIRLTY 2026-08-25 1840 CE
"""
import sys

BROKERAGE_PER_ORDER = 20.0
STT_SELL_PCT = 0.0010
EXCH_TXN_PCT = 0.0005
SEBI_PER_RUPEE = 10.0 / 1e7
STAMP_BUY_PCT = 0.00003
GST_PCT = 0.18


def leg_charges(turnover, side):
    """Statutory + brokerage charges on one leg, given premium turnover."""
    brokerage = BROKERAGE_PER_ORDER
    exch = EXCH_TXN_PCT * turnover
    sebi = SEBI_PER_RUPEE * turnover
    gst = GST_PCT * (brokerage + exch + sebi)
    stt = STT_SELL_PCT * turnover if side == "SELL" else 0.0
    stamp = STAMP_BUY_PCT * turnover if side == "BUY" else 0.0
    return brokerage + exch + sebi + gst + stt + stamp


def round_trip(entry_premium, exit_premium, lot, delta, spot, bid=None, ask=None):
    """Round-trip cost as a fraction of the UNDERLYING price.

    Includes crossing the spread once on the way in and once on the way out when
    bid/ask are supplied; without them only fees are counted, which is a floor,
    not an estimate.
    """
    charges = (leg_charges(entry_premium * lot, "BUY")
               + leg_charges(exit_premium * lot, "SELL"))
    fee_points = charges / lot                      # premium points per unit
    spread_points = (ask - bid) if (bid and ask) else 0.0
    premium_cost = fee_points + spread_points       # total premium given up
    underlying_points = premium_cost / abs(delta)   # convert via delta
    return underlying_points / spot


def from_chain(symbol, expiry, strike, side, exit_multiple=2.0):
    import upstox
    chain = upstox.option_chain(symbol, expiry)
    spot = chain[0]["underlying_spot_price"]
    row = next(r for r in chain if r["strike_price"] == float(strike))
    leg = row["call_options" if side.upper() == "CE" else "put_options"]
    md, gk = leg["market_data"], leg["option_greeks"]
    lot = next(c["lot_size"] for c in upstox.contracts(symbol) if c.get("lot_size"))
    mid = (md["bid_price"] + md["ask_price"]) / 2
    pct = round_trip(mid, mid * exit_multiple, lot, gk["delta"], spot,
                     md["bid_price"], md["ask_price"])
    return {
        "symbol": symbol, "expiry": expiry, "strike": float(strike), "side": side.upper(),
        "spot": spot, "lot": lot, "mid": mid, "bid": md["bid_price"], "ask": md["ask_price"],
        "spread_pct_of_mid": (md["ask_price"] - md["bid_price"]) / mid * 100,
        "delta": gk["delta"], "oi": md["oi"],
        "round_trip_cost_pct": pct,
        "underlying_points": pct * spot,
        "min_stop_pct_for_check14": pct * 10,
        "min_stop_points_for_check14": pct * 10 * spot,
    }


def main():
    if len(sys.argv) < 5:
        print(__doc__)
        return
    r = from_chain(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
    print(f"{r['symbol']} {r['expiry']} {r['strike']:.0f}{r['side']}  spot {r['spot']:,.2f}  lot {r['lot']}")
    print(f"  quote {r['bid']}/{r['ask']} (mid {r['mid']:.2f}, spread {r['spread_pct_of_mid']:.2f}% of mid)"
          f"  delta {r['delta']:.3f}  oi {r['oi']:,.0f}")
    print(f"  round-trip cost vs UNDERLYING: {r['round_trip_cost_pct']*100:.4f}% "
          f"({r['underlying_points']:.2f} points)")
    print(f"  -> smallest stop that clears check 14: {r['min_stop_pct_for_check14']*100:.2f}% "
          f"({r['min_stop_points_for_check14']:.1f} points)")
    print(f"  -> flat 0.001 assumption would demand: 1.00% ({r['spot']*0.01:.1f} points)")


if __name__ == "__main__":
    main()
