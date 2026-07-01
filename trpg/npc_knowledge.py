"""讓 NPC（目前是村長）在回答玩家問題時，能夠引用遊戲裡真實存在的道具/技能/怪物/
區域/任務/成就/地下城內容/異常狀態，而不是憑空幻想出遊戲裡沒有的東西。

做法：把 cog 已經讀進記憶體的各份 JSON 攤平成一份「知識庫」，依照玩家問題裡出現的
關鍵字（真實存在的中/英文名稱）挑出相關條目，組成一小段「事實」文字餵給 LLM，並要求
LLM 只能根據這些事實回答。知識庫只在第一次使用時攤平一次（快取在 cog 上），之後每次
問問題只是字串比對，不會隨著遊戲內容變多而變貴。
"""

import re

from trpg.i18n import tf

# 一個條目：(category, name_zh, name_en, fact_zh, fact_en)
_KnowledgeEntry = tuple

# 有些技能/裝備名稱會加上 emoji 前綴方便玩家一眼看懂機制（例如「🛡️ 盾擊」、
# 「⚔️✖️2 連環快斬」），但玩家問問題時當然不會打 emoji。比對關鍵字前先把開頭那串
# emoji/符號/數字（✖️2 這種倍數標示）去掉，只留下真正的名字，兩邊字串才對得起來。
_LEADING_DECORATION_RE = re.compile(
    "^[" +
    "\U0001F300-\U0001FAFF"  # 主要 emoji 區塊（🛡️🔥❄️⚡🔮🌑🩸💚 等）
    "☀-➿"          # 雜項符號與裝飾符號（✖️☠️✨💫等）
    "️"                 # variation selector（emoji 後面常跟著的隱形字元）
    "‍"                 # zero-width joiner
    "0-9\\s"                 # 倍數標示的數字（✖️2 的 2）與空白
    + "]+"
)


def _match_key(name: str) -> str:
    if not name:
        return name
    cleaned = _LEADING_DECORATION_RE.sub("", name)
    return cleaned or name

_CATEGORY_LABELS = {
    "item": ("道具", "Item"),
    "skill": ("技能", "Skill"),
    "achievement": ("成就", "Achievement"),
    "dungeon_item": ("地下城裝備", "Dungeon Gear"),
    "dungeon_relic": ("地下城遺物", "Dungeon Relic"),
    "status": ("異常狀態", "Status Effect"),
    "quest": ("委託", "Quest"),
    "npc": ("人物", "NPC"),
    "monster": ("怪物", "Monster"),
    "boss": ("首領", "Boss"),
}


def _label(category: str, lang: str) -> str:
    zh, en = _CATEGORY_LABELS.get(category, (category, category))
    return en if lang == "en" else zh


def _build_index(cog) -> list:
    entries = []

    def add(category: str, name_zh: str, name_en: str, fact_zh: str, fact_en: str):
        if not name_zh:
            return
        entries.append((category, name_zh, name_en or name_zh, fact_zh, fact_en or fact_zh))

    for iid, item in cog.items.items():
        name_zh, name_en = item.get("name", iid), item.get("name_en")
        desc_zh, desc_en = item.get("desc", ""), item.get("desc_en")
        price = item.get("price")
        price_suffix_zh = f"（{price}金幣）" if price else ""
        price_suffix_en = f" ({price} gold)" if price else ""
        add("item", name_zh, name_en,
            f"{desc_zh}{price_suffix_zh}", f"{desc_en or desc_zh}{price_suffix_en}")

    for sid, skill in cog.skills.items():
        name_zh, name_en = skill.get("name", sid), skill.get("name_en")
        desc_zh, desc_en = skill.get("desc", ""), skill.get("desc_en")
        req = skill.get("req_level", 1)
        add("skill", name_zh, name_en,
            f"{desc_zh}（需求等級 Lv.{req}）", f"{desc_en or desc_zh} (requires Lv.{req})")

    for aid, achv in cog.achievements.items():
        add("achievement", achv.get("name", aid), achv.get("name_en"),
            achv.get("desc", ""), achv.get("desc_en"))

    for did, ditem in cog.dungeon_items.items():
        add("dungeon_item", ditem.get("name", did), ditem.get("name_en"),
            ditem.get("desc", ""), ditem.get("desc_en"))

    for rid, relic in cog.dungeon_relics.items():
        add("dungeon_relic", relic.get("name", rid), relic.get("name_en"),
            relic.get("desc", ""), relic.get("desc_en"))

    for sid, status in cog.status_effects.items():
        add("status", status.get("name", sid), status.get("name_en"),
            status.get("desc", ""), status.get("desc_en"))

    for qid, quest in cog.quests.items():
        title_zh, title_en = quest.get("title", qid), quest.get("title_en")
        npc_zh = quest.get("npc_name", "")
        npc_en = quest.get("npc_name_en")
        location = f"委託人：{npc_zh}" if npc_zh else "公會委託"
        location_en = f"Quest giver: {npc_en or npc_zh}" if npc_zh else "Guild commission"
        add("quest", title_zh, title_en, location, location_en)
        if npc_zh:
            add("npc", npc_zh, npc_en, f"與委託「{title_zh}」有關的人物", f"Related to the quest \"{title_en or title_zh}\"")

    # 一般區域自己的怪物與首領
    for area_id, area in cog.areas.items():
        area_zh = tf(area, "area_name", "zh") or area_id
        area_en = tf(area, "area_name", "en") or area_zh
        for mid, mon in (area.get("monsters") or {}).items():
            name_zh, name_en = mon.get("name", mid), mon.get("name_en")
            add("monster", name_zh, name_en,
                f"在【{area_zh}】一帶出沒的怪物", f"A monster found in [{area_en}]")
        boss = area.get("boss")
        if boss:
            name_zh, name_en = boss.get("name", ""), boss.get("name_en")
            add("boss", name_zh, name_en,
                f"【{area_zh}】的區域首領", f"The area boss of [{area_en}]")

    # 魔塔/地下城共用怪物池（依樓層分層）
    for tier in (cog.monster_pool or {}).values():
        lo, hi = tier.get("floor_range", [1, 1])
        range_zh = f"魔塔／地下城第 {lo}~{hi} 層一帶會遇到的怪物"
        range_en = f"A monster encountered around floors {lo}-{hi} of the Tower/Dungeon"
        for mid, mon in (tier.get("monsters") or {}).items():
            add("monster", mon.get("name", mid), mon.get("name_en"), range_zh, range_en)
        boss = tier.get("boss")
        if boss:
            add("boss", boss.get("name", ""), boss.get("name_en"),
                f"魔塔／地下城第 {lo}~{hi} 層一帶的首領", f"A boss around floors {lo}-{hi} of the Tower/Dungeon")

    return entries


def _get_index(cog) -> list:
    cached = getattr(cog, "_chief_knowledge_index", None)
    if cached is None:
        cached = _build_index(cog)
        cog._chief_knowledge_index = cached
    return cached


def _world_overview(cog, lang: str) -> str:
    parts = []
    for area in cog.areas.values():
        name = tf(area, "area_name", lang) or "???"
        req = area.get("req_level", 1)
        parts.append(f"{name}(Lv.{req})")
    sep = ", " if lang == "en" else "、"
    return sep.join(parts)


def _current_area_fallback(cog, player, lang: str) -> list:
    """問題沒對到任何關鍵字時，退而給玩家目前所在區域的怪物/首領當保底知識。"""
    area = cog.areas.get(getattr(player, "current_area", ""), {})
    area_name = tf(area, "area_name", lang) or ""
    lines = []
    for mon in (area.get("monsters") or {}).values():
        name = tf(mon, "name", lang)
        if name:
            lines.append(f"- [{_label('monster', lang)}] {name}")
    boss = area.get("boss")
    if boss:
        name = tf(boss, "name", lang)
        if name:
            lines.append(f"- [{_label('boss', lang)}] {name}")
    return lines[:5]


def build_grounding_context(cog, player, question: str, lang: str) -> str:
    """回傳一段「事實」文字，供村長回答問題前參考，讓 LLM 不會編造遊戲裡不存在的東西。"""
    index = _get_index(cog)
    q_lower = question.lower()

    matched_lines = []
    per_category_count = {}
    MAX_PER_CATEGORY = 4
    MAX_TOTAL = 12

    for category, name_zh, name_en, fact_zh, fact_en in index:
        if len(matched_lines) >= MAX_TOTAL:
            break
        match_zh = _match_key(name_zh)
        match_en = _match_key(name_en).lower() if name_en else ""
        hit = (len(match_zh) >= 2 and match_zh in question) or (
            match_en and len(match_en) >= 2 and match_en in q_lower
        )
        if not hit:
            continue
        if per_category_count.get(category, 0) >= MAX_PER_CATEGORY:
            continue
        per_category_count[category] = per_category_count.get(category, 0) + 1
        display_name = name_zh if lang != "en" else (name_en or name_zh)
        fact = fact_zh if lang != "en" else fact_en
        matched_lines.append(f"- [{_label(category, lang)}] {display_name} — {fact}")

    if not matched_lines:
        matched_lines = _current_area_fallback(cog, player, lang)

    world_map = _world_overview(cog, lang)

    if lang == "en":
        header = "[World Map] " + world_map
        body_header = "[Relevant facts for this question]" if matched_lines else ""
        instruction = (
            "Only use the facts listed above to answer. If the player asks about something "
            "not listed there, say in-character that you haven't heard of it — do not invent "
            "items, monsters, or mechanics that aren't listed."
        )
    else:
        header = "【世界地圖】" + world_map
        body_header = "【與這個問題有關的真實資訊】" if matched_lines else ""
        instruction = (
            "回答時只能引用上面列出的事實。如果玩家問的東西不在清單裡，請以角色口吻表示你沒聽過，"
            "不要編造清單以外的道具、怪物或機制。"
        )

    parts = [header]
    if body_header:
        parts.append(body_header)
        parts.extend(matched_lines)
    parts.append(instruction)
    return "\n".join(parts)
