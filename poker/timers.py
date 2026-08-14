from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Coroutine, Any


class GenerationToken:
    """Immutable identity token for an action clock tick.

    If a player acts, hand finishes, or time bank is used, the generation sequence
    advances and any earlier sleeping task becomes an inert no-op.
    """

    def __init__(
        self,
        table_id: str,
        hand_id: str,
        action_sequence: int,
        actor_id: int,
    ):
        self.table_id = table_id
        self.hand_id = hand_id
        self.action_sequence = action_sequence
        self.actor_id = actor_id

    def matches(
        self,
        table_id: str,
        hand_id: str,
        action_sequence: int,
        actor_id: int,
    ) -> bool:
        return (
            self.table_id == table_id
            and self.hand_id == hand_id
            and self.action_sequence == action_sequence
            and self.actor_id == actor_id
        )

    def __repr__(self) -> str:
        return f"GenerationToken({self.table_id}, {self.hand_id}, seq={self.action_sequence}, actor={self.actor_id})"


class ActionClockController:
    """Manages player action timeouts and time bank extensions with race protection."""

    def __init__(
        self,
        table_id: str,
        timeout_seconds: int = 30,
        time_bank_seconds: int = 30,
    ):
        self.table_id = table_id
        self.timeout_seconds = timeout_seconds
        self.time_bank_seconds = time_bank_seconds
        self.action_sequence = 0
        self.current_task: asyncio.Task | None = None
        self.current_token: GenerationToken | None = None
        self.deadline: float = 0.0

    def start_turn(
        self,
        hand_id: str,
        actor_id: int,
        on_timeout: Callable[[GenerationToken], Coroutine[Any, Any, None]],
        duration_override: float | None = None,
    ) -> GenerationToken:
        """Cancel any previous timer and start a new action countdown."""
        self.cancel()
        self.action_sequence += 1
        duration = duration_override if duration_override is not None else float(self.timeout_seconds)
        self.deadline = time.time() + duration
        token = GenerationToken(
            table_id=self.table_id,
            hand_id=hand_id,
            action_sequence=self.action_sequence,
            actor_id=actor_id,
        )
        self.current_token = token
        self.current_task = asyncio.create_task(self._timer_worker(duration, token, on_timeout))
        return token

    def extend_time_bank(
        self,
        on_timeout: Callable[[GenerationToken], Coroutine[Any, Any, None]],
    ) -> tuple[bool, GenerationToken | None]:
        """Add time bank seconds to current turn with a new sequence generation."""
        if self.current_token is None:
            return False, None

        remaining = max(0.0, self.deadline - time.time())
        new_duration = remaining + float(self.time_bank_seconds)
        token = self.start_turn(
            hand_id=self.current_token.hand_id,
            actor_id=self.current_token.actor_id,
            on_timeout=on_timeout,
            duration_override=new_duration,
        )
        return True, token

    def cancel(self):
        """Cancel the active timer without triggering timeout callback."""
        current = asyncio.current_task()
        if (
            self.current_task
            and self.current_task is not current
            and not self.current_task.done()
        ):
            self.current_task.cancel()
        self.current_task = None
        self.current_token = None
        self.deadline = 0.0

    async def _timer_worker(
        self,
        duration: float,
        token: GenerationToken,
        on_timeout: Callable[[GenerationToken], Coroutine[Any, Any, None]],
    ):
        try:
            await asyncio.sleep(duration)
            if (
                self.current_token is not None
                and self.current_token.matches(
                    token.table_id, token.hand_id, token.action_sequence, token.actor_id
                )
            ):
                await on_timeout(token)
        except asyncio.CancelledError:
            pass
        except Exception:
            logging.exception("Poker action clock worker encountered an error")
