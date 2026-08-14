import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from bababucks import BababucksEscrow, BababucksLedger
from poker.cog import PokerCog
from poker.models import BlindLevel, GameMode, HandResult, Player, TableConfig
from poker.pacing import PacingController
from poker.pots import PokerChipEscrow
from poker.service import PokerGameInstance
from poker.ui import RaisePresetsView, TableEmbedBuilder, _safe_respond


class FakeBaba:
    def __init__(self, bank):
        self.bank = bank
        self.money_name = "bababucks"
        self.economy_state = {"casino_escrows": {}}
        self.ledger = BababucksLedger(bank, threading.RLock(), lambda: None)

    def get_money(self, user_id):
        return self.ledger.get_balance(int(user_id))

    def add_money(self, user_id, amount):
        return self.ledger.adjust_clamped(int(user_id), int(amount))

    def new_escrow(self, *, escrow_id=None, game_type=None):
        if escrow_id is None:
            return BababucksEscrow(self.ledger)
        return BababucksEscrow(
            self.ledger,
            state=self.economy_state,
            escrow_id=escrow_id,
            game_type=game_type,
        )


class FakeChannel:
    def __init__(self, guild_id=99):
        self.id = 501
        self.guild = SimpleNamespace(id=guild_id)
        self.send = AsyncMock()


def user(user_id, name):
    return SimpleNamespace(id=user_id, name=name, locale="en-US")


class PokerIntegrationLifecycleTests(unittest.TestCase):
    @staticmethod
    def make_service(players, *, config=None, guild_id=99):
        baba = FakeBaba({player.id: (1000, False) for player in players})
        cog = SimpleNamespace(
            baba=baba,
            money_name="bababucks",
            minimum_bet=10,
            games={},
            lobbies={},
        )
        channel = FakeChannel(guild_id)
        game = PokerGameInstance(channel, players, cog, config=config)
        cog.games[guild_id] = {
            "active": True,
            "state": "playing",
            "instance": game,
            "lobby_code": "TEST",
        }
        game.pacing = PacingController(
            bot_thinking_delay=0,
            bot_action_delay=0,
            street_reveal_delay=0,
            all_in_runout_delay=0,
            fold_win_delay=0,
            showdown_delay=0,
        )
        return baba, cog, channel, game

    def test_cog_registers_group_and_legacy_start_commands(self):
        commands = {command.name: command for command in PokerCog.__cog_app_commands__}
        self.assertIn("poker", commands)
        self.assertIn("poker_start", commands)
        self.assertIn("start", {command.name for command in commands["poker"].commands})

    def test_lobby_start_atomically_debits_and_reuses_lobby_message(self):
        baba = FakeBaba({1: (1000, True)})
        channel = FakeChannel()
        lobby_message = SimpleNamespace(edit=AsyncMock())
        config = TableConfig(
            mode=GameMode.CASH,
            code="ABCD",
            small_blind=5,
            big_blind=10,
            starting_stack=1000,
            buy_in_amount=1000,
            max_seats=2,
        )
        host = Player(user(1, "Host"))
        host.stack = 1000
        cog = object.__new__(PokerCog)
        cog.baba = baba
        cog.money_name = "bababucks"
        cog.minimum_bet = 10
        cog.lobbies = {}
        cog.bot = SimpleNamespace(get_channel=lambda _channel_id: channel)
        cog.games = {
            99: {
                "active": True,
                "state": "lobby",
                "starting": False,
                "host_id": 1,
                "guild_id": 99,
                "channel_id": channel.id,
                "channel": channel,
                "players": [host],
                "config": config,
                "buyin": 1000,
                "code": "ABCD",
                "lobby_code": "ABCD",
                "lobby_message": lobby_message,
                "lobby_view": None,
                "table_escrow": None,
                "instance": None,
            }
        }
        cog.lobbies["ABCD"] = cog.games[99]

        async def scenario():
            started, _message = await cog._begin_table(99, 1)
            self.assertTrue(started)
            instance = cog.games[99]["instance"]
            self.assertIs(instance.table_message, lobby_message)
            self.assertEqual(cog.games[99]["state"], "playing")
            self.assertEqual(len(instance.players), 2)
            self.assertEqual(instance.current_actor_id, 1)
            instance.action_clock.cancel()
            await instance.refund_game("test cleanup")
            await instance.close_session("test cleanup")

        asyncio.run(scenario())
        self.assertEqual(baba.bank[1], (1000, True))
        lobby_message.edit.assert_awaited()

    def test_action_clock_moves_to_the_next_human_actor(self):
        players = [Player(user(1, "SB")), Player(user(2, "BB"))]
        for player in players:
            player.stack = 100
        _baba, _cog, _channel, game = self.make_service(players)

        async def scenario():
            await game.play_game()
            first = game.action_clock.current_token
            self.assertIsNotNone(first)
            self.assertEqual(first.actor_id, 1)
            self.assertTrue(await game.handle_player_action(players[0], "call"))
            second = game.action_clock.current_token
            self.assertIsNotNone(second)
            self.assertEqual(second.actor_id, 2)
            self.assertGreater(second.action_sequence, first.action_sequence)
            game.action_clock.cancel()

        asyncio.run(scenario())

    def test_timeout_streak_survives_automatic_fold(self):
        players = [Player(user(1, "SB")), Player(user(2, "BB"))]
        for player in players:
            player.stack = 100
        _baba, _cog, _channel, game = self.make_service(players)

        async def scenario():
            await game.play_game()
            token = game.action_clock.current_token
            self.assertIsNotNone(token)
            await game.handle_timeout(token)
            self.assertEqual(players[0].timeout_streak, 1)

        asyncio.run(scenario())

    def test_big_blind_ante_is_not_part_of_live_bet(self):
        players = [
            Player(user(1, "BTN")),
            Player(user(2, "SB")),
            Player(user(3, "BB")),
        ]
        for player in players:
            player.stack = 1000
        config = TableConfig(
            mode=GameMode.TOURNAMENT,
            starting_stack=1000,
            buy_in_amount=100,
            blind_schedule=[BlindLevel(1, 25, 50, 50)],
        )
        _baba, _cog, _channel, game = self.make_service(players, config=config)

        self.assertTrue(game.post_forced_bets())
        self.assertEqual(players[1].bet, 25)
        self.assertEqual(players[2].bet, 50)
        self.assertEqual(game.escrow.contributions[3], 100)
        self.assertEqual(game.highest, 50)

        self.assertTrue(game.escrow.try_debit(players[0].id, 50))
        self.assertTrue(game.escrow.try_debit(players[1].id, 25))
        players[0].bet = 50
        players[1].bet = 50
        self.assertEqual(game._return_uncalled_bet(), {})
        self.assertEqual(game.escrow.total, 200)

    def test_all_in_runout_reaches_showdown_without_an_actor(self):
        players = [Player(user(1, "SB")), Player(user(2, "BB"))]
        for player in players:
            player.stack = 100
        _baba, _cog, _channel, game = self.make_service(players)

        async def scenario():
            await game.play_game()
            self.assertTrue(await game.handle_player_action(players[0], "allin"))
            self.assertTrue(await game.handle_player_action(players[1], "call"))
            self.assertEqual(len(game.community), 5)
            self.assertEqual(game.terminal_state, "settled")
            self.assertEqual(len(game.burned), 3)

        asyncio.run(scenario())

    def test_folded_blind_and_two_all_in_bots_settle_and_start_next_hand(self):
        folded = Player(user(1, "Human"))
        winner = Player(user(-1, "Bot 1"), is_bot=True)
        runner_up = Player(user(-2, "Bot 2"), is_bot=True)
        players = [folded, winner, runner_up]
        for player in players:
            player.stack = 1000
        _baba, _cog, _channel, game = self.make_service(players)
        game.table_escrow = SimpleNamespace(state="open")
        game.run_bot_turns = AsyncMock()
        game.escrow = PokerChipEscrow(players)
        self.assertTrue(game.escrow.try_debit(folded.id, 15))
        self.assertTrue(game.escrow.try_debit(winner.id, 1000))
        self.assertTrue(game.escrow.try_debit(runner_up.id, 1000))
        folded.folded = True
        folded.hand = ["2♠", "3♦"]
        winner.hand = ["A♠", "A♦"]
        runner_up.hand = ["K♠", "K♦"]
        game.community = ["4♣", "7♥", "9♦", "J♣", "Q♥"]
        game.current_actor_id = None
        game.street = "river"
        game.round = 4
        settled_escrow = game.escrow

        async def scenario():
            await game.showdown()
            self.assertEqual(settled_escrow.state, "settled")
            self.assertEqual(sum(game.last_payouts.values()), 2015)
            self.assertIsNotNone(game._next_hand_task)
            await game._next_hand_task
            self.assertEqual(game.hand_number, 2)
            self.assertEqual(game.terminal_state, "open")
            self.assertTrue(any(
                entry.action == "rotation" for entry in game.action_log
            ))
            game.action_clock.cancel()

        asyncio.run(scenario())

    def test_safe_respond_omits_absent_view_for_embed_only_reply(self):
        response = SimpleNamespace(
            is_done=lambda: False,
            send_message=AsyncMock(),
        )
        interaction = SimpleNamespace(response=response)
        embed = SimpleNamespace(title="Private hand")

        asyncio.run(_safe_respond(interaction, embed=embed, ephemeral=True))

        response.send_message.assert_awaited_once()
        kwargs = response.send_message.await_args.kwargs
        self.assertIs(kwargs["embed"], embed)
        self.assertNotIn("view", kwargs)

    def test_next_hand_rotates_positions_and_shows_rotation_in_table_embed(self):
        players = [
            Player(user(1, "Seat 1")),
            Player(user(2, "Seat 2")),
            Player(user(3, "Seat 3")),
        ]
        for player in players:
            player.stack = 100
        _baba, _cog, _channel, game = self.make_service(players)
        game.table_escrow = SimpleNamespace(state="open")

        async def scenario():
            await game.play_game()
            self.assertEqual(
                [player.position for player in players], ["BTN", "SB", "BB"]
            )
            game.action_clock.cancel()
            game.terminal_state = "settled"

            await game._start_next_hand("fold")

            self.assertEqual(game.hand_number, 2)
            self.assertEqual(
                [player.position for player in players], ["BB", "BTN", "SB"]
            )
            embed = TableEmbedBuilder.build_table_embed(game)
            recent_actions = next(
                field.value for field in embed.fields
                if "RECENT ACTIONS" in field.name
            )
            self.assertIn("blinds rotate clockwise", recent_actions)
            game.action_clock.cancel()

        asyncio.run(scenario())

    def test_raise_preset_callback_uses_raise_to_total(self):
        player = Player(user(1, "Hero"))
        player.stack = 100
        legal = SimpleNamespace(
            presets=[SimpleNamespace(label="Min", target_total=40, is_all_in=False)],
            min_raise_to=40,
            max_raise_to=100,
        )
        game = SimpleNamespace(raise_bet=AsyncMock(return_value=True))
        view = RaisePresetsView(game, player, legal)
        callback = next(
            child.callback
            for child in view.children
            if str(getattr(child, "label", "")).startswith("Min")
        )
        self.assertTrue(callable(callback))
        interaction = SimpleNamespace(
            user=user(1, "Hero"),
            response=SimpleNamespace(
                is_done=lambda: False,
                defer=AsyncMock(),
            ),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        asyncio.run(callback(interaction))

        game.raise_bet.assert_awaited_once_with(player, 40)

    def test_tournament_completion_settles_prize_pool(self):
        players = [Player(user(1, "Winner")), Player(user(2, "Runner-up"))]
        for player in players:
            player.stack = 1000
        config = TableConfig(
            mode=GameMode.TOURNAMENT,
            starting_stack=1000,
            buy_in_amount=100,
        )
        _baba, cog, _channel, game = self.make_service(players, config=config)
        table_escrow = SimpleNamespace(state="open", settle=MagicMock(return_value=True))
        game.table_escrow = table_escrow
        players[1].stack = 0

        asyncio.run(
            game._after_hand(
                HandResult(winners=[1], payouts={1: 200}), "showdown"
            )
        )

        table_escrow.settle.assert_called_once_with({1: 200})
        self.assertFalse(cog.games[99]["active"])
        self.assertEqual(game.tournament_ranks[0].player_id, 1)


if __name__ == "__main__":
    unittest.main()
