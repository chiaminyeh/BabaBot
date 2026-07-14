import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from trpg.archetypes import core_active, set_core_ability
from trpg.combat import (
    _status_synergy_multiplier_from_skills,
    get_player_def,
    get_player_spd,
)
from trpg.player import TRPGPlayer
from trpg.stats import get_unspent_points, migrate_player_stats

ROOT = Path(__file__).parents[1]


class ArchetypeReworkRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skills = json.loads((ROOT / "trpg_data" / "skills.json").read_text(encoding="utf-8"))
        cls.items = json.loads((ROOT / "trpg_data" / "items.json").read_text(encoding="utf-8"))
        cls.events = json.loads((ROOT / "trpg_data" / "events.json").read_text(encoding="utf-8"))

    def test_warrior_core_is_exactly_twenty_percent_defense(self):
        p = SimpleNamespace(base_def=100, stat_alloc={"knight": 10}, core_ability=None, dungeon_buffs={}, combat_debuffs={}, combat_buffs={})
        self.assertEqual(get_player_def(p, {}), 100)
        self.assertTrue(set_core_ability(p, "knight"))
        self.assertEqual(get_player_def(p, {}), 120)

    def test_rogue_combo_grants_speed_without_extra_attack_passive(self):
        p = SimpleNamespace(base_spd=100, stat_alloc={"rogue": 10}, core_ability="rogue", combat_debuffs={}, combat_buffs={"combo_stacks": 3})
        self.assertEqual(get_player_spd(p), 114)  # int(100 * 1.15)
        self.assertNotIn("combo_attack", self.skills)
        self.assertEqual(_status_synergy_multiplier_from_skills(p, self.skills, {"burn": {"turns": 2}}), 1.0)

    def test_only_one_rogue_skill_is_multihit_and_names_have_no_x2_emoji(self):
        rogue = {sid: skill for sid, skill in self.skills.items() if (skill.get("req_points") or {}).get("rogue")}
        self.assertEqual([sid for sid, skill in rogue.items() if skill.get("hits", 1) > 1], ["double_strike"])
        for skill in rogue.values():
            self.assertNotIn("✖️", skill.get("name", ""))

    def test_warlock_has_no_poison_and_has_sacrifice_drain_pairing(self):
        warlock = {sid: skill for sid, skill in self.skills.items() if (skill.get("req_points") or {}).get("warlock")}
        self.assertFalse(any(skill.get("apply_status") == "poison" for skill in warlock.values()))
        self.assertGreater(warlock["dark_orb"].get("lifesteal", 0), 0)
        pact = warlock["venom_cloud"]
        self.assertLess(pact["buff"]["def_mult"], 1)
        self.assertGreater(pact["buff"]["atk_mult"], 1)
        self.assertGreater(pact.get("hp_cost_percent", 0), 0)

    def test_mage_regeneration_is_not_global_source_code(self):
        source = (ROOT / "trpg" / "combat.py").read_text(encoding="utf-8")
        regen_block = source.split("# Automatic mana regeneration", 1)[1].split("log, can_act", 1)[0]
        self.assertIn('core_active(self.player, "mage")', regen_block)

    def test_luck_points_refund_and_deprecated_skills_are_removed(self):
        p = TRPGPlayer("legacy")
        p.level = 10
        p.stat_alloc = {"knight": 10, "luck": 6}
        p.skills = ["combo_attack", "battle_focus"]
        p.equipped_skills = ["combo_attack", "battle_focus"]
        migrate_player_stats(p, {}, self.skills)
        self.assertNotIn("luck", p.stat_alloc)
        self.assertEqual(get_unspent_points(p), 10)
        self.assertNotIn("combo_attack", p.skills)
        self.assertNotIn("battle_focus", p.skills)
        self.assertIn("shield_bash", p.skills)
        self.assertIn("flame_coating", p.skills)

    def test_endgame_boss_gear_cannot_be_equipped_at_low_level(self):
        expected = {
            "dragonbone_greatsword": "knight",
            "abyssal_cloak": "rogue",
            "demon_king_horn": "mage",
            "forbidden_blood_chalice": "warlock",
        }
        for item_id, core in expected.items():
            item = self.items[item_id]
            self.assertEqual(item["exclusive_level"], 80)
            self.assertEqual(item["stat_requirements"], {core: 80})

    def test_choice_events_have_fallback_and_core_paths(self):
        for event_id in ("shiny_coin", "find_chest", "merchant_corpse"):
            choices = self.events[event_id]["choices"]
            self.assertTrue(any(not choice.get("requires_core") for choice in choices))
            self.assertLessEqual(len(choices), 5)
            for choice in choices:
                self.assertIn("label_en", choice)
                self.assertIn("message_en", choice["outcome"])
        chest_cores = {choice.get("requires_core") for choice in self.events["find_chest"]["choices"] if choice.get("requires_core")}
        self.assertEqual(chest_cores, {"knight", "rogue", "mage", "warlock"})


if __name__ == "__main__":
    unittest.main()
