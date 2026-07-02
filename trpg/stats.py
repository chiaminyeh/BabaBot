"""TRPG 屬性點與數值重算。"""
from trpg.i18n import t
from trpg.balance import (
    ALLOC_BONUS, STAT_POINTS_PER_LEVEL, PRESTIGE_BONUS_PER_LEVEL, SET_BONUSES,
    PRESTIGE_BASE_LEVEL, PRESTIGE_LEVEL_STEP,
)

STAT_KEYS = ("atk", "vit", "int", "spd", "res", "luck")
POINTS_PER_LEVEL = STAT_POINTS_PER_LEVEL  # 相容別名，調整請改 trpg/balance.py


def prestige_required_level(prestige_count: int) -> int:
    """轉生所需等級隨已轉生次數提高（30 -> 31 -> 32 ...），避免越轉越輕鬆。"""
    return PRESTIGE_BASE_LEVEL + prestige_count * PRESTIGE_LEVEL_STEP


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
    bonuses = {"atk": 0, "def": 0, "mdef": 0, "hp": 0, "magic": 0, "res": 0, "spd": 0, "mp": 0, "luck": 0}
    set_counts = {}
    for slot in (
        getattr(player, "weapon", None),
        getattr(player, "armor", None),
        getattr(player, "accessory", None),
    ):
        if slot and slot in items:
            eq = items[slot]
            bonuses["atk"] += eq.get("atk_bonus", 0)
            bonuses["def"] += eq.get("def_bonus", 0)     # 抓取裝備的防禦
            bonuses["mdef"] += eq.get("mdef_bonus", 0)   # 抓取裝備的魔法防禦
            bonuses["hp"] += eq.get("hp_bonus", 0)       # 抓取裝備的血量
            bonuses["magic"] += eq.get("magic_bonus", 0) # 抓取裝備的魔力(智力)
            bonuses["res"] += eq.get("res_bonus", 0)
            bonuses["spd"] += eq.get("spd_bonus", 0)
            bonuses["mp"] += eq.get("mp_bonus", 0)
            bonuses["luck"] += eq.get("luck_bonus", 0)   # 抓取裝備的運氣（目前還沒有裝備實際帶這個欄位，先接好管線）
            set_name = eq.get("set")
            if set_name:
                set_counts[set_name] = set_counts.get(set_name, 0) + 1

    # 套裝加成：湊齊同一 set 的多件裝備，疊加所有達標門檻的加成
    for set_name, count in set_counts.items():
        for need, set_bonus in SET_BONUSES.get(set_name, {}).items():
            if count >= need:
                for stat, val in set_bonus.items():
                    bonuses[stat] = bonuses.get(stat, 0) + val
    return bonuses


def active_set_bonuses(player, items: dict) -> list:
    """回傳目前已啟用的套裝列表 [(set_name, pieces_equipped), ...]，給 UI 顯示用。"""
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
    """依等級、配點、裝備重算基礎戰鬥數值。"""
    level = player.level
    alloc = getattr(player, "stat_alloc", None) or default_stat_alloc()
    eq = get_equipment_bonuses(player, items or {})
    prestige = getattr(player, "prestige_count", 0)
    prestige_mult = 1.0 + prestige * PRESTIGE_BONUS_PER_LEVEL  # 每級轉生提升的全屬性（見 balance.py）

    # 👇 強化跟著「這把裝備」走，不是跟著玩家：查表用目前裝備的 item_id 當 key，
    # 換一把新武器/防具，強化等級就會歸零，要重新投資材料才能再強化起來。
    weapon_upgrades = getattr(player, "weapon_upgrades", None) or {}
    armor_upgrades = getattr(player, "armor_upgrades", None) or {}

    base_atk = 10 + level * 2 + alloc.get("atk", 0) * ALLOC_BONUS["atk"] + eq["atk"]
    if getattr(player, "weapon", None):
        base_atk += weapon_upgrades.get(player.weapon, 0) * 3
    player.base_atk = int(base_atk * prestige_mult)

    # 👇 體力 (vit) 統一管理血量與防禦
    base_def = 4 + level * 1.5 + alloc.get("vit", 0) * 2 + eq["def"]
    if getattr(player, "armor", None):
        base_def += armor_upgrades.get(player.armor, 0) * 2
    player.base_def = int(base_def * prestige_mult)

    old_max_hp = getattr(player, "max_hp", 60)
    base_max_hp = 50 + level * 10 + alloc.get("vit", 0) * 12 + eq["hp"]
    if getattr(player, "armor", None):
        base_max_hp += armor_upgrades.get(player.armor, 0) * 15
    player.max_hp = int(base_max_hp * prestige_mult)
    
    # 👇 智力 (int) 統一管理魔法攻擊與 MP
    base_int = 10 + int(level * 2.5) + alloc.get("int", 0) * 4 + eq["magic"]
    player.base_int = int(base_int * prestige_mult)

    # 👇 魔法防禦：主要吃 int 配點（懂魔法的人也更會抵禦魔法），體力配點給一點點分潤，
    # 讓純戰士不會對法術毫無抵抗力，但真正的抗法還是要走 int 或裝備 mdef_bonus。
    base_mdef = 2 + level * 1.0 + alloc.get("int", 0) * 1.5 + alloc.get("vit", 0) * 0.5 + eq["mdef"]
    player.base_mdef = int(base_mdef * prestige_mult)

    old_max_mp = getattr(player, "max_mp", 25)
    base_max_mp = 40 + level * 6 + alloc.get("int", 0) * 3 + eq["mp"]
    player.max_mp = int(base_max_mp * prestige_mult)
    
    # 👇 速度 (spd) 與抗性 (res)
    base_spd = 10 + level + alloc.get("spd", 0) * ALLOC_BONUS["spd"] + eq.get("spd", 0)
    player.base_spd = int(base_spd * prestige_mult)
    
    base_res = level // 5 + alloc.get("res", 0) * ALLOC_BONUS["res"] + eq["res"]
    player.base_res = int(base_res * prestige_mult)

    # 👇 運氣 (luck)：純配點/裝備堆出來的數值，不跟著等級自動成長（跟 atk/vit/int
    # 不同，這樣「不點運氣」不會讓暴擊率/掉寶率隨等級被動下降，只有玩家主動投資
    # 才會變化）。實際效果（暴擊率加成、掉寶率加成）在 trpg/combat.py 讀取。
    base_luck = alloc.get("luck", 0) * ALLOC_BONUS["luck"] + eq["luck"]
    player.base_luck = int(base_luck * prestige_mult)

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

    # base_magic is a retired field kept only so old saves have somewhere to migrate
    # their value FROM; once base_int exists we never touch base_magic again (previously
    # this deleted-then-recreated base_magic=0 on every single call — pure churn, and it
    # discarded the old value before the one-time transfer could ever be re-read).
    if not hasattr(player, "base_int"):
        player.base_int = getattr(player, "base_magic", 0)
    if not hasattr(player, "base_res"): player.base_res = 0
    if not hasattr(player, "base_spd"): player.base_spd = 5 + player.level
    if not hasattr(player, "base_mdef"): player.base_mdef = 0

    if not hasattr(player, "equipped_skills") or player.equipped_skills is None:
        # 取最多 8 個已學技能作為預設裝備技能
        player.equipped_skills = player.skills[:8] if getattr(player, "skills", None) else []

    # 👇 強化改為跟著裝備走：把舊版「玩家身上一個強化等級」一次性搬進新的
    # {item_id: level} 表，搬完就把舊欄位歸零，不會重複搬遷或疊加。
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
    lines = [
        # 👇 全部改用安全的 .get() 抓法
        t(lang, "stats.alloc_line1", "⚔️攻擊 {atk} | 🛡️體力 {vit} | ✨智力 {int}",
          atk=alloc.get('atk', 0), vit=alloc.get('vit', 0), int=alloc.get('int', 0)),
        t(lang, "stats.alloc_line2", "💨速度 {spd} | 🔰抗性 {res} | 🍀運氣 {luck}",
          spd=alloc.get('spd', 0), res=alloc.get('res', 0), luck=alloc.get('luck', 0)),
        t(lang, "stats.alloc_line3", "剩餘點數 {unspent}", unspent=unspent),
    ]
    return "\n".join(lines)


def get_potion_heal_target(item_data: dict, item_id: str = "") -> str:
    if item_data.get("heal_target") in ("hp", "mp"):
        return item_data["heal_target"]
    if "mana" in item_id:
        return "mp"
    return "hp"
