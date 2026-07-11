import asyncio
import os
import json
import sqlite3
import unittest
import shutil
import sys
import gc
from unittest.mock import patch

# Make sure we can import trpg components
sys.path.append(os.path.dirname(__file__))

from trpg.cog import TRPGCog
from trpg.view import TRPGGameView
from trpg.player import TRPGPlayer
from trpg.combat import execute_skill
from trpg.entity import PlayerCombatant, MonsterCombatant
from trpg.stats import migrate_player_stats, prune_unqualified_skills

class MockBot:
    class Baba:
        bank = {}
        money_name = "bababucks"
        def refresh_bank_file(self):
            pass
    baba = Baba()
    def get_cog(self, name):
        return None

class TestMultiCharacter(unittest.TestCase):
    def setUp(self):
        # Setup clean test data folder
        self.test_dir = "trpg_data_test"
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
        os.makedirs(self.test_dir)
        
        # Override DATA_DIR in cog module
        import trpg.cog
        self.orig_data_dir = trpg.cog.DATA_DIR
        trpg.cog.DATA_DIR = self.test_dir
        
        self.players_file = os.path.join(self.test_dir, "trpg_players.json")
        self.active_slots_file = os.path.join(self.test_dir, "trpg_active_slots.json")
        self.players_db_file = os.path.join(self.test_dir, "trpg_players.sqlite3")
        
        self.bot = MockBot()
        self.write_core_content()

    def tearDown(self):
        # Restore DATA_DIR
        import trpg.cog
        trpg.cog.DATA_DIR = self.orig_data_dir
        gc.collect()
        
        # Cleanup test folder
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def write_json(self, filepath, data):
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    def write_core_content(self):
        self.write_json(os.path.join(self.test_dir, "areas.json"), {
            "area_00village": {"area_name": "Village"},
            "area_01grassland": {"area_name": "Grassland"},
            "area_05forest": {"area_name": "Forest"},
            "area_40vampire_castle": {"area_name": "Vampire Castle"},
        })
        self.write_json(os.path.join(self.test_dir, "items.json"), {
            "scroll_heal_light": {"name": "Heal Scroll"},
        })
        self.write_json(os.path.join(self.test_dir, "skills.json"), {
            "fireball": {"name": "Fireball"},
        })
        self.write_json(os.path.join(self.test_dir, "monsters.json"), {
            "slime": {"name": "Slime", "max_hp": 10, "atk": 1},
        })

    def read_db_counts(self):
        with sqlite3.connect(self.players_db_file) as conn:
            players = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
            active_slots = conn.execute("SELECT COUNT(*) FROM active_slots").fetchone()[0]
        return players, active_slots

    def read_player_row(self, user_id, slot):
        with sqlite3.connect(self.players_db_file) as conn:
            return conn.execute(
                "SELECT level, current_area, prestige_count, language FROM players WHERE user_id = ? AND slot = ?",
                (str(user_id), str(slot)),
            ).fetchone()

    def test_startup_validation_fails_when_required_core_content_missing(self):
        os.remove(os.path.join(self.test_dir, "areas.json"))

        with self.assertRaisesRegex(RuntimeError, "TRPG startup validation failed: areas.json missing"):
            TRPGCog(self.bot)

    def test_legacy_saves_migration(self):
        # 1. Test that legacy saves (keyed by uid) migrate to uid_0
        legacy_data = {
            "11111": {
                "id": "11111",
                "level": 5,
                "current_area": "area_01grassland"
            }
        }
        self.write_json(self.players_file, legacy_data)
        
        cog = TRPGCog(self.bot)
        
        self.assertIn("11111_0", cog.players)
        p = cog.players["11111_0"]
        self.assertEqual(p.id, "11111")
        self.assertEqual(p.character_slot, "0")
        self.assertEqual(p.level, 5)
        self.assertTrue(os.path.exists(self.players_db_file))

    def test_explicit_slot_loading(self):
        # 2. Test explicit slot keys (uid_0, uid_1) loading
        explicit_data = {
            "22222_0": {
                "id": "22222",
                "level": 10,
                "character_slot": "0"
            },
            "22222_1": {
                "id": "22222",
                "level": 20,
                "character_slot": "1"
            }
        }
        self.write_json(self.players_file, explicit_data)
        
        cog = TRPGCog(self.bot)
        
        self.assertIn("22222_0", cog.players)
        self.assertIn("22222_1", cog.players)
        self.assertEqual(cog.players["22222_0"].level, 10)
        self.assertEqual(cog.players["22222_1"].level, 20)

    def test_collision_precedence(self):
        # 3. Test duplicate keys: if legacy key "33333" AND explicit key "33333_0" exist,
        # prefer explicit key "33333_0" regardless of order.
        collision_data = {
            "33333": {
                "id": "33333",
                "level": 1,
                "current_area": "area_00village"
            },
            "33333_0": {
                "id": "33333",
                "level": 15,
                "current_area": "area_05forest"
            }
        }
        self.write_json(self.players_file, collision_data)
        
        cog = TRPGCog(self.bot)
        
        self.assertIn("33333_0", cog.players)
        p = cog.players["33333_0"]
        # Explicit key data should overwrite/precede legacy data
        self.assertEqual(p.level, 15)
        self.assertEqual(p.current_area, "area_05forest")

    def test_active_slots_normalization(self):
        # 4. Test active slots normalization (keys/values normalized to strings, invalid slots filtered)
        player_data = {
            "44444_1": {
                "id": "44444",
                "level": 1,
                "character_slot": "1"
            },
            "55555_2": {
                "id": "55555",
                "level": 1,
                "character_slot": "2"
            }
        }
        self.write_json(self.players_file, player_data)

        active_slots_raw = {
            44444: 1,        # Should convert key to "44444" and value to "1"
            "55555": 2,      # Should convert value to "2"
            "66666": 99,     # Invalid slot, should be filtered out
            "77777": "abc"   # Invalid slot, should be filtered out
        }
        self.write_json(self.active_slots_file, active_slots_raw)
        
        cog = TRPGCog(self.bot)
        
        self.assertEqual(cog.active_slots.get("44444"), "1")
        self.assertEqual(cog.active_slots.get("55555"), "2")
        self.assertNotIn("66666", cog.active_slots)
        self.assertNotIn("77777", cog.active_slots)

    def test_get_player_defensive_fallback(self):
        # 5. Test get_player fallback logic when active slot is missing
        # If user is configured for slot "1" but only slot "0" exists in players,
        # get_player should automatically fallback to slot "0" and update the configuration.
        player_data = {
            "88888_0": {
                "id": "88888",
                "level": 30,
                "character_slot": "0"
            }
        }
        active_slots_data = {
            "88888": "1" # Slot "1" is active, but missing
        }
        self.write_json(self.players_file, player_data)
        self.write_json(self.active_slots_file, active_slots_data)
        
        cog = TRPGCog(self.bot)
        player = cog.get_player(88888)
        
        # Should fallback to slot "0" player
        self.assertEqual(player.character_slot, "0")
        self.assertEqual(player.level, 30)
        self.assertEqual(cog.active_slots["88888"], "0")

    def test_saving_and_active_slots_persistence(self):
        # 6. Test save/load persistence affects the character loaded
        cog = TRPGCog(self.bot)
        
        # Create Slot 0 and Slot 1 characters for "99999"
        cog.active_slots["99999"] = "0"
        p0 = cog.get_player("99999")
        p0.level = 12
        cog.save_players(player=p0, active_slot_user_id="99999")
        
        cog.active_slots["99999"] = "1"
        p1 = cog.get_player("99999")
        p1.level = 24
        cog.save_players(player=p1, active_slot_user_id="99999")
        
        # Save players and slots
        cog.save_players()
        players_count, active_count = self.read_db_counts()
        self.assertEqual(players_count, 2)
        self.assertEqual(active_count, 1)
        
        # Create a fresh cog to reload from file
        cog2 = TRPGCog(self.bot)
        
        # Check active slot is still "1"
        self.assertEqual(cog2.active_slots.get("99999"), "1")
        
        # Retrieve player (should get Slot 1 / level 24)
        p = cog2.get_player("99999")
        self.assertEqual(p.character_slot, "1")
        self.assertEqual(p.level, 24)
        
        # Switch to Slot 0
        cog2.active_slots["99999"] = "0"
        p_switch = cog2.get_player("99999")
        self.assertEqual(p_switch.character_slot, "0")
        self.assertEqual(p_switch.level, 12)
        del p_switch, p, p0, p1, cog2, cog

    def test_db_boot_precedence_over_json(self):
        legacy_data = {
            "12121_0": {
                "id": "12121",
                "level": 7,
                "character_slot": "0"
            }
        }
        self.write_json(self.players_file, legacy_data)

        cog = TRPGCog(self.bot)
        cog.players["12121_0"].level = 42
        cog.save_players(player=cog.players["12121_0"])

        self.write_json(self.players_file, {
            "12121_0": {
                "id": "12121",
                "level": 1,
                "character_slot": "0"
            }
        })

        cog2 = TRPGCog(self.bot)
        self.assertEqual(cog2.players["12121_0"].level, 42)

    def test_incremental_player_upsert_updates_summary_columns(self):
        cog = TRPGCog(self.bot)
        player = cog.get_player("45454")
        player.level = 33
        player.current_area = "area_40vampire_castle"
        player.prestige_count = 2
        player.language = "en"

        cog.save_players(player=player, active_slot_user_id="45454")

        row = self.read_player_row("45454", "0")
        self.assertEqual(row, (33, "area_40vampire_castle", 2, "en"))

    def test_deleted_player_removed_from_db(self):
        cog = TRPGCog(self.bot)
        player = cog.get_player("56565")
        cog.save_players(player=player, active_slot_user_id="56565")
        self.assertEqual(self.read_db_counts()[0], 1)

        del cog.players["56565_0"]
        cog.mark_player_deleted("56565_0")
        cog.save_players(active_slot_user_id="56565")

        with sqlite3.connect(self.players_db_file) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM players WHERE user_id = ? AND slot = ?",
                ("56565", "0"),
            ).fetchone()[0]
        self.assertEqual(row, 0)

    def test_legacy_stat_alloc_migrates_to_archetype_points(self):
        player = TRPGPlayer("77777")
        player.level = 10
        player.stat_alloc = {"atk": 6, "vit": 4, "int": 5, "spd": 3, "luck": 2, "res": 9}

        migrate_player_stats(player, {})

        self.assertEqual(player.stat_alloc["knight"], 10)
        self.assertEqual(player.stat_alloc["mage"], 5)
        self.assertEqual(player.stat_alloc["rogue"], 3)
        self.assertEqual(player.stat_alloc["luck"], 2)
        self.assertEqual(player.stat_alloc["warlock"], 0)

    def test_prune_unqualified_skills_removes_invalid_learned_and_equipped_skills(self):
        player = TRPGPlayer("78787")
        player.level = 20
        player.stat_alloc = {"knight": 0, "rogue": 0, "mage": 0, "warlock": 0, "luck": 0}
        player.skills = ["shield_bash", "fireball"]
        player.equipped_skills = ["shield_bash", "fireball"]
        skills = {
            "shield_bash": {"req_level": 2, "req_points": {"knight": 10}},
            "fireball": {"req_level": 1, "req_points": {"mage": 10}},
        }

        removed = prune_unqualified_skills(player, skills)

        self.assertCountEqual(removed, ["shield_bash", "fireball"])
        self.assertEqual(player.skills, [])
        self.assertEqual(player.equipped_skills, [])

    def test_area_id_migration_and_invalid_area_fallback_persist(self):
        self.write_json(os.path.join(self.test_dir, "areas.json"), {
            "area_00village": {"area_name": "Village"},
            "area_05forest": {"area_name": "Forest"},
        })
        self.write_json(self.players_file, {
            "834715610536869978_0": {
                "id": "834715610536869978",
                "character_slot": "0",
                "current_area": "area_forest",
            },
            "99900_0": {
                "id": "99900",
                "character_slot": "0",
                "current_area": "area_deleted",
                "current_subarea": "old_subarea",
            },
        })

        cog = TRPGCog(self.bot)

        self.assertEqual(cog.players["834715610536869978_0"].current_area, "area_05forest")
        self.assertEqual(cog.players["99900_0"].current_area, "area_00village")
        self.assertIsNone(cog.players["99900_0"].current_subarea)
        with sqlite3.connect(self.players_db_file) as conn:
            rows = dict(conn.execute("SELECT user_id, current_area FROM players"))
        self.assertEqual(rows["834715610536869978"], "area_05forest")
        self.assertEqual(rows["99900"], "area_00village")

    def test_invalid_move_target_does_not_write_bad_area(self):
        self.write_json(os.path.join(self.test_dir, "areas.json"), {
            "area_00village": {"area_name": "Village"},
            "area_05forest": {"area_name": "Forest"},
        })
        cog = TRPGCog(self.bot)
        player = cog.get_player("123456")
        player.onboarding_done = True
        player.current_area = "area_00village"
        cog.save_players(player=player, active_slot_user_id="123456")
        original_save = cog.save_players
        save_calls = []

        def tracked_save(*args, **kwargs):
            save_calls.append((args, kwargs))
            return original_save(*args, **kwargs)

        cog.save_players = tracked_save
        view = TRPGGameView(cog, "123456")
        save_calls.clear()

        asyncio.run(view.handle_move_execute("move_to_area_missing"))

        self.assertEqual(player.current_area, "area_00village")
        self.assertIn("目的地資料不存在", view.log_message)
        self.assertEqual(save_calls, [])

    def test_build_main_menu_does_not_clear_active_battle(self):
        cog = TRPGCog(self.bot)
        view = TRPGGameView(cog, "24680")
        view.in_battle = True
        view.monster_slots = [{"monster": {"id": "slime", "name": "Slime", "max_hp": 10}, "hp": 10, "av": 0, "status": {}}]

        view.build_main_menu()

        self.assertTrue(view.in_battle)
        self.assertEqual(view.monster_slots[0]["monster"]["id"], "slime")
        self.assertTrue(view._blocked_during_battle("btn_back_main"))
        self.assertFalse(view._blocked_during_battle("b_atk"))
        self.assertFalse(view._blocked_during_battle("skill_fireball"))

    def test_daily_boss_lock_preserved_and_written_on_kill(self):
        self.write_json(os.path.join(self.test_dir, "areas.json"), {
            "area_00village": {"area_name": "Village", "boss": {"id": "boss_village", "is_boss": True}},
        })
        self.write_json(os.path.join(self.test_dir, "items.json"), {
            "scroll_heal_light": {"name": "Heal Scroll"},
        })
        cog = TRPGCog(self.bot)
        player = cog.get_player("778899")
        player.onboarding_done = True
        player.current_area = "area_00village"
        today = cog.players["778899_0"].last_stamina_refresh
        player.daily_boss_kills = {
            "area_00village": today,
            "area_old": "1999-01-01",
        }
        view = TRPGGameView(cog, "778899")
        today = view._today_str()
        player.daily_boss_kills = {
            "area_00village": today,
            "area_old": "1999-01-01",
        }

        changed = view._refresh_daily_stamina()

        self.assertTrue(changed)
        self.assertEqual(player.daily_boss_kills, {"area_00village": today})
        player.killed_bosses = []
        log = view.combat._handle_boss_kill_rewards({"id": "boss_village", "is_boss": True, "drops": {"scroll_heal_light": 1.0}})
        self.assertEqual(player.daily_boss_kills["area_00village"], today)
        self.assertIn("scroll_heal_light", player.inventory)
        self.assertIn("首殺", log)

    def test_elemental_immunity_not_raised_to_one_by_status_synergy(self):
        cog = TRPGCog(self.bot)
        player = cog.get_player("90001")
        player.base_atk = 100
        player.base_luck = 0
        player.equipped_skills = ["status_hunter"]
        cog.skills["status_hunter"] = {"type": "passive", "status_target_damage_mult": 3.0}
        slot = {
            "monster": {"id": "fire_blob", "name": "Fire Blob", "max_hp": 20, "def": 0, "immunity": ["fire"]},
            "hp": 20,
            "status": {"poison": {"turns": 2}},
        }

        with patch("trpg.combat.random.uniform", return_value=1.0), patch("trpg.combat.random.random", return_value=1.0):
            _log, total_dmg = execute_skill(
                PlayerCombatant(player, cog.items, cog.status_effects, cog.skills),
                [MonsterCombatant(slot, cog.status_effects, "zh")],
                {"type": "physical", "element": "fire", "power_multiplier": 1.0},
                cog.status_effects,
            )

        self.assertEqual(total_dmg, 0)
        self.assertEqual(slot["hp"], 20)

    def test_combo_attack_does_not_overflow_to_next_monster_with_old_multiplier(self):
        cog = TRPGCog(self.bot)
        cog.items["fire_sword"] = {"name": "Fire Sword", "element": "fire"}
        cog.skills["combo_passive"] = {"type": "passive", "extra_attack_multiplier": 1.0}
        player = cog.get_player("90002")
        player.onboarding_done = True
        player.weapon = "fire_sword"
        player.base_atk = 20
        player.base_luck = 0
        player.equipped_skills = ["combo_passive"]
        view = TRPGGameView(cog, "90002")
        view.start_combat([
            {"id": "dry_leaf", "name": "Dry Leaf", "max_hp": 5, "atk": 1, "def": 0, "weakness": ["fire"]},
            {"id": "fire_spirit", "name": "Fire Spirit", "max_hp": 30, "atk": 1, "def": 0, "immunity": ["fire"]},
        ])

        with patch("trpg.combat.random.uniform", return_value=1.0), patch("trpg.combat.random.random", return_value=1.0):
            log = view.combat.player_attack()

        self.assertEqual(view.monster_slots[0]["hp"], 0)
        self.assertEqual(view.monster_slots[1]["hp"], 30)
        self.assertNotIn("再補了一擊", log)

    def test_dungeon_flee_clears_dungeon_buffs_and_relic_effects(self):
        cog = TRPGCog(self.bot)
        player = cog.get_player("90003")
        player.onboarding_done = True
        player.current_area = "area_dungeon"
        player.dungeon_state = {"in_run": True, "floor": 5, "choices": ["left"]}
        player.dungeon_buffs = {"atk_mult": 2.0}
        player.dungeon_relic_effects = {"lifesteal": 0.2}
        view = TRPGGameView(cog, "90003")
        view.start_combat([{"id": "dg_slime", "name": "DG Slime", "max_hp": 10, "atk": 1, "def": 0, "is_dungeon": True}])

        was_dungeon = view.combat._end_combat_via_flee()

        self.assertTrue(was_dungeon)
        self.assertEqual(player.dungeon_buffs, {})
        self.assertEqual(player.dungeon_relic_effects, {})
        self.assertFalse(player.dungeon_state["in_run"])
        self.assertEqual(player.dungeon_state["floor"], 1)
        self.assertEqual(player.current_area, "area_00village")
        self.assertFalse(view.in_battle)
        self.assertEqual(view.monster_slots, [])

    def test_wrong_cure_item_does_not_consume_or_advance_turn(self):
        cog = TRPGCog(self.bot)
        cog.items["antidote"] = {"name": "Antidote", "cures": ["poison"]}
        player = cog.get_player("90004")
        player.inventory["antidote"] = 1
        player.status_effects = {"burn": {"turns": 2}}
        view = TRPGGameView(cog, "90004")
        view.start_combat([{"id": "slime", "name": "Slime", "max_hp": 10, "atk": 1, "def": 0}])
        view.combat.player_av = 77

        log = view.combat.use_cure_item("antidote")

        self.assertIn("目前沒有", log)
        self.assertEqual(player.inventory["antidote"], 1)
        self.assertIn("burn", player.status_effects)
        self.assertEqual(view.combat.player_av, 77)

    def test_wild_flee_text_does_not_claim_return_to_village(self):
        cog = TRPGCog(self.bot)
        player = cog.get_player("90005")
        player.onboarding_done = True
        player.current_area = "area_05forest"
        player.base_spd = 999
        view = TRPGGameView(cog, "90005")
        view.start_combat([{"id": "boar", "name": "Boar", "max_hp": 10, "atk": 1, "def": 0, "spd": 1}])

        with patch("trpg.combat.random.random", return_value=0.0):
            log = view.combat.attempt_flee()

        self.assertIn("成功脫離", log)
        self.assertNotIn("村", log)
        self.assertEqual(player.current_area, "area_05forest")

if __name__ == "__main__":
    unittest.main()
