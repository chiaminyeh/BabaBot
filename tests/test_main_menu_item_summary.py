import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
VIEW_SOURCE = (ROOT / "trpg" / "view.py").read_text(encoding="utf-8")
MAIN_MENU_SOURCE = (ROOT / "trpg" / "views" / "main_menu.py").read_text(encoding="utf-8")
BATTLE_MENU_SOURCE = (ROOT / "trpg" / "views" / "battle.py").read_text(encoding="utf-8")


class TestMainMenuItemSummary(unittest.TestCase):
    def test_main_panel_shows_current_experience_progress(self):
        self.assertIn('exp=p.exp', VIEW_SOURCE)
        self.assertIn('required_exp = exp_to_next_level(p.level)', VIEW_SOURCE)
        self.assertIn('required=required_exp', VIEW_SOURCE)
        self.assertIn('battle.adventurer_level_exp', VIEW_SOURCE)

    def test_status_callbacks_route_to_items_handler(self):
        self.assertIn('"btn_status": {"m": "handle_items", "i": True}', VIEW_SOURCE)
        self.assertIn('"b_sta": {"m": "handle_items", "i": True}', VIEW_SOURCE)
        self.assertIn('async def handle_items(', VIEW_SOURCE)
        self.assertNotIn('async def handle_status(', VIEW_SOURCE)

    def test_items_handler_only_builds_inventory_content(self):
        start = VIEW_SOURCE.index('async def handle_items(')
        end = VIEW_SOURCE.index('async def re_render_current_menu', start)
        handler = VIEW_SOURCE[start:end]
        self.assertIn('_format_inventory_grouped', handler)
        self.assertIn('char.items_title', handler)
        self.assertIn('char.bag_contents', handler)
        for stale_section in ('char.level_exp', 'char.wallet_balance', 'char.combat_core_stats', 'char.learned_skills', 'char.status_effects'):
            self.assertNotIn(stale_section, handler)

    def test_main_and_battle_buttons_are_labeled_items(self):
        for source in (MAIN_MENU_SOURCE, BATTLE_MENU_SOURCE):
            self.assertIn('menu.btn_items', source)
            self.assertNotIn('menu.btn_status', source)


if __name__ == "__main__":
    unittest.main()
