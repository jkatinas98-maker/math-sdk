"""
Pirate Scratch -- the complete game rules, in one place.

This is the source of truth the RGS books are generated from, and it
mirrors the front end's mock (apps/scratch/src/game/mockBet.ts + kraken.ts
in the web-sdk repo) rule for rule. If a rule changes on one side it must
change on the other.

ONE BET = ONE ROUND = one base card, plus (if the bonus triggers) every
free-scratch card it awards, all inside the same book. Per card:

  * Panels are drawn independently from the mode's weights; at most one
    Kraken, one Shark and one Key per card (a second one is redrawn) since
    each only triggers once.
  * Every paying symbol with 3+ on the card pays its multiplier, plus the
    mode's escalating extra-match bonus for the 4th, 5th, ... copy. Several
    winning symbols on one card all add up.
  * A card of nothing but skulls pays SKULL_GRID_PRIZE.
  * Kraken symbol -> number match: player 1..10 vs Kraken 1..10; a match
    spins the treasure reel (KRAKEN_PRIZE_TABLE).
  * Shark symbol -> Shark Bite roll, one of:
        kraken  -> the Kraken number match
        octopus -> 3 extra "plain" cards (skulls + paying symbols only);
                   every win on them adds to this card
        pirate  -> plunders the highest-paying symbol showing on the card
                   (any count); the mode's lowest symbol if there's none
        key     -> one chest pick
        bonus   -> 10 free scratches
  * Key symbol -> one chest pick (pick 1 of 3; prize from CHEST_PICK_TABLE).
  * 3+ Bonus scatters -> 10 free scratches.

FREE SCRATCHES (the bonus): each free card is a full card (all features
can land, retriggers add 10 more). A free card that wins anything -- its
own matches AND whatever its features won -- adds that multiplier to the
running bonus multiplier, and the card pays bet x the whole bonus
multiplier so far.

The round's total is capped at the mode's max win.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Tables (keep in sync with the front end)
# ---------------------------------------------------------------------------

MODES = ("easy", "medium", "hard")
MAX_WIN = {"easy": 110, "medium": 1000, "hard": 2500}  # per round (base + bonus + features)
TARGET_RTP = 0.96
GRID = {"easy": (3, 5), "medium": (3, 4), "hard": (3, 3)}  # cols, rows

# Paying symbols per mode: id -> multiplier.
PAYING = {
    "easy": {"mult1_5": 1.5, "mult2": 2, "mult4": 4, "mult16": 16, "mult45": 45, "mult110": 110},
    "medium": {"mult1_5": 1.5, "mult2": 2, "mult5": 5, "mult41": 41, "mult110": 110, "mult1000": 1000},
    "hard": {"mult2": 2, "mult6": 6, "mult9": 9, "mult36": 36, "mult130": 130, "mult1000": 1000, "mult2500": 2500},
}

FEATURE_SYMBOLS = ("bonus", "key", "kraken", "shark")
ONE_PER_CARD = ("kraken", "shark", "key")

FREE_SCRATCHES_AWARDED = 10
# A bonus (all its free cards together) always pays at least this many
# times the bet; any shortfall is topped up at the end of the bonus.
BONUS_MINIMUM = {"easy": 5, "medium": 5, "hard": 5}
BONUS_TRIGGER_COUNT = 3
MAX_FREE_CARDS = 100  # safety cap on retrigger chains

# Extra-match ladder: what the 4th, 5th, 6th ... copy of a winning symbol
# adds (last value repeats).
EXTRA_MATCH_LADDER = {
    "easy": [0.25, 0.25, 0.5, 0.5, 1, 1, 1, 1, 1.5, 1.5, 2, 5],
    "medium": [0.5, 0.5, 1, 1, 1.5, 1.5, 2, 3, 5],
    "hard": [1, 1.5, 2, 2.5, 3, 5],
}

SKULL_GRID_PRIZE = {"easy": 5, "medium": 5, "hard": 2}

KRAKEN_NUMBER_RANGE = 10
KRAKEN_PRIZE_TABLE = {
    "easy": [(2, 4785.1), (3, 2830.7), (5, 1461.0), (10, 595.5), (20, 242.7), (45, 84.9)],
    "medium": [(2, 4967.8), (3, 2845.1), (5, 1409.7), (10, 543.6), (25, 154.3), (50, 59.5), (110, 20.1)],
    "hard": [(2, 4986.9), (3, 2859.3), (5, 1418.8), (10, 548.2), (25, 156.0), (100, 23.3), (250, 6.6), (1000, 1.0)],
}

# Shark Bite outcome weights per mode. Pirate (plunders the best symbol on
# the card) is very strong on the high-variance modes, so it's rarer there;
# Bonus via Shark Bite is 4% of rolls (~1 in 250 cards).
SHARK_OUTCOMES = {
    "easy": [("kraken", 53), ("octopus", 25), ("pirate", 6), ("key", 12), ("bonus", 4)],
    "medium": [("kraken", 57.5), ("octopus", 25), ("pirate", 1.5), ("key", 12), ("bonus", 4)],
    "hard": [("kraken", 58), ("octopus", 25), ("pirate", 1), ("key", 12), ("bonus", 4)],
}
OCTOPUS_EXTRA_CARDS = 3

CHEST_PICK_TABLE = {
    "easy": [(1, 40), (2, 28), (3, 16), (5, 9), (10, 5), (20, 2)],  # avg 2.79x
    "medium": [(1, 40), (2, 28), (3, 15), (5, 9), (10, 5), (25, 2), (50, 1)],  # avg 3.36x
    "hard": [(1, 40), (2, 26), (4, 16), (8, 10), (15, 5), (40, 2), (100, 1)],  # avg 4.91x
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def roll(table, rng):
    """table rows are (value, weight)."""
    total = sum(w for _, w in table)
    r = rng.random() * total
    for value, w in table:
        r -= w
        if r < 0:
            return value
    return table[-1][0]


def extra_match_bonus(mode, k):
    ladder = EXTRA_MATCH_LADDER[mode]
    return ladder[min(k, len(ladder)) - 1]


def extra_match_total(mode, extras):
    return round(sum(extra_match_bonus(mode, k) for k in range(1, extras + 1)), 2)


def card_match_win(mode, panels):
    """Every paying symbol with 3+ pays its multiplier + its extra-match bonus."""
    pay = PAYING[mode]
    counts = {}
    for name in panels:
        if name in pay:
            counts[name] = counts.get(name, 0) + 1
    total = 0.0
    for name, c in counts.items():
        if c >= 3:
            total += pay[name] + extra_match_total(mode, c - 3)
    return round(total, 2)


class Weights:
    """Per-mode draw weights (name -> weight), with a fast sampler."""

    def __init__(self, table: dict[str, float]):
        self.table = dict(table)
        self._rebuild()

    def _rebuild(self):
        self.names = list(self.table)
        self.cum = []
        acc = 0.0
        for n in self.names:
            acc += self.table[n]
            self.cum.append(acc)
        self.total = acc
        # plain-card sampler (no feature symbols)
        self.plain_names = [n for n in self.names if n not in FEATURE_SYMBOLS]
        self.plain_cum = []
        acc = 0.0
        for n in self.plain_names:
            acc += self.table[n]
            self.plain_cum.append(acc)
        self.plain_total = acc

    def set(self, name, w):
        self.table[name] = w
        self._rebuild()

    def pick(self, rng, plain=False):
        names, cum, total = (self.plain_names, self.plain_cum, self.plain_total) if plain else (self.names, self.cum, self.total)
        r = rng.random() * total
        lo, hi = 0, len(cum) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if cum[mid] > r:
                hi = mid
            else:
                lo = mid + 1
        return names[lo]


# ---------------------------------------------------------------------------
# One card / one round
# ---------------------------------------------------------------------------


@dataclass
class CardResult:
    amount: float  # this card's own win (matches + skull grid + features), per $1
    free_awarded: int
    events: list = field(default_factory=list)
    # stats
    match_win: float = 0.0
    features: dict = field(default_factory=dict)


def deal(mode, weights: Weights, rng, plain=False):
    cols, rows = GRID[mode]
    panels = []
    dealt = set()
    for _ in range(cols * rows):
        name = weights.pick(rng, plain)
        while name in ONE_PER_CARD and name in dealt:
            name = weights.pick(rng, plain)
        dealt.add(name)
        panels.append(name)
    return panels


def kraken_match(mode, rng):
    player = rng.randint(1, KRAKEN_NUMBER_RANGE)
    kraken = rng.randint(1, KRAKEN_NUMBER_RANGE)
    hit = player == kraken
    prize = roll(KRAKEN_PRIZE_TABLE[mode], rng) if hit else 0
    return {"playerNumber": player, "krakenNumber": kraken, "hit": hit, "multiplier": prize, "amount": prize}


def chest_pick(mode, rng):
    table = CHEST_PICK_TABLE[mode]
    win = roll(table, rng)
    # "missed" chests: uniform over the table so the reveal teases
    missed = [table[rng.randrange(len(table))][0] for _ in range(2)]
    return win, missed


def play_card(mode, weights: Weights, rng, game_type="basegame"):
    panels = deal(mode, weights, rng)
    pay = PAYING[mode]
    events = [{"type": "revealPanels", "panels": [{"name": n} for n in panels], "gameType": game_type}]
    feats = {}

    amount = card_match_win(mode, panels)
    match_win = amount
    if all(n == "skull" for n in panels):
        amount += SKULL_GRID_PRIZE[mode]
        feats["skull_grid"] = SKULL_GRID_PRIZE[mode]

    free = FREE_SCRATCHES_AWARDED if panels.count("bonus") >= BONUS_TRIGGER_COUNT else 0
    if free:
        feats["scatter_bonus"] = 1

    if "kraken" in panels:
        k = kraken_match(mode, rng)
        amount += k["amount"]
        feats["kraken"] = k["amount"]
        events.append({"type": "krakenResult", **k})

    key_picks = 1 if "key" in panels else 0

    if "shark" in panels:
        outcome = roll(SHARK_OUTCOMES[mode], rng)
        events.append({"type": "sharkBite", "outcome": outcome})
        feats["shark_" + outcome] = 0.0
        if outcome == "kraken":
            k = kraken_match(mode, rng)
            amount += k["amount"]
            feats["shark_kraken"] = k["amount"]
            events.append({"type": "krakenResult", **k})
        elif outcome == "octopus":
            cards = []
            for _ in range(OCTOPUS_EXTRA_CARDS):
                p = deal(mode, weights, rng, plain=True)
                cards.append({"panels": [{"name": n} for n in p], "win": card_match_win(mode, p)})
            won = round(sum(c["win"] for c in cards), 2)
            amount += won
            feats["shark_octopus"] = won
            events.append({"type": "octopusCards", "cards": cards})
        elif outcome == "pirate":
            best = min(pay, key=lambda s: pay[s])
            for n in panels:
                if n in pay and pay[n] > pay[best]:
                    best = n
            m = pay[best]
            amount += m
            feats["shark_pirate"] = m
            events.append({"type": "pirateBounty", "symbol": best, "multiplier": m, "amount": m})
        elif outcome == "key":
            key_picks += 1
        elif outcome == "bonus":
            free += FREE_SCRATCHES_AWARDED
            feats["shark_bonus"] = 1

    for _ in range(key_picks):
        win, missed = chest_pick(mode, rng)
        amount += win
        feats["chest"] = feats.get("chest", 0) + win
        events.append({"type": "chestPick", "win": win, "missed": missed})

    if free:
        events.append({"type": "freeScratchesAwarded", "count": free})

    return CardResult(amount=round(amount, 2), free_awarded=free, events=events, match_win=match_win, features=feats)


@dataclass
class RoundResult:
    payout: float  # capped, per $1
    uncapped: float
    events: list
    cards: list  # CardResult per card (base first)
    free_cards: int
    bonus_mult: float
    capped: bool


def play_round(mode, weights: Weights, rng, max_win):
    events = []
    base = play_card(mode, weights, rng, "basegame")
    cards = [base]
    events += base.events
    total = base.amount
    queue = base.free_awarded
    bonus_mult = 0.0
    free_played = 0
    while queue > 0 and free_played < MAX_FREE_CARDS:
        queue -= 1
        free_played += 1
        c = play_card(mode, weights, rng, "freegame")
        cards.append(c)
        events += c.events
        queue += c.free_awarded
        if c.amount > 0:
            bonus_mult = round(bonus_mult + c.amount, 2)
            events.append({"type": "bonusMultiplier", "added": c.amount, "total": bonus_mult})
            total += bonus_mult
    if free_played:
        bonus_paid = round(total - base.amount, 2)
        if bonus_paid < BONUS_MINIMUM[mode]:
            top_up = round(BONUS_MINIMUM[mode] - bonus_paid, 2)
            total += top_up
            events.append({"type": "bonusMinimum", "amount": top_up, "minimum": BONUS_MINIMUM[mode]})
    total = round(total, 2)
    payout = min(total, max_win)
    events.append({"type": "setTotalWin", "amount": payout})
    events.append({"type": "finalWin", "amount": payout})
    for i, e in enumerate(events):
        e["index"] = i
    return RoundResult(payout, total, events, cards, free_played, bonus_mult, total > max_win)
