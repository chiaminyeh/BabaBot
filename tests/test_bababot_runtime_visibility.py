import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import dm_logging
from scripts import restart_bababot


class DmLogFormattingTests(unittest.TestCase):
    def test_complete_dm_log_includes_direction_identity_content_and_attachments(self):
        message = SimpleNamespace(
            author=SimpleNamespace(id=123, display_name="Alice"),
            content="secret full message",
            attachments=[
                SimpleNamespace(filename="photo.png", url="https://cdn.discordapp.com/photo.png"),
            ],
        )

        rendered = dm_logging.format_dm_log("IN", message)

        self.assertIn("DM IN", rendered)
        self.assertIn("user_id=123", rendered)
        self.assertIn("display_name='Alice'", rendered)
        self.assertIn("content='secret full message'", rendered)
        self.assertIn("filename='photo.png'", rendered)
        self.assertIn("url='https://cdn.discordapp.com/photo.png'", rendered)

    def test_outgoing_dm_reply_is_fully_formatted(self):
        recipient = SimpleNamespace(id=123, display_name="Alice")
        rendered = dm_logging.format_outgoing_dm(recipient, "hello")
        self.assertIn("DM OUT", rendered)
        self.assertIn("user_id=123", rendered)
        self.assertIn("content='hello'", rendered)


class WatcherLaunchTests(unittest.TestCase):
    def test_watcher_process_does_not_inherit_restart_terminal_handles(self):
        with patch.object(restart_bababot.os, "name", "nt"), patch.object(
            restart_bababot.subprocess, "Popen"
        ) as popen:
            restart_bababot.start_log_watcher(
                Path(r"C:\repo\logs\baba.log"),
                4321,
            )
        kwargs = popen.call_args.kwargs
        self.assertTrue(kwargs["close_fds"])
        self.assertNotIn("stdin", kwargs)
        self.assertNotIn("stdout", kwargs)
        self.assertNotIn("stderr", kwargs)

    def test_watcher_command_targets_exact_log_and_pid(self):
        command = restart_bababot.build_watcher_command(
            Path(r"C:\repo\logs\bababot_restart_20260101_010203.log"),
            4321,
        )
        joined = " ".join(command)
        self.assertEqual("powershell.exe", command[0])
        self.assertIn("Get-Content", joined)
        self.assertIn("bababot_restart_20260101_010203.log", joined)
        self.assertIn("4321", joined)
        self.assertIn("Bababot Live Log", joined)


if __name__ == "__main__":
    unittest.main()
