from trpg.balance import (
    ALLOC_BONUS, STAT_POINTS_PER_LEVEL, PRESTIGE_BONUS_PER_LEVEL, SET_BONUSES,
    PRESTIGE_BASE_LEVEL, PRESTIGE_LEVEL_STEP,
)
from trpg.i18n import t

STAT_KEYS = ("knight", "rogue", "mage", "warlock")
STAT_ABBR = {
    "knight": "WAR",
    "rogue": "RGE",
    "mage": "MAG",
    "warlock": "WRL",

}
STAT_LABELS = {
    "knight": {"zh": "戰士", "en": "Warrior"},
    "rogue": {"zh": "盜賊", "en": "Rogue"},
    "mage": {"zh": "法師", "en": "Mage"},
    "warlock": {"zh": "術士", "en": "Warlock"},

}
LEGACY_STAT_TO_ARCHETYPE = {
    "atk": {"knight": 1},
    "vit": {"knight": 1},
    "int": {"mage": 1},
    "spd": {"rogue": 1},
    "luck": {},
    "hp": {"knight": 1},
    "def": {"knight": 1},
    "magic": {"mage": 1},
    "res": {},
}
POINTS_PER_LEVEL = STAT_POINTS_PER_LEVEL
DEPRECATED_PROGRESSION_SKILLS = frozenset(("combo_attack", "battle_focus"))
DEPRECATED_ITEM_REPLACEMENTS = {
    "scroll_battle_focus": "skill_manual",
}


def prestige_required_level(prestige_count: int) -> int:
    return PRESTIGE_BASE_LEVEL + prestige_count * PRESTIGE_LEVEL_STEP


def default_stat_alloc() -> dict:
    return {k: 0 for k in STAT_KEYS}


def stat_display_name(stat_key: str, lang: str = "zh", short: bool = False) -> str:
    stat_key = str(stat_key)
    if short:
        return STAT_ABBR.get(stat_key, stat_key.upper())
    return STAT_LABELS.get(stat_key, {}).get(lang, stat_key.title())


def normalize_stat_alloc(raw_alloc: dict | None) -> dict:
    normalized = default_stat_alloc()
    if not isinstance(raw_alloc, dict):
        return normalized

    for key, raw_value in raw_alloc.items():
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            continue
        if value == 0:
            continue
        if key in normalized:
            normalized[key] += value
            continue
        for new_key, weight in LEGACY_STAT_TO_ARCHETYPE.get(key, {}).items():
            normalized[new_key] += value * weight
    return normalized


def normalize_requirement_map(reqs: dict | None) -> dict:
    normalized = default_stat_alloc()
    if not isinstance(reqs, dict):
        return {}

    for key, raw_value in reqs.items():
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        if key in normalized:
            normalized[key] = max(normalized[key], value)
            continue
        for new_key, weight in LEGACY_STAT_TO_ARCHETYPE.get(key, {}).items():
            normalized[new_key] = max(normalized[new_key], value * weight)
    return {key: value for key, value in normalized.items() if value > 0}


def item_stat_requirements(item_data: dict) -> dict:
    return normalize_requirement_map(item_data.get("stat_requirements") or {})


def skill_point_requirements(skill_data: dict) -> dict:
    return normalize_requirement_map(skill_data.get("req_points") or {})


def meets_point_requirements(player, reqs: dict | None) -> tuple[bool, dict]:
    reqs = normalize_requirement_map(reqs)
    alloc = normalize_stat_alloc(getattr(player, "stat_alloc", None) or {})
    missing = {
        stat: need
        for stat, need in reqs.items()
        if int(alloc.get(stat, 0)) < need
    }
    return not missing, missing


def meets_item_stat_requirements(player, item_data: dict) -> tuple[bool, dict]:
    return meets_point_requirements(player, item_stat_requirements(item_data))


def meets_skill_point_requirements(player, skill_data: dict) -> tuple[bool, dict]:
    return meets_point_requirements(player, skill_point_requirements(skill_data))


def meets_skill_requirements(player, skill_data: dict) -> tuple[bool, dict, int]:
    req_level = int(skill_data.get("req_level", 1) or 1)
    _, missing = meets_skill_point_requirements(player, skill_data)
    return getattr(player, "level", 1) >= req_level and not missing, missing, req_level


def format_stat_requirement_map(reqs: dict, lang: str = "zh", with_prefix: bool = True) -> str:
    reqs = normalize_requirement_map(reqs)
    if not reqs:
        return ""
    body = "/".join(f"{stat_display_name(stat, lang, short=True)} {need}" for stat, need in reqs.items())
    if not with_prefix:
        return body
    return f"Requires {body}" if lang == "en" else f"需求 {body}"


def format_item_stat_requirements(item_data: dict, lang: str = "zh", with_prefix: bool = True) -> str:
    return format_stat_requirement_map(item_stat_requirements(item_data), lang=lang, with_prefix=with_prefix)


def format_skill_point_requirements(skill_data: dict, lang: str = "zh", with_prefix: bool = True) -> str:
    return format_stat_requirement_map(skill_point_requirements(skill_data), lang=lang, with_prefix=with_prefix)


def prune_unqualified_skills(player, skills_data: dict) -> list[str]:
    learned = list(dict.fromkeys(getattr(player, "skills", []) or []))
    equipped = list(dict.fromkeys(getattr(player, "equipped_skills", []) or []))
    removed = []
    kept_skills = []
    for skill_id in learned:
        skill = skills_data.get(skill_id)
        if not skill:
            removed.append(skill_id)
            continue
        ok, _, _ = meets_skill_requirements(player, skill)
        if ok:
            kept_skills.append(skill_id)
        else:
            removed.append(skill_id)
    player.skills = kept_skills
    player.equipped_skills = [skill_id for skill_id in equipped if skill_id in kept_skills]
    return removed


def grant_qualified_skills(player, skills_data: dict, max_equipped: int = 8) -> list[tuple[str, bool]]:
    """Grant newly-qualified archetype skills and fill empty skill slots.

    Skills without ``req_points`` remain scroll/drop progression and are never
    auto-granted. Returns ``(skill_id, auto_equipped)`` for each new skill.
    """
    learned = list(dict.fromkeys(getattr(player, "skills", []) or []))
    equipped = list(dict.fromkeys(getattr(player, "equipped_skills", []) or []))
    granted = []
    for skill_id, skill_data in skills_data.items():
        if skill_id in learned or not skill_data.get("req_points"):
            continue
        qualified, _, _ = meets_skill_requirements(player, skill_data)
        if not qualified:
            continue
        learned.append(skill_id)
        auto_equipped = len(equipped) < max_equipped
        if auto_equipped:
            equipped.append(skill_id)
        granted.append((skill_id, auto_equipped))
    player.skills = learned
    player.equipped_skills = equipped
    return granted


def total_stat_points(level: int) -> int:
    return POINTS_PER_LEVEL * level


def get_allocated_points(stat_alloc: dict) -> int:
    alloc = normalize_stat_alloc(stat_alloc)
    return sum(alloc.get(k, 0) for k in STAT_KEYS)


def get_unspent_points(player) -> int:
    alloc = normalize_stat_alloc(getattr(player, "stat_alloc", None) or {})
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
    alloc = normalize_stat_alloc(getattr(player, "stat_alloc", None) or {})
    player.stat_alloc = alloc
    eq = get_equipment_bonuses(player, items or {})
    prestige = getattr(player, "prestige_count", 0)
    prestige_mult = 1.0 + prestige * PRESTIGE_BONUS_PER_LEVEL
    weapon_upgrades = getattr(player, "weapon_upgrades", None) or {}
    armor_upgrades = getattr(player, "armor_upgrades", None) or {}

    old_max_hp = getattr(player, "max_hp", 60)
    old_max_mp = getattr(player, "max_mp", 25)

    knight = alloc.get("knight", 0)
    rogue = alloc.get("rogue", 0)
    mage = alloc.get("mage", 0)
    warlock = alloc.get("warlock", 0)


    base_atk = (
        10 + level * 2
        + knight * ALLOC_BONUS["knight"]
        + rogue * 1.5
        + warlock * 0.5
        + eq["atk"]
    )
    if getattr(player, "weapon", None):
        base_atk += weapon_upgrades.get(player.weapon, 0) * 3
    player.base_atk = int(base_atk * prestige_mult)

    base_def = 4 + level * 1.5 + knight * 2.5 + rogue * 0.3 + warlock * 0.4 + eq["def"]
    if getattr(player, "armor", None):
        base_def += armor_upgrades.get(player.armor, 0) * 2
    player.base_def = int(base_def * prestige_mult)

    base_max_hp = 50 + level * 10 + knight * 14 + warlock * 4 + rogue * 2 + eq["hp"]
    if getattr(player, "armor", None):
        base_max_hp += armor_upgrades.get(player.armor, 0) * 15
    player.max_hp = int(base_max_hp * prestige_mult)

    base_int = 10 + int(level * 2.5) + mage * 4.5 + warlock * 3.5 + eq["magic"]
    player.base_int = int(base_int * prestige_mult)
    player.base_magic = player.base_int

    base_mdef = 2 + level * 1.0 + mage * 1.4 + warlock * 1.2 + knight * 0.5 + eq["mdef"]
    player.base_mdef = int(base_mdef * prestige_mult)

    base_max_mp = 40 + level * 6 + mage * 4 + warlock * 3 + eq["mp"]
    player.max_mp = int(base_max_mp * prestige_mult)

    base_spd = 10 + level + rogue * ALLOC_BONUS["rogue"] + mage * 0.3 + eq["spd"]
    player.base_spd = int(base_spd * prestige_mult)

    # Hidden Fortune is event-driven; gear and archetype allocation never
    # mutate it. base_luck remains a compatibility display field only.
    player.base_luck = 0

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


def migrate_player_stats(player, items: dict, skills_data: dict | None = None):
    from trpg.archetypes import clamp_fortune, normalize_core_selection

    player.stat_alloc = normalize_stat_alloc(getattr(player, "stat_alloc", None) or {})
    player.fortune = clamp_fortune(getattr(player, "fortune", 0) or 0)
    normalize_core_selection(player)
    player.skills = [skill_id for skill_id in (getattr(player, "skills", None) or []) if skill_id not in DEPRECATED_PROGRESSION_SKILLS]
    player.equipped_skills = [skill_id for skill_id in (getattr(player, "equipped_skills", None) or []) if skill_id not in DEPRECATED_PROGRESSION_SKILLS]
    # Replace retired items before catalog pruning so existing owners keep an
    # equivalent usable reward instead of losing inventory during migration.
    migrated_inventory = {}
    for item_id, count in (getattr(player, "inventory", None) or {}).items():
        replacement = DEPRECATED_ITEM_REPLACEMENTS.get(item_id, item_id)
        if replacement in items and count > 0:
            migrated_inventory[replacement] = migrated_inventory.get(replacement, 0) + count
    player.inventory = migrated_inventory
    # Cached regional shop rolls also persist item IDs; remove retired scrolls
    # there so a later refresh cannot re-display an invalid offer.
    for shop in (getattr(player, "shop_state", None) or {}).values():
        if not isinstance(shop, dict):
            continue
        for key in ("items", "mystery_items"):
            if isinstance(shop.get(key), list):
                migrated = [DEPRECATED_ITEM_REPLACEMENTS.get(item_id, item_id) for item_id in shop[key]]
                shop[key] = list(dict.fromkeys(item_id for item_id in migrated if item_id in items))
    for key in ("shop_items", "mystery_shop_items"):
        if isinstance(getattr(player, key, None), list):
            migrated = [DEPRECATED_ITEM_REPLACEMENTS.get(item_id, item_id) for item_id in getattr(player, key)]
            setattr(player, key, list(dict.fromkeys(item_id for item_id in migrated if item_id in items)))

    if not hasattr(player, "base_int"):
        player.base_int = getattr(player, "base_magic", 0)
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
    if skills_data is not None:
        removed = prune_unqualified_skills(player, skills_data)
        grant_qualified_skills(player, skills_data)
        return removed
    return []


def format_stat_alloc_summary(player) -> str:
    alloc = normalize_stat_alloc(getattr(player, "stat_alloc", default_stat_alloc()))
    unspent = get_unspent_points(player)
    lang = getattr(player, "language", "zh")
    row1 = " | ".join(
        f"{stat_display_name(key, lang, short=True)} {alloc.get(key, 0)}"
        for key in ("knight", "rogue", "mage")
    )
    row2 = f"{stat_display_name('warlock', lang, short=True)} {alloc.get('warlock', 0)}"
    return "\n".join([
        row1,
        row2,
        t(lang, "stats.alloc_line3", "剩餘點數 {unspent}", unspent=unspent),
    ])


def get_potion_heal_target(item_data: dict, item_id: str = "") -> str:
    if item_data.get("heal_target") in ("hp", "mp"):
        return item_data["heal_target"]
    if "mana" in item_id:
        return "mp"
    return "hp"
