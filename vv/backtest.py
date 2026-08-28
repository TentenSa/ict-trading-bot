#!/usr/bin/env python3
"""
Phase 2: signal logging + outcome-based backtesting.

- log_signal(): append a signal JSON to signals_log.jsonl with status="open".
- resolve_open_signals(): for every open entry, walk forward bar-by-bar on
  intraday candles (default 5m) from as_of, in three stages:
    1. the entry price must actually be touched -- a pending limit that never
       fills is recorded as "never_filled", NOT as a win or a loss, because no
       trade ever existed;
    2. only after that fill do stop and target count;
    3. the target must clear round-trip costs before it scores as a win.
  Any bar where the ordering of fill/stop/target is ambiguous is re-examined at
  1m resolution; if that data is unavailable the conservative reading wins.
  BUY/SELL use the real entry/stop_loss/take_profits[0]; NO_TRADE uses the
  "hypothetical" block. Gives up after horizon_hours (default 72).
- report_stats(): win rate / breakdown over all resolved signals.

Usage:
    python3 backtest.py log path/to/signal.json
    python3 backtest.py resolve
    python3 backtest.py report
"""
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fetch_data import fetch_ohlc

LOG_PATH = Path(__file__).parent / "signals_log.jsonl"

# --- Intraday-swing resolution settings ---
# Walk forward on 5m candles: 60-day retention (vs 7 days for 1m), fine enough
# to order most stop-vs-target hits correctly.
RESOLUTION_INTERVAL = "5m"
RESOLUTION_RANGE = "60d"
# An intraday-swing idea gets 72 hours of wall-clock time to reach stop or
# target before it is marked unresolved.
MAX_HORIZON_HOURS = 72
# Round-trip cost (fees + spread) a trade must clear to count as a win. The
# target is moved against the trade by this fraction; the stop is left at its
# raw level (a stop-out is already a loss, and slippage beyond it is not
# modelled).
ROUND_TRIP_COST_PCT = 0.001  # 0.1%
# When stop and target both fall inside one 5m bar, re-fetch that bar at 1m to
# see which was actually hit first. Only possible within 1m's 7-day retention;
# older bars fall back to the conservative "count as loss" rule.
DISAMBIGUATION_INTERVAL = "1m"
DISAMBIGUATION_RANGE = "7d"
RESOLUTION_BAR_MINUTES = 5


def _read_log():
    if not LOG_PATH.exists():
        return []
    lines = [l for l in LOG_PATH.read_text().splitlines() if l.strip()]
    return [json.loads(l) for l in lines]


def _write_log(entries):
    with open(LOG_PATH, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def log_signal(signal_path):
    signal = json.load(open(signal_path))
    entries = _read_log()

    trade_direction = None
    entry_price = stop_price = tp_price = None

    if signal.get("signal") in ("BUY", "SELL"):
        trade_direction = signal["signal"]
        entry_price = signal.get("entry")
        stop_price = signal.get("stop_loss")
        tps = signal.get("take_profits") or []
        tp_price = tps[0] if tps else None
    elif signal.get("signal") == "NO_TRADE":
        hyp = signal.get("hypothetical") or {}
        if hyp:
            trade_direction = "BUY" if hyp.get("bias_direction") == "bullish" else "SELL"
            entry_price = hyp.get("entry")
            stop_price = hyp.get("stop_loss")
            tp_price = hyp.get("take_profit")

    if entry_price is None or stop_price is None or tp_price is None:
        print(f"WARNING: {signal['ticker']} signal={signal.get('signal')} has no usable "
              f"entry/stop/tp (real or hypothetical) — logged but will be unresolvable.")

    record = {
        "ticker": signal["ticker"],
        "as_of": signal["as_of"],
        "signal": signal.get("signal"),
        "is_hypothetical": signal.get("signal") == "NO_TRADE",
        "trade_direction": trade_direction,
        "entry": entry_price,
        "stop_loss": stop_price,
        "take_profit": tp_price,
        "confidence": signal.get("confidence"),
        "resolution_interval": signal.get("resolution_interval", RESOLUTION_INTERVAL),
        "horizon_hours": signal.get("horizon_hours", MAX_HORIZON_HOURS),
        "round_trip_cost_pct": signal.get("round_trip_cost_pct", ROUND_TRIP_COST_PCT),
        "status": "open",
        "outcome": None,
        "resolved_at": None,
        "bars_elapsed": 0,
        "hours_elapsed": 0.0,
        "disambiguated": False,
        "entry_filled": False,
        "filled_at": None,
        "bars_to_fill": None,
        # Snapshot of the fields the JOURNAL needs that live only in the signal
        # file. Resolved signal files get deleted once logged, so without this
        # copy the journal's HTF-bias / OTE / TP2 / invalidation columns go blank
        # for every older trade. They used to be back-filled by inheriting the
        # NEWEST signal for that ticker, which silently put one trade's text on
        # another's row until that bug was fixed on 2026-08-26. Blank was the
        # honest result; persisted is the correct one.
        "snapshot": {
            "htf_bias": signal.get("htf_bias"),
            "invalidation": signal.get("invalidation"),
            "take_profits": signal.get("take_profits"),
            "zones": signal.get("zones"),
            "option": signal.get("option"),
            "lot": signal.get("lot"),
        },
    }
    entries.append(record)
    _write_log(entries)
    print(f"Logged {record['ticker']} {record['signal']} (as_of {record['as_of']}) -> {LOG_PATH}")
    return record


def _effective_target(direction, tp, cost_pct):
    """Move the target against the trade by the round-trip cost, so a 'win'
    must clear fees+spread rather than merely touching the raw level."""
    return tp * (1 + cost_pct) if direction == "BUY" else tp * (1 - cost_pct)


def _hits(direction, candle, stop, eff_tp):
    if direction == "BUY":
        return candle["low"] <= stop, candle["high"] >= eff_tp
    return candle["high"] >= stop, candle["low"] <= eff_tp


_MINUTE_CACHE = {}


def _minutes(ticker):
    """1m bars for a ticker, fetched once per run. Empty list if unavailable
    (outside 1m's 7-day retention, or the fetch failed)."""
    if ticker not in _MINUTE_CACHE:
        try:
            _MINUTE_CACHE[ticker], _ = fetch_ohlc(
                ticker, DISAMBIGUATION_RANGE, DISAMBIGUATION_INTERVAL)
        except Exception:
            _MINUTE_CACHE[ticker] = []
    return _MINUTE_CACHE[ticker]


def _touches(candle, level):
    return candle["low"] <= level <= candle["high"]


def _replay(bars, direction, entry, stop, eff_tp, filled):
    """Walk fine-grained bars in order, honouring sequence: the entry must fill
    before stop or target can resolve the trade.

    Returns (filled, outcome, event_dt). outcome is None if nothing concluded.
    """
    for c in bars:
        if not filled:
            if _touches(c, entry):
                filled = True
                # a bar can fill AND then resolve; fall through to check below
            else:
                continue
        hit_stop, hit_tp = _hits(direction, c, stop, eff_tp)
        if hit_stop and hit_tp:
            return filled, None, c["dt"]        # ambiguous even here
        if hit_stop:
            return filled, "loss", c["dt"]
        if hit_tp:
            return filled, "win", c["dt"]
    return filled, None, None


def _refine(ticker, bar_start, direction, entry, stop, eff_tp, filled):
    """Re-examine one resolution bar at 1m to establish the true ordering of
    entry fill vs stop/target. Returns (filled, outcome, dt) or (filled, None,
    None) when the finer data isn't available."""
    minute = _minutes(ticker)
    if not minute:
        return filled, None, None
    bar_end = bar_start + timedelta(minutes=RESOLUTION_BAR_MINUTES)
    window = [c for c in minute if bar_start <= c["dt"] < bar_end]
    if not window:
        return filled, None, None
    return _replay(window, direction, entry, stop, eff_tp, filled)


def _resolve_one(entry):
    if entry["entry"] is None or entry["stop_loss"] is None or entry["take_profit"] is None:
        entry["status"] = "unresolvable"
        return entry

    try:
        as_of = datetime.fromisoformat(entry["as_of"].replace("Z", "+00:00"))
    except ValueError:
        entry["status"] = "unresolvable"
        return entry

    interval = entry.get("resolution_interval", RESOLUTION_INTERVAL)
    horizon_hours = entry.get("horizon_hours", MAX_HORIZON_HOURS)
    cost_pct = entry.get("round_trip_cost_pct", ROUND_TRIP_COST_PCT)

    candles, _ = fetch_ohlc(entry["ticker"], RESOLUTION_RANGE, interval)
    forward = [c for c in candles if c["dt"] > as_of]
    if not forward:
        return entry  # no bars since the signal yet, stays open

    # If the oldest bar we can still fetch is already past as_of, earlier price
    # action has aged out of Yahoo's retention and the verdict would be wrong.
    if candles and candles[0]["dt"] > as_of:
        entry["status"] = "unresolvable"
        entry["outcome"] = "data_expired"
        return entry

    direction = entry["trade_direction"]
    entry_px, stop = entry["entry"], entry["stop_loss"]
    eff_tp = _effective_target(direction, entry["take_profit"], cost_pct)

    deadline = as_of + timedelta(hours=horizon_hours)
    filled = bool(entry.get("entry_filled"))
    bars = 0

    def finish(outcome, dt, elapsed_h):
        entry.update(status="resolved", outcome=outcome, resolved_at=dt.isoformat(),
                      bars_elapsed=bars, hours_elapsed=round(elapsed_h, 2),
                      entry_filled=filled)
        return entry

    for candle in forward:
        bars += 1
        elapsed_h = (candle["dt"] - as_of).total_seconds() / 3600

        if candle["dt"] > deadline:
            # A pending entry that never filled is NOT a loss — no trade existed.
            return finish("never_filled" if not filled else "unresolved_timeout",
                           candle["dt"], elapsed_h)

        touches_entry = (not filled) and _touches(candle, entry_px)
        hit_stop, hit_tp = _hits(direction, candle, stop, eff_tp)

        # Nothing of interest in this bar.
        if not filled and not touches_entry:
            continue
        if filled and not (hit_stop or hit_tp):
            continue

        # Any bar where ordering matters gets re-examined at 1m.
        ambiguous = (touches_entry and (hit_stop or hit_tp)) or (hit_stop and hit_tp)
        if ambiguous:
            filled2, outcome, dt = _refine(entry["ticker"], candle["dt"], direction,
                                            entry_px, stop, eff_tp, filled)
            if outcome:
                filled = filled2
                entry["disambiguated"] = True
                return finish(outcome, dt or candle["dt"], elapsed_h)
            if filled2 != filled:
                filled = filled2
                entry["filled_at"] = candle["dt"].isoformat()
                entry["bars_to_fill"] = bars
            # Conservative fallback: if a stop was touched in a bar we cannot
            # order, assume the worst; never hand out an unverified win.
            if filled and hit_stop:
                return finish("loss", candle["dt"], elapsed_h)
            if touches_entry and not filled:
                filled = True
                entry["filled_at"] = candle["dt"].isoformat()
                entry["bars_to_fill"] = bars
            continue

        if touches_entry:
            filled = True
            entry["filled_at"] = candle["dt"].isoformat()
            entry["bars_to_fill"] = bars
            continue

        if hit_stop:
            return finish("loss", candle["dt"], elapsed_h)
        if hit_tp:
            return finish("win", candle["dt"], elapsed_h)

    last = forward[-1]

    # Bars ran out with no verdict. The loop above only times out when it finds a
    # bar PAST the deadline, so a horizon that expires at or after a session close
    # never resolves: the next bar prints the following morning. If the deadline
    # has already passed in wall-clock time, no future bar can land inside the
    # horizon, and waiting for one leaves the entry open indefinitely.
    if datetime.now(timezone.utc) > deadline:
        return finish("never_filled" if not filled else "unresolved_timeout",
                      last["dt"], (last["dt"] - as_of).total_seconds() / 3600)

    entry.update(bars_elapsed=bars, entry_filled=filled,
                  hours_elapsed=round((last["dt"] - as_of).total_seconds() / 3600, 2))
    return entry


def resolve_open_signals():
    entries = _read_log()
    changed = 0
    for entry in entries:
        if entry["status"] != "open":
            continue
        before = entry["status"]
        _resolve_one(entry)
        if entry["status"] != before:
            changed += 1
    _write_log(entries)
    print(f"Checked {sum(1 for e in entries if e['status'] in ('open','resolved','unresolvable'))} entries, "
          f"{changed} newly resolved.")
    return entries


def cancel_signal(ticker, as_of, reason):
    """Retire an open signal whose premise has expired before its horizon has.

    An intraday setup is scoped to its own session: the arrays it was read from
    are gone the next morning. Leaving it armed lets a stale entry fill against
    a market it no longer describes, and the fill would then be scored as if the
    idea had been live all along. Cancelling is not the same as a loss -- no
    trade existed -- so it is excluded from the win rate exactly like
    never_filled, and the reason is recorded rather than implied.
    """
    entries = _read_log()
    hits = [e for e in entries
            if e["ticker"] == ticker and (as_of is None or e["as_of"] == as_of)
            and e["status"] == "open"]
    if not hits:
        print(f"No open signal for {ticker}" + (f" at {as_of}" if as_of else ""))
        return
    if len(hits) > 1 and as_of is None:
        print(f"{len(hits)} open signals for {ticker} — pass the as_of to pick one:")
        for e in hits:
            print(f"   {e['as_of']}  entry {e['entry']}")
        return
    e = hits[0]
    e["status"] = "unresolvable"
    e["outcome"] = "cancelled"
    e["resolved_at"] = datetime.now(timezone.utc).isoformat()
    e["note"] = f"Cancelled before fill: {reason}"
    _write_log(entries)
    print(f"Cancelled {ticker} {e['trade_direction']} @ {e['entry']} (as_of {e['as_of']})\n  {reason}")


def report_stats():
    entries = _read_log()
    resolved = [e for e in entries if e["status"] == "resolved" and e["outcome"] in ("win", "loss")]
    never_filled = [e for e in entries if e.get("outcome") == "never_filled"]

    def summarize(label, subset):
        if not subset:
            print(f"{label}: no resolved trades yet.")
            return
        wins = sum(1 for e in subset if e["outcome"] == "win")
        avg_h = sum(e.get("hours_elapsed") or 0 for e in subset) / len(subset)
        print(f"{label}: {wins}/{len(subset)} wins ({100*wins/len(subset):.1f}%), "
              f"avg time to resolve {avg_h:.1f}h")

    print(f"\n=== Backtest report ({LOG_PATH.name}) ===")
    print(f"Resolution: {RESOLUTION_INTERVAL} bars | horizon {MAX_HORIZON_HOURS}h | "
          f"round-trip cost {ROUND_TRIP_COST_PCT*100:.2f}%")
    print(f"Total logged: {len(entries)} | open: {sum(1 for e in entries if e['status']=='open')} | "
          f"resolved: {len(resolved)} | unresolved_timeout: "
          f"{sum(1 for e in entries if e.get('outcome')=='unresolved_timeout')} | "
          f"unresolvable: {sum(1 for e in entries if e['status']=='unresolvable')} | "
          f"data_expired: {sum(1 for e in entries if e.get('outcome')=='data_expired')}")
    if never_filled:
        print(f"never_filled: {len(never_filled)} (entry limit never reached — excluded "
              f"from win rate, these were not real trades)")
    disamb = sum(1 for e in entries if e.get("disambiguated"))
    if disamb:
        print(f"({disamb} resolved via 1m disambiguation of same-bar stop/target hits)")
    print()

    summarize("Real signals (BUY/SELL)", [e for e in resolved if not e["is_hypothetical"]])
    summarize("NO_TRADE hypotheticals (would-be trade)", [e for e in resolved if e["is_hypothetical"]])
    summarize("Overall", resolved)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: backtest.py [log <signal.json> | resolve | report | "
              "cancel <ticker> <as_of> <reason>]")
        sys.exit(0)
    cmd = sys.argv[1]
    if cmd == "log":
        log_signal(sys.argv[2])
    elif cmd == "resolve":
        resolve_open_signals()
    elif cmd == "report":
        report_stats()
    elif cmd == "cancel":
        cancel_signal(sys.argv[2], sys.argv[3] or None, " ".join(sys.argv[4:]))
    else:
        print(f"Unknown command: {cmd}")
