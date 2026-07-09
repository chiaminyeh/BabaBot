from trpg.balance import (
    ALLOC_BONUS, STAT_POINTS_PER_LEVEL, PRESTIGE_BONUS_PER_LEVEL, SET_BONUSES,
    PRESTIGE_BASE_LEVEL, PRESTIGE_LEVEL_STEP,
)
from trpg.i18n import t

STAT_KEYS = ("atk", "vit", "int", "spd", "luck")
STAT_ABBR = {"atk": "ATK", "vit": "VIT", "int": "INT", "spd": "SPD", "luck": "LUCK"}
POINTS_PER_LEVEL = STAT_POINTS_PER_LEVEL


def prestige_required_level(prestige_count: int) -> int:
    return PRESTIGE_BASE_LEVEL + prestige_count * PRESTIGE_LEVEL_STEP


def default_stat_alloc() -> dict:
    return {k: 0 for k in STAT_KEYS}


def item_stat_requirements(item_data: dict) -> dict:
    reqs = item_data.get("stat_requirements") or {}
    return {
        key: int(reqs[key])
        for key in STAT_KEYS
        if key in reqs and int(reqs[key]) > 0
    }


def meets_item_stat_requirements(player, item_data: dict) -> tuple[bool, dict]:
    reqs = item_stat_requirements(item_data)
    alloc = getattr(player, "stat_alloc", None) or default_stat_alloc()
    missing = {
        stat: need
        for stat, need in reqs.items()
        if int(alloc.get(stat, 0)) < need
    }
    return not missing, missing


def format_stat_requirement_map(reqs: dict, lang: str = "zh", with_prefix: bool = True) -> str:
    if not reqs:
        return ""
    body = "/".join(f"{STAT_ABBR.get(stat, stat.upper())} {need}" for stat, need in reqs.items())
    if not with_prefix:
        return body
    return f"Requires {body}" if lang == "en" else f"需求 {body}"


def format_item_stat_requirements(item_data: dict, lang: str = "zh", with_prefix: bool = True) -> str:
    return format_stat_requirement_map(item_stat_requirements(item_data), lang=lang, with_prefix=with_prefix)


def total_stat_points(level: int) -> int:
    return POINTS_PER_LEVEL * level


def get_allocated_points(stat_alloc: dict) -> int:
    return sum(stat_alloc.get(k, 0) for k in STAT_KEYS)


def get_unspent_points(player) -> int:
    alloc = getattr(player, "stat_alloc", None) or default_stat_alloc()
    return total_stat_points(player.level) - get_allocated_points(alloc)


def get_equipment_bonuses(player, items: dict) -> dict:
    bonuses = {"atk": 0, "def": 0, "mdef": 0, "hp": 0, "magic": 0, "spd": 0, "mp": 0, "luck": 0}
    set_counts = {}
    for slot in (
        getattr(player, "weapon", None),
        getattr(player, "armor", None),
        getattr(player, "accessory", None),
    ):
        if slot and slot in items:
            eq = items[slot]
            bonuses["atk"] += eq.get("atk_bonus", 0)
            bonuses["def"] += eq.get("def_bonus", 0)
            bonuses["mdef"] += eq.get("mdef_bonus", 0)
            bonuses["hp"] += eq.get("hp_bonus", 0)
            bonuses["magic"] += eq.get("magic_bonus", 0)
            bonuses["spd"] += eq.get("spd_bonus", 0)
            bonuses["mp"] += eq.get("mp_bonus", 0)
            bonuses["luck"] += eq.get("luck_bonus", 0)
            set_name = eq.get("set")
            if set_name:
                set_counts[set_name] = set_counts.get(set_name, 0) + 1

    for set_name, count in set_counts.items():
        for need, set_bonus in SET_BONUSES.get(set_name, {}).items():
            if count >= need:
                for stat, val in set_bonus.items():
                    bonuses[stat] = bonuses.get(stat, 0) + val
    return bonuses


def active_set_bonuses(player, items: dict) -> list:
    set_counts = {}
    for slot in (getattr(player, "weapon", None), getattr(player, "armor", None), getattr(player, "accessory", None)):
        if slot and slot in items:
            s = items[slot].get("set")
            if s:
                set_counts[s] = set_counts.get(s, 0) + 1
    active = []
    for set_name, count in set_counts.items():
        if any(count >= need for need in SET_BONUSES.get(set_name, {})):
            active.append((set_name, count))
    return active


def recalc_player_stats(player, items: dict = None, heal_full: bool = False):
    level = player.level
    alloc = getattr(player, "stat_alloc", None) or default_stat_alloc()
    eq = get_equipment_bonuses(player, items or {})
    prestige = getattr(player, "prestige_count", 0)
    prestige_mult = 1.0 + prestige * PRESTIGE_BONUS_PER_LEVEL
    weapon_upgrades = getattr(player, "weapon_upgrades", None) or {}
    armor_upgrades = getattr(player, "armor_upgrades", None) or {}

    old_max_hp = getattr(player, "max_hp", 60)
    old_max_mp = getattr(player, "max_mp", 25)

    base_atk = 10 + level * 2 + alloc.get("atk", 0) * ALLOC_BONUS["atk"] + eq["atk"]
    if getattr(player, "weapon", None):
        base_atk += weapon_upgrades.get(player.weapon, 0) * 3
    player.base_atk = int(base_atk * prestige_mult)

    base_def = 4 + level * 1.5 + alloc.get("vit", 0) * 2 + eq["def"]
    if getattr(player, "armor", None):
        base_def += armor_upgrades.get(player.armor, 0) * 2
    player.base_def = int(base_def * prestige_mult)

    base_max_hp = 50 + level * 10 + alloc.get("vit", 0) * 12 + eq["hp"]
    if getattr(player, "armor", None):
        base_max_hp += armor_upgrades.get(player.armor, 0) * 15
    player.max_hp = int(base_max_hp * prestige_mult)

    base_int = 10 + int(level * 2.5) + alloc.get("int", 0) * 4 + eq["magic"]
    player.base_int = int(base_int * prestige_mult)

    base_mdef = 2 + level * 1.0 + alloc.get("int", 0) * 1.5 + alloc.get("vit", 0) * 0.5 + eq["mdef"]
    player.base_mdef = int(base_mdef * prestige_mult)

    base_max_mp = 40 + level * 6 + alloc.get("int", 0) * 3 + eq["mp"]
    player.max_mp = int(base_max_mp * prestige_mult)

    base_spd = 10 + level + alloc.get("spd", 0) * ALLOC_BONUS["spd"] + eq["spd"]
    player.base_spd = int(base_spd * prestige_mult)
    player.base_res = 0

    base_luck = alloc.get("luck", 0) * ALLOC_BONUS["luck"] + eq["luck"]
    player.base_luck = int(base_luck * prestige_mult)

    if heal_full:
        player.current_hp = player.max_hp
        player.current_mp = player.max_mp
    else:
        if old_max_hp > 0:
            ratio = player.current_hp / old_max_hp
            player.current_hp = min(player.max_hp, max(0, int(player.max_hp * ratio)))
        else:
            player.current_hp = min(player.current_hp, player.max_hp)
        if old_max_mp > 0:
            ratio_mp = player.current_mp / old_max_mp
            player.current_mp = min(player.max_mp, max(0, int(player.max_mp * ratio_mp)))
        else:
            player.current_mp = min(player.current_mp, player.max_mp)

    player.current_hp = min(player.current_hp, player.max_hp)
    player.current_mp = min(player.current_mp, player.max_mp)


def migrate_player_stats(player, items: dict):
    if not getattr(player, "stat_alloc", None):
        player.stat_alloc = default_stat_alloc()
    else:
        if "hp" in player.stat_alloc or "def" in player.stat_alloc:
            player.stat_alloc["vit"] = player.stat_alloc.pop("hp", 0) + player.stat_alloc.pop("def", 0)
        if "magic" in player.stat_alloc:
            player.stat_alloc["int"] = player.stat_alloc.pop("magic", 0)
        player.stat_alloc.pop("res", None)
        for key in STAT_KEYS:
            if key not in player.stat_alloc:
                player.stat_alloc[key] = 0

    if not hasattr(player, "base_int"):
        player.base_int = getattr(player, "base_magic", 0)
    if not hasattr(player, "base_res"):
        player.base_res = 0
    if not hasattr(player, "base_spd"):
        player.base_spd = 5 + player.level
    if not hasattr(player, "base_mdef"):
        player.base_mdef = 0

    if not hasattr(player, "equipped_skills") or player.equipped_skills is None:
        player.equipped_skills = player.skills[:8] if getattr(player, "skills", None) else []

    if not isinstance(getattr(player, "weapon_upgrades", None), dict):
        player.weapon_upgrades = {}
    if not isinstance(getattr(player, "armor_upgrades", None), dict):
        player.armor_upgrades = {}
    old_weapon_up = getattr(player, "weapon_upgrade", 0)
    if old_weapon_up and getattr(player, "weapon", None):
        player.weapon_upgrades[player.weapon] = old_weapon_up
        player.weapon_upgrade = 0
    old_armor_up = getattr(player, "armor_upgrade", 0)
    if old_armor_up and getattr(player, "armor", None):
        player.armor_upgrades[player.armor] = old_armor_up
        player.armor_upgrade = 0

    if not hasattr(player, "mystery_merchant_date"):
        player.mystery_merchant_date = ""
    if not hasattr(player, "mystery_shop_items"):
        player.mystery_shop_items = []
    if not hasattr(player, "mystery_shop_active"):
        player.mystery_shop_active = False
    recalc_player_stats(player, items, heal_full=False)


def format_stat_alloc_summary(player) -> str:
    alloc = getattr(player, "stat_alloc", default_stat_alloc())
    unspent = get_unspent_points(player)
    lang = getattr(player, "language", "zh")
    return "\n".join([
        f"ATK {alloc.get('atk', 0)} | VIT {alloc.get('vit', 0)} | INT {alloc.get('int', 0)}",
        f"SPD {alloc.get('spd', 0)} | LUCK {alloc.get('luck', 0)}",
        t(lang, "stats.alloc_line3", "剩餘點數 {unspent}", unspent=unspent),
    ])


def get_potion_heal_target(item_data: dict, item_id: str = "") -> str:
    if item_data.get("heal_target") in ("hp", "mp"):
        return item_data["heal_target"]
    if "mana" in item_id:
        return "mp"
    return "hp"
