import json
import unittest
from pathlib import Path

from trpg.monster_ai import _trait_avenger, _trait_regenerator


class DummyPlayer:
    language = "zh"


class DummyCombat:
    player = DummyPlayer()


class TestMonsterAiTraits(unittest.TestCase):
    def test_avenger_gains_fixed_attack_boost_when_damaged_not_damage_scaled_counter(self):
        low_damage_slot = {"monster": {"name": "野豬", "max_hp": 50}, "dmg_last_window": 1}
        high_damage_slot = {"monster": {"name": "野豬", "max_hp": 50}, "dmg_last_window": 40}

        low_mult, low_note = _trait_avenger(DummyCombat(), low_damage_slot)
        high_mult, high_note = _trait_avenger(DummyCombat(), high_damage_slot)

        self.assertEqual(low_mult, 1.15)
        self.assertEqual(high_mult, 1.15)
        self.assertEqual(low_damage_slot["avenger_atk_stacks"], 1)
        self.assertEqual(high_damage_slot["avenger_atk_stacks"], 1)
        self.assertIn("攻擊力提升", low_note)
        self.assertIn("攻擊力提升", high_note)

    def test_avenger_does_not_boost_when_not_damaged(self):
        slot = {"monster": {"name": "野豬", "max_hp": 50}, "dmg_last_window": 0}

        mult, note = _trait_avenger(DummyCombat(), slot)

        self.assertEqual(mult, 1.0)
        self.assertEqual(note, "")
        self.assertNotIn("avenger_atk_stacks", slot)

    def test_regenerator_uses_per_monster_regen_rate(self):
        slot = {
            "monster": {"name": "迷霧林王", "max_hp": 848, "regen_pct": 0.03},
            "hp": 400,
            "status": {},
        }

        _, proceed = _trait_regenerator(DummyCombat(), slot, "")

        self.assertTrue(proceed)
        self.assertEqual(slot["hp"], 425)

    def test_regenerator_keeps_eight_percent_default(self):
        slot = {
            "monster": {"name": "森林精靈", "max_hp": 100},
            "hp": 50,
            "status": {},
        }

        _trait_regenerator(DummyCombat(), slot, "")

        self.assertEqual(slot["hp"], 58)

    def test_mistwood_king_healing_budget_is_bounded(self):
        monsters = json.loads((Path(__file__).parents[1] / "trpg_data" / "monsters.json").read_text(encoding="utf-8"))
        boss = monsters["forest_guardian"]

        self.assertEqual(boss["ai"], "none")
        self.assertEqual(boss["regen_pct"], 0.03)
        self.assertEqual(boss["phase2"]["heal_pct"], 0.15)
        self.assertEqual(boss["active_skills"][0]["trigger"], {"type": "interval", "value": 6})


if __name__ == "__main__":
    unittest.main()
