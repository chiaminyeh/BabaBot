import ast
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
