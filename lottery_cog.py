import discord
from discord.ext import commands, tasks
import random
import os
import threading
import tempfile
from datetime import datetime, timedelta

class LotteryCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.ticket_cost = 100
        self.prize_pool = 10000 + (50 * self.ticket_cost)  # Base prize + 50 tickets worth
        self.lottery_file = "lottery_tickets.txt"
        self._ticket_lock = threading.RLock()
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
        self.save_tickets(user_id, [numbers])

    def save_tickets(self, user_id, tickets):
        """Append a ticket batch with one atomic file replacement."""
        with self._ticket_lock:
            try:
                with open(self.lottery_file, "r", encoding="utf-8") as source:
                    existing = source.read()
            except FileNotFoundError:
                existing = ""

            directory = os.path.dirname(os.path.abspath(self.lottery_file)) or "."
            fd, tmp_path = tempfile.mkstemp(
                prefix="lottery.", suffix=".tmp", dir=directory, text=True
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as target:
                    target.write(existing)
                    for numbers in tickets:
                        nums_str = ",".join(map(str, numbers))
                        target.write(f"{user_id} {nums_str}\n")
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(tmp_path, self.lottery_file)
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

    def clear_tickets(self):
        """Replace the ticket file atomically with an empty generation."""
        with self._ticket_lock:
            directory = os.path.dirname(os.path.abspath(self.lottery_file)) or "."
            fd, tmp_path = tempfile.mkstemp(
                prefix="lottery.", suffix=".tmp", dir=directory, text=True
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as target:
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(tmp_path, self.lottery_file)
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

    def _ticket_sales_paused(self):
        state = self.bot.baba.economy_state
        return bool(
            state.get("pending_lottery_draw_id")
            or state.get("lottery_cleanup_draw_id")
        )

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

        response = None
        with self._ticket_lock:
            current_tickets = self.get_tickets()
            if self._ticket_sales_paused():
                response = (
                    "Lottery ticket sales are temporarily paused while today's "
                    "drawing is being settled."
                )
            elif any(
                ticket["user_id"] == user_id
                and ticket["numbers"] == sorted_numbers
                for ticket in current_tickets
            ):
                response = "You already bought a ticket with these exact numbers!"
            elif not baba.try_debit_money(user_id, self.ticket_cost):
                response = (
                    "You don't have enough bababucks! "
                    f"A ticket costs {self.ticket_cost}."
                )
            else:
                try:
                    self.save_ticket(user_id, sorted_numbers)
                except Exception:
                    try:
                        baba.credit_money(user_id, self.ticket_cost)
                    except Exception:
                        response = (
                            "Ticket purchase failed and the automatic refund could not "
                            "be saved. Please contact an administrator."
                        )
                        await ctx.send(response)
                        raise
                    response = (
                        "Ticket purchase failed; your bababucks were refunded."
                    )
                else:
                    response = (
                        f"Ticket purchased! Numbers: {sorted_numbers}. Good luck!"
                    )

        await ctx.send(response)

    #Buyrandom command
    @commands.command(name="buyrandom", aliases=["br",])
    async def buy_random_ticket(self, ctx, count: int = 1):
        """Buy a lottery ticket with random numbers for 100 bababucks. If only 1 number added after random, buys that many random tickets."""
        user_id = ctx.author.id
        baba = self.bot.baba
        
        if count < 1:
            await ctx.send("Please specify a positive number of tickets.")
            return

        total_cost = self.ticket_cost * count

        tickets_bought = [
            sorted(random.sample(range(1, 21), 6)) for _ in range(count)
        ]
        response = None
        refund_error = None
        with self._ticket_lock:
            if self._ticket_sales_paused():
                response = (
                    "Lottery ticket sales are temporarily paused while today's "
                    "drawing is being settled."
                )
            elif not baba.try_debit_money(user_id, total_cost):
                current_money = baba.get_money(user_id)
                response = (
                    "You don't have enough bababucks! "
                    f"Buying {count} ticket(s) costs {total_cost} "
                    f"{baba.money_name} (you have {current_money})."
                )
            else:
                try:
                    self.save_tickets(user_id, tickets_bought)
                except Exception:
                    try:
                        baba.credit_money(user_id, total_cost)
                    except Exception as exc:
                        refund_error = exc
                        response = (
                            "Ticket purchase failed and the automatic refund could not "
                            "be saved. Please contact an administrator."
                        )
                    else:
                        response = (
                            "Ticket purchase failed; your bababucks were refunded."
                        )
                else:
                    response = (
                        f"Successfully purchased {count} ticket(s) for {total_cost} "
                        f"{baba.money_name}! Good luck! You can check your tickets "
                        "with `baba ticket`.\n"
                    )

        await ctx.send(response)
        if refund_error is not None:
            raise refund_error


    @staticmethod
    def _validate_winning_numbers(winning_numbers):
        numbers = sorted(int(number) for number in winning_numbers)
        if len(numbers) != 6 or len(set(numbers)) != 6:
            raise ValueError("winning numbers must contain 6 unique values")
        if any(number < 1 or number > 20 for number in numbers):
            raise ValueError("winning numbers must be between 1 and 20")
        return numbers

    def _calculate_draw(self, tickets, winning_numbers):
        winners = {6: [], 5: [], 4: [], 3: []}
        for ticket in tickets:
            match_count = len(
                set(ticket["numbers"]) & set(winning_numbers)
            )
            if match_count in winners:
                winners[match_count].append(int(ticket["user_id"]))

        prizes = {5: 5000, 4: 1000, 3: 200}
        jackpot_share = (
            self.prize_pool // len(winners[6]) if winners[6] else 0
        )
        payouts = {}
        for uid in winners[6]:
            payouts[uid] = payouts.get(uid, 0) + jackpot_share
        for match_count in (5, 4, 3):
            for uid in winners[match_count]:
                payouts[uid] = payouts.get(uid, 0) + prizes[match_count]
        return winners, prizes, jackpot_share, payouts

    async def _announce_draw(
        self, tickets, winning_numbers, winners, prizes, jackpot_share
    ):
        if not tickets:
            return

        msg = [
            "🎰 **DAILY LOTTERY RESULTS** 🎰",
            f"Winning Numbers: **{winning_numbers}**",
        ]
        if winners[6]:
            mentions = ", ".join(f"<@{uid}>" for uid in winners[6])
            msg.append(
                f"🏆 **JACKPOT (6/6)**: {mentions} won "
                f"{jackpot_share} bababucks!"
            )
        else:
            msg.append(
                f"🏆 **JACKPOT**: No winners. Pool remains {self.prize_pool}."
            )

        labels = {5: "🥈 **2nd Prize (5/6)**", 4: "🥉 **3rd Prize (4/6)**"}
        for match_count in (5, 4):
            if winners[match_count]:
                mentions = ", ".join(
                    f"<@{uid}>" for uid in winners[match_count]
                )
                msg.append(
                    f"{labels[match_count]}: {mentions} won "
                    f"{prizes[match_count]} bababucks!"
                )
        if winners[3]:
            if len(winners[3]) > 10:
                msg.append(
                    f"🎉 **4th Prize (3/6)**: {len(winners[3])} winners won "
                    f"{prizes[3]} bababucks!"
                )
            else:
                mentions = ", ".join(f"<@{uid}>" for uid in winners[3])
                msg.append(
                    f"🎉 **4th Prize (3/6)**: {mentions} won "
                    f"{prizes[3]} bababucks!"
                )
        if not any(winners.values()):
            msg.append("No winning tickets today. Better luck next time!")

        announcement = "\n".join(msg)
        for channel_id in self.announce_channel_id:
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                print(f"Lottery channel {channel_id} not found.")
                continue
            await channel.send(announcement)

    async def run_draw(self, draw_id, winning_numbers):
        """Settle one durable draw without paying the same draw twice."""
        requested_draw_id = str(draw_id).strip()
        if not requested_draw_id:
            raise ValueError("draw_id must not be empty")
        requested_numbers = self._validate_winning_numbers(winning_numbers)
        baba = self.bot.baba

        with self._ticket_lock:
            state = baba.economy_state
            last_draw_id = str(state.get("last_lottery_draw_id", ""))
            cleanup_draw_id = str(
                state.get("lottery_cleanup_draw_id", "")
            )

            if last_draw_id == requested_draw_id:
                if cleanup_draw_id == requested_draw_id:
                    self.clear_tickets()
                    baba.apply_money_deltas_with_state(
                        {}, {"lottery_cleanup_draw_id": ""}
                    )
                return False

            pending_draw_id = str(
                state.get("pending_lottery_draw_id", "")
            )
            if pending_draw_id:
                active_draw_id = pending_draw_id
                active_numbers = self._validate_winning_numbers(
                    state.get("pending_lottery_numbers", [])
                )
            else:
                active_draw_id = requested_draw_id
                active_numbers = requested_numbers
                baba.apply_money_deltas_with_state(
                    {},
                    {
                        "pending_lottery_draw_id": active_draw_id,
                        "pending_lottery_numbers": active_numbers,
                    },
                )

            tickets = self.get_tickets()
            winners, prizes, jackpot_share, payouts = self._calculate_draw(
                tickets, active_numbers
            )
            baba.apply_money_deltas_with_state(
                payouts,
                {
                    "last_lottery_draw_id": active_draw_id,
                    "pending_lottery_draw_id": "",
                    "pending_lottery_numbers": [],
                    "lottery_cleanup_draw_id": active_draw_id,
                },
            )
            self.clear_tickets()
            baba.apply_money_deltas_with_state(
                {}, {"lottery_cleanup_draw_id": ""}
            )

        await self._announce_draw(
            tickets, active_numbers, winners, prizes, jackpot_share
        )
        return True

    @tasks.loop(hours=24)
    async def lottery_loop(self):
        draw_id = datetime.now().date().isoformat()
        winning_numbers = sorted(random.sample(range(1, 21), 6))
        await self.run_draw(draw_id, winning_numbers)

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