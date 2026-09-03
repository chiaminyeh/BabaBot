import unittest
from types import SimpleNamespace

from wordle_cog import wordle_cog


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

    async def send_board(self, bot, user):
        self.events.append(("board", bot, user))


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


if __name__ == "__main__":
    unittest.main()
