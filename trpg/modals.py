"""Discord UI Modal dialogs (NPC question, bulk buy/sell)."""

import discord

from trpg.i18n import t


class ElderChiefModal(discord.ui.Modal, title="請教老村長"):
    def __init__(self, game_view):
        lang = getattr(game_view.player, "language", "zh")
        super().__init__(title=t(lang, "modal.elder_chief_title", "請教老村長"))
        self.game_view = game_view
        self.question = discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            max_length=200,
            placeholder=t(lang, "modal.elder_chief_question_placeholder", "例如：這個世界有什麼怪物？技能要怎麼學？"),
        )
        self.add_item(discord.ui.Label(text=t(lang, "modal.elder_chief_question_label", "你想問什麼？"), component=self.question))

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
        # 👇 村長的回答走獨立的 ephemeral 訊息，不寫進 game_view.log_message——
        # log_message 是主面板共用的那一格，玩家問完村長後只要再點任何一個按鈕
        # （移動、商店、攻擊...）就會把回答洗掉，等於話講完馬上被遺忘。獨立訊息
        # 不會被任何後續互動覆蓋，也不會佔用主面板版面。
        embed = discord.Embed(
            title=t(lang, "modal.elder_chief_embed_title", "🧓 村長的答覆"),
            description=t(lang, "modal.elder_chief_response", "「{response}」", response=ai_response),
            color=discord.Color.gold(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


class StatPointModal(discord.ui.Modal, title="投入屬性點"):
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
            placeholder=t(lang, "modal.qty_placeholder", "例如：5"),
            default="1",
            max_length=4,
        )
        self.add_item(discord.ui.Label(
            text=t(lang, "modal.stat_point_qty_label", "要投入幾點到 {stat}？（剩餘 {unspent} 點，輸入超過剩餘的數字會自動全押）", stat=stat_label, unspent=unspent),
            component=self.amount,
        ))

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

        await self.game_view.handle_stat_add(self.stat_key, amount=amount)
        try:
            await interaction.message.edit(embed=self.game_view.generate_embed(), view=self.game_view)
        except Exception:
            pass


class BuyItemModal(discord.ui.Modal, title="批量購買"):
    def __init__(self, game_view, item_id: str):
        lang = getattr(game_view.player, "language", "zh")
        super().__init__(title=t(lang, "modal.buy_item_title", "批量購買"))
        self.game_view = game_view
        self.item_id = item_id
        self.qty = discord.ui.TextInput(
            placeholder=t(lang, "modal.qty_placeholder", "例如：5"),
            default="1",
            max_length=3,
        )
        self.add_item(discord.ui.Label(text=t(lang, "modal.buy_qty_label", "請輸入購買數量"), component=self.qty))

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
    def __init__(self, game_view, item_id: str):
        lang = getattr(game_view.player, "language", "zh")
        super().__init__(title=t(lang, "modal.sell_item_title", "批量出售"))
        self.game_view = game_view
        self.item_id = item_id
        self.qty = discord.ui.TextInput(
            placeholder=t(lang, "modal.qty_placeholder", "例如：5"),
            default="1",
            max_length=3,
        )
        self.add_item(discord.ui.Label(text=t(lang, "modal.sell_qty_label", "請輸入出售數量"), component=self.qty))

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
