import json
import unittest
from collections import Counter
from pathlib import Path

from trpg.balance import ARCHETYPE_BALANCE_VERSION, LUCK_CRIT_BONUS_PER_POINT
from trpg.player import TRPGPlayer


ROOT = Path(__file__).resolve().parents[1]


class ArchetypeOverhaulTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skills = json.loads((ROOT / "trpg_data" / "skills.json").read_text(encoding="utf-8"))

    def test_combat_archetypes_have_equal_rosters(self):
        counts = Counter()
        for skill in self.skills.values():
            requirements = skill.get("req_points") or {}
            category = next(iter(requirements), "common")
            counts[category] += 1

        self.assertEqual(
            {name: counts[name] for name in ("knight", "rogue", "mage", "warlock")},
            {"knight": 8, "rogue": 8, "mage": 8, "warlock": 8},
        )
        self.assertEqual(counts["common"], 6)
        self.assertEqual(counts["luck"], 0)

    def test_all_archetypes_share_the_same_unlock_cadence(self):
        expected = [10, 10, 18, 18, 25, 25, 50, 80]
        for archetype in ("knight", "rogue", "mage", "warlock"):
            actual = sorted(
                skill["req_points"][archetype]
                for skill in self.skills.values()
                if archetype in (skill.get("req_points") or {})
            )
            self.assertEqual(actual, expected, archetype)

    def test_luck_is_progression_first(self):
        self.assertEqual(LUCK_CRIT_BONUS_PER_POINT, 0.001)
        self.assertNotIn("luck", self.skills["status_hunter"]["req_points"])
        self.assertEqual(self.skills["status_hunter"]["req_points"], {"rogue": 25})

    def test_existing_players_receive_one_free_respec_marker(self):
        old_player = TRPGPlayer.from_dict({"id": "old", "level": 30})
        new_player = TRPGPlayer("new")
        self.assertEqual(old_player.archetype_balance_version, 0)
        self.assertEqual(new_player.archetype_balance_version, ARCHETYPE_BALANCE_VERSION)

    def test_village_menu_and_grouped_facilities_are_wired(self):
        source = (ROOT / "trpg" / "views" / "main_menu.py").read_text(encoding="utf-8")
        village_branch = source.split('if view.cog.areas.get(view.player.current_area, {}).get("is_village"):', 1)[1].split("        else:", 1)[0]
        self.assertEqual(village_branch.count("view.add_action_button("), 8)
        self.assertIn('custom_id="btn_village_facilities"', village_branch)
        self.assertNotIn('custom_id="btn_rest"', village_branch)

        facilities = source.split("def build_village_facilities_menu(view):", 1)[1].split("    @staticmethod", 1)[0]
        for custom_id in ("btn_rest", "btn_artisan_menu", "btn_church_menu", "btn_school_menu", "btn_back_main"):
            self.assertIn(f'custom_id="{custom_id}"', facilities)

        routes = (ROOT / "trpg" / "view.py").read_text(encoding="utf-8")
        self.assertIn('"btn_village_facilities": {"m": "build_village_facilities_menu"', routes)

if __name__ == "__main__":
    unittest.main()
