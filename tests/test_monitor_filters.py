import unittest
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import discord

from interaction_errors import is_transient_interaction_error
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

    def test_monitor_delivery_disconnect_is_silent(self):
        text = """2026-07-15 ERROR discord.ext.tasks Handling exception in internal background task MonitorCog.monitor_checker
Traceback (most recent call last):
  File "C:/Bot/monitor_cog.py", line 103, in monitor_checker
    await self._send_monitor_message(output)
  File "C:/Bot/monitor_cog.py", line 73, in _send_monitor_message
    await destination.send(text)
aiohttp.client_exceptions.ServerDisconnectedError: Server disconnected
"""
        self.assertEqual(self.detect(text), [])

    def test_expired_or_disconnected_view_interaction_is_silent(self):
        samples = (
            "2026-07-15 ERROR discord.ui.view Ignoring exception in view <TRPGGameView>\nTraceback (most recent call last):\ndiscord.errors.NotFound: 404 Not Found (error code: 10062): Unknown interaction",
            "2026-07-15 ERROR discord.ui.view Ignoring exception in view <TRPGGameView>\nTraceback (most recent call last):\naiohttp.client_exceptions.ServerDisconnectedError: Server disconnected",
        )
        for text in samples:
            with self.subTest(text=text.splitlines()[-1]):
                self.assertEqual(self.detect(text), [])

    def test_view_logic_error_is_not_hidden_as_transport_noise(self):
        text = "2026-07-15 ERROR discord.ui.view Ignoring exception in view <TRPGGameView>\nTraceback (most recent call last):\nKeyError: 'real game bug'"
        self.assertEqual(len(self.detect(text)), 1)

    def test_runtime_transient_classifier_is_narrow(self):
        response = SimpleNamespace(status=404, reason="Not Found", headers={})
        expired = discord.NotFound(response, {"code": 10062, "message": "Unknown interaction"})
        self.assertTrue(is_transient_interaction_error(expired))
        self.assertTrue(is_transient_interaction_error(aiohttp.ServerDisconnectedError()))
        self.assertFalse(is_transient_interaction_error(KeyError("real game bug")))

    def test_same_interaction_failure_with_new_timestamp_is_deduplicated(self):
        seen = set()
        source = Path("synthetic_bababot.log")
        first = "2026-07-15 01:00:00 ERROR trpg.view INTERACTION_FAILURE stage=callback custom_id=b_atk\nRuntimeError: boom"
        second = "2026-07-15 01:02:00 ERROR trpg.view INTERACTION_FAILURE stage=callback custom_id=b_atk\nRuntimeError: boom"
        self.assertEqual(len(watch.detect_error_events(first, source, seen)), 1)
        self.assertEqual(watch.detect_error_events(second, source, seen), [])


if __name__ == "__main__":
    unittest.main()
