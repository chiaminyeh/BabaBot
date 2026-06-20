import datetime
import discord
import random
import time
import math
from discord import DMChannel
from discord import app_commands
from discord.ext import commands, tasks
import asyncio

utc = datetime.timezone.utc

# If no tzinfo is given then UTC is assumed.
# time = datetime.time(hour=8, minute=30, tzinfo=utc)

# If no tzinfo is given then UTC is assumed.
times = [
    datetime.time(hour=8,tzinfo=utc),
    # datetime.time(hour=8,minute=0,tzinfo=utc),
    # datetime.time(hour=9, minute=40,tzinfo=utc),
    # datetime.time(hour=9, minute=0, second=30,tzinfo=utc)
]


class time_cog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # self.ball.start()
        self.channels=[]
        # self.ball_manager=BallManager()


    @commands.command(name='ping', help="pong")
    async def ping(self,ctx):
        try:
            await ctx.reply(f'pong! (latency {round(self.bot.latency*1000)}ms)')
        except Exception as e:
            print('error in ping')
            print(e)

    @commands.command(name='timer', help="Format: baba timer N(s/m/h) message(optional)")
    async def timer(self,ctx, timeInput, *msg):
        try:
            try:
                time = int(timeInput)
            except:
                convertTimeList = {'s':1, 'm':60, 'h':3600, 'd':86400, 'S':1, 'M':60, 'H':3600, 'D':86400}
                time = int(timeInput[:-1]) * convertTimeList[timeInput[-1]]
            if time > 86400:
                await ctx.send("I can\'t do timers over a day long")
                return
            if time <= 0:
                await ctx.send("Timers don\'t go into negatives :/")
                return
            if time >= 3600:
                message = await ctx.send(f"Timer: {time//3600} hours {time%3600//60} minutes {time%60} seconds")
            elif time >= 60:
                message = await ctx.send(f"Timer: {time//60} minutes {time%60} seconds")
            elif time < 60:
                message = await ctx.send(f"Timer: {time} seconds")


            while True:
                try:
                    await asyncio.sleep(1)
                    time -= 1
                    if time >= 3600 and time%5 == 0:
                        await message.edit(content=f"Timer: {time//3600} hours {time %3600//60} minutes {time%60} seconds")
                    elif time >= 60 and time%5 == 0:
                        await message.edit(content=f"Timer: {time//60} minutes {time%60} seconds")
                    elif time < 60 and time%5 == 0:
                        await message.edit(content=f"Timer: {time} seconds")
                    if time <= 0:
                        await message.edit(content="Ended!")
                        x = " ".join(msg)
                        if x != "":
                            await ctx.send(f"{ctx.author.mention} " + f" {x}")
                        else: 
                            await ctx.send(f"{ctx.author.mention} Your countdown Has ended!")
                        break
                    # print(time)
                except:
                    break
        except:
            await ctx.send(f"Alright, first you gotta let me know how I\'m gonna time **{timeInput}**....")


    # @tasks.loop(time=times)
    # async def my_task(self):
    #     print("My task is running!")

async def setup(bot):
    await bot.add_cog(time_cog(bot))
    print('time loaded!')

