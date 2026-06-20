# -*- coding: utf-8 -*-

import discord
from discord.ext import commands, voice_recv
import io
import wave
import asyncio
import time
import speech_recognition as sr

# Make sure the opus library is loaded.
discord.opus._load_default()

class BabaAudioSink:
    def __init__(self, process_callback, loop, sample_rate=48000, channels=2, sample_width=2, timeout=5.0):
        # Dictionary mapping user -> (buffer, last_update_time)
        self.buffers = {}  # {discord.Member: (io.BytesIO, float)}
        self.process_callback = process_callback  # Async callback to process voice commands.
        self.sample_rate = sample_rate
        self.channels = channels
        self.sample_width = sample_width
        # Threshold in bytes (~1 second of audio).
        self.threshold = sample_rate * channels * sample_width
        self.loop = loop  # Main event loop from the bot.
        self.timeout = timeout  # Flush buffer every timeout seconds
        # Dictionary mapping user.id -> last processed timestamp (to throttle command processing)
        self.last_processed = {}  

    def __call__(self, user, voice_data: voice_recv.VoiceData):
        """
        This method is called by discord-ext-voice-recv for each received audio packet.
        It accumulates raw PCM data in a per-user buffer.
        """
        current_time = time.time()
        # Get or create the user's buffer and last update time.
        if user not in self.buffers:
            self.buffers[user] = (io.BytesIO(), current_time)
        buf, last_update = self.buffers[user]
        buf.write(voice_data.pcm)
        # Update the last update timestamp.
        self.buffers[user] = (buf, current_time)
        
        # If we've accumulated enough data or if timeout has passed since the last update:
        if buf.tell() >= self.threshold or (current_time - last_update) >= self.timeout:
            pcm_chunk = buf.getvalue()
            # Reset the buffer for this user.
            self.buffers[user] = (io.BytesIO(), current_time)
            # Throttle command processing to at most once every `timeout` seconds per user.
            if user.id in self.last_processed and (current_time - self.last_processed[user.id]) < self.timeout:
                return
            self.last_processed[user.id] = current_time
            # Schedule processing on the main event loop.
            asyncio.run_coroutine_threadsafe(self._process_audio(user, pcm_chunk), self.loop)

    async def _process_audio(self, user, pcm_data: bytes):
        loop = asyncio.get_running_loop()
        try:
            # Offload the blocking recognition work to a thread.
            text = await loop.run_in_executor(None, self.recognize_audio, pcm_data)
            print(f"[Voice] {user} said: {text}")
            # Check for trigger words robustly.
            lower_text = text.lower()
            trigger = None
            if lower_text.startswith("baba"):
                trigger = "baba"
            elif lower_text.startswith("bubba"):
                trigger = "bubba"
            if trigger:
                # Remove the exact trigger word from the text.
                command_text = text[len(trigger):].strip()
                print(f"[Voice Command] Detected command: {command_text}")
                asyncio.create_task(self.process_callback(user, command_text))
        except sr.UnknownValueError:
            print(f"[Voice] Could not understand audio from {user}")
        except sr.RequestError as e:
            print(f"[Voice] Speech recognition error for {user}: {e}")
        except Exception as e:
            print(f"[Voice] Unexpected error processing audio from {user}: {e}")

    def recognize_audio(self, pcm_data: bytes) -> str:
        """
        Converts the given PCM data to a WAV buffer and performs speech recognition.
        """
        pcm_buffer = io.BytesIO()
        with wave.open(pcm_buffer, 'wb') as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(self.sample_width)
            wf.setframerate(self.sample_rate)
            wf.writeframes(pcm_data)
        pcm_buffer.seek(0)
        recognizer = sr.Recognizer()
        with sr.AudioFile(pcm_buffer) as source:
            audio = recognizer.record(source)
        return recognizer.recognize_google(audio)

class listen_cog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="join", aliases=["connect", "listen"])
    async def join(self, ctx):
        """
        Connects to the voice channel and starts listening for voice commands.
        """
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send("You are not in a voice channel!")
            return
        channel = ctx.author.voice.channel
        # Connect using the voice_recv client.
        vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
        # Instantiate our custom sink with the process_voice_command callback.
        # Pass in the bot's event loop.
        sink = BabaAudioSink(self.process_voice_command, loop=ctx.bot.loop)
        # Wrap the sink in the library's BasicSink and start listening.
        vc.listen(voice_recv.BasicSink(sink))

    async def process_voice_command(self, user: discord.Member, command_text: str):
        """
        Processes a voice command. For example, if the command is 'play', it delegates to your music cog.
        """
        print(f"Processing voice command from {user}: {command_text}")
        parts = command_text.strip().split()
        if not parts:
            return  # Nothing to process
        cmd = parts[0].lower()
        args = parts[1:]
        if cmd == "play":
            query = " ".join(args)
            music_cog = self.bot.get_cog("music_cog")
            if not music_cog:
                print("Music cog not found!")
                return
            guild = user.guild
            # Choose a text channel for responses.
            text_channel = guild.system_channel or (guild.text_channels[0] if guild.text_channels else None)
            if not text_channel:
                print("No text channel available in the guild!")
                return
            fake_msg = await text_channel.send(f"Voice command: play {query}")
            try:
                ctx_obj = await self.bot.get_context(fake_msg)
                await music_cog.play(ctx_obj, *args)
            except Exception as e:
                print(f"Error invoking music play command from voice: {e}")
            finally:
                await fake_msg.delete()
        else:
            print(f"Voice command '{cmd}' not handled by music cog.")

async def setup(bot):
    await bot.add_cog(listen_cog(bot))
    print('Listening cog loaded!')
