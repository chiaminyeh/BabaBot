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

# --- Stat allocation: 1 spent point -> this much of the stat ----------------
# (vit also adds HP/DEF, int also adds MP — see stats.recalc_player_stats)
ALLOC_BONUS = {"atk": 3, "vit": 1, "int": 1, "spd": 2, "luck": 1}

# --- Stamina ---------------------------------------------------------------
STAMINA_MAX = 200
STAMINA_COST_EXPLORE = 4
STAMINA_COST_BOSS = 20
STAMINA_COST_TOWER = 20
STAMINA_COST_DUNGEON = 20
STAMINA_POTION_RESTORE = 50

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

# Luck stat payoff: +crit chance (flat) and +drop rate (relative multiplier) per
# point of player.base_luck. See combat.luck_crit_bonus / combat._roll_drops_for.
LUCK_CRIT_BONUS_PER_POINT = 0.002        # +0.2% crit chance per luck point
LUCK_DROP_RATE_BONUS_PER_POINT = 0.01    # +1% relative boost to every drop rate per luck point

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

# First-kill/repeat boss scroll drop: guaranteed 100% on first kill, this% on repeat clears.
BOSS_DAILY_SCROLL_CHANCE = 0.30

# Fallback scroll pool for area bosses whose "drops" table has no scroll entry.
FALLBACK_BOSS_SCROLLS = {
    "goblin_chief": ["scroll_heal_light", "scroll_power_slash", "scroll_fireball"],
    "forest_guardian": ["scroll_shadow_step", "scroll_combo_attack"],
    "bee_queen": ["scroll_blood_strike", "scroll_double_strike", "ice_spear_scroll"],
    "mad_doctor": ["scroll_inferno", "scroll_divine_thunder", "scroll_blizzard"],
    "lich": ["scroll_divine_thunder", "scroll_blizzard"],
    "vampire_lord": ["scroll_divine_thunder", "scroll_inferno"],
    "rat_king": ["scroll_venom_cloud"],
    "bone_knight": ["scroll_frost_nova"],
    "orc_warlord": ["scroll_earthquake"],
    "abyss_overlord": ["scroll_meteor_swarm", "scroll_void_eruption"],
    "ancient_dragon": ["scroll_inferno", "scroll_earthquake", "scroll_meteor_swarm"],
    "void_sovereign": ["scroll_void_eruption", "scroll_meteor_swarm", "scroll_blizzard"],
    "colosseum_champion": ["scroll_holy_smash", "scroll_divine_thunder", "scroll_power_slash"],
    "sargeras": ["scroll_meteor_swarm", "scroll_holy_smash", "scroll_divine_thunder"],
}

# Magic Tower milestone rewards, keyed by the floor number just completed.
# trophy_key/trophy_fallback feed t() at the call site (locale text lives in locale_en.json).
TOWER_MILESTONES = {
    10: {"trophy_key": "combat.trophy_bronze", "trophy_fallback": "🥉 銅魔箱勳章", "gold": 5000, "item": None},
    25: {"trophy_key": "combat.trophy_silver", "trophy_fallback": "🥈 銀魔箱勳章", "gold": 0, "item": "mystery_power_ring"},
    50: {"trophy_key": "combat.trophy_gold", "trophy_fallback": "🥇 金魔箱勳章", "gold": 0, "item": "mystery_void_blade"},
    75: {"trophy_key": "combat.trophy_diamond", "trophy_fallback": "💎 鑽石魔箱勳章", "gold": 0, "item": "immortal_totem"},
    99: {"trophy_key": "combat.trophy_champion", "trophy_fallback": "👑 冠軍魔箱勳章", "gold": 0, "item": "scroll_divine_thunder"},
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
    "vampiric": {     # 血族 / Vampiric — lvl40 sustain set
        2: {"atk": 30, "hp": 250},
        3: {"atk": 70, "hp": 550},
    },
    "phoenix": {      # 鳳凰 / Phoenix — lvl60 hybrid fire set
        2: {"atk": 50, "magic": 50},
        3: {"atk": 110, "magic": 110, "hp": 400},
    },
    "voidwalker": {   # 虛空行者 / Voidwalker — lvl70 speed/evasion set
        2: {"spd": 30},
        3: {"spd": 60},
    },
    "nether": {       # 冥界 / Nether — lvl80 top-tier all-round set
        2: {"atk": 80, "def": 80},
        3: {"atk": 180, "def": 180, "hp": 600},
    },
}

# --- Per-area shops ----------------------------------------------------------
# Each overworld area gets its OWN daily shop stock instead of one global shop,
# so a high-level character standing in the starting village doesn't get offered
# end-game gear just because their own level qualifies for it. "gear_range" caps
# which weapon/armor/accessory exclusive_level values that area's shop can roll
# (materials/scrolls/cures are unaffected — those stay available everywhere).
# "potion_tier" swaps the always-in-stock health/mana potion for a stronger
# version in higher areas (no new weapon tiers exist above 25, so later areas
# differentiate mainly through better potions, per design).
AREA_SHOP_TIERS = {
    "area_00village":      {"gear_range": (0, 4),   "potion_tier": "basic"},
    "area_01grassland":    {"gear_range": (0, 4),   "potion_tier": "basic"},
    "area_05forest":       {"gear_range": (5, 9),   "potion_tier": "basic"},
    "area_10deep_forest":  {"gear_range": (10, 15), "potion_tier": "medium"},
    "area_20lab":          {"gear_range": (16, 25), "potion_tier": "medium"},
    # With the lv55/lv70 areas now filling the 40-80 gap, each late area sells a
    # sliding band instead of graveyard dumping the entire 16-80 range: you shop
    # where you adventure. The 4 true mythic items stay out of the shop entirely
    # (shop_weight 0) — those are forge-only, see trpg/recipes.py.
    "area_30graveyard":       {"gear_range": (16, 45), "potion_tier": "high"},
    "area_40vampire_castle":  {"gear_range": (30, 60), "potion_tier": "high"},
    "area_55dragon_valley":   {"gear_range": (40, 70), "potion_tier": "high"},
    "area_70void_rift":       {"gear_range": (55, 80), "potion_tier": "high"},
    "area_99demon_castle":    {"gear_range": (60, 80), "potion_tier": "high"},
}
# (min_hp_potion, min_mp_potion) shop-slot item ids per potion_tier.
SHOP_POTION_TIERS = {
    "basic": ("health_potion", "mana_potion"),
    "medium": ("medium_health_potion", "medium_mana_potion"),
    "high": ("high_health_potion", "high_mana_potion"),
}

# --- Dungeon (roguelike mode) ----------------------------------------------
DUNGEON_MAX_FLOOR = 15
DUNGEON_BOSS_FLOOR = 15
# Mini-boss checkpoints: forced UNIQUE boss fights (defined in dungeon.DUNGEON_MINIBOSSES,
# each with its own signature mechanic) that grant a relic choice, not a run-ending fight.
DUNGEON_MINIBOSS_FLOORS = (5,10)
# Sealed level-1 starting character for a dungeon run.
DUNGEON_START_STATS = {
    "max_hp": 60, "base_atk": 12, "base_def": 5, "base_mdef": 3,
    "base_spd": 10, "base_magic": 8, "base_res": 0, "max_mp": 30,
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
