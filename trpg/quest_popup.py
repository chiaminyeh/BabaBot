"""突發任務／隱藏任務／公會任務大廳的後端邏輯。

任務來源（quests.json 欄位）：
- hidden: True          —— 隱藏任務，不需接取，符合條件自動追蹤、達成自動發獎。
- board: True           —— 會出現在公會「任務大廳」的可接取清單。
- npc_offer: True/False —— 是否能由隨機 NPC 委託彈窗提供（舊任務未設定時預設為 NPC 委託）。
- repeatable: True      —— 可重複（每日）任務，完成後當天進冷卻，隔天可再接。
- requires: quest_id    —— 前置任務，需先完成才會出現。
- reward_items: {id:qty}—— 除了 exp/金幣外的道具獎勵。

任務目標（quest_type）：
- kill / boss_kill：擊殺累計（事件型，見 combat._update_kill_quest_progress）。
- collect：背包持有指定物品數量（即時計算）。
- stat：玩家某項統計值（monsters_killed / total_deaths / money_spent…，即時計算）。
- reach_level / reach_area / tower_floor：抵達等級／區域／魔塔樓層（即時計算）。

不論從哪個來源接的任務，進度都能在任務大廳查看。
"""

import time
import random
from datetime import datetime

import discord

from trpg.i18n import t, tf

COOLDOWN_SECONDS = 90
OFFER_CHANCE = 0.15


def _real_player(view):
    """任務系統一律針對「永久角色」運作，絕不能碰地下城探索用的封印替身：
    RoguePlayerWrapper 在探索中會把 level/inventory/exp 等欄位轉址到臨時的
    dungeon_state（見 trpg/dungeon.py SEALED_FIELDS）。如果任務判定/發獎直接用
    view.player，玩家在地下城裡的等級門檻判斷會讀到固定的封印等級、任務獎勵的
    經驗值與道具也會被寫進探索結束就整份丟棄的臨時角色，變成任務完成了但獎勵
    憑空消失。這裡統一解包成真正的存檔角色。"""
    return getattr(view.player, "real_player", view.player)


async def process_quest_popups(view, interaction: discord.Interaction, allow_offer: bool = True):
    """allow_offer=False 讓呼叫端（view.global_callback）壓下「突發委託邀請」——完成獎勵
    彈窗永遠照常檢查，只有邀請新委託這個隨機打擾，限定在玩家真的做了冒險行動時才會出現。"""
    recompute_live_progress(view)

    popped = await _try_pop_completed_active_quest(view, interaction)
    if not popped:
        popped = await _try_pop_completed_hidden_quest(view, interaction)
    if not popped and allow_offer and not getattr(view, "in_battle", False):
        await _maybe_send_offer_popup(view, interaction)


def recompute_live_progress(view):
    """即時型任務進度不是靠事件累加，而是直接從玩家當前狀態算出來，每次都重算。

    這裡一律讀 real_player（見 _real_player），因為 collect/reach_level 用到的
    inventory/level 是「封印欄位」——地下城探索中 wrapper 會回傳臨時封印角色的值
    （等級固定 10、背包是本次探索撿到的臨時道具），拿來算永久任務進度會誤判：等級
    不足的玩家一進地下城就會被判定「已達到 Lv.10」，材料收集任務也會被地下城臨時
    撿到的同名道具灌水，跟玩家實際永久狀態完全對不上。"""
    player = _real_player(view)
    for quest_id, quest_data in player.active_quests.items():
        quest_info = view.cog.quests.get(quest_id)
        if not quest_info:
            continue
        qtype = quest_info.get("quest_type")
        if qtype == "collect":
            quest_data["progress"] = player.inventory.get(quest_info.get("target_item"), 0)
        elif qtype == "stat":
            quest_data["progress"] = player.stats.get(quest_info.get("target_stat"), 0)
        elif qtype == "reach_level":
            quest_data["progress"] = player.level
        elif qtype == "tower_floor":
            quest_data["progress"] = getattr(player, "tower_floor", 1)
        elif qtype == "reach_area":
            quest_data["progress"] = 1 if player.current_area == quest_info.get("target_area") else 0


# --- 任務來源 / 可接取判定 --------------------------------------------------

def _is_npc_offer(quest_info) -> bool:
    if quest_info.get("hidden"):
        return False
    if "npc_offer" in quest_info:
        return bool(quest_info["npc_offer"])
    return not quest_info.get("board", False)  # 舊任務：除非是大廳任務，否則維持 NPC 委託


def _is_board(quest_info) -> bool:
    return bool(quest_info.get("board")) and not quest_info.get("hidden")


def _quest_on_cooldown(player, quest_id, quest_info) -> bool:
    if quest_info.get("repeatable"):
        last = (getattr(player, "repeatable_cooldowns", {}) or {}).get(quest_id)
        return last == datetime.today().strftime("%Y-%m-%d")  # 每日：今天已完成才鎖
    return quest_id in player.completed_quests


def can_accept_quest(view, quest_id, quest_info) -> bool:
    player = _real_player(view)
    if quest_info.get("hidden"):
        return False
    if player.level < quest_info.get("req_level", 1):
        return False
    if quest_id in player.active_quests:
        return False
    if _quest_on_cooldown(player, quest_id, quest_info):
        return False
    requires = quest_info.get("requires")
    if requires and requires not in player.completed_quests:
        return False
    return True


def available_board_quests(view):
    """公會任務大廳可接取的委託清單 [(quest_id, quest_info), ...]。"""
    return [
        (qid, qinfo) for qid, qinfo in view.cog.quests.items()
        if _is_board(qinfo) and can_accept_quest(view, qid, qinfo)
    ]


def accept_quest(view, quest_id) -> bool:
    quest_info = view.cog.quests.get(quest_id)
    if not quest_info or not can_accept_quest(view, quest_id, quest_info):
        return False
    _real_player(view).active_quests[quest_id] = {"progress": 0}
    view.cog.save_players()
    return True


def quest_progress_text(view, quest_id, quest_info, lang) -> str:
    """任務大廳裡單一進行中任務的進度行。"""
    qdata = _real_player(view).active_quests.get(quest_id, {})
    progress = min(qdata.get("progress", 0), quest_info.get("target_count", 1))
    target = quest_info.get("target_count", 1)
    title = tf(quest_info, "title", lang)
    done = "✅" if progress >= target else "•"
    return f"{done} {title} ({progress}/{target})"


# --- 獎勵發放 ---------------------------------------------------------------

def _grant_quest_rewards(view, quest_info) -> str:
    """發放 exp / 金幣 / 道具，回傳道具獎勵的描述文字（沒有道具則為空字串）。"""
    player = _real_player(view)
    cog = view.cog
    lang = getattr(player, "language", "zh")
    player.add_exp(quest_info.get("reward_exp", 0), cog.items)
    cog.adjust_bank(view.user_id, quest_info.get("reward_money", 0))

    reward_items = quest_info.get("reward_items") or {}
    if not reward_items:
        return ""
    names = []
    for item_id, qty in reward_items.items():
        player.inventory[item_id] = player.inventory.get(item_id, 0) + qty
        nm = tf(cog.items.get(item_id, {}), "name", lang) or item_id
        names.append(f"{nm} x{qty}")
    sep = ", " if lang == "en" else "、"
    return t(lang, "quest.reward_items_suffix", "、道具：{items}", items=sep.join(names))


def _mark_completed(player, quest_id, quest_info):
    """標記任務完成：可重複任務記錄今日冷卻，一次性任務永久完成。"""
    if quest_id not in player.completed_quests:
        player.completed_quests.append(quest_id)
    if quest_info.get("repeatable"):
        if not isinstance(getattr(player, "repeatable_cooldowns", None), dict):
            player.repeatable_cooldowns = {}
        player.repeatable_cooldowns[quest_id] = datetime.today().strftime("%Y-%m-%d")


async def _send_reward_popup(view, interaction, title, quest_line, npc_name, raw_prompt,
                             target_name, target_count, reward_exp, reward_money, reward_items_text):
    lang = getattr(view.player, "language", "zh")
    prompt = (raw_prompt.replace("{count}", str(target_count))
                         .replace("{monster}", target_name)
                         .replace("{item}", target_name))
    ai_text = await view.cog.generate_npc_dialogue(prompt)
    tag = (t(lang, "quest.main_line_tag", "📖 主線任務") if quest_line == "main"
           else t(lang, "quest.side_line_tag", "📌 支線任務"))
    content = t(
        lang, "quest.reward_popup", "🎉 **任務達成！** {tag}\n**{title}**\n💬 {npc_name}：「{ai_text}」\n\n🎁 獲得 {reward_exp} EXP、{reward_money} 金幣{reward_items}！",
        tag=tag, title=title, npc_name=npc_name, ai_text=ai_text,
        reward_exp=reward_exp, reward_money=reward_money, reward_items=reward_items_text,
    )
    try:
        await interaction.followup.send(content, ephemeral=True)
    except Exception as e:
        print(f"任務彈出視窗發送失敗: {e}")


async def _try_pop_completed_active_quest(view, interaction) -> bool:
    player = _real_player(view)
    cog = view.cog
    for quest_id, quest_data in list(player.active_quests.items()):
        quest_info = cog.quests.get(quest_id)
        if not quest_info or quest_data["progress"] < quest_info.get("target_count", 1):
            continue

        if quest_info.get("quest_type") == "collect":
            if not player.remove_item(quest_info.get("target_item"), quest_info["target_count"]):
                continue  # 材料中途被用掉了，等湊夠再觸發

        reward_items_text = _grant_quest_rewards(view, quest_info)
        del player.active_quests[quest_id]
        _mark_completed(player, quest_id, quest_info)
        cog.save_players()

        lang = getattr(player, "language", "zh")
        target_name = quest_info.get("target_monster") or quest_info.get("target_item") or t(lang, "quest.default_target", "目標")
        await _send_reward_popup(
            view, interaction,
            title=tf(quest_info, "title", lang), quest_line=quest_info.get("quest_line", "side"),
            npc_name=tf(quest_info, "npc_name", lang) or "???",
            raw_prompt=tf(quest_info, "turn_in_prompt", lang) or t(lang, "quest.default_turn_in_prompt", "請用一句話稱讚玩家完成了委託。"),
            target_name=target_name, target_count=quest_info.get("target_count", 1),
            reward_exp=quest_info.get("reward_exp", 0), reward_money=quest_info.get("reward_money", 0),
            reward_items_text=reward_items_text,
        )
        return True
    return False


async def _try_pop_completed_hidden_quest(view, interaction) -> bool:
    player = _real_player(view)
    cog = view.cog
    progress_map = getattr(player, "hidden_quest_progress", None) or {}
    for quest_id, progress in list(progress_map.items()):
        quest_info = cog.quests.get(quest_id)
        if not quest_info or quest_id in player.completed_quests:
            del progress_map[quest_id]
            continue
        if progress < quest_info.get("target_count", 1):
            continue

        reward_items_text = _grant_quest_rewards(view, quest_info)
        _mark_completed(player, quest_id, quest_info)
        del progress_map[quest_id]
        cog.save_players()

        lang = getattr(player, "language", "zh")
        target_name = quest_info.get("target_monster") or quest_info.get("target_item") or t(lang, "quest.default_target", "目標")
        await _send_reward_popup(
            view, interaction,
            title=tf(quest_info, "title", lang), quest_line=quest_info.get("quest_line", "side"),
            npc_name=tf(quest_info, "npc_name", lang) or "???",
            raw_prompt=tf(quest_info, "turn_in_prompt", lang) or t(lang, "quest.default_turn_in_prompt_hidden", "請用一句話神祕地給予玩家獎勵。"),
            target_name=target_name, target_count=quest_info.get("target_count", 1),
            reward_exp=quest_info.get("reward_exp", 0), reward_money=quest_info.get("reward_money", 0),
            reward_items_text=reward_items_text,
        )
        return True
    return False


async def _maybe_send_offer_popup(view, interaction):
    """突發委託：直接自動接取，不再跳出「接受／拒絕」的限時視窗。之前那個視窗只有
    120 秒的 ephemeral 逾時，玩家沒剛好看到就等於平白錯過這個委託（下次能不能再
    抽到純看運氣）；直接接取不會有任何損失（委託本來就不強制，玩家永遠可以放著
    不管），也不會再讓突發委託憑空消失。"""
    player = _real_player(view)
    cog = view.cog
    now = time.time()
    if now - getattr(player, "last_quest_popup_ts", 0) < COOLDOWN_SECONDS:
        return
    if random.random() > OFFER_CHANCE:
        return

    candidates = [
        (q_id, q_info) for q_id, q_info in cog.quests.items()
        if _is_npc_offer(q_info) and can_accept_quest(view, q_id, q_info)
    ]
    if not candidates:
        return

    quest_id, quest_info = random.choice(candidates)
    player.last_quest_popup_ts = now
    accept_quest(view, quest_id)
    cog.save_players()

    lang = getattr(player, "language", "zh")
    target_name = quest_info.get("target_monster") or quest_info.get("target_item") or t(lang, "quest.default_target", "目標")
    raw_prompt = tf(quest_info, "accept_prompt", lang) or t(lang, "quest.default_accept_prompt", "請用一句話邀請玩家接取委託。")
    prompt = (raw_prompt.replace("{count}", str(quest_info.get("target_count", 1)))
                         .replace("{monster}", target_name)
                         .replace("{item}", target_name))
    ai_text = await cog.generate_npc_dialogue(prompt)

    quest_line = quest_info.get("quest_line", "side")
    tag = (t(lang, "quest.main_line_tag", "📖 主線任務") if quest_line == "main"
           else t(lang, "quest.side_line_tag", "📌 支線任務"))
    title = tf(quest_info, "title", lang)
    npc_name = tf(quest_info, "npc_name", lang) or "???"
    content = t(
        lang, "quest.offer_popup",
        "❗ **突發委託出現！** {tag}\n**{title}**\n💬 {npc_name}：「{ai_text}」\n\n"
        "✅ 已自動為你接下這個委託，可在任務大廳查看進度。",
        tag=tag, title=title, npc_name=npc_name, ai_text=ai_text,
    )
    try:
        await interaction.followup.send(content, ephemeral=True)
    except Exception as e:
        print(f"突發任務彈出視窗發送失敗: {e}")
