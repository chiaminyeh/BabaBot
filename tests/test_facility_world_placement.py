import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from trpg.views.main_menu import npc_progress_state
from trpg.view_shop import blacksmith_accessible, crafting_station_unlocked
from trpg.views.char import prestige_hall_accessible


ROOT = Path(__file__).parents[1]
MAIN_MENU_SOURCE = (ROOT / "trpg" / "views" / "main_menu.py").read_text(encoding="utf-8")


class FacilityWorldPlacementTests(unittest.TestCase):
    def test_village_layout_has_no_outskirts_shortcut(self):
        village = MAIN_MENU_SOURCE.split('if view.cog.areas.get(view.player.current_area, {}).get("is_village"):', 1)[1].split("        else:", 1)[0]
        self.assertNotIn('custom_id="move_to_area_01grassland"', village)
        self.assertIn('custom_id="btn_move_menu", row=0', village)
        self.assertIn('custom_id="btn_status", row=0', village)
        self.assertIn('custom_id="btn_equip_menu", row=0', village)
        self.assertIn('custom_id="btn_stat_alloc", row=1', village)
        self.assertIn('custom_id="btn_school_menu", row=2', village)
        self.assertNotIn('custom_id="btn_village_facilities"', village)

    def test_character_management_is_in_guild(self):
        guild = MAIN_MENU_SOURCE.split("def build_guild_menu(view):", 1)[1].split("    @staticmethod", 1)[0]
        self.assertIn('custom_id="btn_char_menu"', guild)

    def test_world_facilities_are_on_the_requested_areas(self):
        self.assertIn('view.player.current_area == "area_20lab"', MAIN_MENU_SOURCE)
        self.assertIn('custom_id="btn_blacksmith_menu"', MAIN_MENU_SOURCE)
        self.assertIn('view.player.current_area == "area_40vampire_castle"', MAIN_MENU_SOURCE)
        self.assertIn('custom_id="btn_prestige_menu"', MAIN_MENU_SOURCE)

    def test_elune_crafting_station_requires_both_quests(self):
        areas = json.loads((ROOT / "trpg_data" / "areas.json").read_text(encoding="utf-8"))
        npc = areas["area_05forest"]["npc"]
        self.assertEqual(npc["craft_unlock_requires"], ["npc_elune_1", "npc_elune_2"])

        key, unlocked = npc_progress_state(npc, set())
        self.assertEqual((key, unlocked), ("dialogue_before", False))
        key, unlocked = npc_progress_state(npc, {"npc_elune_1"})
        self.assertEqual((key, unlocked), ("dialogue_after_first", False))
        key, unlocked = npc_progress_state(npc, {"npc_elune_1", "npc_elune_2"})
        self.assertEqual((key, unlocked), ("dialogue_complete", True))

        for key in ("dialogue_before", "dialogue_after_first", "dialogue_complete"):
            self.assertTrue(npc[key])
            self.assertTrue(npc[f"{key}_en"])

    def test_crafting_button_is_added_only_for_unlocked_npc(self):
        handler = MAIN_MENU_SOURCE.split("def handle_area_npc(view, notice=\"\"):", 1)[1].split("    @staticmethod", 1)[0]
        self.assertIn('custom_id="btn_craft_menu"', handler)
        self.assertIn("craft_unlocked", handler)

    def test_stale_buttons_cannot_bypass_world_requirements(self):
        player = SimpleNamespace(current_area="area_00village", completed_quests=[])
        self.assertFalse(crafting_station_unlocked(player))
        self.assertFalse(blacksmith_accessible(player))
        self.assertFalse(prestige_hall_accessible(player))

        player.current_area = "area_05forest"
        player.completed_quests = ["npc_elune_1"]
        self.assertFalse(crafting_station_unlocked(player))
        player.completed_quests.append("npc_elune_2")
        self.assertTrue(crafting_station_unlocked(player))

        player.current_area = "area_20lab"
        self.assertTrue(blacksmith_accessible(player))
        self.assertFalse(crafting_station_unlocked(player))

        player.current_area = "area_40vampire_castle"
        self.assertTrue(prestige_hall_accessible(player))


if __name__ == "__main__":
    unittest.main()
