"""Shared Yahoo Finance chart-API fetch helper used by verify_signal.py and backtest.py."""
import json
import urllib.request
from datetime import datetime, timedelta, timezone

BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch_ohlc(ticker, range_, interval, events=False):
    """Returns (candles, meta). candles is a list of dicts sorted oldest->newest:
    {"dt": datetime (UTC), "open","high","low","close","volume"}. Skips null candles.

    With events=True the same request also asks for dividends and splits, and
    meta gains a "corporate_actions" list of
    {"kind": "dividend"|"split", "dt": datetime (UTC), "amount": float}
    sorted oldest->newest. This rides on the existing request rather than a
    second round trip, so it is free to switch on.
    """
    url = BASE_URL.format(ticker=ticker) + f"?range={range_}&interval={interval}"
    if events:
        url += "&events=div%2Csplit"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.load(resp)

    result = data["chart"]["result"]
    if not result:
        raise ValueError(f"No data returned for {ticker} (range={range_}, interval={interval})")
    r = result[0]
    ts = r["timestamp"]
    q = r["indicators"]["quote"][0]

    if events:
        actions = []
        for kind, key in (("dividend", "dividends"), ("split", "splits")):
            for e in (r.get("events", {}).get(key) or {}).values():
                # splits report numerator/denominator; dividends report amount
                amount = e.get("amount")
                if amount is None and e.get("denominator"):
                    amount = e.get("numerator", 0) / e["denominator"]
                actions.append({
                    "kind": kind,
                    "dt": datetime.fromtimestamp(e["date"], tz=timezone.utc),
                    "amount": amount,
                })
        r["meta"]["corporate_actions"] = sorted(actions, key=lambda a: a["dt"])

    candles = []
    for i in range(len(ts)):
        o, h, l, c, v = q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i]
        if o is None or h is None or l is None or c is None:
            continue
        candles.append({
            "dt": datetime.fromtimestamp(ts[i], tz=timezone.utc),
            "open": o, "high": h, "low": l, "close": c, "volume": v or 0,
        })
    return candles, r["meta"]


def combined_range(*candle_lists):
    """Min low / max high across one or more candle lists (e.g. daily + 1h)."""
    lows, highs = [], []
    for candles in candle_lists:
        for c in candles:
            lows.append(c["low"])
            highs.append(c["high"])
    if not lows:
        raise ValueError("No candles supplied")
    return min(lows), max(highs)


def detect_swings(candles, lookback=2):
    """Simple fractal swing detection: a bar is a swing high if its high is the
    max within +/-lookback bars, and a swing low if its low is the min within
    +/-lookback bars. Returns (swing_highs, swing_lows) as lists of price floats.
    """
    swing_highs, swing_lows = [], []
    n = len(candles)
    for i in range(lookback, n - lookback):
        window = candles[i - lookback:i + lookback + 1]
        if candles[i]["high"] == max(c["high"] for c in window):
            swing_highs.append(candles[i]["high"])
        if candles[i]["low"] == min(c["low"] for c in window):
            swing_lows.append(candles[i]["low"])
    return swing_highs, swing_lows


IST = timezone(timedelta(hours=5, minutes=30))

# exchange_time - host_time, re-measured on every market_now() call. Lets code
# with no meta in hand (log timestamps, deadline checks) still ask the exchange
# clock instead of the host's.
_SKEW = timedelta(0)


def exchange_now():
    """Best estimate of exchange time between fetches: the host clock corrected
    by the offset last measured against the feed.

    Use this for WALL-CLOCK decisions (is it past the cutoff, what time to stamp
    on a line). Do NOT use it to measure DURATIONS -- regularMarketTime freezes
    when the market closes, so differences taken from it stall at zero and a
    staleness detector built on it would never fire. Durations are immune to a
    constant offset anyway, so time.monotonic() is the right tool there.
    """
    return datetime.now(timezone.utc) + _SKEW


def market_now(meta, candles=None, bar=None):
    """Exchange wall-clock "now" as an aware UTC datetime.

    Taken from the feed's own regularMarketTime, never from the host clock. The
    host clock is not a safe source of truth for bar-close decisions: this VM has
    already been observed sitting 7 hours behind the exchange, which made every
    closed-bar filter discard the whole session and alert nothing -- silently,
    because an empty bar list is indistinguishable from a quiet market. The feed
    timestamps the data it serves, so it is the correct clock for judging it.

    Outside market hours regularMarketTime freezes at the last trade, which is
    the right answer here: it means "every bar of that session has closed".

    Falls back to the newest candle's close time rather than to datetime.now(),
    so a feed missing the field degrades to a clock-free answer instead of
    quietly reintroducing the bug this function exists to prevent.
    """
    global _SKEW
    t = (meta or {}).get("regularMarketTime")
    if t:
        now = datetime.fromtimestamp(t, timezone.utc)
        _SKEW = now - datetime.now(timezone.utc)
        return now
    if candles:
        return max(c["dt"] for c in candles) + (bar or timedelta(0))
    raise ValueError("no regularMarketTime in meta and no candles to fall back on")


def closed_bars(ticker, interval="5m"):
    """The current session's bars, excluding any bar still printing.

    A wick through a level is not a close through it, so an in-progress bar must
    never be judged -- that is the whole reason this filter exists.
    """
    bar = timedelta(minutes=int(interval.rstrip("m")))
    for rng in ("1d", "5d"):
        try:
            c, meta = fetch_ohlc(ticker, rng, interval)
        except Exception:
            continue
        if not c:
            continue
        now = market_now(meta, c, bar)
        # Session date comes from the data, not the clock: before the open the
        # feed's last-trade time still points at the previous session.
        session = max(b["dt"].astimezone(IST).date() for b in c)
        bars = [b for b in c
                if b["dt"].astimezone(IST).date() == session and b["dt"] + bar <= now]
        if bars:
            return bars
    return []
