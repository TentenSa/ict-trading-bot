# ICT trading signal bot

An ICT (Inner Circle Trader) signal toolchain for NSE equities/options and
crypto. Mechanical tools produce facts; the analyst produces judgement. No price
in a signal is ever invented — every number traces back to fetched OHLC or a live
broker call.

**Read `docs/LEARNINGS.md` before trading this.** It carries findings that cost
real backtests and real money to establish — including the fact that the
mechanical layer has no standalone edge.

## Setup on a fresh machine

Requires **python3 only** — the entire toolchain is standard library, no pip
install, no virtualenv.

```bash
git clone git@github.com:TentenSa/ict-trading-bot.git
cd ict-trading-bot
python3 --version          # 3.12 used in development
```

Credentials live **outside the repo** and are never committed:

```bash
mkdir -p ~/.claude
cat > ~/.claude/broker_creds.json <<'JSON'
{
  "upstox": {
    "api_key":      "your_api_key",
    "api_secret":   "your_api_secret",
    "redirect_uri": "your_redirect_uri",
    "access_token": "PASTE_AFTER_LOGIN"
  }
}
JSON
chmod 600 ~/.claude/broker_creds.json
```

Then, once per trading day (Upstox tokens expire ~03:30 IST):

```bash
python3 vv/upstox.py login          # prints the auth URL
python3 vv/upstox.py token "<code>" # paste the ?code= value from the redirect
```

An "extended" long-lived token authenticates on its own and needs no daily login.
Public data (OHLC via `fetch_data.py`) works with no credentials at all.

## Daily use

Start every session with the opening routine in `docs/WORKFLOW.md` — resolve the
open book and read the track record **before** screening:

```bash
python3 vv/backtest.py resolve      # score open signals forward
python3 vv/backtest.py report       # portfolio record + open book
python3 vv/review.py                # fill rate & execution quality
python3 vv/screen.py                # mechanical pre-filter (NOT a signal)
```

Then, after the analyst produces a signal:

```bash
python3 vv/verify_signal.py signal_x.json signal_x_prose.txt   # anti-hallucination
python3 vv/backtest.py log signal_x.json                       # into signals_log.jsonl
python3 vv/watch_signal.py                                     # live monitoring
```

## Layout

| Path | Purpose |
|---|---|
| `ict-trading-signal-bot-prompt.md` | The analyst system prompt |
| `docs/LEARNINGS.md` | Findings that cost money — read first |
| `docs/WORKFLOW.md` | Opening routine, output format, design rules |
| `vv/signals_log.jsonl` | **Cross-chat trade memory** — every signal, with outcomes |
| `vv/journal_annotations.json` | Hand annotations; source for the journal |
| `vv/signal_*.json` / `_prose.txt` | The 8 currently open positions |
| `vv/example_*` | Output format reference (AAPL, BTC) |

### Toolchain (`vv/`)

`screen.py` mechanical pre-filter · `fetch_data.py` OHLC · `upstox.py` broker +
option chain · `bs.py` pricing (off the **forward**, see LEARNINGS §3) ·
`costs.py` round-trip cost · `verify_signal.py` anti-hallucination checks ·
`backtest.py` log/resolve/report · `stats.py` per-ticker record · `review.py`
execution quality · `watch_signal.py` / `watch_triggers.py` / `watch_structure.py`
live monitors · `make_journal.py` + `xlsx.py` build `trading_journal.xlsx` ·
`journal_sync.py` · `scan_nifty.py` · `chart.py` · `opt_project.py` ·
`universe.py`

## Not committed

Credentials (`~/.claude/broker_creds.json`), the ~43 MB Upstox instrument cache
(`vv/.cache/`, re-downloaded every 24h), `__pycache__`, run logs, and
`trading_journal.xlsx` — that last one is a build artifact, regenerate it with:

```bash
python3 vv/make_journal.py
```
