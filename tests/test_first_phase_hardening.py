import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

import help_cog
from schedule_cog import ScheduleCog, ScheduleView


class FirstPhaseHardeningTests(unittest.TestCase):
    def test_prefix_command_requires_owner_check(self):
        command = help_cog.help_cog.prefix
        self.assertTrue(command.checks, "prefix must not be publicly callable")

    def test_schedule_view_only_accepts_requester(self):
        view = ScheduleView(
            ["Monday, January 01, 2099 09:00 AM: private"],
            per_page=5,
            current_page=1,
            total_pages=1,
            requester_id=123,
        )

        allowed = SimpleNamespace(user=SimpleNamespace(id=123))
        denied_response = SimpleNamespace(send_message=AsyncMock())
        denied = SimpleNamespace(
            user=SimpleNamespace(id=456), response=denied_response
        )

        self.assertTrue(asyncio.run(view.interaction_check(allowed)))
        self.assertFalse(asyncio.run(view.interaction_check(denied)))
        denied_response.send_message.assert_awaited_once_with(
            "This schedule list belongs to another user.", ephemeral=True
        )

    def test_list_schedules_rejects_other_user_for_non_owner(self):
        cog = object.__new__(ScheduleCog)
        cog.load_schedules = lambda _user_id: [
            "Monday, January 01, 2099 09:00 AM: private\n"
        ]
        cog.bot = SimpleNamespace(is_owner=AsyncMock(return_value=False))
        response = SimpleNamespace(send_message=AsyncMock())
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=123), response=response
        )

        callback = ScheduleCog.list_schedules.callback
        asyncio.run(
            callback(cog, interaction, user=discord.Object(id=456))
        )

        response.send_message.assert_awaited_once_with(
            "You can only view your own schedules.", ephemeral=True
        )
        cog.bot.is_owner.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
