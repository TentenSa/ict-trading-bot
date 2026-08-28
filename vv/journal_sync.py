#!/usr/bin/env python3
"""Feeds signals_log.jsonl into trading_journal.xlsx without destroying hand-typed work.

The log knows what the bot decided and how it resolved. The journal also holds
things only a human can supply -- which instrument was actually used, position
size, fees, what went wrong. Those must survive a rebuild, so this module owns
the split:

  MACHINE columns are rewritten from the log on every sync.
  MANUAL  columns are read back out of the existing workbook first, cached in
          journal_annotations.json, and written straight back.

Trade IDs are assigned once and kept stable, because 'Options Detail' and
'Setup Checklist' join to the NSE Journal on Trade ID. Annotations are keyed on
the trade's identity (ticker|as_of) rather than on its ID, so --renumber can
reissue labels without notes following the wrong trade -- but any hand-written
rows on those other two sheets still have to be repointed by hand.

Usage:
    python3 journal_sync.py                    # harvest, merge, rebuild
    python3 journal_sync.py --dry-run          # show changes, touch nothing
    python3 journal_sync.py --forget T007      # drop a trade's manual values
    python3 journal_sync.py --forget T007:AG   # drop one manual cell
    python3 journal_sync.py --renumber         # reissue Trade IDs from T001
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
LOG_PATH = HERE / "signals_log.jsonl"
ANNOT_PATH = HERE / "journal_annotations.json"
BOOK_PATH = HERE / "trading_journal.xlsx"
_LEGACY_ID = re.compile(r"^T\d+$")

# Columns a human owns. Harvested from the workbook and written back untouched.
NSE_MANUAL = ["D", "F", "T", "U", "W", "AA", "AE", "AF", "AG"]
CRY_MANUAL = ["D", "E", "G", "T", "U", "W", "Z", "AD", "AE", "AF"]

SEED_IDS = {}

OUTCOME_LABEL = {
    "win": "Win", "loss": "Loss", "never_filled": "Never Filled",
    "unresolved_timeout": "Timeout",
}

# The journal is a record of TRADES, not of every signal the bot emitted. A
# NO_TRADE's hypothetical block exists so the backtester can score whether
# staying out was right -- it was never a position, and listing it here makes
# the journal read like a book of trades that were taken. Same for entries
# quarantined out of the sample. Both stay in signals_log.jsonl, which is the
# complete record; this is the filtered view.
REAL_TRADES_ONLY = True


def is_real_trade(entry):
    return (entry.get("signal") in ("BUY", "SELL")
            and not entry.get("is_hypothetical")
            and entry.get("status") != "unresolvable")


def _key(entry):
    return f"{entry['ticker']}|{entry['as_of']}"


def read_log():
    if not LOG_PATH.exists():
        return []
    return [json.loads(l) for l in LOG_PATH.read_text().splitlines() if l.strip()]


def load_store():
    if ANNOT_PATH.exists():
        s = json.loads(ANNOT_PATH.read_text())
    else:
        s = {}
    s.setdefault("ids", dict(SEED_IDS))
    s.setdefault("manual", {})
    for k, v in SEED_IDS.items():
        s["ids"].setdefault(k, v)

    # Annotations are keyed on the trade's identity (ticker|as_of), NOT on its
    # Trade ID. Keying on the ID meant a renumber silently handed one trade's
    # notes to another. Migrate any legacy ID-keyed entries across.
    by_id = {tid: k for k, tid in s["ids"].items()}
    for key in [k for k in s["manual"] if _LEGACY_ID.match(k)]:
        nat = by_id.get(key)
        vals = s["manual"].pop(key)
        if nat:
            s["manual"].setdefault(nat, {}).update(vals)
    return s


def save_store(store):
    ANNOT_PATH.write_text(json.dumps(store, indent=2, sort_keys=True) + "\n")


# Text this module writes into the notes column itself. Kept in one place so
# harvest() and the writer cannot drift apart.
MACHINE_FLAG_PREFIXES = (
    "Non-NSE symbol (",
    "NO_TRADE signal —",
    "EXCLUDED from sample (",
)


def _is_machine_flag(val):
    return isinstance(val, str) and val.lstrip().startswith(MACHINE_FLAG_PREFIXES)


def harvest(store, forget=()):
    """Pull human-entered cells out of the current workbook into the store.

    The workbook is the thing the user actually types into, so it wins over the
    cached copy. Absent/blank cells never overwrite a cached value -- a rebuild
    that dropped a column must not erase what was there before.

    That safety has a consequence: clearing a cell in Excel does not clear it
    here, because a blank is indistinguishable from a column this sync never
    wrote. `forget` is the explicit escape hatch -- ("T007",) drops every manual
    value for a trade, ("T007:AG",) drops one cell -- skipped on harvest so the
    workbook cannot immediately re-seed what was just dropped.
    """
    if not BOOK_PATH.exists():
        return store, 0
    from xlsx import read_sheet

    drop_all = {f.split(":")[0] for f in forget if ":" not in f}
    drop_cell = {tuple(f.split(":", 1)) for f in forget if ":" in f}
    by_id = {tid: k for k, tid in store["ids"].items()}

    picked = 0
    for sheet, manual in (("NSE Journal", NSE_MANUAL), ("Crypto Journal", CRY_MANUAL)):
        try:
            rows = read_sheet(BOOK_PATH, sheet)
        except KeyError:
            continue
        for r, cells in rows.items():
            if r < 2:
                continue
            tid = cells.get("A")
            if not isinstance(tid, str) or not tid.startswith("T"):
                continue
            if tid in drop_all:
                continue
            nat = by_id.get(tid)
            if nat is None:      # a row whose ID we no longer track
                continue
            slot = store["manual"].setdefault(nat, {})
            for col in manual:
                if (tid, col) in drop_cell:
                    continue
                val = cells.get(col)
                if val in (None, ""):
                    continue
                if _is_machine_flag(val):
                    # The sync itself parks a flag in the notes column when the
                    # cell is empty. Harvesting it back would launder machine
                    # output into a "human" note, and because the workbook wins
                    # over the cached store it would then overwrite any real
                    # annotation on the next run. Leave it to be regenerated.
                    continue
                slot[col] = val
                picked += 1
    return store, picked


def _signals_by_key():
    """Signal JSONs carry fields the log does not keep -- HTF bias, the OTE
    bounds, TP2, the invalidation text.

    Indexed by (ticker, as_of), NOT by ticker. Keying on ticker alone meant the
    newest signal for a symbol won, and every older row for that symbol then
    inherited its bias, OTE bounds, TP2 and invalidation text. On 2026-08-25
    that put one signal's "Bearish daily sequence, bullish one-night skew", its
    24190.40-24230.70 zone and its "There is NO overnight stop" invalidation
    onto all five NIFTY rows of the day, four of which were different trades
    with different premises. A row now takes its own signal's fields or none:
    resolved signal files get deleted once logged, so a row whose file is gone
    leaves these columns BLANK, which is correct where the old behaviour was
    confidently wrong.
    """
    out = {}
    for p in sorted(HERE.glob("*.json")):
        if p.name == ANNOT_PATH.name:
            continue
        try:
            d = json.loads(p.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(d, dict) or "ticker" not in d or "signal" not in d:
            continue
        out[(d["ticker"], str(d.get("as_of", "")))] = d
    return out


def _date(iso):
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).date()
    except ValueError:
        return None


# Set by main() before make_journal is imported, so the rebuild reuses the rows
# that were just computed instead of deriving its own. Without this, a --forget
# would be undone immediately: make_journal's own build_rows() call would
# re-harvest the dropped values straight back out of the old workbook.
_PREBUILT = None
LAST_SKIPPED = []       # entries the REAL_TRADES_ONLY filter held back


def build_rows(forget=(), renumber=False):
    """(nse_rows, crypto_rows, store). Each row is {column_letter: value}, with
    MACHINE columns from the log/signal and MANUAL columns from the store."""
    if _PREBUILT is not None:
        return _PREBUILT
    store = load_store()
    if renumber:
        # Safe only because annotations are keyed on ticker|as_of: the IDs are
        # just labels, so reissuing them cannot move notes between trades.
        store["ids"] = {}
    by_id = {tid: k for k, tid in store["ids"].items()}
    for f in forget:
        tid, _, col = f.partition(":")
        nat = by_id.get(tid, tid)
        if col:
            store["manual"].get(nat, {}).pop(col, None)
        else:
            store["manual"].pop(nat, None)
    store, _ = harvest(store, forget)
    log = read_log()
    sigs = _signals_by_key()

    used = set(store["ids"].values())
    nse_rows, cry_rows = [], []
    skipped = []

    for entry in log:
        if REAL_TRADES_ONLY and not is_real_trade(entry):
            skipped.append(entry)
            continue
        k = _key(entry)
        tid = store["ids"].get(k)
        if tid is None:
            n = 1
            while f"T{n:03d}" in used:
                n += 1
            tid = f"T{n:03d}"
            store["ids"][k] = tid
        used.add(tid)

        # Prefer the snapshot persisted INTO the log at signal time; fall back to
        # the on-disk signal file only when a row predates the snapshot field.
        # The file is the fragile source - resolved ones are deleted - so the log
        # wins wherever it has the data.
        snap = entry.get("snapshot") or {}
        sig = dict(sigs.get((entry["ticker"], str(entry.get("as_of", ""))), {}))
        for k, v in snap.items():
            if v not in (None, "", [], {}):
                sig[k] = v
        zones = sig.get("zones") or {}
        ote = zones.get("ote") or {}
        tps = sig.get("take_profits") or []
        direction = entry.get("trade_direction")
        is_crypto = entry["ticker"].endswith("-USD")

        row = {
            "A": tid,
            "B": _date(entry.get("as_of")),
            "C": entry["ticker"].replace(".NS", ""),
            "E" if not is_crypto else "F": "Long" if direction == "BUY" else "Short",
            "G" if not is_crypto else "H": sig.get("htf_bias") or "",
            "H" if not is_crypto else "I": ote.get("low"),
            "I" if not is_crypto else "J": ote.get("high"),
            "J" if not is_crypto else "K": entry.get("entry"),
            "K" if not is_crypto else "L": entry.get("stop_loss"),
            "L" if not is_crypto else "M": entry.get("take_profit"),
            "M" if not is_crypto else "N": tps[1] if len(tps) > 1 else None,
            "P" if not is_crypto else "P": entry.get("confidence") or "",
            "Q": "Yes" if entry.get("entry_filled") else "No",
            "R": entry.get("entry") if entry.get("entry_filled") else None,
            "S": _date(entry.get("filled_at")),
            "V": OUTCOME_LABEL.get(entry.get("outcome"), ""),
        }
        if not is_crypto:
            row["O"] = sig.get("invalidation") or ""

        # A hypothetical is not a trade that was taken; say so where a reader
        # will see it rather than letting it look like a live position.
        flag = ""
        if not is_crypto and not entry["ticker"].endswith(".NS"):
            # The workbook only has NSE and Crypto sheets; anything else lands
            # on the equity sheet and must say so rather than look like an NSE name.
            flag = f"Non-NSE symbol ({entry['ticker']}) — filed here for completeness. "
        if entry.get("is_hypothetical"):
            flag = "NO_TRADE signal — hypothetical would-be trade, not taken. "
        if entry.get("status") == "unresolvable":
            flag += f"EXCLUDED from sample ({entry.get('outcome')}). "
        row.update(store["manual"].get(k, {}))
        notes_col = "AG" if not is_crypto else "AF"
        if flag:
            row["_flag"] = flag.strip()
            if not row.get(notes_col):        # never overwrite a real note
                row[notes_col] = flag.strip()
        (cry_rows if is_crypto else nse_rows).append(row)

    global LAST_SKIPPED
    LAST_SKIPPED = skipped
    return nse_rows, cry_rows, store


def diff_report(nse_rows, cry_rows):
    print(f"{len(nse_rows)} NSE + {len(cry_rows)} crypto row(s) from "
          f"{LOG_PATH.name}")
    for label, rows in (("NSE", nse_rows), ("Crypto", cry_rows)):
        for r in rows:
            note = r.get("_flag", "")
            out = r.get("V") or "open"
            print(f"  {label:6s} {r['A']}  {str(r.get('C','')):14s} "
                  f"{str(r.get('E') or r.get('F') or ''):6s} outcome={out:12s} {note}")
    if LAST_SKIPPED:
        print(f"\n  not journalled ({len(LAST_SKIPPED)}) — still in {LOG_PATH.name}:")
        for e in LAST_SKIPPED:
            why = ("hypothetical, never taken" if e.get("is_hypothetical")
                   else f"excluded ({e.get('outcome')})")
            print(f"    {e['ticker']:16s} {e.get('signal','?'):9s} {why}")


def main():
    dry = "--dry-run" in sys.argv
    forget = [a for i, a in enumerate(sys.argv[1:], 1)
              if sys.argv[i - 1] == "--forget"]
    renumber = "--renumber" in sys.argv
    nse_rows, cry_rows, store = build_rows(forget, renumber)
    if renumber:
        print("renumbering Trade IDs from T001 over journalled trades")
    if forget:
        print(f"forgetting manual value(s): {', '.join(forget)}")
    diff_report(nse_rows, cry_rows)
    if dry:
        print("\n--dry-run: nothing written.")
        return
    save_store(store)
    print(f"\nwrote {ANNOT_PATH.name} ({len(store['manual'])} annotated trade(s))")

    global _PREBUILT
    _PREBUILT = (nse_rows, cry_rows, store)
    # Run as a script this file is __main__, so make_journal's `import
    # journal_sync` loads a SECOND, independent copy whose _PREBUILT is still
    # None. Seed that copy too, or the rebuild silently recomputes.
    import journal_sync as _mod
    _mod._PREBUILT = _PREBUILT
    import make_journal  # noqa: F401  -- rebuilding the workbook is its import side effect


if __name__ == "__main__":
    main()
