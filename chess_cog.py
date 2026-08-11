"""Stateless Discord Activity launcher with an optional Lichess fallback."""

from dataclasses import dataclass
import logging
from typing import Awaitable, Callable
from urllib.parse import urlsplit

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands


logger = logging.getLogger(__name__)
DISCORD_CHESS_ACTIVITY_URL = "https://discord.com/activities/832012774040141894"
LICHESS_OPEN_CHALLENGE_URL = "https://lichess.org/api/challenge/open"
LICHESS_TIMEOUT_SECONDS = 10
CHALLENGE_FORM = {
    "rated": "false",
    "variant": "standard",
    "clock.limit": "600",
    "clock.increment": "0",
}
FAILURE_MESSAGE = "❌ 建立棋局失敗，請稍後再試。 / Couldn't create the chess game. Please try again later."
ACTIVITY_MESSAGE = (
    "♟️ **Discord Chess in the Park / Discord 公園西洋棋**\n"
    "按下按鈕，在目前的 Discord 情境啟動棋局；初次使用可能會要求 App 授權。"
    "建議建立 **Locked Game**，並私下分享桌號。\n"
    "Open the Activity in the current Discord context. Initial App authorization may appear. "
    "For privacy, use a **Locked Game** and share the table code privately."
)


class LichessChallengeError(RuntimeError):
    """Raised when Lichess does not return a safe, usable challenge."""


@dataclass(frozen=True)
class ChallengeLinks:
    white: str
    black: str


def _validated_lichess_url(value: object) -> str:
    if not isinstance(value, str):
        raise LichessChallengeError("missing challenge URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "lichess.org"
        or not parsed.path.strip("/")
        or parsed.query
        or parsed.fragment
    ):
        raise LichessChallengeError("unsafe challenge URL")
    return value


def validate_challenge_response(status: int, payload: object) -> ChallengeLinks:
    if not 200 <= status < 300:
        raise LichessChallengeError(f"unexpected HTTP status {status}")
    if not isinstance(payload, dict):
        raise LichessChallengeError("invalid JSON response")
    return ChallengeLinks(
        white=_validated_lichess_url(payload.get("urlWhite")),
        black=_validated_lichess_url(payload.get("urlBlack")),
    )


async def request_open_challenge(session: aiohttp.ClientSession) -> ChallengeLinks:
    """Create one anonymous casual standard 10+0 challenge, sending no user data."""
    try:
        async with session.post(
            LICHESS_OPEN_CHALLENGE_URL,
            data=dict(CHALLENGE_FORM),
            timeout=aiohttp.ClientTimeout(total=LICHESS_TIMEOUT_SECONDS),
        ) as response:
            payload = await response.json()
            return validate_challenge_response(response.status, payload)
    except LichessChallengeError:
        raise
    except (aiohttp.ClientError, TimeoutError, ValueError, TypeError) as exc:
        raise LichessChallengeError("Lichess request failed") from exc


async def create_open_challenge() -> ChallengeLinks:
    async with aiohttp.ClientSession() as session:
        return await request_open_challenge(session)


def _link_view(label: str, url: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label=label, style=discord.ButtonStyle.link, url=url, emoji="♟️"))
    return view


class ChessCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        *,
        challenge_creator: Callable[[], Awaitable[ChallengeLinks]] = create_open_challenge,
    ):
        self.bot = bot
        self.challenge_creator = challenge_creator

    @app_commands.command(name="chess", description="Launch Discord Chess or create an anonymous Lichess 10+0 game")
    @app_commands.describe(mode="Choose Discord Chess in the Park or the Lichess fallback")
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Discord Activity", value="discord"),
            app_commands.Choice(name="Lichess 10+0", value="lichess"),
        ]
    )
    async def chess(self, interaction: discord.Interaction, mode: str = "discord"):
        if mode != "lichess":
            await interaction.response.send_message(
                ACTIVITY_MESSAGE,
                view=_link_view("開啟棋局 / Open Chess", DISCORD_CHESS_ACTIVITY_URL),
            )
            return

        await interaction.response.defer(thinking=True)
        try:
            links = await self.challenge_creator()
        except Exception:
            logger.exception("Failed to create Lichess open challenge")
            await interaction.edit_original_response(content=FAILURE_MESSAGE, view=None)
            return

        public_message = (
            "♟️ **Lichess 10+0 公開棋局 / Open game**\n"
            "對手請按下方公開按鈕；發起者請使用稍後收到的私人按鈕。棋局由 Lichess 託管並進行。\n"
            "Opponent: use the public button below. Inviter: use the private button sent next. "
            "The game is hosted and played on Lichess.\n"
            "Lichess 是第三方服務；其隱私政策適用，可能處理連線與裝置資料。 / "
            "Lichess is a third-party host; its privacy policy applies and it may process connection/device data."
        )
        await interaction.edit_original_response(
            content=public_message,
            view=_link_view("對手加入 / Opponent joins", links.black),
        )
        await interaction.followup.send(
            "🔒 發起者請用這個私人按鈕加入白方。 / Inviter: join as White with this private button.",
            view=_link_view("發起者加入 / Inviter joins", links.white),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ChessCog(bot))
