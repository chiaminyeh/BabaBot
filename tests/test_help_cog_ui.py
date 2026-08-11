import unittest

import discord

import help_cog


class HelpUiContractTests(unittest.TestCase):
    def test_presence_advertises_only_canonical_help_command(self):
        self.assertEqual("baba help", help_cog.PRESENCE_TEXT)

    def test_home_embed_is_compact_and_names_all_categories(self):
        embed = help_cog.build_help_embed("home")
        self.assertEqual("Baba Help", embed.title)
        self.assertLessEqual(len(embed.description or ""), 1000)
        rendered = (embed.description or "") + "\n" + "\n".join(
            f"{field.name}\n{field.value}" for field in embed.fields
        )
        for category in ("General", "Adventure", "Music", "Games", "Utility"):
            self.assertIn(category, rendered)

    def test_general_includes_public_tree_commands(self):
        embed = help_cog.build_help_embed("general")
        rendered = "\n".join(str(field.value) for field in embed.fields)
        self.assertIn("`/roll`", rendered)
        self.assertIn("`/ping`", rendered)
        self.assertIn("`baba help`", rendered)
        self.assertNotIn("BABA help", rendered)
        self.assertNotIn("Baba help", rendered)

    def test_categories_cover_adventure_music_games_and_utility(self):
        expected = {
            "adventure": ("`/trpg`", "`/language`"),
            "music": (
                "`baba play <song>`",
                "`baba playfirst <song>`",
                "`baba skip`",
                "`baba skipto <position>`",
                "`baba volume [0-200]`",
                "`baba silence [on|off]`",
                "`baba shuffle`",
                "`baba loop [off|single|queue]`",
                "`baba move <from> <to>`",
            ),
            "games": ("`/chess`", "`/blackjack <bet>`", "`/wordle_start`", "`baba lottery`"),
            "utility": ("`/record_schedule`", "`/list_schedules`"),
        }
        for category, commands in expected.items():
            with self.subTest(category=category):
                embed = help_cog.build_help_embed(category)
                rendered = "\n".join(str(field.value) for field in embed.fields)
                for command in commands:
                    self.assertIn(command, rendered)

    def test_music_help_warns_that_remove_current_deletes_the_local_file(self):
        embed = help_cog.build_help_embed("music")
        rendered = "\n".join(str(field.value) for field in embed.fields)
        self.assertIn("`baba remove`", rendered)
        self.assertIn("`baba remove current`", rendered)
        self.assertIn("deletes", rendered.lower())
        self.assertIn("local", rendered.lower())
        self.assertTrue(all(len(str(field.value)) <= 1024 for field in embed.fields))

    def test_view_is_persistent_with_stable_category_and_home_ids(self):
        view = help_cog.HelpView()
        self.assertIsNone(view.timeout)
        custom_ids = {item.custom_id for item in view.children}
        self.assertEqual(
            {
                "baba_help:home",
                "baba_help:general",
                "baba_help:adventure",
                "baba_help:music",
                "baba_help:games",
                "baba_help:utility",
            },
            custom_ids,
        )
        self.assertTrue(all(isinstance(item, discord.ui.Button) for item in view.children))


if __name__ == "__main__":
    unittest.main()
