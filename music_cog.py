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

class MusicControls(View):
    def __init__(self, music_cog_instance, ctx):
        super().__init__(timeout=None)
        self.music_cog = music_cog_instance
        self.ctx = ctx
        self.pause_button = Button(label="Pause", style=ButtonStyle.primary, custom_id="pause_button")
        self.skip_button = Button(label="Skip", style=ButtonStyle.secondary, custom_id="skip_button")
        self.last_button = Button(label="Last", style=ButtonStyle.secondary, custom_id="last_button")
        self.queue_button = Button(label="Queue", style=ButtonStyle.secondary, custom_id="queue_button") # Added Queue button
        self.remove_button = Button(label="Remove Current", style=ButtonStyle.danger, custom_id="remove_current_button")

        self.add_item(self.last_button)
        self.add_item(self.pause_button)
        self.add_item(self.skip_button)
        self.add_item(self.queue_button) # Added Queue button
        self.add_item(self.remove_button)

        self.pause_button.callback = self.pause_callback
        self.skip_button.callback = self.skip_callback
        self.last_button.callback = self.last_callback
        self.queue_button.callback = self.queue_callback # Callback for Queue button
        self.remove_button.callback = self.remove_current_callback

        self.update_pause_button_state()

    def update_pause_button_state(self):
        guild_id = self.ctx.guild.id
        state = self.music_cog._get_or_create_state(guild_id)
        if state.is_paused:
            self.pause_button.label = "Resume"
            self.pause_button.style = ButtonStyle.success
        else:
            self.pause_button.label = "Pause"
            self.pause_button.style = ButtonStyle.primary

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
        self.update_pause_button_state()
        await interaction.response.edit_message(view=self) # Update message after pause/resume

    async def skip_callback(self, interaction: discord.Interaction):
        state = self.music_cog._get_or_create_state(interaction.guild.id)
        # Acknowledge the interaction quickly
        await interaction.response.defer()
        # Trigger the skip (this will stop current playback and let play_music handle the next song)
        await self.music_cog.skip(self.ctx)

    async def queue_callback(self, interaction: discord.Interaction):
        state = self.music_cog._get_or_create_state(interaction.guild.id)
        # Defer and reply with the queue as an ephemeral follow-up so the channel isn't spammed
        await interaction.response.defer(ephemeral=True)
        if not state.music_queue:
            await interaction.followup.send("No music in queue <:baba:1422080743886291025>", ephemeral=True)
        else:
            retval = ""
            for i, song in enumerate(state.music_queue[:10]):
                retval += f"#{i + 1} - {song['title']}\n"
            embed = Embed(title="Music Queue <:baba:1422080743886291025>", description=f"```\n{retval}\nTotal songs: {len(state.music_queue)}\n```", color=discord.Color.gold())
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
            song = {"source": self.results[index]["source"], "title": self.results[index]["title"]}
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
                # Auto-pick first result on timeout
                song = {"source": self.results[0]["source"], "title": self.results[0]["title"]}
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
            "quiet": False,
            "skip_download": False,
            "outtmpl": os.path.join(self.music_folder, '%(title)s.%(ext)s'),
            "extractor_args": {"youtube": ["player_client=android,web"]}
        }
        self.FFMPEG_OPTIONS = {'options': '-vn -af volume=0.25 '}
        self.ytdl = YoutubeDL(self.YDL_OPTIONS)

    def _get_or_create_state(self, guild_id: int) -> GuildState:
        if guild_id not in self.guild_states:
            self.guild_states[guild_id] = GuildState()
        return self.guild_states[guild_id]

    def search_yt(self, item: str):
        try:
            if item.startswith(("http://", "https://")):
                info = self.ytdl.extract_info(item, download=False)
                if info is None: return None
                if "entries" in info and info["entries"]:
                    info = info["entries"][0]
                url = info.get("webpage_url") or f"https://www.youtube.com/watch?v={info.get('id')}"
                title = info.get("title", "Unknown title")
                return {"source": url, "title": title}
            search_info = self.ytdl.extract_info(f"ytsearch1:{item}", download=False)
            if not search_info or not search_info.get("entries"): return None
            entry = search_info["entries"][0]
            url = entry.get("webpage_url") or f"https://www.youtube.com/watch?v={entry.get('id')}"
            title = entry.get("title", "Unknown title")
            return {"source": url, "title": title}

        except Exception as e:
            print(f"[search_yt] Error: {e}")
            return None

    def search_yt_multiple(self, item: str, count: int = 3):
        """Returns top `count` YouTube search results as a list of {"source", "title", "duration"} dicts."""
        try:
            # 建立一個專用的搜尋選項，開啟 extract_flat，讓它不要去爬取每個影片的完整網頁
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
                # 注意：使用 extract_flat 後，搜尋結果可能拿不到精準的 duration，我們會給個預設值處理
                duration = entry.get("duration", 0)
                mins, secs = divmod(int(duration) if duration else 0, 60)
                duration_str = f"{mins}:{secs:02d}" if duration else "?"
                results.append({"source": url, "title": title, "duration": duration_str})
            return results
        except Exception as e:
            print(f"[search_yt_multiple] Error: {e}")
            return []

    def fetch_playlist_videos(self, playlist_url: str):
        """
        Extract playlist entries and return a list of {"source", "title"} dicts.
        This is synchronous because play() calls it directly; it uses the same
        YoutubeDL instance as search_yt.
        """
        try:
            info = self.ytdl.extract_info(playlist_url, download=False)
            if not info:
                return []

            entries = info.get("entries") or []
            videos = []
            for entry in entries:
                if not entry:
                    continue
                # Some entries are partial (extract_flat) and may contain 'id' instead of 'webpage_url'
                url = entry.get("webpage_url") or entry.get("url") or (f"https://www.youtube.com/watch?v={entry.get('id')}" if entry.get("id") else None)
                title = entry.get("title") or entry.get("name") or "Unknown title"
                if url:
                    videos.append({"source": url, "title": title})
            return videos
        except Exception as e:
            print(f"[fetch_playlist_videos] Error: {e}")
            return []

    def get_random_song(self):
        files = [
            f for f in os.listdir(self.music_folder)
            if os.path.isfile(os.path.join(self.music_folder, f)) and not f.lower().endswith('desktop.ini')
        ]
        if not files:
            return None
        return os.path.join(self.music_folder, random.choice(files))

    async def send_music_embed(self, ctx, song_title, message_type="<:baba:1422080743886291025>"):
        state = self._get_or_create_state(ctx.guild.id)
        embed = Embed(title=f"{message_type} 🎶", description=f"**{song_title}**", color=discord.Color.blue())
        view = MusicControls(self, ctx)

        #delete the previous message and send a new one
        if state.now_playing_message:
            with contextlib.suppress(Exception):
                old = await ctx.channel.fetch_message(state.now_playing_message.id)
                if old.author == ctx.guild.me:
                    await old.delete()
        state.now_playing_message = await ctx.send(embed=embed, view=view)

            
    async def play_music(self, ctx):
        state = self._get_or_create_state(ctx.guild.id)
        if state.current:
            state.song_history.insert(0, state.current)
            if len(state.song_history) > 50: state.song_history.pop()

        # If there's nothing in the queue, nothing to play
        if not state.music_queue:
            return

        song = state.music_queue.pop(0)
        print(song)
        state.current = song
        await self.send_music_embed(ctx, song['title'], "<:baba:1422080743886291025>")
        state.is_playing = True
        state.is_paused = False

        loop = asyncio.get_running_loop()

        # create an after-handler that only continues playback if there are songs left,
        # otherwise it marks the player as stopped and updates the message
        async def _after():
            st = self._get_or_create_state(ctx.guild.id)
            if st.music_queue:
                await self.play_music(ctx)
            else:
                st.is_playing = False
                st.current = None
                if st.now_playing_message:
                    await st.now_playing_message.edit(embed=Embed(description="No songs left <:baba:1422080743886291025>", color=discord.Color.red()), view=None)

        after_lambda = lambda _: asyncio.run_coroutine_threadsafe(_after(), self.bot.loop)

        try:
            # Source can be a URL or local file path
            source_to_play = song['source']

            if not os.path.isabs(source_to_play) and not source_to_play.startswith(("C:")):
                
                info = await loop.run_in_executor(None, lambda: self.ytdl.extract_info(source_to_play, download=False))
                duration = info.get('duration', 0)
                
                print(f"Song Duration: {duration}")

                if duration > 0 and duration < 360:
                    await ctx.send(f"```Downloading '{info.get('title', 'unknown')}'```", delete_after=10)
                    ydl_opts_download = self.YDL_OPTIONS.copy()
                    ydl_opts_download['outtmpl'] = os.path.join(self.music_folder, '%(title)s.%(ext)s')

                    with YoutubeDL(ydl_opts_download) as ydl:
                        dl_info = await loop.run_in_executor(None, lambda: ydl.extract_info(source_to_play, download=True))
                    # Use the downloaded local file instead of the stream URL
                    downloaded_path = ydl.prepare_filename(dl_info)
                    if os.path.exists(downloaded_path):
                        source_to_play = downloaded_path
                    else:
                        source_to_play = info.get('url')  # fallback to stream
                else:
                    source_to_play = info.get('url')


            # Play the (possibly downloaded) file or remote stream
            state.vc.play(discord.FFmpegPCMAudio(source_to_play, executable="ffmpeg.exe", **self.FFMPEG_OPTIONS), after=after_lambda)

        except Exception as e:
            await ctx.send(f"Error playing **{song.get('title', 'unknown')}**: {e}")


    @commands.command(name="play", aliases=["p", "playing","sing","PLAY", "playfirst"], help="Plays a song from YouTube or local files.")
    async def play(self, ctx, *, query: str = ""):

        state = self._get_or_create_state(ctx.guild.id)

        # Check user is in a voice channel
        if not ctx.author.voice:
            await ctx.send("You need to be in a voice channel first <:baba:1422080743886291025>")
            return

        # Check if bot is in voice channel or not

        # if state.vc is None or not state.vc.is_connected():
        #     try:
        #         # Attempt to disconnect any existing voice client for this guild without trying to join the user's channel
        #         existing_vc = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
        #         if existing_vc and existing_vc.is_connected():
        #             try:
        #                 await existing_vc.disconnect()
        #             except Exception as e:
        #                 print(f"Error disconnecting existing voice client: {e}")

        #         # Also disconnect the stored state.vc if it's connected
        #         if state.vc and getattr(state.vc, "is_connected", lambda: False)():
        #             try:
        #                 await state.vc.disconnect()
        #             except Exception:
        #                 pass
            
        #         state.vc = await ctx.author.voice.channel.connect()
        #     except Exception as e:
        #         print(f"Could not connect: {e}")
        #         state.is_playing = False
        #         return
        # Replace the connection block (lines 376-397) with this:
        if state.vc is None or not state.vc.is_connected():
            try:
                # Clean up any stale voice clients
                existing_vc = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
                if existing_vc:
                    await existing_vc.disconnect(force=True)
                    await asyncio.sleep(1)  # Give Discord time to close the session cleanly

                state.vc = await ctx.author.voice.channel.connect()
            except Exception as e:
                print(f"Could not connect: {e}")
                state.is_playing = False
                return
            
                # If paused, resume instead of adding new song
        if state.is_paused and not query: # Only resume if no new query is given
            await self.resume(ctx)
            return
        
        # If there's only a number after play, get that many random songs
        if query.isdigit():
            num_songs = min(max(int(query), 1), 100) # Limit between 1 and 100
            for _ in range(num_songs):
                song_path = self.get_random_song()
                if song_path:
                    # Remove extension and last word from filename for title
                    base = os.path.basename(song_path).rsplit('.', 1)[0]
                    title = ' '.join(base.split()[:-1]) if len(base.split()) > 1 and base.split()[-1].lower() in ['[', ']'] else base
                    song = {'source': song_path, 'title': title}
                    state.music_queue.append(song)
            await ctx.send(f"**{num_songs} random songs** added to the queue.")
            if not state.is_playing:
                await self.play_music(ctx)
            return
        # Handle "playfirst" logic here
        play_first = "playfirst" in ctx.message.content.lower()

        if not query: # Play a random local song if no query
            song_path = self.get_random_song()
            if song_path:
                title = os.path.basename(song_path).rsplit('.', 1)[0] # Extracts title by removing extension only
                 #remove the last word from the title
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
            await ctx.send("```Loading playlist...```")
            try:
                videos = self.fetch_playlist_videos(query)
                if not videos:
                    await ctx.send("```Couldn't read that playlist (no entries found).```")
                    return

                for video in videos:
                    song = {"source": video['source'], "title": video['title']}
                    if play_first:
                        state.music_queue.insert(0, song)
                    else:
                        state.music_queue.append(song)
                    songs_added += 1

                await ctx.send(f"```{songs_added} songs added to the queue.```")
            except Exception as e:
                await ctx.send(f"```Error loading playlist: {e}```")
        elif query.startswith(("http://", "https://")) or "youtube.com" in query or "youtu.be" in query:
            try:
                song_info = self.search_yt(query)
                if not song_info:
                    await ctx.send("```Couldn't find that YouTube video.```")
                    return
                song = {"source": song_info['source'], "title": song_info['title']}
                if play_first:
                    state.music_queue.insert(0, song)
                else:
                    state.music_queue.append(song)
                await ctx.send(f"**'{song_info['title']}'** added to the queue.")
            except Exception as e:
                await ctx.send(f"```Error processing YouTube link: {e}```")
        else: # Search YouTube or local files
            # Try to play local first (using original play_local logic if you want to keep it)
            # For simplicity, I'm making it search YouTube directly here if no local command
            
            # --- Re-integrated logic from your play_local and play_youtube ---
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
                    song_title = os.path.basename(f).rsplit('.', 1)[0]
                    #remove the last word from the title
                    base = os.path.basename(f).rsplit('.', 1)[0]
                    song_title = ' '.join(base.split()[:-1]) if len(base.split()) > 1 and base.split()[-1].lower() in ['[', ']'] else base

                    song = {"source": song_path, "title": song_title}
                    if play_first: state.music_queue.insert(0, song)
                    else: state.music_queue.append(song)
                await ctx.send(f"**'{files_to_add[0] if len(files_to_add) == 1 else f'{len(files_to_add)} songs'}'** added to the queue.")
            else:
                # Fallback to YouTube search - show top 3 results as buttons
                await ctx.send("```Searching YouTube...```", delete_after=5)
                results = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: self.search_yt_multiple(query, 3)
                )
                if not results:
                    await ctx.send("Could not find any results on YouTube.")
                else:
                    embed = Embed(
                        title="🔎 YouTube Search Results",
                        description="\n".join(
                            f"**{i+1}.** {r['title']} `[{r['duration']}]`"
                            for i, r in enumerate(results)
                        ),
                        color=discord.Color.red()
                    )
                    embed.set_footer(text="Pick a song below • auto-selects #1 after 30s")
                    view = YouTubeSearchView(self, ctx, results, play_first)
                    await ctx.send(embed=embed, view=view)
                    return  # YouTubeSearchView handles queue + playback


        if not state.is_playing:
            await self.play_music(ctx)


    @commands.command(name="pause", help="Pauses the current song.")
    async def pause(self, ctx):
        state = self._get_or_create_state(ctx.guild.id)
        if state.vc and state.vc.is_playing():
            state.is_playing, state.is_paused = False, True
            state.vc.pause()
        elif state.vc and state.vc.is_paused(): # If already paused, resume
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
            state.vc.stop() # The `after` lambda in play_music will handle the next song
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
        state.current = None # CRITICAL: Clear current to prevent double logging to history

        if interrupted_song:
            state.music_queue.insert(0, interrupted_song)
        state.music_queue.insert(0, last_song)

        if state.vc and state.vc.is_playing():
            state.vc.stop() # Triggers play_music via after_lambda
        elif not state.is_playing:
            await self.play_music(ctx) # If nothing was playing, just start the player

    @commands.command(name="current", aliases=["song","now"], help="Displays the current playing song")
    async def current_song(self, ctx):
        state = self._get_or_create_state(ctx.guild.id)
        if state.current:
            try:
                await ctx.send(state.current['title'])
            except:
                print("error in current")
        else:
            await ctx.send(f"Nothing is playing <:baba:1422080743886291025>")

    @commands.command(name="queue", aliases=["q","ls"], help="Displays the current songs in queue")
    async def queue(self, ctx):
        state = self._get_or_create_state(ctx.guild.id)
        if not state.music_queue:
            await ctx.send("No music in queue <:baba:1422080743886291025>")
            return
        retval = ""
        for i, song in enumerate(state.music_queue[:10]): # Display up to 10 songs
            retval += f"#{i + 1} - {song['title']}\n"
        embed = Embed(title="Music Queue <:baba:1422080743886291025>", description=f"```\n{retval}\nTotal songs: {len(state.music_queue)}\n```", color=discord.Color.gold())
        await ctx.send(embed=embed)


    @commands.command(name="clear", aliases=["c", "bin"], help="Clears the queue and history.")
    async def clear(self, ctx):
        state = self._get_or_create_state(ctx.guild.id)
        state.music_queue = []
        state.song_history = [] # Also clear the history
        if state.vc and state.vc.is_playing():
            state.vc.stop() # Stop current playback if any
        state.is_playing = False
        state.current = None
        if state.now_playing_message:
            await state.now_playing_message.delete() # Delete the now playing message
            state.now_playing_message = None
        await ctx.send("Queue cleared <:baba:1422080743886291025>")

    @commands.command(name="leave", aliases=["disconnect", "l", "d","stop","bye"], help="Disconnects the bot and clears the queue.")
    async def leave(self, ctx):
        state = self._get_or_create_state(ctx.guild.id)

        # Clear queues and playback state
        state.music_queue = []
        state.song_history = []  # Also clear the history
        state.is_playing = False
        state.is_paused = False
        state.current = None
        # Attempt to disconnect any existing voice client for this guild
        existing_vc = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
        if existing_vc and existing_vc.is_connected():
            try:
                await existing_vc.disconnect()
            except Exception as e:
                print(f"Error disconnecting existing voice client: {e}")

        state.vc = None

        # Clean up the state for this guild to save memory
        if ctx.guild.id in self.guild_states:
            del self.guild_states[ctx.guild.id]

        # Ensure now_playing_message is deleted if it existed
        if state.now_playing_message:
            try:
                await state.now_playing_message.delete()
            except discord.NotFound:
                pass  # Message already deleted
            state.now_playing_message = None
        state.now_playing_message = None


    @commands.command(name="remove", aliases=["rm"], help="Remove last song from queue or current song if 'current' is specified")
    async def remove(self, ctx, *args):
        state = self._get_or_create_state(ctx.guild.id)
        if args and args[0].lower() == "current":
            if state.current:
                current_title = state.current['title']
                
                if state.vc and state.vc.is_playing():
                    state.vc.stop() # Stop the current song
                
                state.is_playing = False
                state.is_paused = False
                
                song_source = state.current['source']
                if not song_source.startswith("https://") and os.path.exists(song_source):
                    try:
                        # Remove the local file asynchronously
                        # Wait for a few seconds to ensure the file is not in use
                        await asyncio.sleep(5)
                        await asyncio.get_event_loop().run_in_executor(None, os.remove, song_source)
                        await ctx.send(f"```'{current_title}' removed```")
                    except Exception as e:
                        await ctx.send(f"```Failed to remove '{current_title}': {e}```")
                else:
                    await ctx.send(f"```'{current_title}' removed```")

                state.current = None
                if state.now_playing_message:
                    await state.now_playing_message.delete() # Delete the old "Now Playing" message
                    state.now_playing_message = None

            else:
                await ctx.send("```There's no song playing to remove.```")
        else: # Remove last song in queue
            if state.music_queue:
                removed_song = state.music_queue.pop()
                await ctx.send(f"```'{removed_song['title']}' removed```")
            else:
                await ctx.send("```No songs in the queue to remove.```")

async def setup(bot):
    await bot.add_cog(music_cog(bot))
    print('Music loaded!')