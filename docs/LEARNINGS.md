# Accumulated learnings

Findings that cost real money or real backtests to establish. They live here, in
the repo, so a fresh clone carries them. Dates are when the finding was made.

---

## 1. The mechanical ICT layer has no standalone edge

Backtested 2026-08-16. Mechanical conditions only (unbroken sweep + BOS/CHoCH +
unfilled FVG + premium/discount extreme), deterministic FVG-midpoint entry,
impulse-extreme stop, impulse-opposite target, 0.1% round-trip cost, R:R >= 2:

| run | trades | hit-rate | expectancy | equity @1% risk |
|---|---|---|---|---|
| 173 NSE F&O names, 10y daily | 841 | 22.8% | −0.028R | **−30.8%** |
| 173 NSE F&O names, 2y hourly | 685 | 21.9% | −0.086R | **−48.8%** |
| NIFTY + BANKNIFTY, 10y daily | 10 | 20.0% | +0.133R | +1.2% (sample too small) |

NIFTY buy-and-hold over the same 10 years: **+181.9%**.

**This does not prove the discretionary strategy loses.** The judgement layer —
narrative, chosen entry, structural stop, confidence — is absent from the replay,
and `screen.py` says outright it does NOT produce trade signals. What it does
establish: any real edge lives entirely in the analyst's judgement, and that
remains unmeasured. Never imply a mechanical edge exists.

---

## 2. OTE limit entries do not fill — this is the real leak

Measured by `python3 vv/review.py` on 2026-08-17:

```
EXECUTION  fill rate 1/8 (12%)   median entry distance 2.61% of spot
ANALYSIS   entered at market instead: 2/3 decided reached TP1 first
```

The bias layer works; the **order-placement layer** is what loses. HINDCOPPER and
SHREECEM would both have hit target from a market entry, missing by 2.46% and
2.54%. The one signal that filled (ALKEM, 0.85% away) lost.

The 2/3 figure is **not** evidence of edge — 3 decided trades is noise. The 12%
fill rate is the finding.

**Apply:** enter at the *first* PD array price reaches, not the deepest one.
Measure the cost of the entry with `vv/costs.py` before committing. Quote the
current fill rate when placing entries, and let it set how close they go.

---

## 3. NSE options price off the forward, not spot

`vv/bs.py` fed the cash spot misprices NSE stock options badly — ALKEM 5350 PE
(2026-08-14) returned 90.19 against a market mid of 101.83, an **11.4% error**.
NSE stock options are European and price off the **futures forward**.

Back the forward out of put-call parity at the same strike, then price Black-76:

```
F = K + (CE_mid - PE_mid) * exp(r*T)
bs.price(F, K, T, iv, kind, r=0) * exp(-r*T)
```

On that chain F = 5355.31 vs spot 5368.00 (0.24% backwardation); repricing off F
closed the error to **0.9%**. When projecting forward, decay the basis to zero at
expiry: `F(t) = S(t) + basis0 * T/T0`.

**Second, independent defect** (2026-08-18): `option_greeks.iv` refreshes on a
different cadence than `bid_price`/`ask_price`, so pairing published IV with a
live mid misprices — NIFTY 24300 PE overpriced by **+1.35%**, and **+3.8%** on an
earlier poll. How to tell the two apart: a clean parity forward (consistent to
within ~1 point across strikes) plus a *uniform* error across all strikes means
the IV is stale, not the forward.

---

## 4. Displacement trades need an impulse exit

When the premise is **displacement continuation** — selling/buying the retrace
into an FVG left by an impulsive leg — the position is a momentum trade with a
short shelf life. The plan must carry a **third exit** alongside stop and target:

> **Impulse exhaustion:** the first confirmed LTF higher low (for a short) /
> lower high (for a long), or no new extreme within ~20 minutes. Whichever comes
> first with stop and target.

A session-close time stop ("flat by 15:15") is an end-of-day backstop and does
**not** cover this. Do not present it as if it does.

**When impulse exhaustion is the PRIMARY exit:** whenever the actual entry basis
makes TP1 pay less than 2:1 **on the option leg**. Underlying R:R is not the test
— on 2026-08-19 a 2.90-point worse fill took option R:R to TP1 from 2.45 to
**1.22** while underlying R:R was unchanged at 2.15.

**Why this exists:** T011 (^NSEI short, 24100 PE) peaked at **+Rs 679** four
minutes after the impulse low, then round-tripped to **−Rs 166** over 45 minutes
of chop. The plan had no rule that would have taken it off.

---

## 5. Recurring failure modes already guarded

- **`cost_dominated_risk`** — the earliest signals all died with stops too tight
  against fees. Guarded by `verify_signal.py` check `14-cost-vs-risk`.
- **No order block within reach of the FVG** — an honest stop then drops R:R
  below threshold (ASTRAL 2026-08-16 failed `9-stop-structure` at R:R 1.32).
  Rejected setups are *not* captured in `signals_log.jsonl`; state the failing
  check in the reply.
- **Expiry-day pins** — NIFTY weeklies expire Tuesday. On expiry, pull the 0DTE
  chain and compute max pain and OI walls. On 2026-08-18 max pain 24250 vs spot
  24228, 41.3m calls at 24300 and 38.4m puts at 24200 — that pin *was* the
  61-point range.
- **Targets beyond the straddle** — the 0DTE ATM straddle is the market's own
  price for the entire remaining move. On 2026-08-18 it was 69.35 points while
  the only short target clearing 1:2 was 92 points away (1.33x). **A target
  beyond the straddle is not a today trade, whatever its R:R says.** This is the
  cleanest NO_TRADE argument available, and it is arithmetic, not opinion.
