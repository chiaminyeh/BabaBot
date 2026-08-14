from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Coroutine, Any


# Configured pacing delays (in seconds)
PACING_BOT_THINKING = 2.0
PACING_BOT_ACTION = 2.0
PACING_STREET_REVEAL = 3.0
PACING_ALL_IN_RUNOUT = 2.5
PACING_FOLD_WIN = 6.0
PACING_SHOWDOWN = 12.0


class PacingController:
    """Coordinates table animation timings, event pauses, and ready skips."""

    def __init__(
        self,
        bot_thinking_delay: float = PACING_BOT_THINKING,
        bot_action_delay: float = PACING_BOT_ACTION,
        street_reveal_delay: float = PACING_STREET_REVEAL,
        all_in_runout_delay: float = PACING_ALL_IN_RUNOUT,
        fold_win_delay: float = PACING_FOLD_WIN,
        showdown_delay: float = PACING_SHOWDOWN,
    ):
        self.bot_thinking_delay = bot_thinking_delay
        self.bot_action_delay = bot_action_delay
        self.street_reveal_delay = street_reveal_delay
        self.all_in_runout_delay = all_in_runout_delay
        self.fold_win_delay = fold_win_delay
        self.showdown_delay = showdown_delay

        self.ready_players: set[int] = set()
        self.required_ready: set[int] = set()
        self._ready_event = asyncio.Event()

    def reset_ready_state(self, required_human_player_ids: list[int] | None = None):
        self.ready_players.clear()
        self.required_ready = set(required_human_player_ids or [])
        self._ready_event.clear()

    def mark_player_ready(self, player_id: int) -> bool:
        """Mark a human player as ready for next hand.

        Returns True if all required humans are now ready.
        """
        self.ready_players.add(player_id)
        if self.required_ready and self.required_ready.issubset(self.ready_players):
            self._ready_event.set()
            return True
        return False

    async def wait_bot_thinking(self):
        """Pause while bot is 'thinking'."""
        if self.bot_thinking_delay > 0:
            await asyncio.sleep(self.bot_thinking_delay)

    async def wait_bot_action(self):
        """Hold bot action in log before next action."""
        if self.bot_action_delay > 0:
            await asyncio.sleep(self.bot_action_delay)

    async def wait_street_reveal(self):
        """Pause when new community cards are revealed."""
        if self.street_reveal_delay > 0:
            await asyncio.sleep(self.street_reveal_delay)

    async def wait_all_in_runout_street(self):
        """Pause on each street during an all-in runout."""
        if self.all_in_runout_delay > 0:
            await asyncio.sleep(self.all_in_runout_delay)

    async def wait_fold_win(self):
        """Hold result screen when everyone else folded."""
        if self.fold_win_delay > 0:
            await asyncio.sleep(self.fold_win_delay)

    async def wait_showdown(self, required_human_player_ids: list[int] | None = None):
        """Hold showdown screen for duration, or finish early if all humans ready."""
        self.reset_ready_state(required_human_player_ids)
        if self.showdown_delay <= 0:
            return

        if not self.required_ready:
            # Only bots at table (or no humans)
            await asyncio.sleep(self.showdown_delay)
            return

        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=self.showdown_delay)
        except asyncio.TimeoutError:
            pass
