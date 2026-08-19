#!/usr/bin/env python3
"""Builds trading_journal.xlsx — an ICT trading journal for NSE and crypto.

Sheets: Dashboard, NSE Journal, Crypto Journal, Options Detail, Setup Checklist,
Reference. Formulas are live, so P&L, R-multiples and win rates compute
themselves as rows are filled in. Options P&L flows from Options Detail into the
NSE Journal automatically, keyed on Trade ID.
"""
import datetime
import json
import pathlib

from xlsx import (Sheet, write, date_serial, S_HEADER, S_DATE, S_NUM, S_INR,
                  S_USD, S_PCT, S_CALC, S_TITLE, S_WRAP, S_INPUT, S_SUBHDR)

LAST = 400          # rows of formula/validation runway
H = lambda t: ("s", t, S_HEADER)
T = lambda t: ("s", t, S_TITLE)
K = lambda t: ("s", t, S_SUBHDR)


def hdr(sheet, names, widths):
    sheet.add([H(n) for n in names])
    sheet.widths = dict(enumerate(widths))


# --------------------------------------------------------------- NSE Journal
nse = Sheet("NSE Journal")
NSE_COLS = [
    ("Trade ID", 9), ("Date Logged", 12), ("Symbol", 14), ("Instrument", 12),
    ("Direction", 10), ("Setup Type", 15), ("HTF Bias", 10), ("Entry Zone Low", 13),
    ("Entry Zone High", 13), ("Planned Entry", 12), ("Stop Loss", 11), ("TP1", 11),
    ("TP2", 11), ("Planned R:R", 11), ("Invalidation", 26), ("Confidence", 11),
    ("Entry Filled?", 12), ("Actual Entry", 12), ("Entry Date", 12), ("Exit Price", 11),
    ("Exit Date", 12), ("Outcome", 13), ("Qty / Lots", 10), ("Risk per Unit", 12),
    ("Total Risk (Rs)", 13), ("Gross P&L (Rs)", 13), ("Fees (Rs)", 10),
    ("Net P&L (Rs)", 13), ("R Multiple", 10), ("Days Held", 10),
    ("Followed Plan?", 12), ("Mistakes / Lessons", 34), ("Notes / Chart Link", 34),
]
hdr(nse, [c[0] for c in NSE_COLS], [c[1] for c in NSE_COLS])


def nse_row(r, tid, date, sym, instr, direction, setup, bias, ez_lo, ez_hi,
            entry, stop, tp1, tp2, inval, conf, filled, notes):
    return [
        ("s", tid), ("d", date_serial(date), S_DATE), ("s", sym), ("s", instr),
        ("s", direction), ("s", setup), ("s", bias),
        ("n", ez_lo, S_NUM), ("n", ez_hi, S_NUM), ("n", entry, S_NUM),
        ("n", stop, S_NUM), ("n", tp1, S_NUM), ("n", tp2, S_NUM),
        ("f", f'=IFERROR(ABS(L{r}-J{r})/ABS(J{r}-K{r}),"")', S_CALC),
        ("s", inval, S_WRAP), ("s", conf), ("s", filled),
        None, None, None, None, None, None,
        ("f", f'=IFERROR(ABS(R{r}-K{r}),"")', S_CALC),
        ("f", f'=IFERROR(IF(D{r}="Equity",ABS(R{r}-K{r})*W{r},'
              f"SUMIF('Options Detail'!A:A,A{r},'Options Detail'!S:S)),\"\")", S_CALC),
        ("f", f'=IFERROR(IF(D{r}="Equity",IF(E{r}="Long",(T{r}-R{r}),(R{r}-T{r}))*W{r},'
              f"SUMIF('Options Detail'!A:A,A{r},'Options Detail'!R:R)),\"\")", S_CALC),
        None,
        ("f", f'=IFERROR(Z{r}-AA{r},"")', S_CALC),
        ("f", f'=IFERROR(AB{r}/Y{r},"")', S_CALC),
        ("f", f'=IFERROR(U{r}-S{r},"")', S_CALC),
        None, None, ("s", notes, S_WRAP),
    ]


def nse_blank(r):
    cells = [None] * len(NSE_COLS)
    cells[13] = ("f", f'=IFERROR(ABS(L{r}-J{r})/ABS(J{r}-K{r}),"")', S_CALC)
    cells[23] = ("f", f'=IFERROR(ABS(R{r}-K{r}),"")', S_CALC)
    cells[24] = ("f", f'=IFERROR(IF(D{r}="Equity",ABS(R{r}-K{r})*W{r},'
                      f"SUMIF('Options Detail'!A:A,A{r},'Options Detail'!S:S)),\"\")", S_CALC)
    cells[25] = ("f", f'=IFERROR(IF(D{r}="Equity",IF(E{r}="Long",(T{r}-R{r}),(R{r}-T{r}))*W{r},'
                      f"SUMIF('Options Detail'!A:A,A{r},'Options Detail'!R:R)),\"\")", S_CALC)
    cells[27] = ("f", f'=IFERROR(Z{r}-AA{r},"")', S_CALC)
    cells[28] = ("f", f'=IFERROR(AB{r}/Y{r},"")', S_CALC)
    cells[29] = ("f", f'=IFERROR(U{r}-S{r},"")', S_CALC)
    return cells


def col_index(letter):
    n = 0
    for ch in letter:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def merge_row(blank, d):
    """Overlay a synced row (column letter -> value) onto a blank template row.

    Formula cells are left alone: the template owns the arithmetic, the sync
    owns the facts. Cell type is inferred from the value, so a date lands as a
    date and a number stays numeric rather than becoming text.
    """
    cells = list(blank)
    for letter, val in d.items():
        if letter.startswith("_") or val is None or val == "":
            continue
        i = col_index(letter)
        if i >= len(cells):
            continue
        cur = cells[i]
        if isinstance(cur, tuple) and cur[0] == "f":
            continue
        if isinstance(val, datetime.date):
            cells[i] = ("d", date_serial(val), S_DATE)
        elif isinstance(val, (int, float)) and not isinstance(val, bool):
            cells[i] = ("n", val, S_NUM)
        elif len(str(val)) > 40:
            cells[i] = ("s", str(val), S_WRAP)
        else:
            cells[i] = ("s", str(val))
    return cells


import journal_sync  # noqa: E402  -- defined above so build_rows can be merged in

SYNC_NSE, SYNC_CRY, _SYNC_STORE = journal_sync.build_rows()

for i, d in enumerate(SYNC_NSE):
    nse.add(merge_row(nse_blank(2 + i), d))
for r in range(2 + len(SYNC_NSE), LAST + 1):
    nse.add(nse_blank(r))

nse.validate(f"D2:D{LAST}", ["Equity", "Option-CE", "Option-PE"])
nse.validate(f"E2:E{LAST}", ["Long", "Short"])
nse.validate(f"F2:F{LAST}", ["Sweep + OB", "Sweep + FVG", "CHoCH + OTE", "BOS Retest",
                              "Breaker", "Turtle Soup", "Other"])
nse.validate(f"G2:G{LAST}", ["Bullish", "Bearish", "Ranging"])
nse.validate(f"P2:P{LAST}", ["Low", "Medium", "High"])
nse.validate(f"Q2:Q{LAST}", ["Yes", "No", "Partial"])
nse.validate(f"V2:V{LAST}", ["Win", "Loss", "Never Filled", "Timeout", "Breakeven", "Manual Exit"])
nse.validate(f"AE2:AE{LAST}", ["Yes", "No", "Partial"])
nse.highlight(f"V2:V{LAST}", "Win", 0)
nse.highlight(f"V2:V{LAST}", "Loss", 1)
nse.highlight(f"V2:V{LAST}", "Never Filled", 2)

# ------------------------------------------------------------ Crypto Journal
cry = Sheet("Crypto Journal")
CRY_COLS = [
    ("Trade ID", 9), ("Date Logged", 12), ("Pair", 14), ("Exchange", 12), ("Type", 9),
    ("Direction", 10), ("Setup Type", 15), ("HTF Bias", 10), ("Entry Zone Low", 13),
    ("Entry Zone High", 13), ("Planned Entry", 12), ("Stop Loss", 11), ("TP1", 11),
    ("TP2", 11), ("Planned R:R", 11), ("Confidence", 11), ("Entry Filled?", 12),
    ("Actual Entry", 12), ("Entry Date", 12), ("Exit Price", 11), ("Exit Date", 12),
    ("Outcome", 13), ("Position Size", 12), ("Total Risk ($)", 13), ("Gross P&L ($)", 13),
    ("Fees + Funding ($)", 14), ("Net P&L ($)", 13), ("R Multiple", 10), ("Days Held", 10),
    ("Followed Plan?", 12), ("Mistakes / Lessons", 34), ("Notes", 34),
]
hdr(cry, [c[0] for c in CRY_COLS], [c[1] for c in CRY_COLS])
def cry_blank(r):
    return ([None] * 14 +
            [("f", f'=IFERROR(ABS(M{r}-K{r})/ABS(K{r}-L{r}),"")', S_CALC)] +
            [None] * 8 +
            [("f", f'=IFERROR(ABS(R{r}-L{r})*W{r},"")', S_CALC),
             ("f", f'=IFERROR(IF(F{r}="Long",(T{r}-R{r}),(R{r}-T{r}))*W{r},"")', S_CALC),
             None,
             ("f", f'=IFERROR(Y{r}-Z{r},"")', S_CALC),
             ("f", f'=IFERROR(AA{r}/X{r},"")', S_CALC),
             ("f", f'=IFERROR(U{r}-S{r},"")', S_CALC),
             None, None, None])


for i, d in enumerate(SYNC_CRY):
    cry.add(merge_row(cry_blank(2 + i), d))
for r in range(2 + len(SYNC_CRY), LAST + 1):
    cry.add(cry_blank(r))
cry.validate(f"E2:E{LAST}", ["Spot", "Perp"])
cry.validate(f"F2:F{LAST}", ["Long", "Short"])
cry.validate(f"G2:G{LAST}", ["Sweep + OB", "Sweep + FVG", "CHoCH + OTE", "BOS Retest",
                              "Breaker", "Turtle Soup", "Other"])
cry.validate(f"H2:H{LAST}", ["Bullish", "Bearish", "Ranging"])
cry.validate(f"P2:P{LAST}", ["Low", "Medium", "High"])
cry.validate(f"Q2:Q{LAST}", ["Yes", "No", "Partial"])
cry.validate(f"V2:V{LAST}", ["Win", "Loss", "Never Filled", "Timeout", "Breakeven", "Manual Exit"])
cry.validate(f"AD2:AD{LAST}", ["Yes", "No", "Partial"])
cry.highlight(f"V2:V{LAST}", "Win", 0)
cry.highlight(f"V2:V{LAST}", "Loss", 1)
cry.highlight(f"V2:V{LAST}", "Never Filled", 2)

# ----------------------------------------------------------- Options Detail
opt = Sheet("Options Detail")
OPT_COLS = [
    ("Trade ID", 9), ("Underlying", 14), ("Strike", 10), ("CE/PE", 8), ("Expiry", 12),
    ("Lot Size", 9), ("Lots", 7), ("Entry Date", 12), ("DTE at Entry", 11),
    ("IV at Entry %", 12), ("Delta at Entry", 12), ("Premium Entry", 12),
    ("Premium Stop (plan)", 15), ("Premium Target (plan)", 16), ("Option R:R", 11),
    ("Premium Exit (actual)", 16), ("Capital Deployed (Rs)", 16), ("Gross P&L (Rs)", 13),
    ("Planned Risk (Rs)", 14), ("Notes", 40),
]
hdr(opt, [c[0] for c in OPT_COLS], [c[1] for c in OPT_COLS])



def _load_option_legs():
    """Option legs recorded in journal_annotations.json under "options".

    Kept out of journal_sync's harvest loop: that reads the workbook back and
    would round-trip a modelled premium into looking like a quoted one.
    """
    p = pathlib.Path(__file__).parent / "journal_annotations.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text()).get("options", []) or []
    except (ValueError, OSError):
        return []


# One Options Detail row per leg, in the order the legs are listed, starting at
# row 2. A trade with two legs simply lists two records.
_OPT_ROW_FOR = {rec.get("trade_id"): 2 + i
                for i, rec in enumerate(_load_option_legs())
                if rec.get("trade_id")}


def opt_formulas(r):
    return {
        8:  ("f", f'=IFERROR(E{r}-H{r},"")', S_CALC),
        14: ("f", f'=IFERROR((N{r}-L{r})/(L{r}-M{r}),"")', S_CALC),
        16: ("f", f'=IFERROR(L{r}*F{r}*G{r},"")', S_CALC),
        17: ("f", f'=IFERROR((P{r}-L{r})*F{r}*G{r},"")', S_CALC),
        18: ("f", f'=IFERROR((L{r}-M{r})*F{r}*G{r},"")', S_CALC),
    }


# The log carries no premium, IV or greek data, so nothing here is derived from
# it -- inventing those would put unquoted numbers in front of a P&L formula.
# Rows are seeded only from the "options" list in journal_annotations.json, whose
# every field traces to a live upstox.option_chain() quote or to a Black-76
# projection off the parity-implied forward (the record itself says which).
# Anything not listed there stays a blank hand-entry row, as before.
_opt_seed = {}
for _rec in _load_option_legs():
    _tid = _rec.get("trade_id")
    _row = _OPT_ROW_FOR.get(_tid)
    if _row:
        _opt_seed[_row] = _rec

for r in range(2, LAST + 1):
    cells = [None] * 20
    for i, f in opt_formulas(r).items():
        cells[i] = f
    rec = _opt_seed.get(r)
    if rec:
        for idx, key, style in (
            (0, "trade_id", None), (1, "underlying", None), (2, "strike", S_NUM),
            (3, "kind", None), (5, "lot_size", S_NUM), (6, "lots", S_NUM),
            (9, "iv_at_entry", S_NUM), (10, "delta_at_entry", S_NUM),
            (11, "premium_entry", S_NUM), (12, "premium_stop", S_NUM),
            (13, "premium_target", S_NUM), (15, "premium_exit", S_NUM),
            (19, "notes", S_WRAP),
        ):
            val = rec.get(key)
            if val is None:
                continue
            kind = "n" if isinstance(val, (int, float)) and not isinstance(val, bool) else "s"
            cells[idx] = (kind, val, style) if style else (kind, val)
        # E and H must be real dates: column I is =E-H, the DTE at entry.
        for idx, key in ((4, "expiry"), (7, "entry_date")):
            val = rec.get(key)
            if val:
                y, m, dd = (int(x) for x in str(val).split("-"))
                cells[idx] = ("d", date_serial(datetime.date(y, m, dd)), S_DATE)
    opt.add(cells)
opt.validate(f"D2:D{LAST}", ["CE", "PE"])

# ---------------------------------------------------------- Setup Checklist
chk = Sheet("Setup Checklist")
CHK_COLS = [
    ("Trade ID", 9), ("Liquidity Sweep?", 14), ("Sweep Fresh (<=1 bar)?", 16),
    ("BOS / CHoCH?", 12), ("Valid PD Array?", 13), ("Correct Prem/Disc Side?", 17),
    ("HTF & LTF Aligned?", 15), ("R:R >= 1:2?", 11), ("Stop Structural?", 14),
    ("Score /8", 9), ("Core 3 Met?", 11), ("Notes", 44),
]
hdr(chk, [c[0] for c in CHK_COLS], [c[1] for c in CHK_COLS])


def chk_row(r, tid=None, vals=None, note=None):
    vals = vals or [None] * 8
    return ([("s", tid)] + [("s", v) if v else None for v in vals] +
            [("f", f'=IF(A{r}="","",COUNTIF(B{r}:I{r},"Yes"))', S_CALC),
             ("f", f'=IF(A{r}="","",IF(AND(B{r}="Yes",D{r}="Yes",E{r}="Yes"),"YES","NO"))', S_CALC),
             ("s", note, S_WRAP) if note else None])


# Scored by hand at the time of taking the trade — the point of the checklist is
# that a human ticks it, so seeding it from the signal would defeat it.
for r in range(2, LAST + 1):
    chk.add(chk_row(r))
for c in "BCDEFGHI":
    chk.validate(f"{c}2:{c}{LAST}", ["Yes", "No"])
chk.highlight(f"K2:K{LAST}", "YES", 0)
chk.highlight(f"K2:K{LAST}", "NO", 1)

# ----------------------------------------------------------------- Dashboard
dash = Sheet("Dashboard", freeze=None)
dash.widths = {0: 30, 1: 16, 2: 4, 3: 30, 4: 16}


def stats_block(title, sheet, outcome_col, pnl_col, r_col, money_style):
    q = f"'{sheet}'!"
    o, p, rm = f"{q}{outcome_col}2:{outcome_col}{LAST}", f"{q}{pnl_col}2:{pnl_col}{LAST}", f"{q}{r_col}2:{r_col}{LAST}"
    return [
        (K(title), None),
        ("Trades logged", ("f", f'=COUNTA({q}A2:A{LAST})', S_CALC)),
        ("Wins", ("f", f'=COUNTIF({o},"Win")', S_CALC)),
        ("Losses", ("f", f'=COUNTIF({o},"Loss")', S_CALC)),
        ("Never filled (no trade taken)", ("f", f'=COUNTIF({o},"Never Filled")', S_CALC)),
        ("Win rate (decisive only)",
         ("f", f'=IFERROR(COUNTIF({o},"Win")/(COUNTIF({o},"Win")+COUNTIF({o},"Loss")),"")', S_PCT)),
        ("Net P&L", ("f", f'=SUM({p})', money_style)),
        ("Average R multiple", ("f", f'=IFERROR(AVERAGE({rm}),"")', S_CALC)),
        ("Best trade (R)", ("f", f'=IFERROR(MAX({rm}),"")', S_CALC)),
        ("Worst trade (R)", ("f", f'=IFERROR(MIN({rm}),"")', S_CALC)),
        ("Profit factor",
         ("f", f'=IFERROR(SUMIF({p},">0")/ABS(SUMIF({p},"<0")),"")', S_CALC)),
        ("Plan adherence rate", None),
    ]


left = stats_block("NSE / INDIAN MARKET", "NSE Journal", "V", "AB", "AC", S_INR)
right = stats_block("CRYPTO", "Crypto Journal", "V", "AA", "AB", S_USD)
left[11] = ("Plan adherence rate",
            ("f", f'=IFERROR(COUNTIF(\'NSE Journal\'!AE2:AE{LAST},"Yes")/'
                  f'COUNTA(\'NSE Journal\'!AE2:AE{LAST}),"")', S_PCT))
right[11] = ("Plan adherence rate",
             ("f", f'=IFERROR(COUNTIF(\'Crypto Journal\'!AD2:AD{LAST},"Yes")/'
                   f'COUNTA(\'Crypto Journal\'!AD2:AD{LAST}),"")', S_PCT))

dash.add([T("ICT TRADING JOURNAL — DASHBOARD")])
dash.add([("s", "All figures update automatically as you fill the journal sheets.", S_WRAP)])
dash.add([])
for l, r in zip(left, right):
    lab_l, val_l = l
    lab_r, val_r = r
    dash.add([lab_l if isinstance(lab_l, tuple) else ("s", lab_l), val_l, None,
              lab_r if isinstance(lab_r, tuple) else ("s", lab_r), val_r])

dash.add([])
dash.add([K("SETUP QUALITY")])
dash.add([("s", "Avg confluence score /8"),
          ("f", f'=IFERROR(AVERAGE(\'Setup Checklist\'!J2:J{LAST}),"")', S_CALC)])
dash.add([("s", "Trades meeting core 3"),
          ("f", f'=COUNTIF(\'Setup Checklist\'!K2:K{LAST},"YES")', S_CALC)])
dash.add([("s", "Trades missing core 3"),
          ("f", f'=COUNTIF(\'Setup Checklist\'!K2:K{LAST},"NO")', S_CALC)])
dash.add([])
dash.add([K("BY SETUP TYPE (NSE)")])
dash.add([H("Setup"), H("Trades"), None, H("Wins"), H("Win %")])
FIRST_SETUP_ROW = 24        # must match the row the loop below actually writes to
for i, s in enumerate(["Sweep + OB", "Sweep + FVG", "CHoCH + OTE", "BOS Retest",
                        "Breaker", "Turtle Soup", "Other"]):
    r = FIRST_SETUP_ROW + i
    dash.add([("s", s),
              ("f", f'=COUNTIF(\'NSE Journal\'!F2:F{LAST},A{r})', S_CALC), None,
              ("f", f'=COUNTIFS(\'NSE Journal\'!F2:F{LAST},A{r},\'NSE Journal\'!V2:V{LAST},"Win")', S_CALC),
              ("f", f'=IFERROR(D{r}/(D{r}+COUNTIFS(\'NSE Journal\'!F2:F{LAST},A{r},'
                    f'\'NSE Journal\'!V2:V{LAST},"Loss")),"")', S_PCT)])

# ----------------------------------------------------------------- Reference
ref = Sheet("Reference", freeze=None)
ref.widths = {0: 34, 1: 76}
ref.add([T("REFERENCE — RULES & CONVENTIONS")])
ref.add([])
ref.add([K("Discipline rules (from the bot prompt)"), None])
for k, v in [
    ("Confluence minimum",
     "Need all three before BUY/SELL: (a) HTF bias, (b) liquidity sweep or clear draw on liquidity, "
     "(c) a valid PD array (OB / FVG / breaker). One element alone is not a setup."),
    ("Risk:Reward floor", "Do not take below 1:2. Below that, log it as low quality rather than a trade."),
    ("Stop placement", "Beyond a swing point or order block. Never an arbitrary percentage."),
    ("No chasing", "If price has already swept and run from the PD array, wait for the pullback."),
    ("No forcing", "Mid-range or unclear structure = NO TRADE. Record what confirmation is missing."),
    ("Options R:R", "Check R:R on the OPTION, not just the underlying. They diverge badly on "
                     "small-percentage targets and OTM strikes."),
    ("Strike liquidity", "Select by open interest and bid/ask spread, not proximity to spot. "
                          "An illiquid strike loses money on a correct call."),
    ("Never filled", "A pending entry that never triggered is NOT a loss and NOT a win. "
                      "Log it as 'Never Filled' and exclude it from win rate."),
]:
    ref.add([("s", k, S_SUBHDR), ("s", v, S_WRAP)])

ref.add([])
ref.add([K("Column conventions"), None])
for k, v in [
    ("Entry/Stop/TP columns", "Always UNDERLYING price levels — even for option trades. "
                               "Option premiums live on the Options Detail sheet."),
    ("Options P&L", "Enter premiums in Options Detail; the NSE Journal pulls P&L and risk "
                     "automatically via Trade ID. Do not type P&L by hand for option rows."),
    ("Instrument = Equity", "Only then does the journal compute P&L from the price columns."),
    ("Outcome", "Win / Loss / Never Filled / Timeout / Breakeven / Manual Exit. "
                 "Colour-codes itself."),
    ("R Multiple", "Net P&L divided by Total Risk. The single most useful number here — "
                    "track it rather than rupees."),
]:
    ref.add([("s", k, S_SUBHDR), ("s", v, S_WRAP)])

ref.add([])
ref.add([K("Position sizing calculator"), None])
ref.add([("s", "Account size", S_SUBHDR), ("n", 0, S_INPUT)])
ref.add([("s", "Risk per trade %", S_SUBHDR), ("n", 0.01, S_PCT)])
ref.add([("s", "Entry price", S_SUBHDR), ("n", 0, S_INPUT)])
ref.add([("s", "Stop price", S_SUBHDR), ("n", 0, S_INPUT)])
ref.add([("s", "Risk amount", S_SUBHDR), ("f", "=B24*B25", S_CALC)])
ref.add([("s", "Risk per unit", S_SUBHDR), ("f", "=ABS(B26-B27)", S_CALC)])
ref.add([("s", "Position size (units)", S_SUBHDR), ("f", '=IFERROR(B28/B29,"")', S_CALC)])
ref.add([("s", "Yellow cells are inputs.", S_WRAP)])

write("trading_journal.xlsx",
      [dash, nse, cry, opt, chk, ref])
print("wrote trading_journal.xlsx")
