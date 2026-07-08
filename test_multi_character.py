import os
import json
import unittest
import shutil
import sys

# Make sure we can import trpg components
sys.path.append(os.path.dirname(__file__))

from trpg.cog import TRPGCog
from trpg.player import TRPGPlayer

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
        
        self.bot = MockBot()

    def tearDown(self):
        # Restore DATA_DIR
        import trpg.cog
        trpg.cog.DATA_DIR = self.orig_data_dir
        
        # Cleanup test folder
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def write_json(self, filepath, data):
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

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
        
        cog.active_slots["99999"] = "1"
        p1 = cog.get_player("99999")
        p1.level = 24
        
        # Save players and slots
        cog.save_players()
        
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

if __name__ == "__main__":
    unittest.main()
