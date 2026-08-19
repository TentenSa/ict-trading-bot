#!/usr/bin/env python3
"""
Phase 1 verifier: checks a single ICT signal's JSON block for hallucinated
numbers and violations of the ict-trading-signal-bot-prompt.md discipline
rules. Report-only — never blocks, never edits, always exits 0.

Usage:
    python3 verify_signal.py path/to/signal.json
"""
import json
import sys
from datetime import datetime, timezone, timedelta

from fetch_data import fetch_ohlc, combined_range, detect_swings
from stats import ticker_advisory

RANGE_TOLERANCE_PCT = 0.005      # 0.5% buffer for "must fall within fetched data range"
SWING_MATCH_TOLERANCE_PCT = 0.005  # 0.5% tolerance for BSL/SSL matching a real swing
MATH_TOLERANCE_PCT = 0.001       # 0.1% tolerance for arithmetic checks (rounding)
AS_OF_MAX_STALENESS_HOURS = 48
MIN_RR = 2.0
# Round-trip cost may not eat more than this share of the stop distance. Beyond
# it the stop sits inside normal noise on the resolution timeframe, so the
# outcome says more about fees than about the setup.
MAX_COST_SHARE_OF_RISK = 0.10
# How far beyond the observed high/low a stop may legitimately sit, as a share
# of the range's span. A stop is placed past invalidation structure, so it is
# often outside the data; this still bounds it enough to catch a fabricated one.
STOP_BEYOND_RANGE_PCT = 0.02
# --- Execution-quality gates (added 2026-08-17) ---------------------------
# Correct bias is worth nothing if the order never triggers. As of 2026-08-17
# the log held 8 directionally-correct signals and 0 fills, so entry
# reachability is checked as explicitly as the arithmetic is.
#
# A signal counts as intraday when it is resolved on 1m/5m bars or given less
# than this much wall-clock time; those must sit close to spot to be takeable.
INTRADAY_HORIZON_HOURS = 48
MAX_ENTRY_DISTANCE_INTRADAY = 0.005   # 0.5% of spot
MAX_ENTRY_DISTANCE_SWING = 0.020      # 2.0% of spot
# Bars of lower-timeframe history used to judge whether an intraday entry sits
# inside ground price has already covered.
ENTRY_RANGE_LOOKBACK_BARS = 78        # ~1 session of 5m bars
BANNED_PHRASES = [
    "guaranteed", "guarantee", "can't lose", "cant lose", "risk-free",
    "sure thing", "will definitely", "100% win", "no risk",
]


class Findings:
    def __init__(self):
        self.items = []  # (check_id, status, message)

    def add(self, check_id, status, message):
        self.items.append((check_id, status, message))

    def failures(self):
        return [i for i in self.items if i[1] == "FAIL"]


def pct_diff(a, b):
    if b == 0:
        return abs(a - b)
    return abs(a - b) / abs(b)


def collect_price_fields(signal):
    """Every numeric price the bot claimed, as (path, value) pairs."""
    prices = []
    liq = signal.get("liquidity") or {}
    for entry in liq.get("bsl", []):
        prices.append((f"liquidity.bsl[{entry.get('label')}]", entry.get("price")))
    for entry in liq.get("ssl", []):
        prices.append((f"liquidity.ssl[{entry.get('label')}]", entry.get("price")))

    zones = signal.get("zones") or {}
    ob = zones.get("order_block") or {}
    if ob:
        prices += [("zones.order_block.low", ob.get("low")), ("zones.order_block.high", ob.get("high"))]
    fvg = zones.get("fvg") or {}
    if fvg:
        prices += [("zones.fvg.low", fvg.get("low")), ("zones.fvg.high", fvg.get("high"))]
    dr = zones.get("dealing_range") or {}
    if dr:
        prices += [("zones.dealing_range.low", dr.get("low")), ("zones.dealing_range.high", dr.get("high"))]
    if zones.get("equilibrium") is not None:
        prices.append(("zones.equilibrium", zones.get("equilibrium")))
    ote = zones.get("ote") or {}
    if ote:
        prices += [("zones.ote.low", ote.get("low")), ("zones.ote.high", ote.get("high")),
                    ("zones.ote.swing_high", ote.get("swing_high")), ("zones.ote.swing_low", ote.get("swing_low"))]

    for key in ("entry", "stop_loss"):
        if signal.get(key) is not None:
            prices.append((key, signal.get(key)))
    for i, tp in enumerate(signal.get("take_profits") or []):
        prices.append((f"take_profits[{i}]", tp))

    hyp = signal.get("hypothetical") or {}
    for key in ("entry", "stop_loss", "take_profit"):
        if hyp.get(key) is not None:
            prices.append((f"hypothetical.{key}", hyp.get(key)))

    return [(p, v) for p, v in prices if v is not None]


def check_data_fidelity(signal, daily, h1, findings, ltf=None):
    lo, hi = combined_range(daily, h1)
    buf = (hi - lo) * RANGE_TOLERANCE_PCT
    lo_b, hi_b = lo - buf, hi + buf

    all_prices = collect_price_fields(signal)

    # A stop sits BEYOND the extreme by construction — that is what placing it
    # past invalidation structure means — so the observed range is the wrong
    # bound for it. It still gets a bound, just a wider one, so a genuinely
    # hallucinated stop is still caught.
    stop_slack = (hi - lo) * STOP_BEYOND_RANGE_PCT
    lo_s, hi_s = lo - stop_slack, hi + stop_slack

    out_of_range = []
    for p, v in all_prices:
        is_stop = p in ("stop_loss", "hypothetical.stop_loss")
        if is_stop:
            if not (lo_s <= v <= hi_s):
                out_of_range.append((p, v, lo_s, hi_s, "stop allowance"))
        elif not (lo_b <= v <= hi_b):
            out_of_range.append((p, v, lo_b, hi_b, f"+/-{RANGE_TOLERANCE_PCT*100}% buffer"))

    if out_of_range:
        for p, v, blo, bhi, why in out_of_range:
            findings.add("1-range", "FAIL",
                          f"{p} = {v} is outside the observed data range [{lo:.2f}, {hi:.2f}] "
                          f"(allowed [{blo:.2f}, {bhi:.2f}], {why}) — possible hallucinated price.")
    else:
        findings.add("1-range", "PASS",
                      f"All {len(all_prices)} cited prices fall within observed range [{lo:.2f}, {hi:.2f}] "
                      f"(stops allowed out to [{lo_s:.2f}, {hi_s:.2f}]).")

    # An intraday-swing signal legitimately cites BOTH intraday levels and major
    # daily BSL/SSL, so a level passes if it matches swings on EITHER timeframe.
    # Requiring LTF-only would false-positive on valid HTF levels.
    d_highs, d_lows = detect_swings(daily, lookback=2)
    l_highs, l_lows = detect_swings(ltf, lookback=2) if ltf else ([], [])

    def match_of(price, daily_pool, ltf_pool):
        if any(pct_diff(price, s) <= SWING_MATCH_TOLERANCE_PCT for s in daily_pool):
            return "daily"
        if any(pct_diff(price, s) <= SWING_MATCH_TOLERANCE_PCT for s in ltf_pool):
            return "LTF"
        return None

    liq = signal.get("liquidity") or {}
    for entry in liq.get("bsl", []):
        price = entry.get("price")
        if price is None:
            continue
        tf = match_of(price, d_highs, l_highs)
        findings.add("2-swing-match", "PASS" if tf else "FAIL",
                      f"BSL @ {price} ({entry.get('label')}) "
                      + (f"matches a real detected {tf} swing high." if tf
                         else "does NOT match any detected daily or LTF swing high — verify manually."))
    for entry in liq.get("ssl", []):
        price = entry.get("price")
        if price is None:
            continue
        tf = match_of(price, d_lows, l_lows)
        findings.add("2-swing-match", "PASS" if tf else "FAIL",
                      f"SSL @ {price} ({entry.get('label')}) "
                      + (f"matches a real detected {tf} swing low." if tf
                         else "does NOT match any detected daily or LTF swing low — verify manually."))


def check_equilibrium_math(signal, findings):
    zones = signal.get("zones") or {}
    dr = zones.get("dealing_range") or {}
    eq = zones.get("equilibrium")
    if dr.get("low") is None or dr.get("high") is None or eq is None:
        findings.add("3-equilibrium", "SKIP", "dealing_range or equilibrium missing, cannot check.")
        return
    expected = (dr["low"] + dr["high"]) / 2
    ok = pct_diff(eq, expected) <= MATH_TOLERANCE_PCT
    findings.add("3-equilibrium", "PASS" if ok else "FAIL",
                  f"equilibrium={eq}, expected midpoint of dealing_range=({dr['low']}+{dr['high']})/2={expected:.2f}.")


def check_ote_math(signal, findings):
    ote = (signal.get("zones") or {}).get("ote") or {}
    if not ote or ote.get("swing_high") is None or ote.get("swing_low") is None:
        findings.add("4-ote", "SKIP", "ote.swing_high/swing_low missing, cannot recompute OTE.")
        return
    sh, sl = ote["swing_high"], ote["swing_low"]
    rng = sh - sl

    # Direction matters: a bullish leg (low -> high) retraces DOWN from the high
    # for a long-entry OTE; a bearish leg (high -> low) retraces UP from the low
    # for a short-entry OTE.
    #
    # The OTE belongs to the TRADE, not to the higher-timeframe bias. Those
    # differ on a counter-trend setup — which the prompt explicitly allows,
    # labelled and with reduced confidence — so taking htf_bias first would
    # fail every valid counter-trend signal. Trade direction wins; htf_bias is
    # only the fallback when there is no trade to read a direction from.
    direction = counter_trend = None
    t = trade_under_test(signal)
    if t:
        direction = "Bullish" if t[1] == "BUY" else "Bearish"
        htf = signal.get("htf_bias")
        counter_trend = htf in ("Bullish", "Bearish") and htf != direction
    if direction is None:
        htf = signal.get("htf_bias")
        if htf in ("Bullish", "Bearish"):
            direction = htf

    if direction == "Bullish":
        expected_high = sh - 0.62 * rng
        expected_low = sh - 0.79 * rng
    else:  # Bearish (default when direction can't be inferred)
        expected_low = sl + 0.62 * rng
        expected_high = sl + 0.79 * rng

    ok_low = pct_diff(ote.get("low", 0), expected_low) <= MATH_TOLERANCE_PCT * 5
    ok_high = pct_diff(ote.get("high", 0), expected_high) <= MATH_TOLERANCE_PCT * 5
    ok = ok_low and ok_high
    label = direction or "unknown direction"
    if counter_trend:
        label += f" trade, counter-trend to {signal.get('htf_bias')} HTF bias"
    findings.add("4-ote", "PASS" if ok else "FAIL",
                  f"OTE stated [{ote.get('low')}, {ote.get('high')}], expected 62-79% retrace "
                  f"({label}) of [{sl}, {sh}] = [{expected_low:.2f}, {expected_high:.2f}].")


def check_premium_discount_consistency(signal, current_price, findings):
    zones = signal.get("zones") or {}
    eq = zones.get("equilibrium")
    label = zones.get("premium_discount")
    if eq is None or label is None or current_price is None:
        findings.add("5-prem-disc", "SKIP", "equilibrium, premium_discount, or current price missing.")
        return
    expected = "discount" if current_price < eq else ("premium" if current_price > eq else "equilibrium")
    ok = label == expected
    connector = "and" if ok else "but"
    findings.add("5-prem-disc", "PASS" if ok else "FAIL",
                  f"Stated '{label}', {connector} current price {current_price} vs equilibrium {eq} implies '{expected}'.")


def check_as_of_staleness(signal, daily, findings):
    as_of_str = signal.get("as_of")
    if not as_of_str:
        findings.add("6-as-of", "SKIP", "as_of missing.")
        return
    try:
        as_of = datetime.fromisoformat(as_of_str.replace("Z", "+00:00"))
    except ValueError:
        findings.add("6-as-of", "FAIL", f"as_of '{as_of_str}' is not a valid ISO-8601 timestamp.")
        return
    last_candle = daily[-1]["dt"]
    now = datetime.now(timezone.utc)
    if as_of > now + timedelta(hours=1):
        findings.add("6-as-of", "FAIL", f"as_of {as_of} is in the future relative to now ({now}).")
        return
    delta_hours = abs((as_of - last_candle).total_seconds()) / 3600
    ok = delta_hours <= AS_OF_MAX_STALENESS_HOURS
    findings.add("6-as-of", "PASS" if ok else "FAIL",
                  f"as_of={as_of} vs last fetched daily candle={last_candle} "
                  f"(delta {delta_hours:.1f}h, max allowed {AS_OF_MAX_STALENESS_HOURS}h).")


def check_confluence(signal, findings):
    sig = signal.get("signal")
    if sig not in ("BUY", "SELL"):
        findings.add("7-confluence", "SKIP", f"signal={sig}, confluence rule only applies to BUY/SELL.")
        return
    htf_bias_ok = bool(signal.get("htf_bias")) and signal["htf_bias"] != "Ranging"
    narrative_ok = bool(signal.get("narrative"))
    zones = signal.get("zones") or {}
    pd_array_ok = bool(zones.get("order_block")) or bool(zones.get("fvg"))
    missing = [name for name, ok in [("htf_bias (non-ranging)", htf_bias_ok),
                                       ("narrative/draw-on-liquidity", narrative_ok),
                                       ("PD array (order_block or fvg)", pd_array_ok)] if not ok]
    if missing:
        findings.add("7-confluence", "FAIL", f"{sig} issued but missing confluence element(s): {', '.join(missing)}.")
    else:
        findings.add("7-confluence", "PASS", f"{sig} has all 3 required confluence elements present.")


def trade_under_test(signal):
    """The trade a discipline check should judge: the live one on BUY/SELL, or
    the would-be trade in the `hypothetical` block on NO_TRADE.

    NO_TRADE hypotheticals are held to the same rules because backtest.py
    scores them the same way — an unchecked hypothetical still lands in the
    win rate, so a malformed one corrupts the sample exactly like a bad live
    signal would.

    Returns (prefix, direction, entry, stop, take_profit, stated_rr), or None
    when there is no trade to judge at all.
    """
    sig = signal.get("signal")
    if sig in ("BUY", "SELL"):
        tps = signal.get("take_profits") or []
        return ("", sig, signal.get("entry"), signal.get("stop_loss"),
                tps[0] if tps else None, signal.get("risk_reward"))
    if sig == "NO_TRADE":
        hyp = signal.get("hypothetical") or {}
        if not hyp:
            return None
        # bias_direction is the would-be trade's side; anything not explicitly
        # bullish is treated as a short, matching backtest.log_signal().
        direction = "BUY" if hyp.get("bias_direction") == "bullish" else "SELL"
        return ("hypothetical.", direction, hyp.get("entry"), hyp.get("stop_loss"),
                hyp.get("take_profit"), None)
    return None


def check_risk_reward(signal, findings):
    t = trade_under_test(signal)
    if t is None:
        findings.add("8-rr", "SKIP",
                      f"signal={signal.get('signal')} with no hypothetical block — no trade to judge.")
        return
    prefix, direction, entry, stop, tp, stated_rr = t
    what = f"hypothetical {direction}" if prefix else f"{direction} signal"

    if entry is None or stop is None or tp is None:
        findings.add("8-rr", "FAIL",
                      f"{what} missing entry/stop_loss/take_profit — cannot be judged without these.")
        return
    risk = abs(entry - stop)
    if risk == 0:
        findings.add("8-rr", "FAIL", f"{what}: entry equals stop_loss — zero risk denominator, invalid.")
        return
    computed_rr = abs(tp - entry) / risk

    # Only BUY/SELL states an R:R in the JSON; there is nothing to cross-check
    # a hypothetical against, so report the computed value instead.
    if stated_rr is not None:
        rr_math_ok = pct_diff(stated_rr, computed_rr) <= 0.05
        findings.add("8-rr", "PASS" if rr_math_ok else "FAIL",
                      f"stated risk_reward={stated_rr}, computed from entry/stop/TP1={computed_rr:.2f}.")
    else:
        findings.add("8-rr", "SKIP",
                      f"{what} states no risk_reward to cross-check; computed {computed_rr:.2f}.")

    threshold_ok = computed_rr >= MIN_RR
    findings.add("8-rr-threshold", "PASS" if threshold_ok else "FAIL",
                  f"computed R:R={computed_rr:.2f} for {what}, rule requires >= 1:{MIN_RR} "
                  f"(if below, doc says flag as low-quality instead of a signal).")


def check_stop_beyond_structure(signal, findings):
    t = trade_under_test(signal)
    if t is None:
        findings.add("9-stop-structure", "SKIP",
                      f"signal={signal.get('signal')} with no hypothetical block — no trade to judge.")
        return
    prefix, direction, _entry, stop, _tp, _rr = t
    ob = (signal.get("zones") or {}).get("order_block") or {}
    if stop is None or ob.get("low") is None or ob.get("high") is None:
        findings.add("9-stop-structure", "SKIP", f"{prefix}stop_loss or order_block range missing.")
        return
    if direction == "BUY":
        ok = stop < ob["low"]
        detail = f"{prefix}stop {stop} should be below OB low {ob['low']}"
    else:
        ok = stop > ob["high"]
        detail = f"{prefix}stop {stop} should be above OB high {ob['high']}"
    findings.add("9-stop-structure", "PASS" if ok else "FAIL",
                  f"{detail} — {'satisfied' if ok else 'NOT satisfied'}.")


def check_required_fields(signal, prose_text, findings):
    required_top = ["ticker", "as_of", "timeframe_analyzed", "htf_bias", "liquidity", "narrative",
                     "zones", "signal", "invalidation", "confidence"]
    missing = [f for f in required_top if signal.get(f) in (None, "", {})]
    findings.add("10-required-fields", "PASS" if not missing else "FAIL",
                  "All required top-level fields present." if not missing else f"Missing fields: {missing}")

    disclaimer_marker = "not financial advice"
    has_disclaimer = prose_text is not None and disclaimer_marker in prose_text.lower()
    if prose_text is None:
        findings.add("10-disclaimer", "SKIP", "No prose text supplied alongside JSON, cannot check disclaimer.")
    else:
        findings.add("10-disclaimer", "PASS" if has_disclaimer else "FAIL",
                      "Disclaimer present in prose." if has_disclaimer else "Disclaimer text missing from prose output.")


def check_banned_phrases(signal, prose_text, findings):
    text = (signal.get("narrative", "") + " " + (prose_text or "")).lower()
    hits = [p for p in BANNED_PHRASES if p in text]
    findings.add("11-banned-phrases", "PASS" if not hits else "FAIL",
                  "No guarantee/no-risk language found." if not hits else f"Banned phrase(s) found: {hits}")


def check_track_record(signal, findings):
    tr = signal.get("track_record")
    if not tr:
        findings.add("12-track-record", "FAIL", "track_record field missing from signal JSON.")
        return
    actual = ticker_advisory(signal["ticker"])
    fields_match = (tr.get("wins") == actual["wins"] and tr.get("losses") == actual["losses"]
                     and tr.get("timeouts") == actual["timeouts"]
                     and tr.get("decisive_resolved") == actual["decisive_resolved"]
                     and tr.get("sufficient_history") == actual["sufficient_history"])
    findings.add("12-track-record", "PASS" if fields_match else "FAIL",
                  f"Stated track_record={tr}, actual from signals_log.jsonl={actual['note']!r} "
                  f"(wins={actual['wins']}, losses={actual['losses']}, timeouts={actual['timeouts']}, "
                  f"decisive={actual['decisive_resolved']}, sufficient={actual['sufficient_history']}).")


def check_resolution_fields(signal, findings):
    """Intraday-swing signals must declare how they are to be judged."""
    interval = signal.get("resolution_interval")
    horizon = signal.get("horizon_hours")
    cost = signal.get("round_trip_cost_pct")
    missing = [n for n, v in [("resolution_interval", interval),
                               ("horizon_hours", horizon),
                               ("round_trip_cost_pct", cost)] if v is None]
    if missing:
        findings.add("13-resolution-fields", "FAIL",
                      f"Missing resolution metadata: {missing} — backtester cannot judge this signal correctly.")
        return
    valid_intervals = {"1m": 7, "5m": 60, "15m": 60, "1h": 730}
    if interval not in valid_intervals:
        findings.add("13-resolution-fields", "FAIL",
                      f"resolution_interval '{interval}' not one of {sorted(valid_intervals)}.")
        return
    retention_days = valid_intervals[interval]
    horizon_days = horizon / 24
    ok = horizon_days <= retention_days
    findings.add("13-resolution-fields", "PASS" if ok else "FAIL",
                  f"resolution_interval={interval} (retention {retention_days}d), horizon={horizon}h "
                  f"({horizon_days:.1f}d), cost={cost*100:.2f}% — "
                  + ("horizon fits within data retention." if ok
                     else "horizon EXCEEDS retention; signal could age out before resolving."))


def check_cost_vs_risk(signal, findings):
    """A stop tight enough that round-trip cost consumes much of the risk has no
    edge left to measure: on the resolution timeframe it sits inside ordinary
    bar noise, so whichever way it resolves is an artifact of the cost model
    rather than the setup. Checked on the hypothetical block too — those enter
    the backtest sample exactly like a real trade does.
    """
    cost = signal.get("round_trip_cost_pct")
    if cost is None:
        findings.add("14-cost-vs-risk", "SKIP", "round_trip_cost_pct missing, cannot check.")
        return

    pairs = []
    if signal.get("signal") in ("BUY", "SELL"):
        pairs.append(("", signal.get("entry"), signal.get("stop_loss")))
    hyp = signal.get("hypothetical") or {}
    if hyp:
        pairs.append(("hypothetical.", hyp.get("entry"), hyp.get("stop_loss")))
    if not pairs:
        findings.add("14-cost-vs-risk", "SKIP", "no entry/stop pair (real or hypothetical) to check.")
        return

    for prefix, entry, stop in pairs:
        if entry is None or stop is None or entry == 0:
            findings.add("14-cost-vs-risk", "SKIP", f"{prefix}entry/stop_loss missing, cannot check.")
            continue
        stop_dist = abs(entry - stop) / abs(entry)
        if stop_dist == 0:
            findings.add("14-cost-vs-risk", "FAIL",
                          f"{prefix}entry equals stop_loss — zero risk, nothing to measure.")
            continue
        share = cost / stop_dist
        ok = share <= MAX_COST_SHARE_OF_RISK
        findings.add("14-cost-vs-risk", "PASS" if ok else "FAIL",
                      f"{prefix}stop is {stop_dist*100:.2f}% from entry; round-trip cost "
                      f"{cost*100:.2f}% is {share*100:.0f}% of risk "
                      f"(max {MAX_COST_SHARE_OF_RISK*100:.0f}%)"
                      + ("." if ok else " — stop too tight to survive costs; widen it to "
                         "structure or drop the setup."))


def _is_intraday(signal):
    return (signal.get("resolution_interval") in ("1m", "5m")
            or (signal.get("horizon_hours") or 0) <= INTRADAY_HORIZON_HOURS)


def check_entry_distance(signal, current_price, findings):
    """How far the entry sits from spot at the moment the signal is issued.

    This is the check that would have caught the whole 14-17 Aug run: entries
    0.9-3.8% away, every one of them directionally right and none of them ever
    traded. A limit that price has to travel a long way to reach is not a
    conservative entry, it is a trade that mostly does not happen.
    """
    entry = signal.get("entry")
    if entry is None or current_price is None:
        findings.add("15-entry-distance", "SKIP", "entry or current price missing.")
        return
    intraday = _is_intraday(signal)
    limit = MAX_ENTRY_DISTANCE_INTRADAY if intraday else MAX_ENTRY_DISTANCE_SWING
    dist = abs(entry - current_price) / current_price
    ok = dist <= limit
    findings.add("15-entry-distance", "PASS" if ok else "FAIL",
                 f"entry {entry} is {dist*100:.2f}% from spot {current_price} "
                 f"({'intraday' if intraday else 'swing'} limit {limit*100:.1f}%)"
                 + ("." if ok else " — too far to realistically fill; move the entry to the "
                    "nearest PD array or take a confirmation entry instead."))


def check_entry_inside_range(signal, ltf, findings):
    """An intraday limit must sit inside ground price has already covered.

    A SELL whose entry is ABOVE the highest high so far needs price to make a
    NEW high before the short exists — which contradicts the bearish premise
    the signal is built on. The 17 Aug NIFTY short missed by 0.10 points for
    exactly this reason: entry 24360.00 was placed 0.65 above the 24359.35
    rejection high it was derived from.

    Swing entries are legitimately allowed outside the range (selling into a
    higher-timeframe array above price is normal), so this only binds intraday.
    """
    entry, sig = signal.get("entry"), signal.get("signal")
    if entry is None or sig not in ("BUY", "SELL"):
        findings.add("16-entry-in-range", "SKIP", f"signal={sig} with no entry to judge.")
        return
    if not _is_intraday(signal):
        findings.add("16-entry-in-range", "SKIP",
                     "swing signal — an entry beyond the current range is legitimate here.")
        return
    if not ltf:
        findings.add("16-entry-in-range", "SKIP", "no lower-timeframe bars available.")
        return
    # Confine the window to the session the signal was issued in, up to as_of.
    # Reaching into a prior session lets yesterday's high certify today's entry
    # as "already covered", which is exactly the false PASS this check exists to
    # avoid — an intraday limit has to be reachable within its own session.
    window = ltf[-ENTRY_RANGE_LOOKBACK_BARS:]
    try:
        as_of = datetime.fromisoformat(signal["as_of"].replace("Z", "+00:00"))
        same_session = [b for b in ltf if b["dt"].date() == as_of.date() and b["dt"] <= as_of]
        if len(same_session) >= 3:
            window = same_session
    except (KeyError, ValueError, TypeError):
        pass
    hi = max(b["high"] for b in window)
    lo = min(b["low"] for b in window)
    if sig == "SELL":
        ok, edge, word = entry <= hi, hi, "high"
    else:
        ok, edge, word = entry >= lo, lo, "low"
    findings.add("16-entry-in-range", "PASS" if ok else "FAIL",
                 f"{sig} entry {entry} vs {len(window)}-bar {word} {edge:.2f} — "
                 + ("inside ground price has already covered."
                    if ok else
                    f"OUTSIDE it by {abs(entry-edge):.2f}. Filling this requires a NEW "
                    f"{word}, which contradicts the setup's own premise. Place the limit "
                    f"at or just inside {edge:.2f}."))


def check_cost_measured(signal, findings):
    """Flag the cash-equity default when the trade is expressed in options.

    0.001 is a stock round trip. On an index it overstates the real cost ~5x,
    and check 14 then demands a stop ten times too wide. `vv/costs.py` measures
    it from the live chain instead.
    """
    cost = signal.get("round_trip_cost_pct")
    if cost is None:
        findings.add("17-cost-measured", "SKIP", "round_trip_cost_pct missing.")
        return
    default = abs(cost - 0.001) < 1e-9
    findings.add("17-cost-measured", "FAIL" if default else "PASS",
                 f"round_trip_cost_pct={cost}"
                 + (" is the untouched cash-equity default — measure it for the actual "
                    "contract with `python3 vv/costs.py SYM EXPIRY STRIKE CE|PE` and use "
                    "that figure." if default else " (measured, not the 0.001 default)."))


def run(signal_path, prose_path=None):
    signal = json.load(open(signal_path))
    prose_text = open(prose_path).read() if prose_path else None

    ticker = signal["ticker"]
    daily, meta = fetch_ohlc(ticker, "180d", "1d")
    h1, _ = fetch_ohlc(ticker, "10d", "1h")
    try:
        ltf, _ = fetch_ohlc(ticker, "5d", signal.get("resolution_interval", "5m"))
    except Exception:
        ltf = h1
    current_price = meta.get("regularMarketPrice")

    findings = Findings()
    check_data_fidelity(signal, daily, h1, findings, ltf=ltf)
    check_equilibrium_math(signal, findings)
    check_ote_math(signal, findings)
    check_premium_discount_consistency(signal, current_price, findings)
    check_as_of_staleness(signal, daily, findings)
    check_confluence(signal, findings)
    check_risk_reward(signal, findings)
    check_stop_beyond_structure(signal, findings)
    check_required_fields(signal, prose_text, findings)
    check_banned_phrases(signal, prose_text, findings)
    check_track_record(signal, findings)
    check_resolution_fields(signal, findings)
    check_cost_vs_risk(signal, findings)
    check_entry_distance(signal, current_price, findings)
    check_entry_inside_range(signal, ltf, findings)
    check_cost_measured(signal, findings)

    print(f"\n=== Verification report: {ticker} ({signal.get('signal')}) ===\n")
    for check_id, status, message in findings.items:
        marker = {"PASS": "PASS", "FAIL": "FAIL", "SKIP": "SKIP"}[status]
        print(f"[{marker}] {check_id}: {message}")

    fails = findings.failures()
    print(f"\n{len(fails)} failure(s) out of {len(findings.items)} checks run.")
    return findings


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 verify_signal.py path/to/signal.json [path/to/prose.txt]")
        sys.exit(0)
    prose_arg = sys.argv[2] if len(sys.argv) > 2 else None
    run(sys.argv[1], prose_arg)
