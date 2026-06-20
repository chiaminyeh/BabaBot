import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import random
from datetime import datetime

from trpg_combat import TRPGCombat, exp_to_next_level, get_sell_price, get_player_atk, get_player_def, get_player_magic
from trpg_status import format_status_list, activate_jester_immunity, clear_all_status, cure_by_item, get_daily_jester_immunity
from trpg_stats import (
    default_stat_alloc,
    migrate_player_stats,
    recalc_player_stats,
    get_unspent_points,
    format_stat_alloc_summary,
    get_potion_heal_target,
    STAT_KEYS,
)

DATA_DIR = "trpg_data"

class StatAllocModal(discord.ui.Modal, title="批量分配屬性點"):
    atk = discord.ui.TextInput(label="攻擊 (ATK)", default="0", max_length=3)
    vit = discord.ui.TextInput(label="體力 (VIT) - 加血量與防禦", default="0", max_length=3)
    int_stat = discord.ui.TextInput(label="智力 (INT) - 加魔攻與魔力", default="0", max_length=3)
    spd = discord.ui.TextInput(label="速度 (SPD) - 加行動次數與閃避", default="0", max_length=3)
    res = discord.ui.TextInput(label="抗性 (RES)", default="0", max_length=3)

    def __init__(self, game_view):
        super().__init__()
        self.game_view = game_view

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            a = int(self.atk.value.strip() or "0")
            v = int(self.vit.value.strip() or "0")
            i = int(self.int_stat.value.strip() or "0")
            s = int(self.spd.value.strip() or "0")
            r = int(self.res.value.strip() or "0")
            if any(v < 0 for v in (a, v, i, s, r)):
                raise ValueError
        except ValueError:
            await interaction.followup.send("❌ 點數無效，請輸入大於等於 0 的整數！", ephemeral=True)
            return

        total_add = a + v + i + s + r
        from trpg_stats import get_unspent_points, recalc_player_stats, default_stat_alloc
        unspent = get_unspent_points(self.game_view.player)

        if total_add > unspent:
            await interaction.followup.send(f"❌ 點數不足！你剩餘 {unspent} 點，但嘗試分配 {total_add} 點。", ephemeral=True)
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

        self.game_view.log_message = f"✅ 成功分配了 {total_add} 點屬性！"
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
        super().__init__()
        self.game_view = game_view

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        prompt = (
            "你是新手村的老村長，睿智慈祥，用簡短回答冒險者的問題。"
            f"冒險者問：「{self.question.value}」"
            "請在 60 字以內回答，可以給新手有用的遊戲提示（探索、商店、技能卷軸、BOSS 等）。"
        )
        ai_response = await self.game_view.cog.generate_npc_dialogue(prompt)
        self.game_view.log_message = f"🧓 老村長緩緩開口：\n「{ai_response}」"
        try:
            await interaction.message.edit(embed=self.game_view.generate_embed(), view=self.game_view)
        except Exception as e:
            print(f"老村長回覆 UI 更新失敗: {e}")
        await interaction.followup.send("老村長已回答，請看冒險面板！", ephemeral=True)


class TradeTargetModal(discord.ui.Modal, title="發起交易"):
    target_id = discord.ui.TextInput(
        label="對方 Discord ID",
        placeholder="輸入對方的數字 ID",
        max_length=20,
    )
    offer_money = discord.ui.TextInput(
        label="附帶金幣（可填 0）",
        placeholder="0",
        max_length=10,
        required=False,
        default="0",
    )

    def __init__(self, game_view, item_id: str, qty: int):
        super().__init__()
        self.game_view = game_view
        self.item_id = item_id
        self.qty = qty

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            target_id = str(int(self.target_id.value.strip()))
        except ValueError:
            await interaction.followup.send("❌ 請輸入有效的數字 ID。", ephemeral=True)
            return

        if target_id == self.game_view.user_id:
            await interaction.followup.send("❌ 不能跟自己交易。", ephemeral=True)
            return

        try:
            money = max(0, int(self.offer_money.value.strip() or "0"))
        except ValueError:
            await interaction.followup.send("❌ 金幣數量無效。", ephemeral=True)
            return

        sender = self.game_view.player
        if sender.inventory.get(self.item_id, 0) < self.qty:
            await interaction.followup.send("❌ 物品數量不足。", ephemeral=True)
            return

        target_player = self.game_view.cog.get_player(target_id)
        item_name = self.game_view.cog.items.get(self.item_id, {}).get("name", self.item_id)
        sender_name = interaction.user.display_name

        offer = {
            "from_id": self.game_view.user_id,
            "from_name": sender_name,
            "item_id": self.item_id,
            "qty": self.qty,
            "money": money,
        }
        if not hasattr(target_player, "trade_inbox") or target_player.trade_inbox is None:
            target_player.trade_inbox = []
        target_player.trade_inbox.append(offer)
        self.game_view.cog.save_players()

        money_text = f" + {money}$" if money > 0 else ""
        self.game_view.log_message = f"📤 已向 ID {target_id} 發起交易：{item_name} x{self.qty}{money_text}"
        await interaction.followup.send("交易邀請已送出！對方可在【玩家交易】→【交易信箱】接受。", ephemeral=True)
        try:
            await interaction.message.edit(embed=self.game_view.generate_embed(), view=self.game_view)
        except Exception:
            pass

class BuyItemModal(discord.ui.Modal, title="批量購買"):
    qty = discord.ui.TextInput(
        label="請輸入購買數量",
        placeholder="例如：5",
        default="1",
        max_length=3,
    )

    def __init__(self, game_view, item_id: str):
        super().__init__()
        self.game_view = game_view
        self.item_id = item_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            amount = int(self.qty.value.strip())
            if amount <= 0: raise ValueError
        except ValueError:
            await interaction.followup.send("❌ 數量無效，請輸入正整數！", ephemeral=True)
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
        super().__init__()
        self.game_view = game_view
        self.item_id = item_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            amount = int(self.qty.value.strip())
            if amount <= 0: raise ValueError
        except ValueError:
            await interaction.followup.send("❌ 數量無效，請輸入正整數！", ephemeral=True)
            return
            
        await self.game_view.execute_sell(self.item_id, amount)
        try:
            await interaction.message.edit(embed=self.game_view.generate_embed(), view=self.game_view)
        except Exception:
            pass

class TRPGPlayer:
    def __init__(self, user_id):
        self.id = str(user_id)
        self.level = 1
        self.exp = 0
        self.max_hp = 60
        self.current_hp = 60
        self.base_atk = 12
        self.base_def = 4
        self.weapon = None
        self.armor = None
        self.current_area = "area_00village"
        self.inventory = {"health_potion": 2}  # 初始送兩罐藥水
        self.last_shop_refresh = ""
        self.shop_items = []
        self.stats = {
            "monsters_killed": 0,
            "total_deaths": 0,
            "money_spent": 0
        }
        self.achievements = [] # 存放已解鎖的成就 ID
        self.active_quests = {}  # 存放進行中的任務，例如 {"quest_001": {"progress": 2}}
        self.completed_quests = [] # 存放已解鎖/完成的任務 ID
        self.skills = []          # 存放玩家學會的技能 ID，例如 ["fireball"]
        self.daily_boss_kills = {} # 記錄區域 BOSS 擊殺日期，例如 {"area_grassland": "2026-06-01"}
        self.max_mp = 25
        self.current_mp = 25
        self.status_effects = {}   # {"poison": {"turns": 3}}
        self.accessory = None      # 飾品，例如 jester_mask
        self.jester_immunity_date = ""
        self.trade_inbox = []
        self.stat_alloc = default_stat_alloc()
        self.base_magic = 0
        self.base_res = 0
        self.mystery_merchant_date = ""
        self.mystery_shop_active = False
        self.mystery_shop_items = []
        self.tower_floor = 1

    def add_exp(self, amount, items=None):
        self.exp += amount
        needed = exp_to_next_level(self.level)
        leveled_up = False

        while self.exp >= needed:
            self.exp -= needed
            self.level += 1
            needed = exp_to_next_level(self.level)
            leveled_up = True

        recalc_player_stats(self, items or {}, heal_full=leveled_up)
        return leveled_up

    def to_dict(self):
        return self.__dict__

    @classmethod
    def from_dict(cls, data):
        player = cls(data["id"])
        for key, val in data.items():
            if key != "id":
                setattr(player, key, val)
        if not getattr(player, "skills", None):
            player.skills = []
        if not getattr(player, "daily_boss_kills", None):
            player.daily_boss_kills = {}
        if not hasattr(player, "max_mp"):
            player.max_mp = 25 + (player.level - 1) * 5
        if not hasattr(player, "current_mp"):
            player.current_mp = player.max_mp
        if not getattr(player, "status_effects", None):
            player.status_effects = {}
        if not hasattr(player, "accessory"):
            player.accessory = None
        if not hasattr(player, "jester_immunity_date"):
            player.jester_immunity_date = ""
        if not getattr(player, "trade_inbox", None):
            player.trade_inbox = []
        if not hasattr(player, "armor"):
            player.armor = None
        if not hasattr(player, "tower_floor"):
            player.tower_floor = 1
        return player


def _item_shop_level_ok(player, item_id: str, item_data: dict, skills: dict) -> bool:
    if item_data.get("mystery_only"):
        return False
    req = item_data.get("exclusive_level", 0)
    if req > player.level:
        return False
    if item_data.get("type") == "skill_scroll":
        skill = skills.get(item_data.get("teaches", ""), {})
        if skill.get("req_level", 1) > player.level:
            return False
    return True


class TRPGGameView(discord.ui.View):
    def __init__(self, cog, user_id):
        super().__init__(timeout=600)  # 10分鐘不操作才超時
        self.cog = cog
        self.user_id = str(user_id)
        self.player = cog.get_player(user_id)
        
        # 戰鬥暫存狀態
        self.in_battle = False
        self.active_monster = None
        self.active_monster_id = None
        self.monster_hp = 0
        self.combat = TRPGCombat(self)

        self.log_message = "歡迎來到冒險世界！請使用下方按鈕進行探索。"
        self.build_main_menu()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("這不是你的冒險面板，請自己輸入 `/trpg` 開一盤！", ephemeral=True)
            return False
        return True

    # 🛠️ 修復核心：自訂一個按鈕產生器，確實綁定 callback！
    def add_action_button(self, label, style, custom_id, row=None, emoji=None):
        btn = discord.ui.Button(label=label, style=style, custom_id=custom_id, row=row, emoji=emoji)
        
        # 捕捉這個按鈕專屬的 custom_id 並轉交給全域處理函數
        async def callback(interaction: discord.Interaction):
            await self.global_callback(interaction, custom_id)
            
        btn.callback = callback  # 這行就是上次漏掉的靈魂
        self.add_item(btn)

    # 負責接收所有按鈕點擊的總管
    async def global_callback(self, interaction: discord.Interaction, custom_id: str):
        if custom_id == "btn_ask_chief":
            await interaction.response.send_modal(ElderChiefModal(self))
            return
        elif custom_id.startswith("trade_item_"):
            item_id = custom_id.replace("trade_item_", "")
            await interaction.response.send_modal(TradeTargetModal(self, item_id, 1))
            return
        elif custom_id.startswith("buy_"):
            item_id = custom_id.replace("buy_", "")
            await interaction.response.send_modal(BuyItemModal(self, item_id))
            return
        elif custom_id.startswith("sell_"):
            item_id = custom_id.replace("sell_", "")
            await interaction.response.send_modal(SellItemModal(self, item_id))
            return
        # 👇 攔截批量分配的按鈕，彈出 Modal
        elif custom_id == "btn_stat_bulk":
            await interaction.response.send_modal(StatAllocModal(self))
            return

        await interaction.response.defer()

        # 執行對應的按鈕邏輯
        if custom_id == "btn_explore": await self.handle_explore()
        elif custom_id == "btn_move_menu": await self.handle_move_menu()
        elif custom_id.startswith("move_to_"): await self.handle_move_execute(custom_id)
        elif custom_id == "btn_status" or custom_id == "b_sta": await self.handle_status(interaction)
        elif custom_id == "btn_rest": await self.handle_rest()
        elif custom_id == "btn_shop_menu": await self.handle_shop_menu()
        elif custom_id == "btn_shop_refresh": await self.handle_shop_refresh()
        elif custom_id == "btn_shop_sell": await self.handle_sell_menu()
        elif custom_id == "btn_trade_menu": await self.handle_trade_menu()
        elif custom_id == "btn_trade_send": await self.handle_trade_pick_item()
        elif custom_id == "btn_trade_inbox": await self.handle_trade_inbox()
        elif custom_id.startswith("tacc_"): await self.handle_trade_accept(custom_id)
        elif custom_id.startswith("trej_"): await self.handle_trade_reject(custom_id)
        elif custom_id == "btn_quest_menu": await self.handle_quest_menu()
        elif custom_id == "btn_equip_menu": await self.handle_equip_menu()
        elif custom_id.startswith("equip_"): 
            w_id = custom_id.replace("equip_", "")
            await self.handle_equip_action(w_id, equip=True)
        elif custom_id.startswith("unequip_"):
            w_id = custom_id.replace("unequip_", "")
            await self.handle_equip_action(w_id, equip=False)
        elif custom_id.startswith("acc_"):
            a_id = custom_id.replace("acc_", "")
            await self.handle_accessory_action(a_id, equip=True)
        elif custom_id.startswith("unacc_"):
            a_id = custom_id.replace("unacc_", "")
            await self.handle_accessory_action(a_id, equip=False)
        elif custom_id.startswith("accept__"):
            quest_id = custom_id.replace("accept__", "")
            await self.handle_quest_accept(interaction, quest_id)
        elif custom_id.startswith("turn_in_"): 
            quest_id = custom_id.replace("turn_in_", "")
            await self.handle_quest_turn_in(interaction, quest_id)
        elif custom_id.startswith("quest_info__"):
            quest_id = custom_id.replace("quest_info__", "")
            await self.handle_quest_info(quest_id)
        elif custom_id == "btn_boss_explore": await self.handle_boss_explore()
        elif custom_id == "btn_skill_learn": await self.handle_learn_skill_menu()
        elif custom_id.startswith("learn_"):
            skill_id = custom_id.replace("learn_", "")
            await self.handle_learn_skill(skill_id)
        elif custom_id == "btn_stat_alloc": await self.handle_stat_alloc_menu()
        elif custom_id == "btn_stat_reset": await self.handle_stat_reset()
        elif custom_id.startswith("stat_add_"):
            stat_key = custom_id.replace("stat_add_", "")
            await self.handle_stat_add(stat_key)
        elif custom_id == "btn_back_main": 
            self.log_message = "回到了主選單。"
            self.build_main_menu()

        # 👇 魔塔專屬按鈕（修正原本重複與錯誤的區塊）
        elif custom_id == "btn_tower_safe_room": 
            await self.handle_tower_safe_room(revisit=True)
        elif custom_id == "btn_tower_merchant": 
            await self.handle_tower_merchant()
        elif custom_id == "btn_tower_next":
            await self.handle_tower_explore()
            
        # 戰鬥相關
        elif custom_id == "b_atk": await self.handle_battle_attack()
        elif custom_id == "b_def": 
            self.log_message = self.combat.defend()
            self.build_battle_menu()
        elif custom_id == "b_dod": 
            self.log_message = self.combat.dodge()
            self.build_battle_menu()
        elif custom_id == "b_ski": await self.handle_skill_menu()
        elif custom_id.startswith("skill_"):
            skill_id = custom_id.replace("skill_", "")
            await self.handle_use_skill(skill_id)
        elif custom_id == "b_itm": await self.handle_item_menu()  
        elif custom_id.startswith("use_item_"): await self.handle_use_item(custom_id) 
        elif custom_id == "b_fle": await self.handle_battle_flee()
        elif custom_id == "btn_back_battle": self.build_battle_menu()

        # 更新畫面
        try:
            await interaction.message.edit(embed=self.generate_embed(), view=self)
        except Exception as e:
            print(f"UI更新失敗: {e}")

    # --- UI 構建分流 (全部改用 add_action_button) ---

    def build_main_menu(self):
        self.clear_items()
        self.in_battle = False
        self.active_monster = None
        self.active_monster_id = None

        if "village" in self.player.current_area:
            self.add_action_button(label="移動區域", style=discord.ButtonStyle.secondary, custom_id="btn_move_menu", row=0, emoji="🗺️")
            self.add_action_button(label="查看狀態", style=discord.ButtonStyle.success, custom_id="btn_status", row=0, emoji="📜")
            self.add_action_button(label="裝備管理", style=discord.ButtonStyle.secondary, custom_id="btn_equip_menu", row=0, emoji="🛡️")
            self.add_action_button(label="村莊商店", style=discord.ButtonStyle.primary, custom_id="btn_shop_menu", row=1, emoji="🛒")
            self.add_action_button(label="旅館休息 (20$)", style=discord.ButtonStyle.secondary, custom_id="btn_rest", row=1, emoji="💤")
            self.add_action_button(label="任務大廳", style=discord.ButtonStyle.success, custom_id="btn_quest_menu", row=1, emoji="🪧")
            self.add_action_button(label="請教老村長", style=discord.ButtonStyle.secondary, custom_id="btn_ask_chief", row=2, emoji="🧓")
            self.add_action_button(label="技能修練", style=discord.ButtonStyle.primary, custom_id="btn_skill_learn", row=2, emoji="📖")
            self.add_action_button(label="屬性分配", style=discord.ButtonStyle.primary, custom_id="btn_stat_alloc", row=2, emoji="📊")
            # self.add_action_button(label="玩家交易", style=discord.ButtonStyle.secondary, custom_id="btn_trade_menu", row=2, emoji="🤝")
        else:
            # 🛠️ 新增：野外區域限定的每日 BOSS 按鈕
            self.add_action_button(label="區域探索", style=discord.ButtonStyle.primary, custom_id="btn_explore", row=0, emoji="⚔️")
            self.add_action_button(label="移動區域", style=discord.ButtonStyle.secondary, custom_id="btn_move_menu", row=0, emoji="🗺️")
            self.add_action_button(label="查看狀態", style=discord.ButtonStyle.success, custom_id="btn_status", row=0, emoji="📜")
            self.add_action_button(label="裝備管理", style=discord.ButtonStyle.secondary, custom_id="btn_equip_menu", row=0, emoji="🛡️")
            self.add_action_button(label="使用藥水", style=discord.ButtonStyle.secondary, custom_id="b_itm", row=1, emoji="🎒")
            self.add_action_button(label="👹 挑戰區域 BOSS", style=discord.ButtonStyle.danger, custom_id="btn_boss_explore", row=1)

    def process_death(self, log: str, reason: str = "💀 你倒下了...") -> str:
        """統一處理死亡邏輯，回傳組合好的 log 訊息"""
        self.player.current_hp = 0
        self.player.exp = self.player.exp // 2  # 死亡懲罰：經驗值減半
        
        from trpg_status import clear_all_status
        clear_all_status(self.player)
        
        self.player.stats["total_deaths"] = self.player.stats.get("total_deaths", 0) + 1
        self.cog.save_players()
        
        # 清除戰鬥狀態
        self.in_battle = False
        self.active_monster = None
        self.active_monster_id = None
        self.combat._clear_battle_state()
        
        # 強制導回主選單
        self.build_main_menu()
        
        # 回傳最終的戰報文字
        return log + f"\n\n{reason}\n(當前經驗值減半。)"
    
    def build_battle_menu(self):
        self.clear_items()
        self.in_battle = True
        self.add_action_button(label="攻擊", style=discord.ButtonStyle.danger, custom_id="b_atk", row=0, emoji="🗡️")
        self.add_action_button(label="技能", style=discord.ButtonStyle.success, custom_id="b_ski", row=0, emoji="✨")
        self.add_action_button(label="防禦", style=discord.ButtonStyle.primary, custom_id="b_def", row=0, emoji="🛡️")
        self.add_action_button(label="閃避", style=discord.ButtonStyle.primary, custom_id="b_dod", row=0, emoji="💨")
        
        self.add_action_button(label="道具", style=discord.ButtonStyle.secondary, custom_id="b_itm", row=1, emoji="🎒")
        self.add_action_button(label="逃跑", style=discord.ButtonStyle.secondary, custom_id="b_fle", row=1, emoji="🏃")
        self.add_action_button(label="狀態", style=discord.ButtonStyle.success, custom_id="b_sta", row=1, emoji="📜")

    async def handle_move_menu(self):
        self.clear_items()
        self.log_message = "挑選你打算移動前往的下一個區域："
        for area_id, area in self.cog.areas.items():
            if area_id != self.player.current_area:
                req = area.get("req_level", 1)
                label = f"前往 {area['area_name']} (Lv.{req})"
                self.add_action_button(label=label, style=discord.ButtonStyle.primary, custom_id=f"move_to_{area_id}")
        self.add_action_button(label="返回", style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")
    
    async def handle_boss_explore(self):
        from datetime import datetime
        if self.player.current_hp <= 0:
            self.log_message = "❌ 你快死掉了，請先回村莊休息！"
            return
            
        area_data = self.cog.areas.get(self.player.current_area)
        boss_data = area_data.get("boss") if area_data else None
        
        if not boss_data:
            self.log_message = "📍 這個區域似乎沒有盤踞任何 BOSS..."
            return
            
        # 📆 檢查每日擊殺限制
        today_str = datetime.today().strftime('%Y-%m-%d')
        if self.player.daily_boss_kills.get(self.player.current_area) == today_str:
            self.log_message = f"❌ 這裡的 BOSS【{boss_data['name']}】今天已經被你討伐了。明天刷新後再來吧！"
            return
            
       # 遭遇 BOSS，複製數值進入戰鬥
        self.active_monster_id = boss_data.get("id", "boss")
        self.active_monster = dict(boss_data)
        
        # 👇 補上這行！動態計算並寫入 BOSS 速度，讓 UI 抓得到
        self.active_monster["spd"] = boss_data.get("spd", int(15 + self.player.level * 2.2))
        
        self.monster_hp = self.active_monster["max_hp"]
        
        self.log_message = f"🚨 【區域領主警告】 🚨\n大地在震動... 你驚動了隱藏的首領【{self.active_monster['name']}】！"
        self.build_battle_menu()

    async def handle_quest_info(self, quest_id: str):
        """處理點擊進行中任務，顯示任務詳情"""
        quest_info = self.cog.quests.get(quest_id)
        quest_data = self.player.active_quests.get(quest_id)
        if not quest_info or not quest_data:
            return
        
        # 抓取目標名稱（如果是打怪就抓 target_monster，收集就抓 target_item）
        target_name = quest_info.get("target_monster") or quest_info.get("target_item") or "未知目標"
        
        # 組合好通知文字，重新呼叫任務選單並把 notice 傳進去
        notice = (
            f"🔎 【任務追蹤】{quest_info['title']}\n"
            f"• 委託人：{quest_info['npc_name']}\n"
            f"• 目標：{target_name} ({quest_data['progress']}/{quest_info['target_count']})\n"
            f"• 報酬：{quest_info['reward_exp']} EXP, {quest_info['reward_money']} $"
        )
        await self.handle_quest_menu(notice=notice)
        
    async def handle_equip_menu(self, notice=""):
        self.clear_items()
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

        # 把上一層傳來的通知加在最上面
        self.log_message = (notice + "\n\n" if notice else "") + "🎒 【裝備管理】"
        
        c_weap = f"【{self.cog.items[self.player.weapon]['name']}】" if self.player.weapon else "無 (空手)"
        c_armr = f"【{self.cog.items[self.player.armor]['name']}】" if getattr(self.player, 'armor', None) else "無 (布衣)"
        c_accs = f"【{self.cog.items[self.player.accessory]['name']}】" if self.player.accessory else "無"
        
        self.log_message += f"\n👉 武器：{c_weap}\n👉 防具：{c_armr}\n👉 飾品：{c_accs}"

        for w_id in weapons_in_bag:
            w_data = self.cog.items[w_id]
            if w_id == self.player.weapon:
                self.add_action_button(label=f"卸下 {w_data['name']}", style=discord.ButtonStyle.danger, custom_id=f"unequip_{w_id}")
            else:
                self.add_action_button(label=f"裝備 {w_data['name']}", style=discord.ButtonStyle.success, custom_id=f"equip_{w_id}")

        for a_id in armors_in_bag:
            a_data = self.cog.items[a_id]
            if a_id == getattr(self.player, "armor", None):
                self.add_action_button(label=f"卸下 {a_data['name']}", style=discord.ButtonStyle.danger, custom_id=f"unequip_{a_id}")
            else:
                self.add_action_button(label=f"穿戴 {a_data['name']}", style=discord.ButtonStyle.primary, custom_id=f"equip_{a_id}")

        for acc_id in accessories_in_bag:
            acc_data = self.cog.items[acc_id]
            if acc_id == self.player.accessory:
                self.add_action_button(label=f"卸下 {acc_data['name']}", style=discord.ButtonStyle.danger, custom_id=f"unacc_{acc_id}")
            else:
                self.add_action_button(label=f"配戴 {acc_data['name']}", style=discord.ButtonStyle.success, custom_id=f"acc_{acc_id}")

        if not weapons_in_bag and not armors_in_bag and not accessories_in_bag:
            self.log_message += "\n\n背包裡沒有可裝備的物品。"

        self.add_action_button(label="返回", style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    async def handle_equip_action(self, item_id: str, equip: bool):
        item_data = self.cog.items.get(item_id)
        if not item_data: return

        if equip:
            req_lv = item_data.get("exclusive_level", item_data.get("req_level", 0))
            if req_lv > self.player.level:
                # 傳遞錯誤訊息給選單
                await self.handle_equip_menu(f"❌ 等級不足！裝備【{item_data['name']}】需要 Lv.{req_lv}。")
                return

            if item_data["type"] == "weapon":
                self.player.weapon = item_id
            elif item_data["type"] == "armor":
                self.player.armor = item_id

            recalc_player_stats(self.player, self.cog.items, heal_full=False)
            self.cog.save_players()
            await self.handle_equip_menu(f"🛡️ 成功裝備了【{item_data['name']}】！感覺自己變強了。")
        else:
            if item_data["type"] == "weapon":
                self.player.weapon = None
            elif item_data["type"] == "armor":
                self.player.armor = None

            recalc_player_stats(self.player, self.cog.items, heal_full=False)
            self.cog.save_players()
            await self.handle_equip_menu(f"🛡️ 卸下了【{item_data['name']}】。")

    async def handle_accessory_action(self, item_id: str, equip: bool):
        item_data = self.cog.items.get(item_id)
        if not item_data or item_data.get("type") != "accessory":
            return
        req_lv = item_data.get("exclusive_level", 0)
        if req_lv > self.player.level:
            await self.handle_equip_menu(f"❌ 需要 Lv.{req_lv} 才能配戴【{item_data['name']}】。")
            return

        if equip:
            self.player.accessory = item_id
            if item_id == "jester_mask":
                activate_jester_immunity(self.player)
            recalc_player_stats(self.player, self.cog.items, heal_full=False)
            self.cog.save_players()
            await self.handle_equip_menu(f"🎭 配戴了【{item_data['name']}】！")
        else:
            self.player.accessory = None
            recalc_player_stats(self.player, self.cog.items, heal_full=False)
            self.cog.save_players()
            await self.handle_equip_menu(f"🎭 卸下了【{item_data['name']}】。")

    # 👇 加上 notice 參數
    async def handle_quest_menu(self, notice=""):
        self.clear_items()
        
        # 👇 把傳進來的提示加上去
        prefix = notice + "\n\n" if notice else ""
        self.log_message = prefix + "🪧 【布告欄與 NPC 任務大廳】\n"
        
        # --- 1. 顯示進行中的任務 ---
        if self.player.active_quests:
            self.log_message += "\n📜 【進行中的委託】"
            for quest_id, quest_data in self.player.active_quests.items():
                quest_info = self.cog.quests.get(quest_id)
                if not quest_info: continue
                is_completed = quest_data["progress"] >= quest_info["target_count"]
                
                if is_completed:
                    self.add_action_button(label=f"回報：{quest_info['title']}", style=discord.ButtonStyle.success, custom_id=f"turn_in_{quest_id}")
                    self.log_message += f"\n✅ {quest_info['title']} (找 {quest_info['npc_name']} 領賞)"
                else:
                    # 👇 這裡把 custom_id 從 disabled_ 改成 quest_info__
                    self.add_action_button(
                        label=f"進行中：{quest_info['title']} ({quest_data['progress']}/{quest_info['target_count']})", 
                        style=discord.ButtonStyle.secondary, 
                        custom_id=f"quest_info__{quest_id}"
                    )
                    self.log_message += f"\n⏳ {quest_info['title']} - 進度: {quest_data['progress']}/{quest_info['target_count']}"
        
        # --- 2. 顯示可接取的任務 ---
        available_quests = False
        quest_pool_log = ""
        for q_id, q_info in self.cog.quests.items():
            # 👇 關鍵新增：檢查等級是否符合
            if self.player.level < q_info.get("req_level", 1):
                continue
                
            # 如果玩家沒接過，也沒完成過，就顯示在佈告欄上
            if q_id not in self.player.active_quests and q_id not in self.player.completed_quests:
                available_quests = True
                self.add_action_button(label=f"接取：{q_info['title']}", style=discord.ButtonStyle.primary, custom_id=f"accept__{q_id}")
                quest_pool_log += f"\n💬 {q_info['npc_name']} 正在張貼委託：『{q_info['title']}』"
                
        if available_quests:
            self.log_message += "\n\n🪧 【可接取的全新委託】" + quest_pool_log
        elif not self.player.active_quests:
            self.log_message += "\n目前沒有符合你實力的委託可接，先去練練等級吧！"

        self.add_action_button(label="返回村莊", style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    async def handle_quest_accept(self, interaction: discord.Interaction, quest_id: str):
        quest_info = self.cog.quests.get(quest_id)
        if not quest_info: return
        
        # 1. 塞入玩家的進行中任務
        self.player.active_quests[quest_id] = {"progress": 0}
        self.cog.save_players()
        
        # 2. 顯示 AI 思考過場
        self.log_message = f"⏳ 正在與 {quest_info['npc_name']} 對話中..."
        self.build_main_menu()
        await interaction.message.edit(embed=self.generate_embed(), view=self)
        
        # 3. 動態讀取 Accept Prompt 並替換變數 (支援打怪與收集)
        raw_prompt = quest_info.get("accept_prompt", "你是一個 NPC，請用一句話叫玩家去工作。")
        target_name = quest_info.get("target_monster") or quest_info.get("target_item") or "目標"
        prompt = raw_prompt.replace("{count}", str(quest_info.get('target_count', 1))) \
                           .replace("{monster}", target_name) \
                           .replace("{item}", target_name)
        
        # 觸發 AI 生成對話
        ai_response = await self.cog.generate_npc_dialogue(prompt)
        
        self.log_message = f"💬 {quest_info['npc_name']}：\n「{ai_response}」\n\n系統提示：已成功接取任務【{quest_info['title']}】！"
        self.build_main_menu()

    async def handle_quest_turn_in(self, interaction: discord.Interaction, quest_id: str):
        quest_info = self.cog.quests.get(quest_id)
        
        # 1. 給予實質獎勵與存檔
        self.player.add_exp(quest_info["reward_exp"], self.cog.items)
        current_bal = self.cog.bank.get(int(self.user_id), [0])[0]
        self.cog.bank[int(self.user_id)] = (current_bal + quest_info["reward_money"], self.cog.bank.get(int(self.user_id), (0, False))[1])
        del self.player.active_quests[quest_id]
        self.player.completed_quests.append(quest_id)
        self.cog.save_players()

        # 2. 顯示讀取中畫面並手動推播給使用者 (NPC 名稱也動態化)
        self.log_message = f"⏳ {quest_info['npc_name']} 正在打量你的戰利品...\n(AI 思考中，請稍候)"
        self.build_main_menu() 
        await interaction.message.edit(embed=self.generate_embed(), view=self)
        
        # 🛠️ 3. 動態讀取 Prompt 並替換變數！
        raw_prompt = quest_info.get("turn_in_prompt", "你是一個 NPC，請稱讚玩家完成了任務。")
        target_name = quest_info.get("target_monster") or quest_info.get("target_item") or "目標"
        prompt = raw_prompt.replace("{count}", str(quest_info.get('target_count', 1))) \
                           .replace("{monster}", target_name) \
                           .replace("{item}", target_name)
        
        # 觸發 AI 生成對話
        ai_response = await self.cog.generate_npc_dialogue(prompt)
        
        self.log_message = f"💬 {quest_info['npc_name']}：\n「{ai_response}」\n\n💰 獲得了 {quest_info['reward_exp']} EXP 與 {quest_info['reward_money']} 塊錢！"
        self.build_main_menu()
    
    def _format_shop_item_line(self, item_id: str) -> str:
        item = self.cog.items.get(item_id, {})
        req = item.get("exclusive_level", 0)
        req_str = f" | 需 Lv.{req}" if req else ""
        line = f"• {item.get('name', item_id)}{req_str} | {item.get('price', 0)}$ | {item.get('desc', '')}"
        if item.get("type") == "skill_scroll":
            skill = self.cog.skills.get(item.get("teaches", ""), {})
            if skill.get("desc"):
                line += f"\n  ↳ {skill['desc']}"
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

        self.clear_items()
        lines = []
        prefix = (notice + "\n\n" if notice else "")
        self.log_message = prefix + "🛒 【村莊雜貨鋪】今日限定貨架：\n"

        for item_id in self.player.shop_items:
            item = self.cog.items.get(item_id)
            if item:
                lines.append(self._format_shop_item_line(item_id))
                req = item.get("exclusive_level", 0)
                req_label = f" Lv.{req}" if req else ""
                label_text = f"買 {item['name']}{req_label} ({item['price']}$)"
                self.add_action_button(
                    label=label_text[:80],
                    style=discord.ButtonStyle.primary,
                    custom_id=f"buy_{item_id}",
                )

        if getattr(self.player, "mystery_shop_active", False) and self.player.mystery_shop_items:
            self.log_message += "\n\n🎭 【神秘商人 · 今日限定】\n"
            for item_id in self.player.mystery_shop_items:
                item = self.cog.items.get(item_id)
                if not item:
                    continue
                lines.append(self._format_shop_item_line(item_id))
                self.add_action_button(
                    label=f"🎭 {item['name']} ({item['price']}$)"[:80],
                    style=discord.ButtonStyle.success,
                    custom_id=f"buy_{item_id}",
                )

        self.log_message += "\n".join(lines)

        refresh_cost = 100 * (2 ** getattr(self.player, "shop_refresh_count", 0))

        self.add_action_button(label=f"刷新商店 ({refresh_cost}$)", style=discord.ButtonStyle.danger, custom_id="btn_shop_refresh", emoji="🔄")
        self.add_action_button(label="出售物品", style=discord.ButtonStyle.success, custom_id="btn_shop_sell", emoji="💰")
        self.add_action_button(label="返回村莊", style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    async def handle_shop_refresh(self):
        count = getattr(self.player, "shop_refresh_count", 0)
        cost = 100 * (2 ** count)
        user_uid = int(self.user_id)
        user_bal = self.cog.bank.get(user_uid, [0])[0]
        
        if user_bal < cost:
            await self.handle_shop_menu(f"❌ 金幣不足！手動進貨需要支付 {cost}$ 給老闆。")
            return
            
        self.cog.bank[user_uid] = (user_bal - cost, self.cog.bank.get(user_uid, (0, False))[1])
        self.cog.bot.baba.refresh_bank_file()
        
        self.player.shop_refresh_count = count + 1
        self.player.shop_items = []
        self.cog.save_players()

        await self.handle_shop_menu(f"🔄 支付了 {cost}$ 刷新商店！老闆為你進了一批新貨。")

    async def handle_sell_menu(self, notice=""):
        self.clear_items()
        sellable = []
        for item_id, count in self.player.inventory.items():
            if count > 0 and item_id != "jester_mask":
                item = self.cog.items.get(item_id)
                if item and get_sell_price(item_id, self.cog.items) > 0:
                    sellable.append(item_id)

        prefix = notice + "\n\n" if notice else ""
        if not sellable:
            self.log_message = prefix + "💰 【出售物品】\n沒有可以賣給商店的東西。"
        else:
            self.log_message = prefix + "💰 【出售物品】\n選擇要賣出的物品："
            for item_id in sellable:
                item = self.cog.items[item_id]
                price = get_sell_price(item_id, self.cog.items)
                count = self.player.inventory[item_id]
                self.add_action_button(
                    label=f"賣 {item['name']} ({price}$) x{count}",
                    style=discord.ButtonStyle.primary,
                    custom_id=f"sell_{item_id}",
                )

        self.add_action_button(label="返回商店", style=discord.ButtonStyle.secondary, custom_id="btn_shop_menu", emoji="🔙")

    async def handle_trade_menu(self):
        self.clear_items()
        inbox_count = len(getattr(self.player, "trade_inbox", []) or [])
        self.log_message = f"🤝 【玩家交易】\n待處理邀請：{inbox_count} 筆"
        self.add_action_button(label="發起交易", style=discord.ButtonStyle.primary, custom_id="btn_trade_send", emoji="📤")
        if inbox_count > 0:
            self.add_action_button(label=f"交易信箱 ({inbox_count})", style=discord.ButtonStyle.success, custom_id="btn_trade_inbox", emoji="📬")
        self.add_action_button(label="返回村莊", style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    async def handle_trade_pick_item(self):
        self.clear_items()
        items = [i for i, c in self.player.inventory.items() if c > 0 and i != "jester_mask"]
        if not items:
            self.log_message = "❌ 背包沒有可交易的物品。"
            self.add_action_button(label="返回", style=discord.ButtonStyle.secondary, custom_id="btn_trade_menu", emoji="🔙")
            return

        self.log_message = "📤 選擇要交易的物品："
        for item_id in items:
            item = self.cog.items[item_id]
            self.add_action_button(
                label=f"{item['name']} x{self.player.inventory[item_id]}",
                style=discord.ButtonStyle.primary,
                custom_id=f"trade_item_{item_id}",
            )
        self.add_action_button(label="返回", style=discord.ButtonStyle.secondary, custom_id="btn_trade_menu", emoji="🔙")

    async def handle_trade_inbox(self, notice=""):
        self.clear_items()
        inbox = getattr(self.player, "trade_inbox", []) or []
        prefix = notice + "\n\n" if notice else ""
        
        if not inbox:
            self.log_message = prefix + "📬 交易信箱是空的。"
            self.add_action_button(label="返回", style=discord.ButtonStyle.secondary, custom_id="btn_trade_menu", emoji="🔙")
            return

        self.log_message = prefix + "📬 【交易信箱】\n"
        for idx, offer in enumerate(inbox):
            item_name = self.cog.items.get(offer["item_id"], {}).get("name", offer["item_id"])
            money_text = f" + {offer['money']}$" if offer.get("money", 0) > 0 else ""
            self.log_message += f"\n• {offer['from_name']} 提供：{item_name} x{offer['qty']}{money_text}"
            self.add_action_button(label=f"接受 #{idx + 1}", style=discord.ButtonStyle.success, custom_id=f"tacc_{idx}")
            self.add_action_button(label=f"拒絕 #{idx + 1}", style=discord.ButtonStyle.danger, custom_id=f"trej_{idx}")
        self.add_action_button(label="返回", style=discord.ButtonStyle.secondary, custom_id="btn_trade_menu", emoji="🔙")

    async def handle_trade_accept(self, custom_id):
        idx = int(custom_id.replace("tacc_", ""))
        inbox = getattr(self.player, "trade_inbox", []) or []
        if idx >= len(inbox):
            await self.handle_trade_inbox("❌ 交易邀請已失效。")
            return

        offer = inbox[idx]
        sender = self.cog.get_player(offer["from_id"])
        item_id = offer["item_id"]
        qty = offer["qty"]
        money = offer.get("money", 0)

        if sender.inventory.get(item_id, 0) < qty:
            inbox.pop(idx)
            self.cog.save_players()
            await self.handle_trade_inbox("❌ 對方物品不足，交易取消。")
            return

        receiver_uid = int(self.user_id)
        receiver_bal = self.cog.bank.get(receiver_uid, [0])[0]
        if money > 0 and receiver_bal < money:
            await self.handle_trade_inbox(f"❌ 你的金幣不足，需要 {money}$。")
            return

        sender.inventory[item_id] -= qty
        if sender.inventory[item_id] <= 0:
            del sender.inventory[item_id]
        self.player.inventory[item_id] = self.player.inventory.get(item_id, 0) + qty

        if money > 0:
            sender_uid = int(offer["from_id"])
            sender_bal = self.cog.bank.get(sender_uid, [0])[0]
            self.cog.bank[sender_uid] = (sender_bal + money, self.cog.bank.get(sender_uid, (0, False))[1])
            self.cog.bank[receiver_uid] = (receiver_bal - money, self.cog.bank.get(receiver_uid, (0, False))[1])
            self.cog.bot.baba.refresh_bank_file()

        inbox.pop(idx)
        item_name = self.cog.items.get(item_id, {}).get("name", item_id)
        self.cog.save_players()
        await self.handle_trade_inbox(f"✅ 交易完成！獲得【{item_name}】x{qty}" + (f"，支付 {money}$" if money > 0 else ""))

    async def handle_trade_reject(self, custom_id):
        idx = int(custom_id.replace("trej_", ""))
        inbox = getattr(self.player, "trade_inbox", []) or []
        if idx < len(inbox):
            inbox.pop(idx)
            self.cog.save_players()
        await self.handle_trade_inbox("❌ 已拒絕交易邀請。")
        idx = int(custom_id.replace("trej_", ""))
        inbox = getattr(self.player, "trade_inbox", []) or []
        if idx < len(inbox):
            inbox.pop(idx)
            self.cog.save_players()
        self.log_message = "❌ 已拒絕交易邀請。"
        await self.handle_trade_inbox()

    async def handle_item_menu(self):
        self.clear_items()
        # 不要強制設定 self.in_battle = True，這樣才能支援戰鬥外使用
        self.log_message = "🎒 選擇要使用的道具："
        usable = []
        for item_id, count in self.player.inventory.items():
            if count > 0:
                item = self.cog.items.get(item_id)
                if item and item.get("type") in ("potion", "cure"):
                    usable.append(item_id)

        if not usable:
            self.log_message = "❌ 背包裡沒有可用的道具。"
            if self.in_battle:
                self.build_battle_menu()
            else:
                self.build_main_menu()
            return

        for item_id in usable:
            item = self.cog.items[item_id]
            self.add_action_button(
                label=f"{item['name']} x{self.player.inventory[item_id]}",
                style=discord.ButtonStyle.secondary,
                custom_id=f"use_item_{item_id}",
            )
            
        # 根據是否在戰鬥中，返回不同的選單
        back_id = "btn_back_battle" if self.in_battle else "btn_back_main"
        self.add_action_button(label="返回", style=discord.ButtonStyle.secondary, custom_id=back_id, emoji="🔙")

    async def handle_use_item(self, custom_id):
        item_id = custom_id.replace("use_item_", "")
        item = self.cog.items.get(item_id)
        if not item:
            self.log_message = "❌ 無效物品。"
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
            self.log_message = "❌ 無法使用此物品。"

        # 更新畫面
        if self.in_battle:
            self.build_battle_menu()
        else:
            self.build_main_menu()

    def use_potion_out_of_battle(self, item_id: str):
        """戰鬥外的藥水邏輯"""
        if self.player.inventory.get(item_id, 0) <= 0:
            item_name = self.cog.items.get(item_id, {}).get("name", "藥水")
            return f"❌ 你包包裡沒有【{item_name}】了！"

        item_data = self.cog.items.get(item_id, {})
        heal_target = get_potion_heal_target(item_data, item_id)

        if heal_target == "mp" and self.player.current_mp >= self.player.max_mp:
            return "❓ 你的魔力已經滿了，別浪費藥水。"
        if heal_target == "hp" and self.player.current_hp >= self.player.max_hp:
            return "❓ 你的生命值已經滿了，別浪費藥水。"

        self.player.inventory[item_id] -= 1
        if self.player.inventory[item_id] <= 0:
            del self.player.inventory[item_id]

        if "heal_percent" in item_data:
            if heal_target == "mp":
                heal = int(self.player.max_mp * item_data["heal_percent"])
                self.player.current_mp = min(self.player.max_mp, self.player.current_mp + heal)
                self.cog.save_players()
                return f"🧪 你喝下了藥水，回復了 {heal} 點魔力。"
            heal = int(self.player.max_hp * item_data["heal_percent"])
        else:
            heal = item_data.get("heal", 50)

        self.player.current_hp = min(self.player.max_hp, self.player.current_hp + heal)
        self.cog.save_players()
        return f"🧪 你喝下了藥水，回復了 {heal} 點生命值。"

    def use_cure_item_out_of_battle(self, item_id: str):
        """戰鬥外的解藥邏輯"""
        if self.player.inventory.get(item_id, 0) <= 0:
            return "❌ 背包裡沒有這個物品。"
            
        item = self.cog.items.get(item_id, {})
        cures = item.get("cures", [])
        
        cured = []
        for sid in cures:
            if sid in self.player.status_effects:
                cured.append(self.cog.status_effects.get(sid, {}).get("name", sid))
                del self.player.status_effects[sid]
                
        if not cured:
            return "❌ 你目前沒有這個物品能解除的異常狀態，省著點用吧！"
            
        self.player.inventory[item_id] -= 1
        if self.player.inventory[item_id] <= 0:
            del self.player.inventory[item_id]
        self.cog.save_players()
        return f"✨ 使用了【{item['name']}】，解除了：{'、'.join(cured)}"
        
    def _generate_action_bar(self, current, maximum, length=10):
            """生成文字版行動條"""
            if maximum <= 0: return "▱" * length
            filled = int(round((current / maximum) * length))
            filled = min(max(filled, 0), length)
            return "▰" * filled + "▱" * (length - filled)

    def generate_embed(self):
        embed = discord.Embed(color=discord.Color.dark_theme())
        p = self.player
        area_name = self.cog.areas.get(p.current_area, {}).get("area_name", "未知區域")
        user_bal = self.cog.bank.get(int(self.user_id), [0])[0]
        status_text = format_status_list(p.status_effects, self.cog.status_effects)
        state_text = "⚔️ 戰鬥中" if self.in_battle else "🌿 探索中"

        p_atk = get_player_atk(p, self.cog.items)
        p_def = get_player_def(p, self.cog.items)
        p_magic = get_player_magic(p, self.cog.items)
        p_spd = getattr(p, "base_spd", 0)
        p_res = getattr(p, "base_res", 0)
        unspent = get_unspent_points(p)

        # 配合 trpg_combat.py 的設定，抓取 player_av 與 monster_av
        p_atb = getattr(self.combat, "player_av", 0) if self.in_battle else 0
        m_atb = getattr(self.combat, "monster_av", 0) if self.in_battle else 0
        atb_max = 100  # 根據你的 advance_time 邏輯，滿值固定為 100

        player_bar = self._generate_action_bar(p_atb, atb_max)

        # 小丑面具判定
        immune_str = ""
        immune_status_id = get_daily_jester_immunity(p, self.cog.status_effects)
        if immune_status_id:
            immune_name = self.cog.status_effects.get(immune_status_id, {}).get("name", immune_status_id)
            immune_str = f"\n🎭 面具庇護：今日完全免疫【{immune_name}】"

        embed.title = f"{state_text} | {area_name}"

        # 玩家狀態區塊排版
        player_desc = (
            f"**Lv.{p.level} 冒險者** | 💰 {user_bal} {self.cog.bot.baba.money_name}\n"
            f"❤️ HP: `{p.current_hp:03d}/{p.max_hp:03d}`\n"
            f"💧 MP: `{p.current_mp:03d}/{p.max_mp:03d}`\n"
            f"⚔️ ATK: `{p_atk}` | 🛡️ DEF: `{p_def}` | 🚀 SPD: `{p_spd}`\n"
            f"✨ MAG: `{p_magic}` | 🔰 RES: `{p_res}`\n"
        )
        
        # 戰鬥中才顯示行動條
        if self.in_battle:
            player_desc += f"⚡ 行動: `[{player_bar}]`\n"
            
        player_desc += (
            f"📊 未分配點數: `{unspent}`\n"
            f"📜 狀態：{status_text}{immune_str}"
        )
        embed.add_field(name="👤 你的狀態", value=player_desc, inline=False)

        # 戰鬥時顯示敵方狀態區塊
        if self.in_battle and self.active_monster:
            m = self.active_monster
            m_spd = m.get("spd", 0)
            monster_bar = self._generate_action_bar(m_atb, atb_max)
            
            monster_desc = (
                f"❤️ HP: `{max(0, self.monster_hp):03d}/{m['max_hp']:03d}`\n"
                f"⚔️ ATK: `{m['atk']}` | 🛡️ DEF: `{m['def']}` | 🚀 SPD: `{m_spd}`\n"
                f"⚡ 行動: `[{monster_bar}]`"
            )
            embed.add_field(name=f"😈 敵方：{m['name']}", value=monster_desc, inline=False)

        embed.description = f"```\n{self.log_message}\n```"
        return embed

    async def handle_explore(self):
        if self.player.current_hp <= 0:
            self.log_message = "❌ 你已經倒下了，請先去旅館休息療傷！"
            return

        area_data = self.cog.areas.get(self.player.current_area, {})

       # 👇 1. 判斷是否在魔塔區域，如果是，直接轉交給魔塔專屬函數
        if self.player.current_area == "area_tower":
            await self.handle_tower_explore()
            return

        # 👇 2. 新版事件系統：讀取該區域的專屬事件機率（預設 0.2）
        event_chance = area_data.get("event_chance", 0.2)
        if random.random() < event_chance and self.cog.events:
            await self.handle_random_event(area_data)
            return

        if not area_data or not area_data.get("monsters"):
            self.log_message = "📍 這個區域一片祥和，沒有任何怪物跡象。"
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
        
        self.active_monster_id = selected_id
        self.active_monster = dict(base_monster)
        
        # 👇 3. 套用名稱標籤與數值倍率
        self.active_monster["level"] = m_level
        self.active_monster["name"] = f"{base_monster['name']} (Lv.{m_level})"
        self.active_monster["max_hp"] = max(1, int(base_monster["max_hp"] * scale))
        self.active_monster["atk"] = max(1, int(base_monster["atk"] * scale))
        self.active_monster["def"] = max(1, int(base_monster["def"] * scale))
        self.active_monster["exp"] = max(1, int(base_monster.get("exp", 10) * scale))
        
        # 👇 4. 動態生成速度 (SPD)：如果有寫死就用，沒有就根據等級隨機生成
        default_spd = int(10 + m_level * 1.5 + random.randint(-2, 2))
        self.active_monster["spd"] = base_monster.get("spd", default_spd)
        
        self.monster_hp = self.active_monster["max_hp"]

        self.log_message = f"⚔️ 遭遇了【{self.active_monster['name']}】！對方來勢洶洶！"
        self.build_battle_menu()

    async def handle_random_event(self, area_data=None):
        # 👇 根據區域 JSON 抓取專屬事件池，若無則用全域事件
        if area_data and "events" in area_data:
            event_pool = [e for e in area_data["events"] if e in self.cog.events]
        else:
            event_pool = list(self.cog.events.keys())
            
        if not event_pool:
            self.log_message = "🌿 風吹草動，但什麼也沒發生。"
            self.build_main_menu()
            return

        weights = [self.cog.events[eid].get("weight", 1) for eid in event_pool]
        event_id = random.choices(event_pool, weights=weights)[0]
        event = self.cog.events[event_id]
        
        category = event.get("category", "neutral")
        cat_emoji = {"good": "🎁", "neutral": "📖", "bad": "💢"}.get(category, "❓")
        log = f"{cat_emoji} 【隨機事件】\n{event.get('message', '發生了神祕的事……')}"

        rewards = event.get("rewards", {})
        if rewards.get("gold"):
            uid = int(self.user_id)
            bal = self.cog.bank.get(uid, [0])[0]
            self.cog.bank[uid] = (bal + rewards["gold"], self.cog.bank.get(uid, (0, False))[1])
            self.cog.bot.baba.refresh_bank_file()
            log += f"\n💰 獲得 {rewards['gold']} {self.cog.bot.baba.money_name}！"

        for item_id, qty in rewards.get("items", {}).items():
            self.player.inventory[item_id] = self.player.inventory.get(item_id, 0) + qty
            item_name = self.cog.items.get(item_id, {}).get("name", item_id)
            log += f"\n🎁 獲得【{item_name}】x{qty}"

        if event.get("hp_loss_percent"):
            loss = max(1, int(self.player.max_hp * event["hp_loss_percent"]))
            self.player.current_hp = max(0, self.player.current_hp - loss)
            log += f"\n❤️ 損失 {loss} HP"
            if self.player.current_hp <= 0:
                self.log_message = self.process_death(log, "💀 你因事件傷勢過重倒下了！")
                return

        if event.get("mp_loss_percent"):
            loss_mp = max(1, int(self.player.max_mp * event["mp_loss_percent"]))
            self.player.current_mp = max(0, self.player.current_mp - loss_mp)
            log += f"\n💧 流失 {loss_mp} MP"

        if event.get("gold_loss"):
            uid = int(self.user_id)
            bal = self.cog.bank.get(uid, [0])[0]
            loss_g = min(bal, event["gold_loss"])
            self.cog.bank[uid] = (bal - loss_g, self.cog.bank.get(uid, (0, False))[1])
            self.cog.bot.baba.refresh_bank_file()
            log += f"\n💸 損失 {loss_g} {self.cog.bot.baba.money_name}"

        self.log_message = log
        self.cog.save_players()
        self.build_main_menu()

    async def handle_battle_attack(self):
        self.log_message = self.combat.player_attack()
        # 確保砍完重繪戰鬥按鈕
        if self.in_battle:
            self.build_battle_menu()

    async def handle_use_skill(self, skill_id: str):
        if skill_id not in self.player.skills:
            self.log_message = "❌ 你還沒學會這個技能。"
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
        active_skills = [
            s for s in self.player.skills
            if self.cog.skills.get(s, {}).get("type") != "passive"
        ]
        if not active_skills:
            self.log_message = "❌ 你尚未習得可施放的技能！去商店買卷軸，或討伐 BOSS 取得技能卷軸吧。"
            return

        self.clear_items()
        self.in_battle = True
        lines = ["✨ 選擇要施放的技能："]
        for skill_id in active_skills:
            skill = self.cog.skills.get(skill_id)
            if not skill:
                continue
            req = skill.get("req_level", 1)
            req_note = f" [需Lv.{req}]" if req > 1 else ""
            lines.append(f"• {skill['name']}{req_note}: {skill.get('desc', '')}")
            
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
                label=f"{skill['name']}{cost_str}{cd_text}"[:80],
                style=discord.ButtonStyle.secondary if disabled else discord.ButtonStyle.success,
                custom_id=f"skill_{skill_id}",
            )
        self.log_message = "\n".join(lines)
        self.add_action_button(label="返回戰鬥", style=discord.ButtonStyle.secondary, custom_id="btn_back_battle", emoji="🔙")


    async def handle_learn_skill_menu(self, notice=""):
        self.clear_items()
        scrolls = []
        for item_id, count in self.player.inventory.items():
            if count > 0:
                item = self.cog.items.get(item_id)
                if item and item.get("type") == "skill_scroll":
                    scrolls.append(item_id)

        prefix = notice + "\n\n" if notice else ""
        if not scrolls:
            self.log_message = prefix + "📖 【技能修練所】\n背包裡沒有技能卷軸。可從商店購買，或討伐區域 BOSS 取得！"
        else:
            self.log_message = prefix + "📖 【技能修練所】\n選擇要研讀的卷軸（消耗 1 張）："
            for scroll_id in scrolls:
                item = self.cog.items[scroll_id]
                skill_id = item.get("teaches", "")
                skill = self.cog.skills.get(skill_id, {})
                skill_name = skill.get("name", skill_id)
                if skill_id in self.player.skills:
                    label = f"已學會：{skill_name}"
                    btn = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, disabled=True)
                    self.add_item(btn)
                else:
                    self.add_action_button(
                        label=f"研讀 {item['name']}",
                        style=discord.ButtonStyle.primary,
                        custom_id=f"learn_{scroll_id}",
                    )

        self.add_action_button(label="返回村莊", style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    async def handle_learn_skill(self, scroll_id: str):
        item = self.cog.items.get(scroll_id)
        if not item or item.get("type") != "skill_scroll":
            await self.handle_learn_skill_menu("❌ 無效的卷軸。")
            return

        skill_id = item.get("teaches")
        skill = self.cog.skills.get(skill_id)
        if not skill:
            await self.handle_learn_skill_menu("❌ 這卷軸記載的技藝已失傳...")
            return

        if skill_id in self.player.skills:
            await self.handle_learn_skill_menu(f"❌ 你已經學會【{skill['name']}】了，無需重複研讀。")
            return

        req_lv = skill.get("req_level", 1)
        if self.player.level < req_lv:
            await self.handle_learn_skill_menu(
                f"❌ 等級不足！習得【{skill['name']}】需要 Lv.{req_lv}。"
            )
            return

        if self.player.inventory.get(scroll_id, 0) <= 0:
            await self.handle_learn_skill_menu("❌ 背包裡沒有這張卷軸。")
            return

        self.player.inventory[scroll_id] -= 1
        if self.player.inventory[scroll_id] <= 0:
            del self.player.inventory[scroll_id]

        self.player.skills.append(skill_id)
        self.cog.save_players()
        await self.handle_learn_skill_menu(f"📖 你研讀了【{item['name']}】，成功習得技能【{skill['name']}】！\n{skill.get('desc', '')}")
        

    async def handle_move_execute(self, custom_id):
        target_area = custom_id.replace("move_to_", "")
        area_data = self.cog.areas.get(target_area, {})
        # req_level = area_data.get("req_level", 1)
        # if self.player.level < req_level:
        #     self.log_message = f"❌ 等級不足！前往【{area_data.get('area_name', target_area)}】需要 Lv.{req_level}。"
        #     self.build_main_menu()
        #     return
        self.player.current_area = target_area
        self.cog.save_players()
        self.log_message = f"🗺️ 成功抵達了【{self.cog.areas[target_area]['area_name']}】。"
        self.build_main_menu()

    async def handle_status(self, interaction: discord.Interaction):
        p = self.player
        weapon_name = self.cog.items.get(p.weapon, {}).get("name", "無") if p.weapon else "無"
        
        inv_desc = "\n".join([f"• {self.cog.items.get(k,{}).get('name', k)} x{v}" for k, v in p.inventory.items() if v > 0])
        if not inv_desc: inv_desc = "空空如也"

        user_bal = self.cog.bank.get(int(self.user_id), [0])[0]

        status_embed = discord.Embed(title=f"📜 {interaction.user.name} 的詳細冒險狀態", color=discord.Color.blue())
        status_embed.add_field(name="等級與經驗", value=f"Lv.{p.level} (EXP: {p.exp}/{exp_to_next_level(p.level)})", inline=True)
        status_embed.add_field(name="錢包餘額", value=f"{user_bal} {self.cog.bot.baba.money_name}", inline=True)
        alloc_text = format_stat_alloc_summary(p)
        status_embed.add_field(
            name="戰鬥核心數值",
            value=(
                f"❤️ HP: {p.current_hp}/{p.max_hp}\n"
                f"💧 MP: {p.current_mp}/{p.max_mp}\n"
                f"⚔️ ATK: {get_player_atk(p, self.cog.items)} | 🛡️ DEF: {get_player_def(p, self.cog.items)}\n"
                f"✨ MAG: {get_player_magic(p, self.cog.items)} | 🔰 RES: {getattr(p, 'base_res', 0)}\n"
                f"{alloc_text}"
            ),
            inline=False,
        )
        status_embed.add_field(name="配戴武器", value=weapon_name, inline=True)
        status_embed.add_field(name="異常狀態", value=format_status_list(p.status_effects, self.cog.status_effects), inline=True)
        if p.accessory:
            acc_name = self.cog.items.get(p.accessory, {}).get("name", p.accessory)
            status_embed.add_field(name="飾品", value=acc_name, inline=True)
        skill_list = ", ".join([self.cog.skills.get(s, {}).get("name", s) for s in p.skills]) or "無"
        status_embed.add_field(name="已習技能", value=skill_list, inline=True)
        status_embed.add_field(name="行囊儲存物", value=inv_desc, inline=False)
        
        await interaction.followup.send(embed=status_embed, ephemeral=True)

    async def handle_stat_alloc_menu(self, notice=""):
        self.clear_items()
        prefix = notice + "\n\n" if notice else ""
        unspent = get_unspent_points(self.player)
        self.log_message = (
            prefix
            + "📊 【屬性分配】每級 2 點，死亡後重置。\n"
            + format_stat_alloc_summary(self.player)
            + "\n\n攻擊+3 ATK/點 | 體力+12 HP & +2 DEF/點 | 魔力+4 MAG & +3 MP/點 | 速度+2 SPD/點 |抗性+2 RES/點"
        )
        stat_labels = {
                "atk": "＋攻擊",
                "vit": "＋體力",
                "int": "＋智力",
                "spd": "＋速度",
                "res": "＋抗性",
            }
        for key in STAT_KEYS:
            disabled = unspent <= 0
            if disabled:
                btn = discord.ui.Button(
                    label=stat_labels[key],
                    style=discord.ButtonStyle.secondary,
                    disabled=True,
                )
                self.add_item(btn)
            else:
                self.add_action_button(
                    label=stat_labels[key],
                    style=discord.ButtonStyle.primary,
                    custom_id=f"stat_add_{key}",
                )
        self.add_action_button(label=f"批量分配 (剩餘 {unspent} 點)", style=discord.ButtonStyle.success, custom_id="btn_stat_bulk", emoji="📝")
        self.add_action_button(
            label="重置配點",
            style=discord.ButtonStyle.danger,
            custom_id="btn_stat_reset",
        )
        self.add_action_button(
            label="返回村莊",
            style=discord.ButtonStyle.secondary,
            custom_id="btn_back_main",
            emoji="🔙",
        )

    async def handle_stat_add(self, stat_key: str):
        if stat_key not in STAT_KEYS:
            await self.handle_stat_alloc_menu("❌ 無效的屬性。")
            return
        if get_unspent_points(self.player) <= 0:
            await self.handle_stat_alloc_menu("❌ 沒有剩餘屬性點可分配。")
            return
        if not getattr(self.player, "stat_alloc", None):
            self.player.stat_alloc = default_stat_alloc()
        self.player.stat_alloc[stat_key] = self.player.stat_alloc.get(stat_key, 0) + 1
        recalc_player_stats(self.player, self.cog.items, heal_full=False)
        self.cog.save_players()
        await self.handle_stat_alloc_menu(f"✅ 已將 1 點投入【{stat_key}】。")

    async def handle_stat_reset(self):
        self.player.stat_alloc = default_stat_alloc()
        recalc_player_stats(self.player, self.cog.items, heal_full=False)
        self.cog.save_players()
        await self.handle_stat_alloc_menu("🔄 已重置所有屬性配點，請重新分配。")

    async def handle_rest(self):
        user_bal = self.cog.bank.get(int(self.user_id), [0])[0]
        if user_bal < 20:
            self.log_message = "❌ 你身上的硬幣連旅館的乾草床都租不起！去打怪賺錢！"
            return
        if self.player.current_hp == self.player.max_hp and self.player.current_mp == self.player.max_mp and not self.player.status_effects:
            self.log_message = "❓ 你精神飽滿，去睡覺只是在浪費錢。"
            return

        self.cog.bank[int(self.user_id)] = (user_bal - 20, self.cog.bank.get(int(self.user_id), (0, False))[1])
        self.cog.bot.baba.refresh_bank_file()
        
        self.player.current_hp = self.player.max_hp
        self.player.current_mp = self.player.max_mp
        clear_all_status(self.player)
        self.log_message = "💤 在村莊溫暖的旅店休息了一晚，體力、魔力恢復，異常狀態也清除了！(扣除 20$)"
        self.cog.save_players()

    async def handle_tower_explore(self):
        floor = self.player.tower_floor
        if floor > 99:
            self.log_message = "🏆 你已經登頂魔塔！這裡什麼都沒有了，只剩下無盡的虛空與寂靜。"
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

        # 點擊「挑戰」後，生成隨樓層膨脹的魔物
        self.active_monster_id = "tower_slime"
        self.active_monster = {
            "id": "tower_slime",
            "name": f"魔塔變異史萊姆 (Lv.{floor})",
            "is_tower": True,
            "max_hp": 30 + floor * 40,
            "atk": 15 + floor * 12,
            "def": 4 + floor * 5,
            "exp": 15 + floor * 15,
            "money_min": 10 + floor * 5,
            "money_max": 20 + floor * 8,
            "drops": {"slime_jelly": 0.5}
        }
        self.monster_hp = self.active_monster["max_hp"]
        
        self.log_message = f"🗼 【魔塔第 {floor} 層】\n空氣越來越稀薄。一隻因魔力膨脹的【{self.active_monster['name']}】擋住了去路！"
        self.build_battle_menu()

    async def handle_tower_safe_room(self, revisit=False):
        floor = self.player.tower_floor
        self.clear_items()
        
        msg = f"🏕️ 【魔塔第 {floor} 層 - 休息區】\n強大的魔力流經你的身體，你的體力與魔力已完全恢復！"
        if getattr(self, "tower_merchant_spawned", False):
            msg += "\n\n🎭 一名披著斗篷的神祕商人正坐在角落，似乎在等你過去。"
            self.add_action_button(label="與商人交易", style=discord.ButtonStyle.primary, custom_id="btn_tower_merchant", emoji="🎭")
        
        self.log_message = msg if not revisit else self.log_message
        
        self.add_action_button(label="挑戰本層魔物", style=discord.ButtonStyle.danger, custom_id="btn_tower_next", emoji="⚔️")
        self.add_action_button(label="離開魔塔", style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    async def handle_tower_merchant(self):
        self.clear_items()
        self.log_message = "🎭 【魔塔神祕商人】\n「桀桀桀... 能爬到這裡，算你有點本事。來看看這些外面買不到的珍品吧！」\n"
        
        # 👇 直接使用剛剛抽好的商品，防止反覆進出刷新商店
        for item_id in getattr(self, "tower_merchant_items", []):
            item = self.cog.items.get(item_id)
            if not item: continue
            self.add_action_button(label=f"買 {item['name']} ({item['price']}$)", style=discord.ButtonStyle.primary, custom_id=f"buy_{item_id}")
            self.log_message += f"\n• {item['name']} | {item['price']}$ | {item.get('desc', '')}"
            
        self.add_action_button(label="返回休息區", style=discord.ButtonStyle.secondary, custom_id="btn_tower_safe_room", emoji="🔙")

    async def execute_buy(self, item_id: str, amount: int):
        item_data = self.cog.items.get(item_id)
        if not item_data:
            return

        req_lv = item_data.get("exclusive_level", 0)
        if item_data.get("type") == "skill_scroll":
            skill = self.cog.skills.get(item_data.get("teaches", ""), {})
            req_lv = max(req_lv, skill.get("req_level", 1))
        # if req_lv > self.player.level:
        #     self.log_message = f"❌ 等級不足！購買【{item_data['name']}】需要 Lv.{req_lv}。"
        #     return

        total_price = item_data["price"] * amount
        user_bal = self.cog.bank.get(int(self.user_id), [0])[0]
        
        if user_bal < total_price:
            self.log_message = f"❌ 窮鬼！買 {amount} 個【{item_data['name']}】需要 {total_price}$，你只有 {user_bal}$！"
            return

        # 扣錢
        self.cog.bank[int(self.user_id)] = (user_bal - total_price, self.cog.bank.get(int(self.user_id), (0, False))[1])
        self.cog.bot.baba.refresh_bank_file()

        # 給道具
        self.player.inventory[item_id] = self.player.inventory.get(item_id, 0) + amount

        if item_data["type"] == "weapon":
            self.player.weapon = item_id
            recalc_player_stats(self.player, self.cog.items, heal_full=False)
            self.log_message = f"🛍️ 花了 {total_price}$ 買了 {amount} 把【{item_data['name']}】，自動為你裝上最新的一把！"
        elif item_data["type"] == "armor":
            self.player.armor = item_id
            recalc_player_stats(self.player, self.cog.items, heal_full=False)
            self.log_message = f"🛍️ 花了 {total_price}$ 買了 {amount} 件【{item_data['name']}】，已自動穿戴！"
        elif item_data["type"] == "skill_scroll":
            self.log_message = f"🛍️ 花了 {total_price}$ 買了 {amount} 張【{item_data['name']}】，快去技能修練研讀吧！"
        else:
            self.log_message = f"🛍️ 成功花費 {total_price}$，將 {amount} 個【{item_data['name']}】塞進了背包。"

        self.cog.save_players()

    async def execute_sell(self, item_id: str, amount: int):
        from trpg_combat import get_sell_price  # 確保這有在上面 import
        
        item_data = self.cog.items.get(item_id)
        if not item_data: return

        current_qty = self.player.inventory.get(item_id, 0)
        if current_qty < amount:
            self.log_message = f"❌ 裝什麼闊！你身上明明只有 {current_qty} 個【{item_data['name']}】。"
            await self.handle_sell_menu()
            return

        sell_price = get_sell_price(item_id, self.cog.items)
        if sell_price <= 0:
            self.log_message = "❌ 這破爛玩意兒商店不收。"
            await self.handle_sell_menu()
            return

        total_gain = sell_price * amount
        self.player.inventory[item_id] -= amount
        if self.player.inventory[item_id] <= 0:
            del self.player.inventory[item_id]

        # 加錢
        uid = int(self.user_id)
        bal = self.cog.bank.get(uid, [0])[0]
        self.cog.bank[uid] = (bal + total_gain, self.cog.bank.get(uid, (0, False))[1])
        self.cog.bot.baba.refresh_bank_file()
        self.cog.save_players()
        
        self.log_message = f"💰 一口氣賣出了 {amount} 個【{item_data['name']}】，含淚賺取 {total_gain}$！"
        await self.handle_sell_menu()

    

class TRPGCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bank = self.bot.baba.bank
        self.players_file = os.path.join(DATA_DIR, "trpg_players.json")
        self.players = {}
        self.items = {}
        self.skills = {}
        self.status_effects = {}
        self.areas = {}
        self.quests = {}
        self.events = {}
        self.load_all_config()

    def load_all_config(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        
        # 讀取物品庫
        if os.path.exists(os.path.join(DATA_DIR, "items.json")):
            with open(os.path.join(DATA_DIR, "items.json"), "r", encoding="utf-8") as f:
                self.items = json.load(f)

        if os.path.exists(os.path.join(DATA_DIR, "skills.json")):
            with open(os.path.join(DATA_DIR, "skills.json"), "r", encoding="utf-8") as f:
                self.skills = json.load(f)

        if os.path.exists(os.path.join(DATA_DIR, "status_effects.json")):
            with open(os.path.join(DATA_DIR, "status_effects.json"), "r", encoding="utf-8") as f:
                self.status_effects = json.load(f)
        
        if os.path.exists(os.path.join(DATA_DIR, "quests.json")):
            with open(os.path.join(DATA_DIR, "quests.json"), "r", encoding="utf-8") as f:
                self.quests = json.load(f)

        if os.path.exists(os.path.join(DATA_DIR, "events.json")):
            with open(os.path.join(DATA_DIR, "events.json"), "r", encoding="utf-8") as f:
                self.events = json.load(f)

        # 掃描動態區域 JSON
        for file in os.listdir(DATA_DIR):
            if file.startswith("area_") and file.endswith(".json"):
                area_id = file.replace(".json", "")
                with open(os.path.join(DATA_DIR, file), "r", encoding="utf-8") as f:
                    self.areas[area_id] = json.load(f)

        # 讀取玩家存檔
        if os.path.exists(self.players_file):
            with open(self.players_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
                self.players = {k: TRPGPlayer.from_dict(v) for k, v in raw.items()}


    def save_players(self):
        serialized = {k: v.to_dict() for k, v in self.players.items()}
        with open(self.players_file, "w", encoding="utf-8") as f:
            json.dump(serialized, f, ensure_ascii=False, indent=4)

    def get_player(self, user_id):
        uid = str(user_id)
        if uid not in self.players:
            player = TRPGPlayer(uid)
            recalc_player_stats(player, self.items, heal_full=True)
            self.players[uid] = player
            self.save_players()
        else:
            migrate_player_stats(self.players[uid], self.items)
        player = self.players[uid]
        if player.accessory == "jester_mask":
            activate_jester_immunity(player)
        return player
    
    async def generate_npc_dialogue(self, prompt: str):
        # 抓取掛載在 bot 上的 response_cog
        ai_cog = self.bot.get_cog("response_cog")
        if ai_cog:
            return await ai_cog.generate_ai_response(prompt)
        return "（NPC 似乎中了沉默魔法，無法說話。）"

    @app_commands.command(name="trpg", description=" 登入並開啟你的專屬 TRPG 冒險面板")
    async def start_trpg(self, interaction: discord.Interaction):
        # 初始化專屬此使用者的按鈕控制視圖
        view = TRPGGameView(self, interaction.user.id)
        embed = view.generate_embed()
        await interaction.response.send_message(embed=embed, view=view)




async def setup(bot):
    await bot.add_cog(TRPGCog(bot))
    print("TRPG 完全按鈕驅動系統載入完成！")