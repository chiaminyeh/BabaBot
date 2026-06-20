import datetime
import discord
import random
from discord import DMChannel
from discord import app_commands
from discord.ext import commands, tasks
import asyncio

WORDLE_GUESS = 6
WORDLE_LETTER = 5

# Load word lists
my_file = open("wordle.txt", "r")
data = my_file.read()
WORDLE_LIST = data.split("\n")
my_file.close()

my_file = open('valid-wordle-words.txt', "r")
data = my_file.read()
VALID_LIST = data.split("\n")
my_file.close()

wordle_emoji = {
    0: ':white_large_square:',
    1: ':yellow_square:',
    2: ':green_square:',
    3: ':brown_square:',  # custom emoji placeholder
    4: ':black_large_square:',
}


class WordleGame:
    def __init__(self, guild_id, channel_id, user_id):
        self.guild = guild_id
        self.channel = channel_id
        self.user_id = user_id

        self.secret_word = random.choice(WORDLE_LIST)
        self.guesses_left = WORDLE_GUESS
        self.guesses = []  # each element: [ ['c','r','a','n','e'], [2,1,0,0,0] ]
        self.ended = False

    def get_board(self) -> str:
        """
        Creates the visual board using emojis.
        Also sets self.ended when the player wins or loses.
        """
        final_message = ''

        # Already made guesses
        for guess_letters, guess_result in self.guesses:
            line = f"{wordle_emoji[3] * (WORDLE_LETTER + 2)}\n"
            line += wordle_emoji[3]
            for j in range(WORDLE_LETTER):
                line += f":regional_indicator_{guess_letters[j]}:"
            line += wordle_emoji[3]
            line += "\n"

            line += wordle_emoji[3]
            for j in range(WORDLE_LETTER):
                line += f"{wordle_emoji[guess_result[j]]}"
            line += wordle_emoji[3]
            line += "\n"

            final_message += line

        # Top border under guesses
        final_message += f"{wordle_emoji[3] * (WORDLE_LETTER + 2)}\n"

        # Empty rows for remaining guesses
        for _ in range(self.guesses_left):
            line = ""
            line += wordle_emoji[3]
            line += f"{wordle_emoji[4] * WORDLE_LETTER}"
            line += wordle_emoji[3]
            line += "\n"
            final_message += line

        # Bottom border
        final_message += f"{wordle_emoji[3] * (WORDLE_LETTER + 2)}\n"

        # Win / lose / status message
        if self.check_win():
            final_message += f"You win! The word was **{self.secret_word}**!"
            self.ended = True
        elif self.check_loss():
            final_message += f"You ran out of guesses. The word was **{self.secret_word}**!"
            self.ended = True
        else:
            final_message += f"Guesses left: {self.guesses_left}"

        return final_message

    async def send_board(self, bot: commands.Bot, user: discord.abc.User):
        """
        Sends the current board to the original channel of the game.
        Works for both slash & prefix commands.
        """
        channel = bot.get_channel(self.channel)

        # Fallback if channel not found (e.g. removed): DM the user
        if channel is None:
            if isinstance(user, (discord.Member, discord.User)):
                channel = await user.create_dm()
            else:
                return  # no valid place to send

        display_name = getattr(user, "display_name", getattr(user, "name", "Player"))

        embed = discord.Embed(
            title=f"{display_name}'s Wordle Game",
            description=self.get_board(),
            color=0x80008E
        )
        await channel.send(embed=embed)

    def guess_word(self, word: str):
        """
        Process a guess, update guesses list and guesses_left.
        """
        pos = WORDLE_GUESS - self.guesses_left

        secret_word = list(self.secret_word)
        guess_word = list(word)
        correct = [0] * WORDLE_LETTER

        # First pass: correct letters in correct place
        for i in range(WORDLE_LETTER):
            if secret_word[i] == guess_word[i]:
                correct[i] = 2
                secret_word[i] = None

        # Second pass: correct letters in wrong places
        for i in range(WORDLE_LETTER):
            if correct[i] != 0:
                continue
            if guess_word[i] in secret_word:
                correct[i] = 1
                secret_word[secret_word.index(guess_word[i])] = None

        self.guesses.append([guess_word, correct])
        self.guesses_left -= 1

    def check_win(self) -> bool:
        if not self.guesses:
            return False
        # last guess result
        for result in self.guesses[-1][1]:
            if result != 2:
                return False
        return True

    def check_loss(self) -> bool:
        return self.guesses_left <= 0

    def check_word(self, word: str) -> bool:
        if len(word) != WORDLE_LETTER:
            return False
        if not word.isalpha():
            return False
        return True


########################################

class wordle_cog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.wordle_games: list[WordleGame] = []
        self.bank = bot.baba.bank
        self.money_name = bot.baba.money_name

    # ---------- internal helpers ----------

    def _find_game(self, user_id: int):
        """
        Return (index, game) for the given user_id, or (None, None) if not found.
        """
        for idx, game in enumerate(self.wordle_games):
            if game.user_id == user_id:
                return idx, game
        return None, None

    # ---------- slash commands ----------

    @app_commands.command(
        name="wordle_start",
        description="Start a wordle game"
    )
    async def wordle_start(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        idx, existing_game = self._find_game(user_id)

        # If the user already has a game
        if existing_game:
            if existing_game.ended:
                # Previous game is over -> remove and start a new one
                self.wordle_games.pop(idx)
                game = WordleGame(interaction.guild_id, interaction.channel_id, user_id)
                self.wordle_games.append(game)
                await interaction.response.send_message(
                    "Successfully ended your previous Wordle game. Starting a new one!",
                    ephemeral=False
                )
                await game.send_board(self.bot, interaction.user)
                return
            else:
                # Game still active
                await interaction.response.send_message(
                    "You already started a game! Use /wordle_print to see your current board.",
                    ephemeral=False
                )
                return

        # No existing game -> start a fresh one
        game = WordleGame(interaction.guild_id, interaction.channel_id, user_id)
        self.wordle_games.append(game)
        await interaction.response.send_message("Starting Wordle...", ephemeral=False)
        await game.send_board(self.bot, interaction.user)

    @app_commands.command(
        name="wordle_print",
        description="Show the current Wordle game"
    )
    async def wordle_print(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        _, game = self._find_game(user_id)

        if game:
            await game.send_board(self.bot, interaction.user)
        else:
            await interaction.response.send_message(
                "You need to start a game first!",
                ephemeral=False
            )

    @app_commands.command(
        name="wordle_end",
        description="End the current Wordle game"
    )
    async def wordle_end(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        idx, game = self._find_game(user_id)

        if game is None:
            await interaction.response.send_message(
                "You don't have any active Wordle games!",
                ephemeral=False
            )
            return

        # Reveal based on whether game ended naturally
        if game.ended:
            await interaction.response.send_message(
                "Successfully ended the Wordle game.",
                ephemeral=False
            )
        else:
            await interaction.response.send_message(
                f"That's a shame you couldn't solve the Wordle, "
                f"the word was ||**{game.secret_word}**||!",
                ephemeral=False
            )

        self.wordle_games.pop(idx)

    @app_commands.command(
        name="wordle_guess",
        description="Guess a word in Wordle"
    )
    async def wordle_guess(self, interaction: discord.Interaction, word: str):
        user_id = interaction.user.id
        idx, game = self._find_game(user_id)

        word = word.lower()

        if game is None:
            await interaction.response.send_message(
                "You need to start a game first!",
                ephemeral=False
            )
            return

        if not game.check_word(word):
            await interaction.response.send_message(
                "Incorrect input! Make sure the word is 5 letters long and all alphabetic.",
                ephemeral=False
            )
            return

        if word not in VALID_LIST:
            await interaction.response.send_message(
                f"{word} is not a valid word...",
                ephemeral=False
            )
            return

        if game.ended:
            await interaction.response.send_message(
                "The game has already ended!",
                ephemeral=False
            )
            return

        game.guess_word(word)

        # Acknowledge the interaction so it's not "stuck"
        await interaction.response.send_message("Checking your guess...", ephemeral=True)
        await game.send_board(self.bot, interaction.user)

        # If they just won, award money
        if game.check_win():
            attempts = len(game.guesses)  # 1 through 6
            prize_table = {1: 10000, 2: 1000, 3: 500, 4: 200, 5: 100, 6: 10}
            prize = prize_table.get(attempts, 0)

            # credit the bank
            bal, claimed = self.bank[interaction.user.id]
            self.bank[interaction.user.id] = (bal + prize, claimed)
            self.bot.baba.refresh_bank_file()

            await interaction.followup.send(
                f"🎉 You solved it in {attempts} guess(es) and won "
                f"**{prize} {self.money_name}**!",
                ephemeral=False
            )

    # ---------- prefix command ----------

    @commands.command(
        name="guess",
        description="Guess a word in Wordle (prefix command)"
    )
    async def guess_prefix(self, ctx: commands.Context, word: str):
        word = word.lower()
        user_id = ctx.author.id

        _, game = self._find_game(user_id)

        if game is None:
            await ctx.send("You need to start a game first! Use `/wordle_start`.")
            return

        if not game.check_word(word):
            await ctx.send("Invalid word! Make sure it's 5 letters long and all alphabetic.")
            return

        if word not in VALID_LIST:
            await ctx.send(f"{word} is not a valid word...")
            return

        if game.ended:
            await ctx.send("The game has already ended!")
            return

        game.guess_word(word)
        await game.send_board(self.bot, ctx.author)

        # If they just won, award money
        if game.check_win():
            attempts = len(game.guesses)  # 1 through 6
            prize_table = {1: 10000, 2: 1000, 3: 500, 4: 200, 5: 100, 6: 10}
            prize = prize_table.get(attempts, 0)

            # credit the bank  (FIXED: ctx.author.id, no ephemeral kwarg)
            bal, claimed = self.bank[ctx.author.id]
            self.bank[ctx.author.id] = (bal + prize, claimed)
            self.bot.baba.refresh_bank_file()

            await ctx.send(
                f"🎉 You solved it in {attempts} guess(es) and won "
                f"**{prize} {self.money_name}**!"
            )


async def setup(bot):
    await bot.add_cog(wordle_cog(bot))
    print('wordle loaded!')
