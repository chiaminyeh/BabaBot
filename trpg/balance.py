"""Central tuning knobs for the TRPG.

Adjust the numbers here to rebalance the game without hunting through logic in
combat.py / stats.py / monster_pool.py. Each constant documents what it controls.
"""

# --- Leveling ---------------------------------------------------------------
# exp_to_next_level(level) = XP_CURVE_BASE * level ** XP_CURVE_EXP
XP_CURVE_BASE = 50
XP_CURVE_EXP = 1.8
# Stat points granted to the player per level.
STAT_POINTS_PER_LEVEL = 2

# 提升此版本可讓更新前已存在的角色各獲得一次免費流派重置。
ARCHETYPE_BALANCE_VERSION = 2

# --- Archetype point allocation ---------------------------------------------
# These are the direct headline payoffs for each invested point. stats.py also
# layers secondary derived bonuses (HP/DEF/MP/etc.) on top of these anchors.
ALLOC_BONUS = {"knight": 2, "rogue": 3, "mage": 1, "warlock": 1}

# --- Prestige / rebirth -----------------------------------------------------
# Every prestige level multiplies all base stats by (1 + this).
PRESTIGE_BONUS_PER_LEVEL = 0.10
# Level required to prestige climbs by PRESTIGE_LEVEL_STEP each time you do it
# (30, 31, 32, ...) — leveling gets faster as prior prestiges make you stronger,
# so the level requirement keeps pace instead of staying a fixed Lv.30 forever.
PRESTIGE_BASE_LEVEL = 30
PRESTIGE_LEVEL_STEP = 1

# --- Combat -----------------------------------------------------------------
# Base crit chance before per-skill/weapon crit bonuses.
PHYSICAL_CRIT_CHANCE = 0.10   # normal weapon swings
SKILL_CRIT_CHANCE = 0.12      # physical/HP-sacrifice skills (magic can't crit)

# Hidden Fortune payoff. Fortune is bounded to -10..10 and changed only by
# event choices; these multipliers can therefore help or hinder the player.
LUCK_CRIT_BONUS_PER_POINT = 0.005
LUCK_DROP_RATE_BONUS_PER_POINT = 0.01
LUCK_EXP_BONUS_PER_POINT = 0.005
LUCK_GOLD_BONUS_PER_POINT = 0.005

# Flee (player fleeing combat) chance: base + (player_spd - monster_spd) * factor, clamped.
FLEE_BASE_CHANCE = 0.5
FLEE_MIN_CHANCE = 0.2
FLEE_MAX_CHANCE = 0.95
FLEE_SPD_FACTOR = 0.015

# Dodge (monster's attack missing a defending/dodging player)
DODGE_BASE_CHANCE = 0.3
DODGE_MIN_CHANCE = 0.05
DODGE_MAX_CHANCE = 0.95
DODGE_SPD_FACTOR = 0.015

# Schrodinger's Watch accessory: gambles every hit between doubling and halving damage.
SCHRODINGER_DOUBLE_CHANCE = 0.6
SCHRODINGER_DOUBLE_MULT = 2
SCHRODINGER_HALVE_MULT = 0.5

# First-kill/repeat boss drop: guaranteed 100% on first kill, this% on repeat clears.
BOSS_DAILY_REWARD_CHANCE = 0.30

BOSS_SPECIFIC_REWARDS = {
    "goblin_chief": ["bronze_sword", "ancient_wood", "medium_health_potion", "skill_manual", "forging_stone"],
    "forest_guardian": ["bronze_sword", "ancient_wood", "medium_health_potion", "skill_manual", "forging_stone"],
    "bee_queen": ["royal_honey", "poison_stinger", "skill_manual", "forging_stone"],
    "mad_doctor": ["ectoplasm", "medium_health_potion", "skill_manual", "forging_stone"],
    "lich": ["ectoplasm", "void_orb", "pure_blood_essence", ("skill_manual", 2), ("forging_stone", 2)],
    "vampire_lord": ["pure_blood_essence", "void_orb", ("skill_manual", 2), ("forging_stone", 2)],
    "rat_king": ["medium_health_potion", "bronze_sword", "skill_manual", "forging_stone"],
    "bone_knight": ["ectoplasm", "bronze_sword", ("skill_manual", 2), ("forging_stone", 2)],
    "orc_warlord": ["bronze_sword", "ancient_wood", ("skill_manual", 2), ("forging_stone", 2)],
    "abyss_overlord": ["void_orb", "dragon_scale_shard", ("skill_manual", 3), ("forging_stone", 3)],
    "ancient_dragon": ["dragon_scale_shard", "phoenix_scepter", ("skill_manual", 3), ("forging_stone", 3)],
    "void_sovereign": ["void_orb", "pure_blood_essence", ("skill_manual", 4), ("forging_stone", 4)],
    "colosseum_champion": ["bronze_sword", "high_health_potion", ("skill_manual", 4), ("forging_stone", 4)],
    "sargeras": ["dragon_scale_shard", "phoenix_scepter", ("skill_manual", 5), ("forging_stone", 5)],
}

# Magic Tower milestone rewards, keyed by the floor number just completed.
# trophy_key/trophy_fallback feed t() at the call site (locale text lives in locale_en.json).
TOWER_MILESTONES = {
    10: {"trophy_key": "combat.trophy_bronze", "trophy_fallback": "🥉 銅魔箱勳章", "gold": 5000, "item": None},
    25: {"trophy_key": "combat.trophy_silver", "trophy_fallback": "🥈 銀魔箱勳章", "gold": 0, "item": "mystery_power_ring"},
    50: {"trophy_key": "combat.trophy_gold", "trophy_fallback": "🥇 金魔箱勳章", "gold": 0, "item": "mystery_void_blade"},
    75: {"trophy_key": "combat.trophy_diamond", "trophy_fallback": "💎 鑽石魔箱勳章", "gold": 0, "item": "immortal_totem"},
    99: {"trophy_key": "combat.trophy_champion", "trophy_fallback": "👑 冠軍魔箱勳章", "gold": 0, "item": "dragon_scale_shard"},
}

# --- Monster scaling (tower / dungeon, per floor) ---------------------------
MONSTER_HP_PER_FLOOR = 28
MONSTER_ATK_PER_FLOOR = 7
# Monster defense rework: defense is no longer a generic stat every monster
# accumulates per floor — flat def subtraction was silently strangling physical
# builds at higher floors. Defense is now an IDENTITY stat: most monsters run
# 0 def, and a few armored archetypes (tortoise/golem/knight...) carry a big
# chunk of it as their whole gimmick.
MONSTER_DEF_PER_FLOOR = 0
BOSS_HP_MULT = 3.0
BOSS_ATK_MULT = 1.7
BOSS_DEF_BONUS = 0
BOSS_EXP_MULT = 4.0
BOSS_MONEY_MULT = 4

# --- Equipment set bonuses --------------------------------------------------
# Equip multiple items sharing a "set" tag (in items.json) to unlock these.
# Bonuses are cumulative: equipping 3 pieces grants the 2- AND 3-piece tiers.
# Bonus keys match get_equipment_bonuses: atk / def / hp / magic / res / spd / mp.
SET_BONUSES = {
    "guardian": {     # 守護者 / Guardian — tanky bruiser set
        2: {"def": 25, "hp": 150},
        3: {"def": 50, "hp": 400, "atk": 20},
    },
    "archmage": {     # 大法師 / Archmage — caster set
        2: {"magic": 25, "mp": 40},
        3: {"magic": 60, "mp": 120},
    },
    "assassin": {     # 刺客 / Assassin — lvl16 atk+spd burst set
        2: {"atk": 15, "spd": 10},
        3: {"atk": 35, "spd": 25},
    },
    "necromancer": {  # 死靈法師 / Necromancer — lvl30 dark caster set
        2: {"magic": 40, "hp": 150},
        3: {"magic": 90, "hp": 350},
    },
    "vampiric": {     # 血族 / Vampiric — lvl40 sustain burst/sustain set
        2: {"atk": 25, "spd": 15, "hp": 180},
        3: {"atk": 60, "spd": 35, "hp": 480},
    },
    "phoenix": {      # 鳳凰 / Phoenix — lvl60 rebirth hybrid set
        2: {"atk": 35, "magic": 45, "mdef": 15},
        3: {"atk": 90, "magic": 100, "hp": 420, "spd": 20},
    },
    "voidwalker": {   # 虛空行者 / Voidwalker — lvl70 speed/tempo set
        2: {"spd": 30, "atk": 20},
        3: {"spd": 65, "atk": 45, "magic": 45},
    },
    "nether": {       # 冥界 / Nether — lvl80 top-tier endgame set
        2: {"atk": 55, "magic": 55, "def": 55},
        3: {"atk": 135, "magic": 135, "def": 135, "hp": 520},
    },
}

# --- Per-area shops ----------------------------------------------------------
# Overworld shops are fixed and named per area so players can remember where a
# given shop is. Exploration can still surface a wandering mystery merchant.
MYSTERY_MERCHANT_STOCK_COUNT = 5
MYSTERY_MERCHANT_CHANCE = 0.08

AREA_FIXED_SHOPS = {
    "area_00village": {
        "name_zh": "鈴蘭小舖", "name_en": "Lilybell Shop",
        "items": ["health_potion", "mana_potion", "antidote_herb", "rusty_dagger", "wooden_sword", "leather_armor", "scroll_heal_light"],
    },
    "area_01grassland": {
        "name_zh": "風車補給站", "name_en": "Windmill Supply Post",
        "items": ["health_potion", "mana_potion", "burn_salve", "rusty_shortbow", "hunting_knife", "novice_wand", "travelers_charm"],
    },
    "area_05forest": {
        "name_zh": "霧葉小舖", "name_en": "Mistleaf Outfitters",
        "items": ["health_potion", "mana_potion", "thaw_herb", "bronze_sword", "apprentice_staff", "wolf_fang_dagger", "swift_boots", "hide_armor"],
    },
    "area_10deep_forest": {
        "name_zh": "樹心行囊屋", "name_en": "Heartwood Packhouse",
        "items": ["medium_health_potion", "medium_mana_potion", "paralyze_cure", "iron_sword", "mage_staff", "ranger_bow", "ember_wand", "windrunner_pendant"],
    },
    "area_20lab": {
        "name_zh": "試管黑市", "name_en": "Test-Tube Black Market",
        "items": ["medium_health_potion", "medium_mana_potion", "elixir_of_cleansing", "guardian_hammer", "archmage_staff", "experimental_blade", "hazmat_plate", "neural_amplifier"],
    },
    "area_30graveyard": {
        "name_zh": "墓燈當舖", "name_en": "Gravelamp Pawnshop",
        "items": ["high_health_potion", "high_mana_potion", "calming_incense", "bone_reaper_scythe", "spectral_shroud", "grave_charm", "soul_reaper_wand"],
    },
    "area_40vampire_castle": {
        "name_zh": "紅月裁縫館", "name_en": "Redmoon Atelier",
        "items": ["high_health_potion", "high_mana_potion", "echo_herb", "crimson_rapier", "noble_vampire_coat", "blood_ring", "vampiric_wand"],
    },
    "area_55dragon_valley": {
        "name_zh": "龍脊軍需庫", "name_en": "Dragonspine Armory",
        "items": ["high_health_potion", "high_mana_potion", "elixir_of_cleansing", "titan_warhammer", "aegis_of_ages", "sage_ring", "dragon_scale_shield"],
    },
    "area_70void_rift": {
        "name_zh": "裂隙旅商棚", "name_en": "Riftway Trader Tent",
        "items": ["high_health_potion", "high_mana_potion", "mystery_elixir", "voidwalker_glaive", "voidwalker_plate", "voidwalker_band", "void_orb"],
    },
    "area_99demon_castle": {
        "name_zh": "終焉補給所", "name_en": "Final Bastion Provisioner",
        "items": ["high_health_potion", "high_mana_potion", "mystery_mana_elixir", "netherblade", "netherplate", "nethercrown", "abyss_scepter"],
    },
}


def area_shop_config(area_id: str) -> dict:
    return AREA_FIXED_SHOPS.get(area_id, AREA_FIXED_SHOPS["area_00village"])


def area_shop_stock(area_id: str) -> list[str]:
    return list(area_shop_config(area_id).get("items", []))

# --- Dungeon (roguelike mode) ----------------------------------------------
DUNGEON_MAX_FLOOR = 15
DUNGEON_BOSS_FLOOR = 15
# Mini-boss checkpoints: forced UNIQUE boss fights (defined in dungeon.DUNGEON_MINIBOSSES,
# each with its own signature mechanic) that grant a relic choice, not a run-ending fight.
DUNGEON_MINIBOSS_FLOORS = (4, 8, 12)
# Sealed level-1 starting character for a dungeon run.
DUNGEON_START_STATS = {
    "max_hp": 60, "base_atk": 12, "base_def": 5, "base_mdef": 3,
    "base_spd": 10, "base_magic": 8, "max_mp": 30,
}
# Sealed character's "level" is fixed (no in-run leveling) — set high enough that
# dungeon-pool skills up to req_level 10 are always usable once picked up.
DUNGEON_SEALED_LEVEL = 10

# Dungeon monster scaling — deliberately gentle on floor 1 so a fresh sealed
# character (12 atk / 5 def / 60 hp) comfortably beats a floor-1 monster.
# HP_BASE/HP_PER_FLOOR were bumped up slightly (22->24, 13->16) to compensate for
# DEF_PER_FLOOR dropping to 0 below — removing ~0.8 def/floor of damage mitigation
# meant every hit landed harder than the floor's time-to-kill was tuned around.
DUNGEON_MON_HP_BASE = 24
DUNGEON_MON_HP_PER_FLOOR = 16
DUNGEON_MON_ATK_BASE = 6
DUNGEON_MON_ATK_PER_FLOOR = 2.0
# Defense-as-identity rework: regular dungeon monsters run 0 def; only the
# "Guardian" elite archetype carries flat def (see dungeon.ELITE_ARCHETYPES).
DUNGEON_MON_DEF_PER_FLOOR = 0
DUNGEON_MON_SPD_BASE = 8
DUNGEON_MON_SPD_PER_FLOOR = 0.5
DUNGEON_MON_EXP_BASE = 18
DUNGEON_MON_EXP_PER_FLOOR = 10
DUNGEON_ELITE_HP_MULT = 1.9
DUNGEON_ELITE_ATK_MULT = 1.4
DUNGEON_ELITE_DEF_BONUS = 0
DUNGEON_BOSS_HP_MULT = 4.0
DUNGEON_BOSS_ATK_MULT = 1.8
DUNGEON_BOSS_DEF_BONUS = 0

# Weights for the two non-guaranteed doors on each floor (the third door is
# ALWAYS a mystery event — see dungeon.roll_doors).
DUNGEON_ROOM_WEIGHTS = {"monster": 70, "elite": 20, "rest": 10}

# Corrosion (dungeon-exclusive stacking DoT): each tick deals
# stacks * dmg_per_stack flat damage. Stacks are CAPPED — pre-cap corrosion
# builds snowballed into deleting minibosses in 2-3 turns, so both the per-stack
# damage and the total stack count got reined in.
CORROSION_BASE_DMG = 10            # base flat damage per stack (scaled by floor)
CORROSION_DMG_PER_FLOOR = 0
CORROSION_MAX_STACKS = 99         # hard ceiling on accumulated stacks
CORROSION_TURNS = 99             # effectively lasts the whole fight once applied
