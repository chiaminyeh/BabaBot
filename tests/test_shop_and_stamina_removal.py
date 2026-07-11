import json
import unittest
from pathlib import Path

from trpg.balance import AREA_FIXED_SHOPS, MYSTERY_MERCHANT_STOCK_COUNT, area_shop_stock

ROOT = Path(__file__).resolve().parents[1]


def load_json(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


class ShopAndStaminaRemovalTests(unittest.TestCase):
    def test_area_shops_are_named_and_fixed_without_stamina_potion(self):
        items = load_json("trpg_data/items.json")
        areas = load_json("trpg_data/areas.json")
        self.assertNotIn("stamina_potion", items)
        overworld_area_ids = [
            area_id for area_id, area in areas.items()
            if area_id.startswith("area_")
            and not area.get("is_colosseum")
            and area_id not in {"area_tower", "area_dungeon", "area_legend_cave"}
        ]
        self.assertTrue(overworld_area_ids)
        missing = [area_id for area_id in overworld_area_ids if area_id not in AREA_FIXED_SHOPS]
        self.assertFalse(missing, f"areas missing fixed shop config: {missing}")
        names = [AREA_FIXED_SHOPS[area_id]["name_zh"] for area_id in overworld_area_ids]
        self.assertEqual(len(names), len(set(names)), "shop zh names should be unique per area")
        for area_id in overworld_area_ids:
            cfg = AREA_FIXED_SHOPS[area_id]
            self.assertTrue(cfg.get("name_zh"))
            self.assertTrue(cfg.get("name_en"))
            stock = area_shop_stock(area_id)
            self.assertEqual(stock, area_shop_stock(area_id), "stock must be deterministic/fixed")
            self.assertGreaterEqual(len(stock), 3)
            self.assertLessEqual(len(stock), 8)
            self.assertNotIn("stamina_potion", stock)
            self.assertTrue(all(item_id in items for item_id in stock))

    def test_mystery_merchant_is_random_five_item_stock_from_existing_items(self):
        items = load_json("trpg_data/items.json")
        self.assertEqual(MYSTERY_MERCHANT_STOCK_COUNT, 5)
        pool = [
            item_id for item_id, item in items.items()
            if item.get("shop_weight", 0) > 0
        ]
        self.assertGreaterEqual(len(pool), MYSTERY_MERCHANT_STOCK_COUNT)

    def test_no_stamina_system_references_remain_in_runtime_sources(self):
        source_paths = list((ROOT / "trpg").rglob("*.py")) + [ROOT / "trpg_data" / "locale_en.json"]
        forbidden = ["_spend_stamina", "stamina_potion", "stamina.line", "STAMINA_COST", "STAMINA_MAX", "STAMINA_POTION"]
        offenders = []
        for path in source_paths:
            text = path.read_text(encoding="utf-8")
            for needle in forbidden:
                if needle in text:
                    offenders.append(f"{path.relative_to(ROOT)} contains {needle}")
        self.assertFalse(offenders, "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
