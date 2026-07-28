"""地下城（Roguelike 模式）核心邏輯。

設計：進入地下城後玩家的真實數值被封印，改用 dungeon_state 裡的「臨時角色」。
力量來源 = 每層配點（整備）＋ 撿到的地下城裝備 ＋ 遺物(relic)。裝備與遺物的
效果會彙整成兩部分：
  1. 直接寫進 dungeon_state 的基礎數值（攻防血魔速抗）。
  2. 戰鬥 hook 效果（暴擊/吸血/擊殺回血/每回合回血/腐蝕）放到
     real_player.dungeon_relic_effects，由 combat 讀取（僅在地下城啟用）。
  3. 倍率（atk_mult/def_mult/magic_mult）放到 real_player.dungeon_buffs，
     combat 既有的 get_player_* 會自動套用。

對外主要函式：start_run / recompute_loadout / build_monster / roll_doors /
roll_loot / roll_relic / grant_relic / equip_item / end_run。
"""

import random

from trpg.balance import (
    DUNGEON_START_STATS, DUNGEON_SEALED_LEVEL, DUNGEON_ROOM_WEIGHTS,
    DUNGEON_MON_HP_BASE, DUNGEON_MON_HP_PER_FLOOR, DUNGEON_MON_ATK_BASE,
    DUNGEON_MON_ATK_PER_FLOOR, DUNGEON_MON_DEF_PER_FLOOR, DUNGEON_MON_SPD_BASE,
    DUNGEON_MON_SPD_PER_FLOOR, DUNGEON_MON_EXP_BASE, DUNGEON_MON_EXP_PER_FLOOR,
    DUNGEON_ELITE_HP_MULT, DUNGEON_ELITE_ATK_MULT, DUNGEON_ELITE_DEF_BONUS,
    DUNGEON_BOSS_HP_MULT, DUNGEON_BOSS_ATK_MULT, DUNGEON_BOSS_DEF_BONUS,
    DUNGEON_BOSS_FLOOR, DUNGEON_MINIBOSS_FLOORS, CORROSION_BASE_DMG, CORROSION_DMG_PER_FLOOR,
)
from trpg.monster_pool import pick_tier_for_floor

# dungeon_state 裡屬於「封印臨時角色」的欄位（RoguePlayerWrapper 會把這些轉址過去）。
SEALED_FIELDS = [
    "level", "exp", "max_hp", "current_hp", "max_mp", "current_mp",
    "base_atk", "base_def", "base_mdef", "base_spd", "base_magic", "base_int",
    "inventory", "skills", "equipped_skills",
    "weapon", "armor", "accessory", "status_effects", "combat_debuffs", "combat_buffs",
]

# 流派選擇（取代舊版「每層配點」）：選一套起手武器+技能，之後靠戰利品/遺物/菁英技能
# 選擇繼續往該流派疊，或臨時轉點別的流派（參考殺戮尖塔式的路線選擇）。
ARCHETYPES = {
    "warrior": {
        "name": "戰士", "name_en": "Warrior",
        "desc": "近戰肉盾，高HP與防禦，靠破防技能穩定磨死敵人。",
        "desc_en": "Melee tank with high HP/DEF, wears enemies down with armor-shredding skills.",
        "weapon": "d_rusty_blade", "skill": "sunder_strike",
    },
    "mage": {
        "name": "法師", "name_en": "Mage",
        "desc": "魔法爆發，高MP與魔力，靠元素法術從遠處消滅敵人。",
        "desc_en": "Magic burst with high MP/MAG, blasts enemies from range with elemental spells.",
        "weapon": "d_arcane_staff", "skill": "fireball",
    },
    "ranger": {
        "name": "遊俠", "name_en": "Ranger",
        "desc": "敏捷連擊，高速度與暴擊，靠連續攻擊與中毒慢慢磨死敵人。",
        "desc_en": "Agile striker with high SPD/crit, wears enemies down with rapid hits and poison.",
        "weapon": "d_starter_bow", "skill": "twin_shot",
    },
}


def apply_archetype(player, d_state: dict, cog, archetype_id: str) -> bool:
    """套用起手流派：裝上起手武器、學會起手技能。之後仍可透過戰利品/菁英技能轉型。"""
    archetype = ARCHETYPES.get(archetype_id)
    if not archetype:
        return False
    d_state["archetype"] = archetype_id
    equip_item(player, d_state, cog, archetype["weapon"])
    skill_id = archetype["skill"]
    if skill_id not in d_state["skills"]:
        d_state["skills"].append(skill_id)
    if skill_id not in d_state["equipped_skills"]:
        d_state["equipped_skills"].append(skill_id)
    d_state["floor_state"] = "pending"
    recompute_loadout(player, d_state, cog)
    return True

# 彙整時：直接加到基礎數值的平面欄位 / 每層成長 / 倍率 / 戰鬥 hook
_FLAT_KEYS = {"atk", "def", "mdef", "hp", "magic", "spd", "mp"}
_PER_FLOOR_KEYS = {"hp_per_floor", "def_per_floor", "atk_per_floor"}
_MULT_KEYS = {"atk_mult", "def_mult", "magic_mult"}
_HOOK_KEYS = {"crit_bonus", "lifesteal", "on_kill_heal_pct", "regen_pct",
              "corrosion_on_hit", "corrosion_dmg_bonus"}
# 裝備上的數值欄位 -> 彙整鍵
_ITEM_STAT_FIELDS = {
    "atk_bonus": "atk", "def_bonus": "def", "mdef_bonus": "mdef", "hp_bonus": "hp",
    "magic_bonus": "magic", "spd_bonus": "spd", "mp_bonus": "mp",
}


def start_run(d_state: dict):
    """初始化一場全新的地下城（封印角色 + 空遺物 + 第一層）。流派尚未選擇，
    由呼叫端接著顯示流派選擇畫面，選定後呼叫 apply_archetype()。"""
    s = DUNGEON_START_STATS
    d_state.clear()
    d_state.update({
        "in_run": True,
        "floor": 1,
        "floor_state": "pending",
        "archetype": None,
        "level": DUNGEON_SEALED_LEVEL, "exp": 0,
        "max_hp": s["max_hp"], "current_hp": s["max_hp"],
        "max_mp": s["max_mp"], "current_mp": s["max_mp"],
        "base_atk": s["base_atk"], "base_def": s["base_def"], "base_mdef": s["base_mdef"], "base_spd": s["base_spd"],
        "base_magic": s["base_magic"], "base_int": s["base_magic"],
        "inventory": {}, "skills": [], "equipped_skills": [],
        "weapon": None, "armor": None, "accessory": None,
        "status_effects": {}, "combat_debuffs": {}, "combat_buffs": {},
        "relics": [],
        "pending_loot": [],
        "pending_relics": [],
        "pending_skills": [],
        # 隨機事件給的小幅永久加成（磨刀石/秘力泉水之類），跟遺物一樣彙整進數值，
        # 但不算遺物、不佔遺物欄，純粹是「這次探索走運多得到的一點點力量」。
        "event_bonuses": {},
    })


def corrosion_params(d_state: dict, effects: dict) -> tuple:
    """回傳 (本次普攻附加的腐蝕層數, 每層傷害)。"""
    floor = d_state.get("floor", 1)
    stacks = int(effects.get("corrosion_on_hit", 0))
    if stacks <= 0:
        return 0, 0
    dmg_per = int(CORROSION_BASE_DMG + floor * CORROSION_DMG_PER_FLOOR + effects.get("corrosion_dmg_bonus", 0))
    return stacks, max(1, dmg_per)


def _aggregate(d_state: dict, cog) -> tuple:
    """把遺物 + 已裝備地下城裝備彙整成 (flat, per_floor, mult, hook)。"""
    flat = {k: 0 for k in _FLAT_KEYS}
    per_floor = {k: 0 for k in _PER_FLOOR_KEYS}
    mult = {}
    hook = {k: 0 for k in _HOOK_KEYS}

    def absorb(effects: dict):
        for k, v in effects.items():
            if k in _FLAT_KEYS:
                flat[k] += v
            elif k in _PER_FLOOR_KEYS:
                per_floor[k] += v
            elif k in _MULT_KEYS:
                mult[k] = mult.get(k, 1.0) * v
            elif k in _HOOK_KEYS:
                hook[k] += v

    # 遺物
    for rid in d_state.get("relics", []):
        rdef = cog.dungeon_relics.get(rid)
        if rdef:
            absorb(rdef.get("effects", {}))

    # 隨機事件累積的小幅永久加成
    absorb(d_state.get("event_bonuses", {}))

    # 已裝備的地下城裝備（數值欄位 + hook 欄位）
    for slot in ("weapon", "armor", "accessory"):
        item_id = d_state.get(slot)
        if not item_id:
            continue
        idef = cog.dungeon_items.get(item_id)
        if not idef:
            continue
        for field, key in _ITEM_STAT_FIELDS.items():
            if idef.get(field):
                flat[key] += idef[field]
        for k in _HOOK_KEYS:
            if idef.get(k):
                hook[k] += idef[k]
        for k in _MULT_KEYS:
            if idef.get(k):
                mult[k] = mult.get(k, 1.0) * idef[k]

    return flat, per_floor, mult, hook


def recompute_loadout(player, d_state: dict, cog):
    """依封印起始值 + 裝備 + 遺物，重算臨時角色的所有數值與戰鬥 hook。
    地下城不再有配點小遊戲——強度純粹來自撿到的武器/防具/遺物與流派選擇。"""
    s = DUNGEON_START_STATS
    floor = d_state.get("floor", 1)
    flat, per_floor, mult, hook = _aggregate(d_state, cog)

    base_atk = s["base_atk"] + flat["atk"] + int(per_floor["atk_per_floor"] * floor)
    base_def = s["base_def"] + flat["def"] + int(per_floor["def_per_floor"] * floor)
    base_mdef = s["base_mdef"] + flat["mdef"]
    max_hp = s["max_hp"] + flat["hp"] + int(per_floor["hp_per_floor"] * floor)
    max_mp = s["max_mp"] + flat["mp"]
    base_int = s["base_magic"] + flat["magic"]
    base_spd = s["base_spd"] + flat["spd"]

    d_state["base_atk"] = max(1, base_atk)
    d_state["base_def"] = max(0, base_def)
    d_state["base_mdef"] = max(0, int(base_mdef))
    d_state["max_hp"] = max(1, max_hp)
    d_state["max_mp"] = max(0, max_mp)
    d_state["base_magic"] = max(0, base_int)
    d_state["base_int"] = max(0, base_int)
    d_state["base_spd"] = max(1, base_spd)
    d_state["current_hp"] = min(d_state.get("current_hp", d_state["max_hp"]), d_state["max_hp"])
    d_state["current_mp"] = min(d_state.get("current_mp", d_state["max_mp"]), d_state["max_mp"])

    # 倍率交給既有的 dungeon_buffs；戰鬥 hook 放到 real_player.dungeon_relic_effects
    real = getattr(player, "real_player", player)
    real.dungeon_buffs = dict(mult)
    real.dungeon_relic_effects = {k: v for k, v in hook.items() if v}


def end_run(player):
    """結束地下城：清掉只在 run 內生效的倍率與 hook，避免影響主城角色。"""
    real = getattr(player, "real_player", player)
    real.dungeon_buffs = {}
    real.dungeon_relic_effects = {}


# --- 怪物 -------------------------------------------------------------------

def _flavor_monster(cog, floor: int, want_boss: bool):
    tier = pick_tier_for_floor(cog.monster_pool, floor)
    if want_boss and tier.get("boss"):
        return tier["boss"]
    monsters = tier.get("monsters") or {}
    if monsters:
        return random.choice(list(monsters.values()))
    return tier.get("boss") or {"id": "unknown", "name": "迷霧怪影", "name_en": "Mist Phantom"}


# 菁英怪物的「流派」：不是單純放大數值的同一隻怪，而是各自有鮮明的招牌機制。
# def_flat：防禦改走「身份」制之後，守護者是地下城裡唯一帶防禦的一般敵人，
# 用固定值（隨樓層成長）而不是倍率——基礎防禦現在是 0，乘上倍率永遠是 0。
ELITE_ARCHETYPES = [
    {"ai": "heavy_tank", "suffix": "・守護者", "suffix_en": " the Guardian", "hp_mult": 1.1, "def_flat_per_floor": 1.5, "def_flat": 4, "spd_mult": 0.9},
    {"ai": "berserk_low_hp", "suffix": "・狂戰士", "suffix_en": " the Berserker", "atk_mult": 1.1, "spd_mult": 1.2},
    {"ai": "lifesteal", "suffix": "・嗜血者", "suffix_en": " the Bloodletter", "hp_mult": 1.15},
    {"ai": "multi_hit_flurry", "suffix": "・刺客", "suffix_en": " the Assassin", "spd_mult": 1.35, "atk_mult": 0.85},
    {"ai": "debuffer", "suffix": "・咒術師", "suffix_en": " the Warlock", "atk_mult": 0.9},
]


# 守衛頭目（4/8/12 層）：每一隻都有獨一無二的招牌機制，不再是數值放大版的普通怪。
# 機制全部走既有引擎欄位（damage_cap / active_skills 的 divine_shield、self_status、
# inflict_status），玩家在戰鬥面板上會看到對應的警示徽章與蓄力提示。
DUNGEON_MINIBOSSES = {
    4: {
        "id": "aegis_warden",
        "name": "聖盾守衛・埃癸斯", "name_en": "Aegis, the Shieldwarden",
        # 開場自帶聖盾，之後每 3 回合重新充能——用小招戳破聖盾再上大招才是正解
        "start_divine_shield": True,
        "hp_mult": 0.85,
        "active_skills": [
            {"id": "reforge_shield", "name": "聖盾重鑄", "name_en": "Reforge Shield",
             "trigger": {"type": "interval", "value": 3},
             "effect": {"type": "divine_shield"}},
            {"id": "shield_bash", "name": "盾牌猛擊", "name_en": "Shield Bash",
             "trigger": {"type": "chance", "value": 0.25},
             "effect": {"type": "heavy_attack", "mult": 1.8}},
        ],
    },
    8: {
        "id": "hollow_shade",
        "name": "無實體之影・虛靈", "name_en": "The Hollow Shade",
        # 週期性化為虛影（無實體：任何傷害至多 1 點），實體期才是輸出窗口
        "ai": "lifesteal",
        "hp_mult": 0.8,
        "active_skills": [
            {"id": "phase_out", "name": "虛化", "name_en": "Phase Out",
             "trigger": {"type": "interval", "value": 4},
             "effect": {"type": "self_status", "status_id": "intangible", "turns": 2}},
        ],
    },
    12: {
        "id": "silent_inquisitor",
        "name": "沉默審判官", "name_en": "The Silent Inquisitor",
        # 沉默玩家（技能全鎖）+ 承傷上限：斷你的爆發、再逼你打持久戰
        "damage_cap_mult": 2.6,   # damage_cap = atk * 這個倍率（隨樓層自然成長）
        "active_skills": [
            {"id": "decree_of_silence", "name": "沉默律令", "name_en": "Decree of Silence",
             "trigger": {"type": "interval", "value": 3},
             "effect": {"type": "inflict_status", "status_id": "silence", "turns": 2}},
            {"id": "verdict", "name": "審判之錘", "name_en": "The Verdict",
             "trigger": {"type": "hp_below", "value": 0.35},
             "effect": {"type": "heavy_attack", "mult": 2.2}},
        ],
    },
}


def build_monster(cog, floor: int, kind: str = "monster") -> dict:
    """產生一隻平衡過的地下城怪物。floor 1 必須能被封印起始角色打贏。
    kind="boss" 且該樓層在 DUNGEON_MINIBOSSES 時，套用該守衛頭目的專屬名字與機制。"""
    flavor = _flavor_monster(cog, floor, want_boss=(kind == "boss"))
    hp = int(DUNGEON_MON_HP_BASE + floor * DUNGEON_MON_HP_PER_FLOOR)
    atk = int(DUNGEON_MON_ATK_BASE + floor * DUNGEON_MON_ATK_PER_FLOOR)
    df = int(floor * DUNGEON_MON_DEF_PER_FLOOR)
    spd = int(DUNGEON_MON_SPD_BASE + floor * DUNGEON_MON_SPD_PER_FLOOR)
    exp = int(DUNGEON_MON_EXP_BASE + floor * DUNGEON_MON_EXP_PER_FLOOR)

    prefix = ""
    ai = flavor.get("ai", "none")
    suffix_zh, suffix_en = "", ""
    if kind == "elite":
        hp = int(hp * DUNGEON_ELITE_HP_MULT); atk = int(atk * DUNGEON_ELITE_ATK_MULT); df += DUNGEON_ELITE_DEF_BONUS
        prefix = "💠 "
        archetype = random.choice(ELITE_ARCHETYPES)
        ai = archetype["ai"]
        suffix_zh, suffix_en = archetype["suffix"], archetype["suffix_en"]
        hp = int(hp * archetype.get("hp_mult", 1.0))
        atk = int(atk * archetype.get("atk_mult", 1.0))
        df += int(archetype.get("def_flat", 0) + archetype.get("def_flat_per_floor", 0) * floor)
        spd = int(spd * archetype.get("spd_mult", 1.0))
    elif kind == "boss":
        hp = int(hp * DUNGEON_BOSS_HP_MULT); atk = int(atk * DUNGEON_BOSS_ATK_MULT); df += DUNGEON_BOSS_DEF_BONUS
        prefix = "💀 "

    zh = f"{prefix}{flavor.get('name', flavor.get('id', '怪物'))}{suffix_zh}"
    en = f"{prefix}{flavor.get('name_en') or flavor.get('name', flavor.get('id', 'Monster'))}{suffix_en}"
    mon = {
        "id": flavor.get("id", "unknown"),
        "name": zh, "name_en": en,
        "is_dungeon": True,
        "is_elite": kind == "elite",
        "is_boss": kind == "boss",
        "max_hp": max(1, hp), "atk": max(1, atk), "def": max(0, df), "spd": max(5, spd),
        "exp": exp, "money_min": 0, "money_max": 0, "drops": {},
        "ai": ai,
        "weakness": flavor.get("weakness", []), "resistance": flavor.get("resistance", []),
    }

    # 守衛頭目專屬機制覆蓋
    if kind == "boss" and floor in DUNGEON_MINIBOSSES:
        mb = DUNGEON_MINIBOSSES[floor]
        mon["id"] = mb["id"]
        mon["name"] = f"💀 {mb['name']}"
        mon["name_en"] = f"💀 {mb['name_en']}"
        if mb.get("ai"):
            mon["ai"] = mb["ai"]
        if mb.get("hp_mult"):
            mon["max_hp"] = max(1, int(mon["max_hp"] * mb["hp_mult"]))
        if mb.get("active_skills"):
            mon["active_skills"] = mb["active_skills"]
        if mb.get("damage_cap_mult"):
            mon["damage_cap"] = max(1, int(mon["atk"] * mb["damage_cap_mult"]))
        if mb.get("start_divine_shield"):
            mon["start_divine_shield"] = True
    return mon


# --- 房間 / 戰利品 / 遺物 ----------------------------------------------------

def forced_boss_kind(floor: int) -> str:
    """樓層 4/8/12/15 強制打王；回傳 "miniboss"/"final_boss"，其他樓層回傳空字串。"""
    if floor >= DUNGEON_BOSS_FLOOR:
        return "final_boss"
    if floor in DUNGEON_MINIBOSS_FLOORS:
        return "miniboss"
    return ""


def roll_doors(floor: int) -> list:
    """每層擲出三扇門（殺戮尖塔式的路線選擇）：其中一扇永遠是「未知事件」，
    另外兩扇依 DUNGEON_ROOM_WEIGHTS 從 戰鬥/菁英/營火 中抽。門的類型會誠實
    顯示在按鈕上（❓ 除外，賭的就是它），選路本身就是策略的一部分——殘血繞
    營火、想拿技能就挑菁英。回傳例如 ["monster", "event", "rest"]（已洗牌）。"""
    weights = DUNGEON_ROOM_WEIGHTS
    types = list(weights.keys())
    doors = [random.choices(types, weights=[weights[t] for t in types], k=1)[0] for _ in range(2)]
    doors.append("event")
    random.shuffle(doors)
    return doors


def _rarity_weight(rarity: str, floor: int) -> float:
    if rarity == "common":
        return max(1.0, 7.0 - floor * 0.4)
    if rarity == "rare":
        return 3.0 + floor * 0.1
    if rarity == "epic":
        return 0.6 + floor * 0.25
    return 1.0


def roll_loot(cog, floor: int, count: int = 3, guarantee_rare: bool = False) -> list:
    """抽出 count 個地下城裝備 id。guarantee_rare：至少含一件 rare 以上。
    標記 starter 的流派起手武器不進一般戰利品池，避免跟菁英/一般怪重複發放。"""
    ids = [i for i, idef in cog.dungeon_items.items() if not idef.get("starter")]
    if not ids:
        return []
    chosen = []
    pool = ids[:]
    if guarantee_rare:
        rares = [i for i in pool if cog.dungeon_items[i].get("rarity") in ("rare", "epic")]
        if rares:
            pick = random.choices(rares, weights=[_rarity_weight(cog.dungeon_items[i]["rarity"], floor) for i in rares], k=1)[0]
            chosen.append(pick)
            pool.remove(pick)
    while len(chosen) < count and pool:
        pick = random.choices(pool, weights=[_rarity_weight(cog.dungeon_items[i].get("rarity", "common"), floor) for i in pool], k=1)[0]
        chosen.append(pick)
        pool.remove(pick)
    return chosen


def roll_relic(cog, d_state: dict, floor: int = 1):
    """抽一個尚未持有的遺物 id；全持有了則回傳 None。"""
    owned = set(d_state.get("relics", []))
    pool = [r for r in cog.dungeon_relics.keys() if r not in owned]
    if not pool:
        return None
    return random.choices(pool, weights=[_rarity_weight(cog.dungeon_relics[r].get("rarity", "common"), floor) for r in pool], k=1)[0]


def roll_relics(cog, d_state: dict, floor: int = 1, count: int = 3) -> list:
    """抽出 count 個尚未持有、且彼此不重複的遺物 id（給菁英戰後的三選一）。"""
    owned = set(d_state.get("relics", []))
    pool = [r for r in cog.dungeon_relics.keys() if r not in owned]
    chosen = []
    while pool and len(chosen) < count:
        pick = random.choices(pool, weights=[_rarity_weight(cog.dungeon_relics[r].get("rarity", "common"), floor) for r in pool], k=1)[0]
        chosen.append(pick)
        pool.remove(pick)
    return chosen


def roll_dungeon_skills(cog, d_state: dict, count: int = 3) -> list:
    """抽出 count 個尚未擁有、標記 dungeon_pool 的技能 id（給菁英戰後的三選一）。
    菁英不再掉遺物、改掉技能——讓玩家能中途轉型（例如戰士撿到火球術），
    像 Slay the Spire 那樣靠戰鬥中的選擇塑造流派，而不是靠配點。"""
    owned = set(d_state.get("skills", []))
    pool = [sid for sid, sdef in cog.skills.items() if sdef.get("dungeon_pool") and sid not in owned]
    if not pool:
        return []
    random.shuffle(pool)
    return pool[:count]


def grant_relic(player, d_state: dict, cog, relic_id: str):
    d_state.setdefault("relics", []).append(relic_id)
    recompute_loadout(player, d_state, cog)


def grant_skill(d_state: dict, skill_id: str):
    if skill_id not in d_state["skills"]:
        d_state["skills"].append(skill_id)
    if skill_id not in d_state["equipped_skills"]:
        d_state["equipped_skills"].append(skill_id)


def equip_item(player, d_state: dict, cog, item_id: str) -> str:
    """把撿到的地下城裝備穿上對應欄位，回傳該欄位（weapon/armor/accessory）。"""
    idef = cog.dungeon_items.get(item_id)
    if not idef:
        return ""
    slot = idef.get("type")
    if slot not in ("weapon", "armor", "accessory"):
        return ""
    d_state[slot] = item_id
    recompute_loadout(player, d_state, cog)
    return slot
