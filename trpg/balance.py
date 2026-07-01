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
ALLOC_BONUS = {"atk": 3, "vit": 1, "int": 1, "spd": 3, "res": 2}

# --- Prestige / rebirth -----------------------------------------------------
# Every prestige level multiplies all base stats by (1 + this).
PRESTIGE_BONUS_PER_LEVEL = 0.10

# --- Combat -----------------------------------------------------------------
# Base crit chance before per-skill/weapon crit bonuses.
PHYSICAL_CRIT_CHANCE = 0.10   # normal weapon swings
SKILL_CRIT_CHANCE = 0.12      # physical/HP-sacrifice skills (magic can't crit)

# --- Monster scaling (tower / dungeon, per floor) ---------------------------
MONSTER_HP_PER_FLOOR = 28
MONSTER_ATK_PER_FLOOR = 7
MONSTER_DEF_PER_FLOOR = 4
BOSS_HP_MULT = 3.0
BOSS_ATK_MULT = 1.7
BOSS_DEF_BONUS = 15
BOSS_EXP_MULT = 4.0
BOSS_MONEY_MULT = 4

# --- Equipment set bonuses --------------------------------------------------
# Equip multiple items sharing a "set" tag (in items.json) to unlock these.
# Bonuses are cumulative: equipping 3 pieces grants the 2- AND 3-piece tiers.
# Bonus keys match get_equipment_bonuses: atk / def / hp / magic / res / spd / mp.
SET_BONUSES = {
    "guardian": {     # 守護者 / Guardian — tanky bruiser set
        2: {"def": 25, "hp": 150},
        3: {"def": 50, "hp": 400, "res": 15, "atk": 20},
    },
    "archmage": {     # 大法師 / Archmage — caster set
        2: {"magic": 25, "mp": 40},
        3: {"magic": 60, "mp": 120, "res": 10},
    },
}

# --- Dungeon (roguelike mode) ----------------------------------------------
DUNGEON_MAX_FLOOR = 15
DUNGEON_AP_PER_FLOOR = 3
DUNGEON_BOSS_FLOOR = 15
# Sealed level-1 starting character for a dungeon run.
DUNGEON_START_STATS = {
    "max_hp": 60, "base_atk": 12, "base_def": 5,
    "base_spd": 10, "base_magic": 8, "base_res": 0, "max_mp": 30,
}
# Stat points granted per dungeon floor cleared (spent in 整備, free of AP).
DUNGEON_STAT_POINTS_PER_FLOOR = 3

# Dungeon monster scaling — deliberately gentle on floor 1 so a fresh sealed
# character (12 atk / 5 def / 60 hp) comfortably beats a floor-1 monster.
DUNGEON_MON_HP_BASE = 22
DUNGEON_MON_HP_PER_FLOOR = 13
DUNGEON_MON_ATK_BASE = 6
DUNGEON_MON_ATK_PER_FLOOR = 2.0
DUNGEON_MON_DEF_PER_FLOOR = 0.8
DUNGEON_MON_SPD_BASE = 8
DUNGEON_MON_SPD_PER_FLOOR = 0.5
DUNGEON_MON_EXP_BASE = 18
DUNGEON_MON_EXP_PER_FLOOR = 10
DUNGEON_ELITE_HP_MULT = 1.9
DUNGEON_ELITE_ATK_MULT = 1.4
DUNGEON_ELITE_DEF_BONUS = 3
DUNGEON_BOSS_HP_MULT = 4.0
DUNGEON_BOSS_ATK_MULT = 1.8
DUNGEON_BOSS_DEF_BONUS = 8

# Room type weights behind the 3 doors. Relics are NOT a room reward anymore —
# they only drop from elite fights (see on_dungeon_victory), so they stay rare.
DUNGEON_ROOM_WEIGHTS = {"monster": 46, "elite": 18, "rest": 18, "event": 18}

# Corrosion (dungeon-exclusive stacking DoT): each tick deals
# stacks * dmg_per_stack flat damage; stacks accumulate, do not use % HP.
CORROSION_BASE_DMG = 4            # base flat damage per stack (scaled by floor)
CORROSION_DMG_PER_FLOOR = 1.5
CORROSION_TURNS = 99             # effectively lasts the whole fight once applied
