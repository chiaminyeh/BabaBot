"""Classification helpers for Discord interaction transport failures."""

from __future__ import annotations

import asyncio

import aiohttp
import discord


def is_transient_interaction_error(error: BaseException) -> bool:
    """Return True only for expired tokens or transient Discord/network transport failures."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, discord.NotFound) and getattr(current, "code", None) == 10062:
            return True
        if isinstance(current, (aiohttp.ClientConnectionError, asyncio.TimeoutError, ConnectionError)):
            return True
        if isinstance(current, discord.HTTPException) and 500 <= getattr(current, "status", 0) < 600:
            return True
        current = (
            getattr(current, "original", None)
            or getattr(current, "__cause__", None)
            or getattr(current, "__context__", None)
        )
    return False
