import contextlib
import discord
import os
import random
import asyncio
import logging
from discord.ext import commands
from discord.ui import Button, View
from discord import Embed, ButtonStyle
from typing import Optional
from yt_dlp import YoutubeDL


logger = logging.getLogger(__name__)
AUDIO_EXTENSIONS = frozenset(
    {".aac", ".flac", ".m4a", ".mp3", ".mp4", ".ogg", ".opus", ".wav", ".webm", ".wma"}
)


def parse_local_query(query):
    """Return a case-folded search query and an exact-token ``all`` flag."""
    tokens = query.lower().split()
    all_flag = "all" in tokens
    return " ".join(token for token in tokens if token != "all"), all_flag


def format_duration(duration):
    """Formats a duration in seconds as M:SS, or '?' when unknown."""
    mins, secs = divmod(int(duration) if duration else 0, 60)
    return f"{mins}:{secs:02d}" if duration else "?"


def local_song_title(path):
    """Build a display title, dropping all trailing bracketed labels."""
    base = os.path.splitext(os.path.basename(path))[0].strip()
    while base.endswith("]"):
        depth = 0
        opening = None
        for index in range(len(base) - 1, -1, -1):
            if base[index] == "]":
                depth += 1
            elif base[index] == "[":
                depth -= 1
                if depth == 0:
                    opening = index
                    break
        if opening is None or opening <= 0:
            break
        base = base[:opening].rstrip()
    return base


class GuildState:
    """Helper class to manage the state of a single guild."""
    def __init__(self):
        self.is_playing = False
        self.is_paused = False
        self.current = None
        self.music_queue = []
        self.song_history = []
        self.vc = None
        self.now_playing_message = None # To store the message with the embed and buttons
        self.pending_search_message = None
        self.volume = 0.25  # Default volume 25%
        self.silent = True  # Suppress routine volume/lifecycle notices by default.
        self.loop_mode = "off"  # "off", "single", "queue"
        self.idle_task = None  # Auto-disconnect timer
        self.play_lock = asyncio.Lock()
        self.connect_lock = asyncio.Lock()
        self.transition_lock = asyncio.Lock()
        self.playback_generation = 0
        self.search_generation = 0
        self.starting = False
        self.preparing_tasks = set()

class MusicControls(View):
    def __init__(self, music_cog_instance, ctx, *, state: GuildState):
        super().__init__(timeout=None)
        self.music_cog = music_cog_instance
        self.ctx = ctx
        self.state = state
        self.message = None
        self.pause_button = Button(label="Pause", style=ButtonStyle.primary, custom_id="pause_button")
        self.skip_button = Button(label="Skip", style=ButtonStyle.secondary, custom_id="skip_button")
        self.last_button = Button(label="Last", style=ButtonStyle.secondary, custom_id="last_button")
        self.loop_button = Button(label="Loop: Off", style=ButtonStyle.secondary, custom_id="loop_button")
        self.queue_button = Button(label="Queue", style=ButtonStyle.secondary, custom_id="queue_button")
        self.remove_button = Button(
            label="Remove Current",
            style=ButtonStyle.danger,
            custom_id="remove_current_button",
        )

        self.add_item(self.last_button)
        self.add_item(self.pause_button)
        self.add_item(self.skip_button)
        self.add_item(self.loop_button)
        self.add_item(self.queue_button)
        self.add_item(self.remove_button)

        self.pause_button.callback = self.pause_callback
        self.skip_button.callback = self.skip_callback
        self.last_button.callback = self.last_callback
        self.loop_button.callback = self.loop_callback
        self.queue_button.callback = self.queue_callback
        self.remove_button.callback = self.remove_current_callback

        self.update_button_states()

    def _is_live(self) -> bool:
        return self.music_cog._now_playing_is_live(
            self.ctx.guild.id,
            self.state,
            self.message,
        )

    async def _require_live(self, interaction: discord.Interaction) -> bool:
        if self._is_live():
            return True
        self.stop()
        await interaction.response.send_message(
            "❌ These music controls have expired. Use the current Now Playing panel.",
            ephemeral=True,
        )
        return False

    def update_button_states(self):
        if not self._is_live():
            return
        if self.state.is_paused:
            self.pause_button.label = "Resume"
            self.pause_button.style = ButtonStyle.success
        else:
            self.pause_button.label = "Pause"
            self.pause_button.style = ButtonStyle.primary

        if self.state.loop_mode == "single":
            self.loop_button.label = "Loop: Single 🔂"
            self.loop_button.style = ButtonStyle.success
        elif self.state.loop_mode == "queue":
            self.loop_button.label = "Loop: Queue 🔁"
            self.loop_button.style = ButtonStyle.success
        else:
            self.loop_button.label = "Loop: Off"
            self.loop_button.style = ButtonStyle.secondary

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await self._require_live(interaction):
            return False
        if not interaction.user.voice:
            await interaction.response.send_message("❌ You must be in a voice channel to use music controls <:baba:1422080743886291025>", ephemeral=True)
            return False

        if self.state.vc and interaction.user.voice.channel == self.state.vc.channel:
            return True
        await interaction.response.send_message("❌ You must be in the same voice channel as baba <:baba:1422080743886291025>", ephemeral=True)
        return False

    async def pause_callback(self, interaction: discord.Interaction):
        if not await self._require_live(interaction):
            return
        result = await self.music_cog.pause.callback(
            self.music_cog,
            self.ctx,
            expected_state=self.state,
            expected_message=self.message,
        )
        if result is False:
            self.stop()
            await interaction.response.send_message(
                "❌ These music controls have expired. Use the current Now Playing panel.",
                ephemeral=True,
            )
            return
        async with self.state.play_lock:
            if not self._is_live():
                self.stop()
                await interaction.response.send_message(
                    "❌ These music controls have expired. Use the current Now Playing panel.",
                    ephemeral=True,
                )
                return
            self.update_button_states()
            await interaction.response.edit_message(view=self)

    async def skip_callback(self, interaction: discord.Interaction):
        if not await self._require_live(interaction):
            return
        await interaction.response.defer()
        result = await self.music_cog.skip.callback(
            self.music_cog,
            self.ctx,
            expected_state=self.state,
            expected_message=self.message,
        )
        if result is False:
            self.stop()
            await interaction.followup.send(
                "❌ These music controls have expired. Use the current Now Playing panel.",
                ephemeral=True,
            )

    async def loop_callback(self, interaction: discord.Interaction):
        if not await self._require_live(interaction):
            return
        async with self.state.play_lock, self.state.transition_lock:
            if not self._is_live():
                self.stop()
                await interaction.response.send_message(
                    "❌ These music controls have expired. Use the current Now Playing panel.",
                    ephemeral=True,
                )
                return
            if self.state.loop_mode == "off":
                self.state.loop_mode = "single"
            elif self.state.loop_mode == "single":
                self.state.loop_mode = "queue"
            else:
                self.state.loop_mode = "off"
            self.update_button_states()
            await interaction.response.edit_message(view=self)

    async def queue_callback(self, interaction: discord.Interaction):
        if not await self._require_live(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        async with self.state.play_lock:
            if not self._is_live():
                self.stop()
                await interaction.followup.send(
                    "❌ These music controls have expired. Use the current Now Playing panel.",
                    ephemeral=True,
                )
                return
            if not self.state.music_queue:
                await interaction.followup.send("❌ No music in queue <:baba:1422080743886291025>", ephemeral=True)
            else:
                embed = self.music_cog._build_queue_embed(self.state)
                await interaction.followup.send(embed=embed, ephemeral=True)

    async def last_callback(self, interaction: discord.Interaction):
        if not await self._require_live(interaction):
            return
        await interaction.response.defer()
        result = await self.music_cog.last.callback(
            self.music_cog,
            self.ctx,
            expected_state=self.state,
            expected_message=self.message,
        )
        if result is False:
            self.stop()
            await interaction.followup.send(
                "❌ These music controls have expired. Use the current Now Playing panel.",
                ephemeral=True,
            )

    async def remove_current_callback(self, interaction: discord.Interaction):
        if not await self._require_live(interaction):
            return
        if self.state.current:
            await interaction.response.defer()
            result = await self.music_cog.remove.callback(
                self.music_cog,
                self.ctx,
                "current",
                expected_state=self.state,
                expected_message=self.message,
            )
            if result is False:
                self.stop()
                await interaction.followup.send(
                    "❌ These music controls have expired. Use the current Now Playing panel.",
                    ephemeral=True,
                )
        else:
            await interaction.response.send_message("❌ No song is currently playing to remove.", ephemeral=True)


class YouTubeSearchView(View):
    """Presents top 3 YouTube results as buttons for the user to pick from."""
    def __init__(self, music_cog_instance, ctx, results: list, play_first: bool = False, *, state: GuildState, request_generation: int | None = None):
        super().__init__(timeout=30)
        self.music_cog = music_cog_instance
        self.ctx = ctx
        self.results = results
        self.play_first = play_first
        self.state = state
        self.request_generation = state.search_generation if request_generation is None else request_generation
        self.chosen = False
        self.expired = False
        self.message = None

        for i, result in enumerate(results):
            label = f"{i + 1}. {result['title'][:50]}"
            btn = Button(label=label, style=ButtonStyle.secondary, custom_id=f"yt_search_{i}")
            btn.callback = self._make_callback(i)
            self.add_item(btn)

        cancel_btn = Button(label="Cancel", style=ButtonStyle.danger, custom_id="yt_search_cancel")
        cancel_btn.callback = self.cancel_callback
        self.add_item(cancel_btn)

    def _is_live(self) -> bool:
        return (
            self.music_cog._state_is_live(self.ctx.guild.id, self.state)
            and self.state.search_generation == self.request_generation
            and (
                self.message is None
                or self.state.pending_search_message is self.message
            )
        )

    def _enqueue(self, song):
        if self.play_first:
            self.state.music_queue.insert(0, song)
        else:
            self.state.music_queue.append(song)

    def _make_callback(self, index: int):
        async def callback(interaction: discord.Interaction):
            if interaction.user != self.ctx.author:
                await interaction.response.send_message("❌ Only the person who searched can pick a song.", ephemeral=True)
                return
            if self.expired or not self._is_live():
                self.expired = True
                self.stop()
                await interaction.response.edit_message(
                    embed=Embed(description="❌ This search has expired.", color=discord.Color.red()),
                    view=None,
                )
                return
            async with self.state.transition_lock:
                if not self._is_live():
                    self.expired = True
                    self.stop()
                    await interaction.response.edit_message(
                        embed=Embed(description="❌ This search has expired.", color=discord.Color.red()),
                        view=None,
                    )
                    return
                self.chosen = True
                self.expired = True
                self.stop()
                self.music_cog._advance_search_generation(self.state)
                song = self.results[index]
                self._enqueue(song)
                embed = Embed(
                    description=f"✅ **#{len(self.state.music_queue)} - '{song['title']}'** added to the queue.",
                    color=discord.Color.green()
                )
                await interaction.response.edit_message(embed=embed, view=None)
                should_start = not self.state.is_playing
            if should_start:
                await self.music_cog.play_music(self.ctx, state=self.state)
        return callback

    async def cancel_callback(self, interaction: discord.Interaction):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message("❌ Only the person who searched can cancel.", ephemeral=True)
            return
        if not self._is_live():
            self.expired = True
            self.stop()
            await interaction.response.edit_message(
                embed=Embed(description="❌ This search has expired.", color=discord.Color.red()), view=None
            )
            return
        async with self.state.transition_lock:
            if not self._is_live():
                self.expired = True
                self.stop()
                await interaction.response.edit_message(
                    embed=Embed(description="❌ This search has expired.", color=discord.Color.red()), view=None
                )
                return
            self.chosen = True
            self.expired = True
            self.stop()
            self.music_cog._advance_search_generation(self.state)
            await interaction.response.edit_message(
                embed=Embed(description="✅ Search cancelled.", color=discord.Color.red()), view=None
            )

    async def on_timeout(self):
        if self.chosen or self.expired:
            return
        self.chosen = True
        self.expired = True
        self.stop()
        if self.message:
            with contextlib.suppress(Exception):
                await self.message.edit(view=None)
        async with self.state.transition_lock:
            if not self._is_live():
                return
            self.music_cog._advance_search_generation(self.state)
            song = self.results[0]
            self._enqueue(song)
            await self.music_cog._send_if_unsilenced(
                self.ctx,
                self.state,
                f"⏱️ No selection — auto-adding **'{song['title']}'**",
            )
            should_start = not self.state.is_playing
        if should_start:
            await self.music_cog.play_music(self.ctx, state=self.state)


class music_cog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.music_folder = "C:/Users/manza/Music"
        self.guild_states = {} # Dictionary to hold the state for each guild
        self.guild_silence = {}  # Per-guild override; missing guilds stay silent.
        self._unloading = False
        self.YDL_OPTIONS = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "skip_download": False,
            "outtmpl": os.path.join(self.music_folder, '%(title)s.%(ext)s'),
        }
        self.FFMPEG_OPTIONS = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn'
        }

    def _get_or_create_state(self, guild_id: int) -> GuildState:
        if guild_id not in self.guild_states:
            state = GuildState()
            state.silent = self.guild_silence.get(guild_id, True)
            self.guild_states[guild_id] = state
        return self.guild_states[guild_id]

    async def _send_if_unsilenced(self, ctx, state: GuildState, content=None, **kwargs):
        if state.silent:
            return None
        if content is not None:
            content = f"✅ {content}"
        return await ctx.send(content, **kwargs)

    async def _send_error(self, ctx, content, **kwargs):
        return await ctx.send(f"❌ {content}", **kwargs)

    def _state_is_live(self, guild_id: int, state: GuildState) -> bool:
        return not self._unloading and self.guild_states.get(guild_id) is state

    def _now_playing_is_live(
        self,
        guild_id: int,
        state: GuildState,
        message=None,
    ) -> bool:
        return (
            self._state_is_live(guild_id, state)
            and (message is None or state.now_playing_message is message)
        )

    @staticmethod
    def _voice_owns_source(state: GuildState) -> bool:
        return bool(state.vc and (state.vc.is_playing() or state.vc.is_paused()))

    @staticmethod
    def _cleanup_audio_source(source):
        if source is not None:
            with contextlib.suppress(Exception):
                source.cleanup()

    @staticmethod
    def _remember_song(state: GuildState, song):
        if not song:
            return
        state.song_history.insert(0, song)
        if len(state.song_history) > 50:
            state.song_history.pop()

    @staticmethod
    def _invalidate_playback(state: GuildState):
        state.playback_generation += 1
        state.starting = False

    @staticmethod
    def _current_task():
        try:
            return asyncio.current_task()
        except RuntimeError:
            return None

    def _cancel_idle_timer(self, state: GuildState):
        idle_task = state.idle_task
        state.idle_task = None
        if (
            idle_task
            and idle_task is not self._current_task()
            and not idle_task.done()
        ):
            idle_task.cancel()

    def _cancel_preparing_tasks(self, state: GuildState):
        current_task = self._current_task()
        preparing_tasks = tuple(state.preparing_tasks)
        state.preparing_tasks = {
            task for task in preparing_tasks if task is current_task and not task.done()
        }
        for preparing_task in preparing_tasks:
            if (
                preparing_task is not current_task
                and not preparing_task.done()
            ):
                preparing_task.cancel()

    def _advance_search_generation(self, state: GuildState):
        state.search_generation += 1
        pending_message = state.pending_search_message
        state.pending_search_message = None
        return state.search_generation, pending_message

    @staticmethod
    async def _expire_search_message(message):
        if message is not None:
            with contextlib.suppress(Exception):
                await message.edit(view=None)

    async def _run_preparation(self, guild_id: int, state: GuildState, awaitable):
        task = self._current_task()
        state.preparing_tasks.add(task)
        try:
            result = await awaitable
            if not self._state_is_live(guild_id, state):
                raise asyncio.CancelledError
            return result
        finally:
            state.preparing_tasks.discard(task)

    def _start_idle_timer(self, ctx):
        guild_id = ctx.guild.id
        state = self.guild_states.get(guild_id)
        if state is None:
            return
        self._cancel_idle_timer(state)

        async def _idle_disconnect():
            try:
                await asyncio.sleep(180) # 3 minutes idle timeout
                if not self._state_is_live(guild_id, state):
                    return
                if not state.is_playing and not state.is_paused and not state.starting and state.vc and state.vc.is_connected():
                    with contextlib.suppress(Exception):
                        await self._send_if_unsilenced(
                            ctx,
                            state,
                            "💤 Left voice channel due to inactivity <:baba:1422080743886291025>",
                        )
                    await self._leave_guild(ctx)
            except asyncio.CancelledError:
                return
            finally:
                current = self._current_task()
                if state.idle_task is current:
                    state.idle_task = None

        state.idle_task = asyncio.create_task(_idle_disconnect())

    async def search_yt(self, item: str):
        loop = asyncio.get_running_loop()
        def _extract():
            try:
                search_opts = self.YDL_OPTIONS.copy()
                with YoutubeDL(search_opts) as ydl:
                    if item.startswith(("http://", "https://")):
                        info = ydl.extract_info(item, download=False)
                        if info is None: return None
                        if "entries" in info and info["entries"]:
                            info = info["entries"][0]
                    else:
                        search_info = ydl.extract_info(f"ytsearch1:{item}", download=False)
                        if not search_info or not search_info.get("entries"): return None
                        info = search_info["entries"][0]

                    url = info.get("webpage_url") or f"https://www.youtube.com/watch?v={info.get('id')}"
                    title = info.get("title", "Unknown title")
                    duration = info.get("duration", 0)
                    thumbnail = info.get("thumbnail")
                    uploader = info.get("uploader") or info.get("channel") or ""
                    
                    duration_str = format_duration(duration)

                    return {
                        "source": url,
                        "title": title,
                        "duration": duration,
                        "duration_str": duration_str,
                        "thumbnail": thumbnail,
                        "uploader": uploader
                    }
            except Exception as e:
                print(f"[search_yt] Error: {e}")
                return None

        return await loop.run_in_executor(None, _extract)

    def search_yt_multiple(self, item: str, count: int = 3):
        """Returns top `count` YouTube search results as a list of dicts."""
        try:
            search_opts = self.YDL_OPTIONS.copy()
            search_opts['extract_flat'] = True
            
            with YoutubeDL(search_opts) as ydl_search:
                search_info = ydl_search.extract_info(f"ytsearch{count}:{item}", download=False)
                
            if not search_info or not search_info.get("entries"):
                return []
                
            results = []
            for entry in search_info["entries"]:
                if not entry:
                    continue
                url = entry.get("webpage_url") or entry.get("url") or f"https://www.youtube.com/watch?v={entry.get('id')}"
                title = entry.get("title", "Unknown title")
                duration = entry.get("duration", 0)
                duration_str = format_duration(duration)
                results.append({
                    "source": url,
                    "title": title,
                    "duration": duration,
                    "duration_str": duration_str,
                    "uploader": entry.get("uploader") or entry.get("channel") or ""
                })
            return results
        except Exception as e:
            print(f"[search_yt_multiple] Error: {e}")
            return []

    async def fetch_playlist_videos(self, playlist_url: str):
        loop = asyncio.get_running_loop()
        def _extract():
            try:
                opts = self.YDL_OPTIONS.copy()
                opts['extract_flat'] = True
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(playlist_url, download=False)
                    if not info:
                        return []
                    entries = info.get("entries") or []
                    videos = []
                    for entry in entries:
                        if not entry:
                            continue
                        url = entry.get("webpage_url") or entry.get("url") or (f"https://www.youtube.com/watch?v={entry.get('id')}" if entry.get("id") else None)
                        title = entry.get("title") or entry.get("name") or "Unknown title"
                        duration = entry.get("duration", 0)
                        duration_str = format_duration(duration)
                        if url:
                            videos.append({
                                "source": url,
                                "title": title,
                                "duration": duration,
                                "duration_str": duration_str
                            })
                    return videos
            except Exception as e:
                print(f"[fetch_playlist_videos] Error: {e}")
                return []

        return await loop.run_in_executor(None, _extract)

    def _local_audio_files(self):
        return [
            filename
            for filename in os.listdir(self.music_folder)
            if os.path.isfile(os.path.join(self.music_folder, filename))
            and os.path.splitext(filename)[1].lower() in AUDIO_EXTENSIONS
        ]

    def get_random_song(self, files=None):
        if files is None:
            files = self._local_audio_files()
        if not files:
            return None
        return os.path.join(self.music_folder, random.choice(files))

    def _build_queue_embed(self, state):
        retval = ""
        for i, song in enumerate(state.music_queue[:10]):
            duration_str = f" `[{song.get('duration_str', '?')}]`" if song.get('duration_str') else ""
            retval += f"#{i + 1} - {song['title']}{duration_str}\n"
        loop_str = f" | Loop: {state.loop_mode.capitalize()}" if state.loop_mode != "off" else ""
        return Embed(title="✅ Music Queue <:baba:1422080743886291025>", description=f"```\n{retval}\nTotal songs: {len(state.music_queue)}{loop_str}\n```", color=discord.Color.gold())

    async def send_music_embed(
        self,
        ctx,
        song,
        message_type="<:baba:1422080743886291025>",
        *,
        state=None,
    ):
        state = state or self.guild_states.get(ctx.guild.id)
        if state is None or not self._state_is_live(ctx.guild.id, state):
            return
        song_title = song.get("title", "Unknown Title") if isinstance(song, dict) else str(song)
        
        link_url = song.get("webpage_url") or song.get("source", "") if isinstance(song, dict) else ""
        description = f"**[{song_title}]({link_url})**" if link_url.startswith(("http://", "https://")) else f"**{song_title}**"
        embed = Embed(title=f"✅ {message_type} Now Playing 🎶", description=description, color=discord.Color.blue())
        
        if isinstance(song, dict):
            if song.get("uploader"):
                embed.add_field(name="Channel", value=song["uploader"], inline=True)
            if song.get("duration_str"):
                embed.add_field(name="Duration", value=song["duration_str"], inline=True)
            if song.get("thumbnail"):
                embed.set_thumbnail(url=song["thumbnail"])

        loop_status = f"Loop: {state.loop_mode.capitalize()}" if state.loop_mode != "off" else "Loop: Off"
        vol_status = f"Volume: {int(state.volume * 100)}%"
        embed.set_footer(text=f"{vol_status} • {loop_status}")

        view = MusicControls(self, ctx, state=state)
        _, pending_search_message = self._advance_search_generation(state)

        if state.now_playing_message:
            with contextlib.suppress(Exception):
                await state.now_playing_message.delete()
        
        message = await ctx.send(embed=embed, view=view)
        view.message = message
        state.now_playing_message = message
        await self._expire_search_message(pending_search_message)


    def _schedule_after(self, ctx, state: GuildState, token: int, error):
        guild_id = ctx.guild.id
        if not self._state_is_live(guild_id, state) or state.playback_generation != token:
            return
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._handle_after(ctx, state, token, error), self.bot.loop
            )
        except RuntimeError:
            logger.exception("Could not schedule music after callback guild_id=%s", guild_id)
            return

        def _log_callback_failure(done_future):
            if done_future.cancelled():
                return
            try:
                done_future.result()
            except Exception:
                logger.exception("Music after callback crashed guild_id=%s", guild_id)

        future.add_done_callback(_log_callback_failure)

    async def _handle_after(self, ctx, state: GuildState, token: int, error):
        guild_id = ctx.guild.id
        if error is not None:
            logger.error("FFmpeg playback error guild_id=%s: %s", guild_id, error)
        async with state.play_lock:
            if not self._state_is_live(guild_id, state) or state.playback_generation != token:
                return
            state.is_playing = False
            state.is_paused = False
            if error is not None:
                state.current = None
                with contextlib.suppress(Exception):
                    await self._send_error(ctx, "Playback error; skipping this song.")
            await self._play_music_locked(ctx, state, allow_loop=error is None)

    async def play_music(self, ctx, *, state=None):
        state = state or self._get_or_create_state(ctx.guild.id)
        async with state.play_lock:
            if not self._state_is_live(ctx.guild.id, state):
                return
            await self._play_music_locked(ctx, state)

    async def _play_music_locked(self, ctx, state: GuildState, *, allow_loop=True):
        guild_id = ctx.guild.id
        if not self._state_is_live(guild_id, state):
            return
        if state.starting or self._voice_owns_source(state):
            return
        self._cancel_idle_timer(state)

        previous = state.current
        state.current = None
        if previous:
            if allow_loop and state.loop_mode == "single":
                state.music_queue.insert(0, previous)
            elif allow_loop and state.loop_mode == "queue":
                state.music_queue.append(previous)
            else:
                self._remember_song(state, previous)

        while self._state_is_live(guild_id, state):
            if not state.music_queue:
                state.is_playing = False
                state.is_paused = False
                state.starting = False
                if state.now_playing_message:
                    with contextlib.suppress(Exception):
                        await state.now_playing_message.edit(
                            embed=Embed(
                                description="No songs left in queue <:baba:1422080743886291025>",
                                color=discord.Color.red(),
                            ),
                            view=None,
                        )
                self._start_idle_timer(ctx)
                return

            song = state.music_queue.pop(0)
            state.current = song
            state.is_playing = False
            state.is_paused = False
            state.starting = True
            source_to_play = song["source"]
            loop = asyncio.get_running_loop()
            audio_source = None
            source_started = False

            try:
                if source_to_play.startswith(("http://", "https://")):
                    song.setdefault("webpage_url", source_to_play)

                    def _get_stream_info():
                        with YoutubeDL(self.YDL_OPTIONS) as ydl:
                            return ydl.extract_info(source_to_play, download=False)

                    info = await self._run_preparation(
                        guild_id,
                        state,
                        loop.run_in_executor(None, _get_stream_info),
                    )
                    if not info:
                        raise RuntimeError("No playable media information returned")
                    duration = info.get("duration", 0)
                    song["title"] = info.get("title", song.get("title"))
                    song["thumbnail"] = info.get("thumbnail", song.get("thumbnail"))
                    song["uploader"] = info.get("uploader") or info.get("channel") or song.get("uploader", "")
                    song["duration"] = duration
                    song["duration_str"] = format_duration(duration)

                    if 0 < duration < 360:
                        await ctx.send(f"```Downloading '{song['title']}'...```", delete_after=8)

                        def _download():
                            ydl_opts_download = self.YDL_OPTIONS.copy()
                            ydl_opts_download["outtmpl"] = os.path.join(self.music_folder, "%(title)s.%(ext)s")
                            with YoutubeDL(ydl_opts_download) as ydl:
                                dl_info = ydl.extract_info(source_to_play, download=True)
                                return ydl.prepare_filename(dl_info)

                        downloaded_path = await self._run_preparation(
                            guild_id,
                            state,
                            loop.run_in_executor(None, _download),
                        )
                        if downloaded_path and os.path.exists(downloaded_path):
                            source_to_play = os.path.realpath(downloaded_path)
                            song["source"] = source_to_play
                        else:
                            source_to_play = info.get("url")
                    else:
                        source_to_play = info.get("url")

                if not source_to_play:
                    raise RuntimeError("No playable audio source returned")
                ffmpeg_options = (
                    self.FFMPEG_OPTIONS
                    if source_to_play.startswith(("http://", "https://"))
                    else {"options": "-vn"}
                )
                raw_audio = discord.FFmpegPCMAudio(
                    source_to_play, executable="ffmpeg.exe", **ffmpeg_options
                )
                audio_source = raw_audio
                volume_audio = discord.PCMVolumeTransformer(raw_audio, volume=state.volume)
                audio_source = volume_audio
                await self.send_music_embed(
                    ctx,
                    song,
                    "<:baba:1422080743886291025>",
                    state=state,
                )

                if not self._state_is_live(guild_id, state) or state.current is not song:
                    state.starting = False
                    return
                state.playback_generation += 1
                token = state.playback_generation
                state.vc.play(
                    volume_audio,
                    after=lambda error, st=state, gen=token: self._schedule_after(ctx, st, gen, error),
                )
                source_started = True
                state.is_playing = True
                state.is_paused = False
                state.starting = False
                return
            except asyncio.CancelledError:
                state.current = None
                state.is_playing = False
                state.is_paused = False
                state.starting = False
                raise
            except Exception as exc:
                logger.exception("Could not start music guild_id=%s title=%s", guild_id, song.get("title", "unknown"))
                state.current = None
                state.starting = False
                with contextlib.suppress(Exception):
                    await self._send_error(
                        ctx,
                        f"Error playing **{song.get('title', 'unknown')}**; skipping this song.",
                    )
                previous = None
                allow_loop = False
            finally:
                if not source_started:
                    self._cleanup_audio_source(audio_source)


    @commands.command(name="play", aliases=["p", "playing","sing","PLAY", "playfirst"], help="Plays a song from YouTube or local files.")
    async def play(self, ctx, *, query: str = ""):
        guild_id = ctx.guild.id
        state = self._get_or_create_state(guild_id)

        if not ctx.author.voice:
            await self._send_error(
                ctx,
                "You need to be in a voice channel first <:baba:1422080743886291025>",
            )
            return
        request_generation, stale_search_message = self._advance_search_generation(state)
        await self._expire_search_message(stale_search_message)
        if (
            not self._state_is_live(guild_id, state)
            or state.search_generation != request_generation
        ):
            return

        reconnected_with_pending = False
        if state.vc is None or not state.vc.is_connected():
            async with state.connect_lock:
                async with state.play_lock, state.transition_lock:
                    if state.vc is None or not state.vc.is_connected():
                        old_voice_client = state.vc
                        pending_current = state.current
                        had_pending = bool(pending_current or state.music_queue)
                        new_voice_client = None
                        connected_here = False
                        try:
                            existing_vc = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
                            if existing_vc and existing_vc.is_connected():
                                new_voice_client = existing_vc
                            else:
                                new_voice_client = await ctx.author.voice.channel.connect()
                                connected_here = True
                        except Exception:
                            logger.exception("Could not connect to voice guild_id=%s", guild_id)
                            await self._send_error(
                                ctx,
                                "Couldn't connect to your voice channel. Leave and rejoin the voice channel, then try again."
                            )
                            return

                        if not self._state_is_live(guild_id, state):
                            if connected_here and new_voice_client and new_voice_client.is_connected():
                                with contextlib.suppress(Exception):
                                    await new_voice_client.disconnect(force=True)
                            return

                        self._invalidate_playback(state)
                        if (
                            old_voice_client
                            and old_voice_client is not new_voice_client
                            and (old_voice_client.is_playing() or old_voice_client.is_paused())
                        ):
                            with contextlib.suppress(Exception):
                                old_voice_client.stop()
                        state.is_playing = False
                        state.is_paused = False
                        if pending_current:
                            state.music_queue.insert(0, pending_current)
                        state.current = None
                        state.vc = new_voice_client
                        reconnected_with_pending = had_pending

        if not self._state_is_live(guild_id, state):
            return
        self._cancel_idle_timer(state)

        if reconnected_with_pending and not query:
            await self.play_music(ctx, state=state)
            return

        if state.is_paused and not query:
            await self.resume.callback(self, ctx)
            return

        play_first = getattr(ctx, "invoked_with", "").casefold() == "playfirst"

        if query.isdigit():
            num_songs = min(max(int(query), 1), 100)
            local_files = self._local_audio_files()
            songs = []
            for _ in range(num_songs):
                song_path = self.get_random_song(local_files)
                if song_path:
                    songs.append({'source': song_path, 'title': local_song_title(song_path)})
            async with state.transition_lock:
                if (
                    not self._state_is_live(guild_id, state)
                    or state.search_generation != request_generation
                ):
                    return
                if play_first:
                    state.music_queue[0:0] = songs
                else:
                    state.music_queue.extend(songs)
                await self._send_if_unsilenced(ctx, state, f"**{num_songs} random songs** added to the queue.")
                should_start = not state.is_playing
            if should_start:
                await self.play_music(ctx, state=state)
            return

        if not query:
            async with state.transition_lock:
                if (
                    not self._state_is_live(guild_id, state)
                    or state.search_generation != request_generation
                ):
                    return
                song_path = self.get_random_song()
                if song_path:
                    title = local_song_title(song_path)
                    song = {'source': song_path, 'title': title}
                    if play_first:
                        state.music_queue.insert(0, song)
                    else:
                        state.music_queue.append(song)
                    await self._send_if_unsilenced(ctx, state, f"**'{title}'** added to the queue.")
                else:
                    await self._send_error(ctx, "No songs found in the music folder.")
        elif 'playlist?list=' in query:
            msg = await ctx.send("```Loading playlist...```")
            try:
                videos = await self._run_preparation(
                    guild_id,
                    state,
                    self.fetch_playlist_videos(query),
                )
                if (
                    not self._state_is_live(guild_id, state)
                    or state.search_generation != request_generation
                ):
                    await self._expire_search_message(msg)
                    return
                if not videos:
                    async with state.transition_lock:
                        if (
                            not self._state_is_live(guild_id, state)
                            or state.search_generation != request_generation
                        ):
                            await self._expire_search_message(msg)
                            return
                        await msg.edit(content="❌ ```Couldn't read that playlist (no entries found).```")
                    return

                songs = [
                    {
                        "source": video["source"],
                        "title": video["title"],
                        "duration": video.get("duration"),
                        "duration_str": video.get("duration_str"),
                    }
                    for video in videos
                ]
                async with state.transition_lock:
                    if (
                        not self._state_is_live(guild_id, state)
                        or state.search_generation != request_generation
                    ):
                        await self._expire_search_message(msg)
                        return
                    if play_first:
                        state.music_queue[0:0] = songs
                    else:
                        state.music_queue.extend(songs)
                    await msg.edit(content=f"```{len(songs)} songs added to the queue.```")
            except asyncio.CancelledError:
                await self._expire_search_message(msg)
                raise
            except Exception:
                if (
                    self._state_is_live(guild_id, state)
                    and state.search_generation == request_generation
                ):
                    await msg.edit(content="❌ ```Error loading playlist.```")
                else:
                    await self._expire_search_message(msg)
        elif query.startswith(("http://", "https://")) or "youtube.com" in query or "youtu.be" in query:
            try:
                song_info = await self._run_preparation(
                    guild_id,
                    state,
                    self.search_yt(query),
                )
                async with state.transition_lock:
                    if (
                        not self._state_is_live(guild_id, state)
                        or state.search_generation != request_generation
                    ):
                        return
                    if not song_info:
                        await self._send_error(ctx, "```Couldn't find that YouTube video.```")
                        return
                    if play_first:
                        state.music_queue.insert(0, song_info)
                    else:
                        state.music_queue.append(song_info)
                    await self._send_if_unsilenced(ctx, state, f"**'{song_info['title']}'** added to the queue.")
            except asyncio.CancelledError:
                raise
            except Exception:
                if (
                    self._state_is_live(guild_id, state)
                    and state.search_generation == request_generation
                ):
                    await self._send_error(ctx, "```Error processing YouTube link.```")
        else:
            query_for_search, all_flag = parse_local_query(query)
            query_words = query_for_search.split()
            matched_files = [
                filename
                for filename in self._local_audio_files()
                if all(word in filename.lower() for word in query_words)
            ]

            if matched_files:
                files_to_add = matched_files if all_flag else [random.choice(matched_files)]
                songs = [
                    {
                        "source": os.path.join(self.music_folder, filename),
                        "title": local_song_title(filename),
                    }
                    for filename in files_to_add
                ]
                async with state.transition_lock:
                    if (
                        not self._state_is_live(guild_id, state)
                        or state.search_generation != request_generation
                    ):
                        return
                    if play_first:
                        state.music_queue[0:0] = songs
                    else:
                        state.music_queue.extend(songs)
                    await self._send_if_unsilenced(
                        ctx,
                        state,
                        f"**'{files_to_add[0] if len(files_to_add) == 1 else f'{len(files_to_add)} songs'}'** added to the queue.",
                    )
            else:
                msg = await ctx.send("```Searching YouTube...```")
                try:
                    results = await self._run_preparation(
                        guild_id,
                        state,
                        asyncio.get_running_loop().run_in_executor(
                            None, lambda: self.search_yt_multiple(query, 3)
                        ),
                    )
                    async with state.transition_lock:
                        if (
                            not self._state_is_live(guild_id, state)
                            or state.search_generation != request_generation
                        ):
                            await self._expire_search_message(msg)
                            return
                        with contextlib.suppress(Exception):
                            await msg.delete()
                        if not results:
                            await self._send_error(ctx, "Could not find any results on YouTube.")
                        else:
                            embed = Embed(
                                title="✅ 🔎 YouTube Search Results",
                                description="\n".join(
                                    f"**{i+1}.** {r['title']} `[{r['duration_str']}]`"
                                    for i, r in enumerate(results)
                                ),
                                color=discord.Color.red()
                            )
                            embed.set_footer(text="Pick a song below • auto-selects #1 after 30s")
                            view = YouTubeSearchView(
                                self,
                                ctx,
                                results,
                                play_first,
                                state=state,
                                request_generation=request_generation,
                            )
                            view.message = await ctx.send(embed=embed, view=view)
                            if (
                                self._state_is_live(guild_id, state)
                                and state.search_generation == request_generation
                            ):
                                state.pending_search_message = view.message
                            else:
                                await self._expire_search_message(view.message)
                            return
                except asyncio.CancelledError:
                    await self._expire_search_message(msg)
                    raise
                except Exception:
                    if (
                        self._state_is_live(guild_id, state)
                        and state.search_generation == request_generation
                    ):
                        await self._send_error(ctx, "Could not complete the YouTube search.")
                    else:
                        await self._expire_search_message(msg)


        if not state.is_playing:
            await self.play_music(ctx, state=state)


    @commands.command(name="silence", aliases=["quiet"], help="Silences or restores routine music notices for this server.")
    async def silence(self, ctx, mode: Optional[str] = None):
        state = self._get_or_create_state(ctx.guild.id)
        if mode is None:
            silent = not state.silent
        else:
            mode_clean = mode.casefold()
            if mode_clean in {"on", "true", "yes", "silent", "quiet"}:
                silent = True
            elif mode_clean in {"off", "false", "no", "loud", "verbose"}:
                silent = False
            else:
                await self._send_error(ctx, "Usage: `baba silence on|off`.")
                return

        self.guild_silence[ctx.guild.id] = silent
        state.silent = silent
        status = "silenced" if silent else "enabled"
        await ctx.send(f"✅ Routine music notices are now **{status}** for this server.")

    @commands.command(name="volume", aliases=["v", "vol"], help="Sets or views music volume (0-200%).")
    async def volume(self, ctx, vol: Optional[int] = None):
        state = self._get_or_create_state(ctx.guild.id)
        if vol is None:
            await self._send_if_unsilenced(ctx, state, f"🔊 Current volume: **{int(state.volume * 100)}%**")
            return

        vol = min(max(vol, 0), 200)
        state.volume = vol / 100.0

        if state.vc and state.vc.source and hasattr(state.vc.source, "volume"):
            state.vc.source.volume = state.volume

        await self._send_if_unsilenced(ctx, state, f"🔊 Volume set to **{vol}%**")


    @commands.command(name="shuffle", aliases=["sh"], help="Shuffles the current music queue.")
    async def shuffle(self, ctx):
        state = self._get_or_create_state(ctx.guild.id)
        if not state.music_queue:
            await self._send_error(ctx, "Queue is empty, nothing to shuffle.")
            return
        random.shuffle(state.music_queue)
        await self._send_if_unsilenced(ctx, state, f"🔀 Shuffled **{len(state.music_queue)}** songs in the queue!")


    @commands.command(name="loop", aliases=["repeat"], help="Toggles loop mode: off, single, queue.")
    async def loop_cmd(self, ctx, mode: Optional[str] = None):
        state = self._get_or_create_state(ctx.guild.id)
        if mode:
            mode_clean = mode.lower()
            if mode_clean in ["single", "one", "song"]:
                state.loop_mode = "single"
            elif mode_clean in ["queue", "all"]:
                state.loop_mode = "queue"
            else:
                state.loop_mode = "off"
        else:
            if state.loop_mode == "off":
                state.loop_mode = "single"
            elif state.loop_mode == "single":
                state.loop_mode = "queue"
            else:
                state.loop_mode = "off"

        mode_icons = {"off": "➡️ Off", "single": "🔂 Single Song", "queue": "🔁 Entire Queue"}
        await self._send_if_unsilenced(ctx, state, f"🔁 Loop mode set to: **{mode_icons[state.loop_mode]}**")


    @commands.command(name="move", aliases=["mv"], help="Moves a song from one position to another in queue.")
    async def move(self, ctx, from_pos: int, to_pos: int):
        state = self._get_or_create_state(ctx.guild.id)
        if not state.music_queue:
            await self._send_error(ctx, "Queue is empty.")
            return

        if 1 <= from_pos <= len(state.music_queue) and 1 <= to_pos <= len(state.music_queue):
            song = state.music_queue.pop(from_pos - 1)
            state.music_queue.insert(to_pos - 1, song)
            await self._send_if_unsilenced(ctx, state, f"🚚 Moved **'{song['title']}'** to position **#{to_pos}**.")
        else:
            await self._send_error(ctx, f"Invalid positions. Queue size is **{len(state.music_queue)}**.")


    @commands.command(name="skipto", aliases=["st"], help="Skips directly to a specific song number in queue.")
    async def skipto(self, ctx, pos: int):
        state = self._get_or_create_state(ctx.guild.id)
        if state.starting:
            self._cancel_preparing_tasks(state)
        async with state.play_lock, state.transition_lock:
            queue_size = len(state.music_queue)
            if not queue_size:
                await self._send_error(ctx, "Queue is empty.")
                return
            if not 1 <= pos <= queue_size:
                await self._send_error(ctx, f"Invalid song position. Queue size is **{queue_size}**.")
                return

            state.music_queue = state.music_queue[pos - 1:]
            interrupted = state.current
            state.current = None
            self._advance_search_generation(state)
            self._remember_song(state, interrupted)
            self._invalidate_playback(state)
            state.is_playing = False
            state.is_paused = False
            if self._voice_owns_source(state):
                state.vc.stop()
            await self._play_music_locked(ctx, state, allow_loop=False)
        await self._send_if_unsilenced(ctx, state, f"⏭️ Skipped directly to song **#{pos}**.")


    @commands.command(name="pause", help="Pauses the current song.")
    async def pause(self, ctx, *, expected_state=None, expected_message=None):
        state = expected_state or self._get_or_create_state(ctx.guild.id)
        status = None
        async with state.play_lock:
            if (
                expected_state is not None
                and not self._now_playing_is_live(ctx.guild.id, state, expected_message)
            ):
                return False
            if state.vc and state.vc.is_playing():
                state.is_playing, state.is_paused = False, True
                state.vc.pause()
                status = "Paused the current song."
            elif state.vc and state.vc.is_paused():
                state.is_paused, state.is_playing = False, True
                state.vc.resume()
                status = "Resumed the current song."
            else:
                await self._send_error(ctx, "No song is currently playing to pause.")
        if status:
            await self._send_if_unsilenced(ctx, state, status)
        return True


    @commands.command(name="resume", help="Resumes the current song.")
    async def resume(self, ctx, *, expected_state=None, expected_message=None):
        state = expected_state or self._get_or_create_state(ctx.guild.id)
        status = None
        async with state.play_lock:
            if (
                expected_state is not None
                and not self._now_playing_is_live(ctx.guild.id, state, expected_message)
            ):
                return False
            if state.vc and state.vc.is_paused():
                state.is_paused, state.is_playing = False, True
                state.vc.resume()
                status = "Resumed the current song."
            else:
                await self._send_error(ctx, "No song is currently paused to resume.")
        if status:
            await self._send_if_unsilenced(ctx, state, status)
        return True


    @commands.command(name="skip", aliases=["s"], help="Skips the current song.")
    async def skip(self, ctx, *, expected_state=None, expected_message=None):
        state = expected_state or self._get_or_create_state(ctx.guild.id)
        if state.starting:
            self._cancel_preparing_tasks(state)
        async with state.play_lock, state.transition_lock:
            if (
                expected_state is not None
                and not self._now_playing_is_live(ctx.guild.id, state, expected_message)
            ):
                return False
            if state.starting and state.current:
                state.current = None
                self._advance_search_generation(state)
                self._invalidate_playback(state)
                state.is_playing = False
                state.is_paused = False
                if self._voice_owns_source(state):
                    state.vc.stop()
                await self._play_music_locked(ctx, state, allow_loop=False)
                await self._send_if_unsilenced(ctx, state, "Skipped the current song.")
                return True
            if not state.current or not self._voice_owns_source(state):
                await self._send_error(ctx, "No song is currently playing or paused to skip.")
                return True
            interrupted = state.current
            state.current = None
            self._advance_search_generation(state)
            self._remember_song(state, interrupted)
            self._invalidate_playback(state)
            state.is_playing = False
            state.is_paused = False
            state.vc.stop()
            await self._play_music_locked(ctx, state, allow_loop=False)
        await self._send_if_unsilenced(ctx, state, "Skipped the current song.")
        return True


    @commands.command(name="last", aliases=["prev"], help="Plays the previous song.")
    async def last(self, ctx, *, expected_state=None, expected_message=None):
        state = expected_state or self._get_or_create_state(ctx.guild.id)
        if state.starting:
            self._cancel_preparing_tasks(state)
        async with state.play_lock, state.transition_lock:
            if (
                expected_state is not None
                and not self._now_playing_is_live(ctx.guild.id, state, expected_message)
            ):
                return False
            if not state.song_history:
                await self._send_error(ctx, "There is no song history to play from.")
                return True

            interrupted_song = state.current
            last_song = state.song_history.pop(0)
            state.current = None
            self._advance_search_generation(state)
            self._invalidate_playback(state)
            state.is_playing = False
            state.is_paused = False
            if self._voice_owns_source(state):
                state.vc.stop()

            songs_to_front = [last_song]
            if interrupted_song:
                songs_to_front.append(interrupted_song)
            state.music_queue[0:0] = songs_to_front
            await self._play_music_locked(ctx, state, allow_loop=False)
        await self._send_if_unsilenced(ctx, state, "Played the previous song.")
        return True


    @commands.command(name="current", aliases=["song","now"], help="Displays the current playing song")
    async def current_song(self, ctx):
        state = self._get_or_create_state(ctx.guild.id)
        async with state.play_lock, state.transition_lock:
            song = state.current
            if song:
                await self.send_music_embed(
                    ctx,
                    song,
                    "<:baba:1422080743886291025>",
                    state=state,
                )
                return
        await self._send_error(ctx, "Nothing is playing <:baba:1422080743886291025>")


    @commands.command(name="queue", aliases=["q","ls"], help="Displays the current songs in queue")
    async def queue(self, ctx):
        state = self._get_or_create_state(ctx.guild.id)
        if not state.music_queue:
            await self._send_error(ctx, "No music in queue <:baba:1422080743886291025>")
            return
        await ctx.send(embed=self._build_queue_embed(state))


    @commands.command(name="clear", aliases=["c", "bin"], help="Clears the queue and history.")
    async def clear(self, ctx):
        state = self._get_or_create_state(ctx.guild.id)
        self._cancel_preparing_tasks(state)
        pending_search_message = None
        async with state.play_lock, state.transition_lock:
            self._cancel_idle_timer(state)
            state.music_queue.clear()
            state.song_history.clear()
            state.current = None
            _, pending_search_message = self._advance_search_generation(state)
            self._invalidate_playback(state)
            state.is_playing = False
            state.is_paused = False
            if self._voice_owns_source(state):
                state.vc.stop()
            now_playing_message = state.now_playing_message
            state.now_playing_message = None
        if state.vc and state.vc.is_connected():
            self._start_idle_timer(ctx)
        if now_playing_message:
            with contextlib.suppress(Exception):
                await now_playing_message.delete()
        await self._expire_search_message(pending_search_message)
        await self._send_if_unsilenced(ctx, state, "Queue cleared <:baba:1422080743886291025>")


    async def _leave_guild(self, ctx):
        guild_id = ctx.guild.id
        state = self.guild_states.get(guild_id)
        if state is None:
            return
        self._cancel_preparing_tasks(state)
        pending_search_message = None
        async with state.play_lock, state.transition_lock:
            self._cancel_idle_timer(state)
            state.music_queue.clear()
            state.song_history.clear()
            state.current = None
            _, pending_search_message = self._advance_search_generation(state)
            self._invalidate_playback(state)
            state.is_playing = False
            state.is_paused = False
            voice_client = state.vc or discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
            if self._voice_owns_source(state):
                state.vc.stop()
            state.vc = None
            now_playing_message = state.now_playing_message
            state.now_playing_message = None
            if self.guild_states.get(guild_id) is state:
                del self.guild_states[guild_id]

        if voice_client and voice_client.is_connected():
            try:
                await voice_client.disconnect(force=True)
            except Exception:
                logger.exception("Error disconnecting voice client guild_id=%s", guild_id)
        if now_playing_message:
            with contextlib.suppress(Exception):
                await now_playing_message.delete()
        await self._expire_search_message(pending_search_message)

    @commands.command(name="leave", aliases=["disconnect", "l", "d","stop","bye"], help="Disconnects the bot and clears the queue.")
    async def leave(self, ctx):
        state = self.guild_states.get(ctx.guild.id)
        if state is None:
            await self._send_error(ctx, "Baba is not connected to a voice channel.")
            return
        await self._leave_guild(ctx)
        await self._send_if_unsilenced(ctx, state, "Left the voice channel.")


    def _resolve_library_file(self, source):
        if not isinstance(source, str) or source.startswith(("http://", "https://")):
            return None
        library_root = os.path.realpath(os.path.abspath(self.music_folder))
        candidate = os.path.realpath(os.path.abspath(source))
        try:
            inside_library = os.path.normcase(os.path.commonpath([library_root, candidate])) == os.path.normcase(library_root)
        except ValueError:
            return None
        if not inside_library or not os.path.isfile(candidate):
            return None
        return candidate

    async def _delete_library_file(self, path):
        last_error = None
        for attempt in range(3):
            try:
                await asyncio.get_running_loop().run_in_executor(None, os.remove, path)
                return None
            except PermissionError as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.05)
            except Exception as exc:
                return exc
        return last_error

    @commands.command(name="remove", aliases=["rm"], help="Remove last song from queue or current song if 'current' is specified")
    async def remove(self, ctx, *args, expected_state=None, expected_message=None):
        state = expected_state or self._get_or_create_state(ctx.guild.id)
        if args and args[0].lower() == "current":
            if state.starting:
                self._cancel_preparing_tasks(state)
            async with state.play_lock, state.transition_lock:
                if (
                    expected_state is not None
                    and not self._now_playing_is_live(ctx.guild.id, state, expected_message)
                ):
                    return False
                if not state.current:
                    await self._send_error(ctx, "```There's no song playing to remove.```")
                    return True

                removed_song = state.current
                current_title = removed_song["title"]
                library_path = self._resolve_library_file(removed_song.get("source"))

                state.current = None
                self._advance_search_generation(state)
                self._invalidate_playback(state)
                state.is_playing = False
                state.is_paused = False
                if self._voice_owns_source(state):
                    state.vc.stop()

                deletion_error = await self._delete_library_file(library_path) if library_path else None
                if deletion_error is not None:
                    await self._send_error(ctx, f"```Failed to remove '{current_title}': {deletion_error}```")
                elif library_path:
                    await self._send_if_unsilenced(ctx, state, f"```'{current_title}' removed```")
                else:
                    await self._send_if_unsilenced(
                        ctx,
                        state,
                        f"```'{current_title}' removed from playback (no local library file)```",
                    )

                now_playing_message = state.now_playing_message
                state.now_playing_message = None
                if now_playing_message:
                    with contextlib.suppress(Exception):
                        await now_playing_message.delete()

                # Remove Current is also an intentional queue transition.  The
                # invalidated stop callback cannot race this explicit advance.
                await self._play_music_locked(ctx, state, allow_loop=False)
            return True
        else:
            async with state.play_lock, state.transition_lock:
                if (
                    expected_state is not None
                    and not self._now_playing_is_live(ctx.guild.id, state, expected_message)
                ):
                    return False
                if state.music_queue:
                    removed_song = state.music_queue.pop()
                    await self._send_if_unsilenced(ctx, state, f"```'{removed_song['title']}' removed```")
                else:
                    await self._send_error(ctx, "```No songs in the queue to remove.```")
            return True

    async def _disconnect_cleanup(self, voice_client, now_playing_message=None):
        if now_playing_message:
            with contextlib.suppress(Exception):
                await now_playing_message.delete()
        if voice_client and voice_client.is_connected():
            with contextlib.suppress(Exception):
                await voice_client.disconnect(force=True)

    def _schedule_cleanup(self, voice_client, now_playing_message=None):
        coroutine = self._disconnect_cleanup(voice_client, now_playing_message)
        loop = getattr(self.bot, "loop", None)
        if loop is None or loop.is_closed():
            coroutine.close()
            return
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is loop:
            loop.create_task(coroutine)
        else:
            asyncio.run_coroutine_threadsafe(coroutine, loop)

    def cog_unload(self):
        self._unloading = True
        states = list(self.guild_states.values())
        self.guild_states.clear()
        for state in states:
            self._cancel_preparing_tasks(state)
            self._cancel_idle_timer(state)
            state.music_queue.clear()
            state.song_history.clear()
            state.current = None
            state.pending_search_message = None
            self._invalidate_playback(state)
            state.is_playing = False
            state.is_paused = False
            voice_client = state.vc
            if self._voice_owns_source(state):
                state.vc.stop()
            state.vc = None
            now_playing_message = state.now_playing_message
            state.now_playing_message = None
            self._schedule_cleanup(voice_client, now_playing_message)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if before.channel is not None and (after.channel != before.channel):
            channel = before.channel
            guild = channel.guild
            vc = guild.voice_client
            if vc and vc.channel == channel:
                humans = [m for m in channel.members if not m.bot]
                if not humans:
                    state = self.guild_states.get(guild.id)
                    now_playing_message = None
                    if state is not None:
                        self._cancel_preparing_tasks(state)
                        async with state.play_lock, state.transition_lock:
                            self._cancel_idle_timer(state)
                            state.music_queue.clear()
                            state.song_history.clear()
                            state.current = None
                            state.pending_search_message = None
                            self._invalidate_playback(state)
                            state.is_playing = False
                            state.is_paused = False
                            if self._voice_owns_source(state):
                                state.vc.stop()
                            state.vc = None
                            now_playing_message = state.now_playing_message
                            state.now_playing_message = None
                            if self.guild_states.get(guild.id) is state:
                                del self.guild_states[guild.id]
                    await self._disconnect_cleanup(vc, now_playing_message)

async def setup(bot):
    await bot.add_cog(music_cog(bot))
    print('Music loaded!')
