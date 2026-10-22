"""
RTP calibration for Pirate Scratch (rules.py).

Per mode, the feature symbols are weighted to hit target per-card
frequencies (closed form from the panel count), and the shark-bite
outcome weights are per mode. Then the `skull` (no-win) weight is
binary-searched until total RTP -- every card of the round, every
feature, the bonus, after the max-win cap -- lands on TARGET_RTP.
Feature rates are re-derived inside every step (they depend on the total
weight), using common random numbers so RTP is monotonic in the skull
weight.

Prints the final weights; paste them into game_config.py / the front end.
"""
import json
import math
import random
import sys
from pathlib import Path
from collections import defaultdict

import rules
from rules import MODES, GRID, Weights, play_round

TARGET_RTP = 0.96
SEARCH_ROUNDS = 300_000
ITERATIONS = 16

# Paying-symbol base weights (relative shape within a mode -- unchanged).
BASE = {
    "easy": {"mult1_5": 14.1097, "mult2": 8.4658, "mult4": 5.6438, "mult9": 2.822, "mult16": 1.411, "mult45": 0.4938},
    "medium": {"mult1_5": 17.5225, "mult2": 10.2215, "mult5": 6.5709, "mult9": 5.1107, "mult41": 2.9204, "mult110": 1.4602},
    "hard": {"mult2": 20.6734, "mult6": 11.2764, "mult9": 7.5176, "mult36": 4.6985, "mult130": 2.2553, "mult1000": 0.5638},
}

# Target per-card frequencies (probability a card shows the feature).
# `bonus` is the 3-scatter route only; Shark Bite -> Bonus adds
# P(shark) * share on top.
TARGET = {
    #          kraken    shark    key       bonus (3+ scatters)
    "easy":   (1 / 6,   1 / 10,  1 / 200,  1 / 350),
    "medium": (1 / 6,   1 / 10,  1 / 200,  1 / 400),
    "hard":   (1 / 6,   1 / 10,  1 / 200,  1 / 450),
}

SHARK = rules.SHARK_OUTCOMES

MAX_WIN = rules.MAX_WIN


def q_at_least_one(p, n):
    return 1 - (1 - p) ** (1 / n)


def q_at_least_three(p, n):
    lo, hi = 0.0, 0.5
    for _ in range(60):
        q = (lo + hi) / 2
        p3 = 1 - sum(math.comb(n, k) * q**k * (1 - q) ** (n - k) for k in range(3))
        if p3 < p:
            lo = q
        else:
            hi = q
    return (lo + hi) / 2


def build_weights(mode, skull):
    """Paying symbols + skull fixed; feature weights solved for their targets.
    Each feature is a share q of the total, so w = q * total; total solves
    total = others + sum(q_i) * total."""
    n = GRID[mode][0] * GRID[mode][1]
    kr, sh, key, bonus = TARGET[mode]
    qs = {
        "kraken": q_at_least_one(kr, n),
        "shark": q_at_least_one(sh, n),
        "key": q_at_least_one(key, n),
        "bonus": q_at_least_three(bonus, n),
    }
    others = skull + sum(BASE[mode].values())
    total = others / (1 - sum(qs.values()))
    table = {"skull": skull, **BASE[mode]}
    for name, q in qs.items():
        table[name] = q * total
    return table


def measure(mode, table, rounds, seed=2026):
    rng = random.Random(seed)
    w = Weights(table)
    paid = 0.0
    for _ in range(rounds):
        paid += play_round(mode, w, rng, MAX_WIN[mode]).payout
    return paid / rounds


def calibrate(mode, rounds=SEARCH_ROUNDS):
    lo, hi = 20.0, 2000.0
    for _ in range(ITERATIONS):
        mid = math.sqrt(lo * hi)
        rtp = measure(mode, build_weights(mode, mid), rounds)
        if rtp > TARGET_RTP:
            lo = mid
        else:
            hi = mid
    skull = math.sqrt(lo * hi)
    return build_weights(mode, skull)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        SEARCH_ROUNDS = int(sys.argv[1])
    out = {}
    for mode in MODES:
        t = calibrate(mode, SEARCH_ROUNDS)
        rtp = measure(mode, t, SEARCH_ROUNDS, seed=99)
        print(mode, f"RTP(check)={rtp*100:.2f}%", {k: round(v, 4) for k, v in t.items()}, flush=True)
        out[mode] = {k: round(v, 4) for k, v in t.items()}
    with open(Path(__file__).parent / "weights.json", "w") as f:
        json.dump(out, f, indent=2)
    print("wrote weights.json")
