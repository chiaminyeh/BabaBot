from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Literal
import discord
from discord import app_commands
from discord.ext import commands

from poker.models import (
    Player,
    GameMode,
    TableConfig,
    BlindLevel,
)
from poker.service import PokerGameInstance
from trpg.i18n import t as _i18n_t


def _poker_text(key: str, fallback: str, *, lang: str = "en", **kwargs) -> str:
    return _i18n_t(lang, f"poker.{key}", fallback, **kwargs)


def _user_language(user: Any) -> str:
    locale = str(getattr(user, "locale", "en")).lower()
    return "zh" if locale.startswith("zh") else "en"


async def _respond(
    interaction: discord.Interaction,
    content: str,
    *,
    ephemeral: bool = True,
):
    response = interaction.response
    if not getattr(response, "is_done", lambda: False)():
        return await response.send_message(content, ephemeral=ephemeral)
    followup = getattr(interaction, "followup", None)
    if followup is not None:
        return await followup.send(content, ephemeral=ephemeral)
    return None


class PokerLobbyView(discord.ui.View):
    """Small lobby control surface; gameplay continues on the same message."""

    def __init__(self, cog: "PokerCog", guild_id: int):
        super().__init__(timeout=600)
        self.cog = cog
        self.guild_id = guild_id

    @discord.ui.button(
        label="Start table",
        style=discord.ButtonStyle.success,
        custom_id="poker_lobby_start",
    )
    async def start_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ):
        if not getattr(interaction.response, "is_done", lambda: False)():
            await interaction.response.defer(ephemeral=True)
        started, message = await self.cog._begin_table(
            self.guild_id, interaction.user.id
        )
        await _respond(interaction, message, ephemeral=True)
        if started:
            self.stop()

    @discord.ui.button(
        label="Leave lobby",
        style=discord.ButtonStyle.danger,
        custom_id="poker_lobby_leave",
    )
    async def leave_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ):
        left, message = self.cog._leave_lobby_player(
            self.guild_id, interaction.user.id
        )
        await _respond(interaction, message, ephemeral=True)
        if left:
            await self.cog.refresh_lobby(self.guild_id)

    async def on_timeout(self):
        game = self.cog.games.get(self.guild_id)
        if not game or game.get("state") != "lobby":
            return
        self.cog._close_lobby(self.guild_id)
        message = game.get("lobby_message")
        if message is not None:
            try:
                await message.edit(
                    embed=discord.Embed(
                        title="♠ Poker lobby expired",
                        description="No table was started within 10 minutes.",
                        color=discord.Color.dark_grey(),
                    ),
                    view=None,
                )
            except (discord.NotFound, discord.HTTPException):
                logging.warning("Unable to mark expired poker lobby", exc_info=True)


class PokerCog(commands.Cog):
    """Discord Cog for Poker (Texas Hold'em Cash Games & Tournaments)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.baba = getattr(bot, "baba", None)
        self.bank = getattr(self.baba, "bank", {}) if self.baba else {}
        self.money_name = getattr(self.baba, "money_name", "bababucks") if self.baba else "bababucks"
        self.minimum_bet = 10
        self.game_timeout = 600  # 10 mins
        self.games: dict[int, dict[str, Any]] = {}
        self.lobbies: dict[str, dict[str, Any]] = {}

        if not getattr(self, "_recovered_escrows", False):
            self.recover_orphaned_escrows()
            self._recovered_escrows = True

    def recover_orphaned_escrows(self) -> int:
        """Scan and refund any dangling open poker escrows on startup."""
        if not self.baba:
            return 0
        state = getattr(self.baba, "economy_state", {})
        escrows = state.get("casino_escrows", {})
        recovered = 0
        for escrow_id, data in list(escrows.items()):
            if data.get("game_type") == "poker" and data.get("state") == "open":
                try:
                    escrow = self.baba.new_escrow(escrow_id=escrow_id, game_type="poker")
                    if escrow.refund():
                        recovered += 1
                except Exception:
                    logging.exception(f"Failed to recover orphaned escrow: {escrow_id}")
        return recovered

    @staticmethod
    def build_bot_player(guild_id: int, index: int) -> Player:
        """Generate a virtual bot player with a deterministic negative ID."""
        bot_id = -(abs(guild_id) * 100 + index + 1)
        name = f"BabaBot {index + 1}"
        user = SimpleNamespace(id=bot_id, name=name, bot=True)
        return Player(user, is_bot=True)

    def ensure_bot_bankroll(self, player: Player, target: int):
        if not self.baba or not player.is_bot:
            return
        balance = self.baba.get_money(player.id)
        if balance < target:
            self.baba.add_money(player.id, target - balance)

    def lobby_embed(self, game: dict[str, Any]) -> discord.Embed:
        config: TableConfig = game["config"]
        mode_name = (
            "Freezeout Tournament"
            if config.mode == GameMode.TOURNAMENT
            else "Cash Game"
        )
        players: list[Player] = game["players"]
        seats = "\n".join(
            f"**Seat {index + 1}** · {player.name}"
            for index, player in enumerate(players)
        ) or "No players"
        embed = discord.Embed(
            title=f"♠ Poker Lobby · Table {game['code']}",
            description=(
                f"Mode: **{mode_name}** · Blinds: **{config.small_blind}/{config.big_blind}**\n"
                f"Buy-in: **{game['buyin']} {self.money_name}** · Seats: "
                f"**{len(players)}/{config.max_seats}**\n\n"
                f"Join with `/poker join code:{game['code']}`. Open seats become "
                "BabaBots when the host starts."
            ),
            color=discord.Color.dark_green(),
        )
        embed.add_field(name="Players", value=seats, inline=False)
        embed.set_footer(text="Only the host can start · lobby expires in 10 minutes")
        return embed

    async def refresh_lobby(self, guild_id: int):
        game = self.games.get(guild_id)
        if not game or game.get("state") != "lobby":
            return
        message = game.get("lobby_message")
        if message is not None:
            try:
                await message.edit(
                    embed=self.lobby_embed(game), view=game.get("lobby_view")
                )
            except (discord.NotFound, discord.HTTPException):
                logging.warning("Unable to refresh poker lobby", exc_info=True)

    def _leave_lobby_player(self, guild_id: int, user_id: int) -> tuple[bool, str]:
        game = self.games.get(guild_id)
        if not game or game.get("state") != "lobby":
            return False, "This poker lobby is no longer open."
        if user_id == game.get("host_id"):
            return False, "The host must use `/poker close` to close the lobby."
        players: list[Player] = game["players"]
        player = next((item for item in players if item.id == user_id), None)
        if player is None:
            return False, "You are not seated in this lobby."
        # Lobby balances are only checked, not reserved.  The table performs
        # one atomic debit for every seat when the host starts it.
        players.remove(player)
        game["last_action"] = datetime.now()
        return True, "You left the poker lobby."

    async def _begin_table(
        self, guild_id: int, actor_id: int
    ) -> tuple[bool, str]:
        game = self.games.get(guild_id)
        if not game or not game.get("active") or game.get("state") != "lobby":
            return False, "This poker lobby is no longer open."
        if actor_id != game.get("host_id"):
            return False, "Only the lobby host can start this table."
        if game.get("starting"):
            return False, "The poker table is already starting."

        game["starting"] = True
        config: TableConfig = game["config"]
        players: list[Player] = game["players"]
        try:
            existing_bots = sum(player.is_bot for player in players)
            while len(players) < config.max_seats:
                bot = self.build_bot_player(guild_id, existing_bots)
                players.append(bot)
                existing_bots += 1

            buy_in = int(game["buyin"])
            if self.baba:
                for player in players:
                    if player.is_bot:
                        self.ensure_bot_bankroll(player, buy_in)
                table_escrow = self.baba.new_escrow(
                    escrow_id=f"poker:table:{guild_id}:{game['code']}",
                    game_type="poker",
                )
                if not table_escrow.try_debit_many(
                    {player.id: buy_in for player in players}
                ):
                    # Remove provisional bot seats so humans can correct their
                    # balances and retry the same still-open lobby.
                    game["players"] = [player for player in players if not player.is_bot]
                    return False, "A seated player no longer has enough funds for the buy-in."
                game["table_escrow"] = table_escrow
            else:
                table_escrow = None

            table_stack = (
                config.starting_stack
                if config.mode == GameMode.TOURNAMENT
                else buy_in
            )
            for index, player in enumerate(players):
                player.seat_index = index
                player.stack = table_stack

            channel = game.get("channel") or self.bot.get_channel(game["channel_id"])
            if channel is None:
                if table_escrow is not None:
                    table_escrow.refund()
                self._close_lobby(guild_id)
                return False, "The poker channel is no longer available; buy-ins were returned."

            poker = PokerGameInstance(
                channel,
                players,
                self,
                config=config,
                starting_stack=table_stack,
            )
            poker.table_escrow = table_escrow
            poker.table_message = game.get("lobby_message")
            game["table_escrow"] = table_escrow
            game["instance"] = poker
            game["state"] = "playing"
            game["last_action"] = datetime.now()
            lobby_view = game.get("lobby_view")
            if lobby_view is not None:
                lobby_view.stop()
            await poker.play_game()
            return True, "Poker table started."
        except Exception:
            logging.exception("Poker table failed during startup guild=%s", guild_id)
            table_escrow = game.get("table_escrow")
            if table_escrow is not None and table_escrow.state == "open":
                table_escrow.refund()
            self._close_lobby(guild_id)
            return False, "The table failed to start safely; all reserved buy-ins were returned."
        finally:
            game["starting"] = False

    def _table_player(self, guild_id: int, user_id: int) -> tuple[PokerGameInstance | None, Player | None]:
        game = self.games.get(guild_id)
        inst = game.get("instance") if game and game.get("active") else None
        if inst is None:
            return None, None
        return inst, inst.get_player(user_id)

    def _close_lobby(self, guild_id: int):
        games = getattr(self, "games", {})
        game = games.get(guild_id)
        if game:
            code = game.get("lobby_code") or game.get("code")
            lobbies = getattr(self, "lobbies", {})
            if code in lobbies:
                del lobbies[code]
            game["active"] = False
            game["state"] = "closed"
            game["instance"] = None

    async def _refund_bets(self, game_inst: PokerGameInstance) -> bool:
        if not game_inst:
            return True
        return await game_inst.refund_game("admin close")

    # Command Group /poker
    poker_group = app_commands.Group(name="poker", description="Texas Hold'em Poker games and tournaments")

    @poker_group.command(name="create", description="Create a new Poker Cash Game or Tournament table")
    @app_commands.describe(
        mode="Game mode: Cash Game or Freezeout Tournament",
        blinds="Blinds format (e.g. '5/10')",
        buyin="Buy-in amount in Bababucks (default 1000 for Cash, 100 for Tournament)",
        seats="Total seats; open seats become BabaBots when the host starts",
    )
    async def create_table(
        self,
        interaction: discord.Interaction,
        mode: Literal["cash", "tournament"] = "cash",
        blinds: str = "5/10",
        buyin: int | None = None,
        seats: app_commands.Range[int, 2, 10] = 6,
    ):
        guild_id = interaction.guild_id
        if guild_id is None:
            return await interaction.response.send_message(
                "Poker tables can only be created in a server.", ephemeral=True
            )
        if guild_id in self.games and self.games[guild_id].get("active"):
            return await interaction.response.send_message(
                "A poker game or lobby is already active in this server.", ephemeral=True
            )

        try:
            parts = [int(x.strip()) for x in blinds.split("/")]
            if len(parts) != 2:
                raise ValueError
            sb, bb = parts
            if sb <= 0 or bb <= 0 or sb > bb:
                raise ValueError
        except (TypeError, ValueError):
            return await interaction.response.send_message(
                "Blinds must be two positive numbers such as `5/10`, with SB no larger than BB.",
                ephemeral=True,
            )

        game_mode = GameMode.TOURNAMENT if mode == "tournament" else GameMode.CASH
        default_buyin = 100 if game_mode == GameMode.TOURNAMENT else bb * 100
        actual_buyin = buyin if buyin is not None and buyin > 0 else default_buyin

        if game_mode == GameMode.CASH and not (bb * 20 <= actual_buyin <= bb * 200):
            return await interaction.response.send_message(
                f"Cash buy-in must be between {bb * 20} and {bb * 200} (20–200 BB).",
                ephemeral=True,
            )

        if self.baba and self.baba.get_money(interaction.user.id) < actual_buyin:
            return await interaction.response.send_message(
                f"You need at least {actual_buyin} {self.money_name} to create this table.",
                ephemeral=True,
            )

        code = secrets.token_hex(2).upper()
        config = TableConfig(
            mode=game_mode,
            code=code,
            language=_user_language(interaction.user),
            small_blind=sb,
            big_blind=bb,
            starting_stack=1000 if game_mode == GameMode.TOURNAMENT else actual_buyin,
            buy_in_amount=actual_buyin,
            max_seats=int(seats),
        )

        host_player = Player(interaction.user)
        host_player.stack = config.starting_stack

        lobby_data = {
            "guild_id": guild_id,
            "channel_id": interaction.channel_id,
            "host_id": interaction.user.id,
            "config": config,
            "code": code,
            "lobby_code": code,
            "players": [host_player],
            "buyin": actual_buyin,
            "table_escrow": None,
            "active": True,
            "state": "lobby",
            "last_action": datetime.now(),
            "instance": None,
            "starting": False,
            "channel": getattr(interaction, "channel", None),
            "lobby_message": None,
            "lobby_view": None,
        }
        self.games[guild_id] = lobby_data
        self.lobbies[code] = lobby_data

        view = PokerLobbyView(self, guild_id)
        lobby_data["lobby_view"] = view
        await interaction.response.send_message(
            embed=self.lobby_embed(lobby_data), view=view
        )
        try:
            lobby_data["lobby_message"] = await interaction.original_response()
        except (discord.NotFound, discord.HTTPException, AttributeError):
            logging.warning("Unable to retain poker lobby message", exc_info=True)

    @poker_group.command(name="join", description="Join an active poker table by code")
    @app_commands.describe(code="4-character table code")
    async def join_table(self, interaction: discord.Interaction, code: str):
        code = code.strip().upper()
        lobby = self.lobbies.get(code)
        if (
            not lobby
            or not lobby.get("active")
            or lobby.get("state") != "lobby"
            or interaction.guild_id != lobby.get("guild_id")
        ):
            return await interaction.response.send_message(
                "No open poker lobby found with that code.", ephemeral=True
            )

        players: list[Player] = lobby["players"]
        if any(p.id == interaction.user.id for p in players):
            return await interaction.response.send_message(
                "You are already in this poker lobby.", ephemeral=True
            )

        config: TableConfig = lobby["config"]
        if len(players) >= config.max_seats:
            return await interaction.response.send_message(
                f"This poker table is already full ({config.max_seats} players max).",
                ephemeral=True,
            )

        buyin = lobby["buyin"]
        if self.baba and self.baba.get_money(interaction.user.id) < buyin:
            return await interaction.response.send_message(
                f"You need at least {buyin} {self.money_name} to join.", ephemeral=True
            )

        new_player = Player(interaction.user)
        new_player.stack = config.starting_stack
        players.append(new_player)
        lobby["last_action"] = datetime.now()

        await interaction.response.send_message(
            f"You joined table `{code}` in Seat {len(players)}.", ephemeral=True
        )
        await self.refresh_lobby(lobby["guild_id"])

    @poker_group.command(name="start", description="Start your poker lobby now")
    async def start_table(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        if guild_id is None:
            return await interaction.response.send_message(
                "Poker tables can only be started in a server.", ephemeral=True
            )
        if not getattr(interaction.response, "is_done", lambda: False)():
            await interaction.response.defer(ephemeral=True)
        _started, message = await self._begin_table(
            guild_id, interaction.user.id
        )
        await _respond(interaction, message, ephemeral=True)

    @poker_group.command(name="status", description="Check active poker table status")
    async def table_status(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id or 0
        game = self.games.get(guild_id)
        if not game or not game.get("active"):
            return await interaction.response.send_message(
                "No active poker game or lobby in this server.", ephemeral=True
            )

        inst: PokerGameInstance | None = game.get("instance")
        if inst:
            embed = discord.Embed(
                title=f"♠️ Table Status · Hand #{inst.hand_number}",
                description=f"Street: **{inst.street}** · Pot: **{inst.pot_total}** {self.money_name}",
                color=discord.Color.dark_green(),
            )
            embed.add_field(name="Current Actor", value=inst.current_actor.name if inst.current_actor else "None", inline=True)
            embed.add_field(name="Highest Bet", value=str(inst.highest), inline=True)
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            players = game.get("players", [])
            return await interaction.response.send_message(
                f"♠️ Lobby `{game.get('code')}` has {len(players)} players waiting to start.",
                ephemeral=True,
            )

    @poker_group.command(name="leave", description="Leave and cash out after the current hand")
    async def leave_table(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id or 0
        inst, player = self._table_player(guild_id, interaction.user.id)
        if not player or not inst:
            game = self.games.get(guild_id)
            if game and game.get("state") == "lobby":
                left, message = self._leave_lobby_player(
                    guild_id, interaction.user.id
                )
                if left:
                    await self.refresh_lobby(guild_id)
                return await interaction.response.send_message(
                    message, ephemeral=True
                )
            return await interaction.response.send_message(
                "You are not seated at an active poker table.", ephemeral=True
            )

        can_leave, msg = inst.policy.can_leave(player)
        if not can_leave:
            return await interaction.response.send_message(msg, ephemeral=True)

        player.leave_after_hand = True
        await interaction.response.send_message(
            "The cash table will close after this hand so every stack can be paid out safely.",
            ephemeral=True,
        )

    @poker_group.command(name="sitout", description="Sit out from the next hand without leaving")
    async def sitout_table(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id or 0
        inst, player = self._table_player(guild_id, interaction.user.id)
        if not player or not inst:
            return await interaction.response.send_message(
                "You are not seated at an active poker table.", ephemeral=True
            )
        player.sit_out_next_hand = True
        await interaction.response.send_message(
            "You will sit out the next hand. Use `/poker sitin` to return.", ephemeral=True
        )

    @poker_group.command(name="sitin", description="Return to active play for the next hand")
    async def sitin_table(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id or 0
        inst, player = self._table_player(guild_id, interaction.user.id)
        if not player or not inst:
            return await interaction.response.send_message(
                "You are not seated at an active poker table.", ephemeral=True
            )
        player.sitting_out = False
        player.sit_out_next_hand = False
        player.leave_after_hand = False
        player.timeout_streak = 0
        player.away = False
        await interaction.response.send_message("You are back in for the next hand.", ephemeral=True)

    @poker_group.command(name="rebuy", description="Queue a rebuy or top-up for your next hand")
    @app_commands.describe(amount="Amount of chips to add")
    async def rebuy_chips(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[int, 20, 100000],
    ):
        guild_id = interaction.guild_id or 0
        inst, player = self._table_player(guild_id, interaction.user.id)
        if not player or not inst:
            return await interaction.response.send_message(
                "You are not seated at an active poker table.", ephemeral=True
            )

        can_rebuy, msg = inst.policy.can_rebuy(player, amount)
        if not can_rebuy:
            return await interaction.response.send_message(msg, ephemeral=True)

        if inst.table_escrow and not inst.table_escrow.try_debit(player.id, amount):
            return await interaction.response.send_message(
                f"You need at least {amount} {self.money_name} in your bank to rebuy.",
                ephemeral=True,
            )

        player.pending_rebuy += amount
        player.sitting_out = False
        await interaction.response.send_message(
            f"Queued **{amount}** chips for your next hand.", ephemeral=True
        )

    @poker_group.command(name="rules", description="View poker rules and hand rankings")
    async def view_rules(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="♠ Texas Hold'em Rules & Hand Rankings",
            description=(
                "Texas Hold'em: Make the best 5-card hand using your 2 hole cards and 5 community cards.\n\n"
                "**Hand Rankings:**\n"
                "1. Royal Flush (`A K Q J 10` suited)\n"
                "2. Straight Flush (5 consecutive suited cards)\n"
                "3. Four of a Kind\n"
                "4. Full House (Three of a Kind + One Pair)\n"
                "5. Flush (5 cards of the same suit)\n"
                "6. Straight (5 consecutive cards, A-2-3-4-5 is 5-high)\n"
                "7. Three of a Kind\n"
                "8. Two Pair\n"
                "9. One Pair\n"
                "10. High Card"
            ),
            color=discord.Color.dark_green(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @poker_group.command(name="close", description="Admin force close and refund the current table")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def force_close_table(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id or 0
        game = self.games.get(guild_id)
        if not game or not game.get("active"):
            return await interaction.response.send_message(
                "No active game to close.", ephemeral=True
            )

        inst: PokerGameInstance | None = game.get("instance")
        if inst:
            if inst.terminal_state == "open" and not await inst.refund_game(
                "admin force close"
            ):
                return await interaction.response.send_message(
                    "Game could not be ended safely; the escrow remains active."
                )
            if inst.config.mode == GameMode.CASH:
                await inst.close_session("admin force close")

        table_escrow = game.get("table_escrow")
        if table_escrow and table_escrow.state == "open":
            table_escrow.refund()

        self._close_lobby(guild_id)
        await interaction.response.send_message("Game force-ended. Bets returned.")

    # Legacy command aliases
    @app_commands.command(name="poker_rules")
    async def legacy_poker_rules(self, interaction: discord.Interaction):
        await self.view_rules.callback(self, interaction)

    @app_commands.command(name="poker_start")
    async def legacy_poker_start(self, interaction: discord.Interaction):
        await self.start_table.callback(self, interaction)

    @app_commands.command(name="poker_sitout")
    async def legacy_poker_sitout(self, interaction: discord.Interaction):
        await self.sitout_table.callback(self, interaction)

    @app_commands.command(name="poker_sitin")
    async def legacy_poker_sitin(self, interaction: discord.Interaction):
        await self.sitin_table.callback(self, interaction)

    @app_commands.command(name="poker_rebuy")
    async def legacy_poker_rebuy(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[int, 20, 100000],
    ):
        await self.rebuy_chips.callback(self, interaction, amount)

    @app_commands.command(name="poker_end")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def end_poker(self, interaction: discord.Interaction):
        await self.force_close_table.callback(self, interaction)


async def setup(bot: commands.Bot):
    cog = PokerCog(bot)
    await bot.add_cog(cog)
