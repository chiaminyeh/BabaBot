from __future__ import annotations

import asyncio
import inspect
import logging
import secrets
import time
from typing import Any, Callable, Coroutine
import discord

from poker.models import (
    Card,
    Deck,
    Player,
    Street,
    GameMode,
    TableStatus,
    TableConfig,
    ActionLogEntry,
    LegalActionSummary,
    HandResult,
    TournamentRank,
)
from poker.pots import PokerChipEscrow, PotManager
from poker.rules import HandEvaluator, TableRules
from poker.modes import CashPolicy, TournamentPolicy, GameModePolicy
from poker.timers import ActionClockController, GenerationToken
from poker.pacing import PacingController
from poker.ui import TableEmbedBuilder, PokerActionView
from poker_ai_strategy import RLCardRuleStrategy, PokerDecisionContext


class _AsyncRLock:
    """A task-reentrant asyncio lock for one PokerGame state machine."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._owner = None
        self._depth = 0

    async def __aenter__(self):
        task = asyncio.current_task()
        if task is self._owner:
            self._depth += 1
            return self
        await self._lock.acquire()
        self._owner = task
        self._depth = 1
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        task = asyncio.current_task()
        if task is not self._owner:
            raise RuntimeError("Poker state lock released by a different task")
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
            self._lock.release()


class PokerGameInstance:
    """Live No-Limit Hold'em game coordinator for Cash Game or Tournament."""

    def __init__(
        self,
        channel: Any,
        players: list[Player],
        cog: Any,
        *,
        hand_id: str | None = None,
        button_seat: int = 0,
        config: TableConfig | None = None,
        starting_stack: int | None = None,
    ):
        self.channel = channel
        self.channel_id = getattr(channel, "id", 0)
        self.guild_id = getattr(getattr(channel, "guild", None), "id", 0)
        self.cog = cog
        self.money_name = getattr(cog, "money_name", "bababucks") if cog else "bababucks"

        self.config = config or TableConfig()
        if starting_stack is not None:
            self.config.starting_stack = starting_stack
            for p in players:
                if p.stack is None:
                    p.stack = starting_stack

        self.players = players
        for idx, p in enumerate(self.players):
            p.seat_index = idx

        self.button_seat = button_seat % max(len(players), 1)
        self.hand_number = 1
        self.hand_id = hand_id or f"hand-{secrets.token_hex(4)}"
        self.escrow_id = f"poker:{self.guild_id}:{self.hand_id}"

        # Policies
        if self.config.mode == GameMode.TOURNAMENT:
            self.policy: GameModePolicy = TournamentPolicy(
                self.config,
                self.players,
                buy_in_fee=self.config.buy_in_amount,
            )
            self.tournament_policy: TournamentPolicy | None = self.policy  # type: ignore
        else:
            self.policy = CashPolicy(self.config)
            self.tournament_policy = None

        self.action_clock = ActionClockController(
            table_id=str(self.channel_id),
            timeout_seconds=self.config.action_timeout_seconds,
            time_bank_seconds=self.config.time_bank_seconds,
        )
        self.pacing = PacingController()
        self.bot_strategy = RLCardRuleStrategy(big_blind=self.config.big_blind)
        self._current_small_blind = self.config.small_blind
        self._current_big_blind = self.config.big_blind
        self._current_ante = 0

        # Locks
        self._state_lock = _AsyncRLock()
        self._terminal_lock = asyncio.Lock()

        # State
        self.street = "preflop"
        self.round = 0
        self.deck = Deck()
        self.community: list[str] = []
        self.burned: list[str] = []
        self.highest = self.config.big_blind
        self.last_raise_size = self.config.big_blind
        self.acted_players: set[int] = set()
        self.current_actor_id: int | None = None
        self.terminal_state = "open"
        self.action_log: list[ActionLogEntry] = []
        self.last_action = ""
        self.last_result = ""
        self.last_result_summary = ""
        self.last_showdown_hands: list[str] = []
        self.last_payouts: dict[int, int] = {}
        self.last_side_pots = []
        self.tournament_ranks: list[TournamentRank] = []
        self.last_showdown: list[str] = []

        # Table message
        self.table_message: discord.Message | None = None
        self.current_view: PokerActionView | None = None
        self.table_escrow: Any = None

        # Escrow model
        if all(p.stack is None for p in self.players) and hasattr(self.cog, "baba") and hasattr(self.cog.baba, "new_escrow"):
            self.escrow = self.cog.baba.new_escrow(
                escrow_id=self.escrow_id, game_type="poker"
            )
        else:
            self.escrow = PokerChipEscrow(self.players)

        self._forced_bets_posted = False
        self._bot_turn_running = False
        self._next_hand_task: asyncio.Task | None = None

    @property
    def pot(self) -> int:
        if self.terminal_state != "open":
            return 0
        return self.escrow.total

    @pot.setter
    def pot(self, val: int):
        pass

    @property
    def pot_total(self) -> int:
        if self.terminal_state != "open":
            return 0
        return self.escrow.total

    @property
    def highest_bet(self) -> int:
        return self.highest

    @highest_bet.setter
    def highest_bet(self, val: int):
        self.highest = val

    @property
    def community_cards(self) -> list[str]:
        return self.community

    @community_cards.setter
    def community_cards(self, val: list[str]):
        self.community = val

    @property
    def current_actor(self) -> Player | None:
        if self.current_actor_id is None:
            return None
        return self.get_player(self.current_actor_id)

    @property
    def big_blind(self) -> int:
        return self._current_big_blind

    @property
    def small_blind(self) -> int:
        return self._current_small_blind

    def get_player(self, player_id: int) -> Player | None:
        for p in self.players:
            if p.id == player_id:
                return p
        return None

    def _player_for(self, player_id: int | None) -> Player | None:
        if player_id is None:
            return None
        return self.get_player(player_id)

    def _stack_for(self, player: Player) -> int:
        if player.stack is not None:
            return player.stack
        if hasattr(self.cog, "baba") and hasattr(self.cog.baba, "get_money"):
            return self.cog.baba.get_money(player.id)
        return 0

    def touch_activity(self):
        guild_id = self.guild_id
        game = getattr(self.cog, "games", {}).get(guild_id)
        if game:
            from datetime import datetime
            game["last_action"] = datetime.now()

    def log_action(self, text: str, player_name: str = "", action: str = ""):
        self.last_action = text
        self.action_log.append(
            ActionLogEntry(text=text, player_name=player_name, action=action)
        )
        if len(self.action_log) > 20:
            self.action_log.pop(0)

    def rank_hand(self, cards: list[str]) -> tuple[int, list[int]]:
        return HandEvaluator.rank_hand(cards)

    def compare(self, score1: tuple[int, list[int]], score2: tuple[int, list[int]]) -> int:
        return HandEvaluator.compare(score1, score2)

    def hand_name(self, score: tuple[int, list[int]], lang: str = "en") -> str:
        return HandEvaluator.hand_name(score, lang=lang)

    def get_legal_actions(self, player: Player) -> LegalActionSummary:
        return TableRules.calculate_legal_actions(
            player=player,
            highest_bet=self.highest,
            big_blind=self.big_blind,
            last_raise_size=self.last_raise_size,
            acted_players=self.acted_players,
            pot=self.escrow.total,
        )

    def action_order_for(self, street: str) -> list[int]:
        return TableRules.get_action_order(self.players, self.button_seat, street)

    def _next_actor_after(self, player_id: int) -> int | None:
        street_str = self.street.value if hasattr(self.street, "value") else str(self.street)
        order = self.action_order_for(street_str)
        if not order:
            return None
        if player_id in order:
            idx = order.index(player_id)
            next_candidates = order[idx + 1:] + order[:idx + 1]
        else:
            next_candidates = order
        for pid in next_candidates:
            p = self.get_player(pid)
            if p and not p.folded and not p.all_in and pid not in self.acted_players:
                return pid
        for pid in next_candidates:
            p = self.get_player(pid)
            if p and not p.folded and not p.all_in and p.bet < self.highest:
                return pid
        return None

    def post_forced_bets(self) -> bool:
        if self._forced_bets_posted:
            return False

        active_seats = [
            p.seat_index for p in self.players
            if not p.sitting_out and (p.stack is None or p.stack > 0) and p.finish_rank is None
        ]
        if len(active_seats) < 2:
            return False

        self.button_seat = min(
            active_seats,
            key=lambda seat: (seat - self.button_seat) % len(self.players),
        )
        TableRules.assign_positions(self.players, self.button_seat)
        active_sorted = [
            p for p in sorted(self.players, key=lambda x: x.seat_index if x.seat_index is not None else 0)
            if not p.sitting_out
            and p.finish_rank is None
            and (p.stack is None or p.stack > 0)
        ]

        # Calculate SB and BB players
        if len(active_sorted) == 2:
            btn = next((p for p in active_sorted if p.seat_index == self.button_seat), active_sorted[0])
            bb = next(p for p in active_sorted if p.id != btn.id)
            sb_player, bb_player = btn, bb
        else:
            n = len(self.players)
            sb_seat = next(
                ((self.button_seat + 1 + offset) % n for offset in range(n)
                 if (self.button_seat + 1 + offset) % n in active_seats),
                active_seats[0]
            )
            bb_seat = next(
                ((sb_seat + 1 + offset) % n for offset in range(n)
                 if (sb_seat + 1 + offset) % n in active_seats and (sb_seat + 1 + offset) % n != sb_seat),
                active_seats[1]
            )
            sb_player = next(p for p in active_sorted if p.seat_index == sb_seat)
            bb_player = next(p for p in active_sorted if p.seat_index == bb_seat)

        sb_amt, bb_amt, ante_amt = self.policy.get_current_blinds(self.hand_number)
        self._current_small_blind = sb_amt
        self._current_big_blind = bb_amt
        self._current_ante = ante_amt
        sb_avail = self._stack_for(sb_player)
        bb_avail = self._stack_for(bb_player)

        sb_debit = min(sb_amt, sb_avail)
        bb_debit = min(bb_amt + ante_amt, bb_avail)

        forced_bets = {sb_player.id: sb_debit, bb_player.id: bb_debit}

        # Debit atomically
        if hasattr(self.escrow, "try_debit_many"):
            success = self.escrow.try_debit_many(forced_bets)
        else:
            success = True
            for uid, amt in forced_bets.items():
                if not self.escrow.try_debit(uid, amt):
                    success = False
                    break

        if not success:
            return False

        sb_player.bet = sb_debit
        # A big-blind ante contributes to the pot but is not a live wager.
        # Short stacks post their live blind first; only the remainder is ante.
        bb_player.bet = min(bb_amt, bb_debit)

        if self._stack_for(sb_player) == 0:
            sb_player.all_in = True
        if self._stack_for(bb_player) == 0:
            bb_player.all_in = True

        self.highest = max((p.bet for p in self.players), default=self.big_blind)
        self.last_raise_size = self.big_blind
        self._forced_bets_posted = True

        preflop_order = self.action_order_for("preflop")
        self.current_actor_id = preflop_order[0] if preflop_order else None
        return True

    async def play_game(self):
        self.policy.on_hand_start(self.hand_number, self.players)
        (
            self._current_small_blind,
            self._current_big_blind,
            self._current_ante,
        ) = self.policy.get_current_blinds(self.hand_number)
        self.bot_strategy = RLCardRuleStrategy(big_blind=self.big_blind)
        for p in self.players:
            p.reset_for_hand()

        self.deck = Deck()
        for p in self.players:
            if not p.folded and p.finish_rank is None:
                p.hand = self.deck.deal(2)

        if not self.post_forced_bets():
            await self.refund_game("could not post forced bets")
            return

        self.log_action(f"Hand #{self.hand_number} started. Blinds posted.")
        await self._start_action_clock_for_current_actor()
        await self._refresh_board()

        if self.current_actor and self.current_actor.is_bot:
            asyncio.create_task(self.run_bot_turns())

    async def _start_action_clock_for_current_actor(self):
        actor = self.current_actor
        if not actor or actor.is_bot or self.terminal_state != "open":
            self.action_clock.cancel()
            return

        async def _timeout_callback(token: GenerationToken):
            await self.handle_timeout(token)

        self.action_clock.start_turn(
            hand_id=self.hand_id,
            actor_id=actor.id,
            on_timeout=_timeout_callback,
        )

    async def handle_timeout(self, token: GenerationToken):
        async with self._state_lock:
            if (
                self.terminal_state != "open"
                or self.current_actor_id != token.actor_id
                or not self.action_clock.current_token
                or not self.action_clock.current_token.matches(
                    token.table_id, token.hand_id, token.action_sequence, token.actor_id
                )
            ):
                return

            player = self.get_player(token.actor_id)
            if not player or player.folded:
                return

            legal = self.get_legal_actions(player)
            player.timeout_streak += 1

            if legal.can_check:
                self.log_action(f"{player.name} timed out → checked", player.name, "check")
                await self._dispatch_action_unlocked(
                    player, "check", from_timeout=True
                )
            else:
                self.log_action(f"{player.name} timed out → folded", player.name, "fold")
                await self._dispatch_action_unlocked(
                    player, "fold", from_timeout=True
                )

    async def use_time_bank(self, player: Player) -> bool:
        async with self._state_lock:
            if (
                self.terminal_state != "open"
                or self.current_actor_id != player.id
                or not player.time_bank_available
            ):
                return False

            async def _timeout_callback(token: GenerationToken):
                await self.handle_timeout(token)

            extended, token = self.action_clock.extend_time_bank(_timeout_callback)
            if extended:
                player.time_bank_available = False
                self.log_action(f"{player.name} used Time Bank (+30s)", player.name, "time_bank")
                await self._refresh_board()
                return True
            return False

    async def handle_player_action(
        self,
        player: Player,
        action: str,
        *,
        amount: int = 0,
        sender: Any = None,
    ) -> bool:
        async with self._state_lock:
            return await self._dispatch_action_unlocked(
                player, action, amount=amount, sender=sender
            )

    async def place_bet(self, ctx: Any, amount: int) -> bool:
        player = self._player_for(getattr(ctx.author, "id", None))
        if not player:
            return False
        sender = ctx.send if hasattr(ctx, "send") else None
        return await self.handle_player_action(player, "bet", amount=amount, sender=sender)

    async def call(self, ctx: Any) -> bool:
        player = self._player_for(getattr(ctx.author, "id", None))
        if not player:
            return False
        sender = ctx.send if hasattr(ctx, "send") else None
        return await self.handle_player_action(player, "call", sender=sender)

    async def check(self, ctx: Any) -> bool:
        player = self._player_for(getattr(ctx.author, "id", None))
        if not player:
            return False
        sender = ctx.send if hasattr(ctx, "send") else None
        return await self.handle_player_action(player, "check", sender=sender)

    async def fold(self, ctx: Any) -> bool:
        player = self._player_for(getattr(ctx.author, "id", None))
        if not player:
            return False
        sender = ctx.send if hasattr(ctx, "send") else None
        return await self.handle_player_action(player, "fold", sender=sender)

    async def allin(self, ctx: Any) -> bool:
        player = self._player_for(getattr(ctx.author, "id", None))
        if not player:
            return False
        sender = ctx.send if hasattr(ctx, "send") else None
        return await self.handle_player_action(player, "allin", sender=sender)

    async def raise_bet(self, player: Player, target_total: int, *, sender: Any = None) -> bool:
        async with self._state_lock:
            return await self._raise_bet_unlocked(player, target_total, sender=sender)

    async def _raise_bet_unlocked(self, player: Player, target_total: int, *, sender: Any = None) -> bool:
        if (
            self.terminal_state != "open"
            or player.folded
            or player.id != self.current_actor_id
        ):
            return await self._dispatch_action_unlocked(player, "raise", amount=0, sender=sender)

        if player.id in self.acted_players:
            if sender:
                await self._send_msg(sender, "Betting has not reopened for you; you may call, check, or fold.")
            return False

        if target_total <= self.highest:
            if sender:
                await self._send_msg(sender, f"A raise must be above the current bet of {self.highest}.")
            return False

        min_raise_to = self.highest + max(self.big_blind, self.last_raise_size)
        if target_total < min_raise_to:
            if sender:
                await self._send_msg(sender, f"Minimum raise is to {min_raise_to}.")
            return False

        if target_total <= player.bet:
            if sender:
                await self._send_msg(sender, "Your total bet must increase when you raise.")
            return False

        delta = target_total - player.bet
        return await self._dispatch_action_unlocked(player, "raise", amount=delta, sender=sender)

    async def _send_msg(self, sender: Any, text: str):
        if not sender:
            return
        try:
            res = sender(text)
            if inspect.isawaitable(res):
                await res
        except Exception:
            logging.debug("Action announcement message failed", exc_info=True)

    async def _dispatch_action(self, player: Player, action: str, *, amount: int = 0, sender: Any = None) -> bool:
        async with self._state_lock:
            return await self._dispatch_action_unlocked(player, action, amount=amount, sender=sender)

    async def _dispatch_action_unlocked(
        self,
        player: Player,
        action: str,
        *,
        amount: int = 0,
        sender: Any = None,
        from_timeout: bool = False,
    ) -> bool:
        if self.terminal_state != "open":
            if sender:
                await self._send_msg(sender, "This hand has already ended.")
            return False

        if player.folded:
            if sender:
                await self._send_msg(sender, "You are not in the hand or already folded.")
            return False

        if player.id != self.current_actor_id:
            expected = self.current_actor
            if sender:
                await self._send_msg(
                    sender,
                    f"It is {expected.name}'s turn." if expected else "It is not your turn."
                )
            return False

        if not player.is_bot and not from_timeout:
            player.timeout_streak = 0
            player.away = False

        prev_highest = self.highest
        prev_raise_size = max(self.big_blind, self.last_raise_size)
        full_raise = False
        available = self._stack_for(player)

        if action in ("bet", "raise"):
            if amount < 1:
                if sender:
                    await self._send_msg(sender, "Bet must be positive.")
                return False

            is_all_in = amount == available
            target_total = player.bet + amount

            if target_total > prev_highest and player.id in self.acted_players:
                if sender:
                    await self._send_msg(sender, "Betting has not reopened for you; you may call, check, or fold.")
                return False

            if target_total < self.big_blind and not is_all_in and self.street != "preflop" and prev_highest == 0:
                if sender:
                    await self._send_msg(sender, f"The minimum opening bet is {self.big_blind}.")
                return False

            if target_total < self.highest and not is_all_in:
                if sender:
                    await self._send_msg(sender, f"Your bet must at least match the current bet of {self.highest}.")
                return False

            if (
                target_total > prev_highest
                and not is_all_in
                and target_total < prev_highest + prev_raise_size
            ):
                if sender:
                    await self._send_msg(
                        sender,
                        f"Minimum raise is to {prev_highest + prev_raise_size}.",
                    )
                return False

            if not self.escrow.try_debit(player.id, amount):
                if sender:
                    await self._send_msg(sender, "Insufficient funds to raise." if action == "raise" else "Insufficient funds.")
                return False

            player.bet += amount
            raise_increment = player.bet - prev_highest
            if raise_increment >= prev_raise_size:
                self.last_raise_size = raise_increment
                full_raise = True

            if player.bet > self.highest:
                self.highest = player.bet

            if self._stack_for(player) == 0:
                player.all_in = True

            if action == "raise" or prev_highest > 0:
                self.log_action(
                    f"{player.name} raises to {player.bet} · pot {self.escrow.total}",
                    player.name,
                    "raise",
                )
            else:
                self.log_action(
                    f"{player.name} bets {amount} · pot {self.escrow.total}",
                    player.name,
                    "bet",
                )

        elif action == "call":
            difference = self.highest - player.bet
            if difference <= 0:
                if sender:
                    await self._send_msg(sender, "Nothing to call—use `baba check` to check.")
                return False

            call_amt = min(difference, available)
            if call_amt <= 0 or not self.escrow.try_debit(player.id, call_amt):
                if sender:
                    await self._send_msg(sender, "Insufficient to call.")
                return False

            player.bet += call_amt
            if call_amt < difference or self._stack_for(player) == 0:
                player.all_in = True

            self.log_action(
                f"{player.name} calls {call_amt} · pot {self.escrow.total}",
                player.name,
                "call",
            )

        elif action == "check":
            if player.bet != self.highest:
                if sender:
                    await self._send_msg(sender, "You can’t check until you’ve matched the highest bet.")
                return False

            self.log_action(f"{player.name} checks", player.name, "check")

        elif action == "fold":
            player.folded = True
            self.log_action(f"{player.name} folds", player.name, "fold")
            active = [p for p in self.players if not p.folded]
            if len(active) == 1:
                winner = active[0]
                uncalled = self._return_uncalled_bet()
                won_pot = self.escrow.total
                settled = await self.settle_game({winner.id: won_pot}, "fold")
                if not settled:
                    player.folded = False
                    if sender:
                        await self._send_msg(sender, "This hand has already ended.")
                    return False
                self.last_result = f"🎉 {winner.name} wins {won_pot} {self.money_name}"
                self.last_result_summary = f"{winner.name} won {won_pot} {self.money_name}"
                self.last_payouts = {winner.id: won_pot}
                self._stop_current_view()
                await self._refresh_board()
                await self._after_hand(
                    HandResult(
                        winners=[winner.id],
                        payouts={winner.id: won_pot},
                        uncalled_bet_return=uncalled,
                        summary_text=self.last_result_summary,
                    ),
                    "fold",
                )
                return True

        elif action == "allin":
            available = self._stack_for(player)
            if available <= 0:
                player.all_in = True
                self.acted_players.add(player.id)
                self.current_actor_id = self._next_actor_after(player.id)
                return await self._maybe_advance_round()

            target_total = player.bet + available
            if target_total > prev_highest and player.id in self.acted_players:
                if sender:
                    await self._send_msg(sender, "Betting has not reopened for you; you may call, check, or fold.")
                return False

            if not self.escrow.try_debit(player.id, available):
                if sender:
                    await self._send_msg(sender, "Insufficient funds.")
                return False

            player.bet += available
            player.all_in = True

            if player.bet > self.highest:
                raise_size = player.bet - prev_highest
                if raise_size >= prev_raise_size:
                    self.last_raise_size = raise_size
                    full_raise = True
                self.highest = player.bet

            self.log_action(
                f"{player.name} goes ALL IN {available} · pot {self.escrow.total}",
                player.name,
                "allin",
            )
        else:
            raise ValueError(f"unknown poker action: {action}")

        if full_raise:
            self.acted_players.clear()
        self.acted_players.add(player.id)

        if self.terminal_state == "open":
            self.current_actor_id = self._next_actor_after(player.id)
        await self._refresh_board()
        advanced = await self._maybe_advance_round()
        if not advanced and self.terminal_state == "open":
            await self._start_action_clock_for_current_actor()
            await self.run_bot_turns()
        return True

    def _stop_current_view(self):
        if self.current_view:
            self.current_view.stop()
            self.current_view = None

    async def _maybe_advance_round(self) -> bool:
        eligible = [p for p in self.players if not p.folded and not p.all_in]
        pending = [p for p in eligible if p.id not in self.acted_players]
        if pending or any(p.bet != self.highest for p in eligible):
            return False
        self._return_uncalled_bet()
        await self.next_round()
        return True

    async def next_round(self):
        self.round += 1

        if self.round == 1:
            burned = self.deck.burn()
            if burned is not None:
                self.burned.append(burned)
            new_cards = self.deck.deal(3)
            street = "Flop"
        elif self.round == 2:
            burned = self.deck.burn()
            if burned is not None:
                self.burned.append(burned)
            new_cards = self.deck.deal(1)
            street = "Turn"
        elif self.round == 3:
            burned = self.deck.burn()
            if burned is not None:
                self.burned.append(burned)
            new_cards = self.deck.deal(1)
            street = "River"
        else:
            return await self.showdown()

        self.community.extend(new_cards)
        self.street = street.lower()

        for p in self.players:
            p.bet = 0
        self.highest = 0
        self.acted_players.clear()
        order = self.action_order_for(self.street)
        self.current_actor_id = order[0] if order else None

        await self._refresh_board()
        if order:
            await self.pacing.wait_street_reveal()
        else:
            await self.pacing.wait_all_in_runout_street()
        await self.betting_round()

    def _side_pot_payouts(self) -> dict[int, int]:
        payouts, side_pots = PotManager.resolve_showdown_payouts(
            players=self.players,
            contributions=self.escrow.contributions,
            rank_fn=self.rank_hand,
            compare_fn=self.compare,
            community=self.community,
            button_seat=self.button_seat,
        )
        self.last_side_pots = side_pots
        return payouts

    def _return_uncalled_bet(self) -> dict[int, int]:
        street_bets = {player.id: player.bet for player in self.players}
        player_id, amount = PotManager.calculate_uncalled_bet(
            self.players, street_bets
        )
        if (
            player_id is None
            or amount <= 0
            or not hasattr(self.escrow, "return_uncalled_bet")
        ):
            return {}
        if self.escrow.return_uncalled_bet(player_id, amount):
            player = self.get_player(player_id)
            if player is not None:
                self.log_action(
                    f"Uncalled {amount} returned to {player.name}",
                    player.name,
                    "return",
                )
            return {player_id: amount}
        return {}

    async def showdown(self):
        async with self._state_lock:
            return await self._showdown_unlocked()

    async def _showdown_unlocked(self):
        if self.terminal_state != "open":
            return

        self.last_showdown = []
        self.last_showdown_hands = []
        for p in self.players:
            hand_str = " ".join(p.hand) if not p.folded else "Mucked"
            status = "(folded)" if p.folded else ""
            hand_label = "" if p.folded else f"\n{self.hand_name(self.rank_hand(p.hand + self.community))}"
            self.last_showdown.append(
                f"**{p.name} {status}** · {hand_str or 'No cards'}{hand_label}"
            )
        self.last_showdown_hands = list(self.last_showdown)

        active = [p for p in self.players if not p.folded]
        if not active:
            return

        uncalled = self._return_uncalled_bet()
        pot = self.escrow.total
        payouts = self._side_pot_payouts()
        settled = await self.settle_game(payouts, "showdown")
        if not settled:
            return

        winners = ", ".join(
            player.name for player in self.players if payouts.get(player.id, 0) > 0
        )
        self.last_payouts = dict(payouts)
        self.last_result = f"🎉 {winners} wins {pot} {self.cog.money_name} at showdown"
        self.last_result_summary = f"{winners} won {pot} {self.money_name}"
        await self._refresh_board()
        await self._after_hand(
            HandResult(
                winners=[player.id for player in self.players if payouts.get(player.id, 0) > 0],
                payouts=dict(payouts),
                uncalled_bet_return=uncalled,
                side_pots=list(self.last_side_pots),
                summary_text=self.last_result_summary,
            ),
            "showdown",
        )

    async def refund_game(self, reason: str = "unknown") -> bool:
        async with self._state_lock:
            async with self._terminal_lock:
                if self.terminal_state != "open":
                    return False
                if not self.escrow.refund():
                    return False
                self.action_clock.cancel()
                self.terminal_state = "refunded"
                self.highest = 0
                self._stop_current_view()
                return True

    async def settle_game(self, payouts: dict[int, int], reason: str = "showdown") -> bool:
        async with self._state_lock:
            async with self._terminal_lock:
                if self.terminal_state != "open":
                    return False
                if not self.escrow.settle(payouts):
                    return False
                self.action_clock.cancel()
                self.terminal_state = "settled"
                self.highest = 0
                self._stop_current_view()
                return True

    async def _after_hand(self, result: HandResult, finish_kind: str):
        self.policy.on_hand_end(self.hand_number, self.players, result)
        if self.config.mode == GameMode.CASH and any(
            player.leave_after_hand for player in self.players
        ):
            await self.close_session("player left after hand")
            self._mark_game_inactive()
            return
        if (
            self.config.mode == GameMode.TOURNAMENT
            and self.tournament_policy is not None
            and self.tournament_policy.is_game_over(self.players)
        ):
            await self._finish_tournament()
            return
        if self.table_escrow is not None:
            self._next_hand_task = asyncio.create_task(
                self._start_next_hand(finish_kind)
            )
        else:
            self._mark_game_inactive()

    async def _finish_tournament(self):
        if self.tournament_policy is None:
            return
        ranks = self.tournament_policy.calculate_payouts(self.players)
        payouts = {
            rank.player_id: rank.prize for rank in ranks if rank.prize > 0
        }
        if self.table_escrow is not None and self.table_escrow.state == "open":
            if not self.table_escrow.settle(payouts):
                logging.error("Tournament prize escrow could not be settled")
                return
        self.tournament_ranks = ranks
        winner = next((rank for rank in ranks if rank.rank == 1), None)
        if winner is not None:
            self.last_result_summary = (
                f"{winner.name} wins the tournament · prize {winner.prize} {self.money_name}"
            )
        await self._refresh_board()
        self._mark_game_inactive()

    async def close_session(self, reason: str = "table closed") -> bool:
        if self.table_escrow is None or self.table_escrow.state != "open":
            return False
        if self.terminal_state == "open":
            await self.refund_game(reason)
        payouts = {
            player.id: self._stack_for(player) + player.pending_rebuy
            for player in self.players
            if player.stack is not None and self.table_escrow.contributions.get(player.id)
        }
        return self.table_escrow.settle(payouts)

    def _session_can_continue(self) -> bool:
        if self.config.mode == GameMode.TOURNAMENT:
            return not self.policy.is_game_over(self.players)
        active = [
            player for player in self.players
            if player.stack is not None and player.stack + player.pending_rebuy > 0
            and not player.sitting_out
        ]
        return len(active) >= 2 and any(not player.is_bot for player in active)

    async def _start_next_hand(self, finish_kind: str = "showdown"):
        humans = [
            player.id
            for player in self.players
            if not player.is_bot and not player.leave_after_hand
        ]
        if finish_kind == "fold":
            await self.pacing.wait_fold_win()
        else:
            await self.pacing.wait_showdown(humans)
        game_state = getattr(self.cog, "games", {}).get(self.guild_id)
        if (
            not game_state
            or not game_state.get("active")
            or game_state.get("instance") is not self
            or self.table_escrow is None
            or self.table_escrow.state != "open"
        ):
            return
        if not self._session_can_continue():
            await self.close_session("last hand")
            self._mark_game_inactive()
            return

        self.hand_number += 1
        self.log_action("New hand — blinds rotate clockwise", action="rotation")
        self.last_showdown = []
        self.last_showdown_hands = []
        self.last_payouts = {}
        self.last_side_pots = []
        self.button_seat = (self.button_seat + 1) % len(self.players)
        for player in self.players:
            if player.leave_after_hand:
                player.sitting_out = True

        self.deck = Deck()
        self.community = []
        self.burned = []
        self.highest = self.big_blind
        self.last_raise_size = self.big_blind
        self.round = 0
        self.street = "preflop"
        self.acted_players.clear()
        self.current_actor_id = None
        self._forced_bets_posted = False
        self.terminal_state = "open"
        self.hand_id = f"hand-{secrets.token_hex(4)}"
        self.escrow = PokerChipEscrow(self.players)
        try:
            await self.play_game()
        except Exception:
            logging.exception("Poker table failed while starting next hand")
            await self.close_session("next-hand failure")
            self._mark_game_inactive()

    def bot_action(self, player: Player) -> tuple[str, int]:
        difference = self.highest - player.bet
        stack = self._stack_for(player)
        if len(player.hand) != 2:
            if difference > 0:
                return ("allin", 0) if stack < difference else ("call", 0)
            return "check", 0

        if player.id in self.acted_players:
            if difference <= 0:
                return "check", 0
            return ("allin", 0) if stack <= difference else ("call", 0)

        decision = self.bot_strategy.decide(
            player.hand,
            self.community,
            pot=self.escrow.total,
            current_bet=player.bet,
            highest_bet=self.highest,
            stack=stack,
            last_raise_size=self.last_raise_size,
        )
        if difference <= 0 and decision.action in {"fold", "call"}:
            return "check", 0
        return decision.action, decision.amount

    async def run_bot_turns(self):
        if self._bot_turn_running:
            return
        self._bot_turn_running = True
        try:
            for _ in range(len(self.players) * 2 + 1):
                if self.terminal_state != "open" or self.current_actor_id is None:
                    return
                player = self._player_for(self.current_actor_id)
                if not player or not player.is_bot:
                    return
                self.last_action = f"{player.name} is thinking…"
                await self._refresh_board()
                await self.pacing.wait_bot_thinking()

                if self.terminal_state != "open" or self.current_actor_id != player.id:
                    return

                action, amount = self.bot_action(player)
                before_actor = self.current_actor_id
                applied = await self._dispatch_action(player, action, amount=amount)
                if not applied and self.current_actor_id == before_actor:
                    difference = self.highest - player.bet
                    fallback = (
                        ("allin", 0)
                        if difference > 0 and self._stack_for(player) <= difference
                        else ("call", 0)
                        if difference > 0
                        else ("check", 0)
                    )
                    await self._dispatch_action(player, fallback[0], amount=fallback[1])
                await self.pacing.wait_bot_action()
        finally:
            self._bot_turn_running = False

    def _mark_game_inactive(self):
        self._stop_current_view()
        current_task = asyncio.current_task()
        if (
            self._next_hand_task is not None
            and self._next_hand_task is not current_task
            and not self._next_hand_task.done()
        ):
            self._next_hand_task.cancel()
        self._next_hand_task = None
        guild_id = self.guild_id
        game = getattr(self.cog, "games", {}).get(guild_id)
        if game and game.get("instance") is self:
            game["active"] = False
            game["state"] = "closed"
            game["instance"] = None
            getattr(self.cog, "lobbies", {}).pop(game.get("lobby_code"), None)

    async def _refresh_board(self):
        embed = TableEmbedBuilder.build_table_embed(
            self, lang=self.config.language
        )
        if self.current_view is None:
            self.current_view = PokerActionView(self)
        else:
            self.current_view.refresh_buttons()

        try:
            if self.table_message is not None and hasattr(self.table_message, "edit"):
                await self.table_message.edit(embed=embed, view=self.current_view)
            elif self.channel and hasattr(self.channel, "send"):
                self.table_message = await self.channel.send(
                    embed=embed, view=self.current_view
                )
        except Exception:
            logging.debug("Board refresh debug", exc_info=True)

    def table_display(self) -> str:
        lines = []
        for player in sorted(self.players, key=lambda item: item.seat_index if item.seat_index is not None else 0):
            tags = [player.position]
            if player.is_bot:
                tags.append("BOT")
            if player.sitting_out:
                tags.append("sitting out")
            if player.folded:
                tags.append("folded")
            elif player.all_in:
                tags.append("all-in")
            if player.id == self.current_actor_id and self.terminal_state == "open":
                tags.append("TO ACT")
            lines.append(
                f"Seat {player.seat_index + 1 if player.seat_index is not None else 0}: {player.name} — "
                f"{' / '.join(filter(None, tags))}"
            )
        return "\n".join(lines)

    def clean_table_display(self) -> str:
        lines = []
        for player in sorted(self.players, key=lambda item: item.seat_index if item.seat_index is not None else 0):
            tags = [player.position]
            if player.is_bot:
                tags.append("BOT")
            if player.sitting_out:
                tags.append("sitting out")
            if player.folded:
                tags.append("folded")
            elif player.all_in:
                tags.append("all-in")
            if player.id == self.current_actor_id and self.terminal_state == "open":
                tags.append("TO ACT")
            lines.append(
                f"**Seat {player.seat_index + 1 if player.seat_index is not None else 0}** · {player.name} · "
                f"{' / '.join(filter(None, tags))} · bet **{player.bet}** · "
                f"stack **{self._stack_for(player)}**"
            )
        return "\n".join(lines)

    async def betting_round(self):
        await self._refresh_board()
        if self.current_actor is None and self.terminal_state == "open":
            await self.next_round()
            return True
        await self._start_action_clock_for_current_actor()
        if self.current_actor and self.current_actor.is_bot:
            await self.run_bot_turns()
        return True
