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
