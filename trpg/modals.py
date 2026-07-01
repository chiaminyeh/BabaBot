"""Discord UI Modal dialogs (stat allocation, NPC question, bulk buy/sell)."""

import discord

from trpg.i18n import t


class StatAllocModal(discord.ui.Modal, title="批量分配屬性點"):
    atk = discord.ui.TextInput(label="攻擊 (ATK)", default="0", max_length=3)
    vit = discord.ui.TextInput(label="體力 (VIT) - 加血量與防禦", default="0", max_length=3)
    int_stat = discord.ui.TextInput(label="智力 (INT) - 加魔攻與魔力", default="0", max_length=3)
    spd = discord.ui.TextInput(label="速度 (SPD) - 加行動次數與閃避", default="0", max_length=3)
    res = discord.ui.TextInput(label="抗性 (RES)", default="0", max_length=3)

    def __init__(self, game_view):
        lang = getattr(game_view.player, "language", "zh")
        super().__init__(title=t(lang, "modal.stat_alloc_title", "批量分配屬性點"))
        self.game_view = game_view
        self.atk.label = t(lang, "modal.stat_alloc_atk_label", "攻擊 (ATK)")
        self.vit.label = t(lang, "modal.stat_alloc_vit_label", "體力 (VIT) - 加血量與防禦")
        self.int_stat.label = t(lang, "modal.stat_alloc_int_label", "智力 (INT) - 加魔攻與魔力")
        self.spd.label = t(lang, "modal.stat_alloc_spd_label", "速度 (SPD) - 加行動次數與閃避")
        self.res.label = t(lang, "modal.stat_alloc_res_label", "抗性 (RES)")

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        lang = getattr(self.game_view.player, "language", "zh")
        try:
            a = int(self.atk.value.strip() or "0")
            v = int(self.vit.value.strip() or "0")
            i = int(self.int_stat.value.strip() or "0")
            s = int(self.spd.value.strip() or "0")
            r = int(self.res.value.strip() or "0")
            if any(val < 0 for val in (a, v, i, s, r)):
                raise ValueError
        except ValueError:
            await interaction.followup.send(t(lang, "modal.stat_alloc_invalid", "❌ 點數無效，請輸入大於等於 0 的整數！"), ephemeral=True)
            return

        total_add = a + v + i + s + r
        from trpg.stats import get_unspent_points, recalc_player_stats, default_stat_alloc
        unspent = get_unspent_points(self.game_view.player)

        if total_add > unspent:
            await interaction.followup.send(t(lang, "modal.stat_alloc_insufficient", "❌ 點數不足！你剩餘 {unspent} 點，但嘗試分配 {total} 點。", unspent=unspent, total=total_add), ephemeral=True)
            return

        if not getattr(self.game_view.player, "stat_alloc", None):
            self.game_view.player.stat_alloc = default_stat_alloc()

        self.game_view.player.stat_alloc["atk"] += a
        self.game_view.player.stat_alloc["vit"] += v
        self.game_view.player.stat_alloc["int"] += i
        self.game_view.player.stat_alloc["spd"] += s
        self.game_view.player.stat_alloc["res"] += r

        recalc_player_stats(self.game_view.player, self.game_view.cog.items, heal_full=False)
        self.game_view.cog.save_players()

        self.game_view.log_message = t(lang, "modal.stat_alloc_success", "✅ 成功分配了 {total} 點屬性！", total=total_add)
        await self.game_view.handle_stat_alloc_menu()
        try:
            await interaction.message.edit(embed=self.game_view.generate_embed(), view=self.game_view)
        except Exception:
            pass

class ElderChiefModal(discord.ui.Modal, title="請教老村長"):
    question = discord.ui.TextInput(
        label="你想問什麼？",
        style=discord.TextStyle.paragraph,
        max_length=200,
        placeholder="例如：這個世界有什麼怪物？技能要怎麼學？",
    )

    def __init__(self, game_view):
        lang = getattr(game_view.player, "language", "zh")
        super().__init__(title=t(lang, "modal.elder_chief_title", "請教老村長"))
        self.game_view = game_view
        self.question.label = t(lang, "modal.elder_chief_question_label", "你想問什麼？")
        self.question.placeholder = t(lang, "modal.elder_chief_question_placeholder", "例如：這個世界有什麼怪物？技能要怎麼學？")

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        lang = getattr(self.game_view.player, "language", "zh")
        # 👇 先從真實遊戲資料撈出跟這個問題有關的事實，餵給老村長，避免他憑空幻想
        # 出遊戲裡根本不存在的道具/怪物/機制（見 trpg/npc_knowledge.py）。
        from trpg.npc_knowledge import build_grounding_context
        context = build_grounding_context(self.game_view.cog, self.game_view.player, self.question.value, lang)
        if lang == "en":
            prompt = (
                "You are the wise, kindly old village chief of the starting village in a fantasy RPG. "
                "Few know this, but in his youth he was actually a renowned hero who traveled the world "
                "on countless adventures before retiring to settle down here as chief — that's exactly why "
                "he knows so much about the monsters, items, and dungeons out there. He can let a hint of "
                "his adventuring past slip out occasionally (e.g. mentioning he's wielded a similar weapon "
                "or fought something like that before), but doesn't need to bring it up every single time. "
                "Answer the adventurer's question briefly and in character, in English.\n\n"
                f"{context}\n\n"
                f'The adventurer asks: "{self.question.value}" '
                "Answer in 60 words or fewer, and where relevant give a useful gameplay tip "
                "(exploration, the shop, skill scrolls, bosses, etc.). Respond in English only."
            )
        else:
            prompt = (
                "你是新手村的老村長，睿智慈祥，用簡短回答冒險者的問題。很少人知道，他年輕時其實是一位遊歷四方、"
                "身經百戰的英雄，退休後才回到這裡定居擔任村長——這正是為什麼他對世界上的怪物、道具、地下城如此"
                "瞭若指掌。他可以偶爾在回答中不經意流露出當年冒險的痕跡（例如提到自己也用過類似的武器、打過類似"
                "的怪物），但不用每次都刻意提起。\n\n"
                f"{context}\n\n"
                f"冒險者問：「{self.question.value}」"
                "請在 60 字以內回答，可以給新手有用的遊戲提示（探索、商店、技能卷軸、BOSS 等）。"
            )
        ai_response = await self.game_view.cog.generate_npc_dialogue(prompt)
        self.game_view.log_message = t(lang, "modal.elder_chief_response", "🧓 老村長緩緩開口：\n「{response}」", response=ai_response)
        try:
            await interaction.message.edit(embed=self.game_view.generate_embed(), view=self.game_view)
        except Exception as e:
            print(f"老村長回覆 UI 更新失敗: {e}")
        await interaction.followup.send(t(lang, "modal.elder_chief_done", "老村長已回答，請看冒險面板！"), ephemeral=True)


class BuyItemModal(discord.ui.Modal, title="批量購買"):
    qty = discord.ui.TextInput(
        label="請輸入購買數量",
        placeholder="例如：5",
        default="1",
        max_length=3,
    )

    def __init__(self, game_view, item_id: str):
        lang = getattr(game_view.player, "language", "zh")
        super().__init__(title=t(lang, "modal.buy_item_title", "批量購買"))
        self.game_view = game_view
        self.item_id = item_id
        self.qty.label = t(lang, "modal.buy_qty_label", "請輸入購買數量")
        self.qty.placeholder = t(lang, "modal.qty_placeholder", "例如：5")

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        lang = getattr(self.game_view.player, "language", "zh")
        try:
            amount = int(self.qty.value.strip())
            if amount <= 0: raise ValueError
        except ValueError:
            await interaction.followup.send(t(lang, "modal.qty_invalid", "❌ 數量無效，請輸入正整數！"), ephemeral=True)
            return

        await self.game_view.execute_buy(self.item_id, amount)
        try:
            await interaction.message.edit(embed=self.game_view.generate_embed(), view=self.game_view)
        except Exception:
            pass

class SellItemModal(discord.ui.Modal, title="批量出售"):
    qty = discord.ui.TextInput(
        label="請輸入出售數量",
        placeholder="例如：5",
        default="1",
        max_length=3,
    )

    def __init__(self, game_view, item_id: str):
        lang = getattr(game_view.player, "language", "zh")
        super().__init__(title=t(lang, "modal.sell_item_title", "批量出售"))
        self.game_view = game_view
        self.item_id = item_id
        self.qty.label = t(lang, "modal.sell_qty_label", "請輸入出售數量")
        self.qty.placeholder = t(lang, "modal.qty_placeholder", "例如：5")

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        lang = getattr(self.game_view.player, "language", "zh")
        try:
            amount = int(self.qty.value.strip())
            if amount <= 0: raise ValueError
        except ValueError:
            await interaction.followup.send(t(lang, "modal.qty_invalid", "❌ 數量無效，請輸入正整數！"), ephemeral=True)
            return

        await self.game_view.execute_sell(self.item_id, amount)
        try:
            await interaction.message.edit(embed=self.game_view.generate_embed(), view=self.game_view)
        except Exception:
            pass
