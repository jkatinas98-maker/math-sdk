"""
RTP calibration for SCRATCH.

For each mode:
  1. Sets the `kraken` symbol weight so that P(card lands >= 1 kraken) is
     exactly KRAKEN_CARD_RATE (1 in 5), given the other weights and the
     card's panel count:  q = 1 - (1 - rate)^(1/n),  w = q * S / (1 - q).
  2. Binary-searches the `skull` (no-win) weight so the measured TOTAL RTP
     (cash + free scratches + chests + kraken, after the max-win cap) lands
     on cfg.rtp. Uses common random numbers (same seed every iteration) so
     RTP is monotonic in the skull weight and the search converges cleanly.

Prints the weights to paste into game_config.py. Then run scratch_sim.py
for the full 1M-round verification + books/lookup export.

Run:
    python3 calibrate.py
"""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from game_config import GameConfig  # noqa: E402
from scratch_sim import play_round  # noqa: E402

KRAKEN_CARD_RATE = 0.20
SEARCH_ROUNDS = 400_000
ITERATIONS = 14


def set_kraken_weight(symbols, n_panels):
    others = sum(s.weight for s in symbols if not s.is_kraken)
    q = 1 - (1 - KRAKEN_CARD_RATE) ** (1 / n_panels)
    for s in symbols:
        if s.is_kraken:
            s.weight = q * others / (1 - q)


def measure(cfg, mode, symbols, n_panels, rounds, seed):
    rng = random.Random(seed)
    paid = 0.0
    for _ in range(rounds):
        _cards, mult, _free = play_round(cfg, symbols, n_panels, mode, rng)
        paid += mult
    return paid / rounds


def main():
    cfg = GameConfig()
    target = cfg.rtp
    for mode, spec in cfg.difficulties.items():
        symbols = spec["symbols"]
        n = spec["cols"] * spec["rows"]
        skull = next(s for s in symbols if s.id == "skull")
        lo, hi = skull.weight * 0.6, skull.weight * 2.5
        for _ in range(ITERATIONS):
            skull.weight = (lo + hi) / 2
            set_kraken_weight(symbols, n)
            rtp = measure(cfg, mode, symbols, n, SEARCH_ROUNDS, seed=2024)
            # more skull weight -> fewer wins -> lower RTP
            if rtp > target:
                lo = skull.weight
            else:
                hi = skull.weight
        skull.weight = (lo + hi) / 2
        set_kraken_weight(symbols, n)
        kraken = next(s for s in symbols if s.is_kraken)
        rtp = measure(cfg, mode, symbols, n, SEARCH_ROUNDS, seed=2024)
        print(f"{mode:<7} skull={skull.weight:.4f}  kraken={kraken.weight:.4f}  measured RTP={rtp * 100:.2f}%")


if __name__ == "__main__":
    main()
