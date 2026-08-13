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
    def __init__(self, guild_id=88):
        self.guild = SimpleNamespace(id=guild_id)
        self.send = AsyncMock()


class PokerGameplayTests(unittest.TestCase):
    @staticmethod
    def make_cog(bank):
        baba = FakeBaba(bank)
        cog = SimpleNamespace(
            baba=baba,
            bank=baba.bank,
            minimum_bet=10,
            money_name="bababucks",
            games={},
        )
        return baba, cog

    @staticmethod
    def user(user_id, name):
        return SimpleNamespace(id=user_id, name=name)

    def test_table_assigns_visible_positions_and_blinds_in_seat_order(self):
        baba, cog = self.make_cog({
            1: (100, True),
            2: (100, False),
            3: (100, False),
            4: (100, True),
        })
        players = [
            Player(self.user(1, "One")),
            Player(self.user(2, "Two")),
            Player(self.user(3, "Three")),
            Player(self.user(4, "Four")),
        ]
        game = PokerGame(
            FakeChannel(), players, cog, hand_id="seat-order", button_seat=0
        )

        self.assertTrue(game.post_forced_bets())
        self.assertEqual(
            [player.position for player in players],
            ["BTN", "SB", "BB", "UTG"],
        )
        self.assertEqual([player.bet for player in players], [0, 5, 10, 0])
        self.assertEqual(game.pot, 15)
        self.assertEqual(
            game.action_order_for("preflop"), [4, 1, 2, 3]
        )
        self.assertEqual(
            game.action_order_for("postflop"), [2, 3, 4, 1]
        )
        table = game.table_display()
        for seat, name, position in (
            (1, "One", "BTN"),
            (2, "Two", "SB"),
            (3, "Three", "BB"),
            (4, "Four", "UTG"),
        ):
            self.assertIn(f"Seat {seat}: {name} — {position}", table)

    def test_out_of_turn_action_does_not_debit_or_change_state(self):
        baba, cog = self.make_cog({1: (100, True), 2: (100, False), 3: (100, False), 4: (100, False)})
        players = [
            Player(self.user(1, "One")),
            Player(self.user(2, "Two")),
            Player(self.user(3, "Three")),
            Player(self.user(4, "Four")),
        ]
        game = PokerGame(FakeChannel(), players, cog, hand_id="turn-order")
        self.assertTrue(game.post_forced_bets())
        before = dict(baba.bank)
        ctx = SimpleNamespace(
            author=players[0].user,
            send=AsyncMock(),
        )

        asyncio.run(game.place_bet(ctx, 20))

        self.assertEqual(baba.bank, before)
        self.assertEqual(game.escrow.contributions, {2: 5, 3: 10})
        ctx.send.assert_awaited_once()
        self.assertIn("turn", ctx.send.await_args.args[0].lower())

    def test_actions_follow_utg_button_blinds_then_restart_from_sb(self):
        baba, cog = self.make_cog({
            1: (100, True),
            2: (100, False),
            3: (100, False),
            4: (100, False),
        })
        players = [
            Player(self.user(1, "Button")),
            Player(self.user(2, "Small Blind")),
            Player(self.user(3, "Big Blind")),
            Player(self.user(4, "UTG")),
        ]
        game = PokerGame(FakeChannel(), players, cog, hand_id="street-order")
        self.assertTrue(game.post_forced_bets())
        self.assertEqual(game.current_actor_id, 4)

        contexts = {
            player.id: SimpleNamespace(
                author=player.user,
                send=AsyncMock(),
            )
            for player in players
        }
        asyncio.run(game.call(contexts[4]))
        self.assertEqual(game.current_actor_id, 1)
        asyncio.run(game.call(contexts[1]))
        self.assertEqual(game.current_actor_id, 2)
        asyncio.run(game.call(contexts[2]))
        self.assertEqual(game.current_actor_id, 3)
        asyncio.run(game.check(contexts[3]))

        self.assertEqual(game.street, "flop")
        self.assertEqual(game.round, 1)
        self.assertEqual(game.current_actor_id, 2)
        self.assertEqual(len(game.community), 3)

        for player_id in (2, 3, 4, 1):
            asyncio.run(game.check(contexts[player_id]))

        self.assertEqual(game.street, "turn")
        self.assertEqual(game.round, 2)
        self.assertEqual(game.current_actor_id, 2)
        self.assertEqual(len(game.community), 4)

    def test_bot_acts_when_it_is_next_and_hands_turn_to_human(self):
        baba, cog = self.make_cog({
            1001: (100, False),
            7: (100, True),
            8: (100, False),
        })
        players = [
            Player(self.user(1001, "Bot 1"), is_bot=True),
            Player(self.user(7, "Human SB")),
            Player(self.user(8, "Human BB")),
        ]
        game = PokerGame(FakeChannel(), players, cog, hand_id="bot-turn")
        self.assertTrue(game.post_forced_bets())

        asyncio.run(game.betting_round())

        self.assertEqual(game.escrow.contributions, {7: 5, 8: 10, 1001: 10})
        self.assertEqual(game.current_actor_id, 7)
        self.assertTrue(any(
            "Bot 1 calls" in call.args[0]
            for call in game.channel.send.await_args_list
            if call.args
        ))
        self.assertIn("BOT", game.table_display())

    def test_bot_factory_creates_a_non_human_player(self):
        player = PokerCog.build_bot_player(88, 0)

        self.assertTrue(player.is_bot)
        self.assertEqual(player.name, "BabaBot 1")
        self.assertLess(player.id, 0)

    def test_bot_action_uses_rlcard_rule_strategy_for_hole_cards(self):
        baba, cog = self.make_cog({
            -8801: (200, False),
            7: (200, True),
        })
        bot = Player(self.user(-8801, "BabaBot 1"), is_bot=True)
        human = Player(self.user(7, "Human"))
        game = PokerGame(
            FakeChannel(), [bot, human], cog, hand_id="rlcard-strategy"
        )
        bot.hand = ["A♠", "K♦"]
        self.assertTrue(game.post_forced_bets())

        action, amount = game.bot_action(bot)

        self.assertEqual(action, "bet")
        self.assertGreaterEqual(amount, cog.minimum_bet)


if __name__ == "__main__":
    unittest.main()
