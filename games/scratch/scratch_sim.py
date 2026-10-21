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
     card of the same mode, which can itself retrigger the feature), PLUS
     three newer mechanics layered on top of the same per-card draw:
       - Treasure Meter: landing 4-of-a-kind (+1 point) or 5-plus-of-a-kind
         (+2 points) on any mult>0 symbol accumulates across the whole round;
         crossing the mode's threshold pops the meter and fires a Treasure
         Chest case-opening sequence.
       - `key` symbol: each one landed fires its own chest sequence directly
         (same sequence the meter pop fires), independent of the meter.
       - Treasure Chest case-opening: NOT a pick-one-of-three. Every trigger
         opens `chests_per_trigger` (3) independent chests in sequence, each
         drawn from one shared prize table (cash / multiplier-boost /
         retrigger / another key). All chests always resolve -- retrigger
         and key outcomes queue MORE chest opens (capped), they don't end
         the sequence early.
       - Kraken number-match: landing >=1 `kraken` symbol on a card (~1 in
         5 cards) opens it; a hidden player number and a hidden kraken
         number (both 1..10, uniform) are compared -- a match (1 in 10)
         pays a multiplier drawn from the mode's kraken_prize_table (2x up
         to the mode max), shown as a CS2-style case reel.
       - Every round's total payout is capped at the mode's max win.
  2. Measures real total RTP (cash wins + every feature's EV), hit
     frequency, bonus-trigger frequency, treasure-meter-pop frequency,
     kraken-hit frequency, and win distribution per difficulty.
  3. Writes out, per difficulty:
       - books_<difficulty>.jsonl  (one simulated ROUND each -- base card
         plus every free-scratch card it awarded, if any -- in the
         book/bookEvent shape the web-sdk front end already expects: one
         `revealPanels` event per card scratched this round, immediately
         followed by any `krakenResult`/`chestSequence` events that card's
         draw caused, then a `setTotalWin`/`finalWin` pair for the round's
         cumulative payout, and a `freeScratchesAwarded` field)
       - lookup_<difficulty>.csv   (id, weight, payoutMultiplier -- the
         standard math-sdk "lookup table" shape the RGS uses to pick a
         weighted-random outcome per real bet)

NOTE ON WEIGHT CALIBRATION: calibrate.py sets the `kraken` weight so ~1 in
5 cards land one, then binary-searches each mode's `skull` weight until
total RTP (all features, after the max-win cap) is 96%. Re-run it after any
change to symbols, prize tables or feature rules, paste the printed weights
into game_config.py, then run this file to verify + export.

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


def roll_chest_prize(prize_table, rng):
    """prize_table rows are (id, kind, value, weight)."""
    total = sum(row[3] for row in prize_table)
    r = rng.random() * total
    acc = 0.0
    for row in prize_table:
        acc += row[3]
        if r < acc:
            return row
    return prize_table[-1]


def resolve_chest_sequence(prize_table, chests_per_trigger, num_opens, rng, opens_budget, current_total):
    """Resolves one full chest-opening sequence: `num_opens` chests, expanding
    by `value` more opens whenever a 'retrigger' lands, or by
    `value * chests_per_trigger` more whenever a 'key' lands (each extra key
    is its own full chest sequence). `opens_budget` is this round's
    remaining safety-cap allowance. Cash prizes add to the running total
    immediately; multiplier-boost prizes multiply it -- both applied in
    resolution order, interleaved with whatever retrigger/key opens they
    cause, so the sequence behaves like a single ordered chain of chest
    pulls, not three independent ones scored after the fact.

    Returns (outcomes: list[(id, kind, value)], new_total, opens_used).
    """
    outcomes = []
    total = current_total
    queue = num_opens
    opened = 0
    while queue > 0 and opened < opens_budget:
        queue -= 1
        opened += 1
        prize_id, kind, value, _weight = roll_chest_prize(prize_table, rng)
        outcomes.append((prize_id, kind, value))
        if kind == "cash":
            total += value
        elif kind == "multiplier":
            total *= value
        elif kind == "retrigger":
            queue += value
        elif kind == "key":
            queue += value * chests_per_trigger
    return outcomes, total, opened


def roll_weighted(table, rng):
    """table rows are (value, weight)."""
    total = sum(w for _, w in table)
    r = rng.random() * total
    acc = 0.0
    for value, w in table:
        acc += w
        if r < acc:
            return value
    return table[-1][0]


def play_card(symbols, n_panels, rng, bonus_trigger_count, meter_points_table, kraken_range, kraken_prizes):
    """Draw every panel on one card (no early stop -- matches the front end,
    which always reveals the full grid).

    Returns (revealed_symbols, win_symbol_or_None, bonus_triggered: bool,
    meter_points: int, key_count: int, kraken: dict|None).
    """
    counts = defaultdict(int)
    revealed = []
    win_symbol = None
    bonus_count = 0
    key_count = 0
    kraken_count = 0
    for _ in range(n_panels):
        sym = weighted_choice(symbols, rng)
        revealed.append(sym)
        if sym.is_bonus:
            bonus_count += 1
            continue
        if sym.is_key:
            key_count += 1
            continue
        if sym.is_kraken:
            kraken_count += 1
            continue
        if sym.mult > 0:
            counts[sym.id] += 1
            if counts[sym.id] == 3 and win_symbol is None:
                win_symbol = sym

    meter_points = 0
    for count in counts.values():
        if count >= 5:
            meter_points += meter_points_table[5]
        elif count == 4:
            meter_points += meter_points_table[4]

    kraken = None
    if kraken_count > 0:
        player_num = rng.randint(1, kraken_range)
        kraken_num = rng.randint(1, kraken_range)
        hit = player_num == kraken_num
        kraken = {
            "player": player_num,
            "kraken": kraken_num,
            "hit": hit,
            "mult": roll_weighted(kraken_prizes, rng) if hit else 0,
        }

    return revealed, win_symbol, bonus_count >= bonus_trigger_count, meter_points, key_count, kraken


def play_round(cfg, symbols, n_panels, mode_name, rng, max_cards=200):
    """One full betting round: the base card, plus every free-scratch card it
    (recursively) awards, plus every Treasure Chest sequence the round's
    meter pops / key drops trigger, plus every Kraken roll. `max_cards` is a
    safety cap on free-scratch chaining (unchanged from before); chest-open
    recursion has its own separate cap, cfg.max_chest_opens_per_round.

    Returns (cards, total_mult, total_free_awarded) where each entry in
    `cards` is (revealed, win_symbol, bonus_triggered, meter_points,
    key_count, kraken, chest_sequences) -- chest_sequences is the list of
    sequences (each a list of (id, kind, value) outcomes) THAT CARD caused,
    in trigger order (meter-pop sequences before key-drop sequences when
    both happen on the same card).
    """
    meter_threshold = cfg.treasure_meter_threshold[mode_name]
    meter_points_table = cfg.treasure_meter_points
    chests_per_trigger = cfg.chests_per_trigger
    prize_table = cfg.chest_prize_table
    max_chest_opens = cfg.max_chest_opens_per_round
    kraken_range = cfg.kraken_number_range
    kraken_prizes = cfg.kraken_prize_table[mode_name]
    max_win = cfg.mode_max_win[mode_name]

    cards = []
    total_mult = 0.0
    total_free_awarded = 0
    meter_points_acc = 0
    chest_opens_used = 0
    queue = 1

    while queue > 0 and len(cards) < max_cards:
        queue -= 1
        revealed, win_symbol, bonus_triggered, meter_pts, key_count, kraken = play_card(
            symbols, n_panels, rng, cfg.bonus_trigger_count, meter_points_table, kraken_range, kraken_prizes,
        )
        mult = win_symbol.mult if win_symbol else 0.0
        total_mult += mult

        if bonus_triggered:
            total_free_awarded += cfg.free_scratches_awarded
            queue += cfg.free_scratches_awarded

        if kraken and kraken["hit"]:
            total_mult += kraken["mult"]

        meter_points_acc += meter_pts
        chest_triggers = key_count
        while meter_points_acc >= meter_threshold:
            meter_points_acc -= meter_threshold
            chest_triggers += 1

        card_chest_sequences = []
        for _ in range(chest_triggers):
            if chest_opens_used >= max_chest_opens:
                break
            outcomes, total_mult, opened = resolve_chest_sequence(
                prize_table, chests_per_trigger, chests_per_trigger, rng,
                max_chest_opens - chest_opens_used, total_mult,
            )
            chest_opens_used += opened
            card_chest_sequences.append(outcomes)

        cards.append((revealed, win_symbol, bonus_triggered, meter_pts, key_count, kraken, card_chest_sequences))

    # The mode's advertised max win caps the whole round.
    total_mult = min(total_mult, max_win)
    return cards, total_mult, total_free_awarded


def simulate_mode(mode_name, cols, rows, cfg, num_sims, rng, write_books=True):
    symbols = cfg.difficulties[mode_name]["symbols"]
    n_panels = cols * rows
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    books_path = OUT_DIR / f"books_{mode_name}.jsonl"
    lookup_path = OUT_DIR / f"lookup_{mode_name}.csv"

    total_bet = 0.0
    total_paid = 0.0
    wins = 0
    bonus_rounds = 0
    meter_pop_rounds = 0
    key_trigger_rounds = 0
    kraken_shown_rounds = 0
    kraken_hit_rounds = 0
    max_win_mult = 0.0
    win_by_symbol = defaultdict(int)

    # Lookup table: ONE ROW PER SIMULATION ID, 1:1 with the books file --
    # not deduplicated by distinct payout value. Per
    # docs/math_docs/source_section/file_info.md: "The final payout
    # multiplier for each simulation is summarized in lookUpTable_mode.csv
    # ... weights, initially assigned to 1" -- every simulation gets its
    # own row and starts at weight 1 (pre-optimization; we aren't running
    # the Rust reweighting pass). A deduplicated table (what this used to
    # build) only has as many rows as there are distinct payout values,
    # leaving every simulation id beyond that count with nothing to match
    # against in the RGS's id-aligned cross-check -- that mismatch is what
    # ERR_MATH_OUTSIDE_RANGE was actually reporting, not a rounding bug.
    lookup_rows = []

    books_file = open(books_path, "w") if write_books else None

    for sim_id in range(1, num_sims + 1):
        cards, round_mult, free_awarded = play_round(cfg, symbols, n_panels, mode_name, rng)

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

        round_had_meter_pop = False
        round_had_key_trigger = False
        round_had_kraken = False
        round_had_kraken_hit = False
        for _revealed, _win, _bonus, meter_pts, key_count, kraken, chest_sequences in cards:
            if key_count > 0:
                round_had_key_trigger = True
            if chest_sequences and key_count == 0:
                # at least one sequence this card wasn't from a key -> meter popped
                round_had_meter_pop = True
            elif chest_sequences and len(chest_sequences) > key_count:
                round_had_meter_pop = True
            if kraken:
                round_had_kraken = True
                if kraken["hit"]:
                    round_had_kraken_hit = True
        if round_had_meter_pop:
            meter_pop_rounds += 1
        if round_had_key_trigger:
            key_trigger_rounds += 1
        if round_had_kraken:
            kraken_shown_rounds += 1
        if round_had_kraken_hit:
            kraken_hit_rounds += 1

        # Canonical payout value for THIS round, computed once and reused
        # for both the book and the lookup table below -- any float
        # (round_mult vs. round(round_mult, 4) computed separately) can
        # disagree by floating-point noise on compound bonus-chain sums,
        # which the RGS's hash-based cross-check between the two files
        # will reject (ERR_MATH_OUTSIDE_RANGE). Rounding to cents once,
        # here, and reusing that same number everywhere removes the
        # possibility of the two files ever drifting apart.
        payout_cents = round(round_mult * 100)
        lookup_rows.append((sim_id, payout_cents))

        if books_file:
            events = []
            idx = 0
            for revealed, win_symbol, bonus_triggered, meter_pts, key_count, kraken, chest_sequences in cards:
                events.append({
                    "index": idx,
                    "type": "revealPanels",
                    "panels": [{"name": s.id} for s in revealed],
                    "gameType": "basegame" if idx == 0 else "freegame",
                })
                idx += 1
                if kraken is not None:
                    events.append({
                        "index": idx,
                        "type": "krakenResult",
                        "playerNumber": kraken["player"],
                        "krakenNumber": kraken["kraken"],
                        "hit": kraken["hit"],
                        # prize multiplier the case reel lands on (0 on a miss);
                        # amount is the same per-$1 value, like every other event
                        "multiplier": kraken["mult"],
                        "amount": kraken["mult"],
                    })
                    idx += 1
                for outcomes in chest_sequences:
                    events.append({
                        "index": idx,
                        "type": "chestSequence",
                        "chests": [{"id": oid, "kind": okind, "value": oval} for oid, okind, oval in outcomes],
                    })
                    idx += 1
            # NOTE: these "amount" fields are OUR OWN event schema, consumed
            # directly by the front end's book-event player as the real
            # decimal win multiplier (e.g. 1.5) -- NOT Stake's RGS wire
            # format. Only the top-level "payoutMultiplier" below is the
            # x100 fixed-point integer their spec requires. Using
            # payout_cents here too was the bug: the front end displayed/
            # credited the scaled integer (150) as if it were the real
            # multiplier, inflating every win 100x.
            events.append({"index": idx, "type": "setTotalWin", "amount": round_mult})
            idx += 1
            events.append({"index": idx, "type": "finalWin", "amount": round_mult})

            book = {
                "id": sim_id,
                # Integer, fixed-point x100 (11.5x -> 1150) per
                # docs/rgs_docs/data_format.md -- this IS the final
                # published value, not an intermediate one rescaled later.
                # (Only this field; "events" above stay in real multiplier units.)
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

    # lookup table: id, weight, payoutMultiplier -- one row per simulation,
    # same id as that simulation's book entry, weight=1 for every row
    # (uniform / pre-optimization), payoutMultiplier already in the same
    # x100 integer form written to the books file above.
    with open(lookup_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "weight", "payoutMultiplier"])
        for sim_id, mult in lookup_rows:
            w.writerow([sim_id, 1, mult])

    rtp = total_paid / total_bet
    hit_rate = wins / num_sims
    bonus_rate = bonus_rounds / num_sims
    meter_pop_rate = meter_pop_rounds / num_sims
    key_trigger_rate = key_trigger_rounds / num_sims
    kraken_shown_rate = kraken_shown_rounds / num_sims
    kraken_hit_rate = kraken_hit_rounds / num_sims

    return {
        "mode": mode_name,
        "grid_size": f"{cols}x{rows}",
        "sims": num_sims,
        "rtp": rtp,
        "hit_rate": hit_rate,
        "bonus_rate": bonus_rate,
        "meter_pop_rate": meter_pop_rate,
        "key_trigger_rate": key_trigger_rate,
        "kraken_shown_rate": kraken_shown_rate,
        "kraken_hit_rate": kraken_hit_rate,
        "max_win_mult": max_win_mult,
        "win_by_symbol": dict(win_by_symbol),
        "distinct_outcomes": len(set(mult for _, mult in lookup_rows)),
    }


def main():
    cfg = GameConfig()
    rng = random.Random(1337)  # fixed seed: reproducible report across runs

    results = []
    for mode_name, spec in cfg.difficulties.items():
        res = simulate_mode(mode_name, spec["cols"], spec["rows"], cfg, SIMS_PER_MODE, rng)
        results.append(res)

    print(f"\nSCRATCH -- Monte Carlo RTP report ({SIMS_PER_MODE:,} rounds per mode, "
          f"bonus = {cfg.bonus_trigger_count}+ scatters -> {cfg.free_scratches_awarded} free scratches)\n")
    header = (f"{'Mode':<8}{'Grid':<8}{'RTP':<9}{'Hit rate':<11}{'Bonus':<9}"
              f"{'MeterPop':<10}{'KeyTrig':<9}{'Kraken%':<10}{'KrknHit':<9}{'Max win':<10}")
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r['mode']:<8}{r['grid_size']:<8}{r['rtp']*100:>6.2f}%  "
            f"{r['hit_rate']*100:>7.2f}%  {r['bonus_rate']*100:>6.3f}%  "
            f"{r['meter_pop_rate']*100:>7.3f}%  {r['key_trigger_rate']*100:>6.3f}%  "
            f"{r['kraken_shown_rate']*100:>7.3f}%  {r['kraken_hit_rate']*100:>6.4f}%  "
            f"{r['max_win_mult']:>6g}x"
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
