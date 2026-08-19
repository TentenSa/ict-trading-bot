#!/usr/bin/env python3
"""
Phase 3: same-ticker historical track record, advisory-only.

Reads signals_log.jsonl, filters to one ticker's RESOLVED entries (win/loss —
unresolved_timeout is reported separately, not counted in the win rate), and
returns a factual note. Below MIN_RESOLVED, returns an "insufficient history"
note instead of a percentage, so a 1/1 or 2/3 record can't masquerade as a
trend.

This is advisory only: it does not change SIGNAL, CONFIDENCE, entry, stop, or
any other field. Per the bot's OUTPUT FORMAT, run this before every signal and
fold the returned note into the "7. TRACK RECORD" section verbatim.

Usage:
    python3 stats.py TICKER
"""
import json
import sys
from pathlib import Path

LOG_PATH = Path(__file__).parent / "signals_log.jsonl"
MIN_RESOLVED = 5


def _read_log():
    if not LOG_PATH.exists():
        return []
    lines = [l for l in LOG_PATH.read_text().splitlines() if l.strip()]
    return [json.loads(l) for l in lines]


def ticker_advisory(ticker, min_resolved=MIN_RESOLVED):
    entries = [e for e in _read_log() if e["ticker"] == ticker]
    wins = sum(1 for e in entries if e["status"] == "resolved" and e["outcome"] == "win")
    losses = sum(1 for e in entries if e["status"] == "resolved" and e["outcome"] == "loss")
    timeouts = sum(1 for e in entries if e["status"] == "resolved" and e["outcome"] == "unresolved_timeout")
    open_count = sum(1 for e in entries if e["status"] == "open")
    decisive = wins + losses

    if decisive < min_resolved:
        note = (f"Insufficient history for {ticker}: {decisive} resolved trade(s) logged "
                f"(need {min_resolved}), {open_count} still open. Not used to adjust this signal.")
    else:
        win_rate = 100 * wins / decisive
        note = (f"Track record for {ticker}: {wins}/{decisive} wins ({win_rate:.0f}%), "
                f"{timeouts} timed out unresolved. Advisory only — did not change this signal.")

    return {
        "ticker": ticker, "wins": wins, "losses": losses, "timeouts": timeouts,
        "decisive_resolved": decisive, "open": open_count,
        "sufficient_history": decisive >= min_resolved, "note": note,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 stats.py TICKER")
        sys.exit(0)
    result = ticker_advisory(sys.argv[1])
    print(result["note"])
