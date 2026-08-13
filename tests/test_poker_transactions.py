import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bababucks import BababucksEscrow, BababucksLedger
from poker_cog import Player, PokerCog, PokerGame


class FakeBaba:
    def __init__(self, bank):
        self.bank = bank
        self.economy_state = {"casino_escrows": {}}
        self.writes = []
        self.ledger = BababucksLedger(
            bank,
            threading.RLock(),
            lambda: self.writes.append(
                (dict(bank), dict(self.economy_state))
            ),
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


class PokerTransactionTests(unittest.TestCase):
    @staticmethod
    def make_game(bank, *, hand_id=None):
        baba = FakeBaba(bank)
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
        if hand_id is None:
            game = PokerGame(FakeChannel(), players, cog)
        else:
            game = PokerGame(
                FakeChannel(), players, cog, hand_id=hand_id
            )
        cog.games[99] = {"active": True, "instance": game}
        return baba, players, game

    def test_hand_uses_durable_escrow_identity(self):
        baba, _, game = self.make_game(
            {7: (50, True), 8: (50, False)}, hand_id="hand-1"
        )

        self.assertTrue(game.post_forced_bets())

        self.assertEqual(game.hand_id, "hand-1")
        self.assertEqual(game.escrow_id, "poker:99:hand-1")
        self.assertEqual(
            baba.economy_state["casino_escrows"][game.escrow_id],
            {
                "game_type": "poker",
                "state": "open",
                "contributions": {"7": 5, "8": 10},
            },
        )

    def test_forced_bets_are_all_or_nothing_and_record_contributions(self):
        baba, players, game = self.make_game(
            {7: (50, True), 8: (50, False)}
        )

        self.assertTrue(game.post_forced_bets())
        self.assertFalse(game.post_forced_bets())

        self.assertEqual(baba.bank, {7: (45, True), 8: (40, False)})
        self.assertEqual(len(baba.writes), 1)
        self.assertEqual([player.bet for player in players], [5, 10])
        self.assertEqual(game.pot, 15)
        self.assertEqual(game.escrow.contributions, {7: 5, 8: 10})

        poor_baba, poor_players, poor_game = self.make_game(
            {7: (50, True), 8: (5, False)}
        )

        self.assertFalse(poor_game.post_forced_bets())

        self.assertEqual(
            poor_baba.bank, {7: (50, True), 8: (5, False)}
        )
        self.assertEqual(poor_baba.writes, [])
        self.assertEqual([player.bet for player in poor_players], [0, 0])
        self.assertEqual(poor_game.pot, 0)
        self.assertEqual(poor_game.escrow.contributions, {})

    def test_place_bet_commits_debit_before_updating_game_state(self):
        baba, players, game = self.make_game(
            {7: (50, True), 8: (50, False)}
        )
        self.assertTrue(game.post_forced_bets())
        ctx = SimpleNamespace(
            author=SimpleNamespace(id=7), send=AsyncMock()
        )
        game._maybe_advance_round = AsyncMock()

        asyncio.run(game.place_bet(ctx, 15))

        self.assertEqual(baba.bank, {7: (30, True), 8: (40, False)})
        self.assertEqual(len(baba.writes), 2)
        self.assertEqual([player.bet for player in players], [20, 10])
        self.assertEqual(game.pot, 30)
        self.assertEqual(game.highest, 20)
        self.assertEqual(game.escrow.contributions, {7: 20, 8: 10})
        game._maybe_advance_round.assert_awaited_once()

    def test_call_commits_difference_through_durable_escrow(self):
        baba, players, game = self.make_game(
            {7: (50, True), 8: (50, False)}, hand_id="call-hand"
        )
        self.assertTrue(game.post_forced_bets())
        bettor = SimpleNamespace(
            author=SimpleNamespace(id=7), send=AsyncMock()
        )
        caller = SimpleNamespace(
            author=SimpleNamespace(id=8), send=AsyncMock()
        )
        game._maybe_advance_round = AsyncMock()
        asyncio.run(game.place_bet(bettor, 15))

        asyncio.run(game.call(caller))

        self.assertEqual(baba.bank, {7: (30, True), 8: (30, False)})
        self.assertEqual(len(baba.writes), 3)
        self.assertEqual([player.bet for player in players], [20, 20])
        self.assertEqual(game.pot, 40)
        self.assertEqual(game.escrow.contributions, {7: 20, 8: 20})

    def test_allin_debits_remaining_balance_and_preserves_total_contribution(self):
        baba, players, game = self.make_game(
            {7: (50, True), 8: (50, False)}, hand_id="allin-hand"
        )
        self.assertTrue(game.post_forced_bets())
        player_ctx = SimpleNamespace(
            author=SimpleNamespace(id=7), send=AsyncMock()
        )
        game._maybe_advance_round = AsyncMock()

        asyncio.run(game.allin(player_ctx))

        self.assertEqual(baba.bank, {7: (0, True), 8: (40, False)})
        self.assertEqual(len(baba.writes), 2)
        self.assertEqual([player.bet for player in players], [50, 10])
        self.assertEqual(game.pot, 60)
        self.assertEqual(game.highest, 50)
        self.assertEqual(game.escrow.contributions, {7: 50, 8: 10})
        game._maybe_advance_round.assert_awaited_once()

    def test_force_end_refunds_total_contribution_after_street_reset(self):
        baba, players, game = self.make_game(
            {7: (100, True), 8: (100, False)}, hand_id="refund-hand"
        )
        self.assertTrue(game.post_forced_bets())
        game.escrow.try_debit(7, 20)
        players[0].bet += 20
        game.pot = game.escrow.total
        players[0].bet = 0
        game.highest = 0

        refunded = asyncio.run(game.refund_game("admin"))

        self.assertTrue(refunded)
        self.assertEqual(baba.bank, {7: (100, True), 8: (100, False)})
        self.assertEqual(game.escrow.state, "refunded")
        self.assertEqual(game.escrow.contributions, {7: 25, 8: 10})
        self.assertEqual(game.pot, 0)
        self.assertEqual(game.terminal_state, "refunded")
        self.assertEqual(len(baba.writes), 3)

    def test_settlement_pays_winner_exactly_once_and_consumes_escrow(self):
        baba, players, game = self.make_game(
            {7: (100, True), 8: (100, False)}, hand_id="settle-hand"
        )
        self.assertTrue(game.post_forced_bets())
        game.escrow.try_debit(7, 20)
        players[0].bet += 20
        game.pot = game.escrow.total

        settled = asyncio.run(game.settle_game({7: game.pot}, "showdown"))
        replayed = asyncio.run(game.settle_game({7: game.pot}, "retry"))

        self.assertTrue(settled)
        self.assertFalse(replayed)
        self.assertEqual(baba.bank, {7: (110, True), 8: (90, False)})
        self.assertEqual(game.escrow.state, "settled")
        self.assertEqual(game.terminal_state, "settled")
        self.assertEqual(game.pot, 0)
        self.assertEqual(len(baba.writes), 3)

    def test_fold_settles_winner_through_durable_escrow_once(self):
        baba, players, game = self.make_game(
            {7: (100, True), 8: (100, False)}, hand_id="fold-hand"
        )
        self.assertTrue(game.post_forced_bets())
        game._maybe_advance_round = AsyncMock()
        game.current_actor_id = 8
        fold_ctx = SimpleNamespace(
            author=SimpleNamespace(id=8), send=AsyncMock()
        )

        asyncio.run(game.fold(fold_ctx))
        asyncio.run(game.fold(fold_ctx))

        self.assertEqual(baba.bank, {7: (110, True), 8: (90, False)})
        self.assertEqual(game.escrow.state, "settled")
        self.assertEqual(game.terminal_state, "settled")
        self.assertEqual(game.pot, 0)
        self.assertEqual(len(baba.writes), 2)

    def test_showdown_settles_winner_through_durable_escrow_once(self):
        baba, players, game = self.make_game(
            {7: (100, True), 8: (100, False)}, hand_id="showdown-hand"
        )
        self.assertTrue(game.post_forced_bets())
        players[0].hand = ["A♠", "A♥"]
        players[1].hand = ["K♠", "K♥"]
        game.community = ["2♣", "3♣", "4♣", "8♦", "9♦"]
        game.rank_hand = lambda cards: (
            (2, [14]) if "A♠" in cards else (1, [13])
        )

        asyncio.run(game.showdown())
        asyncio.run(game.showdown())

        self.assertEqual(baba.bank, {7: (110, True), 8: (90, False)})
        self.assertEqual(game.escrow.state, "settled")
        self.assertEqual(game.terminal_state, "settled")
        self.assertEqual(game.pot, 0)
        self.assertEqual(len(baba.writes), 2)

    def test_admin_end_awaits_refund_before_confirming_game_end(self):
        baba, _, game = self.make_game(
            {7: (100, True), 8: (100, False)}, hand_id="admin-end-hand"
        )
        self.assertTrue(game.post_forced_bets())

        cog = object.__new__(PokerCog)
        cog.games = {99: {"active": True, "instance": game}}
        interaction = SimpleNamespace(
            guild_id=99,
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        asyncio.run(PokerCog.end_poker.callback(cog, interaction))

        self.assertEqual(baba.bank, {7: (100, True), 8: (100, False)})
        self.assertFalse(cog.games[99]["active"])
        interaction.response.send_message.assert_awaited_once_with(
            "Game force-ended. Bets returned."
        )


if __name__ == "__main__":
    unittest.main()
