"""
Game configuration for SCRATCH.

A single-request (stateless) scratch-ticket game, closer to the `fifty_fifty`
example than to the reel-based templates: each bet resolves in one round.
There IS a feature now though -- landing 3+ `bonus` scatter symbols anywhere
on the card awards 10 free scratches (each one just another ticket of the
same mode, which can itself retrigger the feature). Three difficulty bet
modes (easy/medium/hard) map to the three grid sizes from the front-end.

Paytable, symbol weights and win rule mirror the client-side implementation
exactly (apps/scratch/src/game/config.ts + mockBet.ts in the web-sdk repo)
so the RTP figures below are a direct, honest measurement of that same
logic, not a redesign of it:
  - All panels on the card are always revealed (no early stop).
  - Cash win = the FIRST symbol (scanning reveal order, mult > 0) to reach
    its 3rd occurrence anywhere on the card. At most one win per ticket.
  - Bonus win = landing >= 3 `bonus` scatters anywhere on the card
    (independent of the cash-win scan) -> 10 free scratches.

Symbol ids are the shared multiplier-value ids used by the front end
(mult2 is "2x" in every mode it appears in, not a per-mode icon) so the two
sides can be diffed directly against each other.
"""

from src.config.config import Config
from src.config.distributions import Distribution
from src.config.config import BetMode


class SymbolDef:
    """One scratch-panel symbol: id, display name, payout multiplier, relative draw weight."""

    def __init__(self, symbol_id, name, mult, weight, is_bonus=False):
        self.id = symbol_id
        self.name = name
        self.mult = mult
        self.weight = weight
        self.is_bonus = is_bonus  # scatter: counts toward its own 3-of-a-kind trigger, never toward a cash win


# Shared symbol roster, values matching apps/scratch/src/game/config.ts
# exactly. Weights calibrated by Monte Carlo search (see calibrate.py) so
# that TOTAL RTP -- cash wins plus the bonus-feature's free-scratch EV --
# lands at ~96% per mode; see scratch_sim.py's report for the measured
# figures including the recursive free-scratch chains.
FREE_SCRATCHES_AWARDED = 10
BONUS_TRIGGER_COUNT = 3

EASY_SYMBOLS = [
    # 3x5 grid (15 panels).
    SymbolDef("skull", "Skull", 0, 66.0),
    SymbolDef("bonus", "Bonus", 0, 0.8, is_bonus=True),
    SymbolDef("mult1_5", "1.5x", 1.5, 14.1097),
    SymbolDef("mult2", "2x", 2, 8.4658),
    SymbolDef("mult4", "4x", 4, 5.6438),
    SymbolDef("mult9", "9x", 9, 2.822),
    SymbolDef("mult16", "16x", 16, 1.411),
    SymbolDef("mult45", "45x", 45, 0.4938),
]

MEDIUM_SYMBOLS = [
    # 3x4 grid (12 panels).
    SymbolDef("skull", "Skull", 0, 69.0),
    SymbolDef("bonus", "Bonus", 0, 0.6, is_bonus=True),
    SymbolDef("mult1_5", "1.5x", 1.5, 17.5225),
    SymbolDef("mult2", "2x", 2, 10.2215),
    SymbolDef("mult5", "5x", 5, 6.5709),
    SymbolDef("mult9", "9x", 9, 5.1107),
    SymbolDef("mult41", "41x", 41, 2.9204),
    SymbolDef("mult110", "110x", 110, 1.4602),
]

HARD_SYMBOLS = [
    # 3x3 grid (9 panels).
    SymbolDef("skull", "Skull", 0, 74.0),
    SymbolDef("bonus", "Bonus", 0, 0.4, is_bonus=True),
    SymbolDef("mult2", "2x", 2, 20.6734),
    SymbolDef("mult6", "6x", 6, 11.2764),
    SymbolDef("mult9", "9x", 9, 7.5176),
    SymbolDef("mult36", "36x", 36, 4.6985),
    SymbolDef("mult130", "130x", 130, 2.2553),
    SymbolDef("mult1000", "1000x", 1000, 0.5638),
]


class GameConfig(Config):
    """SCRATCH configuration: three stateless bet modes, one per difficulty."""

    def __init__(self):
        super().__init__()
        self.game_id = "scratch"
        self.provider_number = 0
        self.working_name = "scratch"
        self.win_type = "other"  # not a lines/ways/scatter/cluster reel game

        # This config class is also read directly by scratch_sim.py (which
        # does not depend on the Rust optimization pipeline), so the
        # difficulty definitions live here as plain data.
        # Grids are 3 columns wide (fixed) with rows varying by difficulty:
        # Easy 3x5=15, Medium 3x4=12, Hard 3x3=9.
        self.difficulties = {
            "easy": {"cols": 3, "rows": 5, "symbols": EASY_SYMBOLS},
            "medium": {"cols": 3, "rows": 4, "symbols": MEDIUM_SYMBOLS},
            "hard": {"cols": 3, "rows": 3, "symbols": HARD_SYMBOLS},
        }

        self.free_scratches_awarded = FREE_SCRATCHES_AWARDED
        self.bonus_trigger_count = BONUS_TRIGGER_COUNT

        self.wincap = 1000.0  # highest possible single-ticket multiplier (Hard: 1000x)
        self.rtp = 0.96  # target TOTAL RTP (cash + bonus feature); scratch_sim.py reports measured values

        self.construct_paths()

        # Required by Config / templates but unused for a non-reel game.
        self.num_reels = 0
        self.num_rows = [0] * self.num_reels
        self.paytable = {}
        self.include_padding = False
        self.special_symbols = {"wild": [], "scatter": ["bonus"], "multiplier": []}
        self.freespin_triggers = {self.basegame_type: {}, self.freegame_type: {}}
        self.anticipation_triggers = {self.basegame_type: 0, self.freegame_type: 0}

        self.bet_modes = [
            BetMode(
                name=diff_name,
                cost=1.0,
                rtp=self.rtp,
                max_win=self.wincap,
                auto_close_disabled=False,
                is_feature=True,  # has the bonus-scatter free-scratch feature
                is_buybonus=False,
                distributions=[
                    Distribution(
                        criteria="basegame",
                        quota=1.0,
                        conditions={
                            "reel_weights": {},
                            "force_wincap": False,
                            "force_freegame": False,
                        },
                    ),
                ],
            )
            for diff_name in self.difficulties
        ]
