import json
import unittest
from pathlib import Path
from unittest.mock import patch

from trpg.combat import TRPGCombat
from trpg.player import TRPGPlayer

ROOT = Path(__file__).parents[1]


class DummyCog:
    def __init__(self):
        self.items = {}
        self.skills = {}
        self.status_effects = json.loads((ROOT / "trpg_data" / "status_effects.json").read_text(encoding="utf-8"))


class DummyView:
    def __init__(self, player, monster):
        self.player = player
        self.cog = DummyCog()
        self.monster_slots = [{"monster": monster, "hp": monster["max_hp"], "status": {}}]
        self.in_battle = True
        self.user_id = "ap-test"

    def _front_slot(self):
        return next((slot for slot in self.monster_slots if slot["hp"] > 0), None)

    @property
    def active_monster(self):
        slot = self._front_slot()
        return slot["monster"] if slot else None

    @property
    def monster_hp(self):
        slot = self._front_slot()
        return slot["hp"] if slot else 0

    @monster_hp.setter
    def monster_hp(self, value):
        slot = self._front_slot()
        if slot:
            slot["hp"] = max(0, value)

    def process_death(self, log, message):
        self.in_battle = False
        return f"{log}\n{message}"

    def build_main_menu(self):
        self.in_battle = False


class RoundAPCombatTests(unittest.TestCase):
    def make_combat(self, player_spd=200, monster_spd=100):
        player = TRPGPlayer("ap-test")
        player.onboarding_done = True
        player.max_hp = player.current_hp = 500
        player.max_mp = 100
        player.current_mp = 0
        player.base_atk = 30
        player.base_def = 0
        player.base_mdef = 0
        player.base_spd = player_spd
        player.base_int = 0
        player.stat_alloc = {"knight": 0, "rogue": 0, "mage": 0, "warlock": 0}
        monster = {"id": "training_enemy", "name": "Training Enemy", "name_en": "Training Enemy", "max_hp": 9999, "atk": 20, "def": 0, "spd": monster_spd}
        view = DummyView(player, monster)
        combat = TRPGCombat(view)
        combat.player_max_ap = combat.calculate_player_ap()
        combat.player_ap = combat.player_max_ap
        return combat, view, player

    def test_holy_talisman_deals_unmitigated_damage_and_consumes_one_ap(self):
        combat, view, player = self.make_combat(player_spd=200, monster_spd=100)
        view.cog.items["holy_talisman"] = {
            "type": "damage_item", "damage": 80, "element": "holy", "true_damage": True,
            "name": "Holy Talisman", "name_en": "Holy Talisman",
        }
        player.inventory["holy_talisman"] = 1
        view.monster_slots[0]["monster"]["def"] = 999
        with patch("trpg.combat.random.uniform", return_value=1.0), patch("trpg.combat.random.random", return_value=1.0):
            log = combat.use_damage_item("holy_talisman")
        self.assertEqual(9999 - 80, view.monster_slots[0]["hp"])
        self.assertNotIn("holy_talisman", player.inventory)
        self.assertEqual(combat.player_max_ap - 1, combat.player_ap)
        self.assertIn("Holy Talisman", log)

    def test_killing_damage_item_consumes_one_ap(self):
        combat, view, player = self.make_combat(player_spd=200, monster_spd=100)
        view.cog.items["holy_talisman"] = {
            "type": "damage_item", "damage": 80, "element": "holy", "true_damage": True,
            "name": "Holy Talisman", "name_en": "Holy Talisman",
        }
        player.inventory["holy_talisman"] = 1
        view.monster_slots[0]["hp"] = 80
        with patch.object(combat, "_process_victory", return_value=" victory"):
            log = combat.use_damage_item("holy_talisman")
        self.assertEqual(combat.player_max_ap - 1, combat.player_ap)
        self.assertIn("victory", log)

    def test_speed_thresholds_are_capped_at_three(self):
        cases = ((149, 100, 1), (150, 100, 2), (249, 100, 2), (250, 100, 3), (9999, 1, 3))
        for actor, opponent, expected in cases:
            self.assertEqual(TRPGCombat.action_points_for_speed(actor, opponent), expected)

    def test_monsters_wait_until_all_player_ap_is_spent(self):
        combat, view, player = self.make_combat(player_spd=250, monster_spd=100)
        with patch("trpg.combat.random.uniform", return_value=1.0), patch("trpg.combat.random.random", return_value=1.0):
            combat.player_attack()
            self.assertEqual(combat.player_ap, 2)
            self.assertEqual(player.current_hp, 500)
            combat.player_attack()
            self.assertEqual(combat.player_ap, 1)
            self.assertEqual(player.current_hp, 500)
            combat.player_attack()
        self.assertLess(player.current_hp, 500)
        self.assertEqual(combat.player_ap, combat.player_max_ap)
        self.assertTrue(combat.round_started)

    def test_mage_regenerates_only_once_during_multi_action_round(self):
        combat, _view, player = self.make_combat(player_spd=250, monster_spd=100)
        player.stat_alloc["mage"] = 10
        player.core_ability = "mage"
        player.base_int = 40
        with patch("trpg.combat.random.uniform", return_value=1.0), patch("trpg.combat.random.random", return_value=1.0):
            combat.player_attack()
            first = player.current_mp
            combat.player_attack()
        self.assertGreater(first, 0)
        self.assertEqual(player.current_mp, first)

    def test_incoming_damage_breaks_rogue_combo(self):
        combat, _view, player = self.make_combat(player_spd=100, monster_spd=100)
        player.stat_alloc["rogue"] = 10
        player.core_ability = "rogue"
        player.combat_buffs["combo_stacks"] = 3
        with patch("trpg.combat.random.uniform", return_value=1.0), patch("trpg.combat.random.random", return_value=1.0):
            log = combat.player_attack()
        self.assertEqual(player.combat_buffs.get("combo_stacks"), 0)
        self.assertIn("連擊", log)

    def test_rogue_combo_caps_at_five(self):
        combat, _view, player = self.make_combat(player_spd=250, monster_spd=100)
        player.stat_alloc["rogue"] = 10
        player.core_ability = "rogue"
        for _ in range(10):
            combat._gain_combo()
        self.assertEqual(player.combat_buffs.get("combo_stacks"), 5)

    def test_three_ap_monster_ticks_turn_start_only_once(self):
        combat, view, player = self.make_combat(player_spd=100, monster_spd=250)
        player.current_hp = 5000
        with patch("trpg.combat.random.uniform", return_value=1.0), patch("trpg.combat.random.random", return_value=1.0):
            combat.advance_time("", force_end=True)
        self.assertEqual(view.monster_slots[0].get("turns_acted"), 1)

    def test_source_and_snapshot_no_longer_use_av(self):
        combat_source = (ROOT / "trpg" / "combat.py").read_text(encoding="utf-8")
        view_source = (ROOT / "trpg" / "view.py").read_text(encoding="utf-8")
        self.assertNotIn("player_av", combat_source)
        self.assertNotIn('slot["av"]', combat_source)
        self.assertNotIn('"player_av":', view_source)
        self.assertIn('"player_ap":', view_source)
        self.assertIn('"b_end"', view_source)
        self.assertIn('"⚠️" if self.combat._has_ultimate_warning(slot) else "❗"', view_source)

    def test_paralysis_and_freeze_action_rules(self):
        combat, view, player = self.make_combat(player_spd=250, monster_spd=100)
        # Give player 3 AP this round
        # Test Freeze (Set round_started=True to test intra-round behavior without tick decrement)
        combat.round_started = True
        player.current_hp = 100 # Allow potion use
        player.status_effects = {"freeze": {"turns": 1}}
        player.inventory["health_potion"] = 5
        view.cog.items["health_potion"] = {"heal": 50, "heal_target": "hp"}

        log = combat.use_potion("health_potion")
        # Freeze breaks, consumes 1 AP, item not consumed
        self.assertNotIn("freeze", player.status_effects)
        self.assertEqual(combat.player_ap, 2)
        self.assertEqual(player.inventory["health_potion"], 5)
        self.assertIn("掙脫了冰凍", log)

        # Test Paralysis
        player.status_effects = {"paralysis": {"turns": 1}}
        # Mock random to force a fail
        with patch("trpg.combat.random.random", return_value=0.1):
            log = combat.use_potion("health_potion")

        # Action failed, item not consumed, AP consumed
        self.assertEqual(combat.player_ap, 1)
        self.assertEqual(player.inventory["health_potion"], 5)
        self.assertIn("麻痺", log)
        self.assertIn("paralysis", player.status_effects) # Not removed

if __name__ == "__main__":
    unittest.main()
