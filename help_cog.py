import discord
from discord.ext import commands


PRESENCE_TEXT = "baba help"
HELP_COLOR = discord.Color.blurple()

CATEGORY_META = {
    "general": ("🏠 General", "Everyday Baba commands and public slash commands."),
    "adventure": ("⚔️ Adventure", "Open and configure Baba's persistent TRPG adventure."),
    "music": ("🎵 Music", "Play YouTube music and control the shared voice queue."),
    "games": ("🎮 Games", "Casino, Wordle, and lottery games."),
    "utility": ("🛠️ Utility", "Schedules and reminders."),
}

CATEGORY_FIELDS = {
    "general": [
        (
            "Prefix commands",
            "`baba help` — Open this help menu\n"
            "`baba daily` — Claim your daily Bababucks\n"
            "`baba balance [@user]` — Check a balance\n"
            "`baba give @user <amount>` — Transfer Bababucks",
        ),
        (
            "Slash commands",
            "`/roll` — Roll dice with configurable sides and repeats\n"
            "`/ping` — Check Baba's Discord latency",
        ),
    ],
    "adventure": [
        (
            "Baba Adventure",
            "`/trpg` — Open your persistent adventure panel\n"
            "`/language` — Switch the adventure language",
        ),
        (
            "Inside the panel",
            "Create characters, explore, fight, manage equipment and skills, complete quests, and visit regional facilities using buttons.",
        ),
    ],
    "music": [
        (
            "Playback",
            "`baba play <song>` — Search YouTube and play or queue a song\n"
            "`baba pause` — Pause playback\n"
            "`baba resume` — Resume playback\n"
            "`baba skip` — Skip the current song\n"
            "`baba stop` — Stop and disconnect Baba",
        ),
        (
            "Queue",
            "`baba queue` — Show the queue\n"
            "`baba current` — Show the current song\n"
            "`baba last` — Play the previous song\n"
            "`baba remove [current]` — Remove a queued/current song\n"
            "`baba clear` — Clear the queue and history",
        ),
    ],
    "games": [
        (
            "Casino",
            "`/blackjack <bet>` — Start solo Blackjack\n"
            "`/blackjack_multiplayer` — Open a Blackjack lobby\n"
            "`/start_poker` — Start a Poker lobby\n"
            "`/poker_rules` — Show Poker rules",
        ),
        (
            "Wordle",
            "`/wordle_start` — Start a game\n"
            "`/wordle_guess` — Submit a guess\n"
            "`/wordle_print` — Show your board\n"
            "`/wordle_end` — End your game",
        ),
        (
            "Lottery",
            "`baba lottery` — Show lottery rules\n"
            "`baba buy 1 2 3 4 5 6` — Buy a ticket\n"
            "`baba buyrandom [count]` — Buy random tickets\n"
            "`baba ticket` — Show your tickets",
        ),
    ],
    "utility": [
        (
            "Schedules",
            "`/record_schedule` — Add a schedule entry\n"
            "`/list_schedules` — List upcoming entries\n"
            "`/remove_schedule <index>` — Remove one of your entries",
        ),
    ],
}


def build_help_embed(category: str = "home") -> discord.Embed:
    if category == "home":
        embed = discord.Embed(
            title="Baba Help",
            description=(
                "Choose a category below. Baba accepts friendly prefix casing, "
                "but the canonical syntax shown here is `baba ...`."
            ),
            color=HELP_COLOR,
        )
        for key, (title, summary) in CATEGORY_META.items():
            embed.add_field(name=title, value=summary, inline=False)
        embed.set_footer(text="Use the buttons below • Slash commands begin with /")
        return embed

    if category not in CATEGORY_META:
        category = "home"
        return build_help_embed(category)

    title, summary = CATEGORY_META[category]
    embed = discord.Embed(title=title, description=summary, color=HELP_COLOR)
    for field_name, value in CATEGORY_FIELDS[category]:
        embed.add_field(name=field_name, value=value, inline=False)
    embed.set_footer(text="Canonical prefix: baba • Use Home to return")
    return embed


class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        buttons = [
            ("Home", "home", "🏠", discord.ButtonStyle.secondary, 1),
            ("General", "general", "📖", discord.ButtonStyle.primary, 0),
            ("Adventure", "adventure", "⚔️", discord.ButtonStyle.primary, 0),
            ("Music", "music", "🎵", discord.ButtonStyle.primary, 0),
            ("Games", "games", "🎮", discord.ButtonStyle.primary, 0),
            ("Utility", "utility", "🛠️", discord.ButtonStyle.primary, 0),
        ]
        for label, category, emoji, style, row in buttons:
            button = discord.ui.Button(
                label=label,
                emoji=emoji,
                style=style,
                custom_id=f"baba_help:{category}",
                row=row,
            )
            button.callback = self._callback_for(category)
            self.add_item(button)

    def _callback_for(self, category: str):
        async def callback(interaction: discord.Interaction):
            await interaction.response.edit_message(
                embed=build_help_embed(category),
                view=self,
            )

        return callback


class help_cog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        await self.bot.change_presence(activity=discord.Game(PRESENCE_TEXT))

    @commands.command(name="help", help="Open Baba's interactive help menu")
    async def help(self, ctx):
        await ctx.send(embed=build_help_embed("home"), view=HelpView())

    @commands.command(name="prefix", help="Change bot prefix", hidden=True)
    async def prefix(self, ctx, *args):
        self.bot.command_prefix = " ".join(args)
        await ctx.send(f"prefix set to **'{self.bot.command_prefix}'**")
        await self.bot.change_presence(activity=discord.Game(PRESENCE_TEXT))


async def setup(bot):
    bot.add_view(HelpView())
    await bot.add_cog(help_cog(bot))
