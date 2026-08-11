import asyncio
import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace

import chess_cog


ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class FakeInteractionResponse:
    def __init__(self):
        self.deferred = []
        self.messages = []

    async def defer(self, **kwargs):
        self.deferred.append(kwargs)

    async def send_message(self, content, **kwargs):
        self.messages.append((content, kwargs))


class FakeFollowup:
    def __init__(self):
        self.messages = []

    async def send(self, content, **kwargs):
        self.messages.append((content, kwargs))


class FakeInteraction:
    def __init__(self):
        self.response = FakeInteractionResponse()
        self.followup = FakeFollowup()
        self.original_edits = []
        self.user = SimpleNamespace(id=999, display_name="Private Discord Name")

    async def edit_original_response(self, **kwargs):
        self.original_edits.append(kwargs)


class ChessLinkServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_open_challenge_posts_only_casual_standard_ten_zero_form(self):
        session = FakeSession(
            FakeResponse(
                200,
                {
                    "urlWhite": "https://lichess.org/alpha/white",
                    "urlBlack": "https://lichess.org/alpha/black",
                },
            )
        )

        links = await chess_cog.request_open_challenge(session)

        self.assertEqual(links.white, "https://lichess.org/alpha/white")
        self.assertEqual(links.black, "https://lichess.org/alpha/black")
        self.assertEqual(len(session.calls), 1)
        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://lichess.org/api/challenge/open")
        self.assertEqual(
            kwargs["data"],
            {
                "rated": "false",
                "variant": "standard",
                "clock.limit": "600",
                "clock.increment": "0",
            },
        )
        self.assertGreater(kwargs["timeout"].total, 0)
        serialized = repr(kwargs["data"])
        self.assertNotIn("999", serialized)
        self.assertNotIn("Private Discord Name", serialized)

    async def test_open_challenge_rejects_bad_status_or_non_lichess_links(self):
        cases = [
            FakeResponse(503, {"urlWhite": "https://lichess.org/w", "urlBlack": "https://lichess.org/b"}),
            FakeResponse(200, {"urlWhite": "http://lichess.org/w", "urlBlack": "https://lichess.org/b"}),
            FakeResponse(200, {"urlWhite": "https://lichess.org.evil.test/w", "urlBlack": "https://lichess.org/b"}),
            FakeResponse(200, {"urlWhite": "https://user@lichess.org/w", "urlBlack": "https://lichess.org/b"}),
            FakeResponse(200, {"urlWhite": "https://lichess.org:443/w", "urlBlack": "https://lichess.org/b"}),
            FakeResponse(200, {"urlWhite": "https://lichess.org/w#secret", "urlBlack": "https://lichess.org/b"}),
            FakeResponse(200, {"urlWhite": "https://lichess.org/w", "urlBlack": None}),
        ]
        for response in cases:
            with self.subTest(status=response.status, payload=response._payload):
                with self.assertRaises(chess_cog.LichessChallengeError):
                    await chess_cog.request_open_challenge(FakeSession(response))


class ChessCommandRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_chess_is_stateless_discord_activity_launcher(self):
        creator_calls = 0

        async def must_not_create_challenge():
            nonlocal creator_calls
            creator_calls += 1
            raise AssertionError("default Discord mode must not call Lichess")

        cog = chess_cog.ChessCog(SimpleNamespace(), challenge_creator=must_not_create_challenge)
        interaction = FakeInteraction()

        await cog.chess.callback(cog, interaction)

        self.assertEqual(creator_calls, 0)
        self.assertEqual(interaction.response.deferred, [])
        self.assertEqual(len(interaction.response.messages), 1)
        content, kwargs = interaction.response.messages[0]
        self.assertIn("Chess in the Park", content)
        self.assertIn("授權", content)
        self.assertIn("authorization", content.lower())
        self.assertIn("Locked Game", content)
        self.assertIn("table code", content.lower())
        self.assertEqual(len(kwargs["view"].children), 1)
        button = kwargs["view"].children[0]
        self.assertEqual(button.style, chess_cog.discord.ButtonStyle.link)
        self.assertEqual(button.url, "https://discord.com/activities/832012774040141894")
        self.assertEqual(interaction.original_edits, [])
        self.assertEqual(interaction.followup.messages, [])

    async def test_explicit_lichess_routes_black_publicly_and_white_ephemerally(self):
        async def create_challenge():
            return chess_cog.ChallengeLinks(
                white="https://lichess.org/game/white",
                black="https://lichess.org/game/black",
            )

        cog = chess_cog.ChessCog(SimpleNamespace(), challenge_creator=create_challenge)
        interaction = FakeInteraction()

        await cog.chess.callback(cog, interaction, mode="lichess")

        self.assertEqual(interaction.response.deferred, [{"thinking": True}])
        self.assertEqual(len(interaction.original_edits), 1)
        public = interaction.original_edits[0]
        self.assertIn("Lichess", public["content"])
        self.assertIn("opponent", public["content"].lower())
        self.assertIn("third-party", public["content"].lower())
        self.assertIn("privacy", public["content"].lower())
        self.assertEqual(public["view"].children[0].url, "https://lichess.org/game/black")
        self.assertEqual(len(interaction.followup.messages), 1)
        private_content, private_kwargs = interaction.followup.messages[0]
        self.assertIn("inviter", private_content.lower())
        self.assertTrue(private_kwargs["ephemeral"])
        self.assertEqual(private_kwargs["view"].children[0].url, "https://lichess.org/game/white")

    async def test_chess_reports_bilingual_failure_without_a_second_message(self):
        async def fail():
            raise chess_cog.LichessChallengeError("bad upstream")

        cog = chess_cog.ChessCog(SimpleNamespace(), challenge_creator=fail)
        interaction = FakeInteraction()

        await cog.chess.callback(cog, interaction, mode="lichess")

        self.assertEqual(len(interaction.original_edits), 1)
        failure = interaction.original_edits[0]["content"]
        self.assertIn("建立棋局失敗", failure)
        self.assertIn("Couldn't create", failure)
        self.assertEqual(interaction.followup.messages, [])

    def test_mode_is_an_app_command_choice_with_discord_default(self):
        cog = chess_cog.ChessCog(SimpleNamespace())
        signature = inspect.signature(cog.chess.callback)
        self.assertEqual(signature.parameters["mode"].default, "discord")
        mode_parameter = next(parameter for parameter in cog.chess.parameters if parameter.name == "mode")
        self.assertEqual(
            {(choice.name, choice.value) for choice in mode_parameter.choices},
            {("Discord Activity", "discord"), ("Lichess 10+0", "lichess")},
        )


class ChessRepositorySurfaceTests(unittest.TestCase):
    def test_only_link_cog_remains_and_it_is_registered(self):
        main_source = (ROOT / "main.py").read_text(encoding="utf-8")
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        ci_source = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8").lower()

        self.assertIn("'chess_cog'", main_source)
        self.assertFalse((ROOT / "chess").exists())
        self.assertFalse((ROOT / "screen.py").exists())
        self.assertNotIn("python-chess", requirements)
        self.assertNotIn("pygame", requirements)
        self.assertNotIn("python-chess", ci_source)
        self.assertNotIn("pygame", ci_source)
        source = (ROOT / "chess_cog.py").read_text(encoding="utf-8")
        self.assertNotIn("import chess", source)
        self.assertNotIn("on_message", source)
        self.assertIn("https://discord.com/activities/832012774040141894", source)
        self.assertNotIn("LAUNCH_ACTIVITY", source)
        self.assertNotIn("create_instant_invite", source.lower())


if __name__ == "__main__":
    unittest.main()
