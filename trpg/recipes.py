"""Crafting recipes and equipment upgrade cost tables."""

CRAFTING_RECIPES = {
    "bronze_sword": {
        "name": "青銅長劍",
        "name_en": "Bronze Longsword",
        "materials": {"slime_jelly": 5, "boar_tusk": 3},
        "gold": 50
    },
    "iron_sword": {
        "name": "精鋼長劍",
        "name_en": "Steel Longsword",
        "materials": {"bone_shard": 5, "stolen_gem": 2},
        "gold": 150
    },
    "flame_sword": {
        "name": "火焰之劍",
        "name_en": "Sword of Flame",
        "materials": {"bat_wing": 5, "gargoyle_stone": 3},
        "gold": 500
    },
    "holy_sword": {
        "name": "聖光之劍",
        "name_en": "Sword of Holy Light",
        "materials": {"tainted_blood": 5, "lich_soulstone": 2},
        "gold": 1000
    },
    "iron_armor": {
        "name": "精鋼重甲",
        "name_en": "Steel Plate Armor",
        "materials": {"wolf_fur": 5, "bone_shard": 5},
        "gold": 200
    },
    "dragon_plate": {
        "name": "龍鱗重甲",
        "name_en": "Dragonscale Plate Armor",
        "materials": {"bat_wing": 5, "vampire_fang": 5},
        "gold": 1200
    },
    "sage_staff": {
        "name": "賢者之杖",
        "name_en": "Sage's Staff",
        "materials": {"ancient_wood": 5, "ectoplasm": 3},
        "gold": 800
    },
    "wyrmscale_barrier": {
        "name": "龍鱗壁壘",
        "name_en": "Wyrmscale Barrier",
        "materials": {"dragon_scale_shard": 8, "drake_fang": 5},
        "gold": 15000
    },
    "voidforged_edge": {
        "name": "虛空鍛刃",
        "name_en": "Voidforged Edge",
        "materials": {"void_shard": 8, "void_crystal": 1},
        "gold": 25000
    },
    # 神話裝備鍛造：不再開放一般裝備隨意鍛造，只有這四件終局裝備能鍛造，
    # 且各自需要對應的稀有王級材料，不是靠一般素材就能湊出來的。
    "demon_king_horn": {
        "name": "魔王之角",
        "name_en": "Demon Lord's Horn",
        "materials": {"sargeras_crown": 1},
        "gold": 20000
    },
    "abyssal_cloak": {
        "name": "深淵潛航者披風",
        "name_en": "Abyssal Strider's Cloak",
        "materials": {"void_crystal": 3},
        "gold": 8000
    },
    "dragonbone_greatsword": {
        "name": "龍骨毀滅劍",
        "name_en": "Dragonbone Ruin Blade",
        "materials": {"dragon_heart": 1},
        "gold": 12000
    },
    "forbidden_blood_chalice": {
        "name": "禁忌血之聖杯",
        "name_en": "Forbidden Chalice of Blood",
        "materials": {"pure_blood_essence": 1},
        "gold": 12000
    },
    # 主線關鍵道具：三位主線 NPC 各給一枚碎片，集齊後在這裡合成。
    # 放在行囊即可顯示敵人弱點＋在傳說洞窟指引勇者之劍（見 view.py _has_magic_eye）。
    "magic_eye": {
        "name": "魔法之眼",
        "name_en": "Eye of Insight",
        "materials": {"magic_shard_emerald": 1, "magic_shard_amethyst": 1, "magic_shard_crimson": 1},
        "gold": 3000
    }
}

UPGRADE_COSTS = {
    1: {"gold": 100, "material": "slime_jelly", "mat_qty": 2, "rate": 1.00, "label": "100%"},
    2: {"gold": 250, "material": "boar_tusk", "mat_qty": 2, "rate": 0.80, "label": "80%"},
    3: {"gold": 500, "material": "wolf_fur", "mat_qty": 2, "rate": 0.60, "label": "60%"},
    4: {"gold": 1000, "material": "ancient_wood", "mat_qty": 2, "rate": 0.40, "label": "40%"},
    5: {"gold": 2500, "material": "ectoplasm", "mat_qty": 2, "rate": 0.25, "label": "25%"},
    # +6 以上是後期金幣回收管道：費用陡升、成功率保底不再往下掉太狠（失敗不降級，
    # 純粹是「錢跟稀有材料的坑」），讓後期滿裝玩家的金幣有地方花。
    6: {"gold": 6000, "material": "gargoyle_stone", "mat_qty": 3, "rate": 0.22, "label": "22%"},
    7: {"gold": 12000, "material": "lich_soulstone", "mat_qty": 3, "rate": 0.20, "label": "20%"},
    8: {"gold": 22000, "material": "dragon_scale_shard", "mat_qty": 3, "rate": 0.18, "label": "18%"},
    9: {"gold": 38000, "material": "demon_core", "mat_qty": 2, "rate": 0.15, "label": "15%"},
    10: {"gold": 60000, "material": "void_crystal", "mat_qty": 2, "rate": 0.12, "label": "12%"},
}

MAX_UPGRADE_LEVEL = max(UPGRADE_COSTS)
