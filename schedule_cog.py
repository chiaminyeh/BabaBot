import discord
from discord.ext import commands, tasks
from discord import app_commands
from discord.ui import View, Button
from datetime import datetime, timedelta
import os
import math
import asyncio

# How many minutes before a schedule to send the reminder DM
NOTIFY_BEFORE_MINUTES = 30

TIME_FORMAT = '%A, %B %d, %Y %I:%M %p'


def is_owner():
    """Slash-command-compatible owner check."""
    async def predicate(interaction: discord.Interaction):
        return interaction.user.id == 295288056276189185
    return app_commands.check(predicate)


# ══════════════════════════════════════════════════════════
#  ScheduleCog  (schedule management + timer + ping)
# ══════════════════════════════════════════════════════════
class ScheduleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Track notified events as (user_id, raw_line) so each schedule
        # gets its own reminder, not just one per user.
        self.notified: set[tuple] = set()
        self.schedule_checker.start()

    def cog_unload(self):
        self.schedule_checker.cancel()

    # ── File helpers ──────────────────────────────────────
    def _path(self, user_id) -> str:
        return f"schedules/{user_id}_schedule.txt"

    def load_schedules(self, user_id) -> list[str]:
        path = self._path(user_id)
        if os.path.exists(path):
            with open(path, "r") as f:
                return [l for l in f.readlines() if l.strip()]
        return []

    def save_schedules(self, user_id, schedules: list[str]):
        path = self._path(user_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.writelines(schedules)

    def parse_line(self, line: str) -> tuple[datetime, str]:
        """Parse 'Weekday, Month DD, YYYY HH:MM AM/PM: description'. Raises ValueError on failure."""
        time_str, description = line.split(": ", 1)
        return datetime.strptime(time_str.strip(), TIME_FORMAT), description.strip()

    # ── Task: runs every minute ───────────────────────────
    @tasks.loop(minutes=1)
    async def schedule_checker(self):
        now = datetime.now()
        notify_cutoff = now + timedelta(minutes=NOTIFY_BEFORE_MINUTES)

        if not os.path.exists("schedules"):
            return

        for filename in os.listdir("schedules"):
            if not filename.endswith("_schedule.txt"):
                continue

            try:
                user_id = int(filename.replace("_schedule.txt", ""))
            except ValueError:
                continue

            schedules = self.load_schedules(user_id)
            future = []
            changed = False

            for line in schedules:
                try:
                    schedule_time, description = self.parse_line(line)
                except ValueError:
                    continue

                # Drop past schedules and clean up their notification record
                if schedule_time < now:
                    self.notified.discard((user_id, line.strip()))
                    changed = True
                    continue

                future.append(line)

                # Notify if within the window and not already sent
                key = (user_id, line.strip())
                if schedule_time <= notify_cutoff and key not in self.notified:
                    try:
                        user = await self.bot.fetch_user(user_id)
                        mins_left = max(0, int((schedule_time - now).total_seconds() / 60))
                        time_label = "right now!" if mins_left == 0 else f"in {mins_left} minute{'s' if mins_left != 1 else ''}"
                        await user.send(
                            f"⏰ **Reminder:** {description}\n"
                            f"📅 {schedule_time.strftime(TIME_FORMAT)} ({time_label})"
                        )
                        self.notified.add(key)
                    except discord.Forbidden:
                        pass  # User has DMs closed
                    except Exception as e:
                        print(f"[ScheduleCog] Could not notify {user_id}: {e}")

            if changed:
                self.save_schedules(user_id, future)

    @schedule_checker.before_loop
    async def before_checker(self):
        await self.bot.wait_until_ready()

    # ── Slash: record schedule ────────────────────────────
    @app_commands.command(name="record_schedule", description="Add a new schedule entry")
    async def record_schedule(
        self,
        interaction: discord.Interaction,
        schedule: str,
        day: int,
        hour: int,
        minute: int = 0,
        month: int = 0,   # 0 = use current month
        year: int = 0,    # 0 = use current year
    ):
        now = datetime.now()
        month = month or now.month
        year = year or now.year

        try:
            schedule_time = datetime(year, month, day, hour, minute)
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid date/time. Double-check your day, hour, and month.", ephemeral=True
            )
            return

        if schedule_time <= now:
            await interaction.response.send_message(
                "❌ That time is already in the past!", ephemeral=True
            )
            return

        line = f"{schedule_time.strftime(TIME_FORMAT)}: {schedule}\n"
        user_id = interaction.user.id
        self.save_schedules(user_id, self.load_schedules(user_id) + [line])

        await interaction.response.send_message(
            f"✅ **Saved!**\n📅 {schedule_time.strftime(TIME_FORMAT)} — {schedule}\n"
            f"*(You'll get a DM reminder {NOTIFY_BEFORE_MINUTES} minutes before.)*"
        )

    # ── Slash: list schedules ─────────────────────────────
    @app_commands.command(name="list_schedules", description="List upcoming schedules")
    async def list_schedules(self, interaction: discord.Interaction, user: discord.User = None):
        user_id = user.id if user else interaction.user.id
        schedules = self.load_schedules(user_id)

        if not schedules:
            await interaction.response.send_message("No upcoming schedules found.", ephemeral=True)
            return

        per_page = 5
        total_pages = math.ceil(len(schedules) / per_page)
        view = ScheduleView(schedules, per_page, 1, total_pages)
        await interaction.response.send_message(view.build_content(), view=view)

    # ── Slash: remove own schedule ────────────────────────
    @app_commands.command(name="remove_schedule", description="Remove one of your schedules by index")
    async def remove_schedule(self, interaction: discord.Interaction, index: int):
        user_id = interaction.user.id
        schedules = self.load_schedules(user_id)

        if not (1 <= index <= len(schedules)):
            await interaction.response.send_message(
                f"❌ Index must be between 1 and {len(schedules)}.", ephemeral=True
            )
            return

        removed = schedules.pop(index - 1)
        self.notified.discard((user_id, removed.strip()))
        self.save_schedules(user_id, schedules)
        await interaction.response.send_message(f"✅ Removed: **{removed.strip()}**")

    # ── Slash: owner removes any user's schedule ──────────
    @app_commands.command(name="remove_user_schedule", description="[Owner] Remove any user's schedule by index")
    @is_owner()
    async def remove_user_schedule(self, interaction: discord.Interaction, user: discord.User, index: int):
        user_id = user.id
        schedules = self.load_schedules(user_id)

        if not (1 <= index <= len(schedules)):
            await interaction.response.send_message(
                f"❌ Invalid index for {user.name}.", ephemeral=True
            )
            return

        removed = schedules.pop(index - 1)
        self.notified.discard((user_id, removed.strip()))
        self.save_schedules(user_id, schedules)
        await interaction.response.send_message(
            f"✅ Removed **{user.name}**'s schedule: {removed.strip()}"
        )

    # ── Prefix: ping ──────────────────────────────────────
    @commands.command(name='ping', help="pong")
    async def ping(self, ctx):
        await ctx.reply(f'pong! (latency {round(self.bot.latency * 1000)}ms)')

    # ── Prefix: timer ─────────────────────────────────────
    @commands.command(name='timer', help="Format: baba timer N(s/m/h) [optional message]")
    async def timer(self, ctx, time_input: str, *msg):
        UNITS = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}

        def fmt(s: int) -> str:
            if s >= 3600:
                return f"{s // 3600}h {s % 3600 // 60}m {s % 60}s"
            if s >= 60:
                return f"{s // 60}m {s % 60}s"
            return f"{s}s"

        try:
            try:
                seconds = int(time_input)
            except ValueError:
                unit = time_input[-1].lower()
                if unit not in UNITS:
                    raise ValueError(f"Unknown unit '{unit}'")
                seconds = int(time_input[:-1]) * UNITS[unit]

            if seconds > 86400:
                await ctx.send("I can't do timers over a day long.")
                return
            if seconds <= 0:
                await ctx.send("Timers don't go into negatives :/")
                return

            remaining = seconds
            display = await ctx.send(f"⏱️ **Timer:** {fmt(remaining)}")

            while remaining > 0:
                await asyncio.sleep(1)
                remaining -= 1
                # Edit display every 5 seconds, and every second in the last 10
                if remaining % 5 == 0 or remaining <= 10:
                    try:
                        new_content = f"⏱️ **Timer:** {fmt(remaining)}" if remaining > 0 else "✅ **Done!**"
                        await display.edit(content=new_content)
                    except Exception:
                        break

            end_note = " ".join(msg) if msg else "Your countdown has ended!"
            await ctx.send(f"⏰ {ctx.author.mention} {end_note}")

        except Exception:
            await ctx.send(
                f"Couldn't parse `{time_input}`. Try something like `30s`, `5m`, or `2h`."
            )


# ══════════════════════════════════════════════════════════
#  Pagination View
# ══════════════════════════════════════════════════════════
class ScheduleView(View):
    def __init__(self, schedules: list[str], per_page: int, current_page: int, total_pages: int):
        super().__init__(timeout=120)
        self.schedules = schedules
        self.per_page = per_page
        self.current_page = current_page
        self.total_pages = total_pages
        self._update_buttons()

    def _update_buttons(self):
        self.previous.disabled = self.current_page <= 1
        self.next.disabled = self.current_page >= self.total_pages

    def build_content(self) -> str:
        start = (self.current_page - 1) * self.per_page
        end = start + self.per_page
        lines = self.schedules[start:end]
        formatted = "\n".join(
            f"`{start + i + 1}.` {line.strip()}" for i, line in enumerate(lines)
        )
        return f"📋 **Schedules (Page {self.current_page}/{self.total_pages}):**\n{formatted}"

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: Button):
        self.current_page -= 1
        self._update_buttons()
        await interaction.response.edit_message(content=self.build_content(), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: Button):
        self.current_page += 1
        self._update_buttons()
        await interaction.response.edit_message(content=self.build_content(), view=self)


async def setup(bot):
    await bot.add_cog(ScheduleCog(bot))
    print('schedule + time loaded!')