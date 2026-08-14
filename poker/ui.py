from __future__ import annotations

import inspect
import time
from typing import TYPE_CHECKING, Any, Callable, Coroutine
import discord
from poker.models import (
    Player,
    Street,
    GameMode,
    ActionLogEntry,
    LegalActionSummary,
    HandResult,
    TournamentRank,
)
from poker.rules import HandEvaluator

if TYPE_CHECKING:
    from poker.service import PokerGameInstance


def _poker_i18n(key: str, fallback: str, *, lang: str = "en", **kwargs) -> str:
    from trpg.i18n import t as _i18n_t
    return _i18n_t(lang, f"poker.{key}", fallback, **kwargs)


def _user_lang(user: Any) -> str:
    locale = str(getattr(user, "locale", "en")).lower()
    return "zh" if locale.startswith("zh") else "en"


async def _safe_respond(
    interaction: discord.Interaction,
    content: str | None = None,
    *,
    embed: discord.Embed | None = None,
    view: discord.ui.View | None = None,
    ephemeral: bool = True,
):
    """Reply to an interaction safely regardless of whether it was deferred."""
    response = interaction.response
    send_kwargs: dict[str, Any] = {"ephemeral": ephemeral}
    if content is not None:
        send_kwargs["content"] = content
    if embed is not None:
        send_kwargs["embed"] = embed
    if view is not None:
        send_kwargs["view"] = view
    is_done = getattr(response, "is_done", lambda: False)()
    if not is_done:
        return await response.send_message(**send_kwargs)
    followup = getattr(interaction, "followup", None)
    if followup is not None:
        return await followup.send(**send_kwargs)
    return None


async def _safe_defer(interaction: discord.Interaction, ephemeral: bool = False) -> bool:
    response = interaction.response
    if getattr(response, "is_done", lambda: False)():
        return False
    defer = getattr(response, "defer", None)
    if defer is None:
        return False
    try:
        res = defer(ephemeral=ephemeral)
    except TypeError:
        res = defer()
    if inspect.isawaitable(res):
        await res
    return True


class TableEmbedBuilder:
    """Builds clean, high-clarity Discord embeds for the poker table."""

    @staticmethod
    def build_table_embed(
        game: "PokerGameInstance",
        lang: str = "en",
    ) -> discord.Embed:
        mode_label = "Cash" if game.config.mode == GameMode.CASH else "Tournament"
        code_label = game.config.code or str(game.channel_id)[-4:]
        street_title = game.street.value.title() if hasattr(game.street, "value") else str(game.street).title()

        title_key = (
            "board.title_cash"
            if game.config.mode == GameMode.CASH
            else "board.title_tournament"
        )
        title = _poker_i18n(
            title_key,
            f"♠ NLH {mode_label} · Table {{code}} · Hand #{{hand}}",
            lang=lang,
            code=code_label,
            hand=game.hand_number,
        )
        if game.config.mode == GameMode.TOURNAMENT and getattr(game, "tournament_policy", None):
            level = game.tournament_policy.current_blind_level
            subtitle = f"Level {level.level} (Blinds {level.small_blind}/{level.big_blind}{f' + Ante {level.ante}' if level.ante else ''}) · Pot **{game.pot_total}** · {street_title}"
        else:
            subtitle = _poker_i18n(
                "board.blinds_pot",
                "Blinds {sb}/{bb} · Pot **{pot}** {currency} · {street}",
                lang=lang,
                sb=game.small_blind,
                bb=game.big_blind,
                pot=game.pot_total,
                currency=game.money_name,
                street=street_title,
            )

        # Board cards
        if game.community_cards:
            board_str = " ".join(f"`[{card}]`" for card in game.community_cards)
            missing = 5 - len(game.community_cards)
            if missing > 0:
                board_str += " " + " ".join("`[—]`" for _ in range(missing))
        else:
            board_str = " ".join("`[—]`" for _ in range(5))

        embed = discord.Embed(
            title=title,
            description=f"{subtitle}\n\n**BOARD**\n{board_str}",
            color=discord.Color.dark_green() if game.terminal_state == "open" else discord.Color.gold(),
        )

        # TO ACT section
        actor = game.current_actor
        if actor and game.terminal_state == "open":
            legal = game.get_legal_actions(actor)
            deadline_ts = int(game.action_clock.deadline) if game.action_clock.deadline > 0 else int(time.time() + 30)
            call_text = (
                _poker_i18n(
                    "action.call",
                    "Call {amount}",
                    lang=lang,
                    amount=legal.call_amount,
                )
                if legal.can_call
                else ("Check" if legal.can_check else "Fold")
            )
            raise_text = f" · Min raise to {legal.min_raise_to}" if legal.can_raise else ""
            pos_text = f" · {actor.position}" if actor.position else ""
            bot_tag = " [BOT]" if actor.is_bot else ""
            time_bank_tag = " (+30s Bank available)" if legal.can_time_bank else ""

            if actor.is_bot:
                to_act_value = _poker_i18n(
                    "board.bot_thinking",
                    "{name} is thinking…",
                    lang=lang,
                    name=actor.name,
                )
            else:
                to_act_value = _poker_i18n(
                    "board.to_act_desc",
                    "**{name}**{bot}{pos} · Stack **{stack}**\n{call_text}{raise_text} · ⏱ <t:{deadline}:R>{time_bank}",
                    lang=lang,
                    name=actor.name,
                    bot=bot_tag,
                    pos=pos_text,
                    stack=actor.stack or 0,
                    call_text=call_text,
                    raise_text=raise_text,
                    deadline=deadline_ts,
                    time_bank=time_bank_tag,
                )
            embed.add_field(
                name="⭐ TO ACT",
                value=to_act_value,
                inline=False,
            )
        elif game.terminal_state != "open":
            embed.add_field(
                name="🏁 RESULT",
                value=f"**{game.last_result_summary or 'Hand complete.'}**",
                inline=False,
            )

        # SEATS section
        seat_lines: list[str] = []
        for p in sorted(game.players, key=lambda x: x.seat_index if x.seat_index is not None else 0):
            status_tag = ""
            if p.finish_rank is not None:
                status_tag = f"BUSTED ({p.finish_rank}{'st' if p.finish_rank==1 else 'nd' if p.finish_rank==2 else 'rd' if p.finish_rank==3 else 'th'})"
            elif p.sitting_out:
                status_tag = "SITTING OUT"
            elif p.away:
                status_tag = "AWAY"
            elif p.folded:
                status_tag = "FOLDED"
            elif p.all_in:
                status_tag = "ALL-IN"
            elif p.id == (actor.id if actor else None) and game.terminal_state == "open":
                status_tag = "TO ACT"

            pos = f" `{p.position}`" if p.position else ""
            bot = " 🤖" if p.is_bot else ""
            seat_num = p.seat_index + 1 if p.seat_index is not None else 1
            bet_info = f" · Bet {p.bet}" if p.bet > 0 else ""
            stack_info = f"Stack {p.stack}" if p.stack is not None else ""
            tag_str = f" · *{status_tag}*" if status_tag else ""

            seat_lines.append(
                f"`S{seat_num}`{pos} **{p.name}**{bot} — {stack_info}{bet_info}{tag_str}"
            )

        active_count = sum(
            1 for p in game.players if not p.folded and p.finish_rank is None
        )
        embed.add_field(
            name=_poker_i18n(
                "board.seats_header",
                "🪑 SEATS ({active}/{total} Live)",
                lang=lang,
                active=active_count,
                total=len(game.players),
            ),
            value="\n".join(seat_lines) if seat_lines else "No seats",
            inline=False,
        )

        # RECENT ACTIONS section
        if game.action_log:
            recent = game.action_log[-5:]
            embed.add_field(
                name="📜 RECENT ACTIONS",
                value="\n".join(f"• {entry.text}" for entry in recent),
                inline=False,
            )

        # SHOWDOWN HANDS (if at showdown)
        if game.terminal_state != "open" and game.last_showdown_hands:
            embed.add_field(
                name="🃏 REVEALED HANDS",
                value="\n".join(game.last_showdown_hands),
                inline=False,
            )

        if getattr(game, "tournament_ranks", None):
            embed.add_field(
                name="🏆 TOURNAMENT RESULTS",
                value="\n".join(
                    _poker_i18n(
                        "tournament.prize_line",
                        "`{rank}.` **{name}** — Prize: **{prize}** {currency}",
                        lang=lang,
                        rank=rank.rank,
                        name=rank.name,
                        prize=rank.prize,
                        currency=game.money_name,
                    )
                    for rank in game.tournament_ranks
                ),
                inline=False,
            )

        embed.set_footer(text=_poker_i18n(
            "board.footer",
            "Use 'My Hand' to check private cards • Dynamic clock updates live",
            lang=lang,
        ))
        return embed

    @staticmethod
    def build_my_hand_embed(
        player: Player,
        community: list[str],
        pot_total: int,
        highest_bet: int,
        lang: str = "en",
    ) -> discord.Embed:
        if not player.hand:
            cards_display = "*(No cards dealt)*"
            eval_desc = "Waiting for next hand"
        else:
            cards_display = " ".join(f"`[{card}]`" for card in player.hand)
            all_cards = player.hand + community
            score = HandEvaluator.rank_hand(all_cards)
            eval_desc = HandEvaluator.hand_name(score, lang=lang)

        to_call = max(0, highest_bet - player.bet)
        pos = player.position or "—"
        stack = player.stack if player.stack is not None else 0

        embed = discord.Embed(
            title=_poker_i18n(
                "myhand.title",
                "🃏 Your Hand · {name}",
                lang=lang,
                name=player.name,
            ),
            description=f"**Hole Cards**\n{cards_display}\n\n**Best 5-Card Strength**\n✨ **{eval_desc}**",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Position", value=f"`{pos}`", inline=True)
        embed.add_field(name="Stack", value=f"**{stack}**", inline=True)
        embed.add_field(name="To Call", value=f"**{to_call}**", inline=True)
        embed.set_footer(text="Private info • Only you can see this message")
        return embed


class PokerActionView(discord.ui.View):
    """Main interactive action bar for the table embed."""

    def __init__(self, game: "PokerGameInstance"):
        super().__init__(timeout=None)
        self.game = game
        self.refresh_buttons()

    def refresh_buttons(self):
        actor = self.game.current_actor
        human_turn = (
            actor is not None
            and not actor.is_bot
            and not actor.folded
            and self.game.terminal_state == "open"
        )
        legal = self.game.get_legal_actions(actor) if actor else LegalActionSummary()
        lang = self.game.config.language

        for child in self.children:
            if not isinstance(child, discord.ui.Button):
                continue
            cid = child.custom_id

            if cid == "poker_fold":
                child.disabled = not (human_turn and legal.can_fold)
            elif cid == "poker_check_call":
                child.disabled = not human_turn
                if legal.can_call and legal.call_amount > 0:
                    child.label = _poker_i18n(
                        "action.call",
                        "Call {amount}",
                        lang=lang,
                        amount=legal.call_amount,
                    )
                    child.style = discord.ButtonStyle.primary
                else:
                    child.label = "Check"
                    child.style = discord.ButtonStyle.secondary
            elif cid == "poker_raise":
                child.disabled = not (human_turn and legal.can_raise)
                child.label = "Raise…"
            elif cid == "poker_allin":
                child.disabled = not (human_turn and legal.can_all_in)
                child.label = _poker_i18n(
                    "action.allin",
                    "All-in ({amount})",
                    lang=lang,
                    amount=legal.all_in_amount,
                )
            elif cid == "poker_timebank":
                child.disabled = not (human_turn and legal.can_time_bank)
            elif cid == "poker_ready":
                # Only visible/active during showdown / between hands
                child.disabled = self.game.terminal_state == "open"

    @discord.ui.button(label="Fold", style=discord.ButtonStyle.danger, custom_id="poker_fold", row=0)
    async def fold_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_player_action(interaction, "fold")

    @discord.ui.button(label="Check", style=discord.ButtonStyle.secondary, custom_id="poker_check_call", row=0)
    async def check_call_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        actor = self.game.current_actor
        if actor and actor.id == interaction.user.id:
            legal = self.game.get_legal_actions(actor)
            action = "call" if legal.can_call and legal.call_amount > 0 else "check"
            await self._handle_player_action(interaction, action)
        else:
            await _safe_respond(interaction, "It is not your turn.", ephemeral=True)

    @discord.ui.button(label="Raise…", style=discord.ButtonStyle.primary, custom_id="poker_raise", row=0)
    async def raise_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        actor = self.game.current_actor
        if not actor or actor.id != interaction.user.id:
            return await _safe_respond(interaction, "It is not your turn.", ephemeral=True)

        legal = self.game.get_legal_actions(actor)
        if not legal.can_raise:
            return await _safe_respond(interaction, "Raising is not legal right now.", ephemeral=True)

        # Open ephemeral raise presets view
        view = RaisePresetsView(self.game, actor, legal)
        await _safe_respond(
            interaction,
            content=f"Choose a raise amount (Min: **{legal.min_raise_to}**, Max: **{legal.max_raise_to}**):",
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="All-in", style=discord.ButtonStyle.danger, custom_id="poker_allin", row=0)
    async def allin_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_player_action(interaction, "allin")

    @discord.ui.button(label="My Hand", style=discord.ButtonStyle.secondary, custom_id="poker_myhand", row=1)
    async def myhand_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self.game.get_player(interaction.user.id)
        if not player:
            return await _safe_respond(interaction, "You are not seated at this table.", ephemeral=True)

        lang = _user_lang(interaction.user)
        embed = TableEmbedBuilder.build_my_hand_embed(
            player,
            self.game.community_cards,
            self.game.pot_total,
            self.game.highest_bet,
            lang=lang,
        )
        await _safe_respond(interaction, embed=embed, ephemeral=True)

    @discord.ui.button(label="⏱️ +30s Bank", style=discord.ButtonStyle.secondary, custom_id="poker_timebank", row=1)
    async def timebank_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        actor = self.game.current_actor
        if not actor or actor.id != interaction.user.id:
            return await _safe_respond(interaction, "It is not your turn.", ephemeral=True)

        success = await self.game.use_time_bank(actor)
        if success:
            await _safe_respond(interaction, "⏱️ 30 seconds added to your time bank!", ephemeral=True)
        else:
            await _safe_respond(interaction, "Time bank not available.", ephemeral=True)

    @discord.ui.button(label="⏩ Ready / Next Hand", style=discord.ButtonStyle.success, custom_id="poker_ready", row=1)
    async def ready_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self.game.get_player(interaction.user.id)
        if not player or player.is_bot:
            return await _safe_respond(interaction, "Only seated players can mark ready.", ephemeral=True)

        all_ready = self.game.pacing.mark_player_ready(player.id)
        if all_ready:
            await _safe_respond(interaction, "All players ready! Starting next hand…", ephemeral=True)
        else:
            await _safe_respond(interaction, "Marked as ready for next hand.", ephemeral=True)

    async def _handle_player_action(
        self, interaction: discord.Interaction, action: str, amount: int = 0
    ):
        await _safe_defer(interaction, ephemeral=False)
        player = self.game.get_player(interaction.user.id)
        if not player or player.is_bot:
            return await _safe_respond(interaction, "You are not an active player in this game.", ephemeral=True)

        applied = await self.game.handle_player_action(player, action, amount=amount)
        if not applied:
            await _safe_respond(interaction, "Action could not be completed.", ephemeral=True)


class RaisePresetsView(discord.ui.View):
    """Ephemeral selection view for raise preset amounts."""

    def __init__(
        self,
        game: "PokerGameInstance",
        player: Player,
        legal: LegalActionSummary,
    ):
        super().__init__(timeout=60)
        self.game = game
        self.player = player
        self.legal = legal

        # Add preset buttons
        for preset in legal.presets[:4]:
            btn = discord.ui.Button(
                label=f"{preset.label} ({preset.target_total})",
                style=discord.ButtonStyle.primary if not preset.is_all_in else discord.ButtonStyle.danger,
            )

            def make_callback(val: int):
                async def _cb(inter: discord.Interaction):
                    if inter.user.id != self.player.id:
                        return await _safe_respond(
                            inter, "This raise menu belongs to another player.", ephemeral=True
                        )
                    await _safe_defer(inter, ephemeral=True)
                    applied = await self.game.raise_bet(self.player, val)
                    if not applied:
                        await _safe_respond(
                            inter,
                            "That raise is no longer legal; the table state changed.",
                            ephemeral=True,
                        )
                return _cb

            btn.callback = make_callback(preset.target_total)
            self.add_item(btn)

    @discord.ui.button(label="Custom Amount…", style=discord.ButtonStyle.secondary, row=1)
    async def custom_amount_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = RaiseCustomModal(self.game, self.player, self.legal)
        await interaction.response.send_modal(modal)


class RaiseCustomModal(discord.ui.Modal, title="Custom Raise Total"):
    """Modal for custom raise-to amount."""

    def __init__(
        self,
        game: "PokerGameInstance",
        player: Player,
        legal: LegalActionSummary,
    ):
        super().__init__()
        self.game = game
        self.player = player
        self.legal = legal

        self.amount_input = discord.ui.TextInput(
            label=f"Total Bet (Min: {legal.min_raise_to}, Max: {legal.max_raise_to})",
            placeholder=f"Enter total bet, e.g. {legal.min_raise_to}",
            min_length=1,
            max_length=10,
            required=True,
        )
        self.add_item(self.amount_input)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.player.id:
            return await _safe_respond(
                interaction, "This raise form belongs to another player.", ephemeral=True
            )
        await _safe_defer(interaction, ephemeral=True)
        try:
            target_total = int(str(self.amount_input.value).strip())
        except (ValueError, TypeError):
            return await _safe_respond(interaction, "Please enter a valid whole number.", ephemeral=True)

        if target_total < self.legal.min_raise_to and target_total != self.legal.max_raise_to:
            return await _safe_respond(
                interaction,
                f"Raise total must be at least {self.legal.min_raise_to} (or all-in at {self.legal.max_raise_to}).",
                ephemeral=True,
            )

        applied = await self.game.raise_bet(self.player, target_total)
        if not applied:
            await _safe_respond(
                interaction,
                "That raise is no longer legal; the table state changed.",
                ephemeral=True,
            )
