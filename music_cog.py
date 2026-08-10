import contextlib
import discord
import os
import random
import asyncio
from discord.ext import commands
from discord.ui import Button, View
from discord import Embed, ButtonStyle
from typing import Optional
from yt_dlp import YoutubeDL
import psutil
import subprocess

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
        self.volume = 0.25  # Default volume 25%
        self.loop_mode = "off"  # "off", "single", "queue"
        self.idle_task = None  # Auto-disconnect timer

class MusicControls(View):
    def __init__(self, music_cog_instance, ctx):
        super().__init__(timeout=None)
        self.music_cog = music_cog_instance
        self.ctx = ctx
        self.pause_button = Button(label="Pause", style=ButtonStyle.primary, custom_id="pause_button")
        self.skip_button = Button(label="Skip", style=ButtonStyle.secondary, custom_id="skip_button")
        self.last_button = Button(label="Last", style=ButtonStyle.secondary, custom_id="last_button")
        self.loop_button = Button(label="Loop: Off", style=ButtonStyle.secondary, custom_id="loop_button")
        self.queue_button = Button(label="Queue", style=ButtonStyle.secondary, custom_id="queue_button")
        self.remove_button = Button(label="Remove Current", style=ButtonStyle.danger, custom_id="remove_current_button")

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

    def update_button_states(self):
        guild_id = self.ctx.guild.id
        state = self.music_cog._get_or_create_state(guild_id)
        if state.is_paused:
            self.pause_button.label = "Resume"
            self.pause_button.style = ButtonStyle.success
        else:
            self.pause_button.label = "Pause"
            self.pause_button.style = ButtonStyle.primary

        if state.loop_mode == "single":
            self.loop_button.label = "Loop: Single 🔂"
            self.loop_button.style = ButtonStyle.success
        elif state.loop_mode == "queue":
            self.loop_button.label = "Loop: Queue 🔁"
            self.loop_button.style = ButtonStyle.success
        else:
            self.loop_button.label = "Loop: Off"
            self.loop_button.style = ButtonStyle.secondary

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.voice:
            await interaction.response.send_message("You must be in a voice channel to use music controls <:baba:1422080743886291025>", ephemeral=True)
            return False
        
        state = self.music_cog._get_or_create_state(interaction.guild.id)
        if state.vc and interaction.user.voice.channel == state.vc.channel:
            return True
        else:
            await interaction.response.send_message("You must be in the same voice channel as baba <:baba:1422080743886291025>", ephemeral=True)
            return False
            
    async def pause_callback(self, interaction: discord.Interaction):
        state = self.music_cog._get_or_create_state(interaction.guild.id)
        if state.is_playing:
            await self.music_cog.pause(self.ctx)
        elif state.is_paused:
            await self.music_cog.resume(self.ctx)
        self.update_button_states()
        await interaction.response.edit_message(view=self)

    async def skip_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.music_cog.skip(self.ctx)

    async def loop_callback(self, interaction: discord.Interaction):
        state = self.music_cog._get_or_create_state(interaction.guild.id)
        if state.loop_mode == "off":
            state.loop_mode = "single"
        elif state.loop_mode == "single":
            state.loop_mode = "queue"
        else:
            state.loop_mode = "off"
        self.update_button_states()
        await interaction.response.edit_message(view=self)

    async def queue_callback(self, interaction: discord.Interaction):
        state = self.music_cog._get_or_create_state(interaction.guild.id)
        await interaction.response.defer(ephemeral=True)
        if not state.music_queue:
            await interaction.followup.send("No music in queue <:baba:1422080743886291025>", ephemeral=True)
        else:
            retval = ""
            for i, song in enumerate(state.music_queue[:10]):
                duration_str = f" `[{song.get('duration_str', '?')}]`" if song.get('duration_str') else ""
                retval += f"#{i + 1} - {song['title']}{duration_str}\n"
            loop_str = f" | Loop: {state.loop_mode.capitalize()}" if state.loop_mode != "off" else ""
            embed = Embed(title="Music Queue <:baba:1422080743886291025>", description=f"```\n{retval}\nTotal songs: {len(state.music_queue)}{loop_str}\n```", color=discord.Color.gold())
            await interaction.followup.send(embed=embed, ephemeral=True)

    async def last_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.music_cog.last(self.ctx)
        
    async def remove_current_callback(self, interaction: discord.Interaction):
        state = self.music_cog._get_or_create_state(interaction.guild.id)
        if state.current:
            await interaction.response.defer()
            await self.music_cog.remove(self.ctx, "current")
        else:
            await interaction.response.send_message("No song is currently playing to remove.", ephemeral=True)


class YouTubeSearchView(View):
    """Presents top 3 YouTube results as buttons for the user to pick from."""
    def __init__(self, music_cog_instance, ctx, results: list, play_first: bool = False):
        super().__init__(timeout=30)
        self.music_cog = music_cog_instance
        self.ctx = ctx
        self.results = results
        self.play_first = play_first
        self.chosen = False

        for i, result in enumerate(results):
            label = f"{i + 1}. {result['title'][:50]}"
            btn = Button(label=label, style=ButtonStyle.secondary, custom_id=f"yt_search_{i}")
            btn.callback = self._make_callback(i)
            self.add_item(btn)

        cancel_btn = Button(label="Cancel", style=ButtonStyle.danger, custom_id="yt_search_cancel")
        cancel_btn.callback = self.cancel_callback
        self.add_item(cancel_btn)

    def _make_callback(self, index: int):
        async def callback(interaction: discord.Interaction):
            if interaction.user != self.ctx.author:
                await interaction.response.send_message("Only the person who searched can pick a song.", ephemeral=True)
                return
            self.chosen = True
            self.stop()
            song = self.results[index]
            state = self.music_cog._get_or_create_state(self.ctx.guild.id)
            if self.play_first:
                state.music_queue.insert(0, song)
            else:
                state.music_queue.append(song)
            embed = Embed(
                description=f"**#{len(state.music_queue)} - '{song['title']}'** added to the queue.",
                color=discord.Color.green()
            )
            await interaction.response.edit_message(embed=embed, view=None)
            if not state.is_playing:
                await self.music_cog.play_music(self.ctx)
        return callback

    async def cancel_callback(self, interaction: discord.Interaction):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message("Only the person who searched can cancel.", ephemeral=True)
            return
        self.chosen = True
        self.stop()
        await interaction.response.edit_message(
            embed=Embed(description="Search cancelled.", color=discord.Color.red()), view=None
        )

    async def on_timeout(self):
        if not self.chosen:
            try:
                song = self.results[0]
                state = self.music_cog._get_or_create_state(self.ctx.guild.id)
                if self.play_first:
                    state.music_queue.insert(0, song)
                else:
                    state.music_queue.append(song)
                await self.ctx.send(f"⏱️ No selection — auto-adding **'{song['title']}'**")
                if not state.is_playing:
                    await self.music_cog.play_music(self.ctx)
            except Exception:
                pass


class music_cog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.music_folder = "C:/Users/manza/Music"
        self.guild_states = {} # Dictionary to hold the state for each guild
        self.YDL_OPTIONS = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "skip_download": False,
            "outtmpl": os.path.join(self.music_folder, '%(title)s.%(ext)s'),
            "extractor_args": {"youtube": ["player_client=ios,android_vr,mweb,web"]}
        }
        self.FFMPEG_OPTIONS = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn'
        }
        self.ytdl = YoutubeDL(self.YDL_OPTIONS)

    def _get_or_create_state(self, guild_id: int) -> GuildState:
        if guild_id not in self.guild_states:
            self.guild_states[guild_id] = GuildState()
        return self.guild_states[guild_id]

    def _cancel_idle_timer(self, state: GuildState):
        if state.idle_task and not state.idle_task.done():
            state.idle_task.cancel()
            state.idle_task = None

    def _start_idle_timer(self, ctx):
        state = self._get_or_create_state(ctx.guild.id)
        self._cancel_idle_timer(state)

        async def _idle_disconnect():
            await asyncio.sleep(180) # 3 minutes idle timeout
            st = self._get_or_create_state(ctx.guild.id)
            if not st.is_playing and st.vc and st.vc.is_connected():
                with contextlib.suppress(Exception):
                    await ctx.send("💤 Left voice channel due to inactivity <:baba:1422080743886291025>")
                await self.leave(ctx)

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
                    
                    mins, secs = divmod(int(duration) if duration else 0, 60)
                    duration_str = f"{mins}:{secs:02d}" if duration else "?"

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
                url = entry.get("url") or entry.get("webpage_url") or f"https://www.youtube.com/watch?v={entry.get('id')}"
                title = entry.get("title", "Unknown title")
                duration = entry.get("duration", 0)
                mins, secs = divmod(int(duration) if duration else 0, 60)
                duration_str = f"{mins}:{secs:02d}" if duration else "?"
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
                        mins, secs = divmod(int(duration) if duration else 0, 60)
                        duration_str = f"{mins}:{secs:02d}" if duration else "?"
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

    def get_random_song(self):
        files = [
            f for f in os.listdir(self.music_folder)
            if os.path.isfile(os.path.join(self.music_folder, f)) and not f.lower().endswith('desktop.ini')
        ]
        if not files:
            return None
        return os.path.join(self.music_folder, random.choice(files))

    async def send_music_embed(self, ctx, song, message_type="<:baba:1422080743886291025>"):
        state = self._get_or_create_state(ctx.guild.id)
        song_title = song.get("title", "Unknown Title") if isinstance(song, dict) else str(song)
        
        embed = Embed(title=f"{message_type} Now Playing 🎶", description=f"**[{song_title}]({song.get('source', '')})**" if isinstance(song, dict) and song.get('source', '').startswith('http') else f"**{song_title}**", color=discord.Color.blue())
        
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

        view = MusicControls(self, ctx)

        if state.now_playing_message:
            with contextlib.suppress(Exception):
                old = await ctx.channel.fetch_message(state.now_playing_message.id)
                if old.author == ctx.guild.me:
                    await old.delete()
        
        state.now_playing_message = await ctx.send(embed=embed, view=view)


    async def play_music(self, ctx):
        state = self._get_or_create_state(ctx.guild.id)
        self._cancel_idle_timer(state)

        # Loop logic handling
        if state.current and state.loop_mode == "single":
            state.music_queue.insert(0, state.current)
        elif state.current and state.loop_mode == "queue":
            state.music_queue.append(state.current)

        if state.current and state.loop_mode == "off":
            state.song_history.insert(0, state.current)
            if len(state.song_history) > 50: state.song_history.pop()

        if not state.music_queue:
            state.is_playing = False
            state.current = None
            self._start_idle_timer(ctx)
            return

        song = state.music_queue.pop(0)
        print(f"[play_music] Playing: {song}")
        state.current = song
        await self.send_music_embed(ctx, song, "<:baba:1422080743886291025>")
        state.is_playing = True
        state.is_paused = False

        loop = asyncio.get_running_loop()

        async def _after():
            st = self._get_or_create_state(ctx.guild.id)
            if st.music_queue or st.loop_mode != "off":
                await self.play_music(ctx)
            else:
                st.is_playing = False
                st.current = None
                if st.now_playing_message:
                    with contextlib.suppress(Exception):
                        await st.now_playing_message.edit(embed=Embed(description="No songs left in queue <:baba:1422080743886291025>", color=discord.Color.red()), view=None)
                self._start_idle_timer(ctx)

        after_lambda = lambda _: asyncio.run_coroutine_threadsafe(_after(), self.bot.loop)

        try:
            source_to_play = song['source']

            # If YouTube URL, re-extract fresh stream URL or download if < 6 mins
            if not os.path.isabs(source_to_play) and not (len(source_to_play) > 1 and source_to_play[1] == ':'):
                def _get_stream_info():
                    with YoutubeDL(self.YDL_OPTIONS) as ydl:
                        return ydl.extract_info(source_to_play, download=False)

                info = await loop.run_in_executor(None, _get_stream_info)
                if info:
                    duration = info.get('duration', 0)
                    song['title'] = info.get('title', song.get('title'))
                    song['thumbnail'] = info.get('thumbnail', song.get('thumbnail'))
                    song['uploader'] = info.get('uploader') or info.get('channel') or song.get('uploader', '')
                    song['duration'] = duration
                    mins, secs = divmod(int(duration) if duration else 0, 60)
                    song['duration_str'] = f"{mins}:{secs:02d}" if duration else "?"

                    if 0 < duration < 360:
                        await ctx.send(f"```Downloading '{song['title']}'...```", delete_after=8)
                        def _download():
                            ydl_opts_download = self.YDL_OPTIONS.copy()
                            ydl_opts_download['outtmpl'] = os.path.join(self.music_folder, '%(title)s.%(ext)s')
                            with YoutubeDL(ydl_opts_download) as ydl:
                                dl_info = ydl.extract_info(source_to_play, download=True)
                                return ydl.prepare_filename(dl_info)
                        
                        downloaded_path = await loop.run_in_executor(None, _download)
                        if os.path.exists(downloaded_path):
                            source_to_play = downloaded_path
                        else:
                            source_to_play = info.get('url')
                    else:
                        source_to_play = info.get('url')

            raw_audio = discord.FFmpegPCMAudio(source_to_play, executable="ffmpeg.exe", **self.FFMPEG_OPTIONS)
            volume_audio = discord.PCMVolumeTransformer(raw_audio, volume=state.volume)
            state.vc.play(volume_audio, after=after_lambda)

        except Exception as e:
            print(f"[play_music] Error: {e}")
            await ctx.send(f"Error playing **{song.get('title', 'unknown')}**: {e}")
            await self.play_music(ctx)


    @commands.command(name="play", aliases=["p", "playing","sing","PLAY", "playfirst"], help="Plays a song from YouTube or local files.")
    async def play(self, ctx, *, query: str = ""):
        state = self._get_or_create_state(ctx.guild.id)
        self._cancel_idle_timer(state)

        if not ctx.author.voice:
            await ctx.send("You need to be in a voice channel first <:baba:1422080743886291025>")
            return

        if state.vc is None or not state.vc.is_connected():
            try:
                existing_vc = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
                if existing_vc:
                    await existing_vc.disconnect(force=True)
                    await asyncio.sleep(1)

                state.vc = await ctx.author.voice.channel.connect()
            except Exception as e:
                print(f"Could not connect: {e}")
                state.is_playing = False
                return

        if state.is_paused and not query:
            await self.resume(ctx)
            return

        if query.isdigit():
            num_songs = min(max(int(query), 1), 100)
            for _ in range(num_songs):
                song_path = self.get_random_song()
                if song_path:
                    base = os.path.basename(song_path).rsplit('.', 1)[0]
                    title = ' '.join(base.split()[:-1]) if len(base.split()) > 1 and base.split()[-1].lower() in ['[', ']'] else base
                    song = {'source': song_path, 'title': title}
                    state.music_queue.append(song)
            await ctx.send(f"**{num_songs} random songs** added to the queue.")
            if not state.is_playing:
                await self.play_music(ctx)
            return

        play_first = "playfirst" in ctx.message.content.lower()

        if not query:
            song_path = self.get_random_song()
            if song_path:
                title = os.path.basename(song_path).rsplit('.', 1)[0]
                title = ' '.join(title.split()[:-1]) if len(title.split()) > 1 and title.split()[-1].lower() in ['[', ']'] else title
                song = {'source': song_path, 'title': title}
                if play_first:
                    state.music_queue.insert(0, song)
                else:
                    state.music_queue.append(song)
                await ctx.send(f"**'{title}'** added to the queue.")
            else:
                await ctx.send("No songs found in the music folder.")
        elif 'playlist?list=' in query:
            songs_added = 0
            msg = await ctx.send("```Loading playlist...```")
            try:
                videos = await self.fetch_playlist_videos(query)
                if not videos:
                    await msg.edit(content="```Couldn't read that playlist (no entries found).```")
                    return

                for video in videos:
                    song = {"source": video['source'], "title": video['title'], "duration": video.get('duration'), "duration_str": video.get('duration_str')}
                    if play_first:
                        state.music_queue.insert(0, song)
                    else:
                        state.music_queue.append(song)
                    songs_added += 1

                await msg.edit(content=f"```{songs_added} songs added to the queue.```")
            except Exception as e:
                await msg.edit(content=f"```Error loading playlist: {e}```")
        elif query.startswith(("http://", "https://")) or "youtube.com" in query or "youtu.be" in query:
            try:
                song_info = await self.search_yt(query)
                if not song_info:
                    await ctx.send("```Couldn't find that YouTube video.```")
                    return
                song = song_info
                if play_first:
                    state.music_queue.insert(0, song)
                else:
                    state.music_queue.append(song)
                await ctx.send(f"**'{song_info['title']}'** added to the queue.")
            except Exception as e:
                await ctx.send(f"```Error processing YouTube link: {e}```")
        else:
            matched_files = []
            all_flag = "all" in query.lower()
            if all_flag: query_for_search = query.lower().replace("all", "").strip()
            else: query_for_search = query.lower().strip()
            
            query_words = query_for_search.lower().split()
            for f in os.listdir(self.music_folder):
                if os.path.isfile(os.path.join(self.music_folder, f)) and all(word in f.lower() for word in query_words):
                    matched_files.append(f)

            if matched_files:
                files_to_add = matched_files if all_flag else [random.choice(matched_files)]
                for f in files_to_add:
                    song_path = os.path.join(self.music_folder, f)
                    base = os.path.basename(f).rsplit('.', 1)[0]
                    song_title = ' '.join(base.split()[:-1]) if len(base.split()) > 1 and base.split()[-1].lower() in ['[', ']'] else base

                    song = {"source": song_path, "title": song_title}
                    if play_first: state.music_queue.insert(0, song)
                    else: state.music_queue.append(song)
                await ctx.send(f"**'{files_to_add[0] if len(files_to_add) == 1 else f'{len(files_to_add)} songs'}'** added to the queue.")
            else:
                msg = await ctx.send("```Searching YouTube...```")
                results = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: self.search_yt_multiple(query, 3)
                )
                with contextlib.suppress(Exception):
                    await msg.delete()
                if not results:
                    await ctx.send("Could not find any results on YouTube.")
                else:
                    embed = Embed(
                        title="🔎 YouTube Search Results",
                        description="\n".join(
                            f"**{i+1}.** {r['title']} `[{r['duration_str']}]`"
                            for i, r in enumerate(results)
                        ),
                        color=discord.Color.red()
                    )
                    embed.set_footer(text="Pick a song below • auto-selects #1 after 30s")
                    view = YouTubeSearchView(self, ctx, results, play_first)
                    await ctx.send(embed=embed, view=view)
                    return

        if not state.is_playing:
            await self.play_music(ctx)


    @commands.command(name="volume", aliases=["v", "vol"], help="Sets or views music volume (0-200%).")
    async def volume(self, ctx, vol: Optional[int] = None):
        state = self._get_or_create_state(ctx.guild.id)
        if vol is None:
            await ctx.send(f"🔊 Current volume: **{int(state.volume * 100)}%**")
            return

        vol = min(max(vol, 0), 200)
        state.volume = vol / 100.0

        if state.vc and state.vc.source and hasattr(state.vc.source, "volume"):
            state.vc.source.volume = state.volume

        await ctx.send(f"🔊 Volume set to **{vol}%**")


    @commands.command(name="shuffle", aliases=["sh"], help="Shuffles the current music queue.")
    async def shuffle(self, ctx):
        state = self._get_or_create_state(ctx.guild.id)
        if not state.music_queue:
            await ctx.send("Queue is empty, nothing to shuffle.")
            return
        random.shuffle(state.music_queue)
        await ctx.send(f"🔀 Shuffled **{len(state.music_queue)}** songs in the queue!")


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
        await ctx.send(f"🔁 Loop mode set to: **{mode_icons[state.loop_mode]}**")


    @commands.command(name="move", aliases=["mv"], help="Moves a song from one position to another in queue.")
    async def move(self, ctx, from_pos: int, to_pos: int):
        state = self._get_or_create_state(ctx.guild.id)
        if not state.music_queue:
            await ctx.send("Queue is empty.")
            return

        if 1 <= from_pos <= len(state.music_queue) and 1 <= to_pos <= len(state.music_queue):
            song = state.music_queue.pop(from_pos - 1)
            state.music_queue.insert(to_pos - 1, song)
            await ctx.send(f"🚚 Moved **'{song['title']}'** to position **#{to_pos}**.")
        else:
            await ctx.send(f"Invalid positions. Queue size is **{len(state.music_queue)}**.")


    @commands.command(name="skipto", aliases=["st"], help="Skips directly to a specific song number in queue.")
    async def skipto(self, ctx, pos: int):
        state = self._get_or_create_state(ctx.guild.id)
        if not state.music_queue:
            await ctx.send("Queue is empty.")
            return

        if 1 <= pos <= len(state.music_queue):
            state.music_queue = state.music_queue[pos - 1:]
            if state.vc and (state.vc.is_playing() or state.vc.is_paused()):
                state.vc.stop()
            else:
                await self.play_music(ctx)
            await ctx.send(f"⏭️ Skipped directly to song **#{pos}**.")
        else:
            await ctx.send(f"Invalid song position. Queue size is **{len(state.music_queue)}**.")


    @commands.command(name="pause", help="Pauses the current song.")
    async def pause(self, ctx):
        state = self._get_or_create_state(ctx.guild.id)
        if state.vc and state.vc.is_playing():
            state.is_playing, state.is_paused = False, True
            state.vc.pause()
        elif state.vc and state.vc.is_paused():
             await self.resume(ctx)
        else:
            await ctx.send("No song is currently playing to pause.")
    

    @commands.command(name="resume", help="Resumes the current song.")
    async def resume(self, ctx):
        state = self._get_or_create_state(ctx.guild.id)
        if state.vc and state.vc.is_paused():
            state.is_paused, state.is_playing = False, True
            state.vc.resume()
        else:
            await ctx.send("No song is currently paused to resume.")


    @commands.command(name="skip", aliases=["s"], help="Skips the current song.")
    async def skip(self, ctx):
        state = self._get_or_create_state(ctx.guild.id)
        if state.vc and (state.vc.is_playing() or state.vc.is_paused()):
            state.vc.stop()
        else:
            await ctx.send("No song is currently playing or paused to skip.")


    @commands.command(name="last", aliases=["prev"], help="Plays the previous song.")
    async def last(self, ctx):
        state = self._get_or_create_state(ctx.guild.id)
        if not state.song_history:
            await ctx.send("There is no song history to play from.")
            return

        interrupted_song = state.current
        last_song = state.song_history.pop(0)
        state.current = None

        if interrupted_song:
            state.music_queue.insert(0, interrupted_song)
        state.music_queue.insert(0, last_song)

        if state.vc and state.vc.is_playing():
            state.vc.stop()
        elif not state.is_playing:
            await self.play_music(ctx)


    @commands.command(name="current", aliases=["song","now"], help="Displays the current playing song")
    async def current_song(self, ctx):
        state = self._get_or_create_state(ctx.guild.id)
        if state.current:
            await self.send_music_embed(ctx, state.current, "<:baba:1422080743886291025>")
        else:
            await ctx.send(f"Nothing is playing <:baba:1422080743886291025>")


    @commands.command(name="queue", aliases=["q","ls"], help="Displays the current songs in queue")
    async def queue(self, ctx):
        state = self._get_or_create_state(ctx.guild.id)
        if not state.music_queue:
            await ctx.send("No music in queue <:baba:1422080743886291025>")
            return
        retval = ""
        for i, song in enumerate(state.music_queue[:10]):
            duration_str = f" `[{song.get('duration_str', '?')}]`" if song.get('duration_str') else ""
            retval += f"#{i + 1} - {song['title']}{duration_str}\n"
        loop_str = f" | Loop: {state.loop_mode.capitalize()}" if state.loop_mode != "off" else ""
        embed = Embed(title="Music Queue <:baba:1422080743886291025>", description=f"```\n{retval}\nTotal songs: {len(state.music_queue)}{loop_str}\n```", color=discord.Color.gold())
        await ctx.send(embed=embed)


    @commands.command(name="clear", aliases=["c", "bin"], help="Clears the queue and history.")
    async def clear(self, ctx):
        state = self._get_or_create_state(ctx.guild.id)
        self._cancel_idle_timer(state)
        state.music_queue = []
        state.song_history = []
        if state.vc and state.vc.is_playing():
            state.vc.stop()
        state.is_playing = False
        state.current = None
        if state.now_playing_message:
            with contextlib.suppress(Exception):
                await state.now_playing_message.delete()
            state.now_playing_message = None
        await ctx.send("Queue cleared <:baba:1422080743886291025>")


    @commands.command(name="leave", aliases=["disconnect", "l", "d","stop","bye"], help="Disconnects the bot and clears the queue.")
    async def leave(self, ctx):
        state = self._get_or_create_state(ctx.guild.id)
        self._cancel_idle_timer(state)

        state.music_queue = []
        state.song_history = []
        state.is_playing = False
        state.is_paused = False
        state.current = None
        
        existing_vc = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
        if existing_vc and existing_vc.is_connected():
            try:
                await existing_vc.disconnect(force=True)
            except Exception as e:
                print(f"Error disconnecting existing voice client: {e}")

        state.vc = None

        if state.now_playing_message:
            with contextlib.suppress(Exception):
                await state.now_playing_message.delete()
            state.now_playing_message = None

        if ctx.guild.id in self.guild_states:
            del self.guild_states[ctx.guild.id]


    @commands.command(name="remove", aliases=["rm"], help="Remove last song from queue or current song if 'current' is specified")
    async def remove(self, ctx, *args):
        state = self._get_or_create_state(ctx.guild.id)
        if args and args[0].lower() == "current":
            if state.current:
                current_title = state.current['title']
                
                if state.vc and state.vc.is_playing():
                    state.vc.stop()
                
                state.is_playing = False
                state.is_paused = False
                
                song_source = state.current['source']
                if not song_source.startswith("https://") and os.path.exists(song_source):
                    try:
                        await asyncio.sleep(2)
                        await asyncio.get_running_loop().run_in_executor(None, os.remove, song_source)
                        await ctx.send(f"```'{current_title}' removed```")
                    except Exception as e:
                        await ctx.send(f"```Failed to remove '{current_title}': {e}```")
                else:
                    await ctx.send(f"```'{current_title}' removed```")

                state.current = None
                if state.now_playing_message:
                    with contextlib.suppress(Exception):
                        await state.now_playing_message.delete()
                    state.now_playing_message = None

            else:
                await ctx.send("```There's no song playing to remove.```")
        else:
            if state.music_queue:
                removed_song = state.music_queue.pop()
                await ctx.send(f"```'{removed_song['title']}' removed```")
            else:
                await ctx.send("```No songs in the queue to remove.```")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if before.channel is not None and (after.channel != before.channel):
            channel = before.channel
            guild = channel.guild
            vc = guild.voice_client
            if vc and vc.channel == channel:
                humans = [m for m in channel.members if not m.bot]
                if not humans:
                    state = self._get_or_create_state(guild.id)
                    self._cancel_idle_timer(state)
                    state.music_queue.clear()
                    state.song_history.clear()
                    state.is_playing = False
                    state.is_paused = False
                    state.current = None
                    try:
                        await vc.disconnect(force=True)
                    except Exception:
                        pass
                    if guild.id in self.guild_states:
                        del self.guild_states[guild.id]

async def setup(bot):
    await bot.add_cog(music_cog(bot))
    print('Music loaded!')
