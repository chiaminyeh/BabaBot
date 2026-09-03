import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from wordle_cog import WordleGame, wordle_cog


class RecordingResponse:
    def __init__(self, events):
        self.events = events

    async def defer(self, **kwargs):
        self.events.append(("ack", "defer", kwargs))

    async def send_message(self, content, **kwargs):
        self.events.append(("ack", content, kwargs))


class RecordingGame:
    def __init__(self, user_id, events):
        self.user_id = user_id
        self.events = events

    async def send_board(self, bot, user, **kwargs):
        self.events.append(("board", bot, user, kwargs))


class WordleCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_wordle_print_acknowledges_before_refreshing_board(self):
        events = []
        bot = SimpleNamespace(
            baba=SimpleNamespace(bank={}, money_name="bababucks")
        )
        cog = wordle_cog(bot)
        game = RecordingGame(user_id=42, events=events)
        cog.wordle_games.append(game)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=42),
            response=RecordingResponse(events),
        )

        await cog.wordle_print.callback(cog, interaction)

        self.assertEqual([event[0] for event in events], ["ack", "board"])

    async def test_wordle_print_posts_fresh_board_instead_of_editing_recent_message(self):
        events = []
        old_edit = AsyncMock(
            side_effect=lambda **kwargs: events.append(("edit", kwargs))
        )
        old_board = SimpleNamespace(edit=old_edit)
        new_board = SimpleNamespace()

        async def send(**kwargs):
            events.append(("send", kwargs))
            return new_board

        channel = SimpleNamespace(send=send)
        bot = SimpleNamespace(
            baba=SimpleNamespace(bank={}, money_name="bababucks"),
            get_channel=lambda channel_id: channel,
        )
        cog = wordle_cog(bot)
        game = WordleGame(guild_id=1, channel_id=7, user_id=42)
        game.board_message = old_board
        game.last_board_time = time.time()
        cog.wordle_games.append(game)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=42, display_name="Tester"),
            response=RecordingResponse(events),
        )

        await cog.wordle_print.callback(cog, interaction)

        self.assertEqual([event[0] for event in events], ["ack", "send"])
        self.assertTrue(events[0][2]["ephemeral"])
        old_edit.assert_not_awaited()
        self.assertIs(game.board_message, new_board)
        self.assertIn("Wordle Game", events[1][1]["embed"].title)


if __name__ == "__main__":
    unittest.main()
