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
    # 第一場只教玩家「現在需要知道」的操作。技能、屬性克制與異常狀態等概念
    # 留到真正取得技能／遇到對應敵人時再呈現，避免第一次登入被六段說明淹沒。
    _TUTORIAL_TIPS = [
        ("tutorial.tip_stats",
         "📖 **第一步：攻擊**\n上方是你的 ❤️HP 與戰鬥能力。先點下方的「攻擊」，解決這隻迷路的史萊姆！"),
        ("tutorial.tip_action_bar",
         "📖 **第二步：看 AP**\n⚡ AP 是本回合可用的行動次數，攻擊會消耗 1 點；用完會自動結束回合。"
         "敵人旁的 ❗ 代表牠即將行動。再攻擊一次，把勝利和第一件武器帶回家！"),
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
            "👋 歡迎來到米酥村！\n要不要立刻進行一場約一分鐘的冒險？打贏會獲得你的第一件武器。",
        )
        self.add_action_button(label=t(lang, "tutorial.btn_yes", "⚔️ 立即開始第一戰"), style=discord.ButtonStyle.success, custom_id="btn_tutorial_yes")
        self.add_action_button(label=t(lang, "tutorial.btn_no", "稍後再自己探索"), style=discord.ButtonStyle.secondary, custom_id="btn_tutorial_no")

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
        """A short real encounter with normal rewards and a guaranteed starter weapon."""
        lang = self.player.language
        dummy = {
            "id": "tutorial_slime",
            "name": "迷路的史萊姆", "name_en": "Lost Slime",
            "max_hp": 20, "atk": 4, "def": 0, "spd": 6,
            "exp": 10, "money_min": 10, "money_max": 10,
            "drops": {"slime_jelly": 1, "wooden_sword": 1}, "ai": "none",
            "weakness": [], "resistance": [], "immunity": [],
            "is_tutorial": True,
        }
        self.in_tutorial_battle = True
        self.tutorial_step = 1  # 第 0 則提示現在就送出，之後每次行動送下一則
        self.start_combat([dummy])
        self.log_message = t(
            lang, "tutorial.battle_start",
            "⚔️ 【第一場冒險】一隻迷路的史萊姆擋住村口！擊敗牠，帶回你的第一件武器。",
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
                      "🎉 **第一場冒險完成！** 如果拿到了練習用木劍，下一步請點「裝備」把它穿上。"
                      "其他系統會在你真正遇到時再說明，不用一次全記住。"),
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
