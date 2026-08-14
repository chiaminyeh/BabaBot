from __future__ import annotations

import time
from typing import Any
from poker.models import (
    GameMode,
    Player,
    TableConfig,
    BlindLevel,
    HandResult,
    TournamentRank,
    DEFAULT_TOURNAMENT_BLIND_SCHEDULE,
)


class GameModePolicy:
    """Base policy interface for Cash Game and Tournament."""

    def __init__(self, config: TableConfig):
        self.config = config

    def get_current_blinds(self, hand_number: int) -> tuple[int, int, int]:
        """Return (small_blind, big_blind, ante)."""
        return self.config.small_blind, self.config.big_blind, 0

    def calculate_forced_bets(
        self,
        players: list[Player],
        button_seat: int,
        hand_number: int,
    ) -> dict[int, int]:
        """Calculate forced blinds and antes for each player."""
        raise NotImplementedError

    def on_hand_start(self, hand_number: int, players: list[Player]) -> None:
        pass

    def on_hand_end(
        self, hand_number: int, players: list[Player], result: HandResult
    ) -> None:
        pass

    def can_rebuy(self, player: Player, amount: int) -> tuple[bool, str]:
        raise NotImplementedError

    def can_leave(self, player: Player) -> tuple[bool, str]:
        raise NotImplementedError

    def is_game_over(self, players: list[Player]) -> bool:
        raise NotImplementedError


class CashPolicy(GameModePolicy):
    """Cash Game policy: fixed blinds, table stack escrow, rebuy, sit out / in."""

    def __init__(self, config: TableConfig):
        super().__init__(config)
        self.mode = GameMode.CASH
        self.all_sitting_out_since: float | None = None

    def get_current_blinds(self, hand_number: int) -> tuple[int, int, int]:
        return self.config.small_blind, self.config.big_blind, 0

    def calculate_forced_bets(
        self,
        players: list[Player],
        button_seat: int,
        hand_number: int,
    ) -> dict[int, int]:
        active_players = [
            p for p in sorted(players, key=lambda x: x.seat_index if x.seat_index is not None else 0)
            if not p.sitting_out and (p.stack is None or p.stack > 0)
        ]
        if len(active_players) < 2:
            return {}

        sb_amt, bb_amt, _ = self.get_current_blinds(hand_number)
        bets: dict[int, int] = {}

        # Heads-up: BTN posts SB, other posts BB
        if len(active_players) == 2:
            btn = next((p for p in active_players if p.seat_index == button_seat), active_players[0])
            bb = next(p for p in active_players if p.id != btn.id)
            sb_player, bb_player = btn, bb
        else:
            # 3+ players: clockwise from button
            n = len(players)
            sorted_all = sorted(players, key=lambda x: x.seat_index if x.seat_index is not None else 0)
            active_seats = [p.seat_index for p in active_players if p.seat_index is not None]

            # Next active seat after button is SB
            sb_seat = next(
                ((button_seat + 1 + offset) % n for offset in range(n)
                 if (button_seat + 1 + offset) % n in active_seats),
                active_seats[0]
            )
            bb_seat = next(
                ((sb_seat + 1 + offset) % n for offset in range(n)
                 if (sb_seat + 1 + offset) % n in active_seats and (sb_seat + 1 + offset) % n != sb_seat),
                active_seats[1]
            )
            sb_player = next(p for p in active_players if p.seat_index == sb_seat)
            bb_player = next(p for p in active_players if p.seat_index == bb_seat)

        # Calculate actual amounts (accounting for short stack)
        sb_debit = min(sb_amt, sb_player.stack) if sb_player.stack is not None else sb_amt
        bb_debit = min(bb_amt, bb_player.stack) if bb_player.stack is not None else bb_amt

        bets[sb_player.id] = sb_debit
        bets[bb_player.id] = bb_debit
        return bets

    def on_hand_start(self, hand_number: int, players: list[Player]) -> None:
        for p in players:
            # Apply queued rebuys before dealing
            if p.pending_rebuy > 0:
                if p.stack is not None:
                    p.stack += p.pending_rebuy
                else:
                    p.stack = p.pending_rebuy
                p.pending_rebuy = 0
                p.sitting_out = False
            # Reset per-hand time bank
            p.time_bank_available = True

    def on_hand_end(
        self, hand_number: int, players: list[Player], result: HandResult
    ) -> None:
        # Check consecutive timeouts
        for p in players:
            if p.sit_out_next_hand or p.leave_after_hand:
                p.sitting_out = True
                p.sit_out_next_hand = False
            if p.timeout_streak >= 2 and not p.sitting_out and not p.is_bot:
                p.sitting_out = True

        # Check if all human players are sitting out
        human_players = [p for p in players if not p.is_bot]
        if human_players and all(p.sitting_out for p in human_players):
            if self.all_sitting_out_since is None:
                self.all_sitting_out_since = time.time()
        else:
            self.all_sitting_out_since = None

    def can_rebuy(self, player: Player, amount: int) -> tuple[bool, str]:
        if player.is_bot:
            return False, "Bot stacks are managed automatically."
        min_buy = self.config.min_buy_in_bb * self.config.big_blind
        max_buy = self.config.max_buy_in_bb * self.config.big_blind
        current_total = (player.stack or 0) + player.pending_rebuy
        if current_total + amount > max_buy:
            return False, f"Maximum table stack is {max_buy} ({self.config.max_buy_in_bb} BB)."
        if amount < min_buy and current_total == 0:
            return False, f"Minimum buy-in is {min_buy} ({self.config.min_buy_in_bb} BB)."
        return True, ""

    def can_leave(self, player: Player) -> tuple[bool, str]:
        return True, "You will leave and cash out after the current hand."

    def is_idle_timeout(self) -> bool:
        """Return True if all humans have been sitting out for over 15 minutes."""
        if self.all_sitting_out_since is not None:
            return (time.time() - self.all_sitting_out_since) >= 900  # 15 mins
        return False

    def is_game_over(self, players: list[Player]) -> bool:
        if self.is_idle_timeout():
            return True
        active_with_chips = [
            p for p in players
            if not p.sitting_out and ((p.stack or 0) + p.pending_rebuy > 0)
        ]
        return len(active_with_chips) < 2


class TournamentPolicy(GameModePolicy):
    """Freezeout Tournament policy: prize pool, blind timer, BB ante, eliminations."""

    def __init__(
        self,
        config: TableConfig,
        entrants: list[Player],
        buy_in_fee: int = 100,
    ):
        super().__init__(config)
        self.mode = GameMode.TOURNAMENT
        self.buy_in_fee = buy_in_fee
        self.total_prize_pool = len(entrants) * buy_in_fee
        self.tournament_start_time = time.time()
        self.current_level_index = 0
        self.blind_schedule = config.blind_schedule or list(DEFAULT_TOURNAMENT_BLIND_SCHEDULE)
        self.eliminated_players: list[TournamentRank] = []
        self.num_entrants = len(entrants)

    @property
    def current_blind_level(self) -> BlindLevel:
        if self.current_level_index < len(self.blind_schedule):
            return self.blind_schedule[self.current_level_index]
        return self.blind_schedule[-1]

    def get_current_blinds(self, hand_number: int) -> tuple[int, int, int]:
        level = self.current_blind_level
        return level.small_blind, level.big_blind, level.ante

    def check_and_update_blind_level(self) -> bool:
        """Advance blind level if elapsed time exceeds duration. Called between hands."""
        elapsed = time.time() - self.tournament_start_time
        target_level_idx = min(
            int(elapsed // self.config.blind_level_duration_seconds),
            len(self.blind_schedule) - 1,
        )
        if target_level_idx > self.current_level_index:
            self.current_level_index = target_level_idx
            return True
        return False

    def calculate_forced_bets(
        self,
        players: list[Player],
        button_seat: int,
        hand_number: int,
    ) -> dict[int, int]:
        active_players = [
            p for p in sorted(players, key=lambda x: x.seat_index if x.seat_index is not None else 0)
            if p.finish_rank is None and (p.stack is None or p.stack > 0)
        ]
        if len(active_players) < 2:
            return {}

        sb_amt, bb_amt, ante_amt = self.get_current_blinds(hand_number)
        bets: dict[int, int] = {}

        if len(active_players) == 2:
            btn = next((p for p in active_players if p.seat_index == button_seat), active_players[0])
            bb = next(p for p in active_players if p.id != btn.id)
            sb_player, bb_player = btn, bb
        else:
            n = len(players)
            sorted_all = sorted(players, key=lambda x: x.seat_index if x.seat_index is not None else 0)
            active_seats = [p.seat_index for p in active_players if p.seat_index is not None]

            sb_seat = next(
                ((button_seat + 1 + offset) % n for offset in range(n)
                 if (button_seat + 1 + offset) % n in active_seats),
                active_seats[0]
            )
            bb_seat = next(
                ((sb_seat + 1 + offset) % n for offset in range(n)
                 if (sb_seat + 1 + offset) % n in active_seats and (sb_seat + 1 + offset) % n != sb_seat),
                active_seats[1]
            )
            sb_player = next(p for p in active_players if p.seat_index == sb_seat)
            bb_player = next(p for p in active_players if p.seat_index == bb_seat)

        # Big Blind Ante: BB posts Ante in addition to BB
        bb_total_forced = bb_amt + ante_amt
        sb_debit = min(sb_amt, sb_player.stack) if sb_player.stack is not None else sb_amt
        bb_debit = min(bb_total_forced, bb_player.stack) if bb_player.stack is not None else bb_total_forced

        bets[sb_player.id] = sb_debit
        bets[bb_player.id] = bb_debit
        return bets

    def on_hand_start(self, hand_number: int, players: list[Player]) -> None:
        # Check blind progression between hands
        advanced = self.check_and_update_blind_level()
        if advanced:
            # Refresh time banks for new blind level
            for p in players:
                p.time_bank_available = True

    def on_hand_end(
        self, hand_number: int, players: list[Player], result: HandResult
    ) -> None:
        # Handle eliminations
        busted = [
            p for p in players
            if p.finish_rank is None and (p.stack is not None and p.stack <= 0)
        ]
        if busted:
            # Number of remaining players before these eliminations
            remaining_count = sum(1 for p in players if p.finish_rank is None)
            for idx, p in enumerate(busted):
                # Rank: if 6 players remaining and 2 bust, ranks are 6th and 5th
                rank = remaining_count - idx
                p.finish_rank = rank
                p.folded = True
                self.eliminated_players.append(
                    TournamentRank(
                        player_id=p.id,
                        name=p.name,
                        rank=rank,
                        eliminated_hand=hand_number,
                    )
                )

        # Track away status for consecutive timeouts
        for p in players:
            if p.timeout_streak >= 3 and not p.away and not p.is_bot:
                p.away = True

    def calculate_payouts(self, players: list[Player]) -> list[TournamentRank]:
        """Calculate final payout distribution for 1st, 2nd, etc."""
        # Remaining player is 1st place
        remaining = [p for p in players if p.finish_rank is None]
        all_ranks: list[TournamentRank] = list(self.eliminated_players)
        if remaining:
            winner = remaining[0]
            winner.finish_rank = 1
            all_ranks.append(
                TournamentRank(
                    player_id=winner.id,
                    name=winner.name,
                    rank=1,
                    eliminated_hand=0,
                )
            )

        # Sort ranks from 1st to last
        all_ranks.sort(key=lambda r: r.rank)

        # Payout logic:
        # 2-5 entrants: 1st place = 100%
        # 6-10 entrants: 1st place = 70%, 2nd place = 30%
        if self.num_entrants <= 5:
            if all_ranks:
                all_ranks[0].prize = self.total_prize_pool
        else:
            first_prize = int(self.total_prize_pool * 0.70)
            second_prize = self.total_prize_pool - first_prize
            if len(all_ranks) >= 1:
                all_ranks[0].prize = first_prize
            if len(all_ranks) >= 2:
                all_ranks[1].prize = second_prize

        return all_ranks

    def can_rebuy(self, player: Player, amount: int) -> tuple[bool, str]:
        return False, "Rebuys are not permitted in freezeout tournaments."

    def can_leave(self, player: Player) -> tuple[bool, str]:
        return False, "You cannot leave a tournament with a refund. If you leave, you will fold your hands until eliminated."

    def is_game_over(self, players: list[Player]) -> bool:
        active = [p for p in players if p.finish_rank is None and (p.stack is None or p.stack > 0)]
        return len(active) <= 1
