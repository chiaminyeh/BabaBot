import sys
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except AttributeError:
    pass

import discord
from discord import app_commands
from discord.ext import commands,tasks
from discord import DMChannel
from datetime import datetime, timedelta
import logging
import os, json, tempfile, threading
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
from bababucks import BababucksEscrow, BababucksLedger
from interaction_errors import is_transient_interaction_error
import random
load_dotenv()

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)


class IncidentIdFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, "incident_id"):
            record.incident_id = "-"
        return True


log_formatter = logging.Formatter(
    "%(asctime)s %(levelname)s %(name)s incident=%(incident_id)s module=%(module)s %(message)s"
)
log_handlers = [
    RotatingFileHandler(os.path.join(LOG_DIR, "bababot.log"), maxBytes=2_000_000, backupCount=5, encoding="utf-8"),
    logging.StreamHandler(sys.stdout),
]
for handler in log_handlers:
    handler.setFormatter(log_formatter)
    handler.addFilter(IncidentIdFilter())

logging.basicConfig(
    level=logging.INFO,
    handlers=log_handlers,
)
logging.getLogger("discord").setLevel(logging.INFO)
logger = logging.LoggerAdapter(logging.getLogger("bababot"), {"incident_id": "-"})

intents = discord.Intents.all()
intents.voice_states  = True 
bot = commands.Bot(command_prefix=["baba ","BABA ","Baba "], intents=intents)
bot.remove_command('help')


@bot.event
async def on_command_error(ctx, error):
    """Ignore normal bad commands; preserve actionable command failures in the monitor log."""
    if isinstance(error, commands.CommandNotFound):
        return
    logger.error(
        "COMMAND_FAILURE command=%s user_id=%s guild_id=%s channel_id=%s",
        getattr(ctx.command, "qualified_name", "unknown"),
        getattr(ctx.author, "id", "unknown"),
        getattr(ctx.guild, "id", None) or "dm",
        getattr(ctx.channel, "id", "unknown"),
        exc_info=(type(error), error, error.__traceback__),
    )


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        message = "You need Administrator permission to use this command."
        response = interaction.response
        if not getattr(response, "is_done", lambda: False)():
            await response.send_message(message, ephemeral=True)
        else:
            await interaction.followup.send(message, ephemeral=True)
        return
    if is_transient_interaction_error(error):
        logger.warning(
            "Transient Discord app-command interaction failure ignored command=%s error=%s",
            getattr(getattr(interaction, "command", None), "qualified_name", "unknown"),
            type(error).__name__,
        )
        return
    logger.error(
        "INTERACTION_FAILURE component=app_command stage=callback command=%s user_id=%s guild_id=%s channel_id=%s",
        getattr(getattr(interaction, "command", None), "qualified_name", "unknown"),
        getattr(getattr(interaction, "user", None), "id", "unknown"),
        getattr(interaction, "guild_id", None) or "dm",
        getattr(interaction, "channel_id", None) or "unknown",
        exc_info=(type(error), error, error.__traceback__),
    )
# bot = commands.Bot(command_prefix=["baba ","BABA ","Baba "], intents=discord.Intents.all())

# @bot.command()
# async def test(ctx):
#     await ctx.send("Hi I am baba")

# bot.run(os.getenv('DISCORD_TOKEN'))
    

BANK_FILE = "bank.json"
BANK_META_KEY = "__bababucks_meta__"
DAILY_FILE = "daily.json"
DAILY_REWARD = 100


class Baba():
    def __init__(self):
        self.bank_lock = threading.RLock()
        self.bank = {}          # {uid(int): (money(int), claimed_bool)}  ← 保持元組格式，相容其他 cog
        self.economy_state = {
            "last_lottery_draw_id": "",
            "pending_lottery_draw_id": "",
            "pending_lottery_numbers": [],
            "lottery_cleanup_draw_id": "",
            "casino_escrows": {},
        }
        self.daily_claims = {}  # {uid(int): "YYYY-MM-DD"}  ← 依日期判斷每日簽到，重載/重啟都安全
        self.load_bank()
        self.ledger = BababucksLedger(
            self.bank,
            self.bank_lock,
            self.refresh_bank_file,
        )
        self.load_daily_claims()
        self.hunger = 100
        self.boredom = 50
        self.energy = 100
        self.money_name = "bababucks"

    def load_bank(self):
        # 優先讀 JSON；沒有 JSON 時，從舊的 bank.txt 遷移一次
        if os.path.exists(BANK_FILE):
            try:
                with open(BANK_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                metadata = raw.get(BANK_META_KEY, {})
                if isinstance(metadata, dict):
                    self.economy_state["last_lottery_draw_id"] = str(
                        metadata.get("last_lottery_draw_id", "")
                    )
                    self.economy_state["pending_lottery_draw_id"] = str(
                        metadata.get("pending_lottery_draw_id", "")
                    )
                    pending_numbers = metadata.get(
                        "pending_lottery_numbers", []
                    )
                    if isinstance(pending_numbers, list):
                        self.economy_state["pending_lottery_numbers"] = [
                            int(number) for number in pending_numbers
                        ]
                    self.economy_state["lottery_cleanup_draw_id"] = str(
                        metadata.get("lottery_cleanup_draw_id", "")
                    )
                    raw_escrows = metadata.get("casino_escrows", {})
                    if isinstance(raw_escrows, dict):
                        escrows = {}
                        for escrow_id, record in raw_escrows.items():
                            if not isinstance(escrow_id, str):
                                continue
                            if not isinstance(record, dict):
                                continue
                            game_type = record.get("game_type")
                            escrow_state = record.get("state")
                            contributions = record.get("contributions", {})
                            if game_type not in {"poker", "blackjack"}:
                                continue
                            if escrow_state not in {
                                "open", "refunded", "settled"
                            }:
                                continue
                            if not isinstance(contributions, dict):
                                continue
                            normalized_contributions = {}
                            valid = True
                            for uid, amount in contributions.items():
                                try:
                                    normalized_uid = str(int(uid))
                                    normalized_amount = int(amount)
                                except (TypeError, ValueError):
                                    valid = False
                                    break
                                if normalized_amount <= 0:
                                    valid = False
                                    break
                                normalized_contributions[normalized_uid] = (
                                    normalized_amount
                                )
                            if valid:
                                escrows[escrow_id] = {
                                    "game_type": game_type,
                                    "state": escrow_state,
                                    "contributions": normalized_contributions,
                                }
                        self.economy_state["casino_escrows"] = escrows
                for uid, val in raw.items():
                    if uid == BANK_META_KEY:
                        continue
                    money = int(val[0]) if isinstance(val, (list, tuple)) else int(val)
                    claimed = bool(val[1]) if isinstance(val, (list, tuple)) and len(val) > 1 else False
                    self.bank[int(uid)] = (money, claimed)
                return
            except Exception as e:
                logger.exception("bank.json load failed")
        # 遷移舊格式 bank.txt
        try:
            with open("bank.txt", "r") as file:
                for line in file.readlines():
                    parts = line.strip().split(" ")
                    if len(parts) < 2:
                        continue
                    uid, money = parts[0], parts[1]
                    claimed = (parts[2].lower() == "true") if len(parts) > 2 else False
                    self.bank[int(uid)] = (int(money), claimed)
            self.refresh_bank_file()  # 存成 JSON
            logger.info("Migrated bank.txt -> bank.json")
        except FileNotFoundError:
            logger.warning("bank file not found")

    def refresh_bank_file(self):
        with self.bank_lock:
            payload = {str(uid): [money, claimed] for uid, (money, claimed) in self.bank.items()}
            payload[BANK_META_KEY] = dict(self.economy_state)
            directory = os.path.dirname(os.path.abspath(BANK_FILE)) or "."
            fd, tmp_path = tempfile.mkstemp(prefix="bank.", suffix=".tmp", dir=directory, text=True)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, BANK_FILE)
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

    def load_daily_claims(self):
        if os.path.exists(DAILY_FILE):
            try:
                with open(DAILY_FILE, "r", encoding="utf-8") as f:
                    self.daily_claims = {int(k): v for k, v in json.load(f).items()}
            except Exception as e:
                logger.exception("daily.json load failed")

    def save_daily_claims(self):
        with open(DAILY_FILE, "w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in self.daily_claims.items()}, f, ensure_ascii=False, indent=2)

    def get_money(self, uid: int) -> int:
        return self.ledger.get_balance(int(uid))

    def try_debit_money(self, uid: int, amount: int) -> bool:
        return self.ledger.try_debit(int(uid), int(amount))

    def credit_money(self, uid: int, amount: int) -> int:
        return self.ledger.credit(int(uid), int(amount))

    def apply_money_deltas(self, deltas):
        normalized = {int(uid): int(delta) for uid, delta in deltas.items()}
        return self.ledger.apply(normalized)

    def apply_money_deltas_with_state(self, deltas, state_updates):
        normalized = {int(uid): int(delta) for uid, delta in deltas.items()}
        updates = {str(key): value for key, value in state_updates.items()}
        return self.ledger.apply_with_state(
            normalized, self.economy_state, updates
        )

    def new_escrow(self, *, escrow_id=None, game_type=None):
        if escrow_id is None and game_type is None:
            return BababucksEscrow(self.ledger)
        return BababucksEscrow(
            self.ledger,
            state=self.economy_state,
            escrow_id=escrow_id,
            game_type=game_type,
        )

    def add_money(self, uid: int, amount: int):
        return self.ledger.adjust_clamped(int(uid), int(amount))

    def claim_daily(self, uid: int, reward: int = DAILY_REWARD):
        """依日期判斷每日簽到。回傳 (是否成功, 領取金額, 目前總額)。今天已領則成功=False。"""
        with self.bank_lock:
            uid = int(uid)
            today = datetime.now().strftime("%Y-%m-%d")
            if self.daily_claims.get(uid) == today:
                return False, 0, self.get_money(uid)
            self.daily_claims[uid] = today
            self.save_daily_claims()
            self.add_money(uid, reward)
            return True, reward, self.get_money(uid)
    


   


baba = Baba()

bot.baba = baba

EXTENSIONS = [
    'music_cog',
    'chess_cog',
    'schedule_cog',
    'blackjack_cog',
    'poker_cog',
    'response_cog',
    'trpg_cog',
    'wordle_cog',
    'lottery_cog',
    'help_cog',
    'monitor_cog'
]


async def setup_hook():
    loaded_extensions = []
    for ext in EXTENSIONS:
        try:
            await bot.load_extension(ext)
            loaded_extensions.append(ext)
        except Exception as e:
            logger.exception("Failed to load extension %s", ext)

    marker = f"Extensions loaded: {len(loaded_extensions)}/{len(EXTENSIONS)} ({', '.join(loaded_extensions)})"
    print(marker)
    logger.info(marker)

    if not reset_daily.is_running():
        reset_daily.start()

    try:
        synced = await bot.tree.sync()
        marker = f"Slash commands synced: {len(synced)}"
        print(marker)
        logger.info(marker)
    except Exception as e:
        logger.exception("Slash command sync failed")


bot.setup_hook = setup_hook

@tasks.loop(hours=24)
async def reset_daily():
    with baba.bank_lock:
        for user_id, (money, claimed) in list(baba.bank.items()):
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
        ok, reward, total = baba.claim_daily(ctx.author.id)
        if ok:
            await ctx.send(f"You claimed your daily +{reward}! Total: {total} {baba.money_name}")
        else:
            await ctx.send("You already claimed your daily today! Come back tomorrow.")
    except Exception as e:
        print(f"something in daily went wrong: {e}")

@bot.command(name='give_money', aliases=['give', 'transfer'])
async def give_money(ctx, target: discord.Member, amount: int):
    """Give money to another user"""
    if amount <= 0:
        await ctx.send("Please enter a positive amount to give.")
        return

    sender_id = ctx.author.id
    target_id = target.id

    transfer_error = None
    with baba.bank_lock:
        if sender_id not in baba.bank:
            transfer_error = "You don't have any money to give."
        else:
            sender_money, sender_claimed = baba.bank[sender_id]
            if sender_money < amount:
                transfer_error = "You don't have enough money to give that amount."
            else:
                baba.bank[sender_id] = (sender_money - amount, sender_claimed)
                if target_id in baba.bank:
                    target_money, target_claimed = baba.bank[target_id]
                    baba.bank[target_id] = (target_money + amount, target_claimed)
                else:
                    baba.bank[target_id] = (amount, False)
                baba.refresh_bank_file()

    if transfer_error:
        await ctx.send(transfer_error)
        return
    await ctx.send(f"{ctx.author.name} has given {amount} {baba.money_name} to {target.name}.")

@bot.command(name='balance', aliases=['amount', 'money', 'bababucks', 'coins'])
async def balance(ctx, user: discord.Member = None):
    try:
        if user is None:
            user = ctx.author
        user_id = user.id
        with baba.bank_lock:
            if user_id in baba.bank:
                balance_amount = baba.bank[user_id][0]
                missing = False
            else:
                baba.bank[user_id] = (0, False)
                baba.refresh_bank_file()
                balance_amount = 0
                missing = True
        if missing:
            await ctx.send(f"{user.name} doesn't have any {baba.money_name}")
        else:
            await ctx.send(f"{user.name} has {balance_amount} {baba.money_name}")
    except:
        print("something in balance went wrong")



    
# @tasks.loop(minutes = 3)
# async def metabolism():
#     try:
#         print('looping metabolism')
#         baba.hunger-=1
#         baba.energy-=1
# 
#         if(baba.energy <=0):
#             await DMChannel.send(295288056276189185, f"`I go to sleep`")
#             await bot.close()
# 
#     except:
#         print("there's something wrong with baba's metabolism")

    

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
    num: app_commands.Range[int, 1, 1000] = 6,
    times: app_commands.Range[int, 1, 100] = 1,
    repeat: bool = True
):
    """Roll a dice with a specified number of sides (default is 6), times (default is 1), and repeat option."""
    try:
        if num < 1 or times < 1:
            await interaction.response.send_message(
                "Dice sides and roll count must both be positive.", ephemeral=True
            )
            return

        if not repeat and times > num:
            await interaction.response.send_message(
                "Without repeats, the roll count cannot exceed the number of sides.",
                ephemeral=True,
            )
            return

        if repeat:
            rolls = [random.randint(1, num) for _ in range(times)]
        else:
            rolls = random.sample(range(1, num + 1), times)

        await interaction.response.send_message(f"{', '.join(map(str, rolls))}")
    except Exception:
        logger.exception("ROLL_FAILURE user_id=%s", getattr(interaction.user, "id", "unknown"))
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "I couldn't roll those dice. Please try again with valid values.",
                ephemeral=True,
            )

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
@commands.is_owner()
async def say(ctx, c, *msg):
    try:
        roster = {"general" : 1216934133771534427, "ball" : 1234182535840272455, "monek":1234631351215591494}
        if c not in roster:
            await ctx.reply("Unknown channel.")
            return
        channel = bot.get_channel(roster[c])
        await channel.send(" ".join(msg))
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
            if not await bot.is_owner(ctx.author):
                await ctx.reply("Owner only.")
                return
            if len(data) < 2:
                await ctx.send("Please provide both key and value.")
                return
            key = data[0]
            value = ' '.join(data[1:])
            with open("info.txt", "a", encoding="utf-8") as f:
                f.write(f"{key}: {value}\n")
            await ctx.send("Information added successfully!")

        elif command == 'remove':
            if not await bot.is_owner(ctx.author):
                await ctx.reply("Owner only.")
                return
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
        await bot.reload_extension("chess_cog")
        await bot.reload_extension("response_cog")
        # await bot.reload_extension("time_cog")
        await bot.reload_extension("wordle_cog")
        await bot.reload_extension("schedule_cog")
        await bot.reload_extension("poker_cog")
        await bot.reload_extension("blackjack_cog")
        await bot.reload_extension("lottery_cog")
        await bot.reload_extension("trpg_cog")
        await bot.reload_extension("help_cog")

        await ctx.send("reloaded")
    else:
        await ctx.reply("You do not have permission to use this command.")



bot.run(os.getenv('DISCORD_TOKEN'))
