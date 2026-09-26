"""Four independent daily gathering loops plus crop food bonus energy."""

from __future__ import annotations

from datetime import date, datetime, timezone
import random


DAILY_ENERGY = 20
XP_PER_LEVEL = 100
MAX_LIFE_LEVEL = 10
LIFE_SKILLS = ("woodcutting", "fishing", "mining", "farming")

CROPS = {
    "wheat": {
        "unlock_level": 1,
        "item": "wheat_bundle",
        "waterings_required": 2,
        "base_quantity": 8,
        "variance": 4,
    },
    "hearty_carrot": {
        "unlock_level": 3,
        "item": "hearty_carrot",
        "waterings_required": 3,
        "base_quantity": 6,
        "variance": 3,
    },
    "moon_berry": {
        "unlock_level": 6,
        "item": "moon_berry",
        "waterings_required": 4,
        "base_quantity": 4,
        "variance": 2,
    },
}


def fresh_farm_state() -> dict:
    return {
        "crop": None,
        "planted_on": "",
        "watered_on": "",
        "waterings": 0,
    }


def utc_today(now: date | datetime | None = None) -> date:
    if now is None:
        return datetime.now(timezone.utc).date()
    if isinstance(now, datetime):
        if now.tzinfo is None:
            return now.date()
        return now.astimezone(timezone.utc).date()
    return now


def fresh_life_state() -> dict:
    return {
        "daily_date": "",
        "energy": {skill: DAILY_ENERGY for skill in LIFE_SKILLS},
        "bonus_energy": 0,
        "woodcutting_xp": 0,
        "fishing_xp": 0,
        "mining_xp": 0,
        "farming_xp": 0,
        "farm": fresh_farm_state(),
    }


def ensure_life_state(player, now: date | datetime | None = None) -> dict:
    real = getattr(player, "real_player", player)
    state = getattr(real, "life_skills", None)
    if not isinstance(state, dict):
        state = fresh_life_state()
        real.life_skills = state
    for key in (f"{skill}_xp" for skill in LIFE_SKILLS):
        state[key] = max(0, int(state.get(key, 0) or 0))
    farm = state.get("farm")
    if not isinstance(farm, dict):
        farm = fresh_farm_state()
        state["farm"] = farm
    farm.setdefault("crop", None)
    farm.setdefault("planted_on", "")
    farm.setdefault("watered_on", "")
    farm["waterings"] = max(0, int(farm.get("waterings", 0) or 0))

    today = utc_today(now).isoformat()
    energy = state.get("energy")
    migrated_legacy_energy = not isinstance(energy, dict)
    if state.get("daily_date") != today or migrated_legacy_energy:
        state["daily_date"] = today
        state["energy"] = {skill: DAILY_ENERGY for skill in LIFE_SKILLS}
        state["bonus_energy"] = 0
    else:
        state["energy"] = {
            skill: max(0, min(DAILY_ENERGY, int(energy.get(skill, DAILY_ENERGY) or 0)))
            for skill in LIFE_SKILLS
        }
        state["bonus_energy"] = max(0, min(DAILY_ENERGY, int(state.get("bonus_energy", 0) or 0)))
    return state


def life_level(state: dict, skill: str) -> int:
    xp = max(0, int(state.get(f"{skill}_xp", 0) or 0))
    return min(MAX_LIFE_LEVEL, 1 + xp // XP_PER_LEVEL)


def available_energy(state: dict, skill: str) -> int:
    energy = state.get("energy") or {}
    base = max(0, int(energy.get(skill, 0) or 0))
    # 農作本身會產出可回復額外行動力的食物；若額外行動力也能拿來農作，
    # 就會形成「收成→吃掉→再收成」的無限循環。農作固定每天 20 次，
    # 食物提供的 bonus 只延長伐木、釣魚、採礦。
    if skill == "farming":
        return base
    return base + max(0, int(state.get("bonus_energy", 0) or 0))


def spend_energy(state: dict, skill: str) -> bool:
    if skill not in LIFE_SKILLS:
        return False
    energy = state.setdefault("energy", {})
    remaining = max(0, int(energy.get(skill, 0) or 0))
    if remaining > 0:
        energy[skill] = remaining - 1
        return True
    if skill == "farming":
        return False
    bonus = max(0, int(state.get("bonus_energy", 0) or 0))
    if bonus > 0:
        state["bonus_energy"] = bonus - 1
        return True
    return False


def consume_life_food(state: dict, inventory: dict, item_id: str, restore: int) -> int:
    """Consume one farm food and refill shared bonus life energy."""
    restore = max(0, int(restore or 0))
    current = max(0, min(DAILY_ENERGY, int(state.get("bonus_energy", 0) or 0)))
    if restore <= 0 or current >= DAILY_ENERGY or inventory.get(item_id, 0) <= 0:
        return 0
    restored = min(restore, DAILY_ENERGY - current)
    inventory[item_id] -= 1
    if inventory[item_id] <= 0:
        del inventory[item_id]
    state["bonus_energy"] = current + restored
    return restored


def _grant_xp(state: dict, skill: str, amount: int) -> tuple[int, int]:
    before = life_level(state, skill)
    key = f"{skill}_xp"
    state[key] = max(0, int(state.get(key, 0) or 0)) + max(0, int(amount))
    return before, life_level(state, skill)


def gather(state: dict, skill: str, rng=random) -> dict:
    """Roll one gathering action after the caller spends life energy."""
    if skill not in {"woodcutting", "fishing", "mining"}:
        raise ValueError(f"unsupported life skill: {skill}")
    level = life_level(state, skill)
    quantity = 1 + (level - 1) // 3
    if skill == "woodcutting":
        roll = rng.random()
        if level >= 7 and roll < min(0.08, 0.025 + (level - 7) * 0.018):
            wood_id = "legendary_lumber"
        elif level >= 4 and roll < min(0.12, 0.06 + (level - 4) * 0.01):
            wood_id = "premium_lumber"
        elif level >= 2 and roll < min(0.30, 0.20 + (level - 2) * 0.015):
            wood_id = "rare_lumber"
        else:
            wood_id = "fresh_lumber"
        drops = {wood_id: quantity}
        if rng.random() < min(0.18, 0.05 + (level - 1) * 0.01):
            drops["mystic_sap"] = 1
    elif skill == "fishing":
        drops = {"river_fish": quantity}
        if rng.random() < min(0.18, 0.05 + (level - 1) * 0.01):
            drops["golden_carp"] = 1
    else:
        drops = {"stone": quantity}
        roll = rng.random()
        if level >= 5 and roll < min(0.15, 0.04 + (level - 5) * 0.022):
            drops["diamond"] = 1
        elif level >= 2 and roll < min(0.35, 0.22 + (level - 2) * 0.02):
            drops["gold_ore"] = 1
    old_level, new_level = _grant_xp(state, skill, 5)
    return {"drops": drops, "xp": 5, "old_level": old_level, "new_level": new_level}


def farm_ready(state: dict, now: date | datetime | None = None) -> bool:
    """Return whether the selected crop has received enough daily waterings."""
    farm = state.get("farm") or {}
    crop_data = CROPS.get(farm.get("crop"))
    if not crop_data:
        return False
    return int(farm.get("waterings", 0) or 0) >= crop_data["waterings_required"]


def farm_needs_water(state: dict, now: date | datetime | None = None) -> bool:
    farm = state.get("farm") or {}
    if farm.get("crop") not in CROPS or farm_ready(state, now):
        return False
    return farm.get("watered_on") != utc_today(now).isoformat()


def plant_crop(state: dict, crop: str, now: date | datetime | None = None) -> bool:
    farm = state.setdefault("farm", fresh_farm_state())
    crop_data = CROPS.get(crop)
    if farm.get("crop") or not crop_data or life_level(state, "farming") < crop_data["unlock_level"]:
        return False
    farm["crop"] = crop
    farm["planted_on"] = utc_today(now).isoformat()
    farm["watered_on"] = ""
    farm["waterings"] = 0
    return True


def plant_wheat(state: dict, now: date | datetime | None = None) -> bool:
    """Backward-compatible wrapper used by older call sites and saves."""
    return plant_crop(state, "wheat", now)


def water_farm(state: dict, now: date | datetime | None = None) -> dict | None:
    """Water the active crop once per UTC day; missed days pause but never kill it."""
    if not farm_needs_water(state, now):
        return None
    farm = state["farm"]
    crop = farm["crop"]
    required = CROPS[crop]["waterings_required"]
    farm["waterings"] = min(required, int(farm.get("waterings", 0) or 0) + 1)
    farm["watered_on"] = utc_today(now).isoformat()
    old_level, new_level = _grant_xp(state, "farming", 10)
    return {
        "crop": crop,
        "waterings": farm["waterings"],
        "required": required,
        "ready": farm["waterings"] >= required,
        "xp": 10,
        "old_level": old_level,
        "new_level": new_level,
    }


def harvest_farm(state: dict, now: date | datetime | None = None, rng=random) -> dict | None:
    if not farm_ready(state, now):
        return None
    level = life_level(state, "farming")
    crop = state.get("farm", {}).get("crop") or "wheat"
    crop_data = CROPS.get(crop, CROPS["wheat"])
    quantity = crop_data["base_quantity"] + (level - 1) // 3 + rng.randint(0, crop_data["variance"])
    drops = {crop_data["item"]: quantity}
    rare_chance = min(0.20, 0.06 + (level - 1) * 0.012)
    if crop == "wheat" and rng.random() < rare_chance:
        drops["golden_wheat"] = 1
    old_level, new_level = _grant_xp(state, "farming", 20)
    state["farm"] = fresh_farm_state()
    return {"crop": crop, "drops": drops, "xp": 20, "old_level": old_level, "new_level": new_level}
