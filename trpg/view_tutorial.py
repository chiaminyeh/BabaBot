"""TutorialMixin — first-login onboarding: language pick, tutorial opt-in, and a
scripted practice fight against a training dummy that explains the battle UI
progressively (one tip per action, delivered as ephemeral followups — the same
pattern quest_popup.py uses for its popups, just triggered by combat actions
instead of quest events).

Mixed into TRPGGameView (see trpg/view.py). Relies on attributes/methods defined
on the main view class (self.cog, self.player, self.clear_items, self.add_action_button,
self.start_combat, self.build_battle_menu, self.build_main_menu, self.in_battle, ...).
"""

import discord

from trpg.i18n import t


class TutorialMixin:
    # 教學戰的提示，依序在玩家每次戰鬥行動後彈出一則（ephemeral，不會洗掉主面板）。
    # 第一則在戰鬥剛開始、玩家還沒點任何按鈕前就先送出。
    _TUTORIAL_TIPS = [
        ("tutorial.tip_stats",
         "📖 **戰鬥教學 1/6 —— 你的狀態**\n面板上方會顯示你的 ❤️HP、💧MP、⚔️ATK、🛡️DEF、🚀SPD。"
         "HP 歸零就會戰敗，MP 用來施放技能。\n\n先點擊下方的「攻擊」按鈕，砍這隻木樁人偶一刀試試看！"),
        ("tutorial.tip_action_bar",
         "📖 **戰鬥教學 2/6 —— 行動條、速度與 AP**\n面板上的 ⚡ 行動條會隨著雙方的 SPD（速度）累積，"
         "累積滿了才會輪到你或敵人行動。速度越快，累積得越快，能行動的次數也就越多。\n\n"
         "⚡ **AP（行動點數）**：輪到你時會獲得 AP，每次攻擊或技能都會消耗 1 AP。"
         "AP 耗盡時回合會**自動結束**；也可以按「結束回合」提早收手，把剩餘時間讓給敵人。"),
        ("tutorial.tip_warning",
         "📖 **戰鬥教學 3/6 —— ❗ 警示標記**\n敵人名稱旁若出現 ❗，代表系統預測牠在你下次行動前"
         "還會攻擊幾次（❗越多代表威脅越大）。看到 ❗❗❗ 時，考慮先防禦或優先解決牠！"),
        ("tutorial.tip_element",
         "📖 **戰鬥教學 4/6 —— 屬性相剋**\n武器與技能可能帶有火/冰/雷等屬性。打中弱點會顯示 "
         "🌟【效果拔群】，打到抗性顯示 🛡️【效果微弱】，完全免疫則是 👻【完全無效】。"
         "找出敵人的弱點能讓輸出大幅提升！"),
        ("tutorial.tip_skill_item",
         "📖 **戰鬥教學 5/6 —— 技能、強化與道具**\n去🏫米酥學院用卷軸學習技能、「配置」到戰鬥欄後，"
         "就能在戰鬥中點擊「技能」按鈕施放（消耗 MP 或 HP）。「道具」按鈕能使用藥水回血回魔、"
         "或解除異常狀態。\n\n"
         "💡 **技能等級（最高 Lv.5）**：反覆使用技能可提升熟練度自動升級，或到米酥學院花費金幣直接強化！"
         "等級越高效果越強，但 MP/HP 消耗也會隨之增加。"),
        ("tutorial.tip_defend_flee",
         "📖 **戰鬥教學 6/6 —— 防禦／閃避／逃跑**\n「防禦」能減半這回合受到的傷害；"
         "「閃避」則有機率完全躲開攻擊（速度越快機率越高）；打不過就按「逃跑」，"
         "成功率同樣看雙方速度差。"),
    ]

    def build_language_select_menu(self):
        self.clear_items()
        self.log_message = (
            "🌍 歡迎來到冒險世界！\n請選擇你的顯示語言。\n\n"
            "🌍 Welcome, adventurer!\nPlease choose your display language."
        )
        self.add_action_button(label="🇹🇼 繁體中文", style=discord.ButtonStyle.primary, custom_id="btn_lang_zh")
        self.add_action_button(label="🇬🇧 English", style=discord.ButtonStyle.primary, custom_id="btn_lang_en")

    async def handle_lang_select(self, lang_code: str):
        real = getattr(self.player, "real_player", self.player)
        real.language = lang_code
        self.cog.save_players(player=real)
        self.build_tutorial_prompt_menu()

    def build_tutorial_prompt_menu(self):
        self.clear_items()
        lang = self.player.language
        self.log_message = t(
            lang, "tutorial.prompt",
            "👋 看起來這是你第一次來到這個世界！\n要不要花一分鐘體驗新手教學，認識一下戰鬥面板怎麼看？",
        )
        self.add_action_button(label=t(lang, "tutorial.btn_yes", "✅ 好，教我！"), style=discord.ButtonStyle.success, custom_id="btn_tutorial_yes")
        self.add_action_button(label=t(lang, "tutorial.btn_no", "❌ 不用，我要自己冒險"), style=discord.ButtonStyle.secondary, custom_id="btn_tutorial_no")

    async def handle_tutorial_choice(self, interaction: discord.Interaction, want_tutorial: bool):
        real = getattr(self.player, "real_player", self.player)
        lang = self.player.language

        if not want_tutorial:
            real.onboarding_done = True
            self.cog.save_players(player=real)
            self.log_message = t(
                lang, "tutorial.skipped",
                "好的！隨時可以到村莊裡找村長聊聊，他知道的可不少。祝你冒險順利！",
            )
            self.build_main_menu()
            return

        await self.start_tutorial_battle(interaction)

    async def start_tutorial_battle(self, interaction: discord.Interaction):
        """組出教學戰的木樁人偶：血量足以撐完 6 則提示、攻擊力低到不構成真實威脅，
        速度略低於新手初始速度，讓 ❗ 警示還是有機會自然出現。"""
        lang = self.player.language
        dummy = {
            "id": "training_dummy",
            "name": "木樁人偶", "name_en": "Training Dummy",
            "max_hp": 70, "atk": 4, "def": 0, "spd": 8,
            "exp": 20, "money_min": 10, "money_max": 10,
            "drops": {}, "ai": "none",
            "weakness": [], "resistance": [], "immunity": [],
            "is_tutorial": True,
        }
        self.in_tutorial_battle = True
        self.tutorial_step = 1  # 第 0 則提示現在就送出，之後每次行動送下一則
        self.start_combat([dummy])
        self.log_message = t(
            lang, "tutorial.battle_start",
            "⚔️ 【教學戰】訓練場的木樁人偶站到了你面前，準備好體驗你的第一場戰鬥了嗎？",
        )
        self.build_battle_menu()
        try:
            await interaction.followup.send(t(lang, *self._TUTORIAL_TIPS[0]), ephemeral=True)
        except Exception as e:
            print(f"教學提示發送失敗: {e}")

    async def _maybe_send_tutorial_tip(self, interaction: discord.Interaction):
        """在教學戰中的每次戰鬥動作後呼叫：戰鬥還在繼續就送下一則提示，
        戰鬥已經結束（打贏或逃跑，兩種情況都算「教學跑完了」）就送收尾訊息並解鎖正式開始遊戲。"""
        if not getattr(self, "in_tutorial_battle", False):
            return
        lang = self.player.language

        if not self.in_battle:
            real = getattr(self.player, "real_player", self.player)
            real.onboarding_done = True
            self.cog.save_players(player=real)
            self.in_tutorial_battle = False
            try:
                await interaction.followup.send(
                    t(lang, "tutorial.complete",
                      "🎉 **教學結束！** 你已經掌握戰鬥的基本操作了。記得——🏫米酥學院學技能並升級、"
                      "🛒商店買裝備、🧓村長什麼都能問。祝你在這個世界闖出一片天！"),
                    ephemeral=True,
                )
            except Exception as e:
                print(f"教學收尾彈窗發送失敗: {e}")
            return

        if self.tutorial_step < len(self._TUTORIAL_TIPS):
            tip = self._TUTORIAL_TIPS[self.tutorial_step]
            self.tutorial_step += 1
            try:
                await interaction.followup.send(t(lang, *tip), ephemeral=True)
            except Exception as e:
                print(f"教學提示發送失敗: {e}")
