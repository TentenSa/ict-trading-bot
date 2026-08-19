"""Scan universes for the screener.

NSE_FO is the NSE F&O-eligible (options-listed) stock list. SEBI/NSE revise this
list periodically, so it WILL drift — `validate()` checks every symbol against
Yahoo on each run and reports what no longer resolves rather than silently
dropping it. The authoritative list is NSE's own F&O securities file; treat this
as a working copy that needs periodic review.

Review log — record what changed and why, so a symbol is never silently dropped:

2026-08-16
  PEL  -> PIRAMALFIN   Piramal Enterprises renamed to Piramal Finance Ltd.
                       PEL.NS now 404s; PIRAMALFIN.NS resolves. Confirm F&O
                       eligibility carried across to the new symbol against
                       NSE's own file — a rename does not guarantee it.
  LTIM -> removed      LTIM.NS 404s and no variant resolves (LTIMINDTREE, LTI,
                       MINDTREE, LTIM.BO all fail); Yahoo search returns no
                       listed entity for LTIMindtree at all, so it appears to
                       have stopped trading separately. Parent LT is already
                       in the list, so the group is still covered. Re-add if
                       it reappears.
"""
from concurrent.futures import ThreadPoolExecutor

from fetch_data import fetch_ohlc

NSE_FO = [
    "ABB", "ABCAPITAL", "ABFRL", "ACC", "ADANIENT", "ADANIPORTS", "ALKEM", "AMBUJACEM",
    "APOLLOHOSP", "APOLLOTYRE", "ASHOKLEY", "ASIANPAINT", "ASTRAL", "ATUL", "AUBANK",
    "AUROPHARMA", "AXISBANK", "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE", "BALKRISIND",
    "BANDHANBNK", "BANKBARODA", "BATAINDIA", "BEL", "BERGEPAINT", "BHARATFORG",
    "BHARTIARTL", "BHEL", "BIOCON", "BOSCHLTD", "BPCL", "BRITANNIA", "BSOFT", "CANBK",
    "CANFINHOME", "CHAMBLFERT", "CHOLAFIN", "CIPLA", "COALINDIA", "COFORGE", "COLPAL",
    "CONCOR", "COROMANDEL", "CROMPTON", "CUB", "CUMMINSIND", "DABUR", "DALBHARAT",
    "DEEPAKNTR", "DIVISLAB", "DIXON", "DLF", "DRREDDY", "EICHERMOT", "ESCORTS",
    "EXIDEIND", "FEDERALBNK", "GAIL", "GLENMARK", "GNFC", "GODREJCP", "GODREJPROP",
    "GRANULES", "GRASIM", "GUJGASLTD", "HAL", "HAVELLS", "HCLTECH", "HDFCAMC",
    "HDFCBANK", "HDFCLIFE", "HEROMOTOCO", "HINDALCO", "HINDCOPPER", "HINDPETRO",
    "HINDUNILVR", "HONAUT", "ICICIBANK", "ICICIGI", "ICICIPRULI", "IDEA", "IDFCFIRSTB",
    "IEX", "IGL", "INDHOTEL", "INDIAMART", "INDIGO", "INDUSINDBK", "INDUSTOWER",
    "INFY", "IOC", "IPCALAB", "IRCTC", "ITC", "JINDALSTEL", "JKCEMENT", "JSWSTEEL",
    "JUBLFOOD", "KOTAKBANK", "LALPATHLAB", "LAURUSLABS", "LICHSGFIN", "LT", "LTF",
    "LTTS", "LUPIN", "M&M", "MANAPPURAM", "MARICO", "MARUTI", "MCX",
    "METROPOLIS", "MFSL", "MGL", "MOTHERSON", "MPHASIS", "MRF", "MUTHOOTFIN",
    "NATIONALUM", "NAUKRI", "NAVINFLUOR", "NESTLEIND", "NMDC", "NTPC", "OBEROIRLTY",
    "OFSS", "ONGC", "PAGEIND", "PERSISTENT", "PETRONET", "PFC", "PIDILITIND",
    "PIIND", "PIRAMALFIN", "PNB", "POLYCAB", "POWERGRID", "PVRINOX", "RAMCOCEM", "RBLBANK",
    "RECLTD", "RELIANCE", "SAIL", "SBICARD", "SBILIFE", "SBIN", "SHREECEM",
    "SHRIRAMFIN", "SIEMENS", "SRF", "SUNPHARMA", "SUNTV", "SYNGENE", "TATACHEM",
    "TATACOMM", "TATACONSUM", "TATAPOWER", "TATASTEEL", "TCS", "TECHM", "TITAN",
    "TORNTPHARM", "TRENT", "TVSMOTOR", "UBL", "ULTRACEMCO", "UNITDSPR", "UPL",
    "VEDL", "VOLTAS", "WIPRO", "ZYDUSLIFE",
]

# Liquid crypto pairs. Yahoo uses a numeric suffix for some newer tokens
# (e.g. Hyperliquid is HYPE32196-USD, NOT HYPE-USD, which is a different token).
CRYPTO = [
    "BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD", "DOGE-USD",
    "ADA-USD", "AVAX-USD", "LINK-USD", "HYPE32196-USD",
]


def nse_symbol(sym):
    return sym if sym.endswith((".NS", ".BO")) else f"{sym}.NS"


def universe(market):
    if market == "nse":
        return [nse_symbol(s) for s in NSE_FO]
    if market == "crypto":
        return list(CRYPTO)
    raise ValueError(f"unknown market: {market!r} (expected 'nse' or 'crypto')")


def validate(tickers, workers=8):
    """Returns (valid, invalid). Invalid = symbol no longer resolves on Yahoo
    (delisted, renamed, or merged). Reported, never silently dropped."""
    def check(t):
        try:
            c, _ = fetch_ohlc(t, "5d", "1d")
            return t, bool(c)
        except Exception:
            return t, False

    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(check, tickers))
    return [t for t, ok in results if ok], [t for t, ok in results if not ok]


if __name__ == "__main__":
    import sys
    market = sys.argv[1] if len(sys.argv) > 1 else "nse"
    tickers = universe(market)
    valid, invalid = validate(tickers)
    print(f"{market}: {len(valid)}/{len(tickers)} symbols resolve")
    if invalid:
        print(f"STALE (need review): {invalid}")
