"""Core archetype identities and bounded hidden Fortune rules."""

from __future__ import annotations

from trpg.i18n import t

CORE_POINT_REQUIREMENT = 10
FORTUNE_MIN = -10
FORTUNE_MAX = 10
CORE_KEYS = ("knight", "rogue", "mage", "warlock")

CORE_ABILITIES = {
    "knight": {
        "name": "🛡️ 鋼鐵體魄",
        "name_en": "🛡️ Iron Constitution",
        "desc": "核心被動：防禦力提高 20%。簡單、可靠，但真實傷害與無視防禦仍能穿透你。",
        "desc_en": "Core Passive: DEF +20%. Simple and reliable, but true damage and defense-piercing attacks still bypass it.",
    },
    "rogue": {
        "name": "🔄 連擊節奏",
        "name_en": "🔄 Combo Rhythm",
        "desc": "核心被動：成功攻擊獲得 1 層連擊（最多 3 層）；每層速度 +5%、暴擊率 +3%。部分技能會消耗連擊。",
        "desc_en": "Core Passive: Successful attacks grant 1 Combo (max 3); each grants +5% SPD and +3% crit. Some skills consume Combo.",
    },
    "mage": {
        "name": "🔷 魔力循環",
        "name_en": "🔷 Arcane Cycle",
        "desc": "核心被動：每個玩家回合開始時回復最大 MP 的 4% 加魔力的 5%（至少 3 MP）。",
        "desc_en": "Core Passive: At the start of each player turn, restore 4% max MP plus 5% Magic (minimum 3 MP).",
    },
    "warlock": {
        "name": "🩸 血之契約",
        "name_en": "🩸 Blood Covenant",
        "desc": "核心被動：主動支付 HP 時獲得血契（最多 3 層）；每層使術士傷害 +8%、吸血 +4%。",
        "desc_en": "Core Passive: Paying HP grants Blood Pact (max 3); each stack grants +8% Warlock damage and +4% lifesteal.",
    },
}


def localized_core(core_key: str, lang: str = "zh") -> tuple[str, str]:
    core = CORE_ABILITIES.get(core_key, {})
    suffix = "_en" if lang == "en" else ""
    return core.get(f"name{suffix}", core.get("name", core_key)), core.get(f"desc{suffix}", core.get("desc", ""))


def core_available(player, core_key: str) -> bool:
    if core_key not in CORE_KEYS:
        return False
    alloc = getattr(player, "stat_alloc", None) or {}
    return int(alloc.get(core_key, 0) or 0) >= CORE_POINT_REQUIREMENT


def normalize_core_selection(player) -> str | None:
    selected = getattr(player, "core_ability", None)
    if selected in CORE_KEYS and core_available(player, selected):
        return selected
    player.core_ability = None
    return None


def set_core_ability(player, core_key: str) -> bool:
    if not core_available(player, core_key):
        return False
    player.core_ability = core_key
    return True


def core_active(player, core_key: str) -> bool:
    return getattr(player, "core_ability", None) == core_key and core_available(player, core_key)


def clamp_fortune(value: int) -> int:
    return max(FORTUNE_MIN, min(FORTUNE_MAX, int(value)))


def change_fortune(player, delta: int) -> tuple[int, int]:
    before = clamp_fortune(getattr(player, "fortune", 0) or 0)
    after = clamp_fortune(before + int(delta))
    player.fortune = after
    return before, after


def fortune_tier(value: int, lang: str = "zh") -> str:
    value = clamp_fortune(value)
    if value <= -6:
        return t(lang, "fortune.tier_cursed", "厄運纏身")
    if value <= -2:
        return t(lang, "fortune.tier_unlucky", "最近不太順利")
    if value <= 1:
        return t(lang, "fortune.tier_neutral", "命運平靜")
    if value <= 5:
        return t(lang, "fortune.tier_favored", "似乎受到眷顧")
    return t(lang, "fortune.tier_blessed", "命運正對你微笑")


def fortune_multiplier(player, per_point: float, minimum: float = 0.75, maximum: float = 1.25) -> float:
    value = clamp_fortune(getattr(player, "fortune", 0) or 0)
    return max(minimum, min(maximum, 1.0 + value * per_point))
