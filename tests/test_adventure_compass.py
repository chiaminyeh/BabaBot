import unittest
from types import SimpleNamespace
from unittest.mock import patch

from trpg.player import TRPGPlayer
import trpg.view as view_module
import trpg.views.main_menu as main_menu


class CompassView:
    def __init__(self, *, language="zh", quests=None, areas=None, items=None):
        self.player = TRPGPlayer("compass")
        self.player.language = language
        self.player.onboarding_done = True
        self.player.current_area = "area_forest"
        self.player.daily_boss_kills = {}
        self.cog = SimpleNamespace(
            quests=quests or {},
            areas=areas or {},
            items=items or {},
            monster_pool={},
        )


class AdventureCompassTests(unittest.TestCase):
    def setUp(self):
        self.areas = {
            "area_village": {
                "area_name": "米酥村",
                "area_name_en": "MISO Village",
                "is_village": True,
            },
            "area_forest": {
                "area_name": "迷霧森林",
                "area_name_en": "Misty Forest",
                "is_village": False,
                "monsters": {
                    "wolf": {
                        "id": "wolf",
                        "name": "霧狼",
                        "name_en": "Mist Wolf",
                        "drops": {"wolf_fur": 0.5},
                    }
                },
                "boss": {"id": "forest_boss", "name": "林王", "name_en": "Forest King"},
            },
        }
        self.main_quest = {
            "title": "追查霧狼",
            "title_en": "Track the Mist Wolves",
            "quest_line": "main",
            "quest_type": "kill",
            "target_monster": "wolf",
            "target_count": 5,
        }
        self.daily_quest = {
            "title": "每日採集",
            "title_en": "Daily Gathering",
            "repeatable": True,
            "quest_type": "collect",
            "target_item": "wolf_fur",
            "target_count": 4,
        }
        self.side_quest = {
            "title": "森林巡邏",
            "title_en": "Forest Patrol",
            "quest_type": "kill",
            "target_monster": "wolf",
            "target_count": 3,
        }

    def test_unspent_stats_override_every_quest(self):
        view = CompassView(
            quests={"main": self.main_quest, "daily": self.daily_quest},
            areas=self.areas,
        )
        view.player.active_quests = {"main": {"progress": 2}, "daily": {"progress": 1}}

        name, value = main_menu.build_adventure_compass(view, unspent_points=3)

        self.assertIn("冒險指南", name)
        self.assertIn("3", value)
        self.assertIn("屬性", value)
        self.assertNotIn("追查霧狼", value)

    def test_active_main_quest_beats_daily_and_other_with_progress_and_goal(self):
        quests = {"side": self.side_quest, "daily": self.daily_quest, "main": self.main_quest}
        view = CompassView(quests=quests, areas=self.areas)
        view.player.active_quests = {
            "side": {"progress": 1},
            "daily": {"progress": 3},
            "main": {"progress": 2},
        }

        _name, value = main_menu.build_adventure_compass(view, unspent_points=0)

        self.assertIn("追查霧狼", value)
        self.assertIn("2/5", value)
        self.assertIn("迷霧森林", value)
        self.assertIn("探索", value)
        self.assertNotIn("每日採集", value)
        self.assertNotIn("森林巡邏", value)

    def test_daily_quest_beats_other_when_no_main_is_active(self):
        view = CompassView(
            quests={"side": self.side_quest, "daily": self.daily_quest},
            areas=self.areas,
        )
        view.player.active_quests = {"side": {"progress": 1}, "daily": {"progress": 2}}

        _name, value = main_menu.build_adventure_compass(view, unspent_points=0)

        self.assertIn("每日採集", value)
        self.assertIn("2/4", value)
        self.assertNotIn("森林巡邏", value)

    def test_main_panel_wrapper_recomputes_live_progress_with_existing_quest_helper(self):
        collect = dict(self.daily_quest, repeatable=False, quest_line="main")
        view = CompassView(quests={"collect": collect}, areas=self.areas, items={"wolf_fur": {"name": "狼毛"}})
        view.player.active_quests = {"collect": {"progress": 0}}
        view.player.inventory["wolf_fur"] = 3

        _name, value = main_menu.adventure_compass_for_main_panel(view, unspent_points=0)

        self.assertEqual(view.player.active_quests["collect"]["progress"], 3)
        self.assertIn("3/4", value)

    def test_no_active_quest_points_to_available_quest_hall_count(self):
        board_a = dict(self.side_quest, board=True, req_level=1)
        board_b = dict(self.daily_quest, board=True, req_level=1)
        view = CompassView(quests={"a": board_a, "b": board_b}, areas=self.areas)
        view.player.active_quests = {}

        _name, value = main_menu.build_adventure_compass(view, unspent_points=0)

        self.assertIn("2", value)
        self.assertIn("公會", value)
        self.assertIn("任務大廳", value)

    def test_final_fallback_is_current_area_explore_or_village_move(self):
        forest_view = CompassView(areas=self.areas)
        _name, forest_value = main_menu.build_adventure_compass(forest_view, unspent_points=0)
        self.assertIn("迷霧森林", forest_value)
        self.assertIn("探索", forest_value)
        self.assertIn("BOSS", forest_value)

        village_view = CompassView(areas=self.areas)
        village_view.player.current_area = "area_village"
        _name, village_value = main_menu.build_adventure_compass(village_view, unspent_points=0)
        self.assertIn("移動", village_value)

    def test_english_compass_uses_english_title_progress_and_goal(self):
        view = CompassView(language="en", quests={"main": self.main_quest}, areas=self.areas)
        view.player.active_quests = {"main": {"progress": 2}}

        name, value = main_menu.build_adventure_compass(view, unspent_points=0)

        self.assertEqual(name, "🧭 Adventure Compass")
        self.assertIn("Track the Mist Wolves", value)
        self.assertIn("2/5", value)
        self.assertIn("Misty Forest", value)
        self.assertIn("explore", value.lower())

    def test_visibility_is_only_non_battle_main_state(self):
        self.assertTrue(main_menu.should_show_adventure_compass("main", in_battle=False))
        self.assertFalse(main_menu.should_show_adventure_compass("main", in_battle=True))
        for state in ("battle", "shop", "guild", "quest_hall", "item"):
            with self.subTest(state=state):
                self.assertFalse(main_menu.should_show_adventure_compass(state, in_battle=False))

    def test_field_output_respects_discord_limits(self):
        huge = dict(self.main_quest, title="任" * 1500, title_en="Q" * 1500)
        view = CompassView(quests={"main": huge}, areas=self.areas)
        view.player.active_quests = {"main": {"progress": 1}}

        name, value = main_menu.build_adventure_compass(view, unspent_points=0)

        self.assertLessEqual(len(name), 256)
        self.assertLessEqual(len(value), 1024)

    def test_generate_embed_invokes_compass_only_for_main_non_battle_panel(self):
        player = TRPGPlayer("embed-compass")
        player.onboarding_done = True
        player.current_area = "area_forest"
        player.status_effects = {}
        player.active_quests = {}
        panel = SimpleNamespace(
            player=player,
            cog=SimpleNamespace(
                areas={"area_forest": {"area_name": "森林", "area_name_en": "Forest"}},
                items={},
                quests={},
                status_effects={},
                get_bank_balance=lambda _user_id: 0,
                bot=SimpleNamespace(baba=SimpleNamespace(money_name="Bababucks")),
            ),
            user_id="embed-compass",
            current_menu_state="main",
            in_battle=False,
            monster_slots=[],
            viewing_leaderboard=False,
            log_message="ready",
            combat=SimpleNamespace(player_ap=0, player_max_ap=1),
            _current_subarea_data=lambda _area: None,
            _format_combat_mods=lambda _player: "",
            build_leaderboard_embed=lambda: "leaderboard",
        )

        with (
            patch.object(view_module, "get_player_atk", return_value=1),
            patch.object(view_module, "get_player_def", return_value=1),
            patch.object(view_module, "get_player_magic", return_value=1),
            patch.object(view_module, "get_player_spd", return_value=1),
            patch.object(view_module, "get_unspent_points", return_value=7),
            patch.object(view_module, "get_daily_jester_immunity", return_value=None),
            patch.object(view_module, "format_blood_demon_status", return_value=""),
            patch.object(
                view_module,
                "adventure_compass_for_main_panel",
                return_value=("COMPASS_SENTINEL", "next action"),
                create=True,
            ) as compass,
        ):
            embed = view_module.TRPGGameView.generate_embed(panel)
            self.assertEqual(
                [field.name for field in embed.fields].count("COMPASS_SENTINEL"),
                1,
            )
            compass.assert_called_once_with(panel, unspent_points=7)

            for menu_state, in_battle in (("item", False), ("main", True), ("battle", False)):
                with self.subTest(menu_state=menu_state, in_battle=in_battle):
                    compass.reset_mock()
                    panel.current_menu_state = menu_state
                    panel.in_battle = in_battle
                    embed = view_module.TRPGGameView.generate_embed(panel)
                    self.assertNotIn("COMPASS_SENTINEL", [field.name for field in embed.fields])
                    compass.assert_not_called()

            compass.reset_mock()
            panel.viewing_leaderboard = True
            self.assertEqual(view_module.TRPGGameView.generate_embed(panel), "leaderboard")
            compass.assert_not_called()


if __name__ == "__main__":
    unittest.main()
