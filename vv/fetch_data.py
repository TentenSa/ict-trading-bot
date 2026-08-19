"""Shared Yahoo Finance chart-API fetch helper used by verify_signal.py and backtest.py."""
import json
import urllib.request
from datetime import datetime, timezone

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
