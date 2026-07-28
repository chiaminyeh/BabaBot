"""共用怪物池 — 魔塔與地下城依樓層從這裡抽怪，取代過去寫死的單一怪物/名稱清單。"""

import json
import os
import random

from trpg.balance import (
    MONSTER_HP_PER_FLOOR, MONSTER_ATK_PER_FLOOR, MONSTER_DEF_PER_FLOOR,
    BOSS_HP_MULT, BOSS_ATK_MULT, BOSS_DEF_BONUS, BOSS_EXP_MULT, BOSS_MONEY_MULT,
)


def load_monster_pool(data_dir: str) -> dict:
    # 1. Load flat monsters database
    monsters_path = os.path.join(data_dir, "monsters.json")
    if not os.path.exists(monsters_path):
        return {}
    with open(monsters_path, "r", encoding="utf-8") as f:
        monsters = json.load(f)

    # 2. Load tiers config
    tiers_path = os.path.join(data_dir, "monster_tiers.json")
    if not os.path.exists(tiers_path):
        return {}
    with open(tiers_path, "r", encoding="utf-8") as f:
        tiers = json.load(f)

    # 3. Hydrate the tiers with full monster definitions
    hydrated_tiers = {}
    for tier_id, tier_data in tiers.items():
        hydrated_tier = {
            "floor_range": tier_data.get("floor_range", [1, 1])
        }
        # Monsters
        if "monsters" in tier_data:
            hydrated_tier["monsters"] = {}
            for m_id in tier_data["monsters"]:
                if m_id in monsters:
                    hydrated_tier["monsters"][m_id] = monsters[m_id]
        # Boss
        if "boss" in tier_data:
            boss_id = tier_data["boss"]
            if boss_id in monsters:
                hydrated_tier["boss"] = monsters[boss_id]
        # Boss Minions
        if "boss_minions" in tier_data:
            hydrated_tier["boss_minions"] = {}
            for m_id in tier_data["boss_minions"]:
                if m_id in monsters:
                    hydrated_tier["boss_minions"][m_id] = monsters[m_id]

        hydrated_tiers[tier_id] = hydrated_tier

    return hydrated_tiers


def pick_tier_for_floor(pool: dict, floor: int) -> dict:
    if not pool:
        return {}
    for tier in pool.values():
        lo, hi = tier.get("floor_range", [1, 1])
        if lo <= floor <= hi:
            return tier
    # 超出所有定義範圍：用最後一個 tier（依 floor_range 上限排序）
    return max(pool.values(), key=lambda t: t.get("floor_range", [0, 0])[1])


def instantiate_monster(monster_def: dict, floor: int, is_boss: bool = False, floor_scale: float = 1.0) -> dict:
    """把 tier 裡的怪物定義依樓層放大成一份完整的戰鬥用怪物 dict（跟舊版 active_monster 同形狀）。"""
    eff_floor = max(1, floor) * floor_scale

    base_hp = monster_def.get("base_hp", 20)
    base_atk = monster_def.get("base_atk", 8)
    base_def = monster_def.get("base_def", 3)
    base_spd = monster_def.get("base_spd", 10)

    hp = int(base_hp + eff_floor * MONSTER_HP_PER_FLOOR + eff_floor ** 1.3)
    atk = int(base_atk + eff_floor * MONSTER_ATK_PER_FLOOR + eff_floor ** 1.15)
    df = int(base_def + eff_floor * MONSTER_DEF_PER_FLOOR)
    spd = int(base_spd + eff_floor // 8)
    exp = int(12 + eff_floor * 14)
    money_min = int(8 + eff_floor * 4)
    money_max = int(16 + eff_floor * 7)

    if is_boss:
        hp = int(hp * BOSS_HP_MULT)
        atk = int(atk * BOSS_ATK_MULT)
        df += BOSS_DEF_BONUS
        exp = int(exp * BOSS_EXP_MULT)
        money_min *= BOSS_MONEY_MULT
        money_max *= BOSS_MONEY_MULT

    zh_name = monster_def.get("name", monster_def.get("id", "未知怪物"))
    en_name = monster_def.get("name_en") or zh_name
    prefix = "💀 " if is_boss else ""
    suffix = f" (Lv.{floor})"
    name = f"{prefix}{zh_name}{suffix}"
    name_en = f"{prefix}{en_name}{suffix}"

    instance = {
        "id": monster_def.get("id"),
        "name": name,
        "name_en": name_en,
        "max_hp": max(1, hp),
        "atk": max(1, atk),
        "def": max(0, df),
        "spd": max(5, spd),
        "exp": max(1, exp),
        "money_min": max(1, money_min),
        "money_max": max(money_min, money_max),
        "drops": monster_def.get("drops", {}),
        "ai": monster_def.get("ai", "none"),
        "weakness": monster_def.get("weakness", []),
        "resistance": monster_def.get("resistance", []),
        "vitality_type": monster_def.get("vitality_type", "blood"),
        "level": floor,
        "is_boss": is_boss,
    }
    # 行為相關欄位原樣帶過，交給 trpg_monster_ai 在戰鬥中讀取（不需要依樓層縮放）。
    # immunity/damage_cap 以前漏帶了——怪物池的怪一被實例化就會失去屬性免疫與傷害上限。
    for behavior_key in (
        "status_on_hit", "status_chance", "active_skills", "phase2", "revive_once",
        "traits", "ai_spells", "summon_ids", "immunity", "damage_cap", "magic", "mdef", "max_mp",
    ):
        if behavior_key in monster_def:
            instance[behavior_key] = monster_def[behavior_key]
    return instance


def find_monster_def(pool: dict, monster_id: str) -> dict | None:
    """跨 tier 依 id 找出原始怪物定義（給召喚技能解析 summon_ids 用，含一般怪物、boss 與 boss 專屬衍生怪）。"""
    if not pool or not monster_id:
        return None
    for tier in pool.values():
        if tier.get("boss", {}).get("id") == monster_id:
            return tier["boss"]
        monster_def = tier.get("monsters", {}).get(monster_id)
        if monster_def:
            return monster_def
        minion_def = tier.get("boss_minions", {}).get(monster_id)
        if minion_def:
            return minion_def
    return None


def pick_random_monster(pool: dict, floor: int, want_boss: bool = False, floor_scale: float = 1.0) -> dict:
    tier = pick_tier_for_floor(pool, floor)
    if not tier:
        # 完全沒有資料時的保底怪物，避免炸掉戰鬥流程
        fallback = {"id": "unknown", "name": "迷霧怪影", "name_en": "Mist Phantom", "base_hp": 20, "base_atk": 8, "base_def": 2, "base_spd": 10}
        return instantiate_monster(fallback, floor, want_boss, floor_scale)

    if want_boss and tier.get("boss"):
        return instantiate_monster(tier["boss"], floor, True, floor_scale)

    monsters = tier.get("monsters", {})
    if not monsters:
        return instantiate_monster(tier.get("boss", {"id": "unknown", "name": "迷霧怪影", "name_en": "Mist Phantom"}), floor, want_boss, floor_scale)

    monster_def = random.choice(list(monsters.values()))
    return instantiate_monster(monster_def, floor, False, floor_scale)
