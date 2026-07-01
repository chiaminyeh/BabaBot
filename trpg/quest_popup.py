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


async def process_quest_popups(view, interaction: discord.Interaction):
    recompute_live_progress(view)

    popped = await _try_pop_completed_active_quest(view, interaction)
    if not popped:
        popped = await _try_pop_completed_hidden_quest(view, interaction)
    if not popped and not getattr(view, "in_battle", False):
        await _maybe_send_offer_popup(view, interaction)


def recompute_live_progress(view):
    """即時型任務進度不是靠事件累加，而是直接從玩家當前狀態算出來，每次都重算。"""
    player = view.player
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
    player = view.player
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
    view.player.active_quests[quest_id] = {"progress": 0}
    view.cog.save_players()
    return True


def quest_progress_text(view, quest_id, quest_info, lang) -> str:
    """任務大廳裡單一進行中任務的進度行。"""
    qdata = view.player.active_quests.get(quest_id, {})
    progress = min(qdata.get("progress", 0), quest_info.get("target_count", 1))
    target = quest_info.get("target_count", 1)
    title = tf(quest_info, "title", lang)
    done = "✅" if progress >= target else "•"
    return f"{done} {title} ({progress}/{target})"


# --- 獎勵發放 ---------------------------------------------------------------

def _grant_quest_rewards(view, quest_info) -> str:
    """發放 exp / 金幣 / 道具，回傳道具獎勵的描述文字（沒有道具則為空字串）。"""
    player = view.player
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
    player = view.player
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
    player = view.player
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
    player = view.player
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
        "❗ **突發委託出現！** {tag}\n**{title}**\n💬 {npc_name}：「{ai_text}」\n\n要接受這個委託嗎？",
        tag=tag, title=title, npc_name=npc_name, ai_text=ai_text,
    )
    try:
        await interaction.followup.send(content, view=QuestOfferView(view, quest_id), ephemeral=True)
    except Exception as e:
        print(f"突發任務彈出視窗發送失敗: {e}")


class QuestOfferView(discord.ui.View):
    def __init__(self, game_view, quest_id: str):
        super().__init__(timeout=120)
        self.game_view = game_view
        self.quest_id = quest_id
        lang = getattr(game_view.player, "language", "zh")
        self.accept.label = t(lang, "quest.accept_button", "接受")
        self.decline.label = t(lang, "quest.decline_button", "拒絕")

    @discord.ui.button(label="接受", style=discord.ButtonStyle.success, emoji="✅")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        lang = getattr(self.game_view.player, "language", "zh")
        accept_quest(self.game_view, self.quest_id)
        await interaction.response.edit_message(
            content=t(lang, "quest.accepted_msg", "✅ 已接受委託！進度會自動累積，達成時會再跳出視窗領取獎勵。"), view=None)
        self.stop()

    @discord.ui.button(label="拒絕", style=discord.ButtonStyle.secondary, emoji="❌")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        lang = getattr(self.game_view.player, "language", "zh")
        await interaction.response.edit_message(content=t(lang, "quest.declined_msg", "已拒絕這個委託。"), view=None)
        self.stop()
