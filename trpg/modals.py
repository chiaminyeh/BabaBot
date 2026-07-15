"""Discord UI Modal dialogs (NPC question, bulk buy/sell)."""

import discord
import logging

from trpg.i18n import t
from trpg.view_shared import BABA_EMOJI_TEXT, baba_emoji_text


logger = logging.getLogger(__name__)


def log_modal_failure(modal, interaction: discord.Interaction, stage: str) -> None:
    logger.exception(
        "INTERACTION_FAILURE component=trpg_modal stage=%s modal=%s user_id=%s guild_id=%s channel_id=%s",
        stage,
        type(modal).__name__,
        getattr(getattr(interaction, "user", None), "id", "unknown"),
        getattr(interaction, "guild_id", None) or "dm",
        getattr(interaction, "channel_id", None) or "unknown",
    )


class ElderChiefModal(discord.ui.Modal):
    def __init__(self, game_view):
        lang = getattr(game_view.player, "language", "zh")
        super().__init__(title=t(lang, "modal.elder_chief_title", "詢問 Baba"))
        self.game_view = game_view
        self.question = discord.ui.TextInput(
            label=t(lang, "modal.elder_chief_question_label", "你想問什麼？"),
            style=discord.TextStyle.paragraph,
            max_length=200,
            placeholder=t(lang, "modal.elder_chief_question_placeholder", "例如：這個世界有什麼怪物？技能要怎麼學？"),
        )
        self.add_item(self.question)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        lang = getattr(self.game_view.player, "language", "zh")
        # 👇 先從真實遊戲資料撈出跟這個問題有關的事實，餵給小精靈，避免她憑空幻想
        # 出遊戲裡根本不存在的道具/怪物/機制（見 trpg/npc_knowledge.py）。
        from trpg.npc_knowledge import build_grounding_context
        context = build_grounding_context(self.game_view.cog, self.game_view.player, self.question.value, lang)
        if lang == "en":
            prompt = (
                "You are Baba, a helpful guide sent to MISO town by the Creator to assist new adventurers. "
                "You are energetic, friendly, and highly knowledgeable about the monsters, items, and dungeons because "
                "you were created by the Creator of this world. Answer the adventurer's question briefly and in character "
                f"as Baba, using a friendly tone. You may occasionally use Baba's custom emoji {BABA_EMOJI_TEXT}. "
                f"Here is some world knowledge:\n{context}\n\n"
                f'The adventurer asks: "{self.question.value}" '
                "Answer in 60 words or fewer, and where relevant give a useful gameplay tip. Respond in English only."
            )
        else:
            prompt = (
                "你是 Baba，是由創世神派來米酥村（MISO town）引導新冒險者的嚮導。你個性活潑、親切、熱心助人，"
                "因為是創世神創造的，所以對這個世界的所有怪物、道具、技能與地下城瞭若指掌。請用簡短、親切且帶有魔法感的方式"
                f"回答冒險者的問題（偶爾可以使用 Baba 專屬表情 {BABA_EMOJI_TEXT}）。\n\n"
                f"背景設定與世界知識如下：\n{context}\n\n"
                f"冒險者問：「{self.question.value}」"
                "請在 60 字以內回答，可以給新手有用的遊戲提示（探索、商店、技能卷軸、BOSS 等）。"
            )
        ai_response = await self.game_view.cog.generate_npc_dialogue(prompt)
        # Baba 的回答走獨立的 ephemeral 訊息，不寫進 game_view.log_message。
        embed = discord.Embed(
            title=t(lang, "modal.elder_chief_embed_title", "{emoji} Baba 的答覆", emoji=baba_emoji_text(self.game_view.cog.bot)),
            description=t(lang, "modal.elder_chief_response", "「{response}」", response=ai_response),
            color=discord.Color.gold(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


class StatPointModal(discord.ui.Modal):
    """單一屬性的手動輸入分配——取代舊版「一次跳出 5 個數字欄位、一口氣分配全部
    屬性」的批量彈窗。這裡改成每個屬性各自一顆按鈕、各開一個只有一個欄位的彈窗，
    輸入的數字如果超過剩餘點數會自動封頂到剩餘點數（等於當初「All-in」按鈕的
    效果——打一個很大的數字進去就等於全押，不用再額外維護一顆 All-in 按鈕）。"""

    def __init__(self, game_view, stat_key: str, stat_label: str):
        lang = getattr(game_view.player, "language", "zh")
        super().__init__(title=t(lang, "modal.stat_point_title", "投入屬性點：{stat}", stat=stat_label))
        self.game_view = game_view
        self.stat_key = stat_key
        from trpg.stats import get_unspent_points
        unspent = get_unspent_points(game_view.player)
        self.amount = discord.ui.TextInput(
            label=t(lang, "modal.stat_point_qty_label", "要投入幾點到 {stat}？", stat=stat_label),
            placeholder=t(lang, "modal.stat_point_qty_desc", "剩餘 {unspent} 點，超出自動全押", unspent=unspent)[:100],
            default="1",
            max_length=4,
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        lang = getattr(self.game_view.player, "language", "zh")
        try:
            amount = int(self.amount.value.strip())
            if amount <= 0:
                raise ValueError
        except ValueError:
            await interaction.followup.send(t(lang, "modal.qty_invalid", "❌ 數量無效，請輸入正整數！"), ephemeral=True)
            return

        async with self.game_view.mutation_lock:
            await self.game_view.handle_stat_add(self.stat_key, amount=amount)
            try:
                await self.game_view.message.edit(embed=self.game_view.generate_embed(), view=self.game_view)
            except Exception:
                log_modal_failure(self, interaction, "stat_point_message_edit")


class BulkStatAllocModal(discord.ui.Modal):
    STAT_FIELDS = (
        ("knight", "Warrior / 戰士"),
        ("rogue", "Rogue / 盜賊"),
        ("mage", "Mage / 法師"),
        ("warlock", "Warlock / 術士"),
    )

    def __init__(self, game_view):
        lang = getattr(game_view.player, "language", "zh")
        super().__init__(title=t(lang, "modal.bulk_stat_title", "一次分配屬性點"))
        self.game_view = game_view
        self.inputs = {}
        from trpg.stats import get_unspent_points, stat_display_name
        unspent = get_unspent_points(game_view.player)
        for key, label in self.STAT_FIELDS:
            box = discord.ui.TextInput(
                label=f"{stat_display_name(key, lang)} ({t(lang, 'stats.alloc_line3', '剩餘點數 {unspent}', unspent=unspent)})",
                placeholder="0",
                default="0",
                max_length=4,
            )
            self.inputs[key] = box
            self.add_item(box)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        lang = getattr(self.game_view.player, "language", "zh")
        values = {}
        try:
            for key, box in self.inputs.items():
                raw = str(box.value).strip() or "0"
                amount = int(raw)
                if amount < 0:
                    raise ValueError
                values[key] = amount
        except ValueError:
            await interaction.followup.send(t(lang, "modal.qty_invalid", "❌ 數量無效，請輸入正整數。"), ephemeral=True)
            return

        total = sum(values.values())
        if total <= 0:
            await interaction.followup.send(t(lang, "modal.bulk_stat_empty", "❌ 至少要分配 1 點。"), ephemeral=True)
            return

        from trpg.stats import get_unspent_points, recalc_player_stats, default_stat_alloc, stat_display_name
        unspent = get_unspent_points(self.game_view.player)
        if total > unspent:
            await interaction.followup.send(
                t(lang, "modal.bulk_stat_over", "❌ 你只剩 {unspent} 點，這次輸入了 {total} 點。", unspent=unspent, total=total),
                ephemeral=True,
            )
            return

        async with self.game_view.mutation_lock:
            if not getattr(self.game_view.player, "stat_alloc", None):
                self.game_view.player.stat_alloc = default_stat_alloc()
            # Enforce 99 cap check
            for key, amount in values.items():
                current = self.game_view.player.stat_alloc.get(key, 0)
                if current + amount > 99:
                    await interaction.followup.send(
                        t(lang, "modal.bulk_stat_exceed_cap", "❌ 屬性點數上限為 99 點（{stat} 目前為 {current} 點）。", stat=stat_display_name(key, lang), current=current) if lang == "zh" else f"❌ Max attribute cap is 99 points ({stat_display_name(key, lang)} is currently {current}).",
                        ephemeral=True,
                    )
                    return

            for key, amount in values.items():
                self.game_view.player.stat_alloc[key] = self.game_view.player.stat_alloc.get(key, 0) + amount
            recalc_player_stats(self.game_view.player, self.game_view.cog.items, heal_full=False)
            skill_notice = self.game_view.sync_qualified_skill_notice()
            self.game_view.cog.save_players(player=self.game_view.player)
            await self.game_view.handle_stat_alloc_menu(
                t(lang, "modal.bulk_stat_done", "✅ 已分配 {total} 點屬性。", total=total) + skill_notice
            )
            try:
                await self.game_view.message.edit(embed=self.game_view.generate_embed(), view=self.game_view)
            except Exception:
                log_modal_failure(self, interaction, "bulk_stat_message_edit")


class BuyItemModal(discord.ui.Modal):
    def __init__(self, game_view, item_id: str):
        lang = getattr(game_view.player, "language", "zh")
        super().__init__(title=t(lang, "modal.buy_item_title", "批量購買"))
        self.game_view = game_view
        self.item_id = item_id
        self.qty = discord.ui.TextInput(
            label=t(lang, "modal.buy_qty_label", "請輸入購買數量"),
            placeholder=t(lang, "modal.qty_placeholder", "例如：5"),
            default="1",
            max_length=3,
        )
        self.add_item(self.qty)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        lang = getattr(self.game_view.player, "language", "zh")
        try:
            amount = int(self.qty.value.strip())
            if amount <= 0: raise ValueError
        except ValueError:
            await interaction.followup.send(t(lang, "modal.qty_invalid", "❌ 數量無效，請輸入正整數！"), ephemeral=True)
            return

        async with self.game_view.mutation_lock:
            await self.game_view.execute_buy(self.item_id, amount)
            try:
                await self.game_view.message.edit(embed=self.game_view.generate_embed(), view=self.game_view)
            except Exception:
                log_modal_failure(self, interaction, "buy_message_edit")


class SellItemModal(discord.ui.Modal):
    def __init__(self, game_view, item_id: str):
        lang = getattr(game_view.player, "language", "zh")
        super().__init__(title=t(lang, "modal.sell_item_title", "批量出售"))
        self.game_view = game_view
        self.item_id = item_id
        self.qty = discord.ui.TextInput(
            label=t(lang, "modal.sell_qty_label", "請輸入出售數量"),
            placeholder=t(lang, "modal.qty_placeholder", "例如：5"),
            default="1",
            max_length=3,
        )
        self.add_item(self.qty)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        lang = getattr(self.game_view.player, "language", "zh")
        try:
            amount = int(self.qty.value.strip())
            if amount <= 0: raise ValueError
        except ValueError:
            await interaction.followup.send(t(lang, "modal.qty_invalid", "❌ 數量無效，請輸入正整數！"), ephemeral=True)
            return

        async with self.game_view.mutation_lock:
            await self.game_view.execute_sell(self.item_id, amount)
            try:
                await self.game_view.message.edit(embed=self.game_view.generate_embed(), view=self.game_view)
            except Exception:
                log_modal_failure(self, interaction, "sell_message_edit")
