#!/usr/bin/env python3
"""
Mechanical ICT pre-screener for NSE F&O stocks and crypto.

This does NOT produce trade signals. It applies objective, computable filters to
cut a ~170-name universe down to a handful of candidates worth full ICT
analysis, and prints the raw facts behind each one. Judgement (narrative, entry,
stop, target, confidence) stays with the analyst — this only answers "which
names are liquid, active, and showing ICT structure right now".

Gates (must pass both):
  - turnover  : median daily traded value over the lookback window
  - rel_volume: latest volume vs its own median (is today actually active)

Scored conditions (ranked, not gates):
  - liquidity sweep of a prior swing high/low that closed back inside  (+2)
  - break of structure / change of character                           (+2)
  - unfilled fair value gap                                            (+1)
  - price at a premium/discount extreme rather than mid-range          (+1)

Usage:
    python3 screen.py nse
    python3 screen.py crypto --top 10
"""
import argparse
import sys
from concurrent.futures import ThreadPoolExecutor

from fetch_data import fetch_ohlc
from universe import universe, validate

# Per-market gate defaults. Crypto volume from Yahoo is already USD notional;
# equity volume is share count, so turnover = close * volume there.
DEFAULTS = {
    "nse":    {"min_turnover": 5e8, "label": "INR"},   # ~Rs 50 crore/day
    "crypto": {"min_turnover": 5e7, "label": "USD"},   # ~$50m/day
}
MIN_REL_VOLUME = 1.2
LOOKBACK_BARS = 60
SWEEP_WINDOW = 5      # a sweep only counts if it happened in the last N bars
FVG_WINDOW = 20
RANGE_BARS = 30
# A dividend or split reprices the stock mechanically, so any level compared
# across the ex-date is measured on two different price bases. Below this share
# of price the distortion is inside normal daily noise and can be ignored.
MATERIAL_CA_PCT = 0.005
# An action this recent sits inside the sweep/BOS detection window, so those
# conditions cannot be trusted at all and the name is excluded outright.
CA_DISQUALIFY_BARS = SWEEP_WINDOW + 2


def _swings(candles, lookback=2):
    """Fractal swings as (index, price) pairs."""
    highs, lows = [], []
    for i in range(lookback, len(candles) - lookback):
        w = candles[i - lookback:i + lookback + 1]
        if candles[i]["high"] == max(c["high"] for c in w):
            highs.append((i, candles[i]["high"]))
        if candles[i]["low"] == min(c["low"] for c in w):
            lows.append((i, candles[i]["low"]))
    return highs, lows


def _structure(highs, lows):
    if len(highs) < 2 or len(lows) < 2:
        return "unclear"
    hh, hl = highs[-1][1] > highs[-2][1], lows[-1][1] > lows[-2][1]
    if hh and hl:
        return "bullish"
    if not hh and not hl:
        return "bearish"
    return "mixed"


def _sweep(candles, highs, lows, window=SWEEP_WINDOW, lookback=2):
    """A recent bar poked through a prior swing but closed back inside it —
    the classic stop-hunt / liquidity grab.

    Only swings that were still UNBROKEN going into the sweep count: a level
    price already closed through earlier is spent internal structure, not
    resting liquidity, and treating it as a sweep produces false positives.

    Both sides are scanned and the MOST RECENT sweep wins. Exhausting every
    high before looking at a single low would let a stale BSL sweep from
    several bars back mask a fresh SSL sweep — and because the two imply
    opposite trade directions, that mislabels the setup rather than merely
    ordering it oddly. At equal age a live sweep beats a stale one; a true tie
    is one outside bar taking both sides, reported as such rather than
    resolved arbitrarily.
    """
    n = len(candles)
    found = []
    for swings, side in ((highs, "BSL"), (lows, "SSL")):
        up = side == "BSL"
        for idx, price in reversed(swings):
            start = max(idx + lookback + 1, n - window)
            # unbroken = no close beyond it between confirmation and the window
            prior = candles[idx + lookback + 1:start]
            if any((c["close"] > price) if up else (c["close"] < price) for c in prior):
                continue
            for j in range(start, n):
                c = candles[j]
                poked = ((c["high"] > price and c["close"] < price) if up
                         else (c["low"] < price and c["close"] > price))
                if not poked:
                    continue
                later = candles[j + 1:]
                stale = any((x["close"] > price) if up else (x["close"] < price)
                            for x in later)
                found.append((n - 1 - j, stale, side, price))
                break
    if not found:
        return None

    found.sort(key=lambda f: (f[0], f[1]))
    age, stale, side, price = found[0]
    note = ""
    if stale:
        note = (" [STALE: price has since closed back "
                + ("above]" if side == "BSL" else "below]"))
    text = f"{side} swept @ {price:.2f} ({age} bars ago){note}"

    tie = next((f for f in found if f[0] == age and f[1] == stale and f[2] != side), None)
    if tie:
        text += f"  (+ {tie[2]} @ {tie[3]:.2f} same bar — outside bar took both sides)"
    return text


def _bos(candles, highs, lows, structure, window=SWEEP_WINDOW, lookback=2):
    """Close beyond the most recent confirmed swing = BOS (with trend) or
    CHoCH (against it)."""
    n = len(candles)
    recent = candles[max(0, n - window):]
    if highs:
        idx, price = highs[-1]
        if idx + lookback < n and any(c["close"] > price for c in recent):
            return "BOS up" if structure == "bullish" else "CHoCH up"
    if lows:
        idx, price = lows[-1]
        if idx + lookback < n and any(c["close"] < price for c in recent):
            return "BOS down" if structure == "bearish" else "CHoCH down"
    return None


def _unfilled_fvg(candles, window=FVG_WINDOW):
    """3-bar imbalance that price has not traded back through."""
    n = len(candles)
    for i in range(max(2, n - window), n):
        a, c = candles[i - 2], candles[i]
        if c["low"] > a["high"]:                                    # bullish gap
            if all(x["low"] > a["high"] for x in candles[i + 1:]):
                return f"bullish FVG {a['high']:.2f}-{c['low']:.2f}"
        if c["high"] < a["low"]:                                    # bearish gap
            if all(x["high"] < a["low"] for x in candles[i + 1:]):
                return f"bearish FVG {c['high']:.2f}-{a['low']:.2f}"
    return None


def _median(xs):
    s = sorted(xs)
    return s[len(s) // 2] if s else 0.0


def _recent_corporate_action(candles, meta):
    """The most recent material dividend/split inside the analysis window.

    A dividend gap looks exactly like a liquidity sweep to every mechanical
    test here: price pokes through a prior level and closes beyond it, leaving
    a fresh imbalance behind. It is not order flow, it is arithmetic, so it has
    to be detected rather than scored.

    Returns None, or {"kind","dt","amount","pct","bars_ago"} where pct is the
    action's size against the close before the ex-date.
    """
    actions = meta.get("corporate_actions") or []
    if not actions:
        return None
    first_dt = candles[0]["dt"]
    for a in reversed(actions):
        if a["dt"] < first_dt or a["amount"] is None:
            continue
        # index of the first bar on/after the ex-date, and the close before it
        idx = next((i for i, c in enumerate(candles) if c["dt"] >= a["dt"]), None)
        if idx is None or idx == 0:
            continue
        prior_close = candles[idx - 1]["close"]
        pct = (a["amount"] / prior_close) if a["kind"] == "dividend" else abs(1 - a["amount"])
        if pct < MATERIAL_CA_PCT:
            continue
        return {"kind": a["kind"], "dt": a["dt"], "amount": a["amount"],
                "pct": pct, "bars_ago": len(candles) - 1 - idx}
    return None


def analyse(ticker, market):
    candles, meta = fetch_ohlc(ticker, "90d", "1d", events=True)
    if len(candles) < 30:
        return None
    candles = candles[-LOOKBACK_BARS:]
    last = candles[-1]
    corp_action = _recent_corporate_action(candles, meta)

    vols = [c["volume"] for c in candles if c["volume"]]
    if not vols:
        return None
    med_vol = _median(vols)
    rel_vol = (last["volume"] / med_vol) if med_vol else 0.0
    turnover = _median([
        (c["volume"] if market == "crypto" else c["close"] * c["volume"])
        for c in candles if c["volume"]
    ])

    highs, lows = _swings(candles)
    structure = _structure(highs, lows)
    sweep = _sweep(candles, highs, lows)
    bos = _bos(candles, highs, lows, structure)
    fvg = _unfilled_fvg(candles)

    window = candles[-RANGE_BARS:]
    lo = min(c["low"] for c in window)
    hi = max(c["high"] for c in window)
    pos = (last["close"] - lo) / (hi - lo) if hi > lo else 0.5
    zone = "discount" if pos < 0.5 else "premium"
    extreme = pos < 0.35 or pos > 0.65

    score = (2 if sweep else 0) + (2 if bos else 0) + (1 if fvg else 0) + (1 if extreme else 0)

    return {
        "ticker": ticker, "close": last["close"], "turnover": turnover,
        "rel_volume": rel_vol, "structure": structure, "sweep": sweep, "bos": bos,
        "fvg": fvg, "zone": zone, "pos": pos, "range_lo": lo, "range_hi": hi,
        "score": score, "currency": meta.get("currency", ""),
        "corp_action": corp_action,
    }


def screen(market, top=10, min_turnover=None, min_rel_volume=MIN_REL_VOLUME, skip_validate=False):
    cfg = DEFAULTS[market]
    min_turnover = cfg["min_turnover"] if min_turnover is None else min_turnover

    tickers = universe(market)
    if skip_validate:
        valid, invalid = tickers, []
    else:
        valid, invalid = validate(tickers)
    if invalid:
        print(f"! {len(invalid)} symbol(s) no longer resolve (need review): {', '.join(invalid)}\n")

    def safe(t):
        try:
            return analyse(t, market)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = [r for r in ex.map(safe, valid) if r]

    passed = [r for r in rows
              if r["turnover"] >= min_turnover and r["rel_volume"] >= min_rel_volume]

    # Split out names whose structure spans a fresh dividend/split. Their sweep
    # and BOS readings are arithmetic, not order flow, so they must not compete
    # for shortlist slots against names with genuine structure.
    def disqualified(r):
        ca = r["corp_action"]
        return ca is not None and ca["bars_ago"] <= CA_DISQUALIFY_BARS

    excluded = [r for r in passed if disqualified(r)]
    passed = [r for r in passed if not disqualified(r)]
    passed.sort(key=lambda r: (-r["score"], -r["turnover"]))
    excluded.sort(key=lambda r: -r["score"])

    print(f"=== {market.upper()} mechanical ICT screen ===")
    print(f"scanned {len(rows)} | passed liquidity+activity gates {len(passed) + len(excluded)} "
          f"(turnover >= {min_turnover:,.0f} {cfg['label']}, rel_vol >= {min_rel_volume})")
    if excluded:
        print(f"{len(excluded)} excluded for a corporate action inside {CA_DISQUALIFY_BARS} bars "
              f"-> {len(passed)} analysable")
    print("NOTE: pre-filter only — these are candidates for ICT analysis, not signals.\n")

    if not passed:
        print("No names passed the gates. Nothing to analyse — this is a valid outcome,")
        print("not a failure; forcing a setup here would violate the discipline rules.")
        return []

    shortlist = passed[:top]
    for r in shortlist:
        print(f"{r['ticker']:16s} score {r['score']}  close {r['close']:>10,.2f} {r['currency']}  "
              f"relvol {r['rel_volume']:.2f}  turnover {r['turnover']:>14,.0f}")
        print(f"{'':16s} structure {r['structure']:8s} zone {r['zone']} ({r['pos']*100:.0f}% of "
              f"{r['range_lo']:,.2f}-{r['range_hi']:,.2f})")
        for label, val in (("sweep", r["sweep"]), ("structure break", r["bos"]), ("fvg", r["fvg"])):
            if val:
                print(f"{'':16s} {label}: {val}")
        ca = r["corp_action"]
        # Only worth flagging while the ex-date still sits inside the window the
        # premium/discount read spans; older than that, nothing here crosses it.
        if ca and ca["bars_ago"] <= RANGE_BARS:
            print(f"{'':16s} ! {ca['kind']} {ca['amount']:g} ex-{ca['dt'].date()} "
                  f"({ca['pct']*100:.1f}% of price, {ca['bars_ago']} bars ago) — levels either "
                  f"side of that date are on different price bases")
        print()

    if excluded:
        print(f"-- excluded: corporate action inside the last {CA_DISQUALIFY_BARS} bars "
              f"({len(excluded)} name(s)) --")
        print("   The gap is the reprice, not a liquidity grab; sweep/BOS here are artifacts.")
        for r in excluded:
            ca = r["corp_action"]
            print(f"   {r['ticker']:16s} would have scored {r['score']}  "
                  f"{ca['kind']} {ca['amount']:g} ex-{ca['dt'].date()} "
                  f"({ca['pct']*100:.1f}% of price, {ca['bars_ago']} bars ago)")
            if r["sweep"]:
                print(f"   {'':16s} suppressed sweep: {r['sweep']}")
        print()
    return shortlist


def main():
    p = argparse.ArgumentParser()
    p.add_argument("market", choices=["nse", "crypto"])
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--min-turnover", type=float, default=None)
    p.add_argument("--min-rel-volume", type=float, default=MIN_REL_VOLUME)
    p.add_argument("--skip-validate", action="store_true")
    a = p.parse_args()
    screen(a.market, a.top, a.min_turnover, a.min_rel_volume, a.skip_validate)


if __name__ == "__main__":
    main()
