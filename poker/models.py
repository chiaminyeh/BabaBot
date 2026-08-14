from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import secrets
import time
from typing import Any


class GameMode(str, Enum):
    CASH = "cash"
    TOURNAMENT = "tournament"


class Street(str, Enum):
    PREFLOP = "preflop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN = "showdown"


class TableStatus(str, Enum):
    LOBBY = "lobby"
    PLAYING = "playing"
    PAUSED = "paused"
    CLOSED = "closed"


class ActionType(str, Enum):
    FOLD = "fold"
    CHECK = "check"
    CALL = "call"
    BET = "bet"
    RAISE = "raise"
    ALL_IN = "allin"
    TIME_BANK = "time_bank"
    TIMEOUT = "timeout"


_SUITS = ["♠", "♥", "♦", "♣"]
_RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]


class Card:
    """Card utility class with suit, rank, and numeric value."""

    def __init__(self, rank: str, suit: str):
        self.rank = rank
        self.suit = suit

    @classmethod
    def from_str(cls, card_str: str) -> Card:
        if not isinstance(card_str, str) or len(card_str) < 2:
            raise ValueError(f"Invalid card string: {card_str}")
        suit = card_str[-1]
        rank = card_str[:-1]
        return cls(rank, suit)

    @property
    def value(self) -> int:
        rank_values = {
            "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
            "10": 10, "T": 10, "J": 11, "Q": 12, "K": 13, "A": 14
        }
        return rank_values.get(self.rank, 0)

    def __str__(self) -> str:
        return f"{self.rank}{self.suit}"

    def __repr__(self) -> str:
        return f"Card({self.rank}{self.suit})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Card):
            return self.rank == other.rank and self.suit == other.suit
        if isinstance(other, str):
            return str(self) == other
        return False

    def __hash__(self) -> int:
        return hash((self.rank, self.suit))


class Deck:
    """Standard 52-card poker deck."""

    def __init__(self):
        self.cards = [f"{rank}{suit}" for suit in _SUITS for rank in _RANKS]
        self.shuffle()

    def shuffle(self):
        secrets.SystemRandom().shuffle(self.cards)

    def deal(self, count: int = 1) -> list[str]:
        if len(self.cards) < count:
            raise ValueError("Not enough cards in deck")
        dealt = self.cards[:count]
        self.cards = self.cards[count:]
        return dealt

    def burn(self) -> str | None:
        if self.cards:
            return self.cards.pop(0)
        return None


@dataclass
class Player:
    user: Any
    id: int = field(init=False)
    name: str = field(init=False)
    is_bot: bool = False
    seat_index: int | None = None
    position: str = ""
    stack: int | None = None
    bet: int = 0
    total_hand_contribution: int = 0
    folded: bool = False
    all_in: bool = False
    sitting_out: bool = False
    sit_out_next_hand: bool = False
    leave_after_hand: bool = False
    pending_rebuy: int = 0
    time_bank_available: bool = True
    timeout_streak: int = 0
    away: bool = False
    hand: list[str] = field(default_factory=list)
    finish_rank: int | None = None

    def __post_init__(self):
        self.id = getattr(self.user, "id", 0)
        self.name = getattr(self.user, "name", "Unknown")

    def reset_for_hand(self):
        self.hand = []
        self.bet = 0
        self.total_hand_contribution = 0
        self.folded = self.sitting_out or (self.stack is not None and self.stack <= 0)
        self.all_in = False

    def reset_for_street(self):
        self.bet = 0


@dataclass
class ActionLogEntry:
    text: str
    timestamp: float = field(default_factory=time.time)
    player_name: str = ""
    action: str = ""


@dataclass
class RaisePreset:
    label: str
    target_total: int
    is_all_in: bool = False


@dataclass
class LegalActionSummary:
    can_fold: bool = True
    can_check: bool = False
    can_call: bool = False
    call_amount: int = 0
    can_raise: bool = False
    min_raise_to: int = 0
    max_raise_to: int = 0
    can_all_in: bool = False
    all_in_amount: int = 0
    can_time_bank: bool = False
    presets: list[RaisePreset] = field(default_factory=list)


@dataclass
class SidePot:
    amount: int
    eligible_player_ids: list[int]
    contributor_ids: list[int]
    winners: list[int] = field(default_factory=list)
    payouts: dict[int, int] = field(default_factory=dict)


@dataclass
class HandResult:
    winners: list[int]
    payouts: dict[int, int]
    uncalled_bet_return: dict[int, int] = field(default_factory=dict)
    player_hand_names: dict[int, str] = field(default_factory=dict)
    side_pots: list[SidePot] = field(default_factory=list)
    summary_text: str = ""


@dataclass
class TournamentRank:
    player_id: int
    name: str
    rank: int
    prize: int = 0
    eliminated_hand: int = 0


@dataclass
class BlindLevel:
    level: int
    small_blind: int
    big_blind: int
    ante: int = 0


DEFAULT_TOURNAMENT_BLIND_SCHEDULE: list[BlindLevel] = [
    BlindLevel(level=1, small_blind=5, big_blind=10, ante=0),
    BlindLevel(level=2, small_blind=10, big_blind=20, ante=0),
    BlindLevel(level=3, small_blind=15, big_blind=30, ante=0),
    BlindLevel(level=4, small_blind=25, big_blind=50, ante=50),  # BB Ante
    BlindLevel(level=5, small_blind=50, big_blind=100, ante=100),
    BlindLevel(level=6, small_blind=75, big_blind=150, ante=150),
    BlindLevel(level=7, small_blind=100, big_blind=200, ante=200),
    BlindLevel(level=8, small_blind=150, big_blind=300, ante=300),
    BlindLevel(level=9, small_blind=200, big_blind=400, ante=400),
    BlindLevel(level=10, small_blind=300, big_blind=600, ante=600),
]


@dataclass
class TableConfig:
    mode: GameMode = GameMode.CASH
    code: str = ""
    language: str = "en"
    small_blind: int = 5
    big_blind: int = 10
    min_buy_in_bb: int = 20
    max_buy_in_bb: int = 200
    default_buy_in_bb: int = 100
    starting_stack: int = 1000
    buy_in_amount: int = 1000
    blind_level_duration_seconds: int = 600  # 10 minutes
    max_seats: int = 10
    min_seats: int = 2
    action_timeout_seconds: int = 30
    time_bank_seconds: int = 30
    blind_schedule: list[BlindLevel] = field(
        default_factory=lambda: list(DEFAULT_TOURNAMENT_BLIND_SCHEDULE)
    )
