import unittest
from pathlib import Path

from scripts import watch_bababot_errors as watch


class MonitorFilterTests(unittest.TestCase):
    def detect(self, text):
        return watch.detect_error_events(text, Path("synthetic_bababot.log"), set())

    def test_structured_interaction_failure_is_reported(self):
        text = """2026-07-15 ERROR trpg.view INTERACTION_FAILURE stage=message_edit custom_id=b_atk user_id=123 guild_id=456 channel_id=789
Traceback (most recent call last):
  File \"C:/Bot/trpg/view.py\", line 10, in global_callback
    raise RuntimeError('boom')
RuntimeError: boom
"""
        events = self.detect(text)
        self.assertEqual(len(events), 1)
        self.assertIn("custom_id=b_atk", events[0]["excerpt"])

    def test_discord_view_and_modal_failures_are_reported(self):
        for kind in ("view", "modal"):
            with self.subTest(kind=kind):
                text = f"""2026-07-15 ERROR discord.ui.{kind} Ignoring exception in {kind} <BrokenUI>
Traceback (most recent call last):
  File \"C:/Bot/trpg/{kind}.py\", line 20, in callback
    raise KeyError('broken')
KeyError: 'broken'
"""
                self.assertEqual(len(self.detect(text)), 1)

    def test_invalid_prefix_command_is_silent(self):
        text = """2026-07-15 ERROR discord.ext.commands.bot Ignoring exception in command None
discord.ext.commands.errors.CommandNotFound: Command \"NOPE\" is not found
"""
        self.assertEqual(self.detect(text), [])

    def test_gateway_and_voice_reconnect_noise_is_silent(self):
        samples = (
            """2026-07-15 ERROR discord.client Attempting a reconnect in 3.39s
Traceback (most recent call last):
  File \"discord/client.py\", line 728, in connect
    raise WSServerHandshakeError()
aiohttp.client_exceptions.WSServerHandshakeError: 503 gateway.discord.gg
""",
            """2026-07-15 ERROR discord.voice_state Disconnected with close code 1006; reconnecting
Traceback (most recent call last):
discord.errors.ConnectionClosed: WebSocket closed with 1006
""",
        )
        for text in samples:
            with self.subTest(text=text.splitlines()[0]):
                self.assertEqual(self.detect(text), [])

    def test_same_interaction_failure_with_new_timestamp_is_deduplicated(self):
        seen = set()
        source = Path("synthetic_bababot.log")
        first = "2026-07-15 01:00:00 ERROR trpg.view INTERACTION_FAILURE stage=callback custom_id=b_atk\nRuntimeError: boom"
        second = "2026-07-15 01:02:00 ERROR trpg.view INTERACTION_FAILURE stage=callback custom_id=b_atk\nRuntimeError: boom"
        self.assertEqual(len(watch.detect_error_events(first, source, seen)), 1)
        self.assertEqual(watch.detect_error_events(second, source, seen), [])


if __name__ == "__main__":
    unittest.main()
