import discord
from discord import app_commands
from discord.ext import commands,tasks
from discord import DMChannel
from datetime import datetime, timedelta
import os, asyncio
from dotenv import load_dotenv
import random
load_dotenv()

intents = discord.Intents.all()
intents.voice_states  = True 
bot = commands.Bot(command_prefix=["baba ","BABA ","Baba "], intents=intents)
# bot.remove_command('help')
# bot = commands.Bot(command_prefix=["baba ","BABA ","Baba "], intents=discord.Intents.all())

# @bot.command()
# async def test(ctx):
#     await ctx.send("Hi I am baba")

# bot.run(os.getenv('DISCORD_TOKEN'))
    

class Baba():
    def __init__(self):
        self.bank = {}
        self.load_bank()
        self.hunger = 100
        self.boredom = 50
        self.energy = 100
        self.money_name = "bababucks"

    def load_bank(self):
        try:
            with open("bank.txt", 'r') as file:
                lines = file.readlines()
                for line in lines:
                    id, money, claimed = line.strip().split(" ")
                    # Convert claimed string to proper boolean
                    claimed_bool = (claimed.lower() == 'true')
                    self.bank[int(id)] = (int(money), claimed_bool)
        except FileNotFoundError:
            print("bank file not found")

    def refresh_bank_file(self):
        with open("bank.txt", 'w') as file:
            for id, (money, claimed) in self.bank.items():
                file.write(f"{id} {money} {claimed}\n")
    


   


baba = Baba()

bot.baba = baba

@tasks.loop(hours=24)
async def reset_daily():
    for user_id, (money, claimed) in baba.bank.items():
        baba.bank[user_id] = (money, False)
    baba.refresh_bank_file()
    print("Daily reset completed")
    
    # Notify the bot owner
    owner = bot.get_user(295288056276189185)
    try:
        await owner.send("Daily reset completed.")
    except discord.errors.Forbidden:
        print("Couldn't send a DM to the bot owner.")

@reset_daily.before_loop
async def before_reset_daily():
    now = datetime.now()
    next_run = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    await discord.utils.sleep_until(next_run)

@bot.command(name='daily')
async def daily(ctx):
    try:
        user_id = ctx.author.id
        if user_id in baba.bank:
            money, claimed = baba.bank[user_id]
            print(money, claimed)
            if claimed == True:
                await ctx.send("You claimed your daily already!")
                return
            baba.bank[user_id] = (money + 100, True)
        else:
            baba.bank[user_id] = (100, True)
        
        baba.refresh_bank_file()
        await ctx.send(f"You claimed your daily! Total: {baba.bank[user_id][0]}")
    except:
        print("something in daily went wrong")

@bot.command(name='give_money', aliases=['give', 'transfer'])
async def give_money(ctx, target: discord.Member, amount: int):
    """Give money to another user"""
    if amount <= 0:
        await ctx.send("Please enter a positive amount to give.")
        return

    sender_id = ctx.author.id
    target_id = target.id

    if sender_id not in baba.bank:
        await ctx.send("You don't have any money to give.")
        return

    sender_money, sender_claimed = baba.bank[sender_id]

    if sender_money < amount:
        await ctx.send("You don't have enough money to give that amount.")
        return

    # Deduct the amount from the sender
    baba.bank[sender_id] = (sender_money - amount, sender_claimed)

    # Add the amount to the target user
    if target_id in baba.bank:
        target_money, target_claimed = baba.bank[target_id]
        baba.bank[target_id] = (target_money + amount, target_claimed)
    else:
        baba.bank[target_id] = (amount, False)

    baba.refresh_bank_file()

    await ctx.send(f"{ctx.author.name} has given {amount} {baba.money_name} to {target.name}.")

@bot.command(name='balance', aliases=['amount', 'money', 'bababucks', 'coins'])
async def balance(ctx, user: discord.Member = None):
    try:
        if user is None:
            user = ctx.author
        user_id = user.id
        if user_id in baba.bank:
            await ctx.send(f"{user.name} has {baba.bank[user_id][0]} {baba.money_name}")
        else:
            baba.bank[user_id] = (0, False)
            baba.refresh_bank_file()
            await ctx.send(f"{user.name} doesn't have any {baba.money_name}")
    except:
        print("something in balance went wrong")



    
@tasks.loop(minutes = 3)
async def metabolism():
    try:
        print('looping metabolism')
        baba.hunger-=1
        baba.energy-=1

        if(baba.energy <=0):
            await DMChannel.send(295288056276189185, f"`I go to sleep`")
            await bot.close()

    except:
        print("there's something wrong with baba's metabolism")
    

@bot.event
async def on_message(ctx):
    try:
        if ctx.author == bot.user:
            return
        await bot.process_commands(ctx)
    except Exception as e:
        # print(e)
        pass


@bot.command()
async def test(ctx):
    await ctx.send("Hi I am baba")

@bot.tree.command(name="roll", description="Roll a dice with a specified number of sides and times, with optional repeats.")
@app_commands.describe(
    num="Number of sides on the dice (default 6)",
    times="Number of times to roll (default 1)",
    repeat="Allow repeated numbers? (default True)"
)
async def roll(
    interaction: discord.Interaction,
    num: int = 6,
    times: int = 1,
    repeat: bool = True
):
    """Roll a dice with a specified number of sides (default is 6), times (default is 1), and repeat option."""
    try:
        if num < 1 or times < 1:
            return

        if not repeat and times > num:
            return

        if repeat:
            rolls = [random.randint(1, num) for _ in range(times)]
        else:
            rolls = random.sample(range(1, num + 1), times)

        await interaction.response.send_message(f"{', '.join(map(str, rolls))}")
    except Exception:
        await interaction.response.send_message("Please enter valid numbers.", ephemeral=True)

@bot.command(name="shutdown",aliases=["sleep", "go sleep"])
async def shutdown(ctx):
    admin = 295288056276189185
    if ctx.author.id == admin:
        await ctx.send("zzz...")
        await bot.close()
    else:
        await ctx.reply("no")


#dm someone with username
@bot.command(name="dm")
async def dm(ctx, person, *, message):
    try:
        admin = 295288056276189185
        if ctx.author.id == admin:
            roster = {"chiamin" : "295288056276189185", "aura": "598379467064344576"}
            user = await bot.fetch_user(roster[person])
            await DMChannel.send(user, f"`{str(message)}`")

    except Exception as e:
        print(e)

@bot.command(name="say")
async def say(ctx, c, *msg):
    try:
        print(msg)
        roster = {"general" : 1216934133771534427, "ball" : 1234182535840272455, "monek":1234631351215591494}
        channel = bot.get_channel(roster[c])
        await channel.send(msg)
        await ctx.add_reaction("👌")

    except Exception as e:
        print(e)

@bot.command(name="should" ,aliases=["can","do","may","are","did","is","could","will","am","were","does","have","has","was"])
async def answers(ctx):
    answers = [
    ('yes', 100), ('no', 100), ('maybe', 20), ('probably', 20), ('sure', 10),
    ('nah', 10), ('idk', 10), ('wot', 40), ('ask grubby', 10), ('sounds good', 10),
    ('why not', 10), ("don't", 10), ('just do it', 10), ('ask uri', 10), ('bet', 40),
    ('YES', 50), ("don't talk to me", 3), ('we got bro yapping before gta 6', 2),
    ('chess battle advanced', 10), ('depends on you', 10), ('💀', 10), ('ask yourself', 10),
    ('get some help', 10), ('ask hemre', 20), ('ask marc', 10), ('wdym', 10),
    ('baba has stopped working', 3), ('stop asking me', 3), ('pay $0.99 to unlock the message', 10),
    ('yeah sure', 10), ('touch grass', 10), ('I sleep', 10), ('bro wot', 10)
]

    pick_weighted_random(answers)
    await ctx.send(pick_weighted_random(answers))
    # r = random.randint(0,len(answers)-1)
    # await ctx.send(answers[r])

def pick_weighted_random(choices):
    picked = None
    weight_sum = 0
    for i in choices:
        weight_sum += i[1]
        if(random.random() * weight_sum < i[1]):
            picked = i[0]

    return picked

@bot.command(name='info')
async def info(ctx, command, *data):
    try:
        if not command:
            await ctx.send("Please provide a command.")
            return
            
        if command == 'add':
            if len(data) < 2:
                await ctx.send("Please provide both key and value.")
                return
            key = data[0]
            value = ' '.join(data[1:])
            with open("info.txt", "a", encoding="utf-8") as f:
                f.write(f"{key}: {value}\n")
            await ctx.send("Information added successfully!")

        elif command == 'remove':
            if len(data) < 1:
                await ctx.send("Please provide the key to remove.")
                return
            key = ' '.join(data)
            lines = []
            with open("info.txt", "r", encoding="utf-8") as f:
                lines = f.readlines()
            with open("info.txt", "w", encoding="utf-8") as f:
                for line in lines:
                    if not line.startswith(key + ':'):
                        f.write(line)
            await ctx.send("Information removed successfully!")

        elif command == 'all':
            with open("info.txt", "r", encoding="utf-8") as f:
                keys = [line.split(':', 1)[0] for line in f.readlines()]
                if keys:
                    await ctx.send("All keys:\n" + '\n'.join(keys))
                else:
                    await ctx.send("No keys found.")

        else:
            # Search in file for key and send corresponding value in discord
            key = command + ' '.join(data)
            message = ''
            with open("info.txt", "r", encoding="utf-8") as f:
                lines = f.readlines()
                found = False
                for line in lines:
                    key2 = line.split(':', 1)[0]
                    if key2 == key:
                        message += ' '.join(line.split()[1:]) + '\n'
                        found = True
                if found:
                    await ctx.send(message)
                else:
                    await ctx.send("Information not found.")

    except FileNotFoundError:
        await ctx.send("File 'info.txt' not found.")
    except Exception as e:
        await ctx.send(f'An error occurred: {e}')
        print(f'Error in info: {e}')


# this is how ctx looks like:
#['__annotations__', '__class__', '__class_getitem__', '__delattr__', '__dict__', '__dir__', '__doc__', '__eq__', '__format__', '__ge__', '__getattribute__', 
# '__getstate__', '__gt__', '__hash__', '__init__', '__init_subclass__', '__le__', '__lt__', '__module__', '__ne__', '__new__', '__orig_bases__', '__parameters__', 
# '__reduce__', '__reduce_ex__', '__repr__', '__setattr__', '__sizeof__', '__slots__', '__str__', '__subclasshook__', '__weakref__', '_get_channel', '_is_protocol', 
# '_state', 'args', 'author', 'bot', 'bot_permissions', 'channel', 'clean_prefix', 'cog', 'command', 'command_failed', 'current_argument', 'current_parameter', 'defer', 
# 'fetch_message', 'filesize_limit', 'from_interaction', 'guild', 'history', 'interaction', 'invoke', 'invoked_parents', 'invoked_subcommand', 'invoked_with', 'kwargs', 
# 'me', 'message', 'permissions', 'pins', 'prefix', 'reinvoke', 'reply', 'send', 'send_help', 'subcommand_passed', 'typing', 'valid', 'view', 'voice_client']



@bot.event
async def on_ready():
    print(f"{bot.user} is now running!")
    # metabolism.start()
    reset_daily.start()
    await bot.load_extension('music_cog')
    await bot.load_extension('schedule_cog')
    await bot.load_extension('blackjack_cog')
    await bot.load_extension("poker_cog")
    await bot.load_extension('bomb_cog')
    await bot.load_extension('response_cog')
    await bot.load_extension("chess_cog")
    await bot.load_extension("trpg_cog")
    await bot.load_extension("wordle_cog")
    await bot.load_extension("lottery_cog")

    try:
        synced = await bot.tree.sync()
        print(f"synced {len(synced)} command(s)!")
    except Exception as e:
        print(e)

    # await bot.add_cog(help_cog(bot))



# @bot.tree.command(name="challenge")
# @app_commands.describe(user = "Who do you want to challenge?")
# async def challenge(interaction: discord.Interaction,user: str):
#     await interaction.response.send_000("Hi! This is a slash command", ephemeral=False)


@bot.tree.command(name="ping", description="test bot latency")
async def ping(interaction: discord.Interaction):
    bot_latency = round(bot.latency * 1000)
    await interaction.response.send_message(f"Pong! {bot_latency} ms.")

@bot.command()
async def reload(ctx):
    admin = 295288056276189185
    if ctx.author.id == admin:
        baba.load_bank()
        # Reloads the file, thus updating the Cog class.
        await bot.reload_extension("music_cog")
        await bot.reload_extension("response_cog")
        await bot.reload_extension("chess_cog")
        # await bot.reload_extension("time_cog")
        await bot.reload_extension("wordle_cog")
        await bot.reload_extension("bomb_cog")
        await bot.reload_extension("schedule_cog")
        await bot.reload_extension("poker_cog")
        await bot.reload_extension("blackjack_cog")
        await bot.reload_extension("lottery_cog")
        await bot.reload_extension("trpg_cog")

        # await bot.reload_extension("listen_cog")
        await ctx.send("reloaded")
    else:
        await ctx.reply("You do not have permission to use this command.")



bot.run(os.getenv('DISCORD_TOKEN'))