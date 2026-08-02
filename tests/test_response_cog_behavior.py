import ast
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
from response_cog import response_cog


ROOT = Path(__file__).resolve().parents[1]


def _on_message_source() -> str:
    text = (ROOT / "response_cog.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "on_message":
            return ast.get_source_segment(text, node)
    raise AssertionError("response_cog.on_message not found")


class ResponseCogBehaviorTests(unittest.TestCase):
    def test_group_chat_plain_messages_return_before_canned_send_message(self):
        source = _on_message_source()
        guard = "if not is_dm:\n            return"
        first_send = source.find("await self.send_message")
        guard_pos = source.find(guard)

        self.assertNotEqual(guard_pos, -1, "group chat must return before canned replies")
        self.assertNotEqual(first_send, -1, "DM canned reply path should still exist")
        self.assertLess(guard_pos, first_send)

    def test_bot_messages_are_ignored_to_prevent_echo_loops(self):
        source = _on_message_source()
        self.assertIn("if message.author.bot:", source)

    def test_shawshaw_mention_detection(self):
        cog = response_cog(bot=MagicMock())
        cog.owner_id = 12345

        # Case 1: Other user mentions shawshaw in text
        msg_other_text = MagicMock()
        msg_other_text.author.bot = False
        msg_other_text.author.id = 99999
        msg_other_text.content = "hey Shawshaw check this"
        msg_other_text.mentions = []
        self.assertTrue(cog.is_shawshaw_mention(msg_other_text))

        # Case 2: Other user mentions owner user object
        msg_other_mention = MagicMock()
        msg_other_mention.author.bot = False
        msg_other_mention.author.id = 99999
        msg_other_mention.content = "hello"
        owner_user = MagicMock()
        owner_user.id = 12345
        msg_other_mention.mentions = [owner_user]
        self.assertTrue(cog.is_shawshaw_mention(msg_other_mention))

        # Case 3: Owner mentions self / shawshaw -> False
        msg_owner = MagicMock()
        msg_owner.author.bot = False
        msg_owner.author.id = 12345
        msg_owner.content = "I am shawshaw"
        msg_owner.mentions = [owner_user]
        self.assertFalse(cog.is_shawshaw_mention(msg_owner))

        # Case 4: Bot mentions shawshaw -> False
        msg_bot = MagicMock()
        msg_bot.author.bot = True
        msg_bot.author.id = 88888
        msg_bot.content = "shawshaw alert"
        msg_bot.mentions = []
        self.assertFalse(cog.is_shawshaw_mention(msg_bot))

    def test_shawshaw_mention_sound_cooldown(self):
        cog = response_cog(bot=MagicMock())
        cog.owner_id = 12345
        cog.play_mention_sound = MagicMock()

        msg = MagicMock()
        msg.author.bot = False
        msg.author.id = 99999
        msg.author.display_name = "Alice"
        msg.content = "hey shawshaw"
        msg.channel = "general"
        msg.mentions = []

        # First trigger -> sound played
        self.assertTrue(cog.handle_shawshaw_mention(msg))
        self.assertEqual(cog.play_mention_sound.call_count, 1)

        # Immediate second trigger -> sound cooldowned (call_count stays 1)
        self.assertTrue(cog.handle_shawshaw_mention(msg))
        self.assertEqual(cog.play_mention_sound.call_count, 1)


if __name__ == "__main__":
    unittest.main()
