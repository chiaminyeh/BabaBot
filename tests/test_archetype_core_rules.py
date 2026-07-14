import unittest
from types import SimpleNamespace

from trpg.archetypes import (
    FORTUNE_MAX,
    FORTUNE_MIN,
    change_fortune,
    core_active,
    fortune_tier,
    normalize_core_selection,
    set_core_ability,
)


class ArchetypeCoreRulesTests(unittest.TestCase):
    def player(self, **kwargs):
        base = {"stat_alloc": {"knight": 0, "rogue": 0, "mage": 0, "warlock": 0}, "core_ability": None, "fortune": 0}
        base.update(kwargs)
        return SimpleNamespace(**base)

    def test_only_qualified_core_can_be_selected(self):
        p = self.player(stat_alloc={"knight": 10, "rogue": 9, "mage": 0, "warlock": 0})
        self.assertTrue(set_core_ability(p, "knight"))
        self.assertTrue(core_active(p, "knight"))
        self.assertFalse(set_core_ability(p, "rogue"))
        self.assertEqual(p.core_ability, "knight")

    def test_selection_is_cleared_after_points_no_longer_qualify(self):
        p = self.player(stat_alloc={"knight": 0}, core_ability="knight")
        self.assertIsNone(normalize_core_selection(p))
        self.assertIsNone(p.core_ability)

    def test_fortune_is_bounded_and_described_without_number(self):
        p = self.player(fortune=9)
        self.assertEqual(change_fortune(p, 99)[1], FORTUNE_MAX)
        self.assertEqual(change_fortune(p, -99)[1], FORTUNE_MIN)
        self.assertEqual(fortune_tier(p.fortune), "厄運纏身")
        self.assertNotIn(str(p.fortune), fortune_tier(p.fortune))


if __name__ == "__main__":
    unittest.main()
