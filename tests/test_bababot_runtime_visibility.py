import ast
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import dm_logging
from scripts import check_bababot_health
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


class AbandonedListenVisibilityTests(unittest.TestCase):
    def test_listen_cog_is_absent_and_cannot_be_loaded_or_referenced(self):
        root = Path(__file__).resolve().parents[1]
        self.assertFalse((root / "listen_cog.py").exists())

        main_source = (root / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("listen_cog", main_source)

        stale = []
        for path in root.rglob("*"):
            if not path.is_file() or path.parts[-2:-1] == ("tests",):
                continue
            if path.suffix.lower() not in {".py", ".txt", ".yml", ".yaml"}:
                continue
            if any(part in {".git", ".venv", ".hermes", "__pycache__"} for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for token in ("listen_cog", "voice_recv", "speech_recognition"):
                if token in text:
                    stale.append(f"{path.relative_to(root)}:{token}")
        self.assertEqual([], stale)

        requirements = (root / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("discord.py[voice]", requirements)


class RemovedBombCogVisibilityTests(unittest.TestCase):
    def test_bomb_cog_is_absent_and_cannot_be_loaded_or_referenced(self):
        root = Path(__file__).resolve().parents[1]
        self.assertFalse((root / "bomb_cog.py").exists())

        source_paths = [root / "main.py", root / "scripts" / "check_bababot_health.py"]
        for path in source_paths:
            self.assertNotIn("bomb_cog", path.read_text(encoding="utf-8"))


class ExtensionHealthContractTests(unittest.TestCase):
    def test_runtime_and_health_checker_expect_the_same_extensions(self):
        root = Path(__file__).resolve().parents[1]
        module = ast.parse((root / "main.py").read_text(encoding="utf-8"))
        assignment = next(
            node
            for node in module.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "EXTENSIONS" for target in node.targets)
        )
        runtime_extensions = ast.literal_eval(assignment.value)

        self.assertIn("chess_cog", runtime_extensions)
        self.assertEqual(runtime_extensions, check_bababot_health.EXPECTED_EXTENSIONS)


class HealthLogParsingTests(unittest.TestCase):
    def test_startup_markers_remain_visible_after_runtime_log_grows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "bababot_restart_20260827_000000.log"
            log_path.write_text(
                "Shard ID None has connected to Gateway\n"
                "Extensions loaded: 11/11 (all expected extensions)\n"
                "Slash commands synced: 22\n"
                + ("ordinary runtime chatter\n" * 2000),
                encoding="utf-8",
            )
            with patch.object(check_bababot_health, "LOG_DIR", Path(temp_dir)), patch.object(
                check_bababot_health, "is_bababot_running", return_value=True
            ):
                results = [
                    check_bababot_health.check_gateway_connected(running_required=True),
                    check_bababot_health.check_extensions_loaded(),
                    check_bababot_health.check_slash_commands_synced(),
                ]

        self.assertTrue(all(result.ok for result in results), [result.detail for result in results])


if __name__ == "__main__":
    unittest.main()
