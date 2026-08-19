# Operating workflow

## Every "find me a trade" — run these BEFORE screening, not after

1. `python3 vv/backtest.py resolve` — walks open signals forward and scores them.
   Without this, `stats.py` reads stale outcomes.
2. `python3 vv/backtest.py report` — portfolio-wide record (total / open /
   resolved / unresolvable). The only cross-ticker view; `stats.py <ticker>` is
   per-ticker and stays silent until 5 resolved trades exist.
3. `python3 vv/review.py` — execution quality: fill rate, median entry distance,
   and how many signals were directionally right versus how many actually traded.
   `report` cannot show this: an unfilled limit is never a win or a loss, so it
   never enters the win rate. Quote the fill rate back and let it set how close
   the day's entries go. See LEARNINGS.md §2.
4. **Check the open book.** Never issue a second signal on a ticker whose
   existing signal is still `outcome=open` — say it is already live and pick a
   different name.

`vv/signals_log.jsonl` **is** the cross-chat trade memory — every signal ever
issued, with outcomes. Never reconstruct past trades from conversation history;
read the log with the tools.

### For an intraday idea, two extra checks

- **Is it an index expiry day?** `python3 vv/upstox.py expiries NIFTY` (weeklies
  expire Tuesday). If so, pull the 0DTE chain and compute max pain and OI walls.
- **Does the target fit inside the day?** Compare it to the 0DTE ATM straddle.
  See LEARNINGS.md §5.

### A NO_TRADE is not "nothing to do"

Write the triggers down, log it with a `hypothetical` block so the backtester
scores whether standing aside was right, and arm `vv/watch_triggers.py` on the
levels — **closed bars only**, a wick through a level is not a close through it.

Let the resolved record actually change decisions: if a setup type or ticker has
a losing record, say so and either downgrade confidence or skip it. If nothing
has resolved yet, state that plainly rather than implying an edge exists.

---

## Output format when finalising trades

End with the **top 3 trades** as **two stacked markdown tables** — never
prose-only, never a single wide table:

```
UNDERLYING
| # | Ticker | Dir  | Entry | Exit(TP1) | TP2  | SL   | R:R  |

OPTION LEG
| # | Strike / Expiry  | Lot | Debit/lot | Prem In | Prem Exit | Prem SL | At TP1 |
```

Best setup first. If fewer than 3 genuinely qualify, show the qualifying ones and
note in one line what the others lacked — **do not pad to three by lowering the
bar.**

Premiums come from a live `upstox.option_chain()` call, priced off the
**parity-implied forward**, not spot (LEARNINGS.md §3). Mark modelled premiums
with `~`. Never invent a strike or premium to fill a cell; if the chain is
unavailable, leave the OPTION LEG table out and say why.

---

## Design rule

Mechanical tools produce **facts**; the analyst produces **judgement**. Never let
a tool infer bias, entry, or confidence. No price in a signal is ever invented —
every number traces back to fetched OHLC or a live broker call, and
`verify_signal.py` exists to enforce that.

## Trading context

Indian equities and equity **options** (never futures) via Upstox, plus crypto.
Swing rather than scalp.
