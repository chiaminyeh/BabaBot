import discord
from discord.ext import commands, tasks
import random
import os
from datetime import datetime, timedelta

class LotteryCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.ticket_cost = 100
        self.prize_pool = 10000 + (50 * self.ticket_cost)  # Base prize + 50 tickets worth
        self.lottery_file = "lottery_tickets.txt"
        self.announce_channel_id = [1306668111105228870,1444048712677724416,1269862833491935234]
        self.lottery_loop.start()

    def cog_unload(self):
        self.lottery_loop.cancel()

    def get_tickets(self):
        tickets = []
        if not os.path.exists(self.lottery_file):
            return tickets
        
        with open(self.lottery_file, "r") as f:
            lines = f.readlines()
            for line in lines:
                # Format: user_id numbers
                parts = line.strip().split(" ", 1)
                if len(parts) == 2:
                    user_id = int(parts[0])
                    numbers = list(map(int, parts[1].split(",")))
                    tickets.append({"user_id": user_id, "numbers": numbers})
        return tickets

    def save_ticket(self, user_id, numbers):
        with open(self.lottery_file, "a") as f:
            nums_str = ",".join(map(str, numbers))
            f.write(f"{user_id} {nums_str}\n")

    def clear_tickets(self):
        open(self.lottery_file, "w").close()

    @commands.command(name="buy_ticket", aliases=["buy",])
    async def buy_ticket(self, ctx, *numbers: int):
        """Buy a lottery ticket for 100 bababucks. Pick 6 numbers between 1-20. Usage: baba buy 1 2 3 4 5 6"""
        user_id = ctx.author.id
        baba = self.bot.baba

        # 1. Check input validity
        if len(numbers) != 6:
            await ctx.send("You must choose exactly 6 numbers.")
            return
        
        if any(n < 1 or n > 20 for n in numbers):
            await ctx.send("Numbers must be between 1 and 20.")
            return
        
        if len(set(numbers)) != 6:
            await ctx.send("Numbers must be unique.")
            return

        sorted_numbers = sorted(list(numbers))

        # 2. Check funds
        if user_id not in baba.bank:
            baba.bank[user_id] = (0, False)
        
        current_money, claimed = baba.bank[user_id]
        
        if current_money < self.ticket_cost:
            await ctx.send(f"You don't have enough bababucks! A ticket costs {self.ticket_cost}.")
            return

        # 3. Check for duplicate tickets (optional rule, but good for preventing spam of same numbers)
        current_tickets = self.get_tickets()
        for ticket in current_tickets:
            if ticket["user_id"] == user_id and ticket["numbers"] == sorted_numbers:
                await ctx.send("You already bought a ticket with these exact numbers!")
                return

        # 4. Process transaction
        baba.bank[user_id] = (current_money - self.ticket_cost, claimed)
        baba.refresh_bank_file()
        
        self.save_ticket(user_id, sorted_numbers)
        
        await ctx.send(f"Ticket purchased! Numbers: {sorted_numbers}. Good luck!")

    #Buyrandom command
    @commands.command(name="buyrandom", aliases=["br",])
    async def buy_random_ticket(self, ctx, count: int = 1):
        """Buy a lottery ticket with random numbers for 100 bababucks. If only 1 number added after random, buys that many random tickets."""
        user_id = ctx.author.id
        baba = self.bot.baba
        tickets_bought = []
        for _ in range(count):
            # 1. Check funds
            if user_id not in baba.bank:
                baba.bank[user_id] = (0, False)
            
            current_money, claimed = baba.bank[user_id]
            
            if current_money < self.ticket_cost:
                await ctx.send(f"You don't have enough bababucks! A ticket costs {self.ticket_cost}.")
                return

            # 2. Generate random unique numbers
            random_numbers = sorted(random.sample(range(1, 21), 6))

            # 3. Process transaction
            baba.bank[user_id] = (current_money - self.ticket_cost, claimed)
            baba.refresh_bank_file()
            
            self.save_ticket(user_id, random_numbers)
            tickets_bought.append(random_numbers)
        await ctx.send(f"Tickets purchased! Good luck! You can check your tickets with `baba ticket`.\n")


    @tasks.loop(hours=24)
    async def lottery_loop(self):
        
        tickets = self.get_tickets()

        # Generate winning numbers
        winning_numbers = sorted(random.sample(range(1, 21), 6))
        
        # Categorize winners by match count
        # 6 matches = Jackpot
        # 5 matches = 2nd Prize
        # 4 matches = 3rd Prize
        # 3 matches = 4th Prize
        winners = {6: [], 5: [], 4: [], 3: []}
        
        # Check for winners
        for ticket in tickets:
            # Calculate intersection of ticket numbers and winning numbers
            match_count = len(set(ticket["numbers"]) & set(winning_numbers))
            if match_count in winners:
                winners[match_count].append(ticket["user_id"])

        baba = self.bot.baba
        
        # Define fixed prizes for lower tiers
        prizes = {
            5: 5000,  # 2nd Prize
            4: 1000,  # 3rd Prize
            3: 200    # 4th Prize
        }

        # Calculate Jackpot share
        jackpot_share = 0
        if winners[6]:
            jackpot_share = self.prize_pool // len(winners[6])

        # Process payouts
        # 1. Jackpot (Match 6)
        for uid in winners[6]:
            if uid in baba.bank:
                curr, claimed = baba.bank[uid]
                baba.bank[uid] = (curr + jackpot_share, claimed)
            else:
                baba.bank[uid] = (jackpot_share, False)
        
        # 2. Lower tiers (Match 5, 4, 3)
        for match_count in [5, 4, 3]:
            amount = prizes[match_count]
            for uid in winners[match_count]:
                if uid in baba.bank:
                    curr, claimed = baba.bank[uid]
                    baba.bank[uid] = (curr + amount, claimed)
                else:
                    baba.bank[uid] = (amount, False)

        baba.refresh_bank_file()

        # Announce in all configured channels
        for channel_id in self.announce_channel_id:
            channel = self.bot.get_channel(channel_id)
            if not channel:
                print(f"Lottery channel {channel_id} not found.")
                continue
            
            if not tickets:
                # await channel.send("Daily Lottery: No tickets were bought today. The prize remains unclaimed.")
                continue
            
            # Build announcement message
            msg = [f"🎰 **DAILY LOTTERY RESULTS** 🎰"]
            msg.append(f"Winning Numbers: **{winning_numbers}**")

            # Jackpot Announcement
            if winners[6]:
                mentions = ", ".join([f"<@{uid}>" for uid in winners[6]])
                msg.append(f"🏆 **JACKPOT (6/6)**: {mentions} won {jackpot_share} bababucks!")
            else:
                msg.append(f"🏆 **JACKPOT**: No winners. Pool remains {self.prize_pool}.")

            # 2nd Prize Announcement
            if winners[5]:
                mentions = ", ".join([f"<@{uid}>" for uid in winners[5]])
                msg.append(f"🥈 **2nd Prize (5/6)**: {mentions} won {prizes[5]} bababucks!")

            # 3rd Prize Announcement
            if winners[4]:
                mentions = ", ".join([f"<@{uid}>" for uid in winners[4]])
                msg.append(f"🥉 **3rd Prize (4/6)**: {mentions} won {prizes[4]} bababucks!")

            # 4th Prize Announcement
            if winners[3]:
                uids = winners[3]
                if len(uids) > 10:
                    msg.append(f"🎉 **4th Prize (3/6)**: {len(uids)} winners won {prizes[3]} bababucks!")
                else:
                    mentions = ", ".join([f"<@{uid}>" for uid in uids])
                    msg.append(f"🎉 **4th Prize (3/6)**: {mentions} won {prizes[3]} bababucks!")

            if not any(winners.values()):
                pass
                print("No winners today.")
                msg.append("No winning tickets today. Better luck next time!")

            await channel.send("\n".join(msg))

        # Clear tickets for the next day
        self.clear_tickets()

    @lottery_loop.before_loop
    async def before_lottery_loop(self):
        await self.bot.wait_until_ready()
        # Calculate time until next run (e.g., run at 8 PM everyday, or just 24h from start)
        # For simplicity, this aligns with the daily reset logic or runs 24h from bot start
        # You can adjust specific time here if needed.
        now = datetime.now()
        # Example: Run at 8:00 PM everyday
        next_run = now.replace(hour=20, minute=0, second=0, microsecond=0)
        if next_run < now:
            next_run += timedelta(days=1)
        
        await discord.utils.sleep_until(next_run)

    @commands.command(name="lottery")
    async def lottery_info(self, ctx):
        """Explains how the lottery works."""
        embed = discord.Embed(title="🎰 Baba Lottery Rules 🎰", color=0xFFD700)
        embed.add_field(name="How to Play", value=f"Use `baba buy <n1> <n2> <n3> <n4> <n5> <n6>` to buy a ticket.\nExample: `baba buy 1 2 3 4 5 6`", inline=False)
        embed.add_field(name="Cost", value=f"{self.ticket_cost} bababucks per ticket.", inline=False)
        embed.add_field(name="Jackpot (6/6)", value=f"{self.prize_pool} bababucks", inline=True)
        embed.add_field(name="2nd Prize (5/6)", value="5,000 bababucks", inline=True)
        embed.add_field(name="3rd Prize (4/6)", value="1,000 bababucks", inline=True)
        embed.add_field(name="4th Prize (3/6)", value="200 bababucks", inline=True)
        embed.add_field(name="Rules", value="• Pick 6 unique numbers between 1-20.\n• Drawings happen daily at 12:00 PM.\n• You cannot buy the exact same ticket twice.", inline=False)
        embed.add_field(name="Checking Tickets", value="You can check your tickets with `baba ticket`.", inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="ticket", aliases=["tickets"])
    async def my_tickets(self, ctx):
        """Shows your current tickets and the prize pool."""
        user_id = ctx.author.id
        all_tickets = self.get_tickets()
        user_tickets = [t["numbers"] for t in all_tickets if t["user_id"] == user_id]

        embed = discord.Embed(title=f"🎟️ {ctx.author.display_name}'s Tickets", color=0x00FF00)
        embed.add_field(name="Current Prize Pool", value=f"💰 {self.prize_pool} bababucks", inline=False)

        if user_tickets:
            #too much ticket will spam, limit to 10 tickets shown
            if len(user_tickets) > 10:
                embed.add_field(name="Your Numbers", value=f"You have {len(user_tickets)} tickets. First 10:", inline=False)
                tickets_str = "\n".join([str(nums) for nums in user_tickets[:10]])
                embed.add_field(name="Tickets", value=f"```{tickets_str}```", inline=False)
            else:
                tickets_str = "\n".join([str(nums) for nums in user_tickets])
                embed.add_field(name="Your Numbers", value=f"```{tickets_str}```", inline=False)
        else:
            embed.add_field(name="Your Numbers", value="You haven't bought any tickets for the next drawing yet.", inline=False)
        
        await ctx.send(embed=embed)





async def setup(bot):
    await bot.add_cog(LotteryCog(bot))