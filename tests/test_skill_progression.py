import unittest

class MockPlayer:
    def __init__(self):
        self.skill_levels = {}
        self.skill_usage = {}
        self.money = 0
        self.equipped_skills = []
        self.language = "zh"
        self.stats = {}

from trpg.skill_progression import (
    normalize_skill_level,
    get_proficiency_requirement,
    get_base_upgrade_cost,
    get_upgrade_cost_with_discount,
    get_skill_effect_multiplier,
    get_skill_cost_multiplier,
    record_successful_skill_use,
    validate_paid_upgrade,
    apply_paid_upgrade,
    MAX_SKILL_LEVEL
)

class TestSkillProgression(unittest.TestCase):
    def test_normalize_skill_level(self):
        self.assertEqual(normalize_skill_level(1), 1)
        self.assertEqual(normalize_skill_level(5), 5)
        self.assertEqual(normalize_skill_level(10), 5)
        self.assertEqual(normalize_skill_level(0), 1)
        self.assertEqual(normalize_skill_level(-5), 1)
        self.assertEqual(normalize_skill_level("2"), 2)
        self.assertEqual(normalize_skill_level("abc"), 1)

    def test_get_proficiency_requirement(self):
        self.assertEqual(get_proficiency_requirement(1), 20)
        self.assertEqual(get_proficiency_requirement(2), 50)
        self.assertEqual(get_proficiency_requirement(3), 100)
        self.assertEqual(get_proficiency_requirement(4), 200)
        self.assertEqual(get_proficiency_requirement(5), 0)

    def test_get_base_upgrade_cost(self):
        self.assertEqual(get_base_upgrade_cost(1), 500)
        self.assertEqual(get_base_upgrade_cost(2), 1500)
        self.assertEqual(get_base_upgrade_cost(3), 3000)
        self.assertEqual(get_base_upgrade_cost(4), 6000)
        self.assertEqual(get_base_upgrade_cost(5), 0)

    def test_get_upgrade_cost_with_discount(self):
        self.assertEqual(get_upgrade_cost_with_discount(1, 0), 500)
        self.assertEqual(get_upgrade_cost_with_discount(1, 10), 250)
        self.assertEqual(get_upgrade_cost_with_discount(1, 20), 0)
        self.assertEqual(get_upgrade_cost_with_discount(1, 100), 0)

    def test_get_skill_multipliers(self):
        self.assertAlmostEqual(get_skill_effect_multiplier(1), 1.0)
        self.assertAlmostEqual(get_skill_effect_multiplier(2), 1.2)
        self.assertAlmostEqual(get_skill_cost_multiplier(1), 1.0)
        self.assertAlmostEqual(get_skill_cost_multiplier(2), 1.15)

    def test_record_successful_skill_use(self):
        p = MockPlayer()
        # Initial use
        msg = record_successful_skill_use(p, "test_skill", "Test Skill", "zh")
        self.assertEqual(msg, "")
        self.assertEqual(p.skill_levels["test_skill"], 1)
        self.assertEqual(p.skill_usage["test_skill"], 1)

        # Trigger level up
        p.skill_usage["test_skill"] = 19
        msg = record_successful_skill_use(p, "test_skill", "Test Skill", "zh")
        self.assertIn("升級至 Lv.2", msg)
        self.assertEqual(p.skill_levels["test_skill"], 2)
        self.assertEqual(p.skill_usage["test_skill"], 0)

        # Max level shouldn't increase usage
        p.skill_levels["test_skill"] = MAX_SKILL_LEVEL
        msg = record_successful_skill_use(p, "test_skill", "Test Skill", "zh")
        self.assertEqual(msg, "")
        self.assertEqual(p.skill_usage["test_skill"], 0)

    def test_validate_paid_upgrade(self):
        p = MockPlayer()
        skill_data = {"name": {"zh": "Test Skill"}, "type": "active"}

        # Not equipped
        ok, msg, cost = validate_paid_upgrade(p, skill_data, "test_skill")
        self.assertFalse(ok)
        self.assertIn("未裝備", msg)

        # Equipped but not enough money
        p.equipped_skills = ["test_skill"]
        ok, msg, cost = validate_paid_upgrade(p, skill_data, "test_skill")
        self.assertFalse(ok)
        self.assertIn("金幣不足", msg)

        # Enough money
        p.money = 1000
        ok, msg, cost = validate_paid_upgrade(p, skill_data, "test_skill")
        self.assertTrue(ok)
        self.assertEqual(msg, "")
        self.assertEqual(cost, 500)

        # Discount
        p.skill_usage["test_skill"] = 10
        ok, msg, cost = validate_paid_upgrade(p, skill_data, "test_skill")
        self.assertTrue(ok)
        self.assertEqual(cost, 250)

    def test_apply_paid_upgrade(self):
        p = MockPlayer()
        p.money = 1000
        p.skill_levels["test_skill"] = 1
        p.skill_usage["test_skill"] = 15

        apply_paid_upgrade(p, "test_skill", 250)
        self.assertEqual(p.money, 750)
        self.assertEqual(p.skill_levels["test_skill"], 2)
        self.assertEqual(p.skill_usage["test_skill"], 0)
        self.assertEqual(p.stats["money_spent"], 250)

if __name__ == '__main__':
    unittest.main()
