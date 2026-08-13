import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bababucks import BababucksEscrow, BababucksLedger
from blackjack_cog import BlackjackCog, BlackjackGame, BlackjackPlayer


class FakeBaba:
    def __init__(self, bank):
        self.bank = bank
        self.money_name = "bababucks"
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
        return BababucksEscrow(
            self.ledger,
            state=self.economy_state if escrow_id is not None else None,
            escrow_id=escrow_id,
            game_type=game_type,
        ) if escrow_id is not None else BababucksEscrow(self.ledger)


class FakeChannel:
    def __init__(self, guild_id=44):
        self.guild = SimpleNamespace(id=guild_id)
        self.send = AsyncMock(
            return_value=SimpleNamespace(edit=AsyncMock())
        )


class BlackjackTransactionTests(unittest.TestCase):
    @staticmethod
    def make_cog(bank):
        baba = FakeBaba(bank)
        cog = object.__new__(BlackjackCog)
        cog.bot = SimpleNamespace(baba=baba)
        cog.baba = baba
        cog.bank = baba.bank
        cog.money_name = baba.money_name
        cog.games = {}
        cog.minimum_bet = 10
        return baba, cog

    def test_solo_stake_is_committed_to_durable_escrow_before_start(self):
        baba, cog = self.make_cog({7: (100, True)})
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=44),
            user=SimpleNamespace(id=7, name="Seven"),
            channel=FakeChannel(),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        with patch.object(BlackjackGame, "start", new=AsyncMock()):
            asyncio.run(
                BlackjackCog.blackjack.callback(cog, interaction, 25)
            )

        game = cog.games[44]
        self.assertEqual(baba.bank, {7: (75, True)})
        self.assertEqual(len(baba.writes), 1)
        self.assertTrue(game.escrow_id.startswith("blackjack:44:solo-7:"))
        self.assertEqual(
            baba.economy_state["casino_escrows"][game.escrow_id],
            {
                "game_type": "blackjack",
                "state": "open",
                "contributions": {"7": 25},
            },
        )

    def test_double_uses_escrow_and_duplicate_callback_cannot_debit_twice(self):
        baba, cog = self.make_cog({7: (100, True)})
        player = BlackjackPlayer(SimpleNamespace(id=7, name="Seven"), 25)
        game = BlackjackGame(cog, FakeChannel(), [player], hand_id="double-7")
        self.assertTrue(game.escrow.try_debit(7, 25))
        game.dealer_hand = ["2♠", "3♥"]
        player.hand = ["10♠", "5♥"]
        game.dealer_play = AsyncMock()
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=7),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        asyncio.run(game.handle_action(interaction, "double"))
        asyncio.run(game.handle_action(interaction, "double"))

        self.assertEqual(baba.bank, {7: (50, True)})
        self.assertEqual(game.escrow.contributions, {7: 50})
        self.assertEqual(player.bet, 50)
        self.assertTrue(player.doubled)
        self.assertEqual(len(interaction.response.send_message.await_args_list), 2)
        self.assertIn("already finished", interaction.response.send_message.await_args_list[1].args[0])

    def test_end_round_settles_winnings_once_through_durable_escrow(self):
        baba, cog = self.make_cog({7: (100, True)})
        player = BlackjackPlayer(SimpleNamespace(id=7, name="Seven"), 25)
        game = BlackjackGame(cog, FakeChannel(), [player], hand_id="settle-7")
        self.assertTrue(game.escrow.try_debit(7, 25))
        player.hand = ["K♠", "Q♥"]
        game.dealer_hand = ["9♠", "7♥"]

        asyncio.run(game.end_round())
        asyncio.run(game.end_round())

        self.assertEqual(baba.bank, {7: (125, True)})
        self.assertEqual(game.escrow.state, "settled")
        self.assertEqual(game.terminal_state, "settled")
        self.assertFalse(game.active)
        self.assertEqual(len(baba.writes), 2)


if __name__ == "__main__":
    unittest.main()
