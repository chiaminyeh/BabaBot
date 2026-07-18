import json
import random
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.simulate_bee_queen_balance import make_player, run_benchmark, BenchCog
from trpg.monster_ai import _pick_active_skill, ai_support_healer
from trpg.stats import meets_item_stat_requirements

ROOT = Path(__file__).parents[1]


class BeeQueenBalanceTests(unittest.TestCase):
    def test_profiles_use_production_recalc_legal_level_gear_and_all_qualified_skills(self):
        cog = BenchCog()
        for archetype in ("knight", "rogue", "mage", "warlock"):
            player = make_player(archetype, 14, cog)
            self.assertEqual(player.stat_alloc[archetype], 28)
            self.assertEqual(sum(player.stat_alloc.values()), 28)
            self.assertEqual(player.current_hp, player.max_hp)
            for item_id in (player.weapon, player.armor, player.accessory):
                if item_id:
                    self.assertLessEqual(cog.items[item_id].get("exclusive_level", 1), 14)
                    self.assertFalse(cog.items[item_id].get("mystery_only"))
                    self.assertTrue(meets_item_stat_requirements(player, cog.items[item_id])[0], item_id)
            qualified = {
                sid for sid, skill in cog.skills.items()
                if (skill.get("req_points") or {}).get(archetype) is not None
                and (skill.get("req_points") or {})[archetype] <= 28
                and skill.get("req_level", 1) <= 14
            }
            self.assertTrue(qualified.issubset(set(player.skills)))
            self.assertTrue(qualified.issubset(set(player.equipped_skills)))

            expected_common = {"heal_light", "regeneration", "purify", "power_slash"} if archetype == "knight" else {"heal_light", "regeneration", "purify", "mana_surge"}
            self.assertTrue(expected_common.issubset(set(player.equipped_skills)), (archetype, player.equipped_skills))

        warlock15 = make_player("warlock", 15, cog)
        self.assertIn("inferno", warlock15.equipped_skills)
        self.assertIn("mana_surge", warlock15.equipped_skills)
        self.assertIn("venom_cloud", warlock15.equipped_skills)
        self.assertNotIn("regeneration", warlock15.equipped_skills)
        self.assertLessEqual(len(warlock15.equipped_skills), 8)

    def test_worker_heal_is_capped_by_healer_not_large_target(self):
        worker = {"id": "worker_bee", "name": "Worker", "max_hp": 200, "atk": 1,
                  "heal_chance": 1.0, "heal_target_max_hp_pct": 0.25,
                  "heal_self_max_hp_cap_pct": 0.25}
        queen = {"id": "bee_queen", "name": "Queen", "max_hp": 2800, "atk": 1}
        slots = [{"monster": worker, "hp": 200, "status": {}},
                 {"monster": queen, "hp": 1000, "status": {}}]
        combat = SimpleNamespace(
            player=SimpleNamespace(language="en"),
            view=SimpleNamespace(monster_slots=slots),
        )
        random.seed(1)
        ai_support_healer(combat, slots[0], "")
        self.assertEqual(slots[1]["hp"], 1050)

    def test_bee_queen_preserves_minion_identity_with_bounded_boss_pressure(self):
        monsters = json.loads((ROOT / "trpg_data" / "monsters.json").read_text(encoding="utf-8"))
        self.assertEqual(monsters["bee_queen"]["atk"], 50)
        self.assertEqual(monsters["soldier_bee"]["atk"], 55)
        self.assertEqual(monsters["worker_bee"]["atk"], 40)
        self.assertEqual(monsters["bee_queen"]["active_skills"][0]["trigger"]["value"], 4)
        self.assertEqual(monsters["bee_queen"]["ai"], "none")
        self.assertEqual(monsters["bee_queen"]["status_chance"], 0.08)
        self.assertEqual(monsters["worker_bee"]["heal_self_max_hp_cap_pct"], 0.25)
        self.assertEqual(monsters["bee_queen"]["phase2"]["heal_pct"], 0.15)
        self.assertEqual(monsters["bee_queen"]["active_skills"][0]["max_uses"], 1)
        self.assertEqual(monsters["bee_queen"]["phase2"]["active_skills"][0]["max_uses"], 2)

    def test_active_skill_selection_does_not_consume_until_execution(self):
        skill = {"id": "summon", "trigger": {"type": "interval", "value": 1}, "max_uses": 2}
        slot = {"monster": {"max_hp": 100, "active_skills": [skill]}, "hp": 100}
        self.assertIs(_pick_active_skill(slot), skill)
        self.assertIs(_pick_active_skill(slot), skill)
        self.assertEqual(slot.get("skill_use_counts", {}), {})
        slot["skill_use_counts"] = {"summon": 2}
        self.assertIsNone(_pick_active_skill(slot))

    def test_real_seeded_combat_reports_paths_metrics_and_death_causes(self):
        cog = BenchCog()
        self.assertEqual(cog.areas["area_10deep_forest"]["boss_minions"], ["worker_bee"])
        rows = run_benchmark(seeds=2, levels=(14,))
        self.assertEqual({row["archetype"] for row in rows}, {"knight", "rogue", "mage", "warlock"})
        for row in rows:
            self.assertEqual(row["runs"], 2)
            self.assertIn("median_damage_taken", row)
            self.assertIn("median_estimated_net_enemy_hp_reduction", row)
            self.assertIn("death_causes", row)
            self.assertIn("runs_reaching_phase2", row)
            self.assertIn("estimated_summons", row)
            self.assertIn("skills_used", row)
            self.assertIn("defends", row)
            self.assertIn("dodges", row)
            self.assertTrue(row["real_combat_path"])


if __name__ == "__main__":
    unittest.main()
