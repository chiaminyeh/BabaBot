WORDLE_GUESS = 6
WORDLE_LETTER = 5


my_file = open("wordle.txt", "r") 
data = my_file.read() 
WORDLE_LIST = data.split("\n") 

my_file.close() 

wordle_emoji = {
    0: ':white_large_square:',
    1: ':yellow_square:',
    2: ':green_square:',
    3: '<:Wall:989856522140024842>', # replace this because it is a custom emoji
    4: ':black_large_square:'
}


class WordleGame():
    def __init__(self, guild_id, channel_id, user_id):
        self.guild = guild_id
        self.channel = channel_id
        self.user_id = user_id
        self.channel_object = client.get_channel(self.channel)
        
        self.secret_word = random.choice(WORDLE_LIST)
        self.guesses_left = WORDLE_GUESS
        self.guesses = [
            # [['c','r','a','n','e'], [2,1,0,0,0]],
        ]   # added SKONG line 1766

        self.ended = False

    async def send_board(self, interaction):
        embedVar = discord.Embed(title=f"{interaction.user.global_name}'s Wordle Game", description=f"{self.get_board()}", color=0x80008e)
        await interaction.response.send_message(embed=embedVar, ephemeral=False)

    def get_board(self): # Draws the user interface 
        final_message = ''
        for i in range(len(self.guesses)):
            line = f"{wordle_emoji[3] * (WORDLE_LETTER + 2)}\n"
            line += wordle_emoji[3]
            for j in range(WORDLE_LETTER):
                line += f":regional_indicator_{self.guesses[i][0][j]}:"
            line += wordle_emoji[3]
            line += "\n"
            line += wordle_emoji[3]
            for j in range(WORDLE_LETTER):
                line += f"{wordle_emoji[self.guesses[i][1][j]]}"
            line += wordle_emoji[3]
            line += "\n"
            final_message += line
        
        final_message += f"{wordle_emoji[3] * (WORDLE_LETTER + 2)}\n"

        for i in range(self.guesses_left):
            line = ""
            line += wordle_emoji[3]
            line += f"{wordle_emoji[4] * WORDLE_LETTER}"
            line += wordle_emoji[3]
            line += "\n"

            final_message += line

        final_message += f"{wordle_emoji[3] * (WORDLE_LETTER + 2)}\n"

        if(self.check_win()):
            final_message += f"You win! The word was **{self.secret_word}**!"
            self.ended = True
        elif(self.check_loss()):
            final_message += f"You ran out of guesses. The word was **{self.secret_word}**!"
            self.ended = True
        else:
            final_message += f"Guesses left: {self.guesses_left}"

        return final_message
        



    def guess_word(self, word):
        self.guesses.append(list())
        pos = WORDLE_GUESS - self.guesses_left

        secret_word = list(self.secret_word)
        guess_word = list(word)
        correct = list()

        for i in range(WORDLE_LETTER): # create a list of correct letters
            correct.append(0)

        for i in range(WORDLE_LETTER): # check correct letters
            if(secret_word[i] == guess_word[i]):
                correct[i] = 2
                secret_word[i] = None

        for i in range(WORDLE_LETTER):
            if(correct[i] != 0): continue
            
            if(guess_word[i] in secret_word):
                correct[i] = 1
                secret_word[secret_word.index(guess_word[i])] = None
        
        self.guesses[pos].append(guess_word)
        self.guesses[pos].append(correct)
        self.guesses_left += -1

    def check_win(self):
        if(len(self.guesses) == 0): return False
        for i in self.guesses[len(self.guesses) - 1][1]:
            if(i != 2):
                return False
        return True

    
    def check_loss(self):
        if(self.guesses_left <= 0):
            return True
        return False

    def check_word(self, word):
        if(len(word) != WORDLE_LETTER): return False
        if(not word.isalpha()): return False

        return True
    
########################################

class MyClient(discord.Client):

    def __init__(self, intents):
        super().__init__(intents=intents)  # Call the parent class's __init__ method
        self.wordle_games = []

########################################


intents = discord.Intents.default()
intents.message_content = True

client = MyClient(intents=intents)
tree = app_commands.CommandTree(client)

@tree.command(
    name="wordle_start",
    description="Start a wordle game",
    # guild=discord.Object(id=769403776020774922)
)
async def wordle_start(interaction: discord.Interaction):
    game = WordleGame(interaction.guild_id, interaction.channel_id, interaction.user.id)

    game_found = False
    for i in client.wordle_games:
        if (i.user_id == interaction.user.id):
            game_found = True
            await interaction.response.send_message(f"You already started a game, you dumb nuts", ephemeral=False)
    if(not game_found):
        client.wordle_games.append(game)
        await game.send_board(interaction)

@tree.command(
    name="wordle_print",
    description="Show the current wordle game",
    # guild=discord.Object(id=769403776020774922)
)
async def wordle_print(interaction: discord.Interaction):
    game_found = False
    game = None
    for i in range(len(client.wordle_games)):
        if (client.wordle_games[i].user_id == interaction.user.id):
            game_found = True
            game = client.wordle_games[i]
            
    if(game_found):
        await game.send_board(interaction)
    else:
        await interaction.response.send_message(f"You need to start a game first!", ephemeral=False)

@tree.command(
    name="wordle_end",
    description="End the current wordle game",
    # guild=discord.Object(id=769403776020774922)
)
async def wordle_end(interaction: discord.Interaction):
    game_found = False
    game_pos = None
    for i in range(len(client.wordle_games)):
        if (client.wordle_games[i].user_id == interaction.user.id):
            game_found = True
            game_pos = i
            
    if(game_found):
        if(client.wordle_games[game_pos].ended == True):
            await interaction.response.send_message(f"Successfully ended the wordle game", ephemeral=False)
        else:
            await interaction.response.send_message(f"That's a shame you couldn't solve the wordle, the word was ||**{client.wordle_games[game_pos].secret_word}**||!", ephemeral=False)
        client.wordle_games.pop(game_pos)
        
    else:
        await interaction.response.send_message(f"You dont have any wordle games active!", ephemeral=False)

@tree.command(
    name="wordle_guess",
    description="Guess a word in wordle",
    # guild=discord.Object(id=769403776020774922)
)
async def guess(interaction: discord.Interaction, word: str):
    game_found = False
    game = None
    for i in range(len(client.wordle_games)):
        if (client.wordle_games[i].user_id == interaction.user.id):
            game_found = True
            game = client.wordle_games[i]
    if(not game_found):
        await interaction.response.send_message(f"You need to start a game first!", ephemeral=False)
        return
    if(not game.check_word(word)):
        await interaction.response.send_message(f"Incorrect input! Make sure the word is 5 letters long and all alphabetic", ephemeral=False)
        return
    if(game.ended):
        await interaction.response.send_message(f"The game ended!", ephemeral=False)
        return
    word = word.lower()
    game.guess_word(word)
    await game.send_board(interaction)