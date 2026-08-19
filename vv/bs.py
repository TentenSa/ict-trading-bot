"""Black-Scholes pricer for NSE stock options (European-style, cash-settled).

Used to translate underlying-price levels (entry/stop/target) into option-price
levels. ALWAYS calibrate against a live quote before trusting a projection --
`calibrate()` reprices a known contract and reports the error.
"""
import math

R_INDIA = 0.065          # ~repo-linked risk-free


def _n(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def price(S, K, T, iv, kind, r=R_INDIA):
    """T in years, iv as decimal (0.30 = 30%), kind 'CE' or 'PE'."""
    if T <= 0:
        return max(0.0, S - K) if kind == "CE" else max(0.0, K - S)
    sig = iv * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * iv * iv) * T) / sig
    d2 = d1 - sig
    if kind == "CE":
        return S * _n(d1) - K * math.exp(-r * T) * _n(d2)
    return K * math.exp(-r * T) * _n(-d2) - S * _n(-d1)


def delta(S, K, T, iv, kind, r=R_INDIA):
    if T <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
    return _n(d1) if kind == "CE" else _n(d1) - 1.0


def calibrate(S, K, T, iv, kind, market_ltp, r=R_INDIA):
    """Reprice a live contract; returns (model, market, abs_err, pct_err)."""
    m = price(S, K, T, iv, kind, r)
    err = m - market_ltp
    return m, market_ltp, err, (err / market_ltp * 100 if market_ltp else float("nan"))


def implied_vol(S, K, T, kind, market_price, r=R_INDIA, lo=0.01, hi=3.0, tol=1e-6):
    """Solve for the vol that reprices `market_price` exactly. Returns None if
    the price is outside the no-arbitrage band the model can reach.

    Needed because a broker's published `iv` field is not necessarily in sync
    with its own bid/ask: on the NIFTY chain the two refresh on different
    cadences, so pairing a stale iv with a fresh mid mispriced the projection by
    2-4% -- the same class of error as trusting `close_price`. Solving vol from
    the mid makes the model agree with the market by construction, which is the
    only way a projected premium at some future spot means anything.
    """
    if T <= 0 or market_price is None or market_price <= 0:
        return None
    if price(S, K, T, hi, kind, r) < market_price:
        return None
    if price(S, K, T, lo, kind, r) > market_price:
        return None
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if price(S, K, T, mid, kind, r) < market_price:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)
