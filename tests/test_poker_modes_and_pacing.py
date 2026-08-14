import asyncio
import unittest
import time
from types import SimpleNamespace

from poker.models import (
    Player,
    TableConfig,
    GameMode,
    BlindLevel,
    HandResult,
)
from poker.modes import CashPolicy, TournamentPolicy
from poker.timers import ActionClockController, GenerationToken
from poker.pacing import PacingController


class PokerModesAndPacingTests(unittest.TestCase):
    @staticmethod
    def make_player(uid: int, name: str, stack: int, seat_index: int, is_bot: bool = False) -> Player:
        p = Player(SimpleNamespace(id=uid, name=name), is_bot=is_bot)
        p.stack = stack
        p.seat_index = seat_index
        return p

    def test_cash_policy_rebuy_and_sitout_lifecycle(self):
        config = TableConfig(
            mode=GameMode.CASH,
            small_blind=5,
            big_blind=10,
            min_buy_in_bb=20,
            max_buy_in_bb=200,
        )
        policy = CashPolicy(config)
        p1 = self.make_player(1, "Alice", 1000, 0)
        p2 = self.make_player(2, "Bob", 50, 1)

        # Valid rebuy
        allowed, msg = policy.can_rebuy(p2, 500)
        self.assertTrue(allowed)

        # Invalid rebuy exceeding max stack (200 BB = 2000)
        allowed, msg = policy.can_rebuy(p1, 1500)
        self.assertFalse(allowed)
        self.assertIn("Maximum table stack", msg)

        # Sit out streak
        p2.timeout_streak = 2
        policy.on_hand_end(1, [p1, p2], HandResult(winners=[1], payouts={1: 100}))
        self.assertTrue(p2.sitting_out)

    def test_tournament_freezeout_payout_and_blinds_progression(self):
        # 6 players -> prize pool = 6 * 100 = 600
        # 1st place = 70% (420), 2nd place = 30% (180)
        players = [
            self.make_player(i, f"P{i}", 1500, i - 1)
            for i in range(1, 7)
        ]
        config = TableConfig(
            mode=GameMode.TOURNAMENT,
            blind_level_duration_seconds=600,
            blind_schedule=[
                BlindLevel(level=1, small_blind=5, big_blind=10, ante=0),
                BlindLevel(level=2, small_blind=10, big_blind=20, ante=0),
                BlindLevel(level=3, small_blind=15, big_blind=30, ante=0),
                BlindLevel(level=4, small_blind=25, big_blind=50, ante=50),
            ],
        )
        policy = TournamentPolicy(config, entrants=players, buy_in_fee=100)
        self.assertEqual(policy.total_prize_pool, 600)

        # Rebuy is forbidden
        can_rebuy, _ = policy.can_rebuy(players[0], 500)
        self.assertFalse(can_rebuy)

        # Eliminate players 3, 4, 5, 6
        players[2].stack = 0
        players[3].stack = 0
        players[4].stack = 0
        players[5].stack = 0
        policy.on_hand_end(1, players, HandResult(winners=[1], payouts={1: 200}))

        self.assertEqual(len(policy.eliminated_players), 4)

        # When down to 1 player (P1 wins)
        players[1].stack = 0
        policy.on_hand_end(2, players, HandResult(winners=[1], payouts={1: 400}))

        payouts = policy.calculate_payouts(players)
        self.assertEqual(len(payouts), 6)
        self.assertEqual(payouts[0].rank, 1)
        self.assertEqual(payouts[0].prize, 420)  # 70%
        self.assertEqual(payouts[1].rank, 2)
        self.assertEqual(payouts[1].prize, 180)  # 30%
        self.assertEqual(payouts[2].prize, 0)

        # Total prize pool conserved
        self.assertEqual(sum(r.prize for r in payouts), 600)

    def test_tournament_bb_ante_forced_bet(self):
        players = [
            self.make_player(1, "BTN", 1000, 0),
            self.make_player(2, "SB", 1000, 1),
            self.make_player(3, "BB", 1000, 2),
        ]
        config = TableConfig(
            mode=GameMode.TOURNAMENT,
            blind_schedule=[
                BlindLevel(level=1, small_blind=25, big_blind=50, ante=50),
            ],
        )
        policy = TournamentPolicy(config, entrants=players, buy_in_fee=100)
        forced = policy.calculate_forced_bets(players, button_seat=0, hand_number=1)

        # SB posts 25, BB posts 50 + 50 (Ante) = 100
        self.assertEqual(forced[2], 25)
        self.assertEqual(forced[3], 100)

    def test_action_clock_generation_token_cancellation_and_extension(self):
        controller = ActionClockController(table_id="table-1", timeout_seconds=1, time_bank_seconds=2)
        timed_out = []

        async def on_timeout(token: GenerationToken):
            timed_out.append(token)

        async def scenario():
            token1 = controller.start_turn("hand-1", 42, on_timeout, duration_override=0.05)
            self.assertEqual(token1.actor_id, 42)
            self.assertEqual(token1.action_sequence, 1)

            # Let it expire
            await asyncio.sleep(0.08)
            self.assertEqual(len(timed_out), 1)
            self.assertEqual(timed_out[0].action_sequence, 1)

            # New turn with early action cancellation
            token2 = controller.start_turn("hand-1", 42, on_timeout, duration_override=0.05)
            controller.cancel()
            await asyncio.sleep(0.08)
            # Should still be 1 (no second timeout)
            self.assertEqual(len(timed_out), 1)

        asyncio.run(scenario())

    def test_pacing_controller_mark_ready_skips_wait(self):
        pacing = PacingController(showdown_delay=5.0)

        async def scenario():
            start = time.time()
            pacing.reset_ready_state([1, 2])

            async def waiter():
                await pacing.wait_showdown([1, 2])

            task = asyncio.create_task(waiter())
            await asyncio.sleep(0.02)

            # Mark both players ready
            pacing.mark_player_ready(1)
            pacing.mark_player_ready(2)

            await task
            elapsed = time.time() - start
            self.assertLess(elapsed, 1.0)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
