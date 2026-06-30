"""突發任務／隱藏任務的彈出視窗系統 — 取代舊的任務大廳選單。

不再需要玩家跑去村莊的任務大廳手動接取/回報，改成：
1. 任務進度（擊殺/收集）累積到達標時，自動發獎並跳出一個獨立的通知視窗。
2. 沒有任務剛達成時，才依冷卻時間與機率隨機跳出一個新的委託邀請（可接受/拒絕）。

每次 global_callback 處理完一個按鈕點擊後都會呼叫一次 process_quest_popups()。
"""

import time
import random

import discord

from trpg.i18n import t, tf

COOLDOWN_SECONDS = 90
OFFER_CHANCE = 0.15


async def process_quest_popups(view, interaction: discord.Interaction):
    _recompute_live_progress(view)

    popped = await _try_pop_completed_active_quest(view, interaction)
    if not popped:
        popped = await _try_pop_completed_hidden_quest(view, interaction)
    if not popped and not getattr(view, "in_battle", False):
        await _maybe_send_offer_popup(view, interaction)


def _recompute_live_progress(view):
    """collect/stat 類型的任務進度不是靠事件累加，而是直接從玩家當前狀態算出來的，每次都重新算一次。"""
    player = view.player
    for quest_id, quest_data in player.active_quests.items():
        quest_info = view.cog.quests.get(quest_id)
        if not quest_info:
            continue
        if quest_info.get("quest_type") == "collect":
            quest_data["progress"] = player.inventory.get(quest_info.get("target_item"), 0)
        elif quest_info.get("quest_type") == "stat":
            quest_data["progress"] = player.stats.get(quest_info.get("target_stat"), 0)


async def _send_reward_popup(view, interaction, title, quest_line, npc_name, raw_prompt,
                              target_name, target_count, reward_exp, reward_money):
    lang = getattr(view.player, "language", "zh")
    prompt = (raw_prompt.replace("{count}", str(target_count))
                         .replace("{monster}", target_name)
                         .replace("{item}", target_name))
    ai_text = await view.cog.generate_npc_dialogue(prompt)
    tag = (t(lang, "quest.main_line_tag", "📖 主線任務") if quest_line == "main"
           else t(lang, "quest.side_line_tag", "📌 支線任務"))
    content = t(
        lang, "quest.reward_popup", "🎉 **任務達成！** {tag}\n**{title}**\n💬 {npc_name}：「{ai_text}」\n\n🎁 獲得 {reward_exp} EXP、{reward_money} 金幣！",
        tag=tag, title=title, npc_name=npc_name, ai_text=ai_text,
        reward_exp=reward_exp, reward_money=reward_money,
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

        player.add_exp(quest_info.get("reward_exp", 0), cog.items)
        cog.adjust_bank(view.user_id, quest_info.get("reward_money", 0))
        del player.active_quests[quest_id]
        player.completed_quests.append(quest_id)
        cog.save_players()

        lang = getattr(player, "language", "zh")
        target_name = quest_info.get("target_monster") or quest_info.get("target_item") or t(lang, "quest.default_target", "目標")
        await _send_reward_popup(
            view, interaction,
            title=tf(quest_info, "title", lang), quest_line=quest_info.get("quest_line", "side"),
            npc_name=tf(quest_info, "npc_name", lang) or "???",
            raw_prompt=quest_info.get("turn_in_prompt", t(lang, "quest.default_turn_in_prompt", "請用一句話稱讚玩家完成了委託。")),
            target_name=target_name, target_count=quest_info.get("target_count", 1),
            reward_exp=quest_info.get("reward_exp", 0), reward_money=quest_info.get("reward_money", 0),
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

        player.add_exp(quest_info.get("reward_exp", 0), cog.items)
        cog.adjust_bank(view.user_id, quest_info.get("reward_money", 0))
        player.completed_quests.append(quest_id)
        del progress_map[quest_id]
        cog.save_players()

        lang = getattr(player, "language", "zh")
        target_name = quest_info.get("target_monster") or quest_info.get("target_item") or t(lang, "quest.default_target", "目標")
        await _send_reward_popup(
            view, interaction,
            title=tf(quest_info, "title", lang), quest_line=quest_info.get("quest_line", "side"),
            npc_name=tf(quest_info, "npc_name", lang) or "???",
            raw_prompt=quest_info.get("turn_in_prompt", t(lang, "quest.default_turn_in_prompt_hidden", "請用一句話神祕地給予玩家獎勵。")),
            target_name=target_name, target_count=quest_info.get("target_count", 1),
            reward_exp=quest_info.get("reward_exp", 0), reward_money=quest_info.get("reward_money", 0),
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
        if not q_info.get("hidden")
        and player.level >= q_info.get("req_level", 1)
        and q_id not in player.active_quests
        and q_id not in player.completed_quests
    ]
    if not candidates:
        return

    quest_id, quest_info = random.choice(candidates)
    player.last_quest_popup_ts = now
    cog.save_players()

    lang = getattr(player, "language", "zh")
    target_name = quest_info.get("target_monster") or quest_info.get("target_item") or t(lang, "quest.default_target", "目標")
    raw_prompt = quest_info.get("accept_prompt", t(lang, "quest.default_accept_prompt", "請用一句話邀請玩家接取委託。"))
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
        player = self.game_view.player
        lang = getattr(player, "language", "zh")
        if self.quest_id not in player.active_quests and self.quest_id not in player.completed_quests:
            player.active_quests[self.quest_id] = {"progress": 0}
            self.game_view.cog.save_players()
        await interaction.response.edit_message(
            content=t(lang, "quest.accepted_msg", "✅ 已接受委託！進度會自動累積，達成時會再跳出視窗領取獎勵。"), view=None)
        self.stop()

    @discord.ui.button(label="拒絕", style=discord.ButtonStyle.secondary, emoji="❌")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self.game_view.player
        lang = getattr(player, "language", "zh")
        await interaction.response.edit_message(content=t(lang, "quest.declined_msg", "已拒絕這個委託。"), view=None)
        self.stop()
