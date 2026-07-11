import unittest

from trpg.monster_ai import _trait_avenger


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


if __name__ == "__main__":
    unittest.main()
