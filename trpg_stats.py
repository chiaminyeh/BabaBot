"""TRPG 屬性點與數值重算。"""
STAT_KEYS = ("atk", "vit", "int", "spd", "res")
POINTS_PER_LEVEL = 2

ALLOC_BONUS = {
    "atk": 3,
    "vit": 1,  # 1點體力 = +12血量 & +2防禦
    "int": 1,  # 1點智力 = +4魔攻 & +3最大MP
    "spd": 3,  # 1點速度 = +3速度
    "res": 2,
}


def default_stat_alloc() -> dict:
    return {k: 0 for k in STAT_KEYS}


def total_stat_points(level: int) -> int:
    return POINTS_PER_LEVEL * level


def get_allocated_points(stat_alloc: dict) -> int:
    return sum(stat_alloc.get(k, 0) for k in STAT_KEYS)


def get_unspent_points(player) -> int:
    alloc = getattr(player, "stat_alloc", None) or default_stat_alloc()
    return total_stat_points(player.level) - get_allocated_points(alloc)


def get_equipment_bonuses(player, items: dict) -> dict:
    # 直接手動展開需要的裝備加成欄位，避免跟新的配點 STAT_KEYS 衝突
    bonuses = {"atk": 0, "def": 0, "hp": 0, "magic": 0, "res": 0, "spd": 0, "mp": 0}
    for slot in (
        getattr(player, "weapon", None),
        getattr(player, "armor", None),
        getattr(player, "accessory", None),
    ):
        if slot and slot in items:
            eq = items[slot]
            bonuses["atk"] += eq.get("atk_bonus", 0)
            bonuses["def"] += eq.get("def_bonus", 0)     # 抓取裝備的防禦
            bonuses["hp"] += eq.get("hp_bonus", 0)       # 抓取裝備的血量
            bonuses["magic"] += eq.get("magic_bonus", 0) # 抓取裝備的魔力(智力)
            bonuses["res"] += eq.get("res_bonus", 0)
            bonuses["spd"] += eq.get("spd_bonus", 0)
            bonuses["mp"] += eq.get("mp_bonus", 0)
    return bonuses


def recalc_player_stats(player, items: dict = None, heal_full: bool = False):
    """依等級、配點、裝備重算基礎戰鬥數值。"""
    level = player.level
    alloc = getattr(player, "stat_alloc", None) or default_stat_alloc()
    eq = get_equipment_bonuses(player, items or {})
    prestige = getattr(player, "prestige_count", 0)
    prestige_mult = 1.0 + prestige * 0.10  # 每級轉生提升 10% 全屬性

    base_atk = 10 + level * 2 + alloc.get("atk", 0) * ALLOC_BONUS["atk"] + eq["atk"]
    if getattr(player, "weapon", None):
        base_atk += getattr(player, "weapon_upgrade", 0) * 3
    player.base_atk = int(base_atk * prestige_mult)
    
    # 👇 體力 (vit) 統一管理血量與防禦
    base_def = 4 + level * 1.5 + alloc.get("vit", 0) * 2 + eq["def"]
    if getattr(player, "armor", None):
        base_def += getattr(player, "armor_upgrade", 0) * 2
    player.base_def = int(base_def * prestige_mult)
    
    old_max_hp = getattr(player, "max_hp", 60)
    base_max_hp = 50 + level * 10 + alloc.get("vit", 0) * 12 + eq["hp"]
    if getattr(player, "armor", None):
        base_max_hp += getattr(player, "armor_upgrade", 0) * 15
    player.max_hp = int(base_max_hp * prestige_mult)
    
    # 👇 智力 (int) 統一管理魔法攻擊與 MP
    base_int = 10 + int(level * 2.5) + alloc.get("int", 0) * 4 + eq["magic"]
    player.base_int = int(base_int * prestige_mult)
    
    old_max_mp = getattr(player, "max_mp", 25)
    base_max_mp = 40 + level * 6 + alloc.get("int", 0) * 3 + eq["mp"]
    player.max_mp = int(base_max_mp * prestige_mult)
    
    # 👇 速度 (spd) 與抗性 (res)
    base_spd = 10 + level + alloc.get("spd", 0) * ALLOC_BONUS["spd"] + eq.get("spd", 0)
    player.base_spd = int(base_spd * prestige_mult)
    
    base_res = level // 5 + alloc.get("res", 0) * ALLOC_BONUS["res"] + eq["res"]
    player.base_res = int(base_res * prestige_mult)

    if heal_full:
        player.current_hp = player.max_hp
        player.current_mp = player.max_mp
    else:
        # ... (保留舊有的比例回血/回魔邏輯) ...
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
    """舊存檔遷移：補 stat_alloc 並轉換舊屬性。"""
    if not getattr(player, "stat_alloc", None):
        player.stat_alloc = default_stat_alloc()
    else:
        # 轉換舊版的加點
        if "hp" in player.stat_alloc or "def" in player.stat_alloc:
            player.stat_alloc["vit"] = player.stat_alloc.pop("hp", 0) + player.stat_alloc.pop("def", 0)
        if "magic" in player.stat_alloc:
            player.stat_alloc["int"] = player.stat_alloc.pop("magic", 0)
            
        # 👇 補上這段防呆：確保玩家的 stat_alloc 裡包含所有新屬性鍵值 (包含 spd)
        for key in STAT_KEYS:
            if key not in player.stat_alloc:
                player.stat_alloc[key] = 0

    if not hasattr(player, "base_int"): player.base_int = getattr(player, "base_magic", 0)
    if hasattr(player, "base_magic"): del player.base_magic
    if not hasattr(player, "base_res"): player.base_res = 0
    if not hasattr(player, "base_spd"): player.base_spd = 5 + player.level

    if not getattr(player, "stat_alloc", None):
        player.stat_alloc = default_stat_alloc()
    if not hasattr(player, "base_magic"):
        player.base_magic = 0
    if not hasattr(player, "base_res"):
        player.base_res = 0
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
    lines = [
        # 👇 全部改用安全的 .get() 抓法
        f"⚔️攻擊 {alloc.get('atk', 0)} | 🛡️體力 {alloc.get('vit', 0)} | ✨智力 {alloc.get('int', 0)}",
        f"💨速度 {alloc.get('spd', 0)} | 🔰抗性 {alloc.get('res', 0)} | 剩餘點數 {unspent}",
    ]
    return "\n".join(lines)


def get_potion_heal_target(item_data: dict, item_id: str = "") -> str:
    if item_data.get("heal_target") in ("hp", "mp"):
        return item_data["heal_target"]
    if "mana" in item_id:
        return "mp"
    return "hp"
