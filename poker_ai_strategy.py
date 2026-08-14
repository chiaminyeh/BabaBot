"""A small, dependency-free adapter of RLCard's Hold'em rule policy.

Reference implementation:
    https://github.com/datamllab/rlcard/blob/master/rlcard/models/limitholdem_rule_models.py

RLCard is MIT-licensed.  This module re-expresses the documented rule-agent
policy for BabaBot's existing no-limit action vocabulary; it does not import
or copy RLCard runtime code.  The policy is intentionally deterministic and
requires no model training or external package.
"""

from dataclasses import dataclass
import secrets


_RANK_VALUES = {
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 7,
    "8": 8,
    "9": 9,
    "T": 10,
    "10": 10,
    "J": 11,
    "Q": 12,
    "K": 13,
    "A": 14,
}
_BROADWAY = {"A", "K", "Q", "J", "T"}
_LOW_BOARD = {"2", "3", "4", "5"}


@dataclass(frozen=True)
class PokerDecision:
    """A canonical decision before it enters PokerGame's dispatcher."""

    action: str
    amount: int = 0
    reason: str = ""


@dataclass(frozen=True)
class PokerDecisionContext:
    """Immutable context snapshot provided to decision strategies."""

    hole_cards: tuple[str, ...]
    community_cards: tuple[str, ...]
    pot: int
    current_bet: int
    highest_bet: int
    stack: int
    last_raise_size: int | None = None


class RLCardRuleStrategy:
    """Deterministic rule policy adapted from RLCard's Hold'em rule agent.

    The source policy uses four semantic actions: raise, call, check, and
    fold.  BabaBot's dispatcher represents a raise as ``bet`` with an
    incremental amount, so this adapter performs only that vocabulary and
    legality translation.  It never debits money itself.
    """

    source_repository = "https://github.com/datamllab/rlcard"
    source_file = (
        "https://github.com/datamllab/rlcard/blob/master/"
        """rlcard/models/limitholdem_rule_models.py"""
    )
    source_license = "MIT"

    def __init__(self, *, big_blind: int = 10, randomize: bool = False):
        if not isinstance(big_blind, int) or big_blind <= 0:
            raise ValueError("big_blind must be a positive integer")
        self.big_blind = big_blind
        self.randomize = bool(randomize)

    def decide(
        self,
        hole_cards_or_context: list[str] | tuple[str, ...] | PokerDecisionContext,
        community_cards: list[str] | tuple[str, ...] | None = None,
        *,
        pot: int | None = None,
        current_bet: int | None = None,
        highest_bet: int | None = None,
        stack: int | None = None,
        last_raise_size: int | None = None,
    ) -> PokerDecision:
        """Return one legal-enough action for the current table state."""
        if isinstance(hole_cards_or_context, PokerDecisionContext):
            ctx = hole_cards_or_context
            hole_cards = list(ctx.hole_cards)
            community = list(ctx.community_cards)
            pot = ctx.pot
            current_bet = ctx.current_bet
            highest_bet = ctx.highest_bet
            stack = ctx.stack
            last_raise_size = ctx.last_raise_size
        else:
            hole_cards = list(hole_cards_or_context)
            community = list(community_cards or [])
            if pot is None or current_bet is None or highest_bet is None or stack is None:
                raise ValueError("pot, current_bet, highest_bet, and stack are required")

        if len(hole_cards) != 2:
            raise ValueError("exactly two hole cards are required")
        if not isinstance(stack, int) or stack < 0:
            raise ValueError("stack must be a non-negative integer")
        if not isinstance(current_bet, int) or current_bet < 0:
            raise ValueError("current_bet must be a non-negative integer")
        if not isinstance(highest_bet, int) or highest_bet < current_bet:
            raise ValueError("highest_bet must be >= current_bet")
        if not isinstance(pot, int) or pot < 0:
            raise ValueError("pot must be a non-negative integer")
        if last_raise_size is not None and (
            not isinstance(last_raise_size, int) or last_raise_size <= 0
        ):
            raise ValueError("last_raise_size must be a positive integer")

        semantic_action = self._source_rule_action(hole_cards, community)
        to_call = highest_bet - current_bet
        # No-limit Hold'em's minimum raise is the size of the previous full
        # raise, not a fresh big-blind increment.  The first raise on a
        # street still defaults to the big blind.
        minimum_raise_size = max(self.big_blind, last_raise_size or self.big_blind)

        # RLCard's fallback behavior is preserved where it matters, then
        # adjusted for short stacks in no-limit Hold'em.
        if semantic_action == "raise":
            minimum_raise_increment = to_call + minimum_raise_size
            if stack <= minimum_raise_increment:
                return PokerDecision("allin", 0, "raise policy with a short stack")
            increment = self._raise_increment(
                pot, minimum_raise_increment, stack
            )
            if increment >= stack:
                return PokerDecision("allin", 0, "raise uses the remaining stack")
            return PokerDecision(
                "bet",
                increment,
                "RLCard rule: raise",
            )

        if semantic_action == "call":
            if to_call == 0:
                return PokerDecision("check", 0, "call is not legal when no bet is faced")
            if stack == to_call:
                return PokerDecision("allin", 0, "call uses the remaining stack")
            if stack > to_call:
                return PokerDecision("call", 0, "RLCard rule: call")
            if stack > 0:
                return PokerDecision("allin", 0, "short-stack call")
            return PokerDecision("fold", 0, "call unavailable")

        if semantic_action == "check":
            if to_call == 0:
                return PokerDecision("check", 0, "RLCard rule: check")
            return PokerDecision("fold", 0, "RLCard fallback: check is illegal facing a bet")

        # Folding is only meaningful when a bet is faced.  When checking is
        # free (for example, the big blind after limpers), keep the hand alive
        # with a check instead of turning a no-cost decision into a fold.
        if to_call == 0:
            return PokerDecision(
                "check", 0, "free check takes precedence over fold policy"
            )
        # The source agent folds when the hand is outside its accepted range.
        return PokerDecision("fold", 0, "RLCard rule: fold")

    def _source_rule_action(
        self, hole_cards: list[str], community_cards: list[str]
    ) -> str:
        """Translate RLCard's rank/suit rules into semantic actions."""
        ranks = [self._rank(card) for card in hole_cards]
        suits = [self._suit(card) for card in hole_cards]
        board_ranks = [self._rank(card) for card in community_cards]
        board_suits = [self._suit(card) for card in community_cards]

        if not community_cards:
            if ranks[0] == ranks[1]:
                return "raise"
            if "A" in ranks:
                other = ranks[1] if ranks[0] == "A" else ranks[0]
                if other in {"K", "Q", "J", "T"} or suits[0] == suits[1]:
                    return "raise"
            if all(rank in _BROADWAY - {"A"} for rank in ranks):
                return "raise"
            return "fold"

        has_pair = ranks[0] == ranks[1]
        has_broadway_ace = "A" in ranks and any(
            rank in {"K", "Q", "J", "T"} for rank in ranks
        )
        has_ace = "A" in ranks
        suited_ace = has_ace and suits[0] == suits[1]

        if has_pair and ranks[0] in board_ranks:
            return "raise"
        elif has_broadway_ace and any(rank in _BROADWAY for rank in board_ranks):
            return "raise"
        elif suited_ace and suits[0] in board_suits:
            return "raise"

        highest_board = max((_RANK_VALUES[rank] for rank in board_ranks), default=0)
        if len(community_cards) == 3:
            if highest_board in {_RANK_VALUES[rank] for rank in _LOW_BOARD}:
                return "check"
            return "call"
        if len(community_cards) in {4, 5}:
            if highest_board in {_RANK_VALUES[rank] for rank in _LOW_BOARD}:
                return "fold"
            return "call"
        return "fold"

    def _raise_increment(
        self, pot: int, minimum_raise_increment: int, stack: int
    ) -> int:
        """Choose a bounded increment while ensuring it actually raises."""
        desired = max(self.big_blind, pot // 2)
        if self.randomize and desired > self.big_blind:
            desired = self.big_blind + secrets.randbelow(
                desired - self.big_blind + 1
            )
        return min(stack, max(desired, minimum_raise_increment))

    @staticmethod
    def _rank(card: str) -> str:
        if not isinstance(card, str) or len(card) < 2:
            raise ValueError("cards must be strings such as 'A♠' or '10♦'")
        rank = card[:-1].upper()
        if rank not in _RANK_VALUES:
            raise ValueError(f"unsupported card rank: {rank}")
        return "T" if rank == "10" else rank

    @staticmethod
    def _suit(card: str) -> str:
        if not isinstance(card, str) or len(card) < 2:
            raise ValueError("cards must be strings such as 'A♠' or '10♦'")
        return card[-1]
