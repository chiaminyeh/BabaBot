from __future__ import annotations

import asyncio
import aiohttp
import logging
import os
import datetime
from pathlib import Path

import discord
from discord.ext import commands, tasks

from scripts import monitor_bababot

# Define the log/incident file path
INCIDENT_FILE = Path(__file__).parent / "logs" / "incident_sync.txt"

OWNER_ID = 295288056276189185
MONITOR_INTERVAL_MINUTES = 2

logger = logging.getLogger(__name__)


class MonitorCog(commands.Cog):
    """In-process bababot monitor.

    This replaces the Hermes cron wrapper for normal runtime monitoring so
    Windows does not open a cmd window every tick.  Because it runs inside the
    bot process, it intentionally cannot recover from the bot process being
    fully offline; use scripts/restart_bababot.py manually for that case.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._lock = asyncio.Lock()
        # Ensure log directory exists
        INCIDENT_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.monitor_checker.start()

    def cog_unload(self):
        self.monitor_checker.cancel()

    async def _send_monitor_message(self, text: str) -> None:
        text = text.strip()
        if not text:
            return

        # 1. Sync to incident file for Hermes
        try:
            with open(INCIDENT_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now().isoformat()}]\n{text}\n\n")
        except Exception:
            logger.exception("Failed to sync monitor output to incident file")

        # 2. Existing Discord notification logic
        channel_id = os.getenv("BABABOT_MONITOR_CHANNEL_ID") or os.getenv("DISCORD_HOME_CHANNEL")
        destination = None
        if channel_id:
            try:
                destination = self.bot.get_channel(int(channel_id)) or await self.bot.fetch_channel(int(channel_id))
            except Exception:
                logger.exception("Failed to resolve monitor channel %s", channel_id)

        if destination is None:
            try:
                destination = await self.bot.fetch_user(OWNER_ID)
            except Exception:
                logger.exception("Failed to resolve monitor owner DM target")
                return

        for start in range(0, len(text), 1900):
            try:
                await destination.send(text[start:start + 1900])
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError, ConnectionError) as exc:
                # Discord/gateway reconnect noise is not an actionable Baba failure. The next
                # monitor tick can deliver future incidents after the connection recovers.
                logger.warning("Monitor delivery skipped during transient Discord connection failure: %s", type(exc).__name__)
                return

    @staticmethod
    def _run_monitor_once() -> tuple[int, str]:
        code, messages = monitor_bababot.run_monitor()
        return code, "\n\n".join(messages).strip()

    @tasks.loop(minutes=MONITOR_INTERVAL_MINUTES)
    async def monitor_checker(self):
        if self._lock.locked():
            logger.warning("Previous bababot monitor tick is still running; skipping this tick")
            return

        async with self._lock:
            loop = asyncio.get_running_loop()
            try:
                code, output = await loop.run_in_executor(None, self._run_monitor_once)
            except Exception:
                logger.exception("Bababot monitor tick crashed")
                await self._send_monitor_message("[bababot monitor] monitor task crashed; check bababot logs.")
                return

            if code != 0:
                logger.error("Bababot monitor exited with code %s", code)
                await self._send_monitor_message(f"[bababot monitor] monitor exited with code {code}; check bababot logs.")
                return

            if output:
                await self._send_monitor_message(output)

    @monitor_checker.before_loop
    async def before_monitor_checker(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(MonitorCog(bot))
    print("monitor cog loaded!")
