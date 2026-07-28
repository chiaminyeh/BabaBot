"""skill_progression.py — 技能熟練度與升級規則合約"""

from trpg.i18n import t

MAX_SKILL_LEVEL = 5

PROFICIENCY_THRESHOLDS = [20, 50, 100, 200]
UPGRADE_GOLD_COSTS = [500, 1500, 3000, 6000]

EFFECT_MULTIPLIER_PER_LEVEL = 1.20
COST_MULTIPLIER_PER_LEVEL = 1.15

def normalize_skill_level(level) -> int:
    try:
        level = int(level)
    except (ValueError, TypeError):
        level = 1
    return max(1, min(MAX_SKILL_LEVEL, level))

def get_proficiency_requirement(current_level: int) -> int:
    current_level = normalize_skill_level(current_level)
    if current_level >= MAX_SKILL_LEVEL:
        return 0
    return PROFICIENCY_THRESHOLDS[current_level - 1]

def get_base_upgrade_cost(current_level: int) -> int:
    current_level = normalize_skill_level(current_level)
    if current_level >= MAX_SKILL_LEVEL:
        return 0
    return UPGRADE_GOLD_COSTS[current_level - 1]

def get_upgrade_cost_with_discount(current_level: int, current_usage: int) -> int:
    base_cost = get_base_upgrade_cost(current_level)
    if base_cost == 0:
        return 0
    threshold = get_proficiency_requirement(current_level)
    if threshold <= 0:
        return 0

    try:
        current_usage = max(0, int(current_usage))
    except (ValueError, TypeError):
        current_usage = 0

    current_usage = min(current_usage, threshold)
    discount = int(base_cost * (current_usage / threshold))
    return max(0, base_cost - discount)

def is_proficiency_ready(player, skill_id: str, current_level: int | None = None) -> bool:
    lv = current_level or normalize_skill_level((getattr(player, "skill_levels", None) or {}).get(skill_id, 1))
    threshold = get_proficiency_requirement(lv)
    if threshold <= 0:
        return True
    try:
        usage = max(0, int((getattr(player, "skill_usage", None) or {}).get(skill_id, 0)))
    except (ValueError, TypeError):
        usage = 0
    return usage >= threshold

def get_skill_effect_multiplier(level: int) -> float:
    level = normalize_skill_level(level)
    return EFFECT_MULTIPLIER_PER_LEVEL ** (level - 1)

def get_skill_cost_multiplier(level: int) -> float:
    level = normalize_skill_level(level)
    return COST_MULTIPLIER_PER_LEVEL ** (level - 1)

def record_successful_skill_use(player, skill_id: str, skill_name: str, lang: str) -> str:
    if getattr(player, "skill_usage", None) is None or not isinstance(player.skill_usage, dict):
        player.skill_usage = {}
    if getattr(player, "skill_levels", None) is None or not isinstance(player.skill_levels, dict):
        player.skill_levels = {}

    current_lv = normalize_skill_level(player.skill_levels.get(skill_id, 1))
    player.skill_levels[skill_id] = current_lv

    if current_lv >= MAX_SKILL_LEVEL:
        return ""

    usage = player.skill_usage.get(skill_id, 0)
    try:
        usage = max(0, int(usage))
    except (ValueError, TypeError):
        usage = 0

    threshold = get_proficiency_requirement(current_lv)
    if usage >= threshold:
        player.skill_usage[skill_id] = threshold
        return ""

    usage += 1

    if usage >= threshold:
        player.skill_usage[skill_id] = threshold
        return "\n" + t(lang, "combat.skill_proficiency_ready",
                        "✨ 【{skill}】熟練度已達標！現在可以花費 {cost} Bababucks 升級至 Lv.{lv}。",
                        skill=skill_name, cost=get_base_upgrade_cost(current_lv), lv=current_lv + 1)

    player.skill_usage[skill_id] = usage
    return ""

def validate_paid_upgrade(player, skill_data, skill_id: str, available_money: int | None = None) -> tuple[bool, str, int]:
    """
    驗證並計算升級。回傳 (是否允許, 錯誤訊息/空字串, 需要的金幣)。
    """
    lang = getattr(player, "language", "zh")
    if not skill_data:
        return False, t(lang, "upgrade.skill_not_found", "❌ 找不到該技能資料，請聯絡管理員。"), 0

    if skill_data.get("type") == "passive":
        return False, t(lang, "upgrade.passive_not_upgradable", "❌ 被動技能無法使用金幣強化。"), 0

    equipped = getattr(player, "equipped_skills", []) or []
    if skill_id not in equipped:
        return False, t(lang, "upgrade.skill_not_equipped", "❌ 技能未裝備，無法強化。"), 0

    lv = normalize_skill_level((getattr(player, "skill_levels", None) or {}).get(skill_id, 1))
    if lv >= MAX_SKILL_LEVEL:
        name = skill_data.get("name", {}).get(lang, skill_id) if isinstance(skill_data.get("name"), dict) else skill_data.get("name", skill_id)
        return False, t(lang, "upgrade.already_max", "✅ 【{skill}】已達 Lv.{max} 滿級，無法再強化！", skill=name, max=MAX_SKILL_LEVEL), 0

    if not is_proficiency_ready(player, skill_id, lv):
        threshold = get_proficiency_requirement(lv)
        usage = (getattr(player, "skill_usage", None) or {}).get(skill_id, 0)
        return False, t(lang, "upgrade.proficiency_required",
              "❌ 需要先達到熟練度 {need} 次，目前為 {have}/{need}，才能使用 Bababucks 升級。",
              have=usage, need=threshold), 0

    cost = get_base_upgrade_cost(lv)

    money = (getattr(player, "money", 0) or 0) if available_money is None else max(0, int(available_money))
    if money < cost:
        name = skill_data.get("name", {}).get(lang, skill_id) if isinstance(skill_data.get("name"), dict) else skill_data.get("name", skill_id)
        return False, t(lang, "upgrade.not_enough_gold",
              "❌ Bababucks 不足！強化【{skill}】至 Lv.{next_lv} 需要 {cost} Bababucks，你目前只有 {have}。",
              skill=name, next_lv=lv + 1, cost=cost, have=money), cost

    return True, "", cost

def apply_paid_upgrade(player, skill_id: str, cost: int, *, charge_player_money: bool = True, record_spending: bool = True):
    """套用金幣升級，扣錢並升級。"""
    if charge_player_money:
        if not hasattr(player, "money"):
            player.money = 0
        player.money -= cost

    if getattr(player, "skill_levels", None) is None or not isinstance(player.skill_levels, dict):
        player.skill_levels = {}
    if getattr(player, "skill_usage", None) is None or not isinstance(player.skill_usage, dict):
        player.skill_usage = {}

    lv = normalize_skill_level(player.skill_levels.get(skill_id, 1))
    player.skill_levels[skill_id] = lv + 1
    player.skill_usage[skill_id] = 0

    stats = getattr(player, "stats", None)
    if record_spending and isinstance(stats, dict):
        stats["money_spent"] = stats.get("money_spent", 0) + cost


def get_manual_cost(current_level: int) -> int:
    """技能指南消耗數量 = 目前等級。Lv1→2 費 1 本，Lv4→5 費 4 本。"""
    current_level = normalize_skill_level(current_level)
    return current_level  # current_level is always 1..4 here (5 is max)


def validate_manual_upgrade(player, skill_data, skill_id: str) -> tuple[bool, str, int]:
    """
    驗證技能指南升級。回傳 (是否允許, 訊息, 需要的指南數量)。
    """
    lang = getattr(player, "language", "zh")
    if not skill_data:
        return False, t(lang, "upgrade.skill_not_found", "❌ 找不到該技能資料，請聯絡管理員。"), 0

    if skill_data.get("type") == "passive":
        return False, t(lang, "upgrade.passive_not_upgradable", "❌ 被動技能無法強化。"), 0

    equipped = getattr(player, "equipped_skills", []) or []
    if skill_id not in equipped:
        return False, t(lang, "upgrade.skill_not_equipped", "❌ 技能未裝備，無法強化。"), 0

    lv = normalize_skill_level((getattr(player, "skill_levels", None) or {}).get(skill_id, 1))
    if lv >= MAX_SKILL_LEVEL:
        name = skill_data.get("name", skill_id)
        return False, t(lang, "upgrade.already_max", "✅ 【{skill}】已達 Lv.{max} 滿級！", skill=name, max=MAX_SKILL_LEVEL), 0

    need = get_manual_cost(lv)
    have = (getattr(player, "inventory", None) or {}).get("skill_manual", 0)
    if have < need:
        return False, t(lang, "upgrade.not_enough_manuals",
                        "❌ 技能指南不足！需要 {need} 本，目前持有 {have} 本。",
                        need=need, have=have), need
    return True, "", need


def apply_manual_upgrade(player, skill_id: str, need: int):
    """消耗技能指南並升級技能。"""
    inv = getattr(player, "inventory", None)
    if not isinstance(inv, dict):
        inv = {}
        player.inventory = inv
    inv["skill_manual"] = max(0, inv.get("skill_manual", 0) - need)
    if inv["skill_manual"] == 0:
        del inv["skill_manual"]

    if getattr(player, "skill_levels", None) is None or not isinstance(player.skill_levels, dict):
        player.skill_levels = {}
    if getattr(player, "skill_usage", None) is None or not isinstance(player.skill_usage, dict):
        player.skill_usage = {}

    lv = normalize_skill_level(player.skill_levels.get(skill_id, 1))
    player.skill_levels[skill_id] = lv + 1
    player.skill_usage[skill_id] = 0
