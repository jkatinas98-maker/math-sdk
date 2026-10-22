"""
Pirate Scratch -- book + lookup-table generator and RTP report.

Simulates N rounds per mode with the exact rules in rules.py (weights from
weights.json, produced by calibrate.py) and writes, per mode:

  library/books_<mode>.jsonl   one round per line: id, payoutMultiplier
                               (x100 integer), events (the front end's
                               book-event shape, every card of the round in
                               order: base card then free cards), criteria
  library/lookup_<mode>.csv    id, weight, payoutMultiplier

RTP is made EXACT (TARGET_RTP) through the lookup weights: every winning
round gets weight W, every zero round a weight z solved so that
sum(w * pay) / sum(w) == TARGET_RTP. That's what the lookup weights are for
(the RGS draws a book id by weight); no book's content changes.

Run:  python3 scratch_sim.py [rounds_per_mode]
Then: python3 make_publish_package.py
"""

import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

from rules import MAX_WIN, MODES, TARGET_RTP, Weights, play_round

ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 500_000
OUT_DIR = Path(__file__).parent / "library"
WIN_WEIGHT = 1_000_000  # lookup weight for every winning round


def simulate(mode, table, rounds, seed):
    rng = random.Random(seed)
    w = Weights(table)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    books_path = OUT_DIR / f"books_{mode}.jsonl"
    pays = []
    st = defaultdict(float)
    parts = defaultdict(float)
    with open(books_path, "w") as bf:
        for sim_id in range(1, rounds + 1):
            r = play_round(mode, w, rng, MAX_WIN[mode])
            cents = round(r.payout * 100)
            pays.append(cents)
            criteria = "maxwin" if r.capped else ("bonus" if r.free_cards else ("win" if cents else "zero"))
            bf.write(json.dumps({"id": sim_id, "payoutMultiplier": cents, "events": r.events, "criteria": criteria},
                                separators=(",", ":")) + "\n")
            # stats
            st["hit"] += cents > 0
            st["bonus"] += r.free_cards > 0
            st["capped"] += r.capped
            st["free_cards"] += r.free_cards
            b = r.cards[0]
            parts["matches"] += b.match_win
            for k, v in b.features.items():
                if k == "shark_bonus":
                    st["shark_bonus"] += 1
                elif k == "scatter_bonus":
                    st["scatter_bonus"] += 1
                else:
                    parts[k] += v
                    if k.startswith("shark_"):
                        st["shark"] += 1
                    elif k in ("kraken", "chest", "skull_grid"):
                        st[k] += 1
            if r.free_cards:
                parts["bonus (free scratches)"] += r.payout - min(b.amount, r.payout)
            st["max"] = max(st["max"], r.payout)
            if r.payout >= 20:
                st["big20"] += 1
    return pays, st, parts


def write_lookup(mode, pays):
    """Exact-RTP weights: winners WIN_WEIGHT, zero rounds z."""
    n0 = sum(1 for p in pays if p == 0)
    s = sum(pays) / 100
    n1 = len(pays) - n0
    z = round((WIN_WEIGHT * s / TARGET_RTP - WIN_WEIGHT * n1) / n0)
    if z <= 0:
        raise SystemExit(f"{mode}: raw RTP {s/len(pays):.4f} is below target -- recalibrate (can't fix by weights)")
    with open(OUT_DIR / f"lookup_{mode}.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["id", "weight", "payoutMultiplier"])
        for i, p in enumerate(pays, start=1):
            wr.writerow([i, WIN_WEIGHT if p else z, p])
    total_w = WIN_WEIGHT * n1 + z * n0
    exact = WIN_WEIGHT * s / total_w
    return z, exact


def main():
    weights = json.loads((Path(__file__).parent / "weights.json").read_text())
    print(f"Pirate Scratch -- {ROUNDS:,} rounds per mode\n")
    for i, mode in enumerate(MODES):
        pays, st, parts = simulate(mode, weights[mode], ROUNDS, seed=1000 + i)
        n = len(pays)
        raw = sum(pays) / 100 / n
        z, exact = write_lookup(mode, pays)
        hit_w = st["hit"] * WIN_WEIGHT / (st["hit"] * WIN_WEIGHT + (n - st["hit"]) * z)
        print(f"== {mode} (max win {MAX_WIN[mode]}x) ==")
        print(f"  raw sample RTP {raw*100:.2f}%  ->  published RTP {exact*100:.4f}%  (zero-round weight {z}/{WIN_WEIGHT})")
        print(f"  hit rate {hit_w*100:.1f}%   bonus 1 in {n/max(1,st['bonus']):.0f}   max-win rounds 1 in {n/max(1,st['capped']):.0f}"
              f"   20x+ rounds 1 in {n/max(1,st['big20']):.0f}   biggest {st['max']:g}x")
        print(f"  per card: shark 1 in {n/max(1,st['shark']+st['shark_bonus']):.1f}   kraken(sym) 1 in {n/max(1,st['kraken']):.1f}"
              f"   key/chest 1 in {n/max(1,st['chest']):.0f}   skull grid 1 in {n/max(1,st['skull_grid']):.0f}")
        print("  RTP by source (raw):", ", ".join(f"{k} {v/n*100:.2f}%" for k, v in sorted(parts.items(), key=lambda kv: -kv[1])))
        print()
    print(f"Books + lookup tables in {OUT_DIR}")


if __name__ == "__main__":
    main()
