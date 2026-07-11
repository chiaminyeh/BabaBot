from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "trpg_data"
REQUIRED_FILES = ["areas.json", "monsters.json", "items.json", "skills.json", "events.json", "quests.json", "shop.json"]


def load_json(filename: str) -> Any:
    path = DATA_DIR / filename
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def as_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    if isinstance(value, dict):
        return [key for key in value if isinstance(key, str)]
    return []


def walk_dicts(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk_dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk_dicts(item)


def main() -> int:
    issues: list[str] = []

    for filename in REQUIRED_FILES:
        path = DATA_DIR / filename
        if not path.exists():
            issues.append(f"required file missing: {filename}")
            continue
        try:
            data = load_json(filename)
        except Exception as exc:
            issues.append(f"invalid JSON {filename}: {exc.__class__.__name__}: {exc}")
            continue
        if filename in {"areas.json", "monsters.json", "items.json", "skills.json"} and (not isinstance(data, dict) or not data):
            issues.append(f"required content file is empty/non-dict: {filename}")

    if issues:
        for issue in issues:
            print(f"[FAIL] {issue}")
        return 1

    areas = load_json("areas.json")
    monsters = load_json("monsters.json")
    items = load_json("items.json")
    events = load_json("events.json")
    quests = load_json("quests.json")
    shop = load_json("shop.json")

    monster_ids = set(monsters)
    item_ids = set(items)
    event_ids = set(events)

    for area_id, area in areas.items():
        for monster_id in as_ids(area.get("monsters")):
            if monster_id not in monster_ids:
                issues.append(f"areas.{area_id}.monsters references missing monster: {monster_id}")
        for monster_id in as_ids(area.get("boss")):
            if monster_id not in monster_ids:
                issues.append(f"areas.{area_id}.boss references missing monster: {monster_id}")
        for monster_id in as_ids(area.get("boss_minions")):
            if monster_id not in monster_ids:
                issues.append(f"areas.{area_id}.boss_minions references missing monster: {monster_id}")
        for event_id in as_ids(area.get("events")):
            if event_id not in event_ids:
                issues.append(f"areas.{area_id}.events references missing event: {event_id}")
        for subarea in area.get("subareas", []) if isinstance(area.get("subareas"), list) else []:
            sub_id = subarea.get("id", "<missing-id>") if isinstance(subarea, dict) else "<bad-subarea>"
            if not isinstance(subarea, dict):
                issues.append(f"areas.{area_id}.subareas contains non-dict entry")
                continue
            for monster_id in as_ids(subarea.get("monsters")):
                if monster_id not in monster_ids:
                    issues.append(f"areas.{area_id}.subareas.{sub_id}.monsters references missing monster: {monster_id}")
            for event_id in as_ids(subarea.get("events")):
                if event_id not in event_ids:
                    issues.append(f"areas.{area_id}.subareas.{sub_id}.events references missing event: {event_id}")

    for monster_id, monster in monsters.items():
        for item_id in as_ids(monster.get("drops")):
            if item_id not in item_ids:
                issues.append(f"monsters.{monster_id}.drops references missing item: {item_id}")

    for obj in walk_dicts(shop):
        for key in ("item", "item_id", "id"):
            value = obj.get(key)
            if isinstance(value, str) and value not in item_ids and value not in shop:
                # Ignore non-item IDs nested inside shop metadata by only checking dicts that look like shop entries.
                if any(price_key in obj for price_key in ("price", "stock", "cost", "currency")):
                    issues.append(f"shop entry references missing item via {key}: {value}")

    for quest_id, quest in quests.items() if isinstance(quests, dict) else []:
        for obj in walk_dicts(quest):
            for key in ("item", "item_id", "required_item", "reward_item"):
                value = obj.get(key)
                if isinstance(value, str) and value not in item_ids:
                    issues.append(f"quests.{quest_id} references missing item via {key}: {value}")
            for key in ("monster", "monster_id", "target_monster"):
                value = obj.get(key)
                if isinstance(value, str) and value not in monster_ids:
                    issues.append(f"quests.{quest_id} references missing monster via {key}: {value}")

    if issues:
        print(f"[JSON references] {len(issues)} issue(s) found:")
        for issue in issues[:100]:
            print(f"  - {issue}")
        if len(issues) > 100:
            print(f"  ... and {len(issues) - 100} more")
        return 1

    print(
        "[JSON references] OK: "
        f"{len(areas)} areas, {len(monsters)} monsters, {len(items)} items, "
        f"{len(events)} events cross-reference cleanly."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
