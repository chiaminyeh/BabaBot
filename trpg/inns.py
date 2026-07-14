"""Data-driven regional inn rooms, stories, and area button emoji."""

import random

INN_EVENT_CHANCE = 0.45

INN_ROOMS = {
    "cot": {
        "name_zh": "簡陋床位", "name_en": "Simple Cot", "emoji": "🛏️",
        "restore_pct": 0.35, "clear_statuses": 0,
        "base_cost": 20, "cost_per_level": 2,
    },
    "room": {
        "name_zh": "舒適客房", "name_en": "Comfort Room", "emoji": "🕯️",
        "restore_pct": 0.70, "clear_statuses": 1,
        "base_cost": 50, "cost_per_level": 5,
    },
    "suite": {
        "name_zh": "特製套房", "name_en": "House Special", "emoji": "🛁",
        "restore_pct": 1.0, "clear_statuses": "all",
        "base_cost": 140, "cost_per_level": 10,
    },
}

# Button labels remain simply Shop/Inn; these emoji carry the area identity.
SHOP_EMOJIS = {
    "area_00village": "🪻", "area_01grassland": "🌾",
    "area_05forest": "🍃", "area_10deep_forest": "🌳",
    "area_20lab": "🧪", "area_30graveyard": "🏮",
    "area_40vampire_castle": "🌙", "area_55dragon_valley": "🐉",
    "area_70void_rift": "🌀", "area_99demon_castle": "🔥",
}

AREA_INNS = {
    "area_00village": {
        "emoji": "🏡",
        "intro_zh": "鈴蘭旅館的壁爐燒得正旺，老闆娘把今日冒險者的傳聞寫在木板上。",
        "intro_en": "The Lilybell Inn's hearth burns warmly while the owner pins today's adventurer gossip to a wooden board.",
        "events": [
            {"zh": "老闆娘悄聲說，村外的史萊姆看似柔軟，成群出現時卻最容易讓新手大意。", "en": "The owner whispers that slimes look harmless, but a whole pack is where new adventurers get careless."},
            {"zh": "你聽見鐵匠老王抱怨：真正可靠的裝備，得記得定期回村強化。", "en": "You hear Old Wang grumble that dependable gear still needs regular upgrades back in town."},
        ],
    },
    "area_01grassland": {
        "emoji": "🛖",
        "intro_zh": "風車客棧貼著麥田搭建，整晚都能聽見木葉片規律轉動。",
        "intro_en": "The Windmill Inn stands beside the wheat fields, its wooden sails turning steadily through the night.",
        "events": [
            {"zh": "磨坊主提醒你：郊外看似平靜，但最好先備好藥水再挑戰藏在深處的強敵。", "en": "The miller warns that the outskirts look calm, but you should stock potions before challenging anything lurking deeper out."},
            {"zh": "一名獵人說，野豬衝鋒前總會先用蹄子刨地。", "en": "A hunter says wild boars always scrape the ground before charging."},
        ],
    },
    "area_05forest": {
        "emoji": "🕯️",
        "intro_zh": "霧燈旅館藏在巨木根部，窗邊長明的燭火勉強把濃霧擋在外頭。",
        "intro_en": "The Mistlamp Inn is tucked beneath giant roots, its candlelit windows barely holding back the fog.",
        "events": [
            {"zh": "老獵人壓低聲音：迷霧林王是個長得高大、可怕又陰森的樹人，但樹人都怕火；讓牠燃燒，超再生就會停下來。", "en": "An old hunter whispers: the Mistwood King is a towering, terrifying treant, but treants fear fire. Keep it burning and its regeneration will stop."},
            {"zh": "門口掛著焦黑藤蔓。店主說森林精靈會替同伴療傷，看到牠最好先處理。", "en": "Charred vines hang by the door. The keeper says Forest Sprites heal allies, so deal with them first."},
        ],
    },
    "area_10deep_forest": {
        "emoji": "🍄",
        "intro_zh": "菌傘旅店由發光蘑菇圍成，精靈用露水與樹脂交換床位。",
        "intro_en": "The Mushroom-Cap Inn glows beneath giant fungi, where sprites trade dew and resin for a bed.",
        "events": [
            {"zh": "巡林人說，越深入森林，治療者與蔓藤越常互相掩護；別只盯著體型最大的敵人。", "en": "A ranger says healers and vines protect one another deeper in the forest; do not focus only on the largest enemy."},
            {"zh": "你睡前聽見樹根下傳來敲擊聲，店主只說森林會記得每一個傷害它的人。", "en": "You hear knocking beneath the roots. The keeper only says the forest remembers those who harm it."},
        ],
    },
    "area_20lab": {
        "emoji": "⚗️",
        "intro_zh": "防毒休息站由廢棄觀察室改建，床單帶著淡淡的消毒水味。",
        "intro_en": "The Decontamination Lodge occupies an abandoned observation room, its sheets smelling faintly of antiseptic.",
        "events": [
            {"zh": "逃亡研究員提醒你：實驗體擅長堆疊異常，解毒與淨化道具比多帶一把武器更重要。", "en": "A fleeing researcher warns that experiments stack status effects; cures may matter more than another weapon."},
            {"zh": "半夜警報突然響起，老闆熟練地拔掉電源，說它已經誤報三年了。", "en": "An alarm erupts at midnight. The keeper unplugs it and says it has been giving false warnings for three years."},
        ],
    },
    "area_30graveyard": {
        "emoji": "⚰️",
        "intro_zh": "守墓人旅舍只有幾張窄床，窗外的墓燈整夜都沒有熄滅。",
        "intro_en": "The Gravedigger's Rest has only a few narrow beds, while grave lamps burn outside all night.",
        "events": [
            {"zh": "守墓人提醒你，亡魂喜歡拖長戰鬥；沒有把握時，先處理會召喚或治療的敵人。", "en": "The gravedigger warns that spirits love drawn-out battles; remove summoners and healers first."},
            {"zh": "你醒來時床尾多了一束白花。沒有人承認進過你的房間。", "en": "You wake to find white flowers at the foot of your bed. Nobody admits entering your room."},
        ],
    },
    "area_40vampire_castle": {
        "emoji": "🦇",
        "intro_zh": "紅幕旅館的窗戶全被厚布遮住，銀製餐具則被鎖在櫃子裡。",
        "intro_en": "Every window at the Crimson Curtain Inn is covered, and the silver cutlery stays locked away.",
        "events": [
            {"zh": "侍者警告：吸血鬼靠造成傷害恢復生命，防禦與閃避有時比一味搶攻更有效。", "en": "The attendant warns that vampires heal through damage dealt; defense and evasion can outperform reckless offense."},
            {"zh": "午夜有人敲了三次房門。旅館規矩寫得很清楚：日落後絕不邀請陌生人進房。", "en": "Someone knocks three times at midnight. The rules are clear: never invite a stranger inside after sunset."},
        ],
    },
    "area_55dragon_valley": {
        "emoji": "⛺",
        "intro_zh": "龍望營帳用耐火皮革縫成，地面會隨遠方的龍吼輕輕震動。",
        "intro_en": "The Dragonwatch tents are stitched from fireproof hide, and the ground trembles with distant roars.",
        "events": [
            {"zh": "老兵說，挑戰巨龍前應先確認裝備與藥水；峽谷不會因你的勇氣而手下留情。", "en": "A veteran says to check gear and potions before facing a dragon; the gorge will not go easy on bravery."},
            {"zh": "一片幼龍鱗落在營火旁，整晚散發微熱，天亮後卻化成了灰。", "en": "A young dragon scale stays warm beside the fire all night, only to crumble into ash at dawn."},
        ],
    },
    "area_70void_rift": {
        "emoji": "🌌",
        "intro_zh": "繫繩旅店的每張床都用鐵索固定，免得睡著的人連床一起漂進裂隙。",
        "intro_en": "Every bed at the Tethered Inn is chained down, so sleepers do not drift into the rift.",
        "events": [
            {"zh": "旅人提醒你，虛空生物擅長打亂節奏；保留資源應付變化，比一次用光技能更安全。", "en": "A traveler warns that void creatures disrupt tempo; saving resources is safer than spending everything at once."},
            {"zh": "你夢見另一個自己站在裂隙對面。醒來後，枕邊多了一串不屬於你的腳印。", "en": "You dream of another you across the rift. When you wake, unfamiliar footprints circle your bed."},
        ],
    },
    "area_99demon_castle": {
        "emoji": "🏰",
        "intro_zh": "最後壁壘由廢棄軍營改建，每個住客都知道明天可能是最後一戰。",
        "intro_en": "The Last Bastion occupies an abandoned barracks, and every guest knows tomorrow may bring their final battle.",
        "events": [
            {"zh": "負傷騎士反覆叮嚀：魔王力量深不可測，傳說洞窟中的勇者之劍或許是唯一突破口。", "en": "A wounded knight insists the Demon Lord's power is unfathomable; the Hero's Sword may be the only opening."},
            {"zh": "整座旅館無人交談，只有磨劍聲、鎧甲碰撞聲，以及遠方城門低沉的震動。", "en": "Nobody speaks. Only whetstones, clinking armor, and the distant groan of the gates break the silence."},
        ],
    },
}


def area_inn_config(area_id: str) -> dict:
    return AREA_INNS.get(area_id, AREA_INNS["area_00village"])


def inn_room_cost(room_id: str, level: int) -> int:
    room = INN_ROOMS[room_id]
    return room["base_cost"] + max(1, int(level)) * room["cost_per_level"]


def apply_inn_room(player, room_id: str, choose_status=random.choice) -> tuple[int, int, int]:
    """Apply one room's recovery exactly once; return HP, MP, statuses cleared."""
    room = INN_ROOMS[room_id]
    old_hp, old_mp = player.current_hp, player.current_mp
    restore_pct = room["restore_pct"]
    player.current_hp = min(player.max_hp, player.current_hp + max(1, int(player.max_hp * restore_pct)))
    player.current_mp = min(player.max_mp, player.current_mp + max(1, int(player.max_mp * restore_pct)))

    cleared = 0
    if room["clear_statuses"] == "all":
        cleared = len(player.status_effects)
        player.status_effects.clear()
    elif room["clear_statuses"] == 1 and player.status_effects:
        removed_status = choose_status(list(player.status_effects))
        del player.status_effects[removed_status]
        cleared = 1
    return player.current_hp - old_hp, player.current_mp - old_mp, cleared


def localized(config: dict, key: str, lang: str) -> str:
    return config.get(f"{key}_{'en' if lang == 'en' else 'zh'}", "")
