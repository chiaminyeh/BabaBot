import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bababucks import BababucksEscrow, BababucksLedger
from poker_ai_strategy import RLCardRuleStrategy
from poker_cog import Player, PokerCog, PokerGame


class FakeBaba:
    def __init__(self, bank):
        self.bank = bank
        self.money_name = "bababucks"
        self.economy_state = {"casino_escrows": {}}
        self.writes = []
        self.ledger = BababucksLedger(
            bank,
            threading.RLock(),
            lambda: self.writes.append((dict(bank), dict(self.economy_state))),
        )

    def get_money(self, user_id):
        return self.ledger.get_balance(int(user_id))

    def new_escrow(self, *, escrow_id=None, game_type=None):
        if escrow_id is None and game_type is None:
            return BababucksEscrow(self.ledger)
        return BababucksEscrow(
            self.ledger,
            state=self.economy_state,
            escrow_id=escrow_id,
            game_type=game_type,
        )


class FakeChannel:
    def __init__(self, guild_id=99):
        self.guild = SimpleNamespace(id=guild_id)
        self.send = AsyncMock()


class PokerReviewRegressionTests(unittest.TestCase):
    @staticmethod
    def user(user_id, name):
        return SimpleNamespace(id=user_id, name=name)

    @staticmethod
    def make_game(bank, *, player_ids=(7, 8), hand_id="review-hand"):
        baba = FakeBaba(bank)
        cog = SimpleNamespace(
            baba=baba,
            bank=baba.bank,
            minimum_bet=10,
            money_name="bababucks",
            games={},
        )
        players = [
            Player(SimpleNamespace(id=user_id, name=f"Player {user_id}"))
            for user_id in player_ids
        ]
        game = PokerGame(
            FakeChannel(),
            players,
            cog,
            hand_id=hand_id,
        )
        cog.games[99] = {"active": True, "instance": game}
        return baba, players, game

    def test_poker_end_uses_application_command_permission_check(self):
        self.assertTrue(PokerCog.end_poker.checks)

    def test_concurrent_actions_serialize_one_successful_debit(self):
        baba, players, game = self.make_game({7: (50, True), 8: (50, False)})
        self.assertTrue(game.post_forced_bets())
        game._maybe_advance_round = AsyncMock(return_value=False)
        entered = asyncio.Event()
        release = asyncio.Event()
        first_sender = True

        async def paused_sender(_content):
            nonlocal first_sender
            if first_sender:
                first_sender = False
                entered.set()
                await release.wait()

        async def scenario():
            first = asyncio.create_task(
                game._dispatch_action(
                    players[0], "bet", amount=15, sender=paused_sender
                )
            )
            await asyncio.wait_for(entered.wait(), timeout=1)
            second = asyncio.create_task(
                game._dispatch_action(
                    players[0], "bet", amount=15, sender=AsyncMock()
                )
            )
            await asyncio.sleep(0)
            self.assertFalse(second.done())
            release.set()
            return await asyncio.gather(first, second)

        first_result, second_result = asyncio.run(scenario())

        self.assertEqual((first_result, second_result), (True, False))
        self.assertEqual(baba.bank[7], (30, True))
        self.assertEqual(game.escrow.contributions[7], 20)

    def test_terminal_refund_waits_for_inflight_action_and_blocks_later_action(self):
        baba, players, game = self.make_game({7: (50, True), 8: (50, False)})
        self.assertTrue(game.post_forced_bets())
        game._maybe_advance_round = AsyncMock(return_value=False)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def paused_sender(_content):
            entered.set()
            await release.wait()

        async def scenario():
            action = asyncio.create_task(
                game._dispatch_action(
                    players[0], "bet", amount=15, sender=paused_sender
                )
            )
            await asyncio.wait_for(entered.wait(), timeout=1)
            refund = asyncio.create_task(game.refund_game("race"))
            await asyncio.sleep(0)
            self.assertFalse(refund.done())
            release.set()
            return await asyncio.gather(action, refund)

        action_result, refund_result = asyncio.run(scenario())

        self.assertEqual((action_result, refund_result), (True, True))
        self.assertEqual(game.terminal_state, "refunded")
        self.assertEqual(game.pot, 0)
        self.assertEqual(baba.bank, {7: (50, True), 8: (50, False)})

        later = asyncio.run(
            game._dispatch_action(players[1], "bet", amount=10, sender=AsyncMock())
        )
        self.assertFalse(later)
        self.assertEqual(baba.bank, {7: (50, True), 8: (50, False)})

    def test_bet_below_outstanding_call_is_rejected_without_debit(self):
        baba, players, game = self.make_game({7: (100, True), 8: (100, False)})
        self.assertTrue(game.post_forced_bets())
        game._maybe_advance_round = AsyncMock(return_value=False)
        before_bank = dict(baba.bank)
        before_contributions = game.escrow.contributions

        asyncio.run(
            game._dispatch_action(
                players[0], "bet", amount=1, sender=AsyncMock()
            )
        )

        self.assertEqual(baba.bank, before_bank)
        self.assertEqual(game.escrow.contributions, before_contributions)
        self.assertEqual(players[0].bet, 5)
        self.assertEqual(game.current_actor_id, players[0].id)

    def test_first_postflop_bet_must_meet_big_blind(self):
        baba, players, game = self.make_game({7: (100, True), 8: (100, False)})
        self.assertTrue(game.post_forced_bets())
        game.street = "flop"
        for player in players:
            player.bet = 0
        game.highest = 0
        game.current_actor_id = players[0].id
        game._maybe_advance_round = AsyncMock(return_value=False)
        before = dict(baba.bank)

        result = asyncio.run(
            game._dispatch_action(
                players[0], "bet", amount=5, sender=AsyncMock()
            )
        )

        self.assertFalse(result)
        self.assertEqual(baba.bank, before)
        self.assertEqual(players[0].bet, 0)

    def test_forced_blind_that_uses_full_stack_marks_player_all_in(self):
        _baba, players, game = self.make_game({7: (5, True), 8: (100, False)})

        self.assertTrue(game.post_forced_bets())

        self.assertTrue(players[0].all_in)
        self.assertEqual(game.action_order_for("preflop"), [8])
        self.assertEqual(game.current_actor_id, 8)

    def test_all_in_blinds_leave_no_actor_instead_of_crashing(self):
        _baba, players, game = self.make_game({7: (5, True), 8: (10, False)})

        self.assertTrue(game.post_forced_bets())

        self.assertTrue(all(player.all_in for player in players))
        self.assertEqual(game.action_order_for("preflop"), [])
        self.assertIsNone(game.current_actor_id)

    def test_ordinary_bet_that_uses_full_stack_marks_player_all_in(self):
        _baba, players, game = self.make_game({7: (15, True), 8: (100, False)})
        self.assertTrue(game.post_forced_bets())
        game._maybe_advance_round = AsyncMock(return_value=False)

        result = asyncio.run(
            game._dispatch_action(
                players[0], "bet", amount=10, sender=AsyncMock()
            )
        )

        self.assertTrue(result)
        self.assertTrue(players[0].all_in)
        self.assertEqual(game.action_order_for("preflop"), [8])

    def test_showdown_partitions_main_and_side_pots(self):
        baba, players, game = self.make_game(
            {7: (1000, True), 8: (1000, False), 9: (1000, False)},
            player_ids=(7, 8, 9),
            hand_id="side-pot-hand",
        )
        self.assertTrue(game.post_forced_bets())
        self.assertTrue(game.escrow.try_debit(7, 15))
        self.assertTrue(game.escrow.try_debit(8, 40))
        self.assertTrue(game.escrow.try_debit(9, 40))
        game.pot = game.escrow.total
        players[0].all_in = True
        for player in players:
            player.hand = [f"P{player.id}"]

        scores = {
            7: (3, [14]),
            8: (2, [13]),
            9: (1, [12]),
        }

        def rank_hand(cards):
            for player in players:
                if player.hand[0] in cards:
                    return scores[player.id]
            raise AssertionError("unknown hand")

        game.rank_hand = rank_hand
        asyncio.run(game.showdown())

        self.assertEqual(game.escrow.state, "settled")
        self.assertEqual(baba.bank[7], (1030, True))
        self.assertEqual(baba.bank[8], (1015, False))
        self.assertEqual(baba.bank[9], (955, False))

    def test_orphaned_open_poker_escrow_is_refunded_on_recovery(self):
        baba = FakeBaba({7: (100, True)})
        escrow = baba.new_escrow(
            escrow_id="poker:99:orphan-hand",
            game_type="poker",
        )
        self.assertTrue(escrow.try_debit(7, 25))
        self.assertEqual(baba.bank[7], (75, True))

        cog = object.__new__(PokerCog)
        cog.baba = baba
        recovered = cog.recover_orphaned_escrows()

        self.assertEqual(recovered, 1)
        self.assertEqual(baba.bank[7], (100, True))
        self.assertEqual(
            baba.economy_state["casino_escrows"]["poker:99:orphan-hand"]["state"],
            "refunded",
        )

    def test_rlcard_raise_that_consumes_stack_translates_to_all_in(self):
        strategy = RLCardRuleStrategy(big_blind=10)

        decision = strategy.decide(
            ["A♠", "K♦"],
            [],
            pot=100,
            current_bet=0,
            highest_bet=0,
            stack=30,
        )

        self.assertEqual(decision.action, "allin")

    def test_raise_requires_previous_full_raise_size(self):
        baba, players, game = self.make_game({7: (100, True), 8: (100, False)})
        self.assertTrue(game.post_forced_bets())

        # Small blind raises from 5 to 30: the full raise increment is 20,
        # making 50 the next legal total (not 40).
        self.assertTrue(asyncio.run(game.raise_bet(players[0], 30)))
        self.assertEqual(game.last_raise_size, 20)
        before = dict(baba.bank)

        self.assertFalse(asyncio.run(game.raise_bet(players[1], 40)))
        self.assertEqual(baba.bank, before)
        self.assertEqual(game.highest, 30)

    def test_short_all_in_does_not_reopen_raise_for_players_who_acted(self):
        _baba, players, game = self.make_game(
            {7: (100, True), 8: (11, False), 9: (100, False)},
            player_ids=(7, 8, 9),
            hand_id="short-raise-reopen",
        )
        self.assertTrue(game.post_forced_bets())

        # UTG calls, then the small blind can only move from 5 to 11.  That
        # one-chip all-in raise is below the previous full raise of 10.
        self.assertTrue(
            asyncio.run(game._dispatch_action(players[0], "call"))
        )
        self.assertTrue(
            asyncio.run(game._dispatch_action(players[1], "allin"))
        )
        self.assertEqual(game.highest, 11)
        self.assertEqual(game.last_raise_size, 10)

        # Remove the remaining big blind so action returns to UTG.  UTG may
        # call the short raise but cannot raise again because betting did not
        # reopen for players who had already acted.
        self.assertTrue(
            asyncio.run(game._dispatch_action(players[2], "fold"))
        )
        self.assertEqual(game.current_actor_id, players[0].id)
        self.assertFalse(asyncio.run(game.raise_bet(players[0], 30)))

    def test_call_that_uses_last_chip_marks_player_all_in(self):
        baba, players, game = self.make_game(
            {7: (10, True), 8: (100, False)}, hand_id="short-call"
        )
        self.assertTrue(game.post_forced_bets())
        self.assertEqual(game.current_actor_id, 7)

        asyncio.run(
            game.call(
                SimpleNamespace(author=SimpleNamespace(id=7), send=AsyncMock())
            )
        )

        self.assertEqual(baba.bank[7][0], 0)
        self.assertTrue(players[0].all_in)
        self.assertEqual(players[0].bet, 10)

    def test_short_call_commits_available_stack_instead_of_rejecting(self):
        baba, players, game = self.make_game(
            {7: (25, True), 8: (100, False)}, hand_id="short-call-facing-raise"
        )
        self.assertTrue(game.post_forced_bets())
        # The big blind raises to 30, while the small blind has only 20 left.
        game.highest = 30
        players[1].bet = 30
        game.current_actor_id = players[0].id

        asyncio.run(
            game.call(
                SimpleNamespace(author=SimpleNamespace(id=7), send=AsyncMock())
            )
        )

        self.assertEqual(baba.bank[7][0], 0)
        self.assertTrue(players[0].all_in)
        self.assertEqual(players[0].bet, 25)

    def test_board_lists_folded_status_bet_and_stack_in_one_table_view(self):
        _baba, players, game = self.make_game(
            {7: (100, True), 8: (100, False)}, hand_id="table-board"
        )
        self.assertTrue(game.post_forced_bets())
        players[0].folded = True
        display = game.clean_table_display()

        self.assertIn("folded", display)
        self.assertIn("bet **5**", display)
        self.assertIn("stack **95**", display)

    def test_tied_showdown_splits_pot_with_seat_order_remainder(self):
        _baba, players, game = self.make_game(
            {7: (100, True), 8: (100, False)}, hand_id="tie-hand"
        )
        self.assertTrue(game.post_forced_bets())
        for player in players:
            player.hand = [f"P{player.id}"]
        game.community = ["2♠", "3♣", "4♥", "8♦", "9♦"]
        game.rank_hand = lambda _cards: (1, [10])

        asyncio.run(game.showdown())

        self.assertEqual(game.escrow.state, "settled")
        # The odd chip goes to the first tied winner clockwise from the
        # button, which is seat 1 in this heads-up hand.
        self.assertEqual(_baba.bank[7], (102, True))
        self.assertEqual(_baba.bank[8], (98, False))

    def test_fixed_table_stack_cashout_returns_money_only_at_session_close(self):
        baba = FakeBaba({7: (100, True), 8: (100, False)})
        cog = SimpleNamespace(
            baba=baba,
            bank=baba.bank,
            minimum_bet=10,
            money_name="bababucks",
            games={},
        )
        players = [
            Player(SimpleNamespace(id=7, name="Seven")),
            Player(SimpleNamespace(id=8, name="Eight")),
        ]
        table = baba.new_escrow(
            escrow_id="poker:table:99:stack-test", game_type="poker"
        )
        self.assertTrue(table.try_debit_many({7: 100, 8: 100}))
        game = PokerGame(
            FakeChannel(), players, cog, hand_id="stack-hand", starting_stack=100
        )
        game.table_escrow = table
        self.assertTrue(game.post_forced_bets())
        self.assertEqual(baba.bank[7][0], 0)
        self.assertEqual(baba.bank[8][0], 0)

        asyncio.run(game.refund_game("test"))
        self.assertTrue(asyncio.run(game.close_session("test")))
        self.assertEqual(baba.bank[7], (100, True))
        self.assertEqual(baba.bank[8], (100, False))


if __name__ == "__main__":
    unittest.main()
