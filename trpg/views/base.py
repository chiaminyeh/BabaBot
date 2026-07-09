import discord
from trpg.i18n import t

class TRPGBaseView(discord.ui.View):
    """Base class for all TRPG views, handling timeout, security validation, and dynamic controls."""
    def __init__(self, session):
        super().__init__(timeout=600)  # 10 minutes timeout
        self.session = session
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.session.user_id:
            lang = getattr(self.session.player, "language", "zh")
            await interaction.response.send_message(
                t(lang, "menu.not_your_panel", "這不是你的冒險面板，請自己輸入 `/trpg` 開一盤！"),
                ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        self.session.cog.save_players(player=self.session.player)
        if self.message:
            try:
                for child in self.children:
                    if hasattr(child, "disabled"):
                        child.disabled = True

                lang = getattr(self.session.player, "language", "zh")
                embed = self.session.generate_embed()
                timeout_notice = t(
                    lang,
                    "menu.timeout_notice",
                    "⌛ 此冒險面板已因超時（10分鐘未操作）而關閉並自動存檔。\n請重新輸入 `/trpg` 來繼續冒險！"
                )
                embed.description = f"```\n{timeout_notice}\n```"
                await self.message.edit(embed=embed, view=self)
            except Exception:
                pass

    def add_action_button(self, label, style, custom_id, row=None, emoji=None, disabled=False):
        for child in self.children:
            if getattr(child, "custom_id", None) == custom_id:
                return

        btn = discord.ui.Button(label=label, style=style, custom_id=custom_id, row=row, emoji=emoji, disabled=disabled)

        async def callback(interaction: discord.Interaction):
            await self.session.global_callback(interaction, custom_id)

        if not disabled:
            btn.callback = callback
        self.add_item(btn)

    def add_action_select(self, placeholder, options, row=None, custom_id=None):
        opts = []
        for label, value, desc, emoji in options[:25]:
            opts.append(discord.SelectOption(
                label=str(label)[:100],
                value=str(value)[:100],
                description=(str(desc)[:100] or None) if desc else None,
                emoji=emoji or None,
            ))
        if not opts:
            return
        select = discord.ui.Select(
            placeholder=str(placeholder)[:150],
            options=opts,
            row=row,
            min_values=1,
            max_values=1,
            custom_id=custom_id or f"sel_{len(self.children)}"
        )

        async def callback(interaction: discord.Interaction):
            await self.session.global_callback(interaction, select.values[0])

        select.callback = callback
        self.add_item(select)
