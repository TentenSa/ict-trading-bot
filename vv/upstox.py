#!/usr/bin/env python3
"""
Upstox API v2 client for Indian equity options — zero dependencies (urllib only,
since pip is unavailable on this machine).

Two tiers of data:

  PUBLIC  (no credentials): the instrument master — every NSE contract with its
          strikes, expiries, lot sizes and instrument keys. Cached locally.
  PRIVATE (needs an access token): the live option chain — LTP, bid/ask, open
          interest, volume, IV and greeks. This is the part that makes strike
          selection possible instead of guesswork.

CREDENTIALS — never pasted into chat. Create ~/.claude/broker_creds.json
yourself and `chmod 600` it:

    {
      "upstox": {
        "api_key":      "your_api_key",
        "api_secret":   "your_api_secret",
        "redirect_uri": "https://127.0.0.1"
      }
    }

Then, once per trading day (Upstox tokens expire ~03:30 IST):

    python3 upstox.py login          # prints a URL — open it, log in
    python3 upstox.py token "<code>" # paste the ?code= value from the redirect

Usage:
    python3 upstox.py expiries RELIANCE
    python3 upstox.py chain RELIANCE 2026-08-25
"""
import base64
import gzip
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CREDS_PATH = Path.home() / ".claude" / "broker_creds.json"
CACHE_DIR = Path(__file__).parent / ".cache"
MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
MASTER_CACHE = CACHE_DIR / "upstox_nse.json"
MASTER_MAX_AGE_H = 24
API = "https://api.upstox.com/v2"
UA = {"User-Agent": "Mozilla/5.0"}


# ---------------------------------------------------------------- credentials

def load_creds():
    if not CREDS_PATH.exists():
        raise SystemExit(
            f"No credentials file at {CREDS_PATH}\n"
            "Create it yourself (never paste keys into chat) with:\n"
            '  {"upstox": {"api_key": "...", "api_secret": "...", '
            '"redirect_uri": "https://127.0.0.1"}}\n'
            f"then: chmod 600 {CREDS_PATH}")
    creds = json.loads(CREDS_PATH.read_text()).get("upstox")
    if not creds:
        raise SystemExit(f"{CREDS_PATH} has no 'upstox' section.")
    return creds


def require_oauth_creds():
    """api_key/api_secret are only needed to RUN the login flow. An extended
    token authenticates on its own, so don't demand them otherwise."""
    creds = load_creds()
    unfilled = [k for k in ("api_key", "api_secret")
                if not creds.get(k) or str(creds[k]).startswith("PASTE_")]
    if unfilled:
        raise SystemExit(
            f"Still placeholder value(s) for {', '.join(unfilled)} in {CREDS_PATH}.\n"
            "Get them from https://account.upstox.com/developer/apps and replace the\n"
            "PASTE_YOUR_..._HERE strings, then re-run this command.\n"
            "(Not needed if you are using an extended access_token.)")
    return creds


def _jwt_exp(token):
    """Expiry from a JWT's exp claim, or None if it isn't a decodable JWT."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("exp")
    except Exception:
        return None


def _save_creds(creds):
    blob = json.loads(CREDS_PATH.read_text()) if CREDS_PATH.exists() else {}
    blob["upstox"] = creds
    CREDS_PATH.write_text(json.dumps(blob, indent=2))
    CREDS_PATH.chmod(0o600)


def access_token():
    """Returns a valid token, or exits with instructions.

    Two token flavours: a daily OAuth token (expires ~03:30 IST, needs a fresh
    login each morning) and an 'extended' long-lived token. Validity is taken
    from the JWT's own exp claim where present, which covers both.
    """
    creds = load_creds()
    token = creds.get("access_token")
    if not token or str(token).startswith("PASTE_"):
        raise SystemExit("No access_token set. Run: python3 upstox.py login")

    exp = _jwt_exp(token)
    if exp is not None:
        now = datetime.now(timezone.utc).timestamp()
        if exp < now:
            when = datetime.fromtimestamp(exp, timezone.utc).isoformat()
            raise SystemExit(f"access_token expired at {when}. Run: python3 upstox.py login")
        return token

    # Not a JWT — fall back to the daily-issue-date rule.
    issued = creds.get("token_date")
    today = datetime.now(timezone.utc).date().isoformat()
    if issued != today:
        raise SystemExit(
            f"access_token was issued {issued}, today is {today}. Upstox daily "
            "tokens expire ~03:30 IST. Run: python3 upstox.py login")
    return token


def token_info():
    """Human-readable status of the stored token."""
    token = load_creds().get("access_token")
    if not token or str(token).startswith("PASTE_"):
        return "no token set"
    exp = _jwt_exp(token)
    if exp is None:
        return f"opaque token, issued {load_creds().get('token_date')}"
    left = (exp - datetime.now(timezone.utc).timestamp()) / 86400
    return (f"{'extended' if left > 2 else 'daily'} token, expires "
            f"{datetime.fromtimestamp(exp, timezone.utc).date()} ({left:.0f} days left)")


def login_url():
    c = require_oauth_creds()
    q = urllib.parse.urlencode({
        "client_id": c["api_key"], "redirect_uri": c["redirect_uri"],
        "response_type": "code"})
    return f"{API}/login/authorization/dialog?{q}"


def exchange_code(code):
    """Trade the one-time ?code= from the redirect for a daily access token."""
    c = require_oauth_creds()
    body = urllib.parse.urlencode({
        "code": code, "client_id": c["api_key"], "client_secret": c["api_secret"],
        "redirect_uri": c["redirect_uri"], "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(
        f"{API}/login/authorization/token", data=body,
        headers={**UA, "Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"})
    data = json.load(urllib.request.urlopen(req, timeout=30))
    if "access_token" not in data:
        raise SystemExit(f"Token exchange failed: {data}")
    c["access_token"] = data["access_token"]
    c["token_date"] = datetime.now(timezone.utc).date().isoformat()
    _save_creds(c)
    return c["access_token"]


# ------------------------------------------------------- instrument master

def _load_master(force=False):
    CACHE_DIR.mkdir(exist_ok=True)
    fresh = (MASTER_CACHE.exists()
             and (time.time() - MASTER_CACHE.stat().st_mtime) < MASTER_MAX_AGE_H * 3600)
    if fresh and not force:
        return json.loads(MASTER_CACHE.read_text())
    raw = urllib.request.urlopen(
        urllib.request.Request(MASTER_URL, headers=UA), timeout=120).read()
    data = json.loads(gzip.decompress(raw))
    MASTER_CACHE.write_text(json.dumps(data))
    return data


def resolve(symbol):
    """Underlying instrument record for an NSE symbol (e.g. 'RELIANCE', 'NIFTY').

    Stock options resolve to the cash-equity record. Index options have no EQ
    record at all — NIFTY's underlying is 'NSE_INDEX|Nifty 50' — so fall back to
    the underlying_key its own contracts declare rather than failing outright.
    """
    symbol = symbol.upper().replace(".NS", "")
    master = _load_master()
    for d in master:
        if d.get("trading_symbol") == symbol and d.get("instrument_type") == "EQ":
            return d

    # Index underlying: every CE/PE contract carries the key we need.
    for d in master:
        if d.get("asset_symbol") == symbol and d.get("instrument_type") in ("CE", "PE"):
            key = d.get("underlying_key") or d.get("asset_key")
            if key:
                return {"instrument_key": key, "trading_symbol": symbol,
                        "instrument_type": d.get("underlying_type", "INDEX")}
    raise SystemExit(f"{symbol}: no NSE equity or index underlying found in the "
                     "Upstox instrument master.")


def contracts(symbol):
    symbol = symbol.upper().replace(".NS", "")
    return [d for d in _load_master()
            if d.get("asset_symbol") == symbol and d.get("instrument_type") in ("CE", "PE")]


def expiries(symbol):
    out = sorted({d["expiry"] for d in contracts(symbol)})
    return [datetime.fromtimestamp(e / 1000, timezone.utc).date().isoformat() for e in out]


# ------------------------------------------------------------ option chain

def option_chain(symbol, expiry):
    """Live chain for one expiry. Requires a valid access token."""
    token = access_token()
    eq = resolve(symbol)
    q = urllib.parse.urlencode({
        "instrument_key": eq["instrument_key"], "expiry_date": expiry})
    req = urllib.request.Request(
        f"{API}/option/chain?{q}",
        headers={**UA, "Accept": "application/json", "Authorization": f"Bearer {token}"})
    return json.load(urllib.request.urlopen(req, timeout=30)).get("data", [])


def liquid_strikes(chain, side, spot, min_oi=500, max_spread_pct=2.0, count=5):
    """Strikes near spot that are actually tradeable.

    Illiquid strikes are the main way an options idea dies in practice, so this
    filters on real open interest and a sane bid/ask spread rather than just
    picking the nearest strike to spot. `side` is 'CE' or 'PE'.
    """
    key = "call_options" if side.upper() == "CE" else "put_options"
    rows = []
    for row in chain:
        leg = row.get(key) or {}
        md, gk = leg.get("market_data") or {}, leg.get("option_greeks") or {}
        bid, ask, ltp = md.get("bid_price"), md.get("ask_price"), md.get("ltp")
        oi, vol = md.get("oi"), md.get("volume")
        if not ltp or not oi or oi < min_oi:
            continue
        spread_pct = ((ask - bid) / ltp * 100) if (bid and ask and ltp) else None
        if spread_pct is not None and spread_pct > max_spread_pct:
            continue
        rows.append({
            "strike": row.get("strike_price"), "ltp": ltp, "bid": bid, "ask": ask,
            "oi": oi, "volume": vol, "spread_pct": spread_pct,
            "iv": gk.get("iv"), "delta": gk.get("delta"), "theta": gk.get("theta"),
            "instrument_key": leg.get("instrument_key"),
        })
    rows.sort(key=lambda r: abs((r["strike"] or 0) - spot))
    return rows[:count]


# --------------------------------------------------------------------- cli

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    if cmd == "login":
        url = login_url()          # validates credentials before printing anything
        print("Open this URL, log in, then copy the ?code= value from the redirect:\n")
        print(url)
        print("\nThen run:  python3 upstox.py token \"<code>\"")
    elif cmd == "token":
        exchange_code(sys.argv[2])
        print("Access token saved. Valid until ~03:30 IST tomorrow.")
    elif cmd == "expiries":
        for e in expiries(sys.argv[2])[:8]:
            print(e)
    elif cmd == "contracts":
        sym = sys.argv[2]
        cs = contracts(sym)
        exp = expiries(sym)[0]
        near = [c for c in cs if datetime.fromtimestamp(
            c["expiry"] / 1000, timezone.utc).date().isoformat() == exp]
        strikes = sorted({c["strike_price"] for c in near})
        print(f"{sym}: {len(cs)} contracts | nearest expiry {exp} | "
              f"lot {near[0]['lot_size']} | {len(strikes)} strikes "
              f"{strikes[0]:.0f}-{strikes[-1]:.0f}")
    elif cmd == "chain":
        sym, exp = sys.argv[2], sys.argv[3]
        chain = option_chain(sym, exp)
        spot = chain[0].get("underlying_spot_price") if chain else None
        print(f"{sym} {exp} | spot {spot} | {len(chain)} strikes")
        for side in ("CE", "PE"):
            print(f"\n-- {side} --")
            for r in liquid_strikes(chain, side, spot or 0):
                print(f"  {r['strike']:>9,.0f} ltp {r['ltp']:>8,.2f} oi {r['oi']:>9,} "
                      f"iv {r['iv']} delta {r['delta']}")
    else:
        print(f"unknown command: {cmd}")


if __name__ == "__main__":
    main()
