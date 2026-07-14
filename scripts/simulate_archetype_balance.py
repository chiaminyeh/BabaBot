"""Deterministic geared archetype benchmark across progression milestones.

Run from repo root: python scripts/simulate_archetype_balance.py
This is a comparative smoke benchmark, not a replacement for live combat tests.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from trpg.archetypes import set_core_ability
from trpg.combat import TRPGCombat, get_player_atk, get_player_def, get_player_magic, get_player_spd
from trpg.player import TRPGPlayer
from trpg.stats import meets_item_stat_requirements, recalc_player_stats

ROOT = Path(__file__).parents[1]
LEVELS = (5, 10, 20, 50, 80)
ARCHETYPES = ("knight", "rogue", "mage", "warlock")
LABELS = {"knight": "Warrior", "rogue": "Rogue", "mage": "Mage", "warlock": "Warlock"}


def item_score(item, archetype):
    common = item.get("hp_bonus", 0) * 0.08 + item.get("mdef_bonus", 0)
    if archetype == "knight":
        return item.get("atk_bonus", 0) * 1.2 + item.get("def_bonus", 0) * 1.5 + common
    if archetype == "rogue":
        return item.get("atk_bonus", 0) * 1.2 + item.get("spd_bonus", 0) * 1.5 + common
    if archetype == "mage":
        return item.get("magic_bonus", 0) * 1.5 + item.get("mp_bonus", 0) * 0.15 + common
    return item.get("magic_bonus", 0) + item.get("atk_bonus", 0) + item.get("hp_bonus", 0) * 0.12 + common


def best_item(player, items, item_type, archetype):
    candidates = []
    for item_id, item in items.items():
        if item.get("type") != item_type or item.get("mystery_only"):
            continue
        if int(item.get("exclusive_level", 0) or 0) > player.level:
            continue
        if not meets_item_stat_requirements(player, item)[0]:
            continue
        candidates.append((item_score(item, archetype), item_id))
    return max(candidates, default=(0, None))[1]


def expected_damage(player, skills, archetype, enemy_def, enemy_mdef):
    atk = get_player_atk(player, {})
    magic = get_player_magic(player, {})
    available = [v for v in skills.values() if (v.get("req_points") or {}).get(archetype, 0) <= player.stat_alloc[archetype] and v.get("req_points", {}).get(archetype) and v.get("req_level", 1) <= player.level and v.get("type") in ("physical", "magic")]
    values = []
    for skill in available:
        hits = int(skill.get("hits", 1) or 1)
        if skill.get("hp_scaling_multiplier"):
            value = player.max_hp * skill.get("hp_cost_percent", 0) * skill["hp_scaling_multiplier"]
        elif skill.get("type") == "magic":
            effective = enemy_mdef * (1 - skill.get("def_pierce", 0.5))
            value = skill.get("base_power", 0) + magic * skill.get("magic_scaling", 1.0) - effective
        else:
            effective = enemy_def * (1 - skill.get("physical_def_pierce", 0))
            value = atk * skill.get("power_multiplier", 1.0) - effective
        values.append(max(1, value) * hits)
    return max(values, default=max(1, atk - enemy_def))


def build(level, archetype, items):
    p = TRPGPlayer(f"sim-{level}-{archetype}")
    p.level = level
    p.stat_alloc = {"knight": 0, "rogue": 0, "mage": 0, "warlock": 0}
    p.stat_alloc[archetype] = min(99, level * 2)
    recalc_player_stats(p, items, heal_full=True)
    p.weapon = best_item(p, items, "weapon", archetype)
    p.armor = best_item(p, items, "armor", archetype)
    p.accessory = best_item(p, items, "accessory", archetype)
    recalc_player_stats(p, items, heal_full=True)
    set_core_ability(p, archetype)
    return p


def run():
    items = json.loads((ROOT / "trpg_data" / "items.json").read_text(encoding="utf-8"))
    skills = json.loads((ROOT / "trpg_data" / "skills.json").read_text(encoding="utf-8"))
    print("level,class,hp,mp,atk,def,magic,spd,ap,best_hit,weapon,armor,accessory")
    rows = []
    for level in LEVELS:
        enemy_def = 5 + level * 2.2
        enemy_mdef = 3 + level * 1.6
        for archetype in ARCHETYPES:
            p = build(level, archetype, items)
            spd = get_player_spd(p)
            enemy_spd = int(10 + level * 2.2)
            ap = TRPGCombat.action_points_for_speed(spd, enemy_spd)
            row = (level, LABELS[archetype], p.max_hp, p.max_mp, get_player_atk(p, items), get_player_def(p, items), get_player_magic(p, items), spd, ap, round(expected_damage(p, skills, archetype, enemy_def, enemy_mdef)), p.weapon or "-", p.armor or "-", p.accessory or "-")
            rows.append(row)
            print(",".join(map(str, row)))
    assert len(rows) == len(LEVELS) * len(ARCHETYPES)
    for level in LEVELS:
        hits = [r[9] for r in rows if r[0] == level]
        assert min(hits) > 0
        assert max(hits) / min(hits) < 8.0, (level, hits)


if __name__ == "__main__":
    run()
