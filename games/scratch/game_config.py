"""
Game configuration for SCRATCH.

A single-request (stateless) scratch-ticket game, closer to the `fifty_fifty`
example than to the reel-based templates: each bet resolves in one round, no
free spins / bonus stage. Three difficulty bet modes (easy/medium/hard) map
to the three grid sizes from the front-end prototype.

Paytable, symbol weights and win rule (match 3-of-a-kind anywhere on the
ticket wins that symbol's multiplier; the game pays the FIRST symbol to hit
a 3rd match, in reveal order) mirror the client-side prototype exactly so the
RTP figures below are a direct, honest measurement of that same logic -- not
a redesign of it.
"""

from src.config.config import Config
from src.config.distributions import Distribution
from src.config.config import BetMode


class SymbolDef:
    """One scratch-panel symbol: id, display name, payout multiplier, relative draw weight."""

    def __init__(self, symbol_id, name, mult, weight):
        self.id = symbol_id
        self.name = name
        self.mult = mult
        self.weight = weight


# Shared symbol roster. Weights are relative (not required to sum to 1) and are
# tuned per-difficulty below to match the three win-rate/volatility tiers from
# the prototype: Easy (frequent, small), Medium (middle), Hard (rare, big).
EASY_SYMBOLS = [
    # 3x5 grid (15 panels). Weights calibrated by Monte Carlo search to land
    # at ~96% RTP; see scratch_sim.py output. Symbol identities match the
    # front-end icon set: skull/coin/wallet/chest/crown are flat-black,
    # star and diamond are the only coloured tiers, diamond is top prize.
    SymbolDef("skull", "Skull", 0, 66.8504),
    SymbolDef("coin", "Coin", 1.5, 14.1968),
    SymbolDef("wallet", "Wallet", 2, 8.5181),
    SymbolDef("chest", "Chest", 4, 5.6787),
    SymbolDef("crown", "Crown", 9, 2.8394),
    SymbolDef("star", "Star", 16, 1.4197),
    SymbolDef("diamond", "Diamond", 45, 0.4969),
]

MEDIUM_SYMBOLS = [
    # 3x4 grid (12 panels). Weights calibrated to land at ~96% RTP.
    SymbolDef("skull", "Skull", 0, 60.1571),
    SymbolDef("coin", "Coin", 1.5, 17.1),
    SymbolDef("wallet", "Wallet", 2, 10.26),
    SymbolDef("chest", "Chest", 5, 6.84),
    SymbolDef("crown", "Crown", 9, 3.42),
    SymbolDef("star", "Star", 41, 1.71),
    SymbolDef("diamond", "Diamond", 110, 0.513),
]

HARD_SYMBOLS = [
    # 3x3 grid (9 panels). Weights calibrated to land at ~96% RTP. Now
    # includes smaller multiplier tiers (2x/6x/9x) alongside the 1000x top
    # prize, instead of every win being large.
    SymbolDef("skull", "Skull", 0, 61.1461),
    SymbolDef("coin", "Coin", 2, 16.7474),
    SymbolDef("wallet", "Wallet", 6, 10.0484),
    SymbolDef("chest", "Chest", 9, 6.6989),
    SymbolDef("crown", "Crown", 36, 3.3495),
    SymbolDef("star", "Star", 130, 1.6747),
    SymbolDef("diamond", "Diamond", 1000, 0.3349),
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
        # Grids are now 3 columns wide (fixed) with rows varying by
        # difficulty -- a "3 horizontal" layout, not square. Fewer total
        # panels than the original square grids since 9x9 was too much to
        # scratch through: Easy 3x5=15, Medium 3x4=12, Hard 3x3=9.
        self.difficulties = {
            "easy": {"cols": 3, "rows": 5, "symbols": EASY_SYMBOLS},
            "medium": {"cols": 3, "rows": 4, "symbols": MEDIUM_SYMBOLS},
            "hard": {"cols": 3, "rows": 3, "symbols": HARD_SYMBOLS},
        }

        self.wincap = 1000.0  # highest possible single-ticket multiplier (Hard: Star x3)
        self.rtp = 0.96  # target RTP; scratch_sim.py reports the measured value per mode

        self.construct_paths()

        # Required by Config / templates but unused for a non-reel game.
        self.num_reels = 0
        self.num_rows = [0] * self.num_reels
        self.paytable = {}
        self.include_padding = False
        self.special_symbols = {"wild": [], "scatter": [], "multiplier": []}
        self.freespin_triggers = {self.basegame_type: {}, self.freegame_type: {}}
        self.anticipation_triggers = {self.basegame_type: 0, self.freegame_type: 0}

        self.bet_modes = [
            BetMode(
                name=diff_name,
                cost=1.0,
                rtp=self.rtp,
                max_win=self.wincap,
                auto_close_disabled=False,
                is_feature=False,
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
