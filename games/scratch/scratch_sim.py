"""
Standalone Monte Carlo simulator + RTP verifier for SCRATCH.

This does not depend on the math-sdk's Rust optimization engine (that engine
is built around reel/board games and reweights symbol distributions to hit an
exact target RTP across many feature types -- overkill, and the wrong shape,
for a single-draw scratch ticket). Instead it:

  1. Simulates millions of real bets using the exact draw + resolve logic the
     front-end uses (weighted random panel draws per card, ALL panels always
     revealed, first symbol to reach 3 matches wins that card, 3+ `bonus`
     scatters anywhere on a card award 10 free scratches -- each one another
     card of the same mode, which can itself retrigger the feature).
  2. Measures real total RTP (cash wins + the bonus feature's contribution),
     hit frequency, bonus-trigger frequency, and win distribution per
     difficulty.
  3. Writes out, per difficulty:
       - books_<difficulty>.jsonl  (one simulated ROUND each -- base card
         plus every free-scratch card it awarded, if any -- in the
         book/bookEvent shape the web-sdk front end already expects:
         one `revealPanels` event per card scratched this round, a
         `setTotalWin`/`finalWin` pair for the round's cumulative payout,
         and a `freeScratchesAwarded` field)
       - lookup_<difficulty>.csv   (id, weight, payoutMultiplier -- the
         standard math-sdk "lookup table" shape the RGS uses to pick a
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


def play_card(symbols, n_panels, rng, bonus_trigger_count):
    """Draw every panel on one card (no early stop -- matches the front end,
    which always reveals the full grid). Returns (revealed_symbols,
    win_symbol_or_None, bonus_triggered: bool)."""
    counts = defaultdict(int)
    revealed = []
    win_symbol = None
    bonus_count = 0
    for _ in range(n_panels):
        sym = weighted_choice(symbols, rng)
        revealed.append(sym)
        if sym.is_bonus:
            bonus_count += 1
            continue
        if sym.mult > 0:
            counts[sym.id] += 1
            if counts[sym.id] == 3 and win_symbol is None:
                win_symbol = sym
    return revealed, win_symbol, bonus_count >= bonus_trigger_count


def play_round(symbols, n_panels, rng, bonus_trigger_count, free_scratches_awarded, max_cards=200):
    """One full betting round: the base card, plus every free-scratch card
    it (recursively) awards. `max_cards` is a safety cap, not a real limit --
    with these weights the chain almost never exceeds a handful of cards."""
    cards = []
    total_mult = 0.0
    total_free_awarded = 0
    queue = 1
    while queue > 0 and len(cards) < max_cards:
        queue -= 1
        revealed, win_symbol, bonus_triggered = play_card(symbols, n_panels, rng, bonus_trigger_count)
        mult = win_symbol.mult if win_symbol else 0.0
        total_mult += mult
        cards.append((revealed, win_symbol, bonus_triggered))
        if bonus_triggered:
            total_free_awarded += free_scratches_awarded
            queue += free_scratches_awarded
    return cards, total_mult, total_free_awarded


def simulate_mode(mode_name, cols, rows, symbols, bonus_trigger_count, free_scratches_awarded,
                   num_sims, rng, write_books=True):
    n_panels = cols * rows
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    books_path = OUT_DIR / f"books_{mode_name}.jsonl"
    lookup_path = OUT_DIR / f"lookup_{mode_name}.csv"

    total_bet = 0.0
    total_paid = 0.0
    wins = 0
    bonus_rounds = 0
    max_win_mult = 0.0
    win_by_symbol = defaultdict(int)

    # lookup table: distinct round-level payout outcomes -> occurrence count (used as weight)
    lookup_counts = defaultdict(int)

    books_file = open(books_path, "w") if write_books else None

    for sim_id in range(1, num_sims + 1):
        cards, round_mult, free_awarded = play_round(
            symbols, n_panels, rng, bonus_trigger_count, free_scratches_awarded
        )

        total_bet += 1.0
        total_paid += round_mult
        if round_mult > 0:
            wins += 1
            # credit the win to the base card's winning symbol for the breakdown report
            base_win = cards[0][1]
            if base_win:
                win_by_symbol[base_win.id] += 1
            max_win_mult = max(max_win_mult, round_mult)
        if free_awarded > 0:
            bonus_rounds += 1

        # Canonical payout value for THIS round, computed once and reused
        # for both the book and the lookup table below -- any float
        # (round_mult vs. round(round_mult, 4) computed separately) can
        # disagree by floating-point noise on compound bonus-chain sums,
        # which the RGS's hash-based cross-check between the two files
        # will reject (ERR_MATH_OUTSIDE_RANGE). Rounding to cents once,
        # here, and reusing that same number everywhere removes the
        # possibility of the two files ever drifting apart.
        payout_cents = round(round_mult * 100)
        lookup_counts[payout_cents] += 1

        if books_file:
            events = []
            idx = 0
            for revealed, win_symbol, bonus_triggered in cards:
                events.append({
                    "index": idx,
                    "type": "revealPanels",
                    "panels": [{"name": s.id} for s in revealed],
                    "gameType": "basegame" if idx == 0 else "freegame",
                })
                idx += 1
            events.append({"index": idx, "type": "setTotalWin", "amount": payout_cents})
            idx += 1
            events.append({"index": idx, "type": "finalWin", "amount": payout_cents})

            book = {
                "id": sim_id,
                # Integer, fixed-point x100 (11.5x -> 1150) per
                # docs/rgs_docs/data_format.md -- this IS the final
                # published value, not an intermediate one rescaled later.
                "payoutMultiplier": payout_cents,
                "events": events,
                "criteria": cards[0][1].id if cards[0][1] else "none",
                "baseGameWins": cards[0][1].mult if cards[0][1] else 0.0,
                "freeGameWins": round_mult - (cards[0][1].mult if cards[0][1] else 0.0),
                "freeScratchesAwarded": free_awarded,
            }
            books_file.write(json.dumps(book) + "\n")

    if books_file:
        books_file.close()

    # lookup table: id, weight, payoutMultiplier (weight = observed
    # frequency; payoutMultiplier already in the same x100 integer form
    # written to the books file above -- no separate rescaling step).
    with open(lookup_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "weight", "payoutMultiplier"])
        for i, (mult, count) in enumerate(sorted(lookup_counts.items()), start=1):
            w.writerow([i, count, mult])

    rtp = total_paid / total_bet
    hit_rate = wins / num_sims
    bonus_rate = bonus_rounds / num_sims

    return {
        "mode": mode_name,
        "grid_size": f"{cols}x{rows}",
        "sims": num_sims,
        "rtp": rtp,
        "hit_rate": hit_rate,
        "bonus_rate": bonus_rate,
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
            mode_name, spec["cols"], spec["rows"], spec["symbols"],
            config.bonus_trigger_count, config.free_scratches_awarded,
            SIMS_PER_MODE, rng,
        )
        results.append(res)

    print(f"\nSCRATCH -- Monte Carlo RTP report ({SIMS_PER_MODE:,} rounds per mode, "
          f"bonus = {config.bonus_trigger_count}+ scatters -> {config.free_scratches_awarded} free scratches)\n")
    header = f"{'Mode':<8}{'Grid':<8}{'RTP':<10}{'Hit rate':<12}{'Bonus rate':<13}{'Max win':<10}"
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r['mode']:<8}{r['grid_size']:<8}{r['rtp']*100:>6.2f}%   "
            f"{r['hit_rate']*100:>7.2f}%    {r['bonus_rate']*100:>8.4f}%    {r['max_win_mult']:>6g}x"
        )

    print("\nWin breakdown by symbol, base card only (share of all wins):")
    for r in results:
        print(f"\n  {r['mode']}:")
        total_wins = sum(r["win_by_symbol"].values()) or 1
        for sym_id, count in sorted(r["win_by_symbol"].items(), key=lambda kv: -kv[1]):
            print(f"    {sym_id:<10} {count:>7,}  ({count/total_wins*100:5.2f}% of wins)")

    print(f"\nBooks + lookup tables written to: {OUT_DIR}")


if __name__ == "__main__":
    main()
