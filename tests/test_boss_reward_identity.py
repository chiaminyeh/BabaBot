import unittest

from trpg.balance import BOSS_SPECIFIC_REWARDS, TOWER_MILESTONES
from trpg.stats import migrate_player_stats
import json
import os

class MockPlayer:
    def __init__(self):
        self.inventory = {}
        self.shop_state = {}
        self.shop_items = []
        self.mystery_shop_items = []
        self.skills = []
        self.equipped_skills = []
        self.level = 1
        self.current_hp = 100
        self.max_hp = 100
        self.current_mp = 50
        self.max_mp = 50
        self.base_atk = 10
        self.base_def = 10
        self.base_magic = 10
        self.base_spd = 10

class TestBossRewardIdentity(unittest.TestCase):
    def test_boss_specific_rewards(self):
        # Verify that multiple bosses have specific, distinct reward pools
        self.assertIn("goblin_chief", BOSS_SPECIFIC_REWARDS)
        self.assertIn("lich", BOSS_SPECIFIC_REWARDS)
        self.assertIn("ancient_dragon", BOSS_SPECIFIC_REWARDS)

        # Verify they aren't all just the same scroll_heal_light
        goblin_rewards = BOSS_SPECIFIC_REWARDS["goblin_chief"]
        lich_rewards = BOSS_SPECIFIC_REWARDS["lich"]

        self.assertNotEqual(goblin_rewards, lich_rewards)
        self.assertIn("bronze_sword", goblin_rewards)
        self.assertIn("void_orb", lich_rewards)

    def test_tower_milestone_99(self):
        # Verify floor 99 reward is not a generic scroll
        self.assertNotEqual(TOWER_MILESTONES[99]["item"], "scroll_power_slash")
        self.assertEqual(TOWER_MILESTONES[99]["item"], "dragon_scale_shard")

    def test_retired_scroll_migration(self):
        player = MockPlayer()
        # Add some valid items and some retired items
        player.inventory = {"health_potion": 5, "scroll_power_slash": 2, "retired_scroll": 1}
        player.shop_state = {
            "forest": {"items": ["health_potion", "retired_scroll"]}
        }
        player.shop_items = ["health_potion", "retired_scroll"]

        valid_items = {"health_potion": {}, "scroll_power_slash": {}}

        migrate_player_stats(player, valid_items)

        # Verify retired scrolls are deleted
        self.assertNotIn("retired_scroll", player.inventory)
        self.assertIn("health_potion", player.inventory)
        self.assertEqual(player.inventory["health_potion"], 5)

        # Verify shop caches are cleaned
        self.assertNotIn("retired_scroll", player.shop_state["forest"]["items"])
        self.assertNotIn("retired_scroll", player.shop_items)

if __name__ == '__main__':
    unittest.main()
