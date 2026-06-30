"""TRPGGameView — the button-driven adventure panel and all menu/combat handlers."""

import discord
import random
from datetime import datetime

from trpg.i18n import t, tf
from trpg.combat import TRPGCombat, exp_to_next_level, get_sell_price, get_player_atk, get_player_def, get_player_magic
from trpg.status import format_status_list, activate_jester_immunity, clear_all_status, cure_by_item, get_daily_jester_immunity
from trpg.monster_pool import load_monster_pool, pick_random_monster, instantiate_monster
from trpg.quest_popup import process_quest_popups
from trpg.stats import default_stat_alloc, migrate_player_stats, recalc_player_stats, get_unspent_points, format_stat_alloc_summary, get_potion_heal_target, STAT_KEYS
from trpg.player import RoguePlayerWrapper
from trpg.modals import StatAllocModal, ElderChiefModal, BuyItemModal, SellItemModal
from trpg.recipes import CRAFTING_RECIPES, UPGRADE_COSTS


class TRPGGameView(discord.ui.View):
    def __init__(self, cog, user_id):
        super().__init__(timeout=600)  # 10分鐘不操作才超時
        self.cog = cog
        self.user_id = str(user_id)
        self.player = RoguePlayerWrapper(cog.get_player(user_id))
        self.message = None
        
        # 戰鬥暫存狀態：最多 3 格怪物欄位（前排為第一個還活著的格子）
        self.in_battle = False
        self.monster_slots = []  # [{"monster": {...}, "hp": int, "av": int, "status": {}}, ...]
        self.combat = TRPGCombat(self)

        self.log_message = t(self.player.language, "menu.welcome", "歡迎來到冒險世界！請使用下方按鈕進行探索。")
        self.inventory_page = 0
        self.current_menu_state = "main"
        self.viewing_leaderboard = False
        self.build_main_menu()

    # --- 多怪物欄位（最多 3 格）：前排永遠是還活著的第一格 ---

    def _front_slot(self):
        return next((s for s in self.monster_slots if s["hp"] > 0), None)

    @property
    def active_monster(self):
        front = self._front_slot()
        return front["monster"] if front else None

    @property
    def active_monster_id(self):
        front = self._front_slot()
        return front["monster"].get("id") if front else None

    @property
    def monster_hp(self):
        front = self._front_slot()
        return front["hp"] if front else 0

    @monster_hp.setter
    def monster_hp(self, value):
        front = self._front_slot()
        if front:
            front["hp"] = value

    def start_combat(self, monster_defs: list):
        """所有戰鬥的單一入口：最多吃 3 隻怪物，組成 monster_slots。絕大多數戰鬥仍只傳 1 隻怪物。"""
        self.monster_slots = [
            {"monster": dict(m), "hp": m["max_hp"], "av": 0, "status": {}}
            for m in monster_defs[:3]
        ]
        self.in_battle = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.user_id:
            lang = getattr(self.player, "language", "zh")
            await interaction.response.send_message(t(lang, "menu.not_your_panel", "這不是你的冒險面板，請自己輸入 `/trpg` 開一盤！"), ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        # 儲存玩家狀態
        self.cog.save_players()
        if self.message:
            try:
                for child in self.children:
                    if hasattr(child, "disabled"):
                        child.disabled = True

                lang = getattr(self.player, "language", "zh")
                embed = self.generate_embed()
                embed.description = f"```\n{t(lang, 'menu.timeout_notice', '⌛ 此冒險面板已因超時（10分鐘未操作）而關閉並自動存檔。\n請重新輸入 `/trpg` 來繼續冒險！')}\n```"
                await self.message.edit(embed=embed, view=self)
            except Exception:
                pass

    # 🛠️ 修復核心：自訂一個按鈕產生器，確實綁定 callback！
    def add_action_button(self, label, style, custom_id, row=None, emoji=None):
        btn = discord.ui.Button(label=label, style=style, custom_id=custom_id, row=row, emoji=emoji)
        
        # 捕捉這個按鈕專屬的 custom_id 並轉交給全域處理函數
        async def callback(interaction: discord.Interaction):
            await self.global_callback(interaction, custom_id)
            
        btn.callback = callback  # 這行就是上次漏掉的靈魂
        self.add_item(btn)

    # 負責接收所有按鈕點擊的總管
    # --- Button routing -----------------------------------------------------
    # custom_id -> handler spec. To add a button: add a row here + an
    # add_action_button() call. Spec keys:
    #   m: method name | await: False for sync builders (default True)
    #   i: pass `interaction` as first arg | k: extra kwargs | args: fixed
    #   positional args | guard: require in_battle (else silently ignore)
    _EXACT_ROUTES = {
        "btn_explore": {"m": "handle_explore"},
        "btn_move_menu": {"m": "handle_move_menu"},
        "btn_status": {"m": "handle_status", "i": True},
        "b_sta": {"m": "handle_status", "i": True},
        "btn_leaderboard": {"m": "handle_leaderboard"},
        "btn_combat_history": {"m": "handle_combat_history", "i": True},
        "btn_rest": {"m": "handle_rest"},
        "btn_shop_menu": {"m": "handle_shop_menu"},
        "btn_shop_refresh": {"m": "handle_shop_refresh"},
        "btn_shop_sell": {"m": "handle_sell_menu"},
        "btn_equip_menu": {"m": "handle_equip_menu"},
        "btn_dung_explore": {"m": "handle_dung_explore"},
        "btn_dung_rest": {"m": "handle_dung_rest"},
        "btn_dung_upgrade": {"m": "handle_dung_upgrade"},
        "btn_dung_next": {"m": "handle_dung_next"},
        "btn_back_dungeon": {"m": "build_dungeon_menu", "await": False},
        "btn_boss_explore": {"m": "handle_boss_explore"},
        "btn_skill_learn": {"m": "handle_learn_skill_menu"},
        "btn_stat_alloc": {"m": "handle_stat_alloc_menu"},
        "btn_stat_reset": {"m": "handle_stat_reset"},
        "btn_artisan_menu": {"m": "build_artisan_menu", "await": False},
        "btn_guild_menu": {"m": "build_guild_menu", "await": False},
        "btn_church_menu": {"m": "build_church_menu", "await": False},
        "btn_skill_equip": {"m": "handle_skill_equip_menu"},
        "btn_tower_safe_room": {"m": "handle_tower_safe_room", "k": {"revisit": True}},
        "btn_tower_merchant": {"m": "handle_tower_merchant"},
        "btn_tower_next": {"m": "handle_tower_explore"},
        "btn_dung_flee": {"m": "handle_dungeon_flee"},
        "cave_dir_forward": {"m": "handle_cave_move", "args": ["forward"]},
        "cave_dir_left": {"m": "handle_cave_move", "args": ["left"]},
        "cave_dir_right": {"m": "handle_cave_move", "args": ["right"]},
        "cave_dir_back": {"m": "handle_cave_move", "args": ["back"]},
        "btn_prestige_menu": {"m": "handle_prestige_menu"},
        "btn_prestige_confirm": {"m": "handle_prestige_confirm"},
        "btn_craft_menu": {"m": "handle_craft_menu"},
        "btn_blacksmith_menu": {"m": "handle_blacksmith_menu"},
        "btn_upgrade_weapon": {"m": "handle_upgrade_execute", "k": {"is_weapon": True}},
        "btn_upgrade_armor": {"m": "handle_upgrade_execute", "k": {"is_weapon": False}},
        "b_atk": {"m": "handle_battle_attack", "guard": True},
        "b_ski": {"m": "handle_skill_menu"},
        "b_itm": {"m": "handle_item_menu"},
        "b_fle": {"m": "handle_battle_flee", "guard": True},
        "btn_back_battle": {"m": "build_battle_menu", "await": False},
    }

    # Prefix routes — checked in order, MOST SPECIFIC FIRST. `arg` controls what
    # gets passed: "suffix" = custom_id after the prefix, "full" = whole
    # custom_id, "int_tail" = int of the last underscore segment.
    _PREFIX_ROUTES = [
        ("move_to_", {"m": "handle_move_execute", "arg": "full"}),
        ("dung_enter_", {"m": "handle_dung_enter", "arg": "int_tail"}),
        ("equip_skill_", {"m": "handle_skill_equip_action", "arg": "suffix", "k": {"equip": True}}),
        ("unequip_skill_", {"m": "handle_skill_equip_action", "arg": "suffix", "k": {"equip": False}}),
        ("equip_", {"m": "handle_equip_action", "arg": "suffix", "k": {"equip": True}}),
        ("unequip_", {"m": "handle_equip_action", "arg": "suffix", "k": {"equip": False}}),
        ("acc_", {"m": "handle_accessory_action", "arg": "suffix", "k": {"equip": True}}),
        ("unacc_", {"m": "handle_accessory_action", "arg": "suffix", "k": {"equip": False}}),
        ("learn_", {"m": "handle_learn_skill", "arg": "suffix"}),
        ("stat_add_all_", {"m": "handle_stat_add", "arg": "suffix", "k": {"all_in": True}}),
        ("stat_add_", {"m": "handle_stat_add", "arg": "suffix"}),
        ("craft_", {"m": "handle_craft_execute", "arg": "suffix"}),
        ("skill_", {"m": "handle_use_skill", "arg": "suffix"}),
        ("use_item_", {"m": "handle_use_item", "arg": "full"}),
    ]

    async def _run_route(self, spec, interaction, custom_id, prefix=None) -> bool:
        """Execute a matched route spec. Returns False if a battle guard blocked it."""
        if spec.get("guard") and not getattr(self, "in_battle", False):
            return False
        method = getattr(self, spec["m"])
        call_args = []
        if spec.get("i"):
            call_args.append(interaction)
        argmode = spec.get("arg")
        if argmode == "full":
            call_args.append(custom_id)
        elif argmode == "suffix":
            call_args.append(custom_id[len(prefix):])
        elif argmode == "int_tail":
            call_args.append(int(custom_id.split("_")[-1]))
        call_args.extend(spec.get("args", []))
        result = method(*call_args, **spec.get("k", {}))
        if spec.get("await", True):
            await result
        return True

    # 負責接收所有按鈕點擊的總管
    async def global_callback(self, interaction: discord.Interaction, custom_id: str):
        # Modal dialogs MUST be sent before defer(), and end the callback early.
        if custom_id == "btn_ask_chief":
            return await interaction.response.send_modal(ElderChiefModal(self))
        if custom_id.startswith("buy_"):
            return await interaction.response.send_modal(BuyItemModal(self, custom_id[len("buy_"):]))
        if custom_id.startswith("sell_"):
            return await interaction.response.send_modal(SellItemModal(self, custom_id[len("sell_"):]))
        if custom_id == "btn_stat_bulk":
            return await interaction.response.send_modal(StatAllocModal(self))

        await interaction.response.defer()

        # Routes with bespoke logic that doesn't fit the declarative table.
        if custom_id == "btn_back_main":
            self.log_message = t(self.player.language, "menu.back_to_main", "回到了主選單。")
            self.build_main_menu()
        elif custom_id == "btn_prev_page":
            self.inventory_page = max(0, getattr(self, "inventory_page", 0) - 1)
            await self.re_render_current_menu()
        elif custom_id == "btn_next_page":
            self.inventory_page = getattr(self, "inventory_page", 0) + 1
            await self.re_render_current_menu()
        elif custom_id in ("b_def", "b_dod"):
            if not getattr(self, "in_battle", False):
                return
            self.log_message = self.combat.defend() if custom_id == "b_def" else self.combat.dodge()
            self.build_battle_menu()
        else:
            spec = self._EXACT_ROUTES.get(custom_id)
            prefix = None
            if spec is None:
                for p, s in self._PREFIX_ROUTES:
                    if custom_id.startswith(p):
                        spec, prefix = s, p
                        break
            if spec is not None:
                # A blocked battle guard ends the callback without redrawing.
                if not await self._run_route(spec, interaction, custom_id, prefix):
                    return

        # 任務彈出視窗（達成獎勵 / 突發委託邀請）
        try:
            await process_quest_popups(self, interaction)
        except Exception as e:
            print(f"任務彈出視窗處理失敗: {e}")

        # 更新畫面
        try:
            await interaction.message.edit(embed=self.generate_embed(), view=self)
        except Exception as e:
            print(f"UI更新失敗: {e}")

    # --- UI 構建分流 (全部改用 add_action_button) ---

    def build_main_menu(self):
        self.clear_items()
        self.in_battle = False
        self.monster_slots = []
        self.viewing_leaderboard = False
        
        # Route to dungeon if in dungeon
        if self.player.current_area == "area_dungeon":
            self.build_dungeon_menu()
            return

        if self.player.current_area == "area_legend_cave":
            self.build_legend_cave_menu()
            return

        lang = self.player.language
        if "village" in self.player.current_area:
            self.add_action_button(label=t(lang, "menu.btn_move", "移動"), style=discord.ButtonStyle.secondary, custom_id="btn_move_menu", row=0, emoji="🗺️")
            self.add_action_button(label=t(lang, "menu.btn_status", "狀態"), style=discord.ButtonStyle.success, custom_id="btn_status", row=0, emoji="📜")
            self.add_action_button(label=t(lang, "menu.btn_equip", "裝備"), style=discord.ButtonStyle.secondary, custom_id="btn_equip_menu", row=0, emoji="🛡️")
            self.add_action_button(label=t(lang, "shop.btn_shop", "商店"), style=discord.ButtonStyle.primary, custom_id="btn_shop_menu", row=0, emoji="🛒")
            self.add_action_button(label=t(lang, "menu.btn_inn", "旅館"), style=discord.ButtonStyle.secondary, custom_id="btn_rest", row=1, emoji="💤")
            self.add_action_button(label=t(lang, "menu.btn_blacksmith", "鐵匠"), style=discord.ButtonStyle.primary, custom_id="btn_artisan_menu", row=1, emoji="⚒️")
            self.add_action_button(label=t(lang, "menu.btn_guild", "公會"), style=discord.ButtonStyle.primary, custom_id="btn_guild_menu", row=1, emoji="🏛️")
            self.add_action_button(label=t(lang, "menu.btn_village_chief", "村長"), style=discord.ButtonStyle.secondary, custom_id="btn_ask_chief", row=2, emoji="🧓")
            self.add_action_button(label=t(lang, "menu.btn_church", "教堂"), style=discord.ButtonStyle.success, custom_id="btn_church_menu", row=2, emoji="⛪")
        else:
            # 🛠️ 新增：野外區域限定的每日 BOSS 按鈕
            self.add_action_button(label=t(lang, "menu.btn_explore", "探索"), style=discord.ButtonStyle.primary, custom_id="btn_explore", row=0, emoji="⚔️")
            self.add_action_button(label=t(lang, "menu.btn_move", "移動"), style=discord.ButtonStyle.secondary, custom_id="btn_move_menu", row=0, emoji="🗺️")
            self.add_action_button(label=t(lang, "menu.btn_status", "狀態"), style=discord.ButtonStyle.success, custom_id="btn_status", row=0, emoji="📜")
            self.add_action_button(label=t(lang, "menu.btn_equip", "裝備"), style=discord.ButtonStyle.secondary, custom_id="btn_equip_menu", row=0, emoji="🛡️")
            self.add_action_button(label=t(lang, "menu.btn_potions", "藥水"), style=discord.ButtonStyle.secondary, custom_id="b_itm", row=1, emoji="🎒")
            self.add_action_button(label=t(lang, "menu.btn_log", "記錄"), style=discord.ButtonStyle.secondary, custom_id="btn_combat_history", row=1, emoji="📝")
            self.add_action_button(label=t(lang, "menu.btn_area_boss", "區域BOSS"), style=discord.ButtonStyle.danger, custom_id="btn_boss_explore", row=1, emoji="👹")

    def build_artisan_menu(self):
        self.clear_items()
        lang = self.player.language
        self.log_message = t(lang, "menu.artisan_prompt", "⚒️ 【鐵匠之地】挑選你要去的地方：")
        self.add_action_button(label=t(lang, "menu.btn_blacksmith_shop", "鐵匠鋪"), style=discord.ButtonStyle.primary, custom_id="btn_blacksmith_menu", emoji="⚒️")
        self.add_action_button(label=t(lang, "menu.btn_craft_workshop", "手藝工坊"), style=discord.ButtonStyle.primary, custom_id="btn_craft_menu", emoji="🔨")
        self.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    def build_guild_menu(self):
        self.clear_items()
        lang = self.player.language
        self.log_message = t(lang, "menu.guild_prompt", "🏛️ 【冒險者公會】\n請選擇你要進行的公會服務：")
        self.add_action_button(label=t(lang, "menu.btn_leaderboard", "排行榜"), style=discord.ButtonStyle.secondary, custom_id="btn_leaderboard", emoji="🏆")
        self.add_action_button(label=t(lang, "menu.btn_stat_alloc", "屬性分配"), style=discord.ButtonStyle.primary, custom_id="btn_stat_alloc", emoji="📊")
        self.add_action_button(label=t(lang, "menu.btn_combat_history", "戰鬥記錄"), style=discord.ButtonStyle.secondary, custom_id="btn_combat_history", emoji="📝")
        self.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    def build_church_menu(self):
        self.clear_items()
        lang = self.player.language
        self.log_message = t(lang, "menu.church_prompt", "⛪ 【教堂】\n莊嚴的聖光籠罩著你。這裡能為你洗滌疲憊，指引未來的道路。")
        self.add_action_button(label=t(lang, "menu.btn_prestige_hall", "轉生殿堂"), style=discord.ButtonStyle.success, custom_id="btn_prestige_menu", emoji="🌟")
        self.add_action_button(label=t(lang, "menu.btn_skill_config", "技能配置"), style=discord.ButtonStyle.primary, custom_id="btn_skill_equip", emoji="🔧")
        self.add_action_button(label=t(lang, "menu.btn_learn_magic", "學習魔法"), style=discord.ButtonStyle.primary, custom_id="btn_skill_learn", emoji="📖")
        self.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    def process_death(self, log: str, reason: str = None) -> str:
        """統一處理死亡邏輯，回傳組合好的 log 訊息"""
        lang = self.player.language
        if reason is None:
            reason = t(lang, "menu.death_reason_default", "💀 你倒下了...")
        is_sargeras_fight = any(s["monster"].get("id") == "sargeras" for s in self.monster_slots)

        self.player.current_hp = 0
        self.player.exp = self.player.exp // 2  # 死亡懲罰：經驗值減半

        from trpg.status import clear_all_status
        clear_all_status(self.player)

        self.player.stats["total_deaths"] = self.player.stats.get("total_deaths", 0) + 1

        achv_text = self.check_achievements()
        final_log = log + f"\n\n{reason}\n{t(lang, 'menu.death_exp_halved', '(當前經驗值減半。)')}"
        if achv_text:
            final_log += achv_text

        if is_sargeras_fight:
            final_log += self._handle_sargeras_defeat()

        self.record_combat_history(final_log)
        
        self.cog.save_players()
        
        # 清除戰鬥狀態
        self.in_battle = False
        self.monster_slots = []
        self.combat._clear_battle_state()
        
        if getattr(self.player, "dungeon_state", {}).get("in_run"):
            self.player.dungeon_state["in_run"] = False
            self.player.dungeon_state["choices"] = []
            self.player.dungeon_state["floor"] = 1
            self.player.current_area = "area_00village"

        if self.player.current_area == "area_legend_cave":
            self.player.current_area = "area_00village"
            self.player.cave_state = {"current_node": "entrance", "history": []}

        # 強制導回主選單
        self.build_main_menu()

        # 回傳最終的戰報文字
        return final_log

    def _handle_sargeras_defeat(self) -> str:
        """敗於魔王薩格拉斯時呼叫：累計敗北次數，滿三次觸發吟遊詩人提示傳說洞窟。"""
        lang = self.player.language
        count = getattr(self.player, "sargeras_defeat_count", 0) + 1
        self.player.sargeras_defeat_count = count

        if count == 3 and not getattr(self.player, "legend_cave_unlocked", False):
            self.player.legend_cave_unlocked = True
            self.cog.save_players()
            return "\n\n" + t(
                lang,
                "menu.legend_cave_unlock",
                "🎻 **一位吟遊詩人不知何時出現在你身旁，撥動著琴弦：**\n"
                "「敗給魔王三次的勇士啊，不要灰心……我聽聞在世界的角落，有一座【傳說洞窟】，"
                "洞窟深處插著一把【勇者之劍】，劍身纏繞著聖光，連魔王也為之忌憚。\n"
                "只是那洞窟裡瀰漫著濃郁的魔力，每走一步都會侵蝕你的生命……願聖光指引你的方向。」\n"
                "✨ 【傳說洞窟】已出現在移動選單中！"
            )
        return ""

    def check_achievements(self) -> str:
        """檢查玩家成就，若有新解鎖的成就，回傳解鎖的公告文字，並將其加到 player.achievements"""
        lang = self.player.language
        unlocked_msgs = []
        if not hasattr(self.player, "achievements"):
            self.player.achievements = []

        achievements_config = getattr(self.cog, "achievements", {})
        for achv_id, info in achievements_config.items():
            if achv_id in self.player.achievements:
                continue

            achv_type = info.get("type")
            threshold = info.get("threshold", 0)

            # 從 stats 中獲取對應的數值
            current_val = self.player.stats.get(achv_type, 0)
            if current_val >= threshold:
                self.player.achievements.append(achv_id)
                unlocked_msgs.append(t(lang, "menu.achievement_unlocked", "🎉 【解鎖成就】{name} - {desc}", name=tf(info, "name", lang), desc=tf(info, "desc", lang)))
                
        if unlocked_msgs:
            self.cog.save_players()
            return "\n" + "\n".join(unlocked_msgs)
        return ""

    def _get_equipment_comparison_string(self, item_data: dict) -> str:
        item_type = item_data.get("type")
        if item_type == "weapon":
            eq_id = self.player.weapon
        elif item_type == "armor":
            eq_id = self.player.armor
        elif item_type == "accessory":
            eq_id = self.player.accessory
        else:
            return ""

        eq_item = self.cog.items.get(eq_id, {}) if eq_id else {}
        
        diffs = []
        for key, (label, emoji) in {
            "atk_bonus": ("ATK", "⚔️"),
            "def_bonus": ("DEF", "🛡️"),
            "hp_bonus": ("HP", "❤️"),
            "magic_bonus": ("MAG", "✨"),
            "res_bonus": ("RES", "🔰"),
            "spd_bonus": ("SPD", "🚀"),
        }.items():
            new_val = item_data.get(key, 0)
            old_val = eq_item.get(key, 0)
            diff = new_val - old_val
            if diff > 0:
                diffs.append(f"{emoji}{label}+{diff}▲")
            elif diff < 0:
                diffs.append(f"{emoji}{label}{diff}▼")
                
        if diffs:
            return "[" + " ".join(diffs) + "]"
        return ""
    
    def build_battle_menu(self):
        self.clear_items()
        self.in_battle = True
        lang = self.player.language

        if "berserk" in getattr(self.player, "status_effects", {}):
            self.add_action_button(label=t(lang, "menu.btn_berserk_attack", "狂暴攻擊"), style=discord.ButtonStyle.danger, custom_id="b_atk", row=0, emoji="😡")
            self.add_action_button(label=t(lang, "menu.btn_flee", "逃跑"), style=discord.ButtonStyle.secondary, custom_id="b_fle", row=0, emoji="🏃")
            self.add_action_button(label=t(lang, "menu.btn_status", "狀態"), style=discord.ButtonStyle.success, custom_id="b_sta", row=0, emoji="📜")
            return

        self.add_action_button(label=t(lang, "menu.btn_attack", "攻擊"), style=discord.ButtonStyle.danger, custom_id="b_atk", row=0, emoji="🗡️")
        self.add_action_button(label=t(lang, "menu.btn_skill", "技能"), style=discord.ButtonStyle.success, custom_id="b_ski", row=0, emoji="✨")
        self.add_action_button(label=t(lang, "menu.btn_defend", "防禦"), style=discord.ButtonStyle.primary, custom_id="b_def", row=0, emoji="🛡️")
        self.add_action_button(label=t(lang, "menu.btn_dodge", "閃避"), style=discord.ButtonStyle.primary, custom_id="b_dod", row=0, emoji="💨")

        self.add_action_button(label=t(lang, "menu.btn_item", "道具"), style=discord.ButtonStyle.secondary, custom_id="b_itm", row=1, emoji="🎒")
        self.add_action_button(label=t(lang, "menu.btn_flee", "逃跑"), style=discord.ButtonStyle.secondary, custom_id="b_fle", row=1, emoji="🏃")
        self.add_action_button(label=t(lang, "menu.btn_status", "狀態"), style=discord.ButtonStyle.success, custom_id="b_sta", row=1, emoji="📜")

    async def handle_move_menu(self):
        self.clear_items()
        lang = self.player.language
        self.log_message = t(lang, "menu.move_prompt", "挑選你打算移動前往的下一個區域：")
        for area_id, area in self.cog.areas.items():
            if area_id == self.player.current_area:
                continue
            requires_flag = area.get("requires_flag")
            if requires_flag and not getattr(self.player, requires_flag, False):
                continue
            req = area.get("req_level", 1)
            area_name = tf(area, "area_name", lang) if area.get("area_name") else t(lang, "menu.unknown_area", "未知區域")
            label = t(lang, "menu.move_to_label", "前往 {area_name} (Lv.{req})", area_name=area_name, req=req)
            self.add_action_button(label=label, style=discord.ButtonStyle.primary, custom_id=f"move_to_{area_id}")
        self.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    async def handle_boss_explore(self):
        from datetime import datetime
        lang = self.player.language
        if self.player.current_hp <= 0:
            self.log_message = t(lang, "menu.near_death_rest", "❌ 你快死掉了，請先回村莊休息！")
            return

        area_data = self.cog.areas.get(self.player.current_area)
        boss_data = area_data.get("boss") if area_data else None

        if not boss_data:
            self.log_message = t(lang, "menu.no_boss_here", "📍 這個區域似乎沒有盤踞任何 BOSS...")
            return

        # 📆 檢查每日擊殺限制
        today_str = datetime.today().strftime('%Y-%m-%d')
        if self.player.daily_boss_kills.get(self.player.current_area) == today_str:
            self.log_message = t(lang, "menu.boss_already_defeated_today", "❌ 這裡的 BOSS【{boss_name}】今天已經被你討伐了。明天刷新後再來吧！", boss_name=tf(boss_data, "name", lang))
            return
            
       # 遭遇 BOSS，複製數值進入戰鬥
        boss_instance = dict(boss_data)
        boss_instance.setdefault("id", "boss")
        area_req_level = area_data.get("req_level", 1)
        boss_instance["level"] = boss_data.get("level", area_req_level + 5)
        # 👇 動態計算並寫入 BOSS 速度，讓 UI 抓得到
        boss_instance["spd"] = boss_data.get("spd", int(15 + boss_instance["level"] * 2.2))

        # 👇 勇者之劍：克制特定魔王，大幅削弱其屬性
        weapon = self.cog.items.get(self.player.weapon, {}) if self.player.weapon else {}
        anti_boss_text = ""
        if weapon.get("anti_boss_id") and weapon["anti_boss_id"] == boss_instance.get("id"):
            boss_instance["atk"] = max(1, int(boss_instance["atk"] * weapon.get("anti_boss_atk_mult", 1.0)))
            boss_instance["def"] = max(0, int(boss_instance["def"] * weapon.get("anti_boss_def_mult", 1.0)))
            boss_instance["spd"] = max(5, int(boss_instance["spd"] * weapon.get("anti_boss_spd_mult", 1.0)))
            anti_boss_text = "\n" + t(lang, "menu.anti_boss_weapon_glow", "✨ 【{weapon_name}】散發出聖潔的光輝，魔王的力量被大幅削弱了！", weapon_name=tf(weapon, "name", lang) if weapon else "")

        self.start_combat([boss_instance])

        self.log_message = t(lang, "menu.boss_encounter_warning", "🚨 【區域領主警告】 🚨\n大地在震動... 你驚動了隱藏的首領【{boss_name}】！{anti_boss_text}", boss_name=tf(boss_instance, "name", lang), anti_boss_text=anti_boss_text)
        self.build_battle_menu()

    async def handle_equip_menu(self, notice="", paging=False):
        self.clear_items()
        if not paging:
            self.inventory_page = 0
        self.current_menu_state = "equip"
        lang = self.player.language

        weapons_in_bag = []
        armors_in_bag = []
        accessories_in_bag = []

        for item_id, count in self.player.inventory.items():
            if count > 0:
                item_data = self.cog.items.get(item_id)
                if not item_data: continue
                if item_data.get("type") == "weapon":
                    if item_data.get("exclusive_level", 0) <= self.player.level:
                        weapons_in_bag.append(item_id)
                elif item_data.get("type") == "armor":
                    if item_data.get("exclusive_level", 0) <= self.player.level:
                        armors_in_bag.append(item_id)
                elif item_data.get("type") == "accessory":
                    if item_data.get("exclusive_level", 0) <= self.player.level:
                        accessories_in_bag.append(item_id)

        # 合併所有可裝備的物品
        all_equips = weapons_in_bag + armors_in_bag + accessories_in_bag
        total_items = len(all_equips)

        # 分頁範圍
        items_per_page = 8
        max_page = max(0, (total_items - 1) // items_per_page)
        self.inventory_page = min(self.inventory_page, max_page)

        start_idx = self.inventory_page * items_per_page
        end_idx = start_idx + items_per_page
        page_items = all_equips[start_idx:end_idx]

        self.log_message = (notice + "\n\n" if notice else "") + t(lang, "equip.menu_title", "🎒 【裝備管理】(第 {page}/{max_page} 頁)", page=self.inventory_page + 1, max_page=max_page + 1)

        none_bare_handed = t(lang, "equip.none_bare_handed", "無 (空手)")
        none_cloth = t(lang, "equip.none_cloth", "無 (布衣)")
        none_label = t(lang, "equip.none", "無")
        c_weap = f"【{tf(self.cog.items[self.player.weapon], 'name', lang)}】" if self.player.weapon else none_bare_handed
        c_armr = f"【{tf(self.cog.items[self.player.armor], 'name', lang)}】" if getattr(self.player, 'armor', None) else none_cloth
        c_accs = f"【{tf(self.cog.items[self.player.accessory], 'name', lang)}】" if self.player.accessory else none_label

        self.log_message += t(lang, "equip.current_gear_summary", "\n👉 武器：{weapon}\n👉 防具：{armor}\n👉 飾品：{accessory}", weapon=c_weap, armor=c_armr, accessory=c_accs)

        for item_id in page_items:
            item_data = self.cog.items[item_id]
            item_type = item_data.get("type")
            item_name = tf(item_data, "name", lang)
            if item_type == "weapon":
                if item_id == self.player.weapon:
                    self.add_action_button(label=t(lang, "equip.btn_unequip", "卸下 {name}", name=item_name), style=discord.ButtonStyle.danger, custom_id=f"unequip_{item_id}")
                else:
                    self.add_action_button(label=t(lang, "equip.btn_equip_weapon", "裝備 {name}", name=item_name), style=discord.ButtonStyle.success, custom_id=f"equip_{item_id}")
            elif item_type == "armor":
                if item_id == getattr(self.player, "armor", None):
                    self.add_action_button(label=t(lang, "equip.btn_unequip", "卸下 {name}", name=item_name), style=discord.ButtonStyle.danger, custom_id=f"unequip_{item_id}")
                else:
                    self.add_action_button(label=t(lang, "equip.btn_equip_armor", "穿戴 {name}", name=item_name), style=discord.ButtonStyle.primary, custom_id=f"equip_{item_id}")
            elif item_type == "accessory":
                if item_id == self.player.accessory:
                    self.add_action_button(label=t(lang, "equip.btn_unequip", "卸下 {name}", name=item_name), style=discord.ButtonStyle.danger, custom_id=f"unacc_{item_id}")
                else:
                    self.add_action_button(label=t(lang, "equip.btn_equip_accessory", "配戴 {name}", name=item_name), style=discord.ButtonStyle.success, custom_id=f"acc_{item_id}")

        if not all_equips:
            self.log_message += "\n\n" + t(lang, "equip.no_equippable_items", "背包裡沒有可裝備的物品。")

        # 分頁按鈕
        if total_items > items_per_page:
            self.add_action_button(label=t(lang, "menu.btn_prev_page", "◀️ 上一頁"), style=discord.ButtonStyle.secondary, custom_id="btn_prev_page", row=3)
            self.add_action_button(label=t(lang, "menu.btn_next_page", "▶️ 下一頁"), style=discord.ButtonStyle.secondary, custom_id="btn_next_page", row=3)

        self.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙", row=4)

    async def handle_equip_action(self, item_id: str, equip: bool):
        item_data = self.cog.items.get(item_id)
        if not item_data: return
        lang = self.player.language
        item_name = tf(item_data, "name", lang)

        if equip:
            req_lv = item_data.get("exclusive_level", item_data.get("req_level", 0))
            if req_lv > self.player.level:
                # 傳遞錯誤訊息給選單
                await self.handle_equip_menu(t(lang, "equip.level_too_low", "❌ 等級不足！裝備【{name}】需要 Lv.{req_lv}。", name=item_name, req_lv=req_lv))
                return

            if item_data["type"] == "weapon":
                self.player.weapon = item_id
            elif item_data["type"] == "armor":
                self.player.armor = item_id

            recalc_player_stats(self.player, self.cog.items, heal_full=False)
            self.cog.save_players()
            await self.handle_equip_menu(t(lang, "equip.equip_success", "🛡️ 成功裝備了【{name}】！感覺自己變強了。", name=item_name))
        else:
            if item_data["type"] == "weapon":
                self.player.weapon = None
            elif item_data["type"] == "armor":
                self.player.armor = None

            recalc_player_stats(self.player, self.cog.items, heal_full=False)
            self.cog.save_players()
            await self.handle_equip_menu(t(lang, "equip.unequip_success", "🛡️ 卸下了【{name}】。", name=item_name))

    async def handle_accessory_action(self, item_id: str, equip: bool):
        item_data = self.cog.items.get(item_id)
        if not item_data or item_data.get("type") != "accessory":
            return
        lang = self.player.language
        item_name = tf(item_data, "name", lang)
        req_lv = item_data.get("exclusive_level", 0)
        if req_lv > self.player.level:
            await self.handle_equip_menu(t(lang, "equip.accessory_level_too_low", "❌ 需要 Lv.{req_lv} 才能配戴【{name}】。", req_lv=req_lv, name=item_name))
            return

        if equip:
            self.player.accessory = item_id
            if item_id == "jester_mask":
                activate_jester_immunity(self.player)
            recalc_player_stats(self.player, self.cog.items, heal_full=False)
            self.cog.save_players()
            await self.handle_equip_menu(t(lang, "equip.accessory_equip_success", "🎭 配戴了【{name}】！", name=item_name))
        else:
            self.player.accessory = None
            recalc_player_stats(self.player, self.cog.items, heal_full=False)
            self.cog.save_players()
            await self.handle_equip_menu(t(lang, "equip.accessory_unequip_success", "🎭 卸下了【{name}】。", name=item_name))

    # 👇 加上 notice 參數
    def _format_shop_item_line(self, item_id: str) -> str:
        lang = self.player.language
        item = self.cog.items.get(item_id, {})
        req = item.get("exclusive_level", 0)
        req_str = t(lang, "shop.req_level_suffix", " | 需 Lv.{req}", req=req) if req else ""
        item_name = tf(item, "name", lang) if item else item_id
        item_desc = tf(item, "desc", lang) if item else ""
        line = f"• {item_name}{req_str} | {item.get('price', 0)}$ | {item_desc}"

        if item.get("type") in ("weapon", "armor", "accessory"):
            comp_str = self._get_equipment_comparison_string(item)
            if comp_str:
                line += f" {comp_str}"

        if item.get("type") == "skill_scroll":
            skill = self.cog.skills.get(item.get("teaches", ""), {})
            skill_desc = tf(skill, "desc", lang) if skill else ""
            if skill_desc:
                line += f"\n  ↳ {skill_desc}"
        return line

    def _roll_shop_stock(self):
        today_str = datetime.today().strftime("%Y-%m-%d")
        if self.player.last_shop_refresh != today_str:
            self.player.last_shop_refresh = today_str
            self.player.shop_refresh_count = 0
            self.player.shop_items = []

        if self.player.mystery_merchant_date != today_str:
            self.player.mystery_merchant_date = today_str
            if random.random() < 0.20:
                self.player.mystery_shop_active = True
                mystery_pool = [
                    k for k, v in self.cog.items.items()
                    if v.get("mystery_only") and v.get("price", 0) > 0
                ]
                self.player.mystery_shop_items = random.sample(
                    mystery_pool, min(3, len(mystery_pool))
                ) if mystery_pool else []
            else:
                self.player.mystery_shop_active = False
                self.player.mystery_shop_items = []

        if not self.player.shop_items:
            general_pool = []
            general_weights = []
            for k, v in self.cog.items.items():
                w = v.get("shop_weight", 0)
                if w > 0 and k not in ("health_potion", "mana_potion"):
                    if _item_shop_level_ok(self.player, k, v, self.cog.skills):
                        general_pool.append(k)
                        general_weights.append(w)

            picks = []
            if general_pool:
                for _ in range(5):
                    if not general_pool:
                        break
                    choice = random.choices(general_pool, weights=general_weights, k=1)[0]
                    picks.append(choice)
                    idx = general_pool.index(choice)
                    general_pool.pop(idx)
                    general_weights.pop(idx)

            self.player.shop_items = ["health_potion", "mana_potion"] + picks
            self.cog.save_players()

    async def handle_shop_menu(self, notice=""):
        self._roll_shop_stock()
        self.current_menu_state = "shop"
        lang = self.player.language

        self.clear_items()
        lines = []
        prefix = (notice + "\n\n" if notice else "")
        self.log_message = prefix + t(lang, "shop.village_store_title", "🛒 【村莊雜貨鋪】今日限定貨架：") + "\n"

        for item_id in self.player.shop_items:
            item = self.cog.items.get(item_id)
            if item:
                lines.append(self._format_shop_item_line(item_id))
                req = item.get("exclusive_level", 0)
                req_label = f" Lv.{req}" if req else ""

                comp_str = ""
                if item.get("type") in ("weapon", "armor", "accessory"):
                    comp_str = self._get_equipment_comparison_string(item)
                comp_suffix = f" {comp_str}" if comp_str else ""

                item_name = tf(item, "name", lang)
                label_text = t(lang, "shop.btn_buy_item", "買 {name}{req_label} ({price}$){comp_suffix}", name=item_name, req_label=req_label, price=item['price'], comp_suffix=comp_suffix)
                self.add_action_button(
                    label=label_text[:80],
                    style=discord.ButtonStyle.primary,
                    custom_id=f"buy_{item_id}",
                )

        if getattr(self.player, "mystery_shop_active", False) and self.player.mystery_shop_items:
            self.log_message += "\n\n" + t(lang, "shop.mystery_merchant_title", "🎭 【神秘商人 · 今日限定】") + "\n"
            for item_id in self.player.mystery_shop_items:
                item = self.cog.items.get(item_id)
                if not item:
                    continue
                lines.append(self._format_shop_item_line(item_id))

                comp_str = ""
                if item.get("type") in ("weapon", "armor", "accessory"):
                    comp_str = self._get_equipment_comparison_string(item)
                comp_suffix = f" {comp_str}" if comp_str else ""

                item_name = tf(item, "name", lang)
                label_text = t(lang, "shop.btn_buy_mystery_item", "🎭 {name} ({price}$){comp_suffix}", name=item_name, price=item['price'], comp_suffix=comp_suffix)
                self.add_action_button(
                    label=label_text[:80],
                    style=discord.ButtonStyle.success,
                    custom_id=f"buy_{item_id}",
                )

        self.log_message += "\n".join(lines)

        refresh_cost = 100 * (2 ** getattr(self.player, "shop_refresh_count", 0))

        self.add_action_button(label=t(lang, "shop.btn_refresh_shop", "刷新商店 ({cost}$)", cost=refresh_cost), style=discord.ButtonStyle.danger, custom_id="btn_shop_refresh", emoji="🔄")
        self.add_action_button(label=t(lang, "shop.btn_sell_items", "出售物品"), style=discord.ButtonStyle.success, custom_id="btn_shop_sell", emoji="💰")
        self.add_action_button(label=t(lang, "menu.btn_back_village", "返回村莊"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    async def handle_shop_refresh(self):
        count = getattr(self.player, "shop_refresh_count", 0)
        cost = 100 * (2 ** count)
        user_bal = self.cog.get_bank_balance(self.user_id)
        
        if user_bal < cost:
            await self.handle_shop_menu(t(self.player.language, "shop.refresh_insufficient_gold", "❌ 金幣不足！手動進貨需要支付 {cost}$ 給老闆。", cost=cost))
            return

        self.cog.adjust_bank(self.user_id, -cost)
        if not getattr(self.player, "stats", None):
            self.player.stats = {}
        self.player.stats["money_spent"] = self.player.stats.get("money_spent", 0) + cost

        self.player.shop_refresh_count = count + 1
        self.player.shop_items = []

        achv_text = self.check_achievements()
        notice_text = t(self.player.language, "shop.refresh_success", "🔄 支付了 {cost}$ 刷新商店！老闆為你進了一批新貨。", cost=cost)
        if achv_text:
            notice_text += achv_text
            
        self.cog.save_players()
        await self.handle_shop_menu(notice_text)

    async def _refresh_buy_menu(self, notice: str):
        if getattr(self, "current_menu_state", None) == "tower_merchant":
            await self.handle_tower_merchant(notice)
        else:
            await self.handle_shop_menu(notice)

    async def execute_buy(self, item_id: str, amount: int):
        item = self.cog.items.get(item_id)
        lang = self.player.language
        if not item:
            await self._refresh_buy_menu(t(lang, "shop.item_no_longer_available", "❌ 這個商品已經不在貨架上了。"))
            return

        item_name = tf(item, "name", lang)
        total_cost = item.get("price", 0) * amount
        user_bal = self.cog.get_bank_balance(self.user_id)
        if user_bal < total_cost:
            await self._refresh_buy_menu(t(lang, "shop.buy_insufficient_gold", "❌ 金幣不足！購買 {amount} 個【{name}】需要 {total_cost}$，但你只有 {user_bal}$。", amount=amount, name=item_name, total_cost=total_cost, user_bal=user_bal))
            return

        self.cog.adjust_bank(self.user_id, -total_cost)
        self.player.inventory[item_id] = self.player.inventory.get(item_id, 0) + amount
        if not getattr(self.player, "stats", None):
            self.player.stats = {}
        self.player.stats["money_spent"] = self.player.stats.get("money_spent", 0) + total_cost

        achv_text = self.check_achievements()
        notice_text = t(lang, "shop.buy_success", "✅ 購買了 {amount} 個【{name}】，花費 {total_cost}$！", amount=amount, name=item_name, total_cost=total_cost)
        if achv_text:
            notice_text += achv_text

        self.cog.save_players()
        await self._refresh_buy_menu(notice_text)

    async def execute_sell(self, item_id: str, amount: int):
        item = self.cog.items.get(item_id)
        lang = self.player.language
        owned = self.player.inventory.get(item_id, 0)
        if not item or owned <= 0:
            await self.handle_sell_menu(t(lang, "shop.item_not_owned", "❌ 你並未持有這個物品。"), paging=True)
            return

        item_name = tf(item, "name", lang)
        if amount > owned:
            await self.handle_sell_menu(t(lang, "shop.sell_amount_exceeds_owned", "❌ 數量超過持有量！你只有 {owned} 個【{name}】。", owned=owned, name=item_name), paging=True)
            return

        unit_price = get_sell_price(item_id, self.cog.items)
        total_price = unit_price * amount

        self.player.inventory[item_id] -= amount
        if self.player.inventory[item_id] <= 0:
            del self.player.inventory[item_id]
        self.cog.adjust_bank(self.user_id, total_price)

        self.cog.save_players()
        await self.handle_sell_menu(t(lang, "shop.sell_success", "✅ 賣出了 {amount} 個【{name}】，獲得 {total_price}$！", amount=amount, name=item_name, total_price=total_price), paging=True)

    async def handle_sell_menu(self, notice="", paging=False):
        self.clear_items()
        if not paging:
            self.inventory_page = 0
        self.current_menu_state = "sell"
        lang = self.player.language

        sellable = []
        for item_id, count in self.player.inventory.items():
            if count > 0 and item_id != "jester_mask":
                item = self.cog.items.get(item_id)
                if item and get_sell_price(item_id, self.cog.items) > 0:
                    sellable.append(item_id)

        total_items = len(sellable)
        items_per_page = 8
        max_page = max(0, (total_items - 1) // items_per_page)
        self.inventory_page = min(self.inventory_page, max_page)

        start_idx = self.inventory_page * items_per_page
        end_idx = start_idx + items_per_page
        page_items = sellable[start_idx:end_idx]

        prefix = notice + "\n\n" if notice else ""
        if not sellable:
            self.log_message = prefix + t(lang, "shop.sell_menu_empty", "💰 【出售物品】\n沒有可以賣給商店的東西。")
        else:
            self.log_message = prefix + t(lang, "shop.sell_menu_title", "💰 【出售物品】(第 {page}/{max_page} 頁)\n選擇要賣出的物品：", page=self.inventory_page + 1, max_page=max_page + 1)
            for item_id in page_items:
                item = self.cog.items[item_id]
                price = get_sell_price(item_id, self.cog.items)
                count = self.player.inventory[item_id]
                self.add_action_button(
                    label=t(lang, "shop.btn_sell_item", "賣 {name} ({price}$) x{count}", name=tf(item, "name", lang), price=price, count=count),
                    style=discord.ButtonStyle.primary,
                    custom_id=f"sell_{item_id}",
                )

        if total_items > items_per_page:
            self.add_action_button(label=t(lang, "menu.btn_prev_page", "◀️ 上一頁"), style=discord.ButtonStyle.secondary, custom_id="btn_prev_page", row=3)
            self.add_action_button(label=t(lang, "menu.btn_next_page", "▶️ 下一頁"), style=discord.ButtonStyle.secondary, custom_id="btn_next_page", row=3)

        self.add_action_button(label=t(lang, "shop.btn_back_to_shop", "返回商店"), style=discord.ButtonStyle.secondary, custom_id="btn_shop_menu", emoji="🔙", row=4)

    async def handle_item_menu(self, paging=False):
        self.clear_items()
        if not paging:
            self.inventory_page = 0
        self.current_menu_state = "item"
        lang = self.player.language

        usable = []
        for item_id, count in self.player.inventory.items():
            if count > 0:
                item = self.cog.items.get(item_id)
                if item and item.get("type") in ("potion", "cure"):
                    usable.append(item_id)

        total_items = len(usable)
        items_per_page = 8
        max_page = max(0, (total_items - 1) // items_per_page)
        self.inventory_page = min(self.inventory_page, max_page)

        start_idx = self.inventory_page * items_per_page
        end_idx = start_idx + items_per_page
        page_items = usable[start_idx:end_idx]

        if not usable:
            self.log_message = t(lang, "menu.no_usable_items", "❌ 背包裡沒有可用的道具。")
            if self.in_battle:
                self.build_battle_menu()
            else:
                self.build_main_menu()
            return

        self.log_message = t(lang, "menu.choose_item_to_use", "🎒 選擇要使用的道具：(第 {page}/{max_page} 頁)", page=self.inventory_page + 1, max_page=max_page + 1)
        for item_id in page_items:
            item = self.cog.items[item_id]
            self.add_action_button(
                label=f"{tf(item, 'name', lang)} x{self.player.inventory[item_id]}",
                style=discord.ButtonStyle.secondary,
                custom_id=f"use_item_{item_id}",
            )

        if total_items > items_per_page:
            self.add_action_button(label=t(lang, "menu.btn_prev_page", "◀️ 上一頁"), style=discord.ButtonStyle.secondary, custom_id="btn_prev_page", row=3)
            self.add_action_button(label=t(lang, "menu.btn_next_page", "▶️ 下一頁"), style=discord.ButtonStyle.secondary, custom_id="btn_next_page", row=3)

        back_id = "btn_back_battle" if self.in_battle else "btn_back_main"
        self.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id=back_id, emoji="🔙", row=4)

    async def handle_use_item(self, custom_id):
        lang = self.player.language
        item_id = custom_id.replace("use_item_", "")
        item = self.cog.items.get(item_id)
        if not item:
            self.log_message = t(lang, "battle.invalid_item", "❌ 無效物品。")
        elif item.get("type") == "potion":
            if self.in_battle:
                self.log_message = self.combat.use_potion(item_id)
            else:
                self.log_message = self.use_potion_out_of_battle(item_id)
        elif item.get("type") == "cure":
            if self.in_battle:
                self.log_message = self.combat.use_cure_item(item_id)
            else:
                self.log_message = self.use_cure_item_out_of_battle(item_id)
        else:
            self.log_message = t(lang, "battle.cannot_use_item", "❌ 無法使用此物品。")

        # 更新畫面
        if self.in_battle:
            self.build_battle_menu()
        else:
            self.build_main_menu()

    def use_potion_out_of_battle(self, item_id: str):
        """戰鬥外的藥水邏輯"""
        lang = self.player.language
        if self.player.inventory.get(item_id, 0) <= 0:
            item_name = tf(self.cog.items.get(item_id, {}), "name", lang) or t(lang, "battle.potion_fallback_name", "藥水")
            return t(lang, "battle.no_item_left", "❌ 你包包裡沒有【{item_name}】了！", item_name=item_name)

        item_data = self.cog.items.get(item_id, {})
        heal_target = get_potion_heal_target(item_data, item_id)

        if heal_target == "mp" and self.player.current_mp >= self.player.max_mp:
            return t(lang, "battle.mp_already_full", "❓ 你的魔力已經滿了，別浪費藥水。")
        if heal_target == "hp" and self.player.current_hp >= self.player.max_hp:
            return t(lang, "battle.hp_already_full", "❓ 你的生命值已經滿了，別浪費藥水。")

        self.player.inventory[item_id] -= 1
        if self.player.inventory[item_id] <= 0:
            del self.player.inventory[item_id]

        if "heal_percent" in item_data:
            if heal_target == "mp":
                heal = int(self.player.max_mp * item_data["heal_percent"])
                self.player.current_mp = min(self.player.max_mp, self.player.current_mp + heal)
                self.cog.save_players()
                return t(lang, "battle.drank_potion_mp", "🧪 你喝下了藥水，回復了 {heal} 點魔力。", heal=heal)
            heal = int(self.player.max_hp * item_data["heal_percent"])
        else:
            heal = item_data.get("heal", 50)

        self.player.current_hp = min(self.player.max_hp, self.player.current_hp + heal)
        self.cog.save_players()
        return t(lang, "battle.drank_potion_hp", "🧪 你喝下了藥水，回復了 {heal} 點生命值。", heal=heal)

    def use_cure_item_out_of_battle(self, item_id: str):
        """戰鬥外的解藥邏輯"""
        lang = self.player.language
        if self.player.inventory.get(item_id, 0) <= 0:
            return t(lang, "battle.no_such_item_in_bag", "❌ 背包裡沒有這個物品。")

        item = self.cog.items.get(item_id, {})
        cures = item.get("cures", [])

        cured = []
        for sid in cures:
            if sid in self.player.status_effects:
                cured.append(tf(self.cog.status_effects.get(sid, {}), "name", lang) or sid)
                del self.player.status_effects[sid]

        if not cured:
            return t(lang, "battle.nothing_to_cure", "❌ 你目前沒有這個物品能解除的異常狀態，省著點用吧！")

        self.player.inventory[item_id] -= 1
        if self.player.inventory[item_id] <= 0:
            del self.player.inventory[item_id]
        self.cog.save_players()
        item_name = tf(item, "name", lang)
        return t(lang, "battle.used_item_cured", "✨ 使用了【{item_name}】，解除了：{cured_list}", item_name=item_name, cured_list="、".join(cured))
        
    def _generate_action_bar(self, current, maximum, length=10):
            """生成文字版行動條"""
            if maximum <= 0: return "▱" * length
            filled = int(round((current / maximum) * length))
            filled = min(max(filled, 0), length)
            return "▰" * filled + "▱" * (length - filled)

    def generate_embed(self):
        if getattr(self, "viewing_leaderboard", False):
            return self.build_leaderboard_embed()
        embed = discord.Embed(color=discord.Color.dark_theme())
        p = self.player
        lang = p.language
        area_data = self.cog.areas.get(p.current_area, {})
        area_name = tf(area_data, "area_name", lang) if area_data.get("area_name") else t(lang, "explore.unknown_area", "未知區域")
        user_bal = self.cog.get_bank_balance(self.user_id)
        status_text = format_status_list(p.status_effects, self.cog.status_effects, p.language)
        state_text = t(lang, "battle.state_in_battle", "⚔️ 戰鬥中") if self.in_battle else t(lang, "explore.state_exploring", "🌿 探索中")

        p_atk = get_player_atk(p, self.cog.items, self.cog.status_effects)
        p_def = get_player_def(p, self.cog.items)
        p_magic = get_player_magic(p, self.cog.items, self.cog.status_effects)
        p_spd = getattr(p, "base_spd", 0)
        p_res = getattr(p, "base_res", 0)
        unspent = get_unspent_points(p)

        # 配合 trpg_combat.py 的設定，抓取 player_av
        p_atb = getattr(self.combat, "player_av", 0) if self.in_battle else 0
        atb_max = 100  # 根據你的 advance_time 邏輯，滿值固定為 100

        player_bar = self._generate_action_bar(p_atb, atb_max)

        # 小丑面具判定
        immune_str = ""
        immune_status_id = get_daily_jester_immunity(p, self.cog.status_effects)
        if immune_status_id:
            immune_name = tf(self.cog.status_effects.get(immune_status_id, {}), "name", lang) or immune_status_id
            immune_str = "\n" + t(lang, "battle.jester_mask_immunity", "🎭 面具庇護：今日完全免疫【{immune_name}】", immune_name=immune_name)

        embed.title = f"{state_text} | {area_name}"

        # 玩家狀態區塊排版
        adventurer_line = t(lang, "battle.adventurer_status_line", "**Lv.{level} 冒險者** | 💰 {balance} {money_name}", level=p.level, balance=user_bal, money_name=self.cog.bot.baba.money_name)
        player_desc = (
            f"{adventurer_line}\n"
            f"❤️ HP: `{p.current_hp:03d}/{p.max_hp:03d}`\n"
            f"💧 MP: `{p.current_mp:03d}/{p.max_mp:03d}`\n"
            f"⚔️ ATK: `{p_atk}` | 🛡️ DEF: `{p_def}` | 🚀 SPD: `{p_spd}`\n"
            f"✨ MAG: `{p_magic}` | 🔰 RES: `{p_res}`\n"
        )

        # 戰鬥中才顯示行動條
        if self.in_battle:
            action_label = t(lang, "battle.action_bar_label", "⚡ 行動: `[{bar}]`", bar=player_bar)
            player_desc += f"{action_label}\n"

        unspent_line = t(lang, "battle.unspent_points_line", "📊 未分配點數: `{unspent}`", unspent=unspent)
        status_line = t(lang, "battle.status_line", "📜 狀態：{status_text}", status_text=status_text)
        player_desc += (
            f"{unspent_line}\n"
            f"{status_line}{immune_str}"
        )
        embed.add_field(name=t(lang, "battle.your_status_field", "👤 你的狀態"), value=player_desc, inline=False)

        # 戰鬥時顯示敵方狀態區塊（最多 3 格，前排優先顯示在最上面）
        if self.in_battle and self.monster_slots:
            front_seen = False
            for slot in self.monster_slots:
                m = slot["monster"]
                is_front = (not front_seen) and slot["hp"] > 0
                if slot["hp"] > 0:
                    front_seen = True
                if is_front:
                    row_tag = t(lang, "battle.row_front", "🎯 前排")
                elif slot["hp"] > 0:
                    row_tag = t(lang, "battle.row_back", "　 後排")
                else:
                    row_tag = t(lang, "battle.row_defeated", "💀 已擊倒")
                monster_bar = self._generate_action_bar(slot["av"], atb_max)
                monster_name = tf(m, "name", lang)
                action_label = t(lang, "battle.action_bar_label", "⚡ 行動: `[{bar}]`", bar=monster_bar)
                monster_desc = (
                    f"❤️ HP: `{max(0, slot['hp']):03d}/{m['max_hp']:03d}`\n"
                    f"⚔️ ATK: `{m['atk']}` | 🛡️ DEF: `{m['def']}` | 🚀 SPD: `{m.get('spd', 0)}`\n"
                    f"{action_label}"
                )
                embed.add_field(name=f"{row_tag}：{monster_name}", value=monster_desc, inline=len(self.monster_slots) > 1)

        embed.description = f"```\n{self.log_message}\n```"
        return embed

    async def handle_explore(self):
        lang = self.player.language
        if self.player.current_hp <= 0:
            self.log_message = t(lang, "explore.already_fallen", "❌ 你已經倒下了，請先去旅館休息療傷！")
            return

        area_data = self.cog.areas.get(self.player.current_area, {})

        # 👇 1. 判斷是否在魔塔或地下城區域，如果是，直接轉交給專屬函數
        if self.player.current_area == "area_tower":
            await self.handle_tower_explore()
            return
        elif self.player.current_area == "area_dungeon":
            self.build_dungeon_menu()
            return

        # 👇 2. 新版事件系統：讀取該區域的專屬事件機率（預設 0.2）
        event_chance = area_data.get("event_chance", 0.2)
        if random.random() < event_chance and self.cog.events:
            await self.handle_random_event(area_data)
            return

        if not area_data or not area_data.get("monsters"):
            self.log_message = t(lang, "explore.area_peaceful", "📍 這個區域一片祥和，沒有任何怪物跡象。")
            return

        monster_ids = list(area_data["monsters"].keys())
        weights = [area_data["monsters"][m_id]["spawn_rate"] for m_id in monster_ids]

        selected_id = random.choices(monster_ids, weights=weights)[0]
        base_monster = area_data["monsters"][selected_id]

        # 👇 1. 動態等級浮動：區域等級 + (0 ~ 5) 級的隨機浮動
        area_req_level = area_data.get("req_level", 1)
        m_level = area_req_level + random.randint(0, 5)

        # 👇 2. 數值膨脹倍率：每超過區域底線 1 級，全屬性提升 15%
        scale = 1.0 + (m_level - area_req_level) * 0.15

        monster_instance = dict(base_monster)
        monster_instance.setdefault("id", selected_id)

        # 👇 3. 套用名稱標籤與數值倍率
        monster_instance["level"] = m_level
        base_monster_name = tf(base_monster, "name", lang)
        monster_instance["name"] = f"{base_monster_name} (Lv.{m_level})"
        if base_monster.get("name_en"):
            monster_instance["name_en"] = f"{base_monster['name_en']} (Lv.{m_level})"
        monster_instance["max_hp"] = max(1, int(base_monster["max_hp"] * scale))
        monster_instance["atk"] = max(1, int(base_monster["atk"] * scale))
        monster_instance["def"] = max(1, int(base_monster["def"] * scale))
        monster_instance["exp"] = max(1, int(base_monster.get("exp", 10) * scale))
        if "magic" in base_monster:
            monster_instance["magic"] = max(1, int(base_monster["magic"] * scale))

        # 👇 4. 動態生成速度 (SPD)：如果有寫死就用，沒有就根據等級隨機生成
        default_spd = int(10 + m_level * 1.5 + random.randint(-2, 2))
        monster_instance["spd"] = base_monster.get("spd", default_spd)

        self.start_combat([monster_instance])
        self.cog.save_players()

        encountered_name = tf(monster_instance, "name", lang)
        self.log_message = t(lang, "explore.monster_encountered", "⚔️ 遭遇了【{monster_name}】！對方來勢洶洶！", monster_name=encountered_name)
        self.build_battle_menu()

    async def handle_random_event(self, area_data=None):
        lang = self.player.language
        # 👇 根據區域 JSON 抓取專屬事件池，若無則用全域事件
        if area_data and "events" in area_data:
            event_pool = [e for e in area_data["events"] if e in self.cog.events]
        else:
            event_pool = list(self.cog.events.keys())

        if not event_pool:
            self.log_message = t(lang, "explore.nothing_happened", "🌿 風吹草動，但什麼也沒發生。")
            self.build_main_menu()
            return

        weights = [self.cog.events[eid].get("weight", 1) for eid in event_pool]
        event_id = random.choices(event_pool, weights=weights)[0]
        event = self.cog.events[event_id]

        category = event.get("category", "neutral")
        cat_emoji = {"good": "🎁", "neutral": "📖", "bad": "💢"}.get(category, "❓")
        event_message = tf(event, "message", lang) if event.get("message") else t(lang, "explore.mysterious_event", "發生了神祕的事……")
        event_label = t(lang, "explore.random_event_label", "【隨機事件】")
        log = f"{cat_emoji} {event_label}\n{event_message}"

        rewards = event.get("rewards", {})
        if rewards.get("gold"):
            self.cog.adjust_bank(self.user_id, rewards["gold"])
            log += "\n" + t(lang, "explore.gained_gold", "💰 獲得 {gold} {money_name}！", gold=rewards["gold"], money_name=self.cog.bot.baba.money_name)

        for item_id, qty in rewards.get("items", {}).items():
            self.player.inventory[item_id] = self.player.inventory.get(item_id, 0) + qty
            item_name = tf(self.cog.items.get(item_id, {}), "name", lang) or item_id
            log += "\n" + t(lang, "explore.gained_item", "🎁 獲得【{item_name}】x{qty}", item_name=item_name, qty=qty)

        if event.get("hp_loss_percent"):
            loss = max(1, int(self.player.max_hp * event["hp_loss_percent"]))
            self.player.current_hp = max(0, self.player.current_hp - loss)
            log += "\n" + t(lang, "explore.lost_hp", "❤️ 損失 {loss} HP", loss=loss)
            if self.player.current_hp <= 0:
                self.log_message = self.process_death(log, t(lang, "explore.fallen_from_event", "💀 你因事件傷勢過重倒下了！"))
                return

        if event.get("mp_loss_percent"):
            loss_mp = max(1, int(self.player.max_mp * event["mp_loss_percent"]))
            self.player.current_mp = max(0, self.player.current_mp - loss_mp)
            log += "\n" + t(lang, "explore.lost_mp", "💧 流失 {loss_mp} MP", loss_mp=loss_mp)

        if event.get("gold_loss"):
            bal = self.cog.get_bank_balance(self.user_id)
            loss_g = min(bal, event["gold_loss"])
            self.cog.adjust_bank(self.user_id, -loss_g)
            log += "\n" + t(lang, "explore.lost_gold", "💸 損失 {loss_g} {money_name}", loss_g=loss_g, money_name=self.cog.bot.baba.money_name)

        self.log_message = log
        self.cog.save_players()
        self.build_main_menu()

    async def handle_battle_attack(self):
        self.log_message = self.combat.player_attack()
        # 確保砍完重繪戰鬥按鈕
        if self.in_battle:
            self.build_battle_menu()

    async def handle_use_skill(self, skill_id: str):
        lang = self.player.language
        if skill_id not in getattr(self.player, "equipped_skills", []):
            self.log_message = t(lang, "battle.skill_not_equipped", "❌ 你尚未裝備這個技能。")
            if self.in_battle:
                self.build_battle_menu()
            else:
                self.build_main_menu()
            return
            
        self.log_message = self.combat.use_skill(skill_id)
        
        # 確保施放完技能後重繪戰鬥按鈕
        if self.in_battle:
            self.build_battle_menu()

    async def handle_battle_flee(self):
        self.log_message = self.combat.attempt_flee()

    async def handle_skill_menu(self):
        lang = self.player.language
        # 戰鬥中只顯示裝備中的主動技能
        if not getattr(self.player, "equipped_skills", None):
            self.player.equipped_skills = []

        active_skills = [
            s for s in self.player.equipped_skills
            if self.cog.skills.get(s, {}).get("type") != "passive"
        ]
        if not active_skills:
            self.log_message = t(lang, "skill.no_active_skills_equipped", "❌ 你尚未裝備任何可施放的技能！請去教堂進行【技能配置】。")
            return

        self.clear_items()
        self.in_battle = True
        lines = [t(lang, "battle.choose_skill_to_cast", "✨ 選擇要施放的技能：")]
        for skill_id in active_skills:
            skill = self.cog.skills.get(skill_id)
            if not skill:
                continue
            req = skill.get("req_level", 1)
            skill_name = tf(skill, "name", lang)
            skill_desc = tf(skill, "desc", lang)
            req_note = t(lang, "skill.req_level_note", " [需Lv.{req}]", req=req) if req > 1 else ""
            lines.append(f"• {skill_name}{req_note}: {skill_desc}")

            cd_left = self.combat.skill_cds.get(skill_id, 0)

            # 👇 同時判斷 MP 與 HP 消耗並組合顯示字串
            cost_texts = []
            if skill.get("mp_cost"):
                cost_texts.append(f"MP:{skill['mp_cost']}")
            if skill.get("hp_cost_percent"):
                cost_texts.append(f"HP:{int(self.player.max_hp * skill['hp_cost_percent'])}")

            cost_str = " (" + ", ".join(cost_texts) + ")" if cost_texts else ""
            cd_text = f" [CD:{cd_left}]" if cd_left > 0 else ""

            disabled = cd_left > 0 or self.player.level < req

            self.add_action_button(
                label=f"{skill_name}{cost_str}{cd_text}"[:80],
                style=discord.ButtonStyle.secondary if disabled else discord.ButtonStyle.success,
                custom_id=f"skill_{skill_id}",
            )
        self.log_message = "\n".join(lines)
        self.add_action_button(label=t(lang, "battle.btn_back_to_battle", "返回戰鬥"), style=discord.ButtonStyle.secondary, custom_id="btn_back_battle", emoji="🔙")


    async def handle_learn_skill_menu(self, notice=""):
        lang = self.player.language
        self.clear_items()
        scrolls = []
        for item_id, count in self.player.inventory.items():
            if count > 0:
                item = self.cog.items.get(item_id)
                if item and item.get("type") == "skill_scroll":
                    scrolls.append(item_id)

        prefix = notice + "\n\n" if notice else ""
        if not scrolls:
            self.log_message = prefix + t(lang, "skill.learn_menu_no_scrolls", "📖 【學習魔法】\n背包裡沒有技能卷軸。可從商店購買，或討伐區域 BOSS 取得！")
        else:
            self.log_message = prefix + t(lang, "skill.learn_menu_choose_scroll", "📖 【學習魔法】\n選擇要研讀的卷軸（消耗 1 張）：")
            for scroll_id in scrolls:
                item = self.cog.items[scroll_id]
                skill_id = item.get("teaches", "")
                skill = self.cog.skills.get(skill_id, {})
                skill_name = tf(skill, "name", lang) if skill else skill_id
                item_name = tf(item, "name", lang)
                if skill_id in self.player.skills:
                    label = t(lang, "skill.already_learned_label", "已學會：{skill_name}", skill_name=skill_name)
                    btn = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, disabled=True)
                    self.add_item(btn)
                else:
                    self.add_action_button(
                        label=t(lang, "skill.btn_study_scroll", "研讀 {item_name}", item_name=item_name),
                        style=discord.ButtonStyle.primary,
                        custom_id=f"learn_{scroll_id}",
                    )

        self.add_action_button(label=t(lang, "skill.btn_back_to_church", "返回教堂"), style=discord.ButtonStyle.secondary, custom_id="btn_church_menu", emoji="🔙")

    async def handle_skill_equip_menu(self, notice=""):
        lang = self.player.language
        self.clear_items()
        prefix = notice + "\n\n" if notice else ""

        if not getattr(self.player, "equipped_skills", None):
            self.player.equipped_skills = []

        p_skills = [s for s in getattr(self.player, "skills", []) if self.cog.skills.get(s, {}).get("type") != "passive"]

        if not p_skills:
            self.log_message = prefix + t(lang, "skill.equip_menu_no_skills", "🔧 【技能配置】\n你尚未習得任何主動技能。請先【學習魔法】！")
            self.add_action_button(label=t(lang, "skill.btn_back_to_church", "返回教堂"), style=discord.ButtonStyle.secondary, custom_id="btn_church_menu", emoji="🔙")
            return

        equipped_count = len(self.player.equipped_skills)
        self.log_message = prefix + t(lang, "skill.equip_menu_header", "🔧 【技能配置】 (已裝備: {equipped_count}/8)\n點擊下方按鈕來裝備或卸下你的戰鬥技能。", equipped_count=equipped_count)

        for skill_id in p_skills:
            skill = self.cog.skills.get(skill_id, {})
            skill_name = tf(skill, "name", lang) if skill else skill_id

            if skill_id in self.player.equipped_skills:
                self.add_action_button(
                    label=t(lang, "skill.btn_unequip", "🟢 卸下: {skill_name}", skill_name=skill_name),
                    style=discord.ButtonStyle.success,
                    custom_id=f"unequip_skill_{skill_id}"
                )
            else:
                is_full = equipped_count >= 8
                self.add_action_button(
                    label=t(lang, "skill.btn_equip", "⚪ 裝備: {skill_name}", skill_name=skill_name),
                    style=discord.ButtonStyle.secondary if is_full else discord.ButtonStyle.primary,
                    custom_id=f"equip_skill_{skill_id}"
                )

        self.add_action_button(label=t(lang, "skill.btn_back_to_church", "返回教堂"), style=discord.ButtonStyle.secondary, custom_id="btn_church_menu", emoji="🔙")

    async def handle_skill_equip_action(self, skill_id: str, equip: bool):
        lang = self.player.language
        if not getattr(self.player, "equipped_skills", None):
            self.player.equipped_skills = []

        if equip:
            if len(self.player.equipped_skills) >= 8:
                await self.handle_skill_equip_menu(t(lang, "skill.equip_limit_reached", "❌ 技能裝備已達上限 (8/8)！請先卸下其他技能。"))
                return
            if skill_id not in self.player.equipped_skills:
                self.player.equipped_skills.append(skill_id)
                self.cog.save_players()
                skill_name = tf(self.cog.skills.get(skill_id, {}), "name", lang) or skill_id
                await self.handle_skill_equip_menu(t(lang, "skill.equipped_notice", "✅ 已裝備技能：{skill_name}", skill_name=skill_name))
        else:
            if skill_id in self.player.equipped_skills:
                self.player.equipped_skills.remove(skill_id)
                self.cog.save_players()
                skill_name = tf(self.cog.skills.get(skill_id, {}), "name", lang) or skill_id
                await self.handle_skill_equip_menu(t(lang, "skill.unequipped_notice", "✅ 已卸下技能：{skill_name}", skill_name=skill_name))

    async def handle_learn_skill(self, scroll_id: str):
        lang = self.player.language
        item = self.cog.items.get(scroll_id)
        if not item or item.get("type") != "skill_scroll":
            await self.handle_learn_skill_menu(t(lang, "skill.invalid_scroll", "❌ 無效的卷軸。"))
            return

        skill_id = item.get("teaches")
        skill = self.cog.skills.get(skill_id)
        if not skill:
            await self.handle_learn_skill_menu(t(lang, "skill.lost_to_time", "❌ 這卷軸記載的技藝已失傳..."))
            return

        skill_name = tf(skill, "name", lang)
        if skill_id in self.player.skills:
            await self.handle_learn_skill_menu(t(lang, "skill.already_known", "❌ 你已經學會【{skill_name}】了，無需重複研讀。", skill_name=skill_name))
            return

        req_lv = skill.get("req_level", 1)
        if self.player.level < req_lv:
            await self.handle_learn_skill_menu(
                t(lang, "skill.level_too_low_to_learn", "❌ 等級不足！習得【{skill_name}】需要 Lv.{req_lv}。", skill_name=skill_name, req_lv=req_lv)
            )
            return

        if self.player.inventory.get(scroll_id, 0) <= 0:
            await self.handle_learn_skill_menu(t(lang, "skill.no_scroll_in_bag", "❌ 背包裡沒有這張卷軸。"))
            return

        self.player.inventory[scroll_id] -= 1
        if self.player.inventory[scroll_id] <= 0:
            del self.player.inventory[scroll_id]

        self.player.skills.append(skill_id)
        self.cog.save_players()
        item_name = tf(item, "name", lang)
        skill_desc = tf(skill, "desc", lang)
        await self.handle_learn_skill_menu(t(lang, "skill.studied_and_learned", "📖 你研讀了【{item_name}】，成功習得技能【{skill_name}】！\n{skill_desc}", item_name=item_name, skill_name=skill_name, skill_desc=skill_desc))


    async def handle_move_execute(self, custom_id):
        lang = self.player.language
        target_area = custom_id.replace("move_to_", "")
        area_data = self.cog.areas.get(target_area, {})
        req_level = area_data.get("req_level", 1)
        # 現在先不用動因為要測試遊戲
        # if self.player.level < req_level:
        #     self.log_message = f"❌ 等級不足！前往【{area_data.get('area_name', target_area)}】需要 Lv.{req_level}。"
        #     self.build_main_menu()
        #     return
        self.player.current_area = target_area
        self.cog.save_players()
        area_name = tf(self.cog.areas[target_area], "area_name", lang)
        self.log_message = t(lang, "explore.arrived_at_area", "🗺️ 成功抵達了【{area_name}】。", area_name=area_name)
        self.build_main_menu()

    async def handle_status(self, interaction: discord.Interaction):
        p = self.player
        lang = p.language
        none_label = t(lang, "char.none", "無")
        weapon_name = tf(self.cog.items.get(p.weapon, {}), "name", lang) if p.weapon else none_label
        if p.weapon and not weapon_name:
            weapon_name = none_label

        inv_desc = "\n".join([f"• {tf(self.cog.items.get(k, {}), 'name', lang) or k} x{v}" for k, v in p.inventory.items() if v > 0])
        if not inv_desc: inv_desc = t(lang, "char.bag_empty", "空空如也")

        user_bal = self.cog.get_bank_balance(self.user_id)

        status_embed = discord.Embed(title=t(lang, "char.status_title", "📜 {user} 的詳細冒險狀態", user=interaction.user.name), color=discord.Color.blue())
        status_embed.add_field(name=t(lang, "char.level_exp", "等級與經驗"), value=f"Lv.{p.level} (EXP: {p.exp}/{exp_to_next_level(p.level)})", inline=True)
        status_embed.add_field(name=t(lang, "char.wallet_balance", "錢包餘額"), value=f"{user_bal} {self.cog.bot.baba.money_name}", inline=True)

        prestige = getattr(p, "prestige_count", 0)
        if prestige > 0:
            status_embed.add_field(name=t(lang, "char.prestige_rank", "轉生階級"), value=t(lang, "char.prestige_rank_value", "🌟 {prestige} 轉 (全屬性 +{bonus}%)", prestige=prestige, bonus=prestige*10), inline=True)

        trophies = getattr(p, "trophies", [])
        if trophies:
            status_embed.add_field(name=t(lang, "char.trophies", "🏆 榮譽勳章"), value=" ".join(trophies), inline=False)

        alloc_text = format_stat_alloc_summary(p)
        status_embed.add_field(
            name=t(lang, "char.combat_core_stats", "戰鬥核心數值"),
            value=(
                f"❤️ HP: {p.current_hp}/{p.max_hp}\n"
                f"💧 MP: {p.current_mp}/{p.max_mp}\n"
                f"⚔️ ATK: {get_player_atk(p, self.cog.items, self.cog.status_effects)} | 🛡️ DEF: {get_player_def(p, self.cog.items)}\n"
                f"✨ MAG: {get_player_magic(p, self.cog.items, self.cog.status_effects)} | 🔰 RES: {getattr(p, 'base_res', 0)}\n"
                f"{alloc_text}"
            ),
            inline=False,
        )
        status_embed.add_field(name=t(lang, "char.equipped_weapon", "配戴武器"), value=weapon_name, inline=True)
        status_embed.add_field(name=t(lang, "char.status_effects", "異常狀態"), value=format_status_list(p.status_effects, self.cog.status_effects, p.language), inline=True)
        if p.accessory:
            acc_name = tf(self.cog.items.get(p.accessory, {}), "name", lang) or p.accessory
            status_embed.add_field(name=t(lang, "char.accessory", "飾品"), value=acc_name, inline=True)
        skill_list = ", ".join([tf(self.cog.skills.get(s, {}), "name", lang) or s for s in p.skills]) or none_label
        status_embed.add_field(name=t(lang, "char.learned_skills", "已習技能"), value=skill_list, inline=True)
        status_embed.add_field(name=t(lang, "char.bag_contents", "行囊儲存物"), value=inv_desc, inline=False)

        await interaction.followup.send(embed=status_embed, ephemeral=True)

    async def handle_stat_alloc_menu(self, notice=""):
        self.clear_items()
        lang = self.player.language
        prefix = notice + "\n\n" if notice else ""
        unspent = get_unspent_points(self.player)
        self.log_message = (
            prefix
            + t(lang, "char.stat_alloc_header", "📊 【屬性分配】每級 2 點，死亡後重置。\n")
            + format_stat_alloc_summary(self.player)
            + t(lang, "char.stat_alloc_legend", "\n\n攻擊+3 ATK/點 | 體力+12 HP & +2 DEF/點 | 魔力+4 MAG & +3 MP/點 | 速度+2 SPD/點 |抗性+2 RES/點")
        )
        if unspent > 0:
            self.log_message += t(
                lang,
                "char.stat_alloc_unspent",
                "\n\n**您還有 {unspent} 點屬性點可以分配！**\n(💡 點擊「+1」按鈕投資1點，點擊「All-in」投資所有剩餘點數，或點擊「批量分配」填寫數字)",
                unspent=unspent,
            )

            self.add_action_button(label="+1 ATK", style=discord.ButtonStyle.primary, custom_id="stat_add_atk", row=0, emoji="⚔️")
            self.add_action_button(label="+1 VIT", style=discord.ButtonStyle.primary, custom_id="stat_add_vit", row=0, emoji="🛡️")
            self.add_action_button(label="+1 INT", style=discord.ButtonStyle.primary, custom_id="stat_add_int", row=0, emoji="✨")
            self.add_action_button(label="+1 SPD", style=discord.ButtonStyle.primary, custom_id="stat_add_spd", row=0, emoji="💨")
            self.add_action_button(label="+1 RES", style=discord.ButtonStyle.primary, custom_id="stat_add_res", row=0, emoji="🔰")

            self.add_action_button(label="All-in ATK", style=discord.ButtonStyle.danger, custom_id="stat_add_all_atk", row=1, emoji="⚔️")
            self.add_action_button(label="All-in VIT", style=discord.ButtonStyle.danger, custom_id="stat_add_all_vit", row=1, emoji="🛡️")
            self.add_action_button(label="All-in INT", style=discord.ButtonStyle.danger, custom_id="stat_add_all_int", row=1, emoji="✨")
            self.add_action_button(label="All-in SPD", style=discord.ButtonStyle.danger, custom_id="stat_add_all_spd", row=1, emoji="💨")
            self.add_action_button(label="All-in RES", style=discord.ButtonStyle.danger, custom_id="stat_add_all_res", row=1, emoji="🔰")

            self.add_action_button(label=t(lang, "char.btn_bulk_alloc", "批量分配"), style=discord.ButtonStyle.success, custom_id="btn_stat_bulk", row=2, emoji="⌨️")

        self.add_action_button(label=t(lang, "char.btn_reset_stats", "重置所有屬性點"), style=discord.ButtonStyle.danger, custom_id="btn_stat_reset", row=2 if unspent > 0 else 0, emoji="🔄")
        self.add_action_button(label=t(lang, "char.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    async def handle_stat_add(self, stat_key: str, all_in: bool = False):
        from trpg.stats import get_unspent_points, recalc_player_stats, default_stat_alloc
        lang = self.player.language
        unspent = get_unspent_points(self.player)
        if unspent <= 0:
            await self.handle_stat_alloc_menu(t(lang, "char.no_points_left", "❌ 你沒有可用的屬性點了。"))
            return

        if not getattr(self.player, "stat_alloc", None):
            self.player.stat_alloc = default_stat_alloc()

        add_amount = unspent if all_in else 1
        self.player.stat_alloc[stat_key] = self.player.stat_alloc.get(stat_key, 0) + add_amount
        recalc_player_stats(self.player, self.cog.items, heal_full=False)
        self.cog.save_players()
        await self.handle_stat_alloc_menu(t(lang, "char.points_invested", "✅ 已將 {add_amount} 點投入【{stat_key}】。", add_amount=add_amount, stat_key=stat_key.upper()))

    async def handle_stat_reset(self):
        lang = self.player.language
        self.player.stat_alloc = default_stat_alloc()
        recalc_player_stats(self.player, self.cog.items, heal_full=False)
        self.cog.save_players()
        await self.handle_stat_alloc_menu(t(lang, "char.stats_reset_notice", "🔄 已重置所有屬性配點，請重新分配。"))

    async def handle_rest(self):
        lang = self.player.language
        user_bal = self.cog.get_bank_balance(self.user_id)
        if user_bal < 20:
            self.log_message = t(lang, "char.cant_afford_inn", "❌ 你身上的硬幣連旅館的乾草床都租不起！去打怪賺錢！")
            return
        if self.player.current_hp == self.player.max_hp and self.player.current_mp == self.player.max_mp and not self.player.status_effects:
            self.log_message = t(lang, "char.rest_not_needed", "❓ 你精神飽滿，去睡覺只是在浪費錢。")
            return

        self.cog.adjust_bank(self.user_id, -20)
        if not getattr(self.player, "stats", None):
            self.player.stats = {}
        self.player.stats["money_spent"] = self.player.stats.get("money_spent", 0) + 20

        self.player.current_hp = self.player.max_hp
        self.player.current_mp = self.player.max_mp
        clear_all_status(self.player)
        self.log_message = t(lang, "char.rest_complete", "💤 在村莊溫暖的旅店休息了一晚，體力、魔力恢復，異常狀態也清除了！(扣除 20$)")

        achv_text = self.check_achievements()
        if achv_text:
            self.log_message += achv_text

        self.cog.save_players()

    async def handle_tower_explore(self):
        lang = self.player.language
        floor = self.player.tower_floor
        if floor > 99:
            self.log_message = t(lang, "tower.summit_reached", "🏆 你已經登頂魔塔！這裡什麼都沒有了，只剩下無盡的虛空與寂靜。")
            self.build_main_menu()
            return

        # 👇 玩家一進來就先進休息室補滿血，並決定要不要出商人
        if not getattr(self, "tower_safe_room_visited", False):
            self.tower_safe_room_visited = True

            # 滿血回魔
            self.player.current_hp = self.player.max_hp
            self.player.current_mp = self.player.max_mp

            # 10% 機率出商人，若出現則預先抽好商品 (防玩家反覆進出刷新)
            self.tower_merchant_spawned = (random.random() < 0.10)
            if self.tower_merchant_spawned:
                mystery_pool = [k for k, v in self.cog.items.items() if v.get("mystery_only") and v.get("price", 0) > 0]
                self.tower_merchant_items = random.sample(mystery_pool, min(3, len(mystery_pool))) if mystery_pool else []

            self.cog.save_players()
            await self.handle_tower_safe_room()
            return

        # 點擊「挑戰」後，從共用怪物池依樓層抽怪；每 5 層换成該 tier 的 BOSS（與既有里程碑獎勵並存，純粹是額外的難度/外觀層）
        is_boss_floor = (floor % 5 == 0)
        tower_monster = pick_random_monster(self.cog.monster_pool, floor, want_boss=is_boss_floor, floor_scale=1.0)
        tower_monster["is_tower"] = True
        self.start_combat([tower_monster])

        monster_name = tf(tower_monster, "name", lang)
        if is_boss_floor:
            self.log_message = t(
                lang,
                "tower.boss_floor_encounter",
                "🗼 【魔塔第 {floor} 層 - 魔力凝聚！】\n空氣劇烈震動，一股強大的氣息擋住了去路——是這層的首領【{monster_name}】！",
                floor=floor,
                monster_name=monster_name,
            )
        else:
            self.log_message = t(
                lang,
                "tower.floor_encounter",
                "🗼 【魔塔第 {floor} 層】\n空氣越來越稀薄。一隻【{monster_name}】擋住了去路！",
                floor=floor,
                monster_name=monster_name,
            )
        self.build_battle_menu()

    async def handle_tower_safe_room(self, revisit=False):
        lang = self.player.language
        floor = self.player.tower_floor
        self.clear_items()

        msg = t(lang, "tower.safe_room_intro", "🏕️ 【魔塔第 {floor} 層 - 休息區】\n強大的魔力流經你的身體，你的體力與魔力已完全恢復！", floor=floor)
        if getattr(self, "tower_merchant_spawned", False):
            msg += t(lang, "tower.merchant_present", "\n\n🎭 一名披著斗篷的神祕商人正坐在角落，似乎在等你過去。")
            self.add_action_button(label=t(lang, "tower.btn_trade_merchant", "與商人交易"), style=discord.ButtonStyle.primary, custom_id="btn_tower_merchant", emoji="🎭")

        self.log_message = msg if not revisit else self.log_message

        self.add_action_button(label=t(lang, "tower.btn_challenge_floor", "挑戰本層魔物"), style=discord.ButtonStyle.danger, custom_id="btn_tower_next", emoji="⚔️")
        self.add_action_button(label=t(lang, "tower.btn_leave_tower", "離開魔塔"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    async def handle_tower_merchant(self, notice=""):
        self.clear_items()
        self.current_menu_state = "tower_merchant"
        lang = self.player.language
        floor = self.player.tower_floor
        items = getattr(self, "tower_merchant_items", [])

        prefix = notice + "\n\n" if notice else ""
        self.log_message = prefix + t(lang, "tower.merchant_greeting", "🎭 【第 {floor} 層 - 神祕商人】\n「稀有貨色，看看吧，過了這層樓可不一定還能再遇到我。」", floor=floor)
        if not items:
            self.log_message += t(lang, "tower.merchant_no_stock", "\n（他翻了翻行囊，似乎今天沒帶什麼貨。）")
        else:
            for item_id in items:
                item = self.cog.items.get(item_id)
                if not item:
                    continue
                item_name = tf(item, "name", lang)
                self.add_action_button(
                    label=t(lang, "tower.btn_buy_item", "買 {item_name} ({price}$)", item_name=item_name, price=item['price'])[:80],
                    style=discord.ButtonStyle.primary,
                    custom_id=f"buy_{item_id}",
                )

        self.add_action_button(label=t(lang, "char.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_tower_safe_room", emoji="🔙")

    def build_dungeon_menu(self):
        self.clear_items()
        lang = self.player.language
        d_state = self.player.real_player.dungeon_state if hasattr(self.player, 'real_player') else self.player.dungeon_state

        if not d_state.get("in_run"):
            d_state.update({
                "in_run": True,
                "floor": 1,
                "ap": 3,
                "level": 1,
                "exp": 0,
                "max_hp": 60,
                "current_hp": 60,
                "base_atk": 12,
                "base_def": 5,
                "base_spd": 10,
                "base_magic": 0,
                "inventory": {},
                "skills": [],
                "equipped_skills": [],
                "stat_alloc": {"atk":0, "vit":0, "int":0, "spd":0, "res":0},
                "weapon": None,
                "armor": None,
                "accessory": None,
                "choices": []
            })
            if hasattr(self.player, 'real_player'):
                self.player.real_player.dungeon_buffs = {}
            else:
                self.player.dungeon_buffs = {}
            self.log_message = t(lang, "dungeon.intro", "🕳️ **【無盡深淵地下城】**\n這裡有著奇異的規則，你的真實力量已被封印。你將從零開始，依賴這裡獲取的裝備與技能進行挑戰。只有通關或死亡才會結算真實獎勵！")

        floor = d_state.get("floor", 1)
        ap = d_state.get("ap", 3)

        if floor > 15:
            d_state["in_run"] = False
            # 計算獎勵
            real_p = self.player.real_player if hasattr(self.player, 'real_player') else self.player
            reward_gold = real_p.level * 2000
            reward_exp = real_p.level * 1500
            real_p.money += reward_gold
            real_p.exp += reward_exp
            self.log_message = t(
                lang,
                "dungeon.cleared",
                "🎉 你成功通關了地下城第 15 層！\n所有的臨時力量都消散了，但你帶回了豐厚的寶藏：\n💰 獲得 {reward_gold} 金幣\n✨ 獲得 {reward_exp} 經驗值",
                reward_gold=reward_gold,
                reward_exp=reward_exp,
            )
            real_p.current_area = "area_00village"
            self.build_main_menu()
            return

        msg = self.log_message + t(lang, "dungeon.floor_status", "\n\n🏰 **地下城 - 第 {floor}/15 層**\n⏳ 剩餘行動點數 (AP): {ap}\n\n你想要做什麼？", floor=floor, ap=ap)
        self.log_message = msg

        if ap > 0:
            self.add_action_button(label=t(lang, "dungeon.btn_explore_room", "🚪 探索房間 (1 AP)"), style=discord.ButtonStyle.primary, custom_id="btn_dung_explore")
            self.add_action_button(label=t(lang, "dungeon.btn_rest", "🔥 休息 (1 AP)"), style=discord.ButtonStyle.success, custom_id="btn_dung_rest")
            self.add_action_button(label=t(lang, "dungeon.btn_prepare_upgrade", "📊 整備與強化 (1 AP)"), style=discord.ButtonStyle.secondary, custom_id="btn_dung_upgrade")
        else:
            self.add_action_button(label=t(lang, "dungeon.btn_next_floor", "🪜 前往下一層"), style=discord.ButtonStyle.primary, custom_id="btn_dung_next")

        self.add_action_button(label=t(lang, "dungeon.btn_abandon", "放棄探索"), style=discord.ButtonStyle.danger, custom_id="btn_dung_flee", emoji="🏃")

    async def handle_dung_explore(self):
        lang = self.player.language
        d_state = self.player.real_player.dungeon_state if hasattr(self.player, 'real_player') else self.player.dungeon_state
        if d_state.get("ap", 0) <= 0: return
        self.clear_items()
        self.log_message = t(lang, "dungeon.three_doors", "🚪 你來到了三扇門前，你要進入哪一扇？")
        import random
        if not d_state.get("choices"):
            pool = ["monster", "event", "treasure"]
            d_state["choices"] = []
            for _ in range(3):
                room_type = random.choice(pool)
                lbl = t(lang, "dungeon.room_unknown", "未知房間")
                emo = "❓"
                if room_type == "monster": lbl, emo = t(lang, "dungeon.room_monster", "怪物通道"), "👹"
                elif room_type == "treasure": lbl, emo = t(lang, "dungeon.room_treasure", "寶藏房間"), "🎁"
                elif room_type == "event": lbl, emo = t(lang, "dungeon.room_event", "隨機事件"), "🌟"
                d_state["choices"].append({"type": room_type, "label": lbl, "emoji": emo})
            self.cog.save_players()
        for i, ch in enumerate(d_state["choices"]):
            self.add_action_button(label=t(lang, "dungeon.btn_enter_room", "進入 {room_label}", room_label=ch['label']), style=discord.ButtonStyle.primary, custom_id=f"dung_enter_{i}", emoji=ch["emoji"])
        self.add_action_button(label=t(lang, "char.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_back_dungeon")

    async def handle_dung_enter(self, idx: int):
        lang = self.player.language
        d_state = self.player.real_player.dungeon_state if hasattr(self.player, 'real_player') else self.player.dungeon_state
        if d_state.get("ap", 0) <= 0: return
        choices = d_state.get("choices", [])
        if idx >= len(choices): return
        ch = choices[idx]
        d_state["ap"] -= 1
        d_state["choices"] = [] # clear choices after selecting

        floor = d_state.get("floor", 1)
        if ch["type"] in ["monster", "elite", "boss"]:
            from trpg.monster_pool import pick_random_monster
            monster_def = pick_random_monster(self.cog.monster_pool, floor, want_boss=False, floor_scale=1.5)
            monster_display_name = f"💀 {tf(monster_def, 'name', lang)}"
            dungeon_monster = {
                "id": monster_def["id"],
                "name": monster_display_name,
                "is_dungeon": True,
                "max_hp": max(1, int(monster_def["max_hp"] * (1 + floor * 0.15))),
                "atk": max(1, int(monster_def["atk"] * (1 + floor * 0.1))),
                "def": int(monster_def["def"]),
                "spd": int(monster_def["spd"]),
                "exp": int(monster_def.get("exp", 10) * 2.0), # give more exp in roguelike
                "money_min": monster_def.get("money_min", 0),
                "money_max": monster_def.get("money_max", 0),
                "drops": monster_def.get("drops", {})
            }
            self.start_combat([dungeon_monster])
            self.log_message = t(lang, "dungeon.monster_encounter", "⚔️ 遭遇戰鬥！你遇到了 {monster_name}！", monster_name=dungeon_monster['name'])
            self.build_battle_menu()
        elif ch["type"] == "event":
            import random
            event_type = random.choice(["heal", "trap", "chest"])
            if event_type == "heal":
                heal = int(d_state["max_hp"] * 0.5)
                d_state["current_hp"] = min(d_state["max_hp"], d_state.get("current_hp", 0) + heal)
                self.log_message = t(lang, "dungeon.event_heal", "✨ 你發現了一池散發著柔和光芒的泉水。回復了 {heal} 點 HP！", heal=heal)
            elif event_type == "trap":
                dmg = int(d_state["max_hp"] * 0.2)
                d_state["current_hp"] -= dmg
                self.log_message = t(lang, "dungeon.event_trap", "💥 不小心踩到了陷阱！受到了 {dmg} 點傷害！", dmg=dmg)
                if d_state["current_hp"] <= 0:
                    self.log_message += t(lang, "dungeon.death_in_run", "\n💀 你在地下城中喪命了...")
                    d_state["in_run"] = False
                    real_p = self.player.real_player if hasattr(self.player, 'real_player') else self.player
                    real_p.current_area = "area_00village"
                    self.build_main_menu()
                    return
            elif event_type == "chest":
                gold = random.randint(50, 150) * floor
                d_state["inventory"]["gold"] = d_state.get("inventory", {}).get("gold", 0) + gold
                self.log_message = t(lang, "dungeon.event_chest", "🎁 你打開了一個寶箱，獲得了 {gold} 枚臨時金幣！", gold=gold)
            self.build_dungeon_menu()
        else:
            gold = 100 * floor
            d_state["inventory"]["gold"] = d_state.get("inventory", {}).get("gold", 0) + gold
            self.log_message = t(lang, "dungeon.event_chest", "🎁 你打開了一個寶箱，獲得了 {gold} 枚臨時金幣！", gold=gold)
            self.build_dungeon_menu()

    async def handle_dung_rest(self):
        lang = self.player.language
        d_state = self.player.real_player.dungeon_state if hasattr(self.player, 'real_player') else self.player.dungeon_state
        if d_state.get("ap", 0) <= 0: return
        d_state["ap"] -= 1
        heal = int(d_state["max_hp"] * 0.3)
        d_state["current_hp"] = min(d_state["max_hp"], d_state.get("current_hp", 0) + heal)
        self.log_message = t(lang, "dungeon.campfire_rest", "🔥 你升起營火稍作休息，回復了 {heal} 點 HP！", heal=heal)
        self.build_dungeon_menu()

    async def handle_dung_upgrade(self):
        lang = self.player.language
        d_state = self.player.real_player.dungeon_state if hasattr(self.player, 'real_player') else self.player.dungeon_state
        if d_state.get("ap", 0) <= 0: return
        d_state["ap"] -= 1
        points = d_state.get("stat_points", 0)
        self.log_message = t(
            lang,
            "dungeon.prepare_upgrade",
            "📊 **整備與強化**\n你整理了行囊。目前尚有 {points} 點未分配屬性！(提示: 你可以點擊返回並使用一般配點功能，但需在此消耗 1 AP 打開權限)",
            points=points,
        )
        self.build_dungeon_menu()

    async def handle_dung_next(self):
        lang = self.player.language
        d_state = self.player.real_player.dungeon_state if hasattr(self.player, 'real_player') else self.player.dungeon_state
        d_state["floor"] += 1
        d_state["ap"] = 3
        d_state["choices"] = []
        if d_state["floor"] == 15:
            self.log_message = t(lang, "dungeon.boss_floor_arrival", "🪜 你來到了第 {floor} 層... 深處傳來恐怖的咆哮聲，Boss 就在前方！", floor=d_state['floor'])
            # spawn boss immediately
            from trpg.monster_pool import pick_random_monster
            monster_def = pick_random_monster(self.cog.monster_pool, 15, want_boss=True, floor_scale=1.5)
            dungeon_monster = {
                "id": monster_def["id"],
                "name": f"💀 {tf(monster_def, 'name', lang)}",
                "is_dungeon": True,
                "max_hp": int(monster_def["max_hp"] * 1.5),
                "atk": int(monster_def["atk"] * 1.2),
                "def": int(monster_def["def"]),
                "spd": int(monster_def["spd"]),
                "exp": int(monster_def.get("exp", 10) * 2.0),
                "money_min": monster_def.get("money_min", 0),
                "money_max": monster_def.get("money_max", 0),
                "drops": monster_def.get("drops", {})
            }
            self.start_combat([dungeon_monster])
            self.build_battle_menu()
            return
        else:
            self.log_message = t(lang, "dungeon.descend_floor", "🪜 你小心翼翼地走下階梯，來到了第 {floor} 層...", floor=d_state['floor'])
            self.build_dungeon_menu()

    async def handle_dungeon_flee(self):
        lang = self.player.language
        d_state = self.player.real_player.dungeon_state if hasattr(self.player, 'real_player') else self.player.dungeon_state
        d_state["in_run"] = False
        self.log_message = t(lang, "dungeon.flee_notice", "🏃 你帶著遺憾離開了地下城。所有的臨時裝備與經驗都化為烏有了。")
        real_p = self.player.real_player if hasattr(self.player, 'real_player') else self.player
        real_p.current_area = "area_00village"
        self.build_main_menu()

    def build_legend_cave_menu(self):
        self.clear_items()
        lang = self.player.language
        area_data = self.cog.areas.get("area_legend_cave", {})
        nodes = area_data.get("nodes", {})
        cave_state = self.player.cave_state
        node_id = cave_state.get("current_node", "entrance")
        node = nodes.get(node_id) or nodes.get("entrance", {})
        sword_id = area_data.get("sword_item", "hero_sword")

        if node.get("is_sword_room") and not self.player.inventory.get(sword_id, 0):
            self.player.inventory[sword_id] = self.player.inventory.get(sword_id, 0) + 1
            sword_name = tf(self.cog.items.get(sword_id, {}), "name", lang) or sword_id
            self.cog.save_players()
            self.log_message = t(
                lang,
                "cave.sword_obtained",
                "🕯️ {node_desc}\n\n✨ 你從石墩上拔起了【{sword_name}】！這把劍似乎在期待著與魔王的決戰。\n（記得回村莊把它裝備上！）",
                node_desc=tf(node, "desc", lang) or "",
                sword_name=sword_name,
            )
            self.add_action_button(label=t(lang, "cave.btn_leave_cave", "離開洞窟"), style=discord.ButtonStyle.success, custom_id="cave_dir_back", emoji="🚪")
            return

        hp_pct = int(self.player.current_hp / self.player.max_hp * 100) if self.player.max_hp else 0
        self.log_message = t(
            lang,
            "cave.status_display",
            "🕯️ **【傳說洞窟】**\n{node_desc}\n\n❤️ 目前生命值：{current_hp}/{max_hp} ({hp_pct}%)",
            node_desc=tf(node, "desc", lang) or "...",
            current_hp=self.player.current_hp,
            max_hp=self.player.max_hp,
            hp_pct=hp_pct,
        )

        exits = node.get("exits", {})
        if exits.get("forward"):
            self.add_action_button(label=t(lang, "cave.btn_forward", "前進"), style=discord.ButtonStyle.primary, custom_id="cave_dir_forward", emoji="⬆️")
        if exits.get("left"):
            self.add_action_button(label=t(lang, "cave.btn_left", "左邊"), style=discord.ButtonStyle.primary, custom_id="cave_dir_left", emoji="⬅️")
        if exits.get("right"):
            self.add_action_button(label=t(lang, "cave.btn_right", "右邊"), style=discord.ButtonStyle.primary, custom_id="cave_dir_right", emoji="➡️")
        self.add_action_button(label=t(lang, "char.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="cave_dir_back", emoji="🔙")

    async def handle_cave_move(self, direction: str):
        lang = self.player.language
        area_data = self.cog.areas.get("area_legend_cave", {})
        nodes = area_data.get("nodes", {})
        cave_state = self.player.cave_state
        node_id = cave_state.get("current_node", "entrance")
        node = nodes.get(node_id) or nodes.get("entrance", {})
        history = cave_state.setdefault("history", [])

        if direction == "back":
            if node.get("is_sword_room") or not history:
                self.log_message = t(lang, "cave.exit_to_village", "🚪 你退出了傳說洞窟，回到了村莊。")
                self.player.current_area = "area_00village"
                self.cog.save_players()
                self.build_main_menu()
                return
            target_node_id = history.pop()
        else:
            target_node_id = node.get("exits", {}).get(direction)
            if not target_node_id:
                return
            history.append(node_id)

        cost_pct = area_data.get("step_hp_cost_pct", 0.05)
        dmg = max(1, int(self.player.max_hp * cost_pct))
        self.player.current_hp -= dmg
        cave_state["current_node"] = target_node_id
        self.cog.save_players()

        target_node = nodes.get(target_node_id, {})
        if self.player.current_hp <= 0:
            self.log_message = self.process_death(
                t(
                    lang,
                    "cave.death_damage",
                    "🕯️ {node_desc}\n\n💥 濃郁的魔力侵蝕了你最後的生命力，扣除了 {dmg} 點 HP！",
                    node_desc=tf(target_node, "desc", lang) or "",
                    dmg=dmg,
                ),
                t(lang, "cave.death_message", "💀 你倒在了傳說洞窟的深處..."),
            )
            return

        self.build_legend_cave_menu()

    async def handle_prestige_menu(self, notice=""):
        self.clear_items()
        lang = self.player.language
        prefix = notice + "\n\n" if notice else ""

        prestige = getattr(self.player, "prestige_count", 0)
        bonus = prestige * 10

        self.log_message = (
            prefix
            + t(lang, "prestige.hall_title", "🌟 【轉生殿堂】\n")
            + t(lang, "prestige.hall_intro", "在這裡，你可以超越冒險者的極限，重獲新生！\n")
            + t(lang, "prestige.current_level", "• 當前等級：Lv.{level} (轉生需要 Lv.30)\n", level=self.player.level)
            + t(lang, "prestige.current_count", "• 當前轉生次數：{prestige} 轉\n", prestige=prestige)
            + t(lang, "prestige.current_bonus", "• 當前轉生被動增幅：全屬性 +{bonus}%\n\n", bonus=bonus)
            + t(lang, "prestige.rules_title", "⚠️ 【轉生規則說明】\n")
            + t(lang, "prestige.rule_1", "1. 轉生將使你的等級重置回 Lv.1，EXP 歸零，並重置屬性配點。\n")
            + t(lang, "prestige.rule_2", "2. 轉生後你將獲得 1 層永久被動增幅，所有戰鬥屬性額外 +10%！\n")
            + t(lang, "prestige.rule_3", "3. 轉生會卸下你身上的武器、防具與飾品，並重置魔塔／地下城的目前樓層（已達成的里程碑勳章與獎杯不會消失）。\n")
            + t(lang, "prestige.rule_4", "4. 轉生不會清除你的背包道具、金幣與已學會的技能。")
        )

        if self.player.level >= 30:
            self.add_action_button(label=t(lang, "prestige.btn_confirm", "確認轉生 (Lv.30+)"), style=discord.ButtonStyle.danger, custom_id="btn_prestige_confirm", emoji="🌟")
        else:
            disabled_btn = discord.ui.Button(label=t(lang, "prestige.btn_level_locked", "等級不足 Lv.30"), style=discord.ButtonStyle.secondary, disabled=True, emoji="❌")
            self.add_item(disabled_btn)

        self.add_action_button(label=t(lang, "prestige.btn_back_village", "返回村莊"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    async def handle_prestige_confirm(self):
        lang = self.player.language
        if self.player.level < 30:
            self.log_message = t(lang, "prestige.fail_level", "❌ 轉生失敗：你的等級不足 Lv.30！")
            await self.handle_prestige_menu()
            return

        self.player.level = 1
        self.player.exp = 0
        self.player.stat_alloc = default_stat_alloc()
        self.player.prestige_count = getattr(self.player, "prestige_count", 0) + 1

        # 👇 轉生重置：魔塔/地下城回到第一層、清空地下城暫時加成、卸下所有裝備（避免轉生後因殘留裝備直接過強）
        self.player.tower_floor = 1
        self.player.dungeon_state = {"floor": 1, "choices": [], "in_run": False}
        self.player.dungeon_buffs = {}
        self.player.weapon = None
        self.player.armor = None
        self.player.accessory = None
        self.tower_safe_room_visited = False

        recalc_player_stats(self.player, self.cog.items, heal_full=True)
        self.cog.save_players()

        self.log_message = t(
            lang,
            "prestige.success",
            "🌟 恭喜成功轉生！你已重回 Lv.1，並永久獲得 +{bonus}% 的全屬性增幅！\n（魔塔/地下城樓層已重置，武器/防具/飾品已卸下）",
            bonus=self.player.prestige_count * 10,
        )
        self.build_main_menu()

    async def handle_craft_menu(self, notice=""):
        self.clear_items()
        lang = self.player.language
        prefix = notice + "\n\n" if notice else ""

        self.log_message = prefix + t(lang, "craft.title", "🔨 【手藝工坊】\n利用冒險收集的材料合成強力的裝備吧！\n")

        user_bal = self.cog.get_bank_balance(self.user_id)

        for item_id, recipe in CRAFTING_RECIPES.items():
            materials_desc = []
            can_craft = True

            for mat_id, req_qty in recipe["materials"].items():
                mat_name = tf(self.cog.items.get(mat_id, {}), "name", lang) or mat_id
                current_qty = self.player.inventory.get(mat_id, 0)
                materials_desc.append(f"{mat_name} ({current_qty}/{req_qty})")
                if current_qty < req_qty:
                    can_craft = False

            if user_bal < recipe["gold"]:
                can_craft = False

            item_data = self.cog.items.get(item_id, {})
            comp_str = self._get_equipment_comparison_string(item_data)
            comp_suffix = f" {comp_str}" if comp_str else ""

            recipe_name = tf(recipe, "name", lang)
            desc_line = t(
                lang,
                "craft.recipe_line",
                "• **{name}**{comp_suffix} | {gold}$ | 材料: {materials}",
                name=recipe_name,
                comp_suffix=comp_suffix,
                gold=recipe["gold"],
                materials=", ".join(materials_desc),
            )
            self.log_message += f"\n{desc_line}"

            style = discord.ButtonStyle.primary if can_craft else discord.ButtonStyle.secondary

            btn_label = t(lang, "craft.btn_craft", "製作 {name}", name=recipe_name)
            if comp_str:
                btn_label += f" {comp_str}"

            self.add_action_button(
                label=btn_label[:80],
                style=style,
                custom_id=f"craft_{item_id}"
            )

        self.add_action_button(label=t(lang, "menu.btn_back_village", "返回村莊"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    async def handle_craft_execute(self, item_id: str):
        lang = self.player.language
        recipe = CRAFTING_RECIPES.get(item_id)
        if not recipe: return

        user_bal = self.cog.get_bank_balance(self.user_id)
        if user_bal < recipe["gold"]:
            await self.handle_craft_menu(t(lang, "craft.err_no_gold", "❌ 金幣不足！"))
            return

        for mat_id, req_qty in recipe["materials"].items():
            current_qty = self.player.inventory.get(mat_id, 0)
            if current_qty < req_qty:
                await self.handle_craft_menu(t(lang, "craft.err_no_materials", "❌ 材料不足！"))
                return

        # 扣除材料和金幣
        for mat_id, req_qty in recipe["materials"].items():
            self.player.inventory[mat_id] -= req_qty
            if self.player.inventory[mat_id] <= 0:
                del self.player.inventory[mat_id]

        self.cog.adjust_bank(self.user_id, -recipe["gold"])

        if not getattr(self.player, "stats", None):
            self.player.stats = {}
        self.player.stats["money_spent"] = self.player.stats.get("money_spent", 0) + recipe["gold"]

        # 給予物品
        self.player.inventory[item_id] = self.player.inventory.get(item_id, 0) + 1

        # 裝備自動穿戴邏輯
        item_data = self.cog.items.get(item_id, {})
        equip_msg = ""
        if item_data.get("type") == "weapon":
            self.player.weapon = item_id
            equip_msg = t(lang, "craft.auto_equip_weapon", "，已為你自動裝備")
        elif item_data.get("type") == "armor":
            self.player.armor = item_id
            equip_msg = t(lang, "craft.auto_equip_armor", "，已為你自動穿戴")

        recalc_player_stats(self.player, self.cog.items, heal_full=False)

        achv_text = self.check_achievements()
        notice_text = t(
            lang,
            "craft.success",
            "🎉 製作成功！你獲得了【{name}】{equip_msg}！",
            name=tf(recipe, "name", lang),
            equip_msg=equip_msg,
        )
        if achv_text:
            notice_text += achv_text

        self.cog.save_players()
        await self.handle_craft_menu(notice_text)

    async def handle_blacksmith_menu(self, notice=""):
        self.clear_items()
        lang = self.player.language
        prefix = notice + "\n\n" if notice else ""

        p = self.player
        self.log_message = prefix + t(lang, "blacksmith.title", "⚒️ 【鐵匠鋪】\n把你的裝備交給熟練的鐵匠吧！花費金幣與怪物的材料，可以強化武器與防具。\n")

        # 取得目前裝備資訊
        w_id = getattr(p, "weapon", None)
        a_id = getattr(p, "armor", None)

        none_label = t(lang, "blacksmith.none", "無")
        w_name = tf(self.cog.items.get(w_id, {}), "name", lang) if w_id else none_label
        a_name = tf(self.cog.items.get(a_id, {}), "name", lang) if a_id else none_label

        w_up = getattr(p, "weapon_upgrade", 0)
        a_up = getattr(p, "armor_upgrade", 0)

        self.log_message += "\n" + t(lang, "blacksmith.current_weapon", "⚔️ 目前武器：【{name}】", name=w_name) + (f" (+{w_up})" if w_id and w_up > 0 else "")
        self.log_message += "\n" + t(lang, "blacksmith.current_armor", "🛡️ 目前防具：【{name}】", name=a_name) + (f" (+{a_up})" if a_id and a_up > 0 else "")
        self.log_message += "\n"

        user_bal = self.cog.get_bank_balance(self.user_id)

        # 武器強化資訊
        can_up_w = False
        w_desc = t(lang, "blacksmith.cannot_upgrade_no_weapon", "無法強化（未裝備武器）")
        if w_id:
            if w_up >= 5:
                w_desc = t(lang, "blacksmith.max_level", "已達到最高強化等級 (+5)")
            else:
                next_lvl = w_up + 1
                cost = UPGRADE_COSTS[next_lvl]
                mat_name = tf(self.cog.items.get(cost["material"], {}), "name", lang) or cost["material"]
                current_qty = p.inventory.get(cost["material"], 0)

                w_desc = t(
                    lang,
                    "blacksmith.upgrade_info",
                    "升級至 +{next_lvl} | 成功率: {rate_label}\n花費: {gold}$ | 材料: {mat_name} ({current_qty}/{mat_qty})",
                    next_lvl=next_lvl,
                    rate_label=cost["label"],
                    gold=cost["gold"],
                    mat_name=mat_name,
                    current_qty=current_qty,
                    mat_qty=cost["mat_qty"],
                )

                if user_bal >= cost["gold"] and current_qty >= cost["mat_qty"]:
                    can_up_w = True

        self.log_message += "\n" + t(lang, "blacksmith.weapon_upgrade_section", "**武器強化：**\n{desc}\n", desc=w_desc)

        # 防具強化資訊
        can_up_a = False
        a_desc = t(lang, "blacksmith.cannot_upgrade_no_armor", "無法強化（未裝備防具）")
        if a_id:
            if a_up >= 5:
                a_desc = t(lang, "blacksmith.max_level", "已達到最高強化等級 (+5)")
            else:
                next_lvl = a_up + 1
                cost = UPGRADE_COSTS[next_lvl]
                mat_name = tf(self.cog.items.get(cost["material"], {}), "name", lang) or cost["material"]
                current_qty = p.inventory.get(cost["material"], 0)

                a_desc = t(
                    lang,
                    "blacksmith.upgrade_info",
                    "升級至 +{next_lvl} | 成功率: {rate_label}\n花費: {gold}$ | 材料: {mat_name} ({current_qty}/{mat_qty})",
                    next_lvl=next_lvl,
                    rate_label=cost["label"],
                    gold=cost["gold"],
                    mat_name=mat_name,
                    current_qty=current_qty,
                    mat_qty=cost["mat_qty"],
                )

                if user_bal >= cost["gold"] and current_qty >= cost["mat_qty"]:
                    can_up_a = True

        self.log_message += "\n" + t(lang, "blacksmith.armor_upgrade_section", "**防具強化：**\n{desc}\n", desc=a_desc)

        # 按鈕
        w_style = discord.ButtonStyle.primary if can_up_w else discord.ButtonStyle.secondary
        w_label = t(lang, "blacksmith.btn_upgrade_weapon", "強化武器")
        if w_id and w_up < 5:
            w_label += " [⚔️ATK+3▲]"
        self.add_action_button(
            label=w_label,
            style=w_style,
            custom_id="btn_upgrade_weapon" if can_up_w else "btn_disabled_w"
        )

        a_style = discord.ButtonStyle.primary if can_up_a else discord.ButtonStyle.secondary
        a_label = t(lang, "blacksmith.btn_upgrade_armor", "強化防具")
        if a_id and a_up < 5:
            a_label += " [🛡️DEF+2▲ ❤️HP+15▲]"
        self.add_action_button(
            label=a_label,
            style=a_style,
            custom_id="btn_upgrade_armor" if can_up_a else "btn_disabled_a"
        )

        self.add_action_button(label=t(lang, "menu.btn_back_village", "返回村莊"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    async def handle_upgrade_execute(self, is_weapon: bool):
        p = self.player
        lang = p.language
        slot = "weapon" if is_weapon else "armor"
        item_id = getattr(p, slot, None)
        if not item_id:
            await self.handle_blacksmith_menu(t(lang, "blacksmith.err_no_equipment", "❌ 你沒有裝備任何對應的裝備！"))
            return

        current_up = getattr(p, f"{slot}_upgrade", 0)
        if current_up >= 5:
            await self.handle_blacksmith_menu(t(lang, "blacksmith.err_max_level", "❌ 該裝備已達到最高強化等級 (+5)！"))
            return

        next_lvl = current_up + 1
        cost = UPGRADE_COSTS[next_lvl]

        user_bal = self.cog.get_bank_balance(self.user_id)
        if user_bal < cost["gold"]:
            await self.handle_blacksmith_menu(t(lang, "blacksmith.err_no_gold", "❌ 金幣不足！"))
            return

        current_qty = p.inventory.get(cost["material"], 0)
        if current_qty < cost["mat_qty"]:
            await self.handle_blacksmith_menu(t(lang, "blacksmith.err_no_materials", "❌ 強化材料不足！"))
            return

        # 扣除材料和金幣
        p.inventory[cost["material"]] -= cost["mat_qty"]
        if p.inventory[cost["material"]] <= 0:
            del p.inventory[cost["material"]]

        self.cog.adjust_bank(self.user_id, -cost["gold"])

        # 強化此時將金幣花費計入 money_spent
        if not getattr(p, "stats", None):
            p.stats = {}
        p.stats["money_spent"] = p.stats.get("money_spent", 0) + cost["gold"]

        # 強化判定
        success = (random.random() < cost["rate"])

        if success:
            setattr(p, f"{slot}_upgrade", next_lvl)
            recalc_player_stats(p, self.cog.items, heal_full=False)

            achv_text = self.check_achievements()
            item_name = tf(self.cog.items.get(item_id, {}), "name", lang) or item_id
            notice_text = t(
                lang,
                "blacksmith.upgrade_success",
                "✨ 🌟 強化成功！\n你的【{name}】成功強化至 **+{next_lvl}**！",
                name=item_name,
                next_lvl=next_lvl,
            )
            if achv_text:
                notice_text += achv_text

            self.cog.save_players()
            await self.handle_blacksmith_menu(notice_text)
        else:
            achv_text = self.check_achievements()
            notice_text = t(
                lang,
                "blacksmith.upgrade_fail",
                "💥 強化失敗！\n材料被熔毀了，但是鐵匠拼命保住了你的裝備，等級維持在 **+{current_up}**。",
                current_up=current_up,
            )
            if achv_text:
                notice_text += achv_text

            self.cog.save_players()
            await self.handle_blacksmith_menu(notice_text)

    def build_leaderboard_embed(self) -> discord.Embed:
        lang = self.player.language
        players = list(self.cog.players.values())
        no_data = t(lang, "leaderboard.no_data", "無資料")

        # 1. 等級排行
        lvl_rank = sorted(players, key=lambda x: x.level, reverse=True)[:5]
        lvl_desc = "\n".join([f"🏆 **Rank {i+1}** | Lv.{p.level} - <@{p.id}>" for i, p in enumerate(lvl_rank)])
        if not lvl_desc: lvl_desc = no_data

        # 2. 魔塔排行
        tower_rank = sorted(players, key=lambda x: getattr(x, "tower_floor", 1), reverse=True)[:5]
        tower_desc = "\n".join([
            t(lang, "leaderboard.tower_rank_line", "🏆 **Rank {rank}** | {floor}層 - <@{uid}>", rank=i + 1, floor=getattr(p, 'tower_floor', 1), uid=p.id)
            for i, p in enumerate(tower_rank)
        ])
        if not tower_desc: tower_desc = no_data

        # 3. 擊殺排行
        kill_rank = sorted(players, key=lambda x: x.stats.get("monsters_killed", 0) if getattr(x, "stats", None) else 0, reverse=True)[:5]
        kill_desc = "\n".join([
            t(
                lang,
                "leaderboard.kill_rank_line",
                "🏆 **Rank {rank}** | {count}隻 - <@{uid}>",
                rank=i + 1,
                count=p.stats.get('monsters_killed', 0) if getattr(p, 'stats', None) else 0,
                uid=p.id,
            )
            for i, p in enumerate(kill_rank)
        ])
        if not kill_desc: kill_desc = no_data

        # 4. 金幣排行
        bank_rank = []
        for p in players:
            bal = self.cog.get_bank_balance(p.id)
            bank_rank.append((p.id, bal))
        bank_rank = sorted(bank_rank, key=lambda x: x[1], reverse=True)[:5]
        bank_desc = "\n".join([f"🏆 **Rank {i+1}** | {bal}$ - <@{uid}>" for i, (uid, bal) in enumerate(bank_rank)])
        if not bank_desc: bank_desc = no_data

        embed = discord.Embed(title=t(lang, "leaderboard.embed_title", "🏆 【皇家冒險者公會 - 全服英雄榜】"), color=discord.Color.gold())
        embed.description = t(lang, "leaderboard.embed_desc", "冒險者們的傳奇戰績已被記錄於此。不斷前進，刻下你的名字吧！")
        embed.add_field(name=t(lang, "leaderboard.field_level", "🎖️ 等級最高殿堂"), value=lvl_desc, inline=False)
        embed.add_field(name=t(lang, "leaderboard.field_tower", "🗼 魔塔最高登頂層數"), value=tower_desc, inline=False)
        embed.add_field(name=t(lang, "leaderboard.field_kills", "⚔️ 累計討伐魔物數量"), value=kill_desc, inline=False)
        embed.add_field(name=t(lang, "leaderboard.field_gold", "💰 冒險財富榜"), value=bank_desc, inline=False)
        return embed

    async def handle_leaderboard(self):
        lang = self.player.language
        self.viewing_leaderboard = True
        self.clear_items()
        self.add_action_button(label=t(lang, "menu.btn_back_village", "返回村莊"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    def record_combat_history(self, log_msg: str):
        if not hasattr(self.player, "combat_history"):
            self.player.combat_history = []
        self.player.combat_history.insert(0, log_msg)
        self.player.combat_history = self.player.combat_history[:3]

    async def handle_combat_history(self, interaction: discord.Interaction):
        lang = self.player.language
        history = getattr(self.player, "combat_history", [])
        if not history:
            await interaction.followup.send(t(lang, "leaderboard.no_history", "📝 目前沒有任何戰鬥記錄。"), ephemeral=True)
            return

        embed = discord.Embed(title=t(lang, "leaderboard.history_embed_title", "📜 最近 3 次冒險戰鬥記錄"), color=discord.Color.blue())
        for i, log_entry in enumerate(history):
            snippet = log_entry.strip()
            # remove excessive empty lines to keep it clean
            snippet = "\n".join([line for line in snippet.splitlines() if line.strip()])
            field_name = t(lang, "leaderboard.history_field_name", "戰績 #{n}", n=i + 1)
            embed.add_field(name=field_name, value=f"```\n{snippet[:1000]}\n```", inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    async def re_render_current_menu(self):
        state = getattr(self, "current_menu_state", "main")
        if state == "equip":
            await self.handle_equip_menu(paging=True)
        elif state == "sell":
            await self.handle_sell_menu(paging=True)
        elif state == "item":
            await self.handle_item_menu(paging=True)
        else:
            self.build_main_menu()

    
