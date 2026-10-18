"""
Standalone Monte Carlo simulator + RTP verifier for SCRATCH.

This does not depend on the math-sdk's Rust optimization engine (that engine
is built around reel/board games and reweights symbol distributions to hit an
exact target RTP across many feature types -- overkill, and the wrong shape,
for a single-draw scratch ticket). Instead it:

  1. Simulates millions of real tickets using the exact draw + resolve logic
     the front-end prototype uses (weighted random panel draws, first symbol
     to reach 3 matches wins, ticket ends when all panels are revealed).
  2. Measures real RTP, hit frequency, and win distribution per difficulty.
  3. Writes out:
       - books_<difficulty>.jsonl  (one simulated round each, in the
         book/bookEvent shape the web-sdk front end already expects:
         reveal-style events + a final payout multiplier)
       - lookup_<difficulty>.csv   (id, weight, payoutMultiplier -- the
         standard math-sdk "lookup table" shape that the RGS uses to pick a
         weighted-random outcome per real bet)

Run:
    python3 scratch_sim.py
"""

import json
import csv
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root, for game_config's own imports

from game_config import GameConfig  # noqa: E402


SIMS_PER_MODE = 1_000_000
OUT_DIR = Path(__file__).parent / "library"


def weighted_choice(symbols, rng):
    total = sum(s.weight for s in symbols)
    r = rng.random() * total
    acc = 0.0
    for s in symbols:
        acc += s.weight
        if r < acc:
            return s
    return symbols[-1]


def play_ticket(symbols, n_panels, rng):
    """Draw panels one at a time; stop at the first 3-of-a-kind match or when
    the ticket is fully revealed. Returns (revealed_symbol_ids, win_symbol_or_None)."""
    n = n_panels
    counts = defaultdict(int)
    revealed = []
    win_symbol = None
    for _ in range(n):
        sym = weighted_choice(symbols, rng)
        revealed.append(sym)
        if sym.mult > 0:
            counts[sym.id] += 1
            if counts[sym.id] >= 3:
                win_symbol = sym
                break
    return revealed, win_symbol


def simulate_mode(mode_name, cols, rows, symbols, num_sims, rng, write_books=True):
    n_panels = cols * rows
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    books_path = OUT_DIR / f"books_{mode_name}.jsonl"
    lookup_path = OUT_DIR / f"lookup_{mode_name}.csv"

    total_bet = 0.0
    total_paid = 0.0
    wins = 0
    max_win_mult = 0.0
    win_by_symbol = defaultdict(int)

    # lookup table: distinct payout outcomes -> occurrence count (used as weight)
    lookup_counts = defaultdict(int)

    books_file = open(books_path, "w") if write_books else None

    for sim_id in range(1, num_sims + 1):
        revealed, win_symbol = play_ticket(symbols, n_panels, rng)
        payout_mult = win_symbol.mult if win_symbol else 0.0

        total_bet += 1.0
        total_paid += payout_mult
        if win_symbol:
            wins += 1
            win_by_symbol[win_symbol.id] += 1
            max_win_mult = max(max_win_mult, payout_mult)

        lookup_counts[round(payout_mult, 4)] += 1

        if books_file:
            book = {
                "id": sim_id,
                "payoutMultiplier": payout_mult,
                "events": [
                    {
                        "index": 0,
                        "type": "revealPanels",
                        "panels": [{"name": s.id} for s in revealed],
                        "gameType": "basegame",
                    },
                    {"index": 1, "type": "setTotalWin", "amount": payout_mult},
                    {"index": 2, "type": "finalWin", "amount": payout_mult},
                ],
                "criteria": win_symbol.id if win_symbol else "none",
                "baseGameWins": payout_mult,
                "freeGameWins": 0.0,
            }
            books_file.write(json.dumps(book) + "\n")

    if books_file:
        books_file.close()

    # lookup table: id, weight, payoutMultiplier (weight = observed frequency)
    with open(lookup_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "weight", "payoutMultiplier"])
        for i, (mult, count) in enumerate(sorted(lookup_counts.items()), start=1):
            w.writerow([i, count, mult])

    rtp = total_paid / total_bet
    hit_rate = wins / num_sims

    return {
        "mode": mode_name,
        "grid_size": f"{cols}x{rows}",
        "sims": num_sims,
        "rtp": rtp,
        "hit_rate": hit_rate,
        "max_win_mult": max_win_mult,
        "win_by_symbol": dict(win_by_symbol),
        "distinct_outcomes": len(lookup_counts),
    }


def main():
    config = GameConfig()
    rng = random.Random(1337)  # fixed seed: reproducible report across runs

    results = []
    for mode_name, spec in config.difficulties.items():
        res = simulate_mode(
            mode_name, spec["cols"], spec["rows"], spec["symbols"], SIMS_PER_MODE, rng
        )
        results.append(res)

    print(f"\nSCRATCH -- Monte Carlo RTP report ({SIMS_PER_MODE:,} tickets per mode)\n")
    header = f"{'Mode':<8}{'Grid':<8}{'RTP':<10}{'Hit rate':<12}{'Max win':<10}"
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r['mode']:<8}{r['grid_size']:<8}{r['rtp']*100:>6.2f}%   "
            f"{r['hit_rate']*100:>7.2f}%    {r['max_win_mult']:>6g}x"
        )

    print("\nWin breakdown by symbol (share of all wins):")
    for r in results:
        print(f"\n  {r['mode']}:")
        total_wins = sum(r["win_by_symbol"].values()) or 1
        for sym_id, count in sorted(r["win_by_symbol"].items(), key=lambda kv: -kv[1]):
            print(f"    {sym_id:<10} {count:>7,}  ({count/total_wins*100:5.2f}% of wins)")

    print(f"\nBooks + lookup tables written to: {OUT_DIR}")


if __name__ == "__main__":
    main()
