"""TRPGGameView — the button-driven adventure panel and all menu/combat handlers."""

import discord
import random
from datetime import datetime

from trpg.i18n import t, tf
from trpg.combat import TRPGCombat, exp_to_next_level, get_player_atk, get_player_def, get_player_magic, get_player_spd
from trpg.status import format_status_list, clear_all_status, get_daily_jester_immunity
from trpg.monster_pool import pick_random_monster
from trpg.quest_popup import process_quest_popups
from trpg.stats import default_stat_alloc, recalc_player_stats, get_unspent_points, format_stat_alloc_summary, get_potion_heal_target, prestige_required_level
from trpg.player import RoguePlayerWrapper
from trpg.entity import absorb_monster_damage
from trpg import dungeon as dg
from trpg.balance import PRESTIGE_LEVEL_STEP
from trpg.modals import ElderChiefModal, BuyItemModal, SellItemModal, StatPointModal
from trpg.view_shared import ITEM_TYPE_EMOJI, item_emoji, EQUIP_STAT_DISPLAY, ELEMENT_DISPLAY
from trpg.view_shop import ShopMixin
from trpg.view_dungeon import DungeonMixin
from trpg.view_tutorial import TutorialMixin


class TRPGGameView(discord.ui.View, ShopMixin, DungeonMixin, TutorialMixin):
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
        # 教學戰狀態（只在記憶體中，不落存檔）：是否正在教學戰、目前示範到第幾個提示。
        self.in_tutorial_battle = False
        self.tutorial_step = 0

        self.log_message = t(self.player.language, "menu.welcome", "歡迎來到冒險世界！請使用下方按鈕進行探索。")
        self.inventory_page = 0
        self.current_menu_state = "main"
        self.viewing_leaderboard = False

        # 👇 若上次面板關閉/逾時時還有一場沒打完的戰鬥，接回去，而不是讓 /trpg 變成免費逃跑。
        real_player = getattr(self.player, "real_player", self.player)
        pending_battle = getattr(real_player, "active_battle", None)
        if pending_battle and pending_battle.get("monster_slots"):
            self.monster_slots = pending_battle["monster_slots"]
            self.combat.player_av = pending_battle.get("player_av", 100)
            self.combat.skill_cds = pending_battle.get("skill_cds", {})
            self.combat.is_defending = pending_battle.get("is_defending", False)
            self.combat.is_dodging = pending_battle.get("is_dodging", False)
            self.log_message = t(self.player.language, "menu.battle_restored", "⚔️ 你回到了先前未完成的戰鬥，敵人依然虎視眈眈！")
            self.build_battle_menu()
        elif not getattr(real_player, "onboarding_done", False):
            # 真正的第一次登入：先選語言，再問要不要教學戰，而不是直接丟進主選單。
            self.build_language_select_menu()
        else:
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
            # 聖盾/無實體/傷害上限這些頭目防禦機制在 absorb_monster_damage 統一結算
            front["hp"] = max(0, absorb_monster_damage(front, value))

    def start_combat(self, monster_defs: list):
        """所有戰鬥的單一入口：最多吃 3 隻怪物，組成 monster_slots。絕大多數戰鬥仍只傳 1 隻怪物。
        monster 定義帶 start_divine_shield 的話，開場就自帶一層聖盾（完全抵銷下一次傷害）。"""
        self.monster_slots = [
            {"monster": dict(m), "hp": m["max_hp"], "av": 0, "status": {},
             "divine_shield": bool(m.get("start_divine_shield"))}
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
                timeout_notice = t(lang, "menu.timeout_notice", "⌛ 此冒險面板已因超時（10分鐘未操作）而關閉並自動存檔。\n請重新輸入 `/trpg` 來繼續冒險！")
                embed.description = f"```\n{timeout_notice}\n```"
                await self.message.edit(embed=embed, view=self)
            except Exception:
                pass

    # 🛠️ 修復核心：自訂一個按鈕產生器，確實綁定 callback！
    def add_action_button(self, label, style, custom_id, row=None, emoji=None, disabled=False):
        # 防呆：同一個畫面若不小心產生重複 custom_id（例如資料重複），Discord 會回 400。
        # 這裡直接略過重複的按鈕，避免整個面板更新失敗。
        for child in self.children:
            if getattr(child, "custom_id", None) == custom_id:
                return

        btn = discord.ui.Button(label=label, style=style, custom_id=custom_id, row=row, emoji=emoji, disabled=disabled)

        # 捕捉這個按鈕專屬的 custom_id 並轉交給全域處理函數
        async def callback(interaction: discord.Interaction):
            await self.global_callback(interaction, custom_id)

        if not disabled:
            btn.callback = callback  # 這行就是上次漏掉的靈魂
        self.add_item(btn)

    def add_action_select(self, placeholder, options, row=None, custom_id=None):
        """下拉選單版的 add_action_button：一排最多 5 顆按鈕的版面塞不下 20+ 件裝備/
        技能，改用下拉選單一格就能容納 25 個選項。每個選項的 value 直接沿用既有按鈕
        的 custom_id（equip_xxx / use_item_xxx / craft_xxx...），選中後轉交
        global_callback——所有既有路由、等級檢查、彈窗邏輯完全複用，不用寫第二套。

        options: [(label, value_custom_id, description, emoji), ...]，超過 25 個只取前 25
        （呼叫端應先用 _paginate 以 25/頁分頁）。"""
        opts = []
        for label, value, desc, emoji in options[:25]:
            opts.append(discord.SelectOption(
                label=str(label)[:100], value=str(value)[:100],
                description=(str(desc)[:100] or None) if desc else None,
                emoji=emoji or None,
            ))
        if not opts:
            return
        select = discord.ui.Select(placeholder=str(placeholder)[:150], options=opts, row=row,
                                   min_values=1, max_values=1, custom_id=custom_id or f"sel_{len(self.children)}")

        async def callback(interaction: discord.Interaction):
            await self.global_callback(interaction, select.values[0])

        select.callback = callback
        self.add_item(select)

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
        "btn_quest_hall": {"m": "handle_quest_hall"},
        "btn_area_npc": {"m": "handle_area_npc"},
        "btn_lottery_menu": {"m": "handle_lottery_menu"},
        "btn_lottery_draw": {"m": "handle_lottery_draw"},
        "btn_daily_claim": {"m": "handle_daily_claim"},
        "btn_achievements": {"m": "handle_achievements", "await": False},
        "btn_combat_history": {"m": "handle_combat_history", "i": True},
        "btn_rest": {"m": "handle_rest"},
        "btn_shop_menu": {"m": "handle_shop_menu"},
        "btn_shop_refresh": {"m": "handle_shop_refresh"},
        "btn_shop_sell": {"m": "handle_sell_menu"},
        "btn_equip_menu": {"m": "handle_equip_menu"},
        "btn_dung_next": {"m": "handle_dung_next"},
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
        "btn_colo_fight": {"m": "handle_colosseum_fight"},
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
        "btn_tutorial_yes": {"m": "handle_tutorial_choice", "i": True, "k": {"want_tutorial": True}},
        "btn_tutorial_no": {"m": "handle_tutorial_choice", "i": True, "k": {"want_tutorial": False}},
    }

    # Prefix routes — checked in order, MOST SPECIFIC FIRST. `arg` controls what
    # gets passed: "suffix" = custom_id after the prefix, "full" = whole
    # custom_id, "int_tail" = int of the last underscore segment.
    _PREFIX_ROUTES = [
        ("btn_lang_", {"m": "handle_lang_select", "arg": "suffix"}),
        ("move_to_", {"m": "handle_move_execute", "arg": "full"}),
        ("darch_", {"m": "handle_dung_archetype", "arg": "suffix"}),
        ("ddoor_", {"m": "handle_dung_door", "arg": "suffix"}),
        ("dpick_", {"m": "handle_dung_loot_pick", "arg": "suffix"}),
        ("drelic_", {"m": "handle_dung_relic_pick", "arg": "suffix"}),
        ("dskill_", {"m": "handle_dung_skill_pick", "arg": "suffix"}),
        ("equip_skill_", {"m": "handle_skill_equip_action", "arg": "suffix", "k": {"equip": True}}),
        ("unequip_skill_", {"m": "handle_skill_equip_action", "arg": "suffix", "k": {"equip": False}}),
        ("equip_", {"m": "handle_equip_action", "arg": "suffix", "k": {"equip": True}}),
        ("unequip_", {"m": "handle_equip_action", "arg": "suffix", "k": {"equip": False}}),
        ("acc_", {"m": "handle_accessory_action", "arg": "suffix", "k": {"equip": True}}),
        ("unacc_", {"m": "handle_accessory_action", "arg": "suffix", "k": {"equip": False}}),
        ("learn_", {"m": "handle_learn_skill", "arg": "suffix"}),
        ("qaccept_", {"m": "handle_quest_accept", "arg": "suffix"}),
        ("stat_add_", {"m": "handle_stat_add", "arg": "suffix"}),
        ("craft_", {"m": "handle_craft_execute", "arg": "suffix"}),
        ("skill_", {"m": "handle_use_skill", "arg": "suffix"}),
        ("use_item_", {"m": "handle_use_item", "arg": "full"}),
    ]

    # custom_ids that represent an actual adventuring moment (explore/travel/fight/rest),
    # as opposed to browsing a menu (shop, equip screen, achievements, stat allocation...).
    # NPC quest-offer popups only get a chance to fire on these — otherwise every single
    # click anywhere in the UI had a 15% shot at interrupting the player with an offer,
    # which felt like menu-browsing was randomly ambushed by unrelated popups.
    _WORLD_ACTION_IDS = {
        "btn_explore", "btn_boss_explore", "btn_rest",
        "btn_tower_next", "btn_dung_next", "btn_dung_flee", "btn_colo_fight",
        "cave_dir_forward", "cave_dir_left", "cave_dir_right", "cave_dir_back",
        "b_atk", "b_def", "b_dod", "b_fle",
    }
    _WORLD_ACTION_PREFIXES = ("move_to_", "skill_", "use_item_")

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
        if custom_id.startswith("stat_manual_"):
            stat_key = custom_id[len("stat_manual_"):]
            return await interaction.response.send_modal(StatPointModal(self, stat_key, stat_key.upper()))

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
            # 👇 修正：防禦/閃避這一回合裡，怪物可能因為腐蝕/中毒等 DOT 在牠自己的
            # 行動點死掉，戰鬥當場結束並且已經切到戰利品/遺物選單——這時絕對不能
            # 再無條件蓋回戰鬥選單，要跟攻擊/技能一樣先看 in_battle 是否還為真。
            if self.in_battle:
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

        # 👇 每次互動後同步戰鬥快照：in_battle 就存檔，戰鬥結束就清掉。
        # 這是唯一能防止玩家靠重打 /trpg 或晾著面板逾時來免費逃跑的地方——
        # 所有戰鬥中的按鈕互動最終都會走到這裡。
        real_player = getattr(self.player, "real_player", self.player)
        if getattr(self, "in_battle", False) and self.monster_slots:
            real_player.active_battle = {
                "monster_slots": self.monster_slots,
                "player_av": self.combat.player_av,
                "skill_cds": self.combat.skill_cds,
                "is_defending": self.combat.is_defending,
                "is_dodging": self.combat.is_dodging,
            }
        else:
            real_player.active_battle = None
        self.cog.save_players()

        # 先即時更新冒險面板，讓玩家動作立刻有回饋。
        # （任務彈窗可能會呼叫 LLM 產生 NPC 對話，若放在更新畫面前會讓殺怪後卡一下）
        try:
            await interaction.message.edit(embed=self.generate_embed(), view=self)
        except Exception as e:
            print(f"UI更新失敗: {e}")

        # 教學戰進行中：提示彈窗取代任務彈窗（新玩家還沒有任何任務可觸發，也不該被
        # 突發委託打斷第一場戰鬥）。教學戰以外的一切互動仍走原本的任務彈窗流程。
        if getattr(self, "in_tutorial_battle", False):
            try:
                await self._maybe_send_tutorial_tip(interaction)
            except Exception as e:
                print(f"教學提示處理失敗: {e}")
            return

        # 任務彈出視窗（達成獎勵 / 突發委託邀請）— 放在面板更新之後，AI 對話的延遲不再卡住主畫面。
        # 達成獎勵永遠檢查（不管這次點的是什麼），但「突發委託邀請」只在真的做了冒險相關的
        # 動作時才有機會彈出，避免玩家單純逛商店/看成就都會被隨機委託打斷。
        is_world_action = (
            custom_id in self._WORLD_ACTION_IDS
            or custom_id.startswith(self._WORLD_ACTION_PREFIXES)
        )
        try:
            await process_quest_popups(self, interaction, allow_offer=is_world_action)
            # 任務獎勵可能改變了等級／金幣，完成後再刷新一次讓數值同步
            await interaction.message.edit(embed=self.generate_embed(), view=self)
        except Exception as e:
            print(f"任務彈出視窗處理失敗: {e}")

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

        if self.cog.areas.get(self.player.current_area, {}).get("is_colosseum"):
            self.build_colosseum_menu()
            return

        lang = self.player.language
        if self.cog.areas.get(self.player.current_area, {}).get("is_village"):
            self.add_action_button(label=t(lang, "menu.btn_outskirts", "郊外"), style=discord.ButtonStyle.primary, custom_id="move_to_area_01grassland", row=0, emoji="🌾")
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
            # 👇 魔塔已經有自己的休息室商人，不重複給一般商店按鈕
            if self.player.current_area != "area_tower":
                self.add_action_button(label=t(lang, "shop.btn_shop", "商店"), style=discord.ButtonStyle.primary, custom_id="btn_shop_menu", row=1, emoji="🛒")
            today_str = datetime.today().strftime("%Y-%m-%d")
            boss_done_today = self.player.daily_boss_kills.get(self.player.current_area) == today_str
            if boss_done_today:
                self.add_action_button(label=t(lang, "menu.btn_area_boss_done", "✅ 已討伐"), style=discord.ButtonStyle.secondary, custom_id="btn_boss_explore", row=1, emoji="👹", disabled=True)
            else:
                self.add_action_button(label=t(lang, "menu.btn_area_boss", "區域BOSS"), style=discord.ButtonStyle.danger, custom_id="btn_boss_explore", row=1, emoji="👹")
            # 👇 郊外是新手村的直接鄰接區域，給一顆直達按鈕互通，不用繞去移動選單裡找。
            if self.player.current_area == "area_01grassland":
                self.add_action_button(label=t(lang, "menu.btn_back_village", "返回新手村"), style=discord.ButtonStyle.secondary, custom_id="move_to_area_00village", row=2, emoji="🏠")
            # 👇 主線 NPC：區域 JSON 有定義 npc 的話，主選單多一顆拜訪按鈕（精靈長老/亡靈學者/龍語者）。
            area_npc = self.cog.areas.get(self.player.current_area, {}).get("npc")
            if area_npc:
                npc_name = tf(area_npc, "name", lang) or "NPC"
                self.add_action_button(label=npc_name[:80], style=discord.ButtonStyle.success, custom_id="btn_area_npc", row=2, emoji=area_npc.get("emoji") or "🧑")

    def build_artisan_menu(self):
        self.clear_items()
        lang = self.player.language
        self.log_message = t(lang, "menu.artisan_prompt", "⚒️ 【鐵匠之地】挑選你要去的地方：")
        self.add_action_button(label=t(lang, "menu.btn_blacksmith_shop", "鐵匠鋪"), style=discord.ButtonStyle.primary, custom_id="btn_blacksmith_menu", emoji="⚒️")
        self.add_action_button(label=t(lang, "menu.btn_craft_workshop", "手藝工坊"), style=discord.ButtonStyle.primary, custom_id="btn_craft_menu", emoji="🔨")
        self.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    def _daily_claimed_today(self) -> bool:
        baba = getattr(self.cog.bot, "baba", None)
        if baba is None or not hasattr(baba, "daily_claims"):
            return False
        today_str = datetime.today().strftime("%Y-%m-%d")
        return baba.daily_claims.get(int(self.user_id)) == today_str

    def build_guild_menu(self):
        self.clear_items()
        lang = self.player.language
        self.log_message = t(lang, "menu.guild_prompt", "🏛️ 【冒險者公會】\n請選擇你要進行的公會服務：")
        if self._daily_claimed_today():
            self.add_action_button(label=t(lang, "menu.btn_daily_claimed", "✅ 已簽到"), style=discord.ButtonStyle.secondary, custom_id="btn_daily_claim", emoji="🎁", disabled=True)
        else:
            self.add_action_button(label=t(lang, "menu.btn_daily", "每日簽到"), style=discord.ButtonStyle.success, custom_id="btn_daily_claim", emoji="🎁")
        self.add_action_button(label=t(lang, "menu.btn_quest_hall", "任務大廳"), style=discord.ButtonStyle.success, custom_id="btn_quest_hall", emoji="📜")
        self.add_action_button(label=t(lang, "menu.btn_lottery", "幸運抽獎"), style=discord.ButtonStyle.primary, custom_id="btn_lottery_menu", emoji="🎰")
        self.add_action_button(label=t(lang, "menu.btn_achievements", "成就"), style=discord.ButtonStyle.primary, custom_id="btn_achievements", emoji="🏅")
        self.add_action_button(label=t(lang, "menu.btn_leaderboard", "排行榜"), style=discord.ButtonStyle.secondary, custom_id="btn_leaderboard", emoji="🏆")
        self.add_action_button(label=t(lang, "menu.btn_stat_alloc", "屬性分配"), style=discord.ButtonStyle.primary, custom_id="btn_stat_alloc", emoji="📊")
        self.add_action_button(label=t(lang, "menu.btn_combat_history", "戰鬥記錄"), style=discord.ButtonStyle.secondary, custom_id="btn_combat_history", emoji="📝")
        self.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    # 🎰 公會幸運抽獎：後期金幣回收管道。獎池刻意「期望值小虧」（約回本 7 成），
    # 但有小機率抽到卷軸/稀有飾品/大獎金幣，運氣屬性會微幅提高稀有獎項的權重，
    # 讓 LUCK 流玩家多一個發揮的地方。
    LOTTERY_COST = 800

    def _lottery_prize_pool(self):
        """回傳 [(weight, kind, payload), ...]。物品獎項在抽中時才 roll 具體內容。"""
        scroll_ids = [iid for iid, it in self.cog.items.items()
                      if it.get("type") == "skill_scroll" and it.get("shop_weight", 0) > 0]
        luck = getattr(getattr(self.player, "real_player", self.player), "base_luck", 0)
        luck_bonus = min(3.0, luck * 0.05)
        return [
            (34.0, "items", [("high_health_potion", 3), ("high_mana_potion", 3)]),
            (20.0, "items", [("ancient_wood", 3), ("ectoplasm", 3), ("bone_shard", 3), ("gargoyle_stone", 2)]),
            (15.0, "gold", (300, 1000)),
            (12.0, "scroll", scroll_ids),
            (9.0, "items", [("mystery_elixir", 1), ("elixir_of_cleansing", 2)]),
            (6.0 + luck_bonus, "gold", (5000, 5000)),
            (4.0 + luck_bonus, "items", [("lucky_clover_charm", 1), ("schrodinger_watch", 1)]),
        ]

    async def handle_lottery_menu(self, notice=""):
        self.clear_items()
        lang = self.player.language
        prefix = notice + "\n\n" if notice else ""
        balance = self.cog.get_bank_balance(self.user_id)
        self.log_message = prefix + t(
            lang, "lottery.menu_text",
            "🎰 【公會幸運抽獎】\n公會接待員眨眨眼：「試試手氣嗎？每抽 {cost} 金幣，獎品從藥水、材料、"
            "技能卷軸到稀有飾品都有可能——聽說運氣好的人更容易抽到大獎喔。」\n\n💰 目前餘額：{balance} 金幣",
            cost=self.LOTTERY_COST, balance=balance,
        )
        can_draw = balance >= self.LOTTERY_COST
        self.add_action_button(
            label=t(lang, "lottery.btn_draw", "抽一次（{cost}$）", cost=self.LOTTERY_COST),
            style=discord.ButtonStyle.success if can_draw else discord.ButtonStyle.secondary,
            custom_id="btn_lottery_draw", emoji="🎰", disabled=not can_draw,
        )
        self.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_guild_menu", emoji="🔙")

    async def handle_lottery_draw(self):
        lang = self.player.language
        if not self.cog.try_spend(self.user_id, self.player, self.LOTTERY_COST):
            await self.handle_lottery_menu(t(lang, "lottery.no_gold", "❌ 金幣不足，湊滿 {cost} 金幣再來試手氣吧！", cost=self.LOTTERY_COST))
            return

        pool = self._lottery_prize_pool()
        weights = [w for w, _, _ in pool]
        _, kind, payload = random.choices(pool, weights=weights)[0]
        real = getattr(self.player, "real_player", self.player)

        if kind == "gold":
            lo, hi = payload
            amount = random.randint(lo, hi)
            self.cog.adjust_bank(self.user_id, amount)
            if amount >= 5000:
                notice = t(lang, "lottery.win_jackpot", "🎆 【頭獎】獎池的鈴鐺瘋狂作響——你抽中了 {amount} 金幣大獎！！", amount=amount)
            else:
                notice = t(lang, "lottery.win_gold", "🪙 抽中了 {amount} 金幣！", amount=amount)
        elif kind == "scroll" and payload:
            scroll_id = random.choice(payload)
            real.inventory[scroll_id] = real.inventory.get(scroll_id, 0) + 1
            name = tf(self.cog.items.get(scroll_id, {}), "name", lang) or scroll_id
            notice = t(lang, "lottery.win_scroll", "📜 【稀有】抽中了技能卷軸：【{name}】！", name=name)
        else:
            item_id, qty = random.choice(payload if kind == "items" else [("health_potion", 3)])
            real.inventory[item_id] = real.inventory.get(item_id, 0) + qty
            name = tf(self.cog.items.get(item_id, {}), "name", lang) or item_id
            notice = t(lang, "lottery.win_items", "🎁 抽中了【{name}】x{qty}！", name=name, qty=qty)

        self.cog.save_players()
        await self.handle_lottery_menu(notice)

    async def handle_daily_claim(self):
        lang = self.player.language
        baba = getattr(self.cog.bot, "baba", None)
        if baba is None or not hasattr(baba, "claim_daily"):
            self.build_guild_menu()
            return
        ok, reward, total = baba.claim_daily(self.user_id)
        self.build_guild_menu()  # 重建公會選單，讓已簽到按鈕立刻反映新狀態，再覆蓋成簽到結果訊息
        if ok:
            self.log_message = t(lang, "menu.daily_claimed", "🎁 每日簽到成功！獲得 {reward} {money}，目前共有 {total}。", reward=reward, money=baba.money_name, total=total)
        else:
            self.log_message = t(lang, "menu.daily_already", "❌ 今天已經簽到過了，明天再來吧！")

    def handle_achievements(self):
        self.clear_items()
        lang = self.player.language
        p = self.player
        unlocked_ids = set(getattr(p, "achievements", []) or [])
        achievements_config = getattr(self.cog, "achievements", {})

        lines = [t(lang, "achievements.title", "🏅 【成就總覽】")]
        if unlocked_ids:
            lines.append(t(lang, "achievements.unlocked_header", "\n── 已解鎖 ──"))
        for achv_id, info in achievements_config.items():
            if achv_id not in unlocked_ids:
                continue
            lines.append(f"✅ {tf(info, 'name', lang)} — {tf(info, 'desc', lang)}")

        lines.append(t(lang, "achievements.locked_header", "\n── 未解鎖 ──"))
        any_locked = False
        for achv_id, info in achievements_config.items():
            if achv_id in unlocked_ids:
                continue
            any_locked = True
            achv_type = info.get("type")
            threshold = info.get("threshold", 0)
            current_val = self._achievement_progress_value(achv_type)
            lines.append(f"🔒 {tf(info, 'name', lang)} — {tf(info, 'desc', lang)} ({min(current_val, threshold)}/{threshold})")
        if not any_locked:
            lines.append(t(lang, "achievements.all_unlocked", "🎉 已解鎖所有成就！"))

        self.log_message = "\n".join(lines)
        self.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_guild_menu", emoji="🔙")

    def build_quest_hall_menu(self, notice=""):
        """公會任務大廳：列出進行中任務的進度，以及可接取的看板委託。
        每筆委託底下都附一行「🎯 怎麼完成」，不用再自己猜要去哪裡打什麼怪。"""
        from trpg.quest_popup import recompute_live_progress, available_board_quests, quest_progress_text, describe_quest_goal
        self.clear_items()
        lang = self.player.language
        p = self.player
        recompute_live_progress(self)

        lines = []
        if notice:
            lines.append(notice + "\n")
        lines.append(t(lang, "quest_hall.title", "📜 【任務大廳】"))

        # 進行中的委託（含進度 + 完成方式）
        active = [(qid, self.cog.quests.get(qid)) for qid in p.active_quests if self.cog.quests.get(qid)]
        if active:
            lines.append(t(lang, "quest_hall.active_header", "\n── 進行中的委託 ──"))
            for qid, qinfo in active:
                lines.append(quest_progress_text(self, qid, qinfo, lang))
                goal = describe_quest_goal(self, qinfo, lang)
                if goal:
                    lines.append(f"   {goal}")
        else:
            lines.append(t(lang, "quest_hall.no_active", "\n（目前沒有進行中的委託）"))

        # 可接取的看板委託
        avail = available_board_quests(self)
        if avail:
            lines.append(t(lang, "quest_hall.available_header", "\n── 可接取的委託 ──"))
            for qid, qinfo in avail[:5]:
                title = tf(qinfo, "title", lang)
                tag = t(lang, "quest_hall.daily_tag", "（每日）") if qinfo.get("repeatable") else ""
                reward = t(lang, "quest_hall.reward_brief", "獎勵 {exp} EXP / {money}$", exp=qinfo.get("reward_exp", 0), money=qinfo.get("reward_money", 0))
                lines.append(f"• {title}{tag} — {reward}")
                goal = describe_quest_goal(self, qinfo, lang)
                if goal:
                    lines.append(f"   {goal}")
                self.add_action_button(
                    label=t(lang, "quest_hall.btn_accept", "接取：{title}", title=title[:70]),
                    style=discord.ButtonStyle.success, custom_id=f"qaccept_{qid}", emoji="✅",
                )
        else:
            lines.append(t(lang, "quest_hall.no_available", "\n（暫時沒有可接取的看板委託，去打怪或升級後再來看看！）"))

        lines.append(t(lang, "quest_hall.hint", "\n💡 部分委託會由 NPC 隨機找上你，接取後一樣能在這裡查看進度。"))
        self.log_message = "\n".join(lines)
        self.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_guild_menu", emoji="🔙")

    async def handle_quest_hall(self, notice=""):
        self.build_quest_hall_menu(notice)

    async def handle_area_npc(self, notice=""):
        """主線 NPC 選單：顯示 NPC 開場白與其專屬任務鏈的狀態（可接取／進行中／已完成）。
        任務資料標記 board=false + npc_offer=false，只能從這裡接取；完成後的交付與
        獎勵彈窗沿用既有的自動任務結算流程（process_quest_popups）。"""
        from trpg.quest_popup import recompute_live_progress, can_accept_quest, quest_progress_text, describe_quest_goal
        self.clear_items()
        lang = self.player.language
        area_npc = self.cog.areas.get(self.player.current_area, {}).get("npc")
        if not area_npc:
            self.build_main_menu()
            return

        recompute_live_progress(self)
        real = getattr(self.player, "real_player", self.player)
        npc_name = tf(area_npc, "name", lang) or "NPC"
        lines = []
        if notice:
            lines.append(notice + "\n")
        lines.append(f"{area_npc.get('emoji', '🧑')} **{npc_name}**")
        lines.append(tf(area_npc, "intro", lang) or "")

        lines.append(t(lang, "npc.quest_header", "\n── 委託 ──"))
        for qid in area_npc.get("quest_ids", []):
            qinfo = self.cog.quests.get(qid)
            if not qinfo:
                continue
            title = tf(qinfo, "title", lang) or qid
            if qid in real.completed_quests:
                lines.append(t(lang, "npc.quest_done", "✅ {title}（已完成）", title=title))
            elif qid in real.active_quests:
                lines.append(quest_progress_text(self, qid, qinfo, lang))
                goal = describe_quest_goal(self, qinfo, lang)
                if goal:
                    lines.append(f"   {goal}")
            elif can_accept_quest(self, qid, qinfo):
                reward_bits = [f"{qinfo.get('reward_exp', 0)} EXP", f"{qinfo.get('reward_money', 0)}$"]
                for item_id in (qinfo.get("reward_items") or {}):
                    reward_bits.append(tf(self.cog.items.get(item_id, {}), "name", lang) or item_id)
                lines.append(t(lang, "npc.quest_available", "❗ {title} — 獎勵：{rewards}", title=title, rewards=" / ".join(reward_bits)))
                goal = describe_quest_goal(self, qinfo, lang)
                if goal:
                    lines.append(f"   {goal}")
                self.add_action_button(
                    label=t(lang, "quest_hall.btn_accept", "接取：{title}", title=title[:70]),
                    style=discord.ButtonStyle.success, custom_id=f"qaccept_{qid}", emoji="✅",
                )
            else:
                req_lv = qinfo.get("req_level", 1)
                if real.level < req_lv:
                    lines.append(t(lang, "npc.quest_locked_level", "🔒 {title}（需 Lv.{req}）", title=title, req=req_lv))
                else:
                    lines.append(t(lang, "npc.quest_locked_chain", "🔒 {title}（完成前一項委託後開啟）", title=title))

        if all(qid in real.completed_quests for qid in area_npc.get("quest_ids", [])):
            lines.append(t(lang, "npc.all_done_hint", "\n💠 這位 NPC 的委託已全部完成。集齊三色魔法碎片後，到鐵匠之地的手藝工坊合成【魔法之眼】吧！"))

        self.log_message = "\n".join(lines)
        self.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    async def handle_quest_accept(self, quest_id: str):
        from trpg.quest_popup import accept_quest
        lang = self.player.language
        quest_info = self.cog.quests.get(quest_id)
        title = tf(quest_info, "title", lang) if quest_info else quest_id
        if accept_quest(self, quest_id):
            notice = t(lang, "quest_hall.accepted", "✅ 已接取委託：{title}", title=title)
        else:
            notice = t(lang, "quest_hall.accept_failed", "❌ 無法接取此委託（可能已接取、已完成或等級不足）。")
        # 從主線 NPC 接的委託：回到 NPC 畫面而不是公會任務大廳
        area_npc = self.cog.areas.get(self.player.current_area, {}).get("npc")
        if area_npc and quest_id in area_npc.get("quest_ids", []):
            await self.handle_area_npc(notice)
            return
        self.build_quest_hall_menu(notice)

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
            dg.end_run(self.player)
            self.player.current_area = "area_00village"

        if self.player.current_area == "area_legend_cave":
            self.player.current_area = "area_00village"
            self.player.cave_state = {"current_node": "entrance", "history": []}

        # 鬥技場死亡：連戰進度歸零，下次要從第一輪重新打
        if self.cog.areas.get(self.player.current_area, {}).get("is_colosseum"):
            self._colosseum_state()["round"] = 0

        # 強制導回主選單
        self.build_main_menu()

        # 回傳最終的戰報文字
        return final_log

    def _handle_sargeras_defeat(self) -> str:
        """敗於魔王薩格拉斯時呼叫：累計敗北次數（洞窟解鎖已改為「踏入魔王戰即解鎖」，
        見 handle_boss_explore——這裡只留統計，並在玩家還沒拿到勇者之劍時給個提醒）。"""
        lang = self.player.language
        count = getattr(self.player, "sargeras_defeat_count", 0) + 1
        self.player.sargeras_defeat_count = count

        real = getattr(self.player, "real_player", self.player)
        if real.inventory.get("hero_sword", 0) <= 0 and real.weapon != "hero_sword":
            return "\n\n" + t(
                lang,
                "menu.sargeras_defeat_hint",
                "🎻 吟遊詩人的歌聲在腦海中迴響：「勇士啊，別忘了【傳說洞窟】深處的【勇者之劍】……"
                "沒有它，魔王的力量深不可測。」",
            )
        return ""

    def _achievement_progress_value(self, achv_type: str):
        """成就進度依 type 查詢：優先看 player.stats（累計型數值，例如 monsters_killed／
        total_deaths／money_spent），找不到就退回玩家屬性本身——這讓 achievements.json
        不必再侷限於那 3 個 stats 計數器，可以直接拿 level／prestige_count／tower_floor
        這種玩家物件上已經有現成數值的欄位，或是 killed_bosses／trophies 這種list（取
        長度，例如「累計擊敗過幾隻不同的區域BOSS」）。

        屬性查詢一律解包成 real_player 再讀：level 是地下城探索用的封印欄位（見
        trpg/dungeon.py SEALED_FIELDS），探索中直接讀 self.player 會拿到固定的封印
        等級 10，跟任務系統一樣的陷阱——沒解包的話玩家一進地下城就會被誤判「已達到
        Lv.10」而提早解鎖等級類成就。"""
        p = self.player
        stats = getattr(p, "stats", None) or {}
        if achv_type in stats:
            return stats.get(achv_type, 0)
        real = getattr(p, "real_player", p)
        val = getattr(real, achv_type, None)
        if isinstance(val, (list, dict)):
            return len(val)
        if isinstance(val, bool):
            return int(val)
        if isinstance(val, (int, float)):
            return val
        return 0

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
            current_val = self._achievement_progress_value(achv_type)
            if current_val >= threshold:
                self.player.achievements.append(achv_id)
                unlocked_msgs.append(t(lang, "menu.achievement_unlocked", "🎉 【解鎖成就】{name} - {desc}", name=tf(info, "name", lang), desc=tf(info, "desc", lang)))
                
        if unlocked_msgs:
            self.cog.save_players()
            return "\n" + "\n".join(unlocked_msgs)
        return ""

    @staticmethod
    def _diff_stats_string(item_data: dict, eq_item: dict) -> str:
        """共用的裝備數值差異字串產生器——玩家一般裝備跟地下城封印裝備的比較函式
        只差在「怎麼找出目前裝備的那一件」，數值差異的算法跟顯示格式完全一樣，
        不需要各自維護一份幾乎一模一樣的迴圈。"""
        diffs = []
        for key, (label, emoji) in EQUIP_STAT_DISPLAY.items():
            diff = item_data.get(key, 0) - eq_item.get(key, 0)
            if diff > 0:
                diffs.append(f"{emoji}{label}+{diff}▲")
            elif diff < 0:
                diffs.append(f"{emoji}{label}{diff}▼")
        return "[" + " ".join(diffs) + "]" if diffs else ""

    def _get_equipment_comparison_string(self, item_data: dict) -> str:
        item_type = item_data.get("type")
        slot_attr = {"weapon": "weapon", "armor": "armor", "accessory": "accessory"}.get(item_type)
        if not slot_attr:
            return ""
        eq_id = getattr(self.player, slot_attr, None)
        eq_item = self.cog.items.get(eq_id, {}) if eq_id else {}
        return self._diff_stats_string(item_data, eq_item)

    def _dungeon_equipment_comparison_string(self, item_data: dict) -> str:
        """跟 _get_equipment_comparison_string 一樣，但比較對象是地下城封印裝備
        （d_state 的 weapon/armor/accessory 欄位 + cog.dungeon_items），而不是
        玩家的常規裝備。"""
        item_type = item_data.get("type")
        if item_type not in ("weapon", "armor", "accessory"):
            return ""

        d_state = self._dstate()
        eq_id = d_state.get(item_type)
        eq_item = self.cog.dungeon_items.get(eq_id, {}) if eq_id else {}
        return self._diff_stats_string(item_data, eq_item)

    _DUNGEON_HOOK_DISPLAY = {
        "crit_bonus": ("🎯", "暴擊率", "Crit"),
        "lifesteal": ("🩸", "吸血", "Lifesteal"),
        "on_kill_heal_pct": ("💚", "擊殺回血", "Heal on kill"),
        "regen_pct": ("💧", "每回合回魔", "MP regen"),
        "corrosion_on_hit": ("☣️", "腐蝕層數", "Corrosion stacks"),
        "corrosion_dmg_bonus": ("☣️", "腐蝕傷害加成", "Corrosion dmg bonus"),
    }

    def _format_dungeon_item_effect_tags(self, item_data: dict) -> str:
        """地下城裝備版的 _format_item_effect_tags——列出 crit/吸血/擊殺回血/腐蝕
        這些 hook 效果欄位（見 trpg/dungeon.py _HOOK_KEYS），戰利品選單之前只顯示
        數值加成的比較，這些效果完全看不到。"""
        lang = self.player.language
        tags = []
        for key, (emoji, label_zh, label_en) in self._DUNGEON_HOOK_DISPLAY.items():
            val = item_data.get(key)
            if not val:
                continue
            if key in ("crit_bonus", "lifesteal", "on_kill_heal_pct", "regen_pct"):
                val_str = f"{val * 100:.0f}%"
            else:
                val_str = f"{val}"
            tags.append(f"{emoji}{label_en}+{val_str}" if lang == "en" else f"{emoji}{label_zh}+{val_str}")
        if not tags:
            return ""
        return "{" + " ".join(tags) + "}"

    def _format_equip_item_line(self, item_id: str) -> str:
        """裝備選單用：印出這件裝備的絕對數值加成，再加上跟目前裝備的比較。"""
        lang = self.player.language
        item = self.cog.items.get(item_id, {})
        item_name = f"{item_emoji(item)} {tf(item, 'name', lang)}"

        stat_parts = [
            f"{emoji}{label}{item[key]:+d}"
            for key, (label, emoji) in EQUIP_STAT_DISPLAY.items()
            if item.get(key)
        ]
        stat_str = " ".join(stat_parts) if stat_parts else t(lang, "equip.no_stat_bonus", "（無數值加成）")

        line = f"▫️ {item_name}：{stat_str}"
        if item.get("type") in ("weapon", "armor", "accessory"):
            comp_str = self._get_equipment_comparison_string(item)
            if comp_str:
                line += f" {comp_str}"
        effect_str = self._format_item_effect_tags(item)
        if effect_str:
            line += f" {effect_str}"
        return line

    def _resolve_boss_name(self, boss_id: str) -> str:
        lang = self.player.language
        for area in self.cog.areas.values():
            boss = area.get("boss", {})
            if boss.get("id") == boss_id:
                return tf(boss, "name", lang) or boss_id
        return boss_id

    def _format_item_effect_tags(self, item_data: dict) -> str:
        """列出裝備上「數值加成」以外的特殊效果（元素屬性、觸發異常狀態機率、
        攻擊吸血、抗boss、速度加成攻擊）。這些欄位一直都有在戰鬥裡實際生效
        （見 combat.py _apply_weapon_on_hit / player_attack），只是商店、裝備選單
        從來沒把它們印出來，玩家只能靠 desc 文字（通常還沒寫、也沒寫機率數字）
        才知道自己這把武器到底有沒有中毒/燃燒之類的附加效果。"""
        lang = self.player.language
        tags = []

        element = item_data.get("element")
        if element and element in ELEMENT_DISPLAY:
            emoji, name_zh, name_en = ELEMENT_DISPLAY[element]
            tags.append(f"{emoji}{name_en} Element" if lang == "en" else f"{emoji}{name_zh}屬性")

        status_id = item_data.get("on_hit_status")
        if status_id:
            sdef = self.cog.status_effects.get(status_id, {})
            s_emoji = sdef.get("emoji", "❓")
            s_name = tf(sdef, "name", lang) or status_id
            chance_pct = int(item_data.get("on_hit_chance", 0.25) * 100)
            tags.append(f"{s_emoji}{chance_pct}% {s_name}" if lang == "en" else f"{s_emoji}{chance_pct}%機率{s_name}")

        heal_pct = item_data.get("on_hit_heal_percent")
        if heal_pct:
            pct = int(heal_pct * 100)
            tags.append(f"🩸{pct}% Lifesteal" if lang == "en" else f"🩸攻擊吸血{pct}%")

        if item_data.get("spd_scaling"):
            tags.append("🏃Speed-scaling ATK" if lang == "en" else "🏃速度加成攻擊")

        anti_boss_id = item_data.get("anti_boss_id")
        if anti_boss_id:
            boss_name = self._resolve_boss_name(anti_boss_id)
            tags.append(f"👑vs.{boss_name} Bonus" if lang == "en" else f"👑對{boss_name}特效")

        if not tags:
            return ""
        return "{" + " ".join(tags) + "}"

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

    def _area_unlocked(self, area: dict) -> bool:
        """區域解鎖條件：requires_flag（例如傳說洞窟）、requires_boss（必須先在
        player.killed_bosses 裡有指定區域的首殺紀錄），或 requires_boss_count
        （累計首殺過 N 個不同區域 BOSS——給修羅鬥技場這種「集齊戰功才受邀」的隱藏區域用）。"""
        requires_flag = area.get("requires_flag")
        if requires_flag and not getattr(self.player, requires_flag, False):
            return False
        requires_boss = area.get("requires_boss")
        if requires_boss and requires_boss not in getattr(self.player, "killed_bosses", []):
            return False
        requires_count = area.get("requires_boss_count", 0)
        if requires_count and len(getattr(self.player, "killed_bosses", [])) < requires_count:
            return False
        return True

    def _boss_name_for_area(self, area_id: str, lang: str) -> str:
        boss_area = self.cog.areas.get(area_id, {})
        return tf(boss_area.get("boss", {}), "name", lang) or tf(boss_area, "area_name", lang) or area_id

    async def handle_move_menu(self):
        self.clear_items()
        lang = self.player.language
        self.log_message = t(lang, "menu.move_prompt", "挑選你打算移動前往的下一個區域：")
        locked_hints = []
        for area_id, area in self.cog.areas.items():
            if area_id == self.player.current_area:
                continue
            if not self._area_unlocked(area):
                area_name = tf(area, "area_name", lang) or area_id
                requires_boss = area.get("requires_boss")
                requires_count = area.get("requires_boss_count", 0)
                if requires_boss:
                    boss_name = self._boss_name_for_area(requires_boss, lang)
                    locked_hints.append(t(
                        lang, "menu.area_locked_hint", "🔒 {area_name}（需先擊敗【{boss_name}】）",
                        area_name=area_name, boss_name=boss_name,
                    ))
                elif requires_count:
                    # 隱藏區域的存在本身是彩蛋：戰功累積過半才開始聽到「傳聞」，
                    # 之前完全不顯示，保留初次發現的驚喜感。
                    have = len(getattr(self.player, "killed_bosses", []))
                    if have * 2 >= requires_count:
                        locked_hints.append(t(
                            lang, "menu.area_locked_count_hint", "🔒 {area_name}（需累計首殺 {need} 個不同區域BOSS，目前 {have}）",
                            area_name=area_name, need=requires_count, have=have,
                        ))
                continue
            req = area.get("req_level", 1)
            area_name = tf(area, "area_name", lang) if area.get("area_name") else t(lang, "menu.unknown_area", "未知區域")
            label = t(lang, "menu.move_to_label", "前往 {area_name} (Lv.{req})", area_name=area_name, req=req)
            self.add_action_button(label=label, style=discord.ButtonStyle.primary, custom_id=f"move_to_{area_id}")
        if locked_hints:
            self.log_message += "\n\n" + "\n".join(locked_hints)
        self.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    def _instantiate_boss_minion(self, minion_def: dict, boss_level: int, area_req_level: int) -> dict:
        """依 BOSS 等級放大區域 boss_minion 的數值（與召喚邏輯一致）。"""
        m = dict(minion_def)
        scale = 1.0 + max(0, boss_level - area_req_level) * 0.15
        m["max_hp"] = max(1, int(m.get("base_hp", m.get("max_hp", 10)) * scale))
        m["atk"] = max(1, int(m.get("base_atk", m.get("atk", 5)) * scale))
        m["def"] = max(0, int(m.get("base_def", m.get("def", 0)) * scale))
        m["spd"] = max(1, int(m.get("base_spd", m.get("spd", 5)) * scale))
        return m

    def _build_boss_encounter(self, boss_instance: dict, area_data: dict) -> list:
        """組出 start_combat 用的怪物列表：BOSS 開場就帶著小怪在前排。
        一般 BOSS 因為開場已有小怪，會移除自行召喚技能；只有魔王薩格拉斯保留持續召喚，且開場帶兩隻小鬼。"""
        minion_defs = area_data.get("boss_minions") or {}
        is_sargeras = boss_instance.get("id") == "sargeras"
        boss_level = boss_instance.get("level", area_data.get("req_level", 1) + 5)
        area_req = area_data.get("req_level", 1)

        # 輔助/治療型小怪要待在後排（被前排的肉盾擋著），所以排序時往後放
        SUPPORT_AIS = {"support_healer", "shield_ally", "buffer"}

        minions = []
        if minion_defs:
            ids = list(minion_defs.keys())
            chosen = [ids[0], ids[0]] if is_sargeras else ids[:2]
            minions = [self._instantiate_boss_minion(minion_defs[mid], boss_level, area_req) for mid in chosen]
            # 非輔助（攻擊/減益型）排前面當肉盾，輔助型排後面
            minions.sort(key=lambda m: m.get("ai") in SUPPORT_AIS)

        # 一般 BOSS 開場已有小怪 → 拿掉自行召喚技能，避免無限疊加；魔王保留召喚
        if not is_sargeras and boss_instance.get("active_skills"):
            boss_instance["active_skills"] = [
                s for s in boss_instance["active_skills"]
                if s.get("effect", {}).get("type") != "summon_minion"
            ]

        encounter = minions + [boss_instance]  # BOSS 永遠在最後排，最受保護
        return encounter[:3]

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
            # 明定的魔防也要跟著聖劍削弱，不然法師流拿了劍反而打不動
            if "mdef" in boss_instance:
                boss_instance["mdef"] = max(0, int(boss_instance["mdef"] * weapon.get("anti_boss_def_mult", 1.0)))
            # 聖光也削弱魔王的生命本源：沒有 hp_mult 的話，持劍戰依然是 3 萬血的耐久馬拉松，
            # 「有來有回」的攻防在 40+ 回合的消耗戰裡會變成疲勞轟炸。
            boss_instance["max_hp"] = max(1, int(boss_instance["max_hp"] * weapon.get("anti_boss_hp_mult", 1.0)))
            if "anti_boss_spd_set" in weapon:
                boss_instance["spd"] = weapon["anti_boss_spd_set"]
            else:
                boss_instance["spd"] = max(5, int(boss_instance["spd"] * weapon.get("anti_boss_spd_mult", 1.0)))
            anti_boss_text = "\n" + t(lang, "menu.anti_boss_weapon_glow", "✨ 【{weapon_name}】散發出聖潔的光輝，魔王的力量被大幅削弱了！", weapon_name=tf(weapon, "name", lang) if weapon else "")

        self.start_combat(self._build_boss_encounter(boss_instance, area_data))

        self.log_message = t(lang, "menu.boss_encounter_warning", "🚨 【區域領主警告】 🚨\n大地在震動... 你驚動了隱藏的首領【{boss_name}】！{anti_boss_text}", boss_name=tf(boss_instance, "name", lang), anti_boss_text=anti_boss_text)

        # 👇 傳說洞窟解鎖條件：踏入過魔王戰即可（不再需要敗北三次）。
        # 第一次與薩格拉斯交手的瞬間，吟遊詩人的傳聞就此解鎖。
        real = getattr(self.player, "real_player", self.player)
        if boss_instance.get("id") == "sargeras" and not getattr(real, "legend_cave_unlocked", False):
            real.legend_cave_unlocked = True
            self.cog.save_players()
            self.log_message += "\n\n" + t(
                lang, "menu.legend_cave_unlock_on_fight",
                "🎻 直面魔王的瞬間，你想起吟遊詩人的歌謠——世界角落的【傳說洞窟】深處，"
                "沉睡著一把連魔王都忌憚的【勇者之劍】……\n✨ 【傳說洞窟】已出現在移動選單中！",
            )
        self.build_battle_menu()

    def _paginate(self, items: list, items_per_page: int, paging: bool) -> tuple:
        """共用分頁計算，取代原本在裝備/出售/道具/技能配置/工坊選單各自重複的頁碼算法。
        paging=False 代表這次是「開啟這個選單」而不是「換頁」，一律從第一頁看起。
        回傳 (這一頁的項目清單, 目前頁碼(0-based), 最大頁碼(0-based))。"""
        if not paging:
            self.inventory_page = 0
        max_page = max(0, (len(items) - 1) // items_per_page)
        self.inventory_page = min(self.inventory_page, max_page)
        start = self.inventory_page * items_per_page
        return items[start:start + items_per_page], self.inventory_page, max_page

    def _add_pagination_buttons(self, total_items: int, items_per_page: int, row: int = 3):
        if total_items > items_per_page:
            lang = self.player.language
            self.add_action_button(label=t(lang, "menu.btn_prev_page", "◀️ 上一頁"), style=discord.ButtonStyle.secondary, custom_id="btn_prev_page", row=row)
            self.add_action_button(label=t(lang, "menu.btn_next_page", "▶️ 下一頁"), style=discord.ButtonStyle.secondary, custom_id="btn_next_page", row=row)

    def _equip_option_desc(self, item_id: str) -> str:
        """下拉選項的說明列（上限 100 字）：數值加成 + 與目前裝備的差異。"""
        lang = self.player.language
        item = self.cog.items.get(item_id, {})
        stat_parts = [
            f"{label}{item[key]:+d}"
            for key, (label, emoji) in EQUIP_STAT_DISPLAY.items()
            if item.get(key)
        ]
        desc = " ".join(stat_parts) if stat_parts else t(lang, "equip.no_stat_bonus", "（無數值加成）")
        comp = self._get_equipment_comparison_string(item)
        if comp:
            desc += f" {comp}"
        return desc[:100]

    async def handle_equip_menu(self, notice="", paging=False):
        """裝備管理：改用下拉選單——武器/防具/飾品各一格，一格容得下 25 件，
        不再是一整面 8 顆一頁的按鈕牆。選中已裝備的品項＝卸下，其餘＝裝備；
        等級不足的品項照樣列出（🔒 標示），點了會由既有的等級檢查擋下並提示。"""
        self.clear_items()
        self.current_menu_state = "equip"
        lang = self.player.language

        categories = {"weapon": [], "armor": [], "accessory": []}
        for item_id, count in self.player.inventory.items():
            if count > 0:
                item_data = self.cog.items.get(item_id)
                if item_data and item_data.get("type") in categories:
                    categories[item_data["type"]].append(item_id)

        # 分頁以「最大的分類」為準（每格下拉選單上限 25 個選項），三個分類共用頁碼
        per_page = 25
        max_cat = max((len(v) for v in categories.values()), default=0)
        if not paging:
            self.inventory_page = 0
        max_page = max(0, (max_cat - 1) // per_page)
        self.inventory_page = min(self.inventory_page, max_page)
        start = self.inventory_page * per_page

        self.log_message = (notice + "\n\n" if notice else "") + t(lang, "equip.menu_title_select", "🎒 【裝備管理】從下方選單挑選要裝備／卸下的品項：")
        if max_page > 0:
            self.log_message += t(lang, "equip.page_suffix", "（第 {page}/{max_page} 頁）", page=self.inventory_page + 1, max_page=max_page + 1)

        none_bare_handed = t(lang, "equip.none_bare_handed", "無 (空手)")
        none_cloth = t(lang, "equip.none_cloth", "無 (布衣)")
        none_label = t(lang, "equip.none", "無")
        c_weap = f"【{tf(self.cog.items[self.player.weapon], 'name', lang)}】" if self.player.weapon else none_bare_handed
        c_armr = f"【{tf(self.cog.items[self.player.armor], 'name', lang)}】" if getattr(self.player, 'armor', None) else none_cloth
        c_accs = f"【{tf(self.cog.items[self.player.accessory], 'name', lang)}】" if self.player.accessory else none_label

        self.log_message += t(lang, "equip.current_gear_summary", "\n👉 武器：{weapon}\n👉 防具：{armor}\n👉 飾品：{accessory}", weapon=c_weap, armor=c_armr, accessory=c_accs)
        # 目前裝備的完整數值細節照舊印在面板上，選單選項則各自帶精簡說明
        for eq_id in (self.player.weapon, getattr(self.player, "armor", None), self.player.accessory):
            if eq_id and eq_id in self.cog.items:
                self.log_message += "\n" + self._format_equip_item_line(eq_id)

        cat_meta = [
            ("weapon", t(lang, "equip.select_weapon", "⚔️ 武器：選擇要裝備／卸下的武器"), "equip_", "unequip_", self.player.weapon, 0),
            ("armor", t(lang, "equip.select_armor", "🛡️ 防具：選擇要穿戴／卸下的防具"), "equip_", "unequip_", getattr(self.player, "armor", None), 1),
            ("accessory", t(lang, "equip.select_accessory", "💍 飾品：選擇要配戴／卸下的飾品"), "acc_", "unacc_", self.player.accessory, 2),
        ]
        for cat, placeholder, eq_prefix, uneq_prefix, current_id, row in cat_meta:
            item_ids = categories[cat][start:start + per_page]
            if not item_ids:
                continue
            options = []
            for item_id in item_ids:
                item_data = self.cog.items[item_id]
                name = tf(item_data, "name", lang) or item_id
                req = item_data.get("exclusive_level", 0)
                if item_id == current_id:
                    label = t(lang, "equip.opt_unequip", "✅ {name}（裝備中，選擇以卸下）", name=name)
                    value = f"{uneq_prefix}{item_id}"
                elif req > self.player.level:
                    label = t(lang, "equip.opt_locked", "🔒 {name}（需 Lv.{req}）", name=name, req=req)
                    value = f"{eq_prefix}{item_id}"  # 選了會被既有等級檢查擋下並提示
                else:
                    label = name
                    value = f"{eq_prefix}{item_id}"
                options.append((label, value, self._equip_option_desc(item_id), item_emoji(item_data)))
            self.add_action_select(placeholder, options, row=row, custom_id=f"sel_equip_{cat}")

        if not any(categories.values()):
            self.log_message += "\n\n" + t(lang, "equip.no_equippable_items", "背包裡沒有可裝備的物品。")

        if max_cat > per_page:
            self._add_pagination_buttons(max_cat, per_page)
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
            recalc_player_stats(self.player, self.cog.items, heal_full=False)
            self.cog.save_players()
            await self.handle_equip_menu(t(lang, "equip.accessory_equip_success", "🎭 配戴了【{name}】！", name=item_name))
        else:
            self.player.accessory = None
            recalc_player_stats(self.player, self.cog.items, heal_full=False)
            self.cog.save_players()
            await self.handle_equip_menu(t(lang, "equip.accessory_unequip_success", "🎭 卸下了【{name}】。", name=item_name))

    async def handle_item_menu(self, paging=False):
        self.clear_items()
        self.current_menu_state = "item"
        lang = self.player.language

        usable = []
        for item_id, count in self.player.inventory.items():
            if count > 0:
                item = self.cog.items.get(item_id)
                if item and item.get("type") in ("potion", "cure", "buff_item"):
                    usable.append(item_id)

        total_items = len(usable)
        page_items, _, max_page = self._paginate(usable, 25, paging)

        if not usable:
            self.log_message = t(lang, "menu.no_usable_items", "❌ 背包裡沒有可用的道具。")
            if self.in_battle:
                self.build_battle_menu()
            else:
                self.build_main_menu()
            return

        self.log_message = t(lang, "menu.choose_item_to_use_select", "🎒 從下方選單挑選要使用的道具：")
        if max_page > 0:
            self.log_message += t(lang, "equip.page_suffix", "（第 {page}/{max_page} 頁）", page=self.inventory_page + 1, max_page=max_page + 1)
        options = []
        for item_id in page_items:
            item = self.cog.items[item_id]
            label = f"{tf(item, 'name', lang)} x{self.player.inventory[item_id]}"
            options.append((label, f"use_item_{item_id}", (tf(item, "desc", lang) or "")[:100], item_emoji(item)))
        self.add_action_select(t(lang, "menu.select_item_placeholder", "🎒 選擇道具"), options, row=0, custom_id="sel_use_item")

        if total_items > 25:
            self._add_pagination_buttons(total_items, 25)
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
        elif item.get("type") == "buff_item":
            if self.in_battle:
                self.log_message = self.combat.use_buff_item(item_id)
            else:
                self.log_message = t(lang, "battle.buff_item_battle_only", "❌ 這個道具只能在戰鬥中使用。")
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

    def _has_magic_eye(self) -> bool:
        """魔法之眼是「放在行囊就生效」的主線道具——一律讀永久角色的背包，
        地下城探索中封印替身的臨時背包不算數。"""
        real = getattr(self.player, "real_player", self.player)
        return real.inventory.get("magic_eye", 0) > 0

    def _format_elem_list(self, elems, lang) -> str:
        """把 weakness/resistance 清單轉成帶 emoji 的顯示字串（魔法之眼的弱點透視用）。
        清單裡可能混著異常狀態 id（例如魔王抗性帶 poison/burn），不在元素表裡的
        直接用狀態表的名字/emoji 顯示，都查不到就原樣印出。"""
        parts = []
        for e in elems:
            if e in ELEMENT_DISPLAY:
                emoji, name_zh, name_en = ELEMENT_DISPLAY[e]
                parts.append(f"{emoji}{name_en if lang == 'en' else name_zh}")
            else:
                sdef = self.cog.status_effects.get(e, {})
                name = tf(sdef, "name", lang) or e
                parts.append(f"{sdef.get('emoji', '')}{name}")
        return " ".join(parts)

    def _format_combat_mods(self, p) -> str:
        """把目前的增益/減益整理成一行精簡文字（戰鬥面板用）。"""
        parts = []
        buffs = getattr(p, "combat_buffs", None) or {}
        if buffs.get("turns", 0) > 0:
            b = []
            if buffs.get("atk_mult", 1) > 1: b.append("⚔️↑")
            if buffs.get("def_mult", 1) > 1: b.append("🛡️↑")
            if buffs.get("spd_mult", 1) > 1: b.append("🚀↑")
            if b:
                parts.append("".join(b) + f"({buffs['turns']})")
        if buffs.get("regen_turns", 0) > 0:
            parts.append(f"🌿({buffs['regen_turns']})")
        debuffs = getattr(p, "combat_debuffs", None) or {}
        if debuffs.get("turns", 0) > 0:
            d = []
            if debuffs.get("atk_mult", 1) < 1: d.append("⚔️↓")
            if debuffs.get("def_mult", 1) < 1: d.append("🛡️↓")
            if debuffs.get("spd_mult", 1) < 1: d.append("🚀↓")
            if d:
                parts.append("".join(d) + f"({debuffs['turns']})")
        return " ".join(parts)

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
        p_spd = get_player_spd(p)
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
        # 戰鬥中：不顯示金錢/未分配點數（用不到），改顯示增益/減益；探索中：顯示金錢與未分配點數
        if self.in_battle:
            header_line = t(lang, "battle.header_line", "**Lv.{level} 冒險者**", level=p.level)
        else:
            header_line = t(lang, "battle.adventurer_status_line", "**Lv.{level} 冒險者** | 💰 {balance} {money_name}", level=p.level, balance=user_bal, money_name=self.cog.bot.baba.money_name)
        player_desc = (
            f"{header_line}\n"
            f"❤️ HP: `{p.current_hp:03d}/{p.max_hp:03d}`\n"
            f"💧 MP: `{p.current_mp:03d}/{p.max_mp:03d}`\n"
            f"⚔️ ATK: `{p_atk}` | 🛡️ DEF: `{p_def}` | 🚀 SPD: `{p_spd}`\n"
            f"✨ MAG: `{p_magic}` | 🔰 RES: `{p_res}`\n"
        )

        status_line = t(lang, "battle.status_line", "📜 狀態：{status_text}", status_text=status_text)
        if self.in_battle:
            action_label = t(lang, "battle.action_bar_label", "⚡ 行動: `[{bar}]`", bar=player_bar)
            player_desc += f"{action_label}\n"
            mods_str = self._format_combat_mods(p)
            if mods_str:
                player_desc += t(lang, "battle.mods_line", "🔺 增益/減益：{mods}", mods=mods_str) + "\n"
            player_desc += f"{status_line}{immune_str}"
        else:
            unspent_line = t(lang, "battle.unspent_points_line", "📊 未分配點數: `{unspent}`", unspent=unspent)
            player_desc += f"{unspent_line}\n{status_line}{immune_str}"
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
                # ❗ 提示：預估玩家下次行動後這隻怪物會攻擊幾次（❗=1 次，❗❗=2 次以上）
                warn = ""
                if slot["hp"] > 0:
                    acts = self.combat.predict_monster_actions(slot)
                    if acts >= 1:
                        warn = " " + "❗" * min(acts, 3)
                action_label = t(lang, "battle.action_bar_label", "⚡ 行動: `[{bar}]`", bar=monster_bar) + warn
                # 防禦是「身份標籤」不是通用數值：大多數怪物 0 防（不顯示），少數
                # 鐵殼/重甲型怪物的高防才值得佔一格版面，讓玩家一眼認出要換打法。
                def_part = f" | 🛡️ DEF: `{m['def']}`" if m.get("def", 0) > 0 else ""
                monster_desc = (
                    f"❤️ HP: `{max(0, slot['hp']):03d}/{m['max_hp']:03d}`\n"
                    f"⚔️ ATK: `{m['atk']}`{def_part} | 🚀 SPD: `{m.get('spd', 0)}`\n"
                    f"{action_label}"
                )
                # 👇 警示系統：怪物的下一步意圖與防禦性機制全部搬上檯面，玩家不用去
                # 翻戰鬥紀錄才知道要防、要打斷、還是要留一發小招戳破聖盾。
                intent_lines = []
                # 🧿 魔法之眼：行囊裡有就透視這隻怪物的屬性弱點/抗性
                if slot["hp"] > 0 and self._has_magic_eye():
                    eye_bits = []
                    if m.get("weakness"):
                        eye_bits.append(t(lang, "battle.eye_weakness", "弱點 {elems}", elems=self._format_elem_list(m["weakness"], lang)))
                    if m.get("resistance"):
                        eye_bits.append(t(lang, "battle.eye_resistance", "抗性 {elems}", elems=self._format_elem_list(m["resistance"], lang)))
                    if m.get("immunity"):
                        eye_bits.append(t(lang, "battle.eye_immunity", "免疫 {elems}", elems=self._format_elem_list(m["immunity"], lang)))
                    if eye_bits:
                        intent_lines.append("🧿 " + " | ".join(eye_bits))
                    else:
                        intent_lines.append(t(lang, "battle.eye_no_weakness", "🧿 魔法之眼：這隻怪物沒有明顯的屬性弱點。"))
                if slot["hp"] > 0:
                    tele = slot.get("telegraph")
                    if tele:
                        intent_lines.append(t(lang, "battle.intent_telegraph", "⚠️ 蓄力中：【{skill}】即將發動！", skill=tf(tele, "name", lang)))
                    if slot.get("is_charging"):
                        intent_lines.append(t(lang, "battle.intent_charging", "⚡ 正在聚集毀滅性的能量！"))
                    if slot.get("telegraph_flee"):
                        intent_lines.append(t(lang, "battle.intent_flee", "😰 準備逃跑！（下回合就會逃走）"))
                    if slot.get("counter_stance"):
                        intent_lines.append(t(lang, "battle.intent_counter_stance", "🥋 反擊架勢！此時攻擊牠會遭到猛烈反擊"))
                    if slot.get("bomb_fuse"):
                        intent_lines.append(t(lang, "battle.intent_bomb_fuse", "💣 引信已點燃，下回合就會爆炸！"))
                    badges = []
                    if slot.get("divine_shield"):
                        badges.append(t(lang, "battle.badge_divine_shield", "🛡️聖盾"))
                    if slot.get("magic_absorb_shield"):
                        badges.append(t(lang, "battle.badge_magic_absorb", "🌀魔法吸收"))
                    if m.get("damage_cap"):
                        badges.append(t(lang, "battle.badge_damage_cap", "🧱承傷上限{cap}/回合", cap=m["damage_cap"]))
                    if slot.get("playing_dead"):
                        badges.append(t(lang, "battle.badge_playing_dead", "🎭倒地不起"))
                    if slot.get("rage_stacks"):
                        badges.append(t(lang, "battle.badge_rage", "😤怒氣x{n}", n=slot["rage_stacks"]))
                    if slot.get("fortify_stacks"):
                        badges.append(t(lang, "battle.badge_fortify", "🪨強固x{n}", n=slot["fortify_stacks"]))
                    if slot.get("stolen_gold"):
                        badges.append(t(lang, "battle.badge_stolen_gold", "💰贓款{g}", g=slot["stolen_gold"]))
                    if slot.get("status"):
                        badges.append(format_status_list(slot["status"], self.cog.status_effects, lang))
                    if badges:
                        intent_lines.append(" | ".join(badges))
                if intent_lines:
                    monster_desc += "\n" + "\n".join(intent_lines)
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
        # 👇 新手保護：Lv.3 以下只會遇到史萊姆——新手村附近其他怪物（野豬/山賊等）
        # 對剛開局、還沒武器沒技能的玩家來說殺傷力太不穩定，容易一上來就被血洗。
        # 練到 Lv.3 之後才會恢復正常的野外怪物池。
        if self.player.level < 3 and "slime" in area_data["monsters"]:
            monster_ids = ["slime"]

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
        monster_instance["def"] = max(0, int(base_monster.get("def", 0) * scale))
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

        active_skills = list(dict.fromkeys(
            s for s in self.player.equipped_skills
            if self.cog.skills.get(s, {}).get("type") != "passive"
        ))
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
            # 戰鬥中不再顯示等級需求（技能已學會、已裝備，等級門檻此時無意義）
            skill_name = tf(skill, "name", lang)
            skill_desc = tf(skill, "desc", lang)
            lines.append(f"• {skill_name}: {skill_desc}")

            cd_left = self.combat.skill_cds.get(skill_id, 0)

            # 👇 同時判斷 MP 與 HP 消耗並組合顯示字串
            cost_texts = []
            if skill.get("mp_cost"):
                cost_texts.append(f"MP:{skill['mp_cost']}")
            if skill.get("hp_cost_percent"):
                cost_texts.append(f"HP:{int(self.player.max_hp * skill['hp_cost_percent'])}")

            cost_str = " (" + ", ".join(cost_texts) + ")" if cost_texts else ""
            cd_text = f" [CD:{cd_left}]" if cd_left > 0 else ""

            mp_cost = skill.get("mp_cost", 0)
            hp_cost_pct = skill.get("hp_cost_percent", 0.0)
            actual_hp_cost = int(self.player.max_hp * hp_cost_pct)
            cant_afford = (mp_cost > 0 and self.player.current_mp < mp_cost) or (
                actual_hp_cost > 0 and self.player.current_hp <= actual_hp_cost
            )
            disabled = cd_left > 0 or cant_afford

            if cd_left > 0:
                style = discord.ButtonStyle.secondary
            elif cant_afford:
                style = discord.ButtonStyle.danger
            else:
                style = discord.ButtonStyle.success

            self.add_action_button(
                label=f"{skill_name}{cost_str}{cd_text}"[:80],
                style=style,
                custom_id=f"skill_{skill_id}",
                disabled=disabled,
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
            # 已學會的列在面板文字，還沒學的放進下拉選單——之前一張卷軸一顆按鈕，
            # 收集超過 24 種卷軸會撞上 Discord 25 個元件上限，整個面板直接渲染失敗。
            self.log_message = prefix + t(lang, "skill.learn_menu_choose_scroll", "📖 【學習魔法】\n選擇要研讀的卷軸（消耗 1 張）：")
            learnable = []
            learned_lines = []
            for scroll_id in scrolls:
                item = self.cog.items[scroll_id]
                skill_id = item.get("teaches", "")
                skill = self.cog.skills.get(skill_id, {})
                skill_name = tf(skill, "name", lang) if skill else skill_id
                if skill_id in self.player.skills:
                    learned_lines.append(t(lang, "skill.already_learned_label", "已學會：{skill_name}", skill_name=skill_name))
                else:
                    req_lv = skill.get("req_level", 1)
                    item_name = tf(item, "name", lang) or scroll_id
                    label = item_name if self.player.level >= req_lv else t(lang, "skill.opt_locked", "🔒 {skill_name}（需 Lv.{req}）", skill_name=item_name, req=req_lv)
                    learnable.append((label, f"learn_{scroll_id}", (tf(skill, "desc", lang) or "")[:100], "📜"))
            if learned_lines:
                self.log_message += "\n" + "\n".join(f"✅ {ln}" for ln in learned_lines[:20])
            if learnable:
                self.add_action_select(t(lang, "skill.select_learn", "📜 選擇要研讀的卷軸"), learnable[:25], row=0, custom_id="sel_learn_scroll")
            else:
                self.log_message += "\n\n" + t(lang, "skill.all_scrolls_learned", "（背包裡卷軸的技能都已學會）")

        self.add_action_button(label=t(lang, "skill.btn_back_to_church", "返回教堂"), style=discord.ButtonStyle.secondary, custom_id="btn_church_menu", emoji="🔙")

    async def handle_skill_equip_menu(self, notice="", paging=False):
        lang = self.player.language
        self.clear_items()
        self.current_menu_state = "skill_equip"
        prefix = notice + "\n\n" if notice else ""

        if not getattr(self.player, "equipped_skills", None):
            self.player.equipped_skills = []

        # 👇 被動技能也要能裝備、也要佔掉技能槽——它們只是不能在戰鬥中主動點來放
        # （那份過濾在 handle_skill_menu 那邊），配置畫面這裡不該把它們藏起來。
        p_skills = list(dict.fromkeys(getattr(self.player, "skills", [])))

        if not p_skills:
            self.log_message = prefix + t(lang, "skill.equip_menu_no_skills", "🔧 【技能配置】\n你尚未習得任何技能。請先【學習魔法】！")
            self.add_action_button(label=t(lang, "skill.btn_back_to_church", "返回教堂"), style=discord.ButtonStyle.secondary, custom_id="btn_church_menu", emoji="🔙")
            return

        equipped_count = len(self.player.equipped_skills)
        equipped_list = [s for s in p_skills if s in self.player.equipped_skills]
        unequipped_list = [s for s in p_skills if s not in self.player.equipped_skills]

        # 下拉選單一格 25 個選項；未裝備清單超過 25 才需要翻頁（已裝備最多 8 個）
        per_page = 25
        if not paging:
            self.inventory_page = 0
        max_page = max(0, (len(unequipped_list) - 1) // per_page)
        self.inventory_page = min(self.inventory_page, max_page)
        start = self.inventory_page * per_page

        header = prefix + t(lang, "skill.equip_menu_header_select", "🔧 【技能配置】 (已裝備: {equipped_count}/8)\n用下方選單裝備／卸下你的戰鬥技能。", equipped_count=equipped_count)
        if max_page > 0:
            header += t(lang, "skill.equip_page_suffix", "  (第 {page}/{max_page} 頁)", page=self.inventory_page + 1, max_page=max_page + 1)
        lines = [header]
        # 已裝備的技能照舊完整列在面板上，一眼看到目前的配置；技能說明放進選項的說明列
        for skill_id in equipped_list:
            skill = self.cog.skills.get(skill_id, {})
            lines.append(f"🟢 {tf(skill, 'name', lang) or skill_id}: {tf(skill, 'desc', lang) or ''}")

        if unequipped_list:
            options = []
            for skill_id in unequipped_list[start:start + per_page]:
                skill = self.cog.skills.get(skill_id, {})
                skill_name = tf(skill, "name", lang) or skill_id
                # 👇 轉生會把等級重置回 1，但學過的技能不會清掉——等級不足的照樣列出
                # （🔒 標示），選了會被 handle_skill_equip_action 的等級檢查擋下並提示。
                req_lv = skill.get("req_level", 1)
                if self.player.level < req_lv:
                    label = t(lang, "skill.opt_locked", "🔒 {skill_name}（需 Lv.{req}）", skill_name=skill_name, req=req_lv)
                else:
                    label = skill_name
                options.append((label, f"equip_skill_{skill_id}", (tf(skill, "desc", lang) or "")[:100], "⚪"))
            self.add_action_select(t(lang, "skill.select_equip", "⚪ 裝備技能：選擇要裝上的技能"), options, row=0, custom_id="sel_skill_equip")

        if equipped_list:
            options = []
            for skill_id in equipped_list[:25]:
                skill = self.cog.skills.get(skill_id, {})
                skill_name = tf(skill, "name", lang) or skill_id
                options.append((skill_name, f"unequip_skill_{skill_id}", (tf(skill, "desc", lang) or "")[:100], "🟢"))
            self.add_action_select(t(lang, "skill.select_unequip", "🟢 卸下技能：選擇要卸下的技能"), options, row=1, custom_id="sel_skill_unequip")

        self.log_message = "\n".join(lines)
        if len(unequipped_list) > per_page:
            self._add_pagination_buttons(len(unequipped_list), per_page)
        self.add_action_button(label=t(lang, "skill.btn_back_to_church", "返回教堂"), style=discord.ButtonStyle.secondary, custom_id="btn_church_menu", emoji="🔙", row=4)

    async def handle_skill_equip_action(self, skill_id: str, equip: bool):
        lang = self.player.language
        if not getattr(self.player, "equipped_skills", None):
            self.player.equipped_skills = []

        if equip:
            if len(self.player.equipped_skills) >= 8:
                await self.handle_skill_equip_menu(t(lang, "skill.equip_limit_reached", "❌ 技能裝備已達上限 (8/8)！請先卸下其他技能。"))
                return
            skill = self.cog.skills.get(skill_id, {})
            req_lv = skill.get("req_level", 1)
            if self.player.level < req_lv:
                skill_name = tf(skill, "name", lang) or skill_id
                await self.handle_skill_equip_menu(t(lang, "skill.equip_level_too_low", "❌ 等級不足！裝備【{skill_name}】需要 Lv.{req_lv}。", skill_name=skill_name, req_lv=req_lv))
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

        # 👇 學完技能後，技能欄還有空位的話直接幫忙裝備上——不然新手常常學了技能
        # 卻不知道還要另外跑一趟教堂的「技能配置」才能真正在戰鬥中用得到。
        if not getattr(self.player, "equipped_skills", None):
            self.player.equipped_skills = []
        auto_equipped = len(self.player.equipped_skills) < 8
        if auto_equipped:
            self.player.equipped_skills.append(skill_id)

        self.cog.save_players()
        item_name = tf(item, "name", lang)
        skill_desc = tf(skill, "desc", lang)
        learned_msg = t(lang, "skill.studied_and_learned", "📖 你研讀了【{item_name}】，成功習得技能【{skill_name}】！\n{skill_desc}", item_name=item_name, skill_name=skill_name, skill_desc=skill_desc)
        if auto_equipped:
            learned_msg += "\n" + t(lang, "skill.auto_equipped", "✅ 技能欄還有空位，已自動為你裝備上【{skill_name}】！", skill_name=skill_name)
        await self.handle_learn_skill_menu(learned_msg)


    async def handle_move_execute(self, custom_id):
        # 👇 等級只是「建議」不是硬性門檻——真正把關的是 requires_boss/requires_boss_count
        # （能不能打贏上一個區域的 BOSS，或累積首殺過夠多區域 BOSS），能贏就代表有實力
        # 進去了，不需要再疊加一層等級限制卡人。req_level 依然會顯示在移動選單的按鈕
        # 標籤上，純粹給玩家參考「這裡建議幾級來」。
        lang = self.player.language
        target_area = custom_id.replace("move_to_", "")
        area_data = self.cog.areas.get(target_area, {})
        if not self._area_unlocked(area_data):
            requires_boss = area_data.get("requires_boss")
            if requires_boss:
                boss_name = self._boss_name_for_area(requires_boss, lang)
                self.log_message = t(lang, "menu.move_blocked_boss", "❌ 這條路還被封鎖著，得先擊敗【{boss_name}】才能通行。", boss_name=boss_name)
            else:
                self.log_message = t(lang, "menu.move_blocked_generic", "❌ 目前還無法前往這個區域。")
            self.build_main_menu()
            return
        self.player.current_area = target_area
        self.cog.save_players()
        area_name = tf(self.cog.areas[target_area], "area_name", lang)
        self.log_message = t(lang, "explore.arrived_at_area", "🗺️ 成功抵達了【{area_name}】。", area_name=area_name)
        self.build_main_menu()

    def _format_inventory_grouped(self, p, lang) -> str:
        """把背包依分類（武器/防具/飾品/消耗品/卷軸/雜物）整理，每項前面加上分類 emoji。"""
        cats = {"weapon": [], "armor": [], "accessory": [], "consumable": [], "scroll": [], "misc": []}
        for k, v in p.inventory.items():
            if v <= 0:
                continue
            item = self.cog.items.get(k, {})
            typ = item.get("type")
            entry = f"{tf(item, 'name', lang) or k} x{v}"
            if typ == "weapon":
                cats["weapon"].append(entry)
            elif typ == "armor":
                cats["armor"].append(entry)
            elif typ == "accessory":
                cats["accessory"].append(entry)
            elif typ in ("potion", "cure"):
                cats["consumable"].append(entry)
            elif typ == "skill_scroll":
                cats["scroll"].append(entry)
            else:
                cats["misc"].append(entry)
        headers = {
            "weapon": t(lang, "char.cat_weapon", "⚔️ 武器"),
            "armor": t(lang, "char.cat_armor", "🛡️ 防具"),
            "accessory": t(lang, "char.cat_accessory", "💍 飾品"),
            "consumable": t(lang, "char.cat_consumable", "🧪 消耗品"),
            "scroll": t(lang, "char.cat_scroll", "📜 卷軸"),
            "misc": t(lang, "char.cat_misc", "📦 雜物"),
        }
        sep = ", " if lang == "en" else "、"
        lines = [f"**{headers[key]}**: {sep.join(cats[key])}" for key in cats if cats[key]]
        text = "\n".join(lines)
        return text[:1020] if text else t(lang, "char.bag_empty", "空空如也")

    async def handle_status(self, interaction: discord.Interaction):
        p = self.player
        lang = p.language
        none_label = t(lang, "char.none", "無")
        if p.weapon:
            witem = self.cog.items.get(p.weapon, {})
            weapon_name = f"{item_emoji(witem)} {tf(witem, 'name', lang) or p.weapon}"
        else:
            weapon_name = none_label

        inv_desc = self._format_inventory_grouped(p, lang)

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
                f"🍀 LUCK: {getattr(p, 'base_luck', 0)}\n"
                f"{alloc_text}"
            ),
            inline=False,
        )
        status_embed.add_field(name=t(lang, "char.equipped_weapon", "配戴武器"), value=weapon_name, inline=True)
        status_embed.add_field(name=t(lang, "char.status_effects", "異常狀態"), value=format_status_list(p.status_effects, self.cog.status_effects, p.language), inline=True)
        if p.accessory:
            acc_item = self.cog.items.get(p.accessory, {})
            acc_name = f"{item_emoji(acc_item)} {tf(acc_item, 'name', lang) or p.accessory}"
            status_embed.add_field(name=t(lang, "char.accessory", "飾品"), value=acc_name, inline=True)
        skill_list = ", ".join([tf(self.cog.skills.get(s, {}), "name", lang) or s for s in p.skills]) or none_label
        status_embed.add_field(name=t(lang, "char.learned_skills", "已習技能"), value=skill_list, inline=True)
        status_embed.add_field(name=t(lang, "char.bag_contents", "行囊儲存物"), value=inv_desc, inline=False)

        await interaction.followup.send(embed=status_embed, ephemeral=True)

    # (label, custom_id 用的 stat key, emoji) —— 6 個屬性，每組（+1／手動輸入）都要跨
    # 兩排才放得下（Discord 每排最多 5 顆按鈕），版面固定用 row 0/1（+1）與 2/3（手動輸入）。
    # 「手動輸入」開的是 StatPointModal（單一屬性、單一數字欄位）：打小數字=精準微調、
    # 打一個很大的數字＝等於全押到這項，取代原本另外一顆 All-in 按鈕的功能。
    _STAT_ALLOC_BUTTONS = (
        ("ATK", "atk", "⚔️"), ("VIT", "vit", "🛡️"), ("INT", "int", "✨"),
        ("SPD", "spd", "💨"), ("RES", "res", "🔰"), ("LUCK", "luck", "🍀"),
    )

    async def handle_stat_alloc_menu(self, notice=""):
        self.clear_items()
        lang = self.player.language
        prefix = notice + "\n\n" if notice else ""
        unspent = get_unspent_points(self.player)
        self.log_message = (
            prefix
            + t(lang, "char.stat_alloc_header", "📊 【屬性分配】每級 2 點，死亡後重置。\n")
            + format_stat_alloc_summary(self.player)
            + t(lang, "char.stat_alloc_legend", "\n\n攻擊+3 ATK/點 | 體力+12 HP & +2 DEF/點 | 魔力+4 MAG & +3 MP/點 | "
                "速度+2 SPD/點 | 抗性+2 RES/點 | 運氣+1 LUCK/點（提升暴擊率與掉寶率）")
        )
        if unspent > 0:
            self.log_message += t(
                lang,
                "char.stat_alloc_unspent",
                "\n\n**您還有 {unspent} 點屬性點可以分配！**\n(💡 點擊「+1」按鈕投資1點，點擊「手動輸入」打數字精準分配，數字打超過剩餘點數就等於全押)",
                unspent=unspent,
            )

            for label, key, emoji in self._STAT_ALLOC_BUTTONS:
                row = 0 if key != "luck" else 1
                self.add_action_button(label=f"+1 {label}", style=discord.ButtonStyle.primary, custom_id=f"stat_add_{key}", row=row, emoji=emoji)
            for label, key, emoji in self._STAT_ALLOC_BUTTONS:
                row = 2 if key != "luck" else 3
                self.add_action_button(label=t(lang, "char.btn_stat_manual", "輸入 {stat}", stat=label), style=discord.ButtonStyle.success, custom_id=f"stat_manual_{key}", row=row, emoji=emoji)

        last_row = 4 if unspent > 0 else 0
        reset_cost = self._stat_reset_cost()
        reset_label = (t(lang, "char.btn_reset_stats_paid", "重置所有屬性點（{cost}$）", cost=reset_cost) if reset_cost > 0
                       else t(lang, "char.btn_reset_stats", "重置所有屬性點"))
        self.add_action_button(label=reset_label, style=discord.ButtonStyle.danger, custom_id="btn_stat_reset", row=last_row, emoji="🔄")
        self.add_action_button(label=t(lang, "char.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", row=last_row, emoji="🔙")

    async def handle_stat_add(self, stat_key: str, amount: int = 1):
        """amount 超過目前剩餘點數時直接封頂到剩餘點數——這樣手動輸入視窗打一個
        很大的數字（例如 999）就等於「全押」，不需要另外維護一顆 All-in 按鈕。"""
        from trpg.stats import get_unspent_points, recalc_player_stats, default_stat_alloc
        lang = self.player.language
        unspent = get_unspent_points(self.player)
        if unspent <= 0:
            await self.handle_stat_alloc_menu(t(lang, "char.no_points_left", "❌ 你沒有可用的屬性點了。"))
            return

        if not getattr(self.player, "stat_alloc", None):
            self.player.stat_alloc = default_stat_alloc()

        add_amount = min(amount, unspent)
        self.player.stat_alloc[stat_key] = self.player.stat_alloc.get(stat_key, 0) + add_amount
        recalc_player_stats(self.player, self.cog.items, heal_full=False)
        self.cog.save_players()
        await self.handle_stat_alloc_menu(t(lang, "char.points_invested", "✅ 已將 {add_amount} 點投入【{stat_key}】。", add_amount=add_amount, stat_key=stat_key.upper()))

    def _stat_reset_cost(self) -> int:
        """屬性重置費用：Lv.10 以下免費（新手試錯期），之後隨等級成長——
        後期洗點是金幣回收管道之一，也讓「隨便亂點再免費洗掉」有一點成本感。"""
        real = getattr(self.player, "real_player", self.player)
        return 0 if real.level < 10 else real.level * 30

    async def handle_stat_reset(self):
        lang = self.player.language
        cost = self._stat_reset_cost()
        if cost > 0 and not self.cog.try_spend(self.user_id, self.player, cost):
            await self.handle_stat_alloc_menu(t(lang, "char.stat_reset_no_gold", "❌ 重置屬性配點需要 {cost} 金幣，你的餘額不足！", cost=cost))
            return
        self.player.stat_alloc = default_stat_alloc()
        recalc_player_stats(self.player, self.cog.items, heal_full=False)
        self.cog.save_players()
        if cost > 0:
            await self.handle_stat_alloc_menu(t(lang, "char.stats_reset_paid_notice", "🔄 支付了 {cost} 金幣，已重置所有屬性配點，請重新分配。", cost=cost))
        else:
            await self.handle_stat_alloc_menu(t(lang, "char.stats_reset_notice", "🔄 已重置所有屬性配點，請重新分配。"))

    async def handle_rest(self):
        lang = self.player.language
        if self.player.current_hp == self.player.max_hp and self.player.current_mp == self.player.max_mp and not self.player.status_effects:
            self.log_message = t(lang, "char.rest_not_needed", "❓ 你精神飽滿，去睡覺只是在浪費錢。")
            return
        if not self.cog.try_spend(self.user_id, self.player, 20):
            self.log_message = t(lang, "char.cant_afford_inn", "❌ 你身上的硬幣連旅館的乾草床都租不起！去打怪賺錢！")
            return

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

        # 👇 玩家一進來就先進休息室補滿血，並決定要不要出商人。這些狀態存在玩家存檔
        # 的 tower_state（不是 View），關掉/重開面板或面板逾時都不會重置——不然玩家
        # 只要重打 /trpg 就能在同一層無限刷回滿血、無限重骰是否出神秘商人。
        t_state = self.player.tower_state
        if not t_state.get("safe_room_visited"):
            t_state["safe_room_visited"] = True

            # 滿血回魔
            self.player.current_hp = self.player.max_hp
            self.player.current_mp = self.player.max_mp

            # 10% 機率出商人，若出現則預先抽好商品 (防玩家反覆進出刷新)
            t_state["merchant_spawned"] = (random.random() < 0.10)
            if t_state["merchant_spawned"]:
                mystery_pool = [k for k, v in self.cog.items.items() if v.get("mystery_only") and v.get("price", 0) > 0]
                t_state["merchant_items"] = random.sample(mystery_pool, min(3, len(mystery_pool))) if mystery_pool else []
            else:
                t_state["merchant_items"] = []

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
        if self.player.tower_state.get("merchant_spawned"):
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
        items = self.player.tower_state.get("merchant_items", [])

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

    # ============================ 修羅鬥技場（隱藏連戰區域） ============================
    # 解鎖條件：累計首殺 6 個不同區域 BOSS（requires_boss_count，見 area_60colosseum.json）。
    # 三輪連戰、輪與輪之間不回復（藥水自理），終點是帶著聖盾+沉默+承傷上限三件套的
    # 不敗冠軍。首次通關送 champions_belt，之後每日可再挑戰拿金幣與卷軸機率。

    def _colosseum_state(self) -> dict:
        real = getattr(self.player, "real_player", self.player)
        if not isinstance(getattr(real, "colosseum_state", None), dict):
            real.colosseum_state = {"round": 0}
        return real.colosseum_state

    def build_colosseum_menu(self):
        self.clear_items()
        lang = self.player.language
        area = self.cog.areas.get(self.player.current_area, {})
        state = self._colosseum_state()
        total_rounds = len(area.get("rounds", [])) + 1  # 小怪輪 + 冠軍戰
        cur_round = min(state.get("round", 0), total_rounds - 1)

        today_str = datetime.today().strftime("%Y-%m-%d")
        cleared_today = self.player.daily_boss_kills.get(self.player.current_area) == today_str

        lines = [t(lang, "colosseum.title",
                   "🏟️ **【修羅鬥技場】**\n觀眾的咆哮聲震耳欲聾。這裡沒有退路——連續三輪死鬥，輪與輪之間傷勢不會恢復，"
                   "終點站著從未嘗過敗績的冠軍「剎羅」。")]
        if cleared_today:
            lines.append(t(lang, "colosseum.cleared_today", "\n🏆 你今天已經站上冠軍寶座了。明天再來衛冕吧！"))
        else:
            lines.append(t(lang, "colosseum.round_status", "\n⚔️ 目前進度：第 {cur}/{total} 輪", cur=cur_round + 1, total=total_rounds))
            if cur_round == total_rounds - 1:
                lines.append(t(lang, "colosseum.champion_next", "👑 下一戰：不敗冠軍・剎羅本人！"))
        self.log_message = "\n".join(lines)

        self.add_action_button(
            label=t(lang, "colosseum.btn_fight", "進入下一輪") if not cleared_today else t(lang, "colosseum.btn_done", "✅ 今日已通關"),
            style=discord.ButtonStyle.danger, custom_id="btn_colo_fight", row=0, emoji="🏟️",
            disabled=cleared_today,
        )
        self.add_action_button(label=t(lang, "menu.btn_move", "移動"), style=discord.ButtonStyle.secondary, custom_id="btn_move_menu", row=0, emoji="🗺️")
        self.add_action_button(label=t(lang, "menu.btn_status", "狀態"), style=discord.ButtonStyle.success, custom_id="btn_status", row=0, emoji="📜")
        self.add_action_button(label=t(lang, "menu.btn_potions", "藥水"), style=discord.ButtonStyle.secondary, custom_id="b_itm", row=1, emoji="🎒")
        self.add_action_button(label=t(lang, "menu.btn_log", "記錄"), style=discord.ButtonStyle.secondary, custom_id="btn_combat_history", row=1, emoji="📝")

    async def handle_colosseum_fight(self):
        lang = self.player.language
        if self.player.current_hp <= 0:
            self.log_message = t(lang, "menu.near_death_rest", "❌ 你快死掉了，請先回村莊休息！")
            return

        area = self.cog.areas.get(self.player.current_area, {})
        today_str = datetime.today().strftime("%Y-%m-%d")
        if self.player.daily_boss_kills.get(self.player.current_area) == today_str:
            self.build_colosseum_menu()
            return

        state = self._colosseum_state()
        rounds_cfg = area.get("rounds", [])
        cur_round = state.get("round", 0)

        if cur_round < len(rounds_cfg):
            cfg = rounds_cfg[cur_round]
            encounter = []
            for _ in range(min(3, cfg.get("count", 2))):
                mon = pick_random_monster(self.cog.monster_pool, cfg.get("floor", 60), want_boss=False, floor_scale=1.0)
                mon["is_colosseum"] = True
                mon["money_min"] = 0  # 鬥技場的報酬集中在冠軍戰與每日獎勵，小怪輪不發薪水
                mon["money_max"] = 0
                mon["exp"] = int(mon.get("exp", 0) * 0.5)
                encounter.append(mon)
            self.start_combat(encounter)
            self.log_message = t(lang, "colosseum.round_start",
                                 "🏟️ 【第 {n} 輪】鐵閘升起，{count} 名挑戰者同時衝入場中！觀眾的歡呼聲響徹雲霄！",
                                 n=cur_round + 1, count=len(encounter))
        else:
            champion = dict(area.get("champion", {}))
            champion["is_colosseum"] = True
            self.start_combat([champion])
            self.log_message = t(lang, "colosseum.champion_start",
                                 "🏟️ 【冠軍戰】全場忽然安靜下來。\n「剎羅」緩緩起身，聖盾的光輝包覆著他的身軀——"
                                 "「讓我看看，你配不配站在這裡。」")
        self.build_battle_menu()

    def on_colosseum_victory(self, primary_monster: dict) -> str:
        """鬥技場戰鬥勝利後由 combat._process_victory 呼叫：推進輪次／發放通關獎勵，回傳附加戰報。"""
        lang = self.player.language
        area = self.cog.areas.get(self.player.current_area, {})
        state = self._colosseum_state()

        if primary_monster.get("is_boss"):
            # 冠軍戰獲勝：重置輪次（每日鎖與卷軸獎勵由 _handle_boss_kill_rewards 處理）
            state["round"] = 0
            log = ""
            gold = area.get("clear_reward_gold", 0)
            if gold > 0:
                self.cog.adjust_bank(self.user_id, gold)
                log += "\n" + t(lang, "colosseum.clear_gold", "🏟️ 全場起立鼓掌！鬥技場獎勵你 {gold} 金幣！", gold=gold)
            belt_id = area.get("first_clear_reward_item")
            real = getattr(self.player, "real_player", self.player)
            if belt_id and real.inventory.get(belt_id, 0) <= 0 and real.accessory != belt_id:
                real.inventory[belt_id] = real.inventory.get(belt_id, 0) + 1
                belt_name = tf(self.cog.items.get(belt_id, {}), "name", lang) or belt_id
                log += "\n" + t(lang, "colosseum.first_clear_belt",
                                "👑 【新王加冕】剎羅解下腰間的【{belt}】拋給你：「它是你的了。下次…我不會再輸。」", belt=belt_name)
            return log

        state["round"] = state.get("round", 0) + 1
        total_rounds = len(area.get("rounds", [])) + 1
        return "\n" + t(lang, "colosseum.round_cleared",
                        "🏟️ 第 {n} 輪獲勝！鐵閘再度轟隆作響……（傷勢不會恢復，記得喝藥水再按下一輪）",
                        n=min(state["round"], total_rounds - 1))

    def _cave_direction_to_sword(self, nodes: dict, start_id: str) -> str | None:
        """BFS 找出從目前節點通往劍之石室的最短路徑，回傳第一步的方向
        （forward/left/right）；已在死路或無路可通則回傳 None（該往回走了）。
        給魔法之眼的指引功能用。"""
        from collections import deque
        if start_id == "sword_room":
            return None
        queue = deque([(start_id, None)])
        visited = {start_id}
        while queue:
            node_id, first_dir = queue.popleft()
            for direction, nxt in (nodes.get(node_id, {}).get("exits") or {}).items():
                if not nxt or nxt in visited:
                    continue
                step = first_dir or direction
                if nxt == "sword_room":
                    return step
                visited.add(nxt)
                queue.append((nxt, step))
        return None

    def _collect_cave_loot(self, node_id: str, node: dict, lang: str) -> str:
        """撿取節點上的物資（每個節點一人一生一次，記錄在 cave_state["looted_nodes"]）。
        標記 loot_requires_eye 的隱藏暗格只有魔法之眼能照出來。"""
        loot = node.get("loot")
        if not loot:
            return ""
        if node.get("loot_requires_eye") and not self._has_magic_eye():
            return ""
        cave_state = self.player.cave_state
        looted = cave_state.setdefault("looted_nodes", [])
        if node_id in looted:
            return ""
        looted.append(node_id)
        real = getattr(self.player, "real_player", self.player)
        names = []
        for item_id, qty in loot.items():
            real.inventory[item_id] = real.inventory.get(item_id, 0) + qty
            nm = tf(self.cog.items.get(item_id, {}), "name", lang) or item_id
            names.append(f"{nm} x{qty}")
        self.cog.save_players()
        sep = ", " if lang == "en" else "、"
        if node.get("loot_requires_eye"):
            return "\n\n" + t(lang, "cave.loot_found_eye", "🧿 魔法之眼透視石壁，照出了一處隱藏暗格！獲得：{items}", items=sep.join(names))
        return "\n\n" + t(lang, "cave.loot_found", "🎒 你在角落裡搜出了先人留下的物資：{items}", items=sep.join(names))

    def build_legend_cave_menu(self):
        self.clear_items()
        lang = self.player.language
        area_data = self.cog.areas.get("area_legend_cave", {})
        nodes = area_data.get("nodes", {})
        cave_state = self.player.cave_state
        node_id = cave_state.get("current_node", "entrance")
        node = nodes.get(node_id) or nodes.get("entrance", {})
        sword_id = area_data.get("sword_item", "hero_sword")
        has_eye = self._has_magic_eye()

        if node.get("is_sword_room") and not self.player.inventory.get(sword_id, 0):
            # 👇 沒有魔法之眼：聖劍被強大的隱匿結界藏著，石室看起來空無一物
            if not has_eye:
                self.log_message = t(
                    lang,
                    "cave.sword_room_hidden",
                    "🕯️ 你來到一座圓形石室——這裡的魔力波動濃烈得令人窒息，但放眼望去卻空無一物。\n"
                    "空氣中彷彿有什麼在微微震顫……肉眼看不見的東西。\n"
                    "（也許需要某種能「看穿萬物」的祕寶才能找到它。）",
                )
                self.add_action_button(label=t(lang, "char.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="cave_dir_back", emoji="🔙")
                return
            self.player.inventory[sword_id] = self.player.inventory.get(sword_id, 0) + 1
            sword_name = tf(self.cog.items.get(sword_id, {}), "name", lang) or sword_id
            self.cog.save_players()
            self.log_message = t(
                lang,
                "cave.sword_obtained_eye",
                "🕯️ {node_desc}\n\n🧿 魔法之眼灼熱地震動，石室中央的隱匿結界如薄紗般被撕開——"
                "石墩上，一把纏繞聖光的長劍顯露真身！\n"
                "✨ 你拔起了【{sword_name}】！這把劍似乎在期待著與魔王的決戰。\n（記得回村莊把它裝備上！）",
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

        # 🎒 節點物資（高級藥水等，一次性）
        self.log_message += self._collect_cave_loot(node_id, node, lang)

        # 🧿 魔法之眼指引：往劍之石室的下一步方向
        if has_eye and not self.player.inventory.get(sword_id, 0):
            direction = self._cave_direction_to_sword(nodes, node_id)
            if direction:
                dir_names = {
                    "forward": t(lang, "cave.dir_forward", "前方"),
                    "left": t(lang, "cave.dir_left", "左邊"),
                    "right": t(lang, "cave.dir_right", "右邊"),
                }
                self.log_message += "\n\n" + t(lang, "cave.eye_guidance", "🧿 魔法之眼微微發熱，指向【{direction}】……", direction=dir_names.get(direction, direction))
            else:
                self.log_message += "\n\n" + t(lang, "cave.eye_guidance_back", "🧿 魔法之眼黯淡無光——這條路不通，先返回上一個岔路吧。")

        exits = node.get("exits", {})
        if exits.get("forward"):
            self.add_action_button(label=t(lang, "cave.btn_forward", "前進"), style=discord.ButtonStyle.primary, custom_id="cave_dir_forward", emoji="⬆️")
        if exits.get("left"):
            self.add_action_button(label=t(lang, "cave.btn_left", "左邊"), style=discord.ButtonStyle.primary, custom_id="cave_dir_left", emoji="⬅️")
        if exits.get("right"):
            self.add_action_button(label=t(lang, "cave.btn_right", "右邊"), style=discord.ButtonStyle.primary, custom_id="cave_dir_right", emoji="➡️")
        # 🧪 洞窟每一步都會扣血，藥水按鈕直接放在面板上（走既有的道具選單）
        self.add_action_button(label=t(lang, "menu.btn_potions", "藥水"), style=discord.ButtonStyle.secondary, custom_id="b_itm", emoji="🎒")
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
        req_level = prestige_required_level(prestige)

        self.log_message = (
            prefix
            + t(lang, "prestige.hall_title", "🌟 【轉生殿堂】\n")
            + t(lang, "prestige.hall_intro", "在這裡，你可以超越冒險者的極限，重獲新生！\n")
            + t(lang, "prestige.current_level", "• 當前等級：Lv.{level} (轉生需要 Lv.{req})\n", level=self.player.level, req=req_level)
            + t(lang, "prestige.current_count", "• 當前轉生次數：{prestige} 轉\n", prestige=prestige)
            + t(lang, "prestige.current_bonus", "• 當前轉生被動增幅：全屬性 +{bonus}%\n\n", bonus=bonus)
            + t(lang, "prestige.rules_title", "⚠️ 【轉生規則說明】\n")
            + t(lang, "prestige.rule_1", "1. 轉生將使你的等級重置回 Lv.1，EXP 歸零，並重置屬性配點。\n")
            + t(lang, "prestige.rule_2", "2. 轉生後你將獲得 1 層永久被動增幅，所有戰鬥屬性額外 +10%！\n")
            + t(lang, "prestige.rule_3", "3. 轉生會卸下你身上的武器、防具與飾品，並重置魔塔／地下城的目前樓層（已達成的里程碑勳章與獎杯不會消失）。\n")
            + t(lang, "prestige.rule_4", "4. 轉生不會清除你的背包道具、金幣與已學會的技能。\n")
            + t(lang, "prestige.rule_5", "5. 每次轉生後，下一次轉生所需的等級都會提高 {step} 級。", step=PRESTIGE_LEVEL_STEP)
        )

        if self.player.level >= req_level:
            self.add_action_button(label=t(lang, "prestige.btn_confirm", "確認轉生 (Lv.{req}+)", req=req_level), style=discord.ButtonStyle.danger, custom_id="btn_prestige_confirm", emoji="🌟")
        else:
            disabled_btn = discord.ui.Button(label=t(lang, "prestige.btn_level_locked", "等級不足 Lv.{req}", req=req_level), style=discord.ButtonStyle.secondary, disabled=True, emoji="❌")
            self.add_item(disabled_btn)

        self.add_action_button(label=t(lang, "prestige.btn_back_village", "返回村莊"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    async def handle_prestige_confirm(self):
        lang = self.player.language
        prestige = getattr(self.player, "prestige_count", 0)
        req_level = prestige_required_level(prestige)
        if self.player.level < req_level:
            self.log_message = t(lang, "prestige.fail_level", "❌ 轉生失敗：你的等級不足 Lv.{req}！", req=req_level)
            await self.handle_prestige_menu()
            return

        self.player.level = 1
        self.player.exp = 0
        self.player.stat_alloc = default_stat_alloc()
        self.player.prestige_count = prestige + 1

        # 👇 轉生重置：魔塔/地下城回到第一層、清空地下城暫時加成、卸下所有裝備（避免轉生後因殘留裝備直接過強）
        # 已裝備的技能欄也要一併清空——轉生只重置等級，不會清掉已學會的技能，若不清空
        # equipped_skills，玩家可以帶著轉生前遠超 Lv.1 的技能繼續裝備使用，等於繞過等級門檻。
        # 技能本身仍留在 player.skills，練回等級後可以重新裝備。
        self.player.equipped_skills = []
        self.player.tower_floor = 1
        self.player.dungeon_state = {"floor": 1, "choices": [], "in_run": False}
        self.player.dungeon_buffs = {}
        self.player.weapon = None
        self.player.armor = None
        self.player.accessory = None
        self.player.daily_boss_kills = {}
        self.player.tower_state = {"safe_room_visited": False, "merchant_spawned": False, "merchant_items": []}

        recalc_player_stats(self.player, self.cog.items, heal_full=True)
        self.cog.save_players()

        self.log_message = t(
            lang,
            "prestige.success",
            "🌟 恭喜成功轉生！你已重回 Lv.1，並永久獲得 +{bonus}% 的全屬性增幅！\n（魔塔/地下城樓層已重置，武器/防具/飾品已卸下）",
            bonus=self.player.prestige_count * 10,
        )
        self.build_main_menu()

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
        elif state == "skill_equip":
            await self.handle_skill_equip_menu(paging=True)
        elif state == "craft":
            await self.handle_craft_menu(paging=True)
        else:
            self.build_main_menu()

    
