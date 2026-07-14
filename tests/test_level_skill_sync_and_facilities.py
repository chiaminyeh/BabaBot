import unittest
from pathlib import Path
from types import SimpleNamespace

from trpg.player import TRPGPlayer
from trpg.quest_popup import _grant_quest_rewards
from trpg.stats import grant_qualified_skills


ROOT = Path(__file__).parents[1]


class LevelSkillSyncAndFacilitiesTests(unittest.TestCase):
    def setUp(self):
        self.skills = {
            "shield_bash": {"req_level": 5, "req_points": {"knight": 10}},
            "scroll_only": {"req_level": 1},
            "too_high": {"req_level": 20, "req_points": {"knight": 10}},
        }

    def test_qualified_archetype_skill_is_learned_and_auto_equipped(self):
        player = SimpleNamespace(
            level=10,
            stat_alloc={"knight": 10},
            skills=[],
            equipped_skills=[],
        )
        granted = grant_qualified_skills(player, self.skills)
        self.assertEqual(granted, [("shield_bash", True)])
        self.assertEqual(player.skills, ["shield_bash"])
        self.assertEqual(player.equipped_skills, ["shield_bash"])

    def test_generic_scroll_skill_is_not_auto_granted_and_full_bar_is_preserved(self):
        equipped = [f"skill_{i}" for i in range(8)]
        player = SimpleNamespace(
            level=10,
            stat_alloc={"knight": 10},
            skills=list(equipped),
            equipped_skills=list(equipped),
        )
        granted = grant_qualified_skills(player, self.skills)
        self.assertEqual(granted, [("shield_bash", False)])
        self.assertIn("shield_bash", player.skills)
        self.assertEqual(player.equipped_skills, equipped)
        self.assertNotIn("scroll_only", player.skills)

    def test_add_exp_checks_newly_qualified_skills(self):
        player = TRPGPlayer("test")
        player.level = 4
        player.exp = 0
        player.stat_alloc["knight"] = 10
        needed = __import__("trpg.combat", fromlist=["exp_to_next_level"]).exp_to_next_level(player.level)
        leveled = player.add_exp(needed, items={}, skills_data=self.skills)
        self.assertTrue(leveled)
        self.assertEqual(player.level, 5)
        self.assertIn("shield_bash", player.skills)
        self.assertIn("shield_bash", player.equipped_skills)

    def test_facilities_directly_contains_blacksmith_and_crafting_station(self):
        source = (ROOT / "trpg" / "views" / "main_menu.py").read_text(encoding="utf-8")
        self.assertNotIn("def build_artisan_menu", source)
        facilities = source.split("def build_village_facilities_menu(view):", 1)[1].split("    @staticmethod", 1)[0]
        self.assertIn('custom_id="btn_blacksmith_menu"', facilities)
        self.assertIn('custom_id="btn_craft_menu"', facilities)
        self.assertIn('menu.btn_crafting_station', facilities)
        self.assertNotIn('custom_id="btn_artisan_menu"', facilities)

    def test_quest_rewards_and_arrival_have_localized_level_notice(self):
        quest_source = (ROOT / "trpg" / "quest_popup.py").read_text(encoding="utf-8")
        view_source = (ROOT / "trpg" / "view.py").read_text(encoding="utf-8")
        self.assertIn("quest.level_up_notice", quest_source)
        self.assertIn("quest.skills_unlocked_notice", quest_source)
        self.assertIn('"成功抵達【{area_name}】。"', view_source)
        self.assertNotIn('"You successfully arrived at {area_name}."', view_source)

    def test_quest_level_up_reports_and_equips_shield_bash(self):
        player = TRPGPlayer("quest-test")
        player.level = 4
        player.stat_alloc["knight"] = 10
        needed = __import__("trpg.combat", fromlist=["exp_to_next_level"]).exp_to_next_level(player.level)
        player.exp = needed - 1
        cog = SimpleNamespace(
            items={},
            skills={"shield_bash": {"name": "🛡️ 盾擊", "name_en": "🛡️ Shield Bash", "req_level": 5, "req_points": {"knight": 10}}},
            adjust_bank=lambda *_: None,
        )
        view = SimpleNamespace(player=player, cog=cog, user_id=player.id)
        notice = _grant_quest_rewards(view, {"reward_exp": 1, "reward_money": 0})
        self.assertEqual(player.level, 5)
        self.assertIn("盾擊", notice)
        self.assertIn("升到了", notice)
        self.assertIn("自動裝備", notice)
        self.assertIn("shield_bash", player.equipped_skills)


if __name__ == "__main__":
    unittest.main()
