# ICT trading signal bot

A trade-idea toolchain for Indian equity options. Mechanical tools produce
**facts**; the analyst (you) produces **judgement**.

## Read these before working

- **`docs/WORKFLOW.md`** — the opening routine. Run it BEFORE screening on every
  "find me a trade", not after.
- **`docs/LEARNINGS.md`** — 17 findings that cost real money or real backtests to
  establish. Read before proposing a setup; several are hard vetoes.

## Non-negotiables

- **Never invent a price.** Every number in a signal traces back to fetched OHLC
  or a live broker call. `vv/verify_signal.py` enforces this — run it.
- **`vv/signals_log.jsonl` is the cross-chat trade memory.** Read past trades
  with the tools, never reconstruct them from conversation history.
- **Never record a fill you did not witness.** A quote is not a fill; only the
  user's word or a broker document establishes one (LEARNINGS §16).
- **Never condition on a fact that does not exist at the entry timestamp**
  (LEARNINGS §14). Quote entry prices with the clock time they were read (§15).
- **Options price off the parity-implied forward, not spot** (LEARNINGS §3).
  Mark modelled premiums with `~`.

## Environment

- **Python 3, standard library only.** There is no pip on the development
  machine — no new dependencies, ever.
- Credentials live at `~/.claude/broker_creds.json`, outside the repo, and are
  never pasted into chat. Setup and the daily token refresh are in `README.md`.
- Public OHLC works with no credentials; the live option chain needs a token.
