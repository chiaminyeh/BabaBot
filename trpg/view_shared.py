"""Small display constants/helpers shared across the view.py, view_shop.py, and
view_dungeon.py modules. Kept dependency-free (no imports from trpg.view or its
mixins) so none of those modules risk a circular import on each other."""

# 物品分類 -> 開頭 emoji，讓玩家一眼看出是武器/防具/消耗品/飾品/雜物
ITEM_TYPE_EMOJI = {
    "weapon": "⚔️",
    "armor": "🛡️",
    "accessory": "💍",
    "potion": "🧪",
    "cure": "💊",
    "buff_item": "⏳",
    "skill_scroll": "📜",
    "etc": "📦",
}


def item_emoji(item: dict) -> str:
    return ITEM_TYPE_EMOJI.get((item or {}).get("type"), "📦")


# 裝備數值欄位 -> (顯示縮寫, emoji)，共用給商店/地下城戰利品/裝備選單三處的比較與
# 數值顯示，一次列全所有引擎有在讀的欄位（見 trpg/stats.py get_equipment_bonuses
# 與 trpg/dungeon.py _ITEM_STAT_FIELDS），避免像之前那樣漏掉 mdef_bonus/mp_bonus。
EQUIP_STAT_DISPLAY = {
    "atk_bonus": ("ATK", "⚔️"),
    "def_bonus": ("DEF", "🛡️"),
    "mdef_bonus": ("MDEF", "🔮"),
    "hp_bonus": ("HP", "❤️"),
    "magic_bonus": ("MAG", "✨"),
    "spd_bonus": ("SPD", "🚀"),
    "mp_bonus": ("MP", "🔷"),
}

# 武器元素屬性 -> (emoji, 中文名, 英文名)，純顯示用（不影響戰鬥判定，判定仍讀
# item["element"] 原始字串），給商店/裝備選單標示這把武器的普攻到底是不是物理。
ELEMENT_DISPLAY = {
    "fire": ("🔥", "火", "Fire"),
    "ice": ("❄️", "冰", "Ice"),
    "thunder": ("⚡", "雷", "Thunder"),
    "water": ("💧", "水", "Water"),
    "earth": ("🌍", "土", "Earth"),
    "dark": ("🌑", "暗", "Dark"),
    "holy": ("✨", "光", "Holy"),
}
