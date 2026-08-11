import asyncio
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import music_cog as music_module


class FakeMessage:
    def __init__(self):
        self.deleted = False
        self.edits = []
        self.id = id(self)

    async def delete(self):
        self.deleted = True

    async def edit(self, **kwargs):
        self.edits.append(kwargs)


class FakeContext:
    def __init__(self, guild, *, content="baba play"):
        self.guild = guild
        self.author = SimpleNamespace(voice=SimpleNamespace(channel=SimpleNamespace(connect=None)))
        self.message = SimpleNamespace(content=content)
        tokens = content.split()
        self.invoked_with = tokens[1].lower() if len(tokens) > 1 else "play"
        self.sent = []
        self.channel = self

    async def send(self, content=None, **kwargs):
        message = FakeMessage()
        self.sent.append((content, kwargs, message))
        return message


class FakeInteractionResponse:
    def __init__(self):
        self.messages = []
        self.edits = []
        self.deferred = []

    async def send_message(self, content=None, **kwargs):
        self.messages.append((content, kwargs))

    async def edit_message(self, **kwargs):
        self.edits.append(kwargs)

    async def defer(self, **kwargs):
        self.deferred.append(kwargs)


class FakeFollowup:
    def __init__(self):
        self.messages = []

    async def send(self, content=None, **kwargs):
        self.messages.append((content, kwargs))


class FakeMusicInteraction:
    def __init__(self, guild, user):
        self.guild = guild
        self.user = user
        self.response = FakeInteractionResponse()
        self.followup = FakeFollowup()


class FakeVoiceClient:
    def __init__(self, guild):
        self.guild = guild
        self.channel = SimpleNamespace()
        self.source = None
        self.playing = False
        self.paused = False
        self.connected = True
        self.after = None
        self.play_calls = []
        self.stop_calls = 0
        self.disconnect_calls = 0

    def is_connected(self):
        return self.connected

    def is_playing(self):
        return self.playing

    def is_paused(self):
        return self.paused

    def play(self, source, *, after):
        if self.playing or self.paused:
            raise RuntimeError("VoiceClient.play called while a source still owns the client")
        self.source = source
        self.playing = True
        self.after = after
        self.play_calls.append(source)

    def pause(self):
        self.playing = False
        self.paused = True

    def resume(self):
        self.paused = False
        self.playing = True

    def stop(self):
        self.stop_calls += 1
        callback = self.after
        self.after = None
        self.playing = False
        self.paused = False
        if callback:
            callback(None)

    async def disconnect(self, *, force=False):
        self.disconnect_calls += 1
        self.connected = False
        if self.playing or self.paused:
            self.stop()


class FakeYoutubeDL:
    downloaded_path = None

    def __init__(self, _options):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def extract_info(self, url, *, download):
        if download:
            return {"title": "Downloaded", "url": url}
        return {
            "title": "Downloaded",
            "duration": 60,
            "thumbnail": "https://img.test/x",
            "uploader": "Uploader",
            "url": "https://cdn.test/audio",
        }

    def prepare_filename(self, _info):
        return self.downloaded_path


class CleanupAudio:
    def __init__(self, original=None):
        self.original = original
        self.cleanup_calls = 0

    def cleanup(self):
        self.cleanup_calls += 1
        if self.original is not None:
            self.original.cleanup()


class MusicLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.guild = SimpleNamespace(id=101)
        self.bot = SimpleNamespace(loop=asyncio.get_running_loop(), voice_clients=[])
        self.cog = music_module.music_cog(self.bot)
        self.cog.music_folder = self.temp_dir.name
        self.ctx = FakeContext(self.guild)
        self.voice = FakeVoiceClient(self.guild)
        self.bot.voice_clients.append(self.voice)
        self.state = self.cog._get_or_create_state(self.guild.id)
        self.state.vc = self.voice
        self.audio_patches = [
            patch.object(
                music_module.discord,
                "FFmpegPCMAudio",
                side_effect=lambda source, **_kwargs: SimpleNamespace(path=source),
            ),
            patch.object(
                music_module.discord,
                "PCMVolumeTransformer",
                side_effect=lambda source, volume: SimpleNamespace(original=source, volume=volume),
            ),
        ]
        for patcher in self.audio_patches:
            patcher.start()

    async def asyncTearDown(self):
        for patcher in reversed(self.audio_patches):
            patcher.stop()
        self.temp_dir.cleanup()

    def make_track(self, name):
        path = Path(self.temp_dir.name, name)
        path.write_bytes(b"audio")
        return {"source": str(path), "title": path.stem}

    async def start_tracks(self, *names):
        self.state.music_queue.extend(self.make_track(name) for name in names)
        await self.cog.play_music(self.ctx)

    async def test_idle_timeout_does_not_cancel_itself_and_finishes_disconnect(self):
        async def immediate_sleep(_delay):
            return None

        with patch.object(music_module.asyncio, "sleep", new=immediate_sleep):
            self.cog._start_idle_timer(self.ctx)
            idle_task = self.state.idle_task
            await idle_task

        self.assertFalse(idle_task.cancelled())
        self.assertEqual(self.voice.disconnect_calls, 1)
        self.assertNotIn(self.guild.id, self.cog.guild_states)

    async def test_play_without_user_voice_preserves_existing_idle_timer(self):
        idle_task = asyncio.create_task(asyncio.Event().wait())
        self.state.idle_task = idle_task
        self.ctx.author.voice = None

        try:
            await music_module.music_cog.play.callback(self.cog, self.ctx, query="song")
            self.assertIs(self.state.idle_task, idle_task)
            self.assertFalse(idle_task.done())
        finally:
            idle_task.cancel()
            await asyncio.gather(idle_task, return_exceptions=True)

    async def test_play_resumes_paused_state_without_command_binding_error(self):
        self.state.is_playing = False
        self.state.is_paused = True
        self.voice.playing = False
        self.voice.paused = True

        await music_module.music_cog.play.callback(self.cog, self.ctx, query="")

        self.assertTrue(self.state.is_playing)
        self.assertFalse(self.state.is_paused)
        self.assertTrue(self.voice.is_playing())

    async def test_clear_restarts_idle_timer_after_stopping_paused_client(self):
        self.state.current = self.make_track("paused.mp3")
        self.state.is_paused = True
        self.voice.paused = True

        await music_module.music_cog.clear.callback(self.cog, self.ctx)

        self.assertIsNotNone(self.state.idle_task)
        self.assertFalse(self.state.idle_task.done())
        self.state.idle_task.cancel()
        await asyncio.gather(self.state.idle_task, return_exceptions=True)

    async def test_concurrent_starts_are_serialized_without_losing_queue_state(self):
        self.state.music_queue.extend([self.make_track("first.mp3"), self.make_track("second.mp3")])

        async def slow_embed(*_args, **_kwargs):
            await asyncio.sleep(0.02)

        self.cog.send_music_embed = slow_embed
        await asyncio.gather(self.cog.play_music(self.ctx), self.cog.play_music(self.ctx))

        self.assertEqual(len(self.voice.play_calls), 1)
        self.assertEqual(self.state.current["title"], "first")
        self.assertEqual([song["title"] for song in self.state.music_queue], ["second"])

    async def test_concurrent_reconnects_share_one_voice_connection(self):
        self.state.vc = None
        self.bot.voice_clients.clear()
        connect_calls = 0

        async def connect_once():
            nonlocal connect_calls
            connect_calls += 1
            await asyncio.sleep(0.02)
            return self.voice

        self.ctx.author.voice.channel.connect = connect_once
        await asyncio.gather(
            music_module.music_cog.play.callback(self.cog, self.ctx, query=""),
            music_module.music_cog.play.callback(self.cog, self.ctx, query=""),
        )

        self.assertEqual(connect_calls, 1)
        self.assertIs(self.state.vc, self.voice)

    async def test_downloaded_track_replaces_current_source_with_real_local_path(self):
        downloaded = Path(self.temp_dir.name, "downloaded.webm")
        downloaded.write_bytes(b"audio")
        FakeYoutubeDL.downloaded_path = str(downloaded)
        original_url = "https://www.youtube.com/watch?v=test"
        self.state.music_queue.append({"source": original_url, "title": "Remote"})

        with patch.object(music_module, "YoutubeDL", FakeYoutubeDL):
            await self.cog.play_music(self.ctx)

        self.assertEqual(self.state.current["source"], str(downloaded))
        self.assertEqual(self.state.current["webpage_url"], original_url)
        self.assertEqual(self.voice.play_calls[0].original.path, str(downloaded))

    async def test_local_file_playback_does_not_pass_network_reconnect_options_to_ffmpeg(self):
        captured = {}

        def capture_source(source, **kwargs):
            captured["source"] = source
            captured["kwargs"] = kwargs
            return SimpleNamespace(path=source)

        self.state.music_queue.append(self.make_track("local-options.mp3"))
        with patch.object(
            music_module.discord,
            "FFmpegPCMAudio",
            side_effect=capture_source,
        ):
            await self.cog.play_music(self.ctx)

        self.assertNotIn("before_options", captured["kwargs"])
        self.assertEqual(captured["kwargs"]["options"], "-vn")

    async def test_voice_connect_failure_replies_without_mutating_playback_or_idle_state(self):
        stale_voice = self.voice
        stale_voice.connected = False
        current = self.make_track("current.mp3")
        queued = self.make_track("queued.mp3")
        self.state.current = current
        self.state.music_queue.append(queued)
        self.state.is_playing = True
        idle_task = asyncio.create_task(asyncio.Event().wait())
        self.state.idle_task = idle_task

        async def fail_connect():
            raise RuntimeError("voice gateway unavailable")

        self.ctx.author.voice.channel.connect = fail_connect
        try:
            await music_module.music_cog.play.callback(self.cog, self.ctx, query="new song")
            rendered = "\n".join(content or "" for content, _kwargs, _message in self.ctx.sent).lower()
            self.assertIn("couldn't connect", rendered)
            self.assertIn("rejoin", rendered)
            self.assertIs(self.state.current, current)
            self.assertEqual(self.state.music_queue, [queued])
            self.assertTrue(self.state.is_playing)
            self.assertIs(self.state.idle_task, idle_task)
            self.assertFalse(idle_task.done())
        finally:
            idle_task.cancel()
            await asyncio.gather(idle_task, return_exceptions=True)

    async def test_successful_reconnect_reconciles_flags_and_restarts_current_track(self):
        old_voice = self.voice
        old_voice.connected = False
        old_voice.playing = True
        current = self.make_track("resume-me.mp3")
        queued = self.make_track("next.mp3")
        self.state.current = current
        self.state.music_queue.append(queued)
        self.state.is_playing = True
        self.state.is_paused = False
        self.state.starting = True
        self.state.playback_generation = 4
        old_token = self.state.playback_generation
        new_voice = FakeVoiceClient(self.guild)

        async def connect():
            return new_voice

        self.ctx.author.voice.channel.connect = connect
        await music_module.music_cog.play.callback(self.cog, self.ctx, query="")

        self.assertIs(self.state.vc, new_voice)
        self.assertEqual(old_voice.stop_calls, 1)
        self.assertEqual(self.state.current["title"], "resume-me")
        self.assertEqual([song["title"] for song in self.state.music_queue], ["next"])
        self.assertTrue(self.state.is_playing)
        self.assertFalse(self.state.is_paused)
        self.assertFalse(self.state.starting)
        self.assertGreater(self.state.playback_generation, old_token)
        self.assertEqual(len(new_voice.play_calls), 1)

        await self.cog._handle_after(self.ctx, self.state, old_token, None)
        self.assertEqual(self.state.current["title"], "resume-me")
        self.assertEqual(len(new_voice.play_calls), 1)

    async def test_reconnect_does_not_restore_track_cleared_while_connecting(self):
        old_voice = self.voice
        old_voice.connected = False
        current = self.make_track("cleared-current.mp3")
        queued = self.make_track("cleared-next.mp3")
        self.state.current = current
        self.state.music_queue.append(queued)
        new_voice = FakeVoiceClient(self.guild)
        connect_started = asyncio.Event()
        release_connect = asyncio.Event()

        async def connect():
            connect_started.set()
            await release_connect.wait()
            return new_voice

        self.ctx.author.voice.channel.connect = connect
        play_task = asyncio.create_task(
            music_module.music_cog.play.callback(self.cog, self.ctx, query="")
        )
        await connect_started.wait()
        clear_task = asyncio.create_task(
            music_module.music_cog.clear.callback(self.cog, self.ctx)
        )
        await asyncio.sleep(0.01)
        release_connect.set()
        await asyncio.gather(play_task, clear_task)
        await asyncio.sleep(0.05)

        self.assertIsNone(self.state.current)
        self.assertEqual(self.state.music_queue, [])

    async def test_clear_stops_paused_source_and_stale_callback_cannot_advance(self):
        await self.start_tracks("current.mp3", "queued.mp3")
        self.voice.pause()
        self.state.is_playing = False
        self.state.is_paused = True

        await music_module.music_cog.clear.callback(self.cog, self.ctx)
        await asyncio.sleep(0.05)

        self.assertEqual(self.voice.stop_calls, 1)
        self.assertFalse(self.voice.is_paused())
        self.assertIsNone(self.state.current)
        self.assertEqual(self.state.music_queue, [])
        self.assertEqual(len(self.voice.play_calls), 1)

    async def test_old_after_callback_cannot_recreate_or_advance_replacement_session(self):
        await self.start_tracks("old.mp3")
        old_after = self.voice.after

        await music_module.music_cog.leave.callback(self.cog, self.ctx)
        replacement = self.cog._get_or_create_state(self.guild.id)
        replacement_voice = FakeVoiceClient(self.guild)
        replacement.vc = replacement_voice
        replacement.music_queue.append(self.make_track("new.mp3"))
        await self.cog.play_music(self.ctx)

        old_after(None)
        await asyncio.sleep(0.05)

        self.assertIs(self.cog.guild_states[self.guild.id], replacement)
        self.assertEqual(replacement.current["title"], "new")
        self.assertEqual(len(replacement_voice.play_calls), 1)

    async def test_remove_current_deletes_playing_library_file_then_continues_queue(self):
        await self.start_tracks("delete-me.mp3", "next.mp3")
        removed_path = Path(self.state.current["source"])

        await music_module.music_cog.remove.callback(self.cog, self.ctx, "current")
        await asyncio.sleep(0.05)

        self.assertFalse(removed_path.exists())
        self.assertEqual(self.state.current["title"], "next")
        self.assertEqual(len(self.voice.play_calls), 2)
        self.assertTrue(any("removed" in (content or "") for content, _kwargs, _msg in self.ctx.sent))

    async def test_remove_current_stops_paused_file_before_delete_and_continues(self):
        await self.start_tracks("paused-delete.mp3", "next.mp3")
        removed_path = Path(self.state.current["source"])
        self.voice.pause()
        self.state.is_playing = False
        self.state.is_paused = True

        await music_module.music_cog.remove.callback(self.cog, self.ctx, "current")
        await asyncio.sleep(0.05)

        self.assertEqual(self.voice.stop_calls, 1)
        self.assertFalse(removed_path.exists())
        self.assertEqual(self.state.current["title"], "next")
        self.assertEqual(len(self.voice.play_calls), 2)

    async def test_remove_current_reports_delete_failure_and_still_continues(self):
        await self.start_tracks("locked.mp3", "next.mp3")

        with patch.object(music_module.os, "remove", side_effect=PermissionError("locked")):
            await music_module.music_cog.remove.callback(self.cog, self.ctx, "current")
        await asyncio.sleep(0.05)

        self.assertEqual(self.state.current["title"], "next")
        self.assertEqual(len(self.voice.play_calls), 2)
        self.assertTrue(any("Failed to remove" in (content or "") for content, _kwargs, _msg in self.ctx.sent))

    async def test_last_stops_paused_owner_before_starting_previous_song(self):
        await self.start_tracks("current.mp3")
        previous = self.make_track("previous.mp3")
        self.state.song_history.insert(0, previous)
        self.voice.pause()
        self.state.is_playing = False
        self.state.is_paused = True

        await music_module.music_cog.last.callback(self.cog, self.ctx)
        await asyncio.sleep(0.05)

        self.assertEqual(self.voice.stop_calls, 1)
        self.assertEqual(self.state.current["title"], "previous")
        self.assertEqual([song["title"] for song in self.state.music_queue], ["current"])
        self.assertEqual(len(self.voice.play_calls), 2)

    async def test_skip_and_skipto_bypass_single_loop_for_intuitive_targets(self):
        await self.start_tracks("first.mp3", "second.mp3", "third.mp3", "fourth.mp3")
        self.state.loop_mode = "single"

        await music_module.music_cog.skip.callback(self.cog, self.ctx)
        await asyncio.sleep(0.05)
        self.assertEqual(self.state.current["title"], "second")

        await music_module.music_cog.skipto.callback(self.cog, self.ctx, 2)
        await asyncio.sleep(0.05)
        self.assertEqual(self.state.current["title"], "fourth")

    async def test_ffmpeg_after_error_is_logged_reported_and_advances(self):
        await self.start_tracks("broken.mp3", "next.mp3")
        self.state.loop_mode = "single"
        token = self.state.playback_generation
        self.voice.playing = False
        self.voice.after = None

        with self.assertLogs("music_cog", level="ERROR"):
            await self.cog._handle_after(self.ctx, self.state, token, RuntimeError("decoder failed"))

        self.assertEqual(self.state.current["title"], "next")
        self.assertNotIn("broken", [song["title"] for song in self.state.song_history])
        self.assertEqual(len(self.voice.play_calls), 2)
        self.assertTrue(any("Playback error" in (content or "") for content, _kwargs, _msg in self.ctx.sent))

    async def test_ffmpeg_after_error_does_not_expose_internal_exception_details(self):
        await self.start_tracks("broken-private.mp3")
        token = self.state.playback_generation
        self.voice.playing = False
        self.voice.after = None

        await self.cog._handle_after(
            self.ctx,
            self.state,
            token,
            RuntimeError("private decoder path and implementation detail"),
        )

        rendered = "\n".join(content or "" for content, _kwargs, _msg in self.ctx.sent)
        self.assertIn("Playback error", rendered)
        self.assertNotIn("private decoder path", rendered)

    async def test_failed_source_start_is_not_recorded_or_retried_by_single_loop(self):
        failed = self.make_track("cannot-start.mp3")
        self.state.music_queue.append(failed)
        self.state.loop_mode = "single"

        with patch.object(music_module.discord, "FFmpegPCMAudio", side_effect=RuntimeError("bad source")):
            await self.cog.play_music(self.ctx)

        self.assertIsNone(self.state.current)
        self.assertEqual(self.state.music_queue, [])
        self.assertEqual(self.state.song_history, [])
        self.assertEqual(self.voice.play_calls, [])
        if self.state.idle_task:
            self.state.idle_task.cancel()
            await asyncio.gather(self.state.idle_task, return_exceptions=True)

    async def test_cancelled_source_start_clears_state_before_propagating_cancellation(self):
        self.state.music_queue.append(self.make_track("cancelled.mp3"))

        with patch.object(
            music_module.discord,
            "FFmpegPCMAudio",
            side_effect=asyncio.CancelledError,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await self.cog._play_music_locked(self.ctx, self.state)

        self.assertIsNone(self.state.current)
        self.assertFalse(self.state.starting)
        self.assertFalse(self.state.is_playing)
        self.assertFalse(self.state.is_paused)

    async def test_failed_source_start_cleans_audio_and_expires_now_playing_controls(self):
        failed = self.make_track("cleanup-on-start-error.mp3")
        self.state.music_queue.append(failed)
        raw_audio = CleanupAudio()
        volume_audio = CleanupAudio(raw_audio)

        def fail_play(_source, *, after):
            raise RuntimeError("voice rejected source")

        self.voice.play = fail_play
        with (
            patch.object(music_module.discord, "FFmpegPCMAudio", return_value=raw_audio),
            patch.object(music_module.discord, "PCMVolumeTransformer", return_value=volume_audio),
        ):
            await self.cog.play_music(self.ctx)

        self.assertEqual(volume_audio.cleanup_calls, 1)
        self.assertEqual(raw_audio.cleanup_calls, 1)
        playing_messages = [
            message
            for _content, kwargs, message in self.ctx.sent
            if "view" in kwargs
        ]
        self.assertEqual(len(playing_messages), 1)
        self.assertEqual(playing_messages[0].edits[-1]["view"], None)

    async def test_natural_successful_end_records_final_track_in_history(self):
        await self.start_tracks("finished.mp3")
        token = self.state.playback_generation
        self.voice.playing = False
        self.voice.after = None

        await self.cog._handle_after(self.ctx, self.state, token, None)

        self.assertIsNone(self.state.current)
        self.assertEqual([song["title"] for song in self.state.song_history], ["finished"])
        if self.state.idle_task:
            self.state.idle_task.cancel()
            await asyncio.gather(self.state.idle_task, return_exceptions=True)

    async def test_cog_unload_invalidates_stops_cancels_and_disconnects(self):
        await self.start_tracks("current.mp3")
        self.voice.pause()
        self.state.is_playing = False
        self.state.is_paused = True
        self.state.idle_task = asyncio.create_task(asyncio.sleep(60))
        idle_task = self.state.idle_task

        self.cog.cog_unload()
        await asyncio.sleep(0.05)

        self.assertTrue(idle_task.cancelled())
        self.assertEqual(self.voice.stop_calls, 1)
        self.assertEqual(self.voice.disconnect_calls, 1)
        self.assertEqual(self.cog.guild_states, {})

    async def _start_blocking_search(self):
        started = threading.Event()
        release = threading.Event()
        result = self.make_track("search-result.mp3")

        def blocking_search(_query, _count):
            started.set()
            release.wait(timeout=2)
            return [result]

        self.cog.search_yt_multiple = blocking_search
        task = asyncio.create_task(
            music_module.music_cog.play.callback(self.cog, self.ctx, query="missing title")
        )
        self.assertTrue(
            await asyncio.wait_for(asyncio.to_thread(started.wait, 1), timeout=1.5)
        )
        return task, release

    async def test_leave_cancels_inflight_search_and_stale_completion_cannot_recreate_state(self):
        play_task, release = await self._start_blocking_search()
        loading_message = self.ctx.sent[0][2]
        try:
            await music_module.music_cog.leave.callback(self.cog, self.ctx)
            await asyncio.sleep(0)
            was_cancelled = play_task.cancelled()
        finally:
            release.set()
            await asyncio.gather(play_task, return_exceptions=True)

        self.assertTrue(was_cancelled)
        self.assertTrue(any(edit.get("view") is None for edit in loading_message.edits))
        self.assertNotIn(self.guild.id, self.cog.guild_states)

    async def test_older_search_cannot_publish_after_newer_search_request(self):
        old_started = threading.Event()
        new_started = threading.Event()
        old_release = threading.Event()
        new_release = threading.Event()

        def ordered_search(query, _count):
            if query == "old":
                old_started.set()
                old_release.wait(timeout=2)
            else:
                new_started.set()
                new_release.wait(timeout=2)
            return [{
                "source": f"https://youtube.test/{query}",
                "title": query,
                "duration_str": "0:01",
            }]

        self.cog.search_yt_multiple = ordered_search
        old_task = asyncio.create_task(
            music_module.music_cog.play.callback(self.cog, self.ctx, query="old")
        )
        self.assertTrue(await asyncio.wait_for(asyncio.to_thread(old_started.wait, 1), timeout=1.5))
        new_task = asyncio.create_task(
            music_module.music_cog.play.callback(self.cog, self.ctx, query="new")
        )
        self.assertTrue(await asyncio.wait_for(asyncio.to_thread(new_started.wait, 1), timeout=1.5))

        old_release.set()
        await asyncio.sleep(0.05)
        new_release.set()
        await asyncio.gather(old_task, new_task)

        old_loading = self.ctx.sent[0][2]
        self.assertTrue(any(edit.get("view") is None for edit in old_loading.edits))
        self.assertIs(self.state.pending_search_message, self.ctx.sent[-1][2])

    async def test_clear_cancels_inflight_search_so_result_cannot_repopulate_queue(self):
        play_task, release = await self._start_blocking_search()
        try:
            await music_module.music_cog.clear.callback(self.cog, self.ctx)
            await asyncio.sleep(0)
            was_cancelled = play_task.cancelled()
        finally:
            release.set()
            await asyncio.gather(play_task, return_exceptions=True)

        self.assertTrue(was_cancelled)
        self.assertEqual(self.state.music_queue, [])
        if self.state.idle_task:
            self.state.idle_task.cancel()
            await asyncio.gather(self.state.idle_task, return_exceptions=True)

    async def test_clear_cancels_playlist_and_disables_loading_message(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def blocking_playlist(_query):
            started.set()
            await release.wait()
            return [{"source": "one", "title": "one"}]

        self.cog.fetch_playlist_videos = blocking_playlist
        play_task = asyncio.create_task(
            music_module.music_cog.play.callback(
                self.cog,
                self.ctx,
                query="https://youtube.com/playlist?list=blocked",
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        loading_message = self.ctx.sent[0][2]
        try:
            await music_module.music_cog.clear.callback(self.cog, self.ctx)
        finally:
            release.set()
            await asyncio.gather(play_task, return_exceptions=True)

        self.assertTrue(play_task.cancelled())
        self.assertTrue(any(edit.get("view") is None for edit in loading_message.edits))

    async def test_clear_cancels_all_concurrent_url_preparations(self):
        started = threading.Event()
        release = threading.Event()
        started_count = 0
        started_lock = threading.Lock()
        result = self.make_track("concurrent-result.mp3")

        async def blocking_search(_query):
            nonlocal started_count
            with started_lock:
                started_count += 1
                if started_count == 2:
                    started.set()
            await asyncio.to_thread(release.wait, 2)
            return result

        self.cog.search_yt = blocking_search
        tasks = [
            asyncio.create_task(
                music_module.music_cog.play.callback(
                    self.cog,
                    self.ctx,
                    query=f"https://www.youtube.com/watch?v={index}",
                )
            )
            for index in range(2)
        ]
        self.assertTrue(await asyncio.wait_for(asyncio.to_thread(started.wait, 1), timeout=1.5))
        self.assertEqual(len(self.state.preparing_tasks), 2)

        try:
            await music_module.music_cog.clear.callback(self.cog, self.ctx)
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)

        self.assertTrue(all(task.cancelled() for task in tasks))
        self.assertIsNone(self.state.current)
        self.assertEqual(self.state.music_queue, [])
        self.assertEqual(self.voice.play_calls, [])

    async def test_skip_cancels_inflight_current_preparation_before_waiting_for_play_lock(self):
        started = threading.Event()
        release = threading.Event()

        class BlockingYoutubeDL:
            def __init__(self, _options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _source, *, download):
                assert not download
                started.set()
                release.wait(timeout=2)
                return {
                    "title": "Loading",
                    "duration": 0,
                    "url": "https://cdn.test/loading",
                }

        self.state.music_queue.append(
            {"source": "https://www.youtube.com/watch?v=loading", "title": "Loading"}
        )
        patcher = patch.object(music_module, "YoutubeDL", BlockingYoutubeDL)
        patcher.start()

        timed_out = False
        play_task = None
        skip_task = None
        try:
            play_task = asyncio.create_task(self.cog.play_music(self.ctx))
            self.assertTrue(await asyncio.wait_for(asyncio.to_thread(started.wait, 1), timeout=1.5))
            skip_task = asyncio.create_task(music_module.music_cog.skip.callback(self.cog, self.ctx))
            try:
                await asyncio.wait_for(asyncio.shield(skip_task), timeout=0.2)
            except asyncio.TimeoutError:
                timed_out = True
        finally:
            release.set()
            await asyncio.gather(play_task, skip_task, return_exceptions=True)
            patcher.stop()

        self.assertFalse(timed_out, "skip waited for the blocked preparation")
        self.assertTrue(play_task.cancelled())
        self.assertIsNone(self.state.current)
        self.assertFalse(self.state.starting)
        self.assertEqual(self.state.music_queue, [])
        if self.state.idle_task:
            self.state.idle_task.cancel()
            await asyncio.gather(self.state.idle_task, return_exceptions=True)

    async def test_cog_unload_cancels_inflight_search_without_state_resurrection(self):
        play_task, release = await self._start_blocking_search()
        try:
            self.cog.cog_unload()
            await asyncio.sleep(0)
            was_cancelled = play_task.cancelled()
        finally:
            release.set()
            await asyncio.gather(play_task, return_exceptions=True)

        self.assertTrue(was_cancelled)
        self.assertEqual(self.cog.guild_states, {})

    async def test_leave_cancels_inflight_download_before_waiting_for_play_lock(self):
        started = threading.Event()
        release = threading.Event()

        class BlockingYoutubeDL:
            def __init__(self, _options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _url, *, download):
                self.assert_download_flag = download
                started.set()
                release.wait(timeout=2)
                return {
                    "title": "Remote",
                    "duration": 400,
                    "url": "https://cdn.test/stream",
                }

        self.state.music_queue.append(
            {"source": "https://www.youtube.com/watch?v=slow", "title": "Remote"}
        )
        patcher = patch.object(music_module, "YoutubeDL", BlockingYoutubeDL)
        patcher.start()
        play_task = asyncio.create_task(self.cog.play_music(self.ctx))
        self.assertTrue(
            await asyncio.wait_for(asyncio.to_thread(started.wait, 1), timeout=1.5)
        )
        leave_task = asyncio.create_task(music_module.music_cog.leave.callback(self.cog, self.ctx))
        timed_out = False
        try:
            try:
                await asyncio.wait_for(asyncio.shield(leave_task), timeout=0.2)
            except asyncio.TimeoutError:
                timed_out = True
        finally:
            release.set()
            await asyncio.gather(play_task, leave_task, return_exceptions=True)
            patcher.stop()

        self.assertFalse(timed_out, "leave waited for the blocked yt-dlp executor call")
        self.assertTrue(play_task.cancelled())
        self.assertNotIn(self.guild.id, self.cog.guild_states)


class MusicQueueParsingTests(unittest.IsolatedAsyncioTestCase):
    def test_local_song_title_strips_all_trailing_bracketed_labels(self):
        self.assertEqual(
            music_module.local_song_title(
                r"C:\\Users\\manza\\Music\\Queen - Song [abc123].m4a"
            ),
            "Queen - Song",
        )
        self.assertEqual(
            music_module.local_song_title(
                r"C:\\Users\\manza\\Music\\Song [Official Video] [abc123].m4a"
            ),
            "Song",
        )
        self.assertEqual(
            music_module.local_song_title(r"C:\\Users\\manza\\Music\\Song.m4a"),
            "Song",
        )

    def test_all_is_only_recognized_as_an_exact_token(self):
        self.assertEqual(music_module.parse_local_query("small"), ("small", False))
        self.assertEqual(music_module.parse_local_query("fallout"), ("fallout", False))
        self.assertEqual(music_module.parse_local_query("all fallout"), ("fallout", True))
        self.assertEqual(music_module.parse_local_query("SMALL ALL"), ("small", True))

    def test_flat_search_prefers_webpage_url_over_bare_entry_url(self):
        class FlatYoutubeDL:
            def __init__(self, _options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _query, *, download):
                self.download = download
                return {
                    "entries": [
                        {
                            "id": "abc",
                            "title": "Result",
                            "url": "abc",
                            "webpage_url": "https://www.youtube.com/watch?v=abc",
                        }
                    ]
                }

        with patch.object(music_module, "YoutubeDL", FlatYoutubeDL):
            cog = music_module.music_cog(SimpleNamespace())
            results = cog.search_yt_multiple("result", 3)

        self.assertEqual(results[0]["source"], "https://www.youtube.com/watch?v=abc")

    async def test_local_all_search_excludes_metadata_and_non_audio_files(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        for filename in ("track.mp3", "album.flac", "desktop.ini", "cover.jpg", "notes.txt"):
            Path(temp_dir.name, filename).write_bytes(b"data")
        guild = SimpleNamespace(id=201)
        bot = SimpleNamespace(loop=asyncio.get_running_loop(), voice_clients=[])
        cog = music_module.music_cog(bot)
        cog.music_folder = temp_dir.name
        state = cog._get_or_create_state(guild.id)
        state.vc = FakeVoiceClient(guild)
        state.vc.playing = True
        state.is_playing = True
        ctx = FakeContext(guild, content="baba play all")

        await music_module.music_cog.play.callback(cog, ctx, query="all")

        self.assertEqual(
            {Path(song["source"]).name for song in state.music_queue},
            {"track.mp3", "album.flac"},
        )

    async def test_playfirst_playlist_preserves_provider_order(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        guild = SimpleNamespace(id=202)
        bot = SimpleNamespace(loop=asyncio.get_running_loop(), voice_clients=[])
        cog = music_module.music_cog(bot)
        cog.music_folder = temp_dir.name
        state = cog._get_or_create_state(guild.id)
        state.vc = FakeVoiceClient(guild)
        state.vc.playing = True
        state.is_playing = True
        state.music_queue.append({"source": "existing", "title": "existing"})
        ctx = FakeContext(guild, content="baba playfirst https://youtube.com/playlist?list=abc")

        async def playlist(_url):
            return [
                {"source": "one", "title": "one"},
                {"source": "two", "title": "two"},
                {"source": "three", "title": "three"},
            ]

        cog.fetch_playlist_videos = playlist
        await music_module.music_cog.play.callback(
            cog, ctx, query="https://youtube.com/playlist?list=abc"
        )

        self.assertEqual([song["title"] for song in state.music_queue], ["one", "two", "three", "existing"])

    async def test_playfirst_numeric_inserts_generated_songs_before_existing_queue(self):
        guild = SimpleNamespace(id=205)
        bot = SimpleNamespace(loop=asyncio.get_running_loop(), voice_clients=[])
        cog = music_module.music_cog(bot)
        state = cog._get_or_create_state(guild.id)
        state.vc = FakeVoiceClient(guild)
        state.vc.playing = True
        state.is_playing = True
        state.music_queue.append({"source": "existing", "title": "existing"})
        generated = iter(["one.mp3", "two.mp3", "three.mp3"])
        cog.get_random_song = lambda *_args: next(generated, None)
        ctx = FakeContext(guild, content="baba playfirst 3")

        await music_module.music_cog.play.callback(cog, ctx, query="3")

        self.assertEqual(
            [song["title"] for song in state.music_queue],
            ["one", "two", "three", "existing"],
        )

    async def test_playfirst_in_query_does_not_prioritize_normal_play_command(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        guild = SimpleNamespace(id=203)
        bot = SimpleNamespace(loop=asyncio.get_running_loop(), voice_clients=[])
        cog = music_module.music_cog(bot)
        cog.music_folder = temp_dir.name
        state = cog._get_or_create_state(guild.id)
        state.vc = FakeVoiceClient(guild)
        state.vc.playing = True
        state.is_playing = True
        state.music_queue.append({"source": "existing", "title": "existing"})
        Path(temp_dir.name, "playfirst-song.mp3").write_bytes(b"audio")
        ctx = FakeContext(guild, content="baba play playfirst-song")

        await music_module.music_cog.play.callback(cog, ctx, query="playfirst-song")

        self.assertEqual(
            [song["title"] for song in state.music_queue],
            ["existing", "playfirst-song"],
        )

    async def test_new_play_query_invalidates_old_search_message_before_local_enqueue(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        guild = SimpleNamespace(id=204)
        bot = SimpleNamespace(loop=asyncio.get_running_loop(), voice_clients=[])
        cog = music_module.music_cog(bot)
        cog.music_folder = temp_dir.name
        state = cog._get_or_create_state(guild.id)
        state.vc = FakeVoiceClient(guild)
        state.vc.playing = True
        state.is_playing = True
        old_view = music_module.YouTubeSearchView(
            cog,
            FakeContext(guild),
            [{"source": "old.mp3", "title": "old"}],
            state=state,
        )
        old_message = FakeMessage()
        old_view.message = old_message
        state.pending_search_message = old_message
        Path(temp_dir.name, "new-song.mp3").write_bytes(b"audio")
        ctx = FakeContext(guild, content="baba play new-song")

        await music_module.music_cog.play.callback(cog, ctx, query="new-song")

        self.assertIsNone(state.pending_search_message)
        self.assertEqual([song["title"] for song in state.music_queue], ["new-song"])


class MusicViewLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.guild = SimpleNamespace(id=303)
        self.bot = SimpleNamespace(loop=asyncio.get_running_loop(), voice_clients=[])
        self.cog = music_module.music_cog(self.bot)
        self.ctx = FakeContext(self.guild)
        self.state = self.cog._get_or_create_state(self.guild.id)
        self.voice = FakeVoiceClient(self.guild)
        self.state.vc = self.voice
        self.state.is_playing = True
        self.voice.playing = True
        self.ctx.author.voice.channel = self.voice.channel
        self.result = {"source": "local.mp3", "title": "Picked", "duration_str": "1:00"}

    async def test_search_callback_rejects_replaced_state_without_enqueueing_or_recreating(self):
        view = music_module.YouTubeSearchView(
            self.cog,
            self.ctx,
            [self.result],
            state=self.state,
        )
        replacement = music_module.GuildState()
        replacement.vc = self.voice
        self.cog.guild_states[self.guild.id] = replacement
        interaction = FakeMusicInteraction(self.guild, self.ctx.author)

        await view.children[0].callback(interaction)

        self.assertEqual(self.state.music_queue, [])
        self.assertEqual(replacement.music_queue, [])
        self.assertIs(self.cog.guild_states[self.guild.id], replacement)
        self.assertEqual(interaction.response.edits[-1]["view"], None)

    async def test_now_playing_replacement_disables_pending_search_message(self):
        pending_message = FakeMessage()
        self.state.pending_search_message = pending_message

        await self.cog.send_music_embed(self.ctx, self.result, state=self.state)

        self.assertIsNone(self.state.pending_search_message)
        self.assertTrue(any(edit.get("view") is None for edit in pending_message.edits))

    async def test_old_search_view_rejects_after_same_state_search_message_replaced(self):
        view = music_module.YouTubeSearchView(
            self.cog,
            self.ctx,
            [self.result],
            state=self.state,
        )
        old_message = FakeMessage()
        view.message = old_message
        self.state.pending_search_message = old_message
        self.state.pending_search_message = FakeMessage()
        interaction = FakeMusicInteraction(self.guild, self.ctx.author)

        await view.children[0].callback(interaction)

        self.assertEqual(self.state.music_queue, [])
        self.assertEqual(interaction.response.edits[-1]["view"], None)

    async def test_search_timeout_auto_selects_first_only_while_origin_state_is_live(self):
        view = music_module.YouTubeSearchView(
            self.cog,
            self.ctx,
            [self.result],
            state=self.state,
        )
        message = FakeMessage()
        view.message = message
        self.state.pending_search_message = message

        await view.on_timeout()

        self.assertEqual([song["title"] for song in self.state.music_queue], ["Picked"])
        self.assertEqual(message.edits[-1]["view"], None)
        interaction = FakeMusicInteraction(self.guild, self.ctx.author)
        await view.children[0].callback(interaction)
        self.assertEqual([song["title"] for song in self.state.music_queue], ["Picked"])

    async def test_search_timeout_on_removed_state_only_expires_buttons(self):
        view = music_module.YouTubeSearchView(
            self.cog,
            self.ctx,
            [self.result],
            state=self.state,
        )
        message = FakeMessage()
        view.message = message
        del self.cog.guild_states[self.guild.id]

        await view.on_timeout()

        self.assertEqual(self.state.music_queue, [])
        self.assertNotIn(self.guild.id, self.cog.guild_states)
        self.assertEqual(self.ctx.sent, [])
        self.assertEqual(message.edits[-1]["view"], None)

    async def test_old_music_controls_reject_without_recreating_state_and_keep_channel_check(self):
        controls = music_module.MusicControls(self.cog, self.ctx, state=self.state)
        live_interaction = FakeMusicInteraction(self.guild, self.ctx.author)
        self.assertTrue(await controls.interaction_check(live_interaction))
        self.assertEqual(controls.remove_button.label, "Remove Current")

        del self.cog.guild_states[self.guild.id]
        stale_interaction = FakeMusicInteraction(self.guild, self.ctx.author)
        self.assertFalse(await controls.interaction_check(stale_interaction))
        self.assertNotIn(self.guild.id, self.cog.guild_states)

    async def test_old_same_session_controls_reject_after_now_playing_message_replaced(self):
        old_message = FakeMessage()
        controls = music_module.MusicControls(self.cog, self.ctx, state=self.state)
        controls.message = old_message
        self.state.now_playing_message = old_message
        self.state.now_playing_message = FakeMessage()

        interaction = FakeMusicInteraction(self.guild, self.ctx.author)
        self.assertFalse(await controls.interaction_check(interaction))
        self.assertIn("expired", interaction.response.messages[0][0].lower())

    async def test_suspended_old_skip_control_cannot_recreate_state_after_leave(self):
        old_message = FakeMessage()
        controls = music_module.MusicControls(self.cog, self.ctx, state=self.state)
        controls.message = old_message
        self.state.now_playing_message = old_message
        defer_started = asyncio.Event()
        release_defer = asyncio.Event()

        async def blocked_defer(**_kwargs):
            defer_started.set()
            await release_defer.wait()

        interaction = FakeMusicInteraction(self.guild, self.ctx.author)
        interaction.response.defer = blocked_defer
        callback_task = asyncio.create_task(controls.skip_callback(interaction))
        await defer_started.wait()

        await music_module.music_cog.leave.callback(self.cog, self.ctx)
        self.assertNotIn(self.guild.id, self.cog.guild_states)

        release_defer.set()
        await callback_task

        self.assertNotIn(self.guild.id, self.cog.guild_states)

    async def test_clear_invalidates_pending_search_before_old_callback_can_enqueue(self):
        view = music_module.YouTubeSearchView(
            self.cog,
            self.ctx,
            [self.result],
            state=self.state,
        )
        message = FakeMessage()
        view.message = message
        self.state.pending_search_message = message

        await music_module.music_cog.clear.callback(self.cog, self.ctx)
        interaction = FakeMusicInteraction(self.guild, self.ctx.author)
        play_calls = []

        async def unexpected_play(*args, **kwargs):
            play_calls.append((args, kwargs))

        with patch.object(self.cog, "play_music", side_effect=unexpected_play):
            await view.children[0].callback(interaction)

        self.assertIsNone(self.state.pending_search_message)
        self.assertEqual(self.state.music_queue, [])
        self.assertEqual(play_calls, [])
        self.assertEqual(interaction.response.edits[-1]["view"], None)


class MusicDependencyContractTests(unittest.TestCase):
    def test_music_dependencies_are_single_source_and_ci_fallback_can_import_cog(self):
        root = Path(__file__).resolve().parents[1]
        requirements = [
            line.strip().lower()
            for line in (root / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        ci_source = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8").lower()

        self.assertNotIn("youtube-dl==2021.12.17", requirements)
        self.assertIn("yt-dlp", requirements)
        self.assertEqual(sum(line.startswith("pynacl") for line in requirements), 1)
        self.assertIn("yt-dlp", ci_source)

    def test_cog_does_not_construct_unused_shared_ytdl_instance(self):
        cog = music_module.music_cog(SimpleNamespace())
        self.assertFalse(hasattr(cog, "ytdl"))


if __name__ == "__main__":
    unittest.main()
