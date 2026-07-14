import unittest
from types import SimpleNamespace

from trpg.balance import AREA_FIXED_SHOPS
from trpg.inns import AREA_INNS, INN_ROOMS, SHOP_EMOJIS, apply_inn_room, area_inn_config, inn_room_cost
from trpg.views.main_menu import select_main_menu_quests


class TestRegionalInnsAndMainMenu(unittest.TestCase):
    def test_every_fixed_shop_area_has_a_distinct_inn(self):
        self.assertEqual(set(AREA_INNS), set(AREA_FIXED_SHOPS))
        self.assertEqual(set(SHOP_EMOJIS), set(AREA_FIXED_SHOPS))
        self.assertEqual(len(SHOP_EMOJIS.values()), len(set(SHOP_EMOJIS.values())))
        emojis = [cfg["emoji"] for cfg in AREA_INNS.values()]
        self.assertEqual(len(emojis), len(set(emojis)))
        for area_id, cfg in AREA_INNS.items():
            self.assertTrue(cfg["intro_zh"], area_id)
            self.assertTrue(cfg["intro_en"], area_id)
            self.assertGreaterEqual(len(cfg["events"]), 2, area_id)
            for event in cfg["events"]:
                self.assertTrue(event["zh"])
                self.assertTrue(event["en"])

    def test_misty_forest_inn_teaches_the_fire_counter(self):
        forest = area_inn_config("area_05forest")
        combined_zh = " ".join(event["zh"] for event in forest["events"])
        combined_en = " ".join(event["en"] for event in forest["events"])
        self.assertIn("迷霧林王", combined_zh)
        self.assertIn("高大", combined_zh)
        self.assertIn("怕火", combined_zh)
        self.assertIn("Mistwood King", combined_en)
        self.assertIn("fire", combined_en.lower())

    def test_room_costs_scale_with_level_and_restore_is_not_always_full(self):
        self.assertEqual(set(INN_ROOMS), {"cot", "room", "suite"})
        self.assertEqual(INN_ROOMS["cot"]["restore_pct"], 0.35)
        self.assertEqual(INN_ROOMS["room"]["restore_pct"], 0.70)
        self.assertEqual(INN_ROOMS["suite"]["restore_pct"], 1.0)
        self.assertLess(inn_room_cost("cot", 50), inn_room_cost("room", 50))
        self.assertLess(inn_room_cost("room", 50), inn_room_cost("suite", 50))
        self.assertGreater(inn_room_cost("suite", 50), inn_room_cost("suite", 5))

    def test_room_recovery_and_cleansing_match_the_menu(self):
        cot_player = SimpleNamespace(current_hp=10, max_hp=100, current_mp=10, max_mp=100, status_effects={"poison": {}, "burn": {}})
        self.assertEqual(apply_inn_room(cot_player, "cot"), (35, 35, 0))
        self.assertEqual(set(cot_player.status_effects), {"poison", "burn"})

        room_player = SimpleNamespace(current_hp=10, max_hp=100, current_mp=10, max_mp=100, status_effects={"poison": {}, "burn": {}})
        self.assertEqual(apply_inn_room(room_player, "room", choose_status=lambda statuses: "poison"), (70, 70, 1))
        self.assertEqual(set(room_player.status_effects), {"burn"})

        suite_player = SimpleNamespace(current_hp=10, max_hp=100, current_mp=10, max_mp=100, status_effects={"poison": {}, "burn": {}})
        self.assertEqual(apply_inn_room(suite_player, "suite"), (90, 90, 2))
        self.assertEqual(suite_player.status_effects, {})

    def test_main_menu_missions_prioritize_main_then_daily(self):
        quests = {
            "side_a": {"title": "支線甲", "title_en": "Side A", "quest_line": "side"},
            "daily": {"title": "每日", "title_en": "Daily", "repeatable": True},
            "main": {"title": "主線", "title_en": "Main", "quest_line": "main"},
            "side_b": {"title": "支線乙", "title_en": "Side B", "quest_line": "side"},
        }
        selected, remaining = select_main_menu_quests(
            {"side_a": {}, "daily": {}, "main": {}, "side_b": {}}, quests
        )
        self.assertEqual(selected, ["main", "daily"])
        self.assertEqual(remaining, 2)

    def test_missing_main_or_daily_falls_back_to_other_missions(self):
        quests = {
            "side_a": {"quest_line": "side"},
            "side_b": {"quest_line": "side"},
            "daily": {"repeatable": True},
        }
        selected, remaining = select_main_menu_quests(
            {"side_a": {}, "side_b": {}, "daily": {}}, quests
        )
        self.assertEqual(selected, ["side_a", "daily"])
        self.assertEqual(remaining, 1)


if __name__ == "__main__":
    unittest.main()
