import discord
from discord.ext import commands

class bomb_cog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.rooms = {
            "room1": {
                "run" : False,
                "defense_channel_ids": [934251451637710878],
                "crackers_channel_ids": [934251273904062465],
                "defense_inputs": [],
                "crackers_inputs": []
            },
            "room2": {
                "run" : False,
                "defense_channel_ids": [934270038016413716],
                "crackers_channel_ids": [934268850688630796],
                "defense_inputs": [],
                "crackers_inputs": []
            },
            "room3": {
                "run" : False,
                "defense_channel_ids": [1017411263908818985],
                "crackers_channel_ids": [1017411228919930961],
                "defense_inputs": [],
                "crackers_inputs": []
            },
            "room4": {
                "run" : False,
                "defense_channel_ids": [1312615093480984586],
                "crackers_channel_ids": [1312615071305695252],
                "defense_inputs": [],
                "crackers_inputs": []
            }
        }

    def is_valid_input(self, inputs):
        return len(inputs) == len(set(inputs))

    def check_inputs(self, room):
        if len(room["defense_inputs"]) >= 2 and len(room["crackers_inputs"]) >= 2:
            all_inputs = room["defense_inputs"] + room["crackers_inputs"]
            return self.is_valid_input(all_inputs)
        return None
    
    def extract_numbers_from_message(self,message_content):
        return [int(num) for num in message_content.split() if num.isdigit()]
    
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author == self.bot.user:
            return

        if message.content.startswith("reset"):
            room = None
            for room_name, room_data in self.rooms.items():
                if message.channel.id in room_data["defense_channel_ids"] or message.channel.id in room_data["crackers_channel_ids"]:
                    room = room_data
                    break

            if room is not None:
                room["defense_inputs"] = []
                room["crackers_inputs"] = []
                await message.channel.send("該房間已重置。")
                room["run"] = False
                return


        room = None
        for room_name, room_data in self.rooms.items():
            if message.channel.id in room_data["defense_channel_ids"] or message.channel.id in room_data["crackers_channel_ids"]:
                room = room_data
                break

        if room is None or room["run"]:
            return

        numbers = self.extract_numbers_from_message(message.content)

        if any(num < 1 or num > 16 for num in numbers):
            await message.channel.send("請輸入1~16之間的數字。")
            return
        
        if message.channel.id in room["defense_channel_ids"]:
                room["defense_inputs"].extend(numbers)
                await message.add_reaction("✅")
        elif message.channel.id in room["crackers_channel_ids"]:
                room["crackers_inputs"].extend(numbers)
                await message.add_reaction("✅")

        if self.check_inputs(room) is not None:
            if self.check_inputs(room):
                for channel_id in room["defense_channel_ids"] + room["crackers_channel_ids"]:
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        await channel.send("沒有重複。遊戲可以開始了！")
                        room["run"] = True
                # room["defense_inputs"] = []
                # room["crackers_inputs"] = []
            else:
                for channel_id in room["defense_channel_ids"] + room["crackers_channel_ids"]:
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        await channel.send("有重複。請重新投入。")
                room["defense_inputs"] = []
                room["crackers_inputs"] = []


async def setup(bot):
    await bot.add_cog(bomb_cog(bot))
    print('Bomb loaded!')
    

    