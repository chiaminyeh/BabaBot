import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_json(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


class QuestTimingAndAchievementPrivacyTests(unittest.TestCase):
    def test_redundant_misty_forest_entry_quest_removed(self):
        quests = load_json("trpg_data/quests.json")
        self.assertNotIn("explore_the_forest", quests)

    def test_rat_chain_and_lost_cat_wait_until_misty_forest_level(self):
        quests = load_json("trpg_data/quests.json")
        self.assertGreaterEqual(quests["chain_1_cleanup"].get("req_level", 0), 5)
        self.assertGreaterEqual(quests["chain_2_boss"].get("req_level", 0), 5)
        self.assertGreaterEqual(quests["quest_find_cat"].get("req_level", 0), 5)
        self.assertEqual(quests["chain_2_boss"].get("requires"), "chain_1_cleanup")

    def test_lost_cat_prompt_is_not_in_pre_forest_event_pools(self):
        areas = load_json("trpg_data/areas.json")
        offenders = []
        for area_id, area in areas.items():
            req_level = area.get("req_level", 1)
            if req_level >= 5:
                continue
            if "village_old_man_cat" in area.get("events", []):
                offenders.append(f"{area_id}.events")
            for sub in area.get("subareas", []) or []:
                if "village_old_man_cat" in sub.get("events", []):
                    offenders.append(f"{area_id}.{sub.get('id')}.events")
        self.assertFalse(offenders, f"cat quest prompt appears before forest unlock: {offenders}")

    def test_locked_achievement_list_does_not_show_progress_numbers(self):
        view_source = (ROOT / "trpg" / "view.py").read_text(encoding="utf-8")
        handle_start = view_source.index("    def handle_achievements(self):")
        handle_end = view_source.index("    def build_quest_hall_menu", handle_start)
        handle_body = view_source[handle_start:handle_end]
        self.assertNotIn("current_val", handle_body)
        self.assertNotIn("threshold", handle_body)
        self.assertNotRegex(handle_body, re.compile(r"\{min\(current_val, threshold\)\}/\{threshold\}"))


if __name__ == "__main__":
    unittest.main()
