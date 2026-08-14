from __future__ import annotations

from typing import TYPE_CHECKING, Callable
from poker.models import Player, SidePot, HandResult

if TYPE_CHECKING:
    pass


class PokerChipEscrow:
    """In-table chip ledger for fixed-stack or session-based poker.

    The session buy-in is reserved from the real wallet once, then all bets
    and payouts happen against these virtual chips. This prevents an action
    from spending money earned or lost outside the table while a hand is in
    progress, and keeps the existing BababucksEscrow contract for legacy callers.
    """

    def __init__(self, players: list[Player]):
        self.players = {player.id: player for player in players}
        self._contributions: dict[int, int] = {}
        self._state = "open"

    @property
    def state(self) -> str:
        return self._state

    @property
    def contributions(self) -> dict[int, int]:
        return dict(self._contributions)

    @property
    def total(self) -> int:
        return sum(self._contributions.values())

    def try_debit(self, user_id: int, amount: int) -> bool:
        return self.try_debit_many({user_id: amount})

    def try_debit_many(self, debits: dict[int, int]) -> bool:
        if self._state != "open" or not debits:
            return False
        normalized = {int(uid): int(amount) for uid, amount in debits.items()}
        if any(amount <= 0 for amount in normalized.values()):
            return False
        players = [self.players.get(uid) for uid in normalized]
        if any(player is None or player.stack is None for player in players):
            return False
        if any(player.stack < normalized[player.id] for player in players if player and player.stack is not None):
            return False
        for player in players:
            if player is not None and player.stack is not None:
                amount = normalized[player.id]
                player.stack -= amount
                player.total_hand_contribution += amount
                player.all_in = player.stack == 0
                self._contributions[player.id] = (
                    self._contributions.get(player.id, 0) + amount
                )
        return True

    def return_uncalled_bet(self, user_id: int, amount: int) -> bool:
        """Return uncalled excess wager directly to the player stack."""
        if self._state != "open" or amount <= 0:
            return False
        current = self._contributions.get(user_id, 0)
        if current < amount:
            return False
        self._contributions[user_id] = current - amount
        player = self.players.get(user_id)
        if player is not None and player.stack is not None:
            player.stack += amount
            player.total_hand_contribution = max(0, player.total_hand_contribution - amount)
            if player.stack > 0:
                player.all_in = False
        return True

    def refund(self) -> bool:
        if self._state != "open":
            return False
        for uid, amount in self._contributions.items():
            player = self.players.get(uid)
            if player is not None and player.stack is not None:
                player.stack += amount
        self._state = "refunded"
        return True

    def settle(self, payouts: dict[int, int]) -> bool:
        if self._state != "open":
            return False
        if not isinstance(payouts, dict) or any(
            int(amount) < 0 for amount in payouts.values()
        ):
            return False
        if sum(int(amount) for amount in payouts.values()) != self.total:
            return False
        for uid, amount in payouts.items():
            player = self.players.get(int(uid))
            if player is not None and player.stack is not None:
                player.stack += int(amount)
        self._state = "settled"
        return True


class PotManager:
    """Calculates uncalled bets, side pots, pot splits, and odd-chip allocations."""

    @staticmethod
    def calculate_uncalled_bet(
        players: list[Player], contributions: dict[int, int]
    ) -> tuple[int | None, int]:
        """Determine if the highest active bettor is uncalled and by how much.

        Returns (player_id, uncalled_amount) or (None, 0).
        """
        active_players = [p for p in players if not p.folded and contributions.get(p.id, 0) > 0]
        if not active_players:
            return None, 0
        if len(active_players) == 1:
            winner = active_players[0]
            winner_amount = contributions.get(winner.id, 0)
            matched_amount = max(
                (
                    amount
                    for player_id, amount in contributions.items()
                    if player_id != winner.id
                ),
                default=0,
            )
            if winner_amount > matched_amount:
                return winner.id, winner_amount - matched_amount
            return None, 0

        active_contribs = sorted(
            [(p.id, contributions.get(p.id, 0)) for p in active_players],
            key=lambda item: item[1],
            reverse=True,
        )
        highest_id, highest_amt = active_contribs[0]
        second_amt = active_contribs[1][1]

        if highest_amt > second_amt:
            uncalled = highest_amt - second_amt
            return highest_id, uncalled
        return None, 0

    @staticmethod
    def build_side_pots(
        players: list[Player], contributions: dict[int, int]
    ) -> list[SidePot]:
        """Construct main pot and side pots from all players' contributions.

        Unequal contributions form side pots when active players are all-in with
        different chip counts.
        """
        active = [
            p for p in players
            if not p.folded and contributions.get(p.id, 0) > 0
        ]
        if not active:
            return []

        # If no active player is all-in, every active player has matched the current wager.
        # It's a single main pot.
        if not any(p.all_in for p in active):
            eligible_ids = [p.id for p in active]
            contributor_ids = [
                p.id for p in players if contributions.get(p.id, 0) > 0
            ]
            return [
                SidePot(
                    amount=sum(contributions.values()),
                    eligible_player_ids=eligible_ids,
                    contributor_ids=contributor_ids,
                )
            ]

        levels = sorted({contributions[p.id] for p in active if contributions.get(p.id, 0) > 0})
        pots: list[SidePot] = []
        previous_level = 0

        for level in levels:
            pot_contributors = [
                p.id for p in players
                if contributions.get(p.id, 0) >= level
            ]
            pot_amount = (level - previous_level) * len(pot_contributors)
            previous_level = level

            eligible_active = [
                p.id for p in active
                if contributions.get(p.id, 0) >= level
            ]
            if pot_amount > 0 and eligible_active:
                pots.append(
                    SidePot(
                        amount=pot_amount,
                        eligible_player_ids=eligible_active,
                        contributor_ids=pot_contributors,
                    )
                )

        return pots

    @staticmethod
    def resolve_showdown_payouts(
        players: list[Player],
        contributions: dict[int, int],
        rank_fn: Callable[[list[str]], Any],
        compare_fn: Callable[[Any, Any], int],
        community: list[str],
        button_seat: int = 0,
    ) -> tuple[dict[int, int], list[SidePot]]:
        """Evaluate hands for each pot, divide chips, and assign odd chips.

        Odd chips are given one each to tied winners starting from the first
        eligible player to the left of the button (clockwise from button).
        """
        active = [
            p for p in players
            if not p.folded and contributions.get(p.id, 0) > 0
        ]
        if not active:
            return {}, []

        side_pots = PotManager.build_side_pots(players, contributions)
        if not side_pots:
            return {}, []

        scores = {
            p.id: rank_fn(p.hand + community)
            for p in active
        }

        # Order players clockwise starting from left of button
        num_seats = max(len(players), 1)
        player_by_id = {p.id: p for p in players}

        def clockwise_distance(p_id: int) -> int:
            p = player_by_id.get(p_id)
            if p is None or p.seat_index is None:
                return 999
            return (p.seat_index - button_seat - 1) % num_seats

        payouts: dict[int, int] = {}

        for pot in side_pots:
            eligible = [
                player_by_id[pid] for pid in pot.eligible_player_ids
                if pid in player_by_id
            ]
            if not eligible:
                continue

            winners: list[Player] = []
            best_score = None
            for p in eligible:
                score = scores[p.id]
                if best_score is None or compare_fn(score, best_score) > 0:
                    best_score = score
                    winners = [p]
                elif compare_fn(score, best_score) == 0:
                    winners.append(p)

            pot.winners = [p.id for p in winners]
            share, remainder = divmod(pot.amount, len(winners))

            # Sort tied winners by clockwise distance from button
            sorted_winners = sorted(winners, key=lambda p: clockwise_distance(p.id))

            for index, winner in enumerate(sorted_winners):
                win_amount = share + (1 if index < remainder else 0)
                pot.payouts[winner.id] = pot.payouts.get(winner.id, 0) + win_amount
                payouts[winner.id] = payouts.get(winner.id, 0) + win_amount

        total_escrow = sum(contributions.values())
        if sum(payouts.values()) != total_escrow:
            raise RuntimeError(
                f"Side-pot resolution total {sum(payouts.values())} != escrow total {total_escrow}"
            )

        return payouts, side_pots
