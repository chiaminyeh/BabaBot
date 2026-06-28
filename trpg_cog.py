import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import random
from datetime import datetime

from trpg_combat import TRPGCombat, exp_to_next_level, get_sell_price, get_player_atk, get_player_def, get_player_magic
from trpg_status import format_status_list, activate_jester_immunity, clear_all_status, cure_by_item, get_daily_jester_immunity
from trpg_monster_pool import load_monster_pool, pick_random_monster, instantiate_monster
from trpg_quest_popup import process_quest_popups
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

CRAFTING_RECIPES = {
    "bronze_sword": {
        "name": "青銅長劍",
        "materials": {"slime_jelly": 5, "boar_tusk": 3},
        "gold": 50
    },
    "iron_sword": {
        "name": "精鋼長劍",
        "materials": {"bone_shard": 5, "stolen_gem": 2},
        "gold": 150
    },
    "flame_sword": {
        "name": "火焰之劍",
        "materials": {"bat_wing": 5, "gargoyle_stone": 3},
        "gold": 500
    },
    "holy_sword": {
        "name": "聖光之劍",
        "materials": {"tainted_blood": 5, "lich_soulstone": 2},
        "gold": 1000
    },
    "iron_armor": {
        "name": "精鋼重甲",
        "materials": {"wolf_fur": 5, "bone_shard": 5},
        "gold": 200
    },
    "dragon_plate": {
        "name": "龍鱗重甲",
        "materials": {"bat_wing": 5, "vampire_fang": 5},
        "gold": 1200
    },
    "sage_staff": {
        "name": "賢者之杖",
        "materials": {"ancient_wood": 5, "ectoplasm": 3},
        "gold": 800
    }
}

UPGRADE_COSTS = {
    1: {"gold": 100, "material": "slime_jelly", "mat_qty": 2, "rate": 1.00, "label": "100%"},
    2: {"gold": 250, "material": "boar_tusk", "mat_qty": 2, "rate": 0.80, "label": "80%"},
    3: {"gold": 500, "material": "wolf_fur", "mat_qty": 2, "rate": 0.60, "label": "60%"},
    4: {"gold": 1000, "material": "ancient_wood", "mat_qty": 2, "rate": 0.40, "label": "40%"},
    5: {"gold": 2500, "material": "ectoplasm", "mat_qty": 2, "rate": 0.25, "label": "25%"},
}

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
            if any(val < 0 for val in (a, v, i, s, r)):
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
        self.equipped_skills = [] # 玩家裝備中的技能 (上限8個)
        self.daily_boss_kills = {} # 記錄區域 BOSS 擊殺日期，例如 {"area_grassland": "2026-06-01"}
        self.killed_bosses = []    # 記錄玩家已擊殺過的 BOSS 區域 ID
        self.weapon_upgrade = 0
        self.armor_upgrade = 0
        self.tower_milestones = []
        self.trophies = []
        self.combat_history = []
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
        self.dungeon_state = {"floor": 1, "choices": [], "in_run": False}
        self.prestige_count = 0
        self.hidden_quest_progress = {}  # {"quest_006": 1} 隱藏任務的累積進度，不需要先「接受」
        self.last_quest_popup_ts = 0     # 突發任務彈出視窗的冷卻計時
        self.dungeon_buffs = {}          # 地下城事件給的暫時性加成 {"atk_mult":..., "floors_left":...}
        self.combat_debuffs = {}         # 怪物技能造成的戰鬥內暫時減益 {"atk_mult":..., "def_mult":..., "spd_mult":..., "turns":...}
        self.sargeras_defeat_count = 0   # 敗於魔王薩格拉斯的次數，滿3次解鎖傳說洞窟
        self.legend_cave_unlocked = False
        self.cave_state = {"current_node": "entrance", "history": []}

    def remove_item(self, item_id: str, amount: int = 1) -> bool:
        current = self.inventory.get(item_id, 0)
        if current < amount:
            return False
        self.inventory[item_id] -= amount
        if self.inventory[item_id] <= 0:
            if item_id in self.inventory:
                del self.inventory[item_id]
        return True

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
        if getattr(player, "equipped_skills", None) is None:
            player.equipped_skills = player.skills[:8] if hasattr(player, "skills") else []
        if not getattr(player, "daily_boss_kills", None):
            player.daily_boss_kills = {}
        if not getattr(player, "killed_bosses", None):
            player.killed_bosses = []
        if not hasattr(player, "weapon_upgrade"):
            player.weapon_upgrade = 0
        if not hasattr(player, "armor_upgrade"):
            player.armor_upgrade = 0
        if not getattr(player, "tower_milestones", None):
            player.tower_milestones = []
        if not getattr(player, "trophies", None):
            player.trophies = []
        if not getattr(player, "combat_history", None):
            player.combat_history = []
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
        if not hasattr(player, "dungeon_floor"):
            player.dungeon_floor = 1
        if not hasattr(player, "prestige_count"):
            player.prestige_count = 0
        if not getattr(player, "achievements", None):
            player.achievements = []
        if not isinstance(getattr(player, "hidden_quest_progress", None), dict):
            player.hidden_quest_progress = {}
        if not hasattr(player, "last_quest_popup_ts"):
            player.last_quest_popup_ts = 0
        if not isinstance(getattr(player, "dungeon_buffs", None), dict):
            player.dungeon_buffs = {}
        if not isinstance(getattr(player, "combat_debuffs", None), dict):
            player.combat_debuffs = {}
        if not hasattr(player, "sargeras_defeat_count"):
            player.sargeras_defeat_count = 0
        if not hasattr(player, "legend_cave_unlocked"):
            player.legend_cave_unlocked = False
        if not isinstance(getattr(player, "cave_state", None), dict):
            player.cave_state = {"current_node": "entrance", "history": []}
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



class RoguePlayerWrapper:
    def __init__(self, real_player):
        self.real_player = real_player

    def __getattr__(self, name):
        if getattr(self.real_player, 'current_area', '') == 'area_dungeon' and self.real_player.dungeon_state.get('in_run'):
            if name in ['level', 'exp', 'max_hp', 'current_hp', 'base_atk', 'base_def', 'base_spd', 'base_magic', 
                        'inventory', 'skills', 'equipped_skills', 'stat_alloc', 'weapon', 'armor', 'accessory']:
                return self.real_player.dungeon_state.get(name)
        return getattr(self.real_player, name)
    
    def __setattr__(self, name, value):
        if name == 'real_player':
            super().__setattr__(name, value)
            return
        if getattr(self.real_player, 'current_area', '') == 'area_dungeon' and self.real_player.dungeon_state.get('in_run'):
            if name in ['level', 'exp', 'max_hp', 'current_hp', 'base_atk', 'base_def', 'base_spd', 'base_magic', 
                        'inventory', 'skills', 'equipped_skills', 'stat_alloc', 'weapon', 'armor', 'accessory']:
                self.real_player.dungeon_state[name] = value
                return
        setattr(self.real_player, name, value)

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

        self.log_message = "歡迎來到冒險世界！請使用下方按鈕進行探索。"
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
            await interaction.response.send_message("這不是你的冒險面板，請自己輸入 `/trpg` 開一盤！", ephemeral=True)
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
                
                embed = self.generate_embed()
                embed.description = f"```\n⌛ 此冒險面板已因超時（10分鐘未操作）而關閉並自動存檔。\n請重新輸入 `/trpg` 來繼續冒險！\n```"
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
    async def global_callback(self, interaction: discord.Interaction, custom_id: str):
        if custom_id == "btn_ask_chief":
            await interaction.response.send_modal(ElderChiefModal(self))
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
        elif custom_id == "btn_leaderboard": await self.handle_leaderboard()
        elif custom_id == "btn_combat_history": await self.handle_combat_history(interaction)
        elif custom_id == "btn_rest": await self.handle_rest()
        elif custom_id == "btn_shop_menu": await self.handle_shop_menu()
        elif custom_id == "btn_shop_refresh": await self.handle_shop_refresh()
        elif custom_id == "btn_shop_sell": await self.handle_sell_menu()
        elif custom_id == "btn_equip_menu": await self.handle_equip_menu()
        elif custom_id == "btn_dung_explore": await self.handle_dung_explore()
        elif custom_id == "btn_dung_rest": await self.handle_dung_rest()
        elif custom_id == "btn_dung_upgrade": await self.handle_dung_upgrade()
        elif custom_id == "btn_dung_next": await self.handle_dung_next()
        elif custom_id == "btn_back_dungeon": self.build_dungeon_menu()
        elif custom_id.startswith("dung_enter_"):
            idx = int(custom_id.split('_')[-1])
            await self.handle_dung_enter(idx)
        elif custom_id.startswith("equip_skill_"):
            skill_id = custom_id.replace("equip_skill_", "")
            await self.handle_skill_equip_action(skill_id, equip=True)
        elif custom_id.startswith("unequip_skill_"):
            skill_id = custom_id.replace("unequip_skill_", "")
            await self.handle_skill_equip_action(skill_id, equip=False)
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
        elif custom_id == "btn_boss_explore": await self.handle_boss_explore()
        elif custom_id == "btn_skill_learn": await self.handle_learn_skill_menu()
        elif custom_id.startswith("learn_"):
            skill_id = custom_id.replace("learn_", "")
            await self.handle_learn_skill(skill_id)
        elif custom_id == "btn_stat_alloc": await self.handle_stat_alloc_menu()
        elif custom_id == "btn_stat_reset": await self.handle_stat_reset()
        elif custom_id.startswith("stat_add_all_"):
            stat_key = custom_id.replace("stat_add_all_", "")
            await self.handle_stat_add(stat_key, all_in=True)
        elif custom_id.startswith("stat_add_"):
            stat_key = custom_id.replace("stat_add_", "")
            await self.handle_stat_add(stat_key)
        elif custom_id == "btn_back_main":
            self.log_message = "回到了主選單。"
            self.build_main_menu()
        elif custom_id == "btn_artisan_menu":
            self.build_artisan_menu()
        elif custom_id == "btn_guild_menu":
            self.build_guild_menu()
        elif custom_id == "btn_church_menu":
            self.build_church_menu()
        elif custom_id == "btn_skill_equip":
            await self.handle_skill_equip_menu()

        # 👇 魔塔專屬按鈕（修正原本重複與錯誤的區塊）
        elif custom_id == "btn_tower_safe_room": 
            await self.handle_tower_safe_room(revisit=True)
        elif custom_id == "btn_tower_merchant": 
            await self.handle_tower_merchant()
        elif custom_id == "btn_tower_next":
            await self.handle_tower_explore()
            
        # 👇 地下城專屬按鈕 (Slay the Spire Style)
        elif custom_id == "btn_dung_flee":
            await self.handle_dungeon_flee()

        # 👇 傳說洞窟專屬按鈕（主線：勇者之劍）
        elif custom_id == "cave_dir_forward":
            await self.handle_cave_move("forward")
        elif custom_id == "cave_dir_left":
            await self.handle_cave_move("left")
        elif custom_id == "cave_dir_right":
            await self.handle_cave_move("right")
        elif custom_id == "cave_dir_back":
            await self.handle_cave_move("back")

        # 👇 轉生、合成按鈕
        elif custom_id == "btn_prestige_menu":
            await self.handle_prestige_menu()
        elif custom_id == "btn_prestige_confirm":
            await self.handle_prestige_confirm()
        elif custom_id == "btn_craft_menu":
            await self.handle_craft_menu()
        elif custom_id.startswith("craft_"):
            item_id = custom_id.replace("craft_", "")
            await self.handle_craft_execute(item_id)
        elif custom_id == "btn_blacksmith_menu":
            await self.handle_blacksmith_menu()
        elif custom_id == "btn_upgrade_weapon":
            await self.handle_upgrade_execute(is_weapon=True)
        elif custom_id == "btn_upgrade_armor":
            await self.handle_upgrade_execute(is_weapon=False)
        elif custom_id == "btn_prev_page":
            self.inventory_page = max(0, getattr(self, "inventory_page", 0) - 1)
            await self.re_render_current_menu()
        elif custom_id == "btn_next_page":
            self.inventory_page = getattr(self, "inventory_page", 0) + 1
            await self.re_render_current_menu()
            
        # 戰鬥相關
        elif custom_id == "b_atk": 
            if not getattr(self, "in_battle", False): return
            await self.handle_battle_attack()
        elif custom_id == "b_def": 
            if not getattr(self, "in_battle", False): return
            self.log_message = self.combat.defend()
            self.build_battle_menu()
        elif custom_id == "b_dod": 
            if not getattr(self, "in_battle", False): return
            self.log_message = self.combat.dodge()
            self.build_battle_menu()
        elif custom_id == "b_ski": await self.handle_skill_menu()
        elif custom_id.startswith("skill_"):
            skill_id = custom_id.replace("skill_", "")
            await self.handle_use_skill(skill_id)
        elif custom_id == "b_itm": await self.handle_item_menu()  
        elif custom_id.startswith("use_item_"): await self.handle_use_item(custom_id) 
        elif custom_id == "b_fle": 
            if not getattr(self, "in_battle", False): return
            await self.handle_battle_flee()
        elif custom_id == "btn_back_battle": self.build_battle_menu()

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

        if "village" in self.player.current_area:
            self.add_action_button(label="移動", style=discord.ButtonStyle.secondary, custom_id="btn_move_menu", row=0, emoji="🗺️")
            self.add_action_button(label="狀態", style=discord.ButtonStyle.success, custom_id="btn_status", row=0, emoji="📜")
            self.add_action_button(label="裝備", style=discord.ButtonStyle.secondary, custom_id="btn_equip_menu", row=0, emoji="🛡️")
            self.add_action_button(label="商店", style=discord.ButtonStyle.primary, custom_id="btn_shop_menu", row=0, emoji="🛒")
            self.add_action_button(label="旅館", style=discord.ButtonStyle.secondary, custom_id="btn_rest", row=1, emoji="💤")
            self.add_action_button(label="鐵匠", style=discord.ButtonStyle.primary, custom_id="btn_artisan_menu", row=1, emoji="⚒️")
            self.add_action_button(label="公會", style=discord.ButtonStyle.primary, custom_id="btn_guild_menu", row=1, emoji="🏛️")
            self.add_action_button(label="村長", style=discord.ButtonStyle.secondary, custom_id="btn_ask_chief", row=2, emoji="🧓")
            self.add_action_button(label="教堂", style=discord.ButtonStyle.success, custom_id="btn_church_menu", row=2, emoji="⛪")
        else:
            # 🛠️ 新增：野外區域限定的每日 BOSS 按鈕
            self.add_action_button(label="探索", style=discord.ButtonStyle.primary, custom_id="btn_explore", row=0, emoji="⚔️")
            self.add_action_button(label="移動", style=discord.ButtonStyle.secondary, custom_id="btn_move_menu", row=0, emoji="🗺️")
            self.add_action_button(label="狀態", style=discord.ButtonStyle.success, custom_id="btn_status", row=0, emoji="📜")
            self.add_action_button(label="裝備", style=discord.ButtonStyle.secondary, custom_id="btn_equip_menu", row=0, emoji="🛡️")
            self.add_action_button(label="藥水", style=discord.ButtonStyle.secondary, custom_id="b_itm", row=1, emoji="🎒")
            self.add_action_button(label="記錄", style=discord.ButtonStyle.secondary, custom_id="btn_combat_history", row=1, emoji="📝")
            self.add_action_button(label="區域BOSS", style=discord.ButtonStyle.danger, custom_id="btn_boss_explore", row=1, emoji="👹")

    def build_artisan_menu(self):
        self.clear_items()
        self.log_message = "⚒️ 【鐵匠之地】挑選你要去的地方："
        self.add_action_button(label="鐵匠鋪", style=discord.ButtonStyle.primary, custom_id="btn_blacksmith_menu", emoji="⚒️")
        self.add_action_button(label="手藝工坊", style=discord.ButtonStyle.primary, custom_id="btn_craft_menu", emoji="🔨")
        self.add_action_button(label="返回", style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    def build_guild_menu(self):
        self.clear_items()
        self.log_message = "🏛️ 【冒險者公會】\n請選擇你要進行的公會服務："
        self.add_action_button(label="排行榜", style=discord.ButtonStyle.secondary, custom_id="btn_leaderboard", emoji="🏆")
        self.add_action_button(label="屬性分配", style=discord.ButtonStyle.primary, custom_id="btn_stat_alloc", emoji="📊")
        self.add_action_button(label="戰鬥記錄", style=discord.ButtonStyle.secondary, custom_id="btn_combat_history", emoji="📝")
        self.add_action_button(label="返回", style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    def build_church_menu(self):
        self.clear_items()
        self.log_message = "⛪ 【教堂】\n莊嚴的聖光籠罩著你。這裡能為你洗滌疲憊，指引未來的道路。"
        self.add_action_button(label="轉生殿堂", style=discord.ButtonStyle.success, custom_id="btn_prestige_menu", emoji="🌟")
        self.add_action_button(label="技能配置", style=discord.ButtonStyle.primary, custom_id="btn_skill_equip", emoji="🔧")
        self.add_action_button(label="學習魔法", style=discord.ButtonStyle.primary, custom_id="btn_skill_learn", emoji="📖")
        self.add_action_button(label="返回", style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    def process_death(self, log: str, reason: str = "💀 你倒下了...") -> str:
        """統一處理死亡邏輯，回傳組合好的 log 訊息"""
        is_sargeras_fight = any(s["monster"].get("id") == "sargeras" for s in self.monster_slots)

        self.player.current_hp = 0
        self.player.exp = self.player.exp // 2  # 死亡懲罰：經驗值減半

        from trpg_status import clear_all_status
        clear_all_status(self.player)

        self.player.stats["total_deaths"] = self.player.stats.get("total_deaths", 0) + 1

        achv_text = self.check_achievements()
        final_log = log + f"\n\n{reason}\n(當前經驗值減半。)"
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
        count = getattr(self.player, "sargeras_defeat_count", 0) + 1
        self.player.sargeras_defeat_count = count

        if count == 3 and not getattr(self.player, "legend_cave_unlocked", False):
            self.player.legend_cave_unlocked = True
            self.cog.save_players()
            return (
                "\n\n🎻 **一位吟遊詩人不知何時出現在你身旁，撥動著琴弦：**\n"
                "「敗給魔王三次的勇士啊，不要灰心……我聽聞在世界的角落，有一座【傳說洞窟】，"
                "洞窟深處插著一把【勇者之劍】，劍身纏繞著聖光，連魔王也為之忌憚。\n"
                "只是那洞窟裡瀰漫著濃郁的魔力，每走一步都會侵蝕你的生命……願聖光指引你的方向。」\n"
                "✨ 【傳說洞窟】已出現在移動選單中！"
            )
        return ""

    def check_achievements(self) -> str:
        """檢查玩家成就，若有新解鎖的成就，回傳解鎖的公告文字，並將其加到 player.achievements"""
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
                unlocked_msgs.append(f"🎉 【解鎖成就】{info.get('name')} - {info.get('desc')}")
                
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

        if "berserk" in getattr(self.player, "status_effects", {}):
            self.add_action_button(label="狂暴攻擊", style=discord.ButtonStyle.danger, custom_id="b_atk", row=0, emoji="😡")
            self.add_action_button(label="逃跑", style=discord.ButtonStyle.secondary, custom_id="b_fle", row=0, emoji="🏃")
            self.add_action_button(label="狀態", style=discord.ButtonStyle.success, custom_id="b_sta", row=0, emoji="📜")
            return

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
            if area_id == self.player.current_area:
                continue
            requires_flag = area.get("requires_flag")
            if requires_flag and not getattr(self.player, requires_flag, False):
                continue
            req = area.get("req_level", 1)
            label = f"前往 {area.get('area_name', '未知區域')} (Lv.{req})"
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
            anti_boss_text = f"\n✨ 【{weapon.get('name', '')}】散發出聖潔的光輝，魔王的力量被大幅削弱了！"

        self.start_combat([boss_instance])

        self.log_message = f"🚨 【區域領主警告】 🚨\n大地在震動... 你驚動了隱藏的首領【{boss_instance['name']}】！{anti_boss_text}"
        self.build_battle_menu()

    async def handle_equip_menu(self, notice="", paging=False):
        self.clear_items()
        if not paging:
            self.inventory_page = 0
        self.current_menu_state = "equip"
        
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

        self.log_message = (notice + "\n\n" if notice else "") + f"🎒 【裝備管理】(第 {self.inventory_page + 1}/{max_page + 1} 頁)"
        
        c_weap = f"【{self.cog.items[self.player.weapon]['name']}】" if self.player.weapon else "無 (空手)"
        c_armr = f"【{self.cog.items[self.player.armor]['name']}】" if getattr(self.player, 'armor', None) else "無 (布衣)"
        c_accs = f"【{self.cog.items[self.player.accessory]['name']}】" if self.player.accessory else "無"
        
        self.log_message += f"\n👉 武器：{c_weap}\n👉 防具：{c_armr}\n👉 飾品：{c_accs}"

        for item_id in page_items:
            item_data = self.cog.items[item_id]
            t = item_data.get("type")
            if t == "weapon":
                if item_id == self.player.weapon:
                    self.add_action_button(label=f"卸下 {item_data['name']}", style=discord.ButtonStyle.danger, custom_id=f"unequip_{item_id}")
                else:
                    self.add_action_button(label=f"裝備 {item_data['name']}", style=discord.ButtonStyle.success, custom_id=f"equip_{item_id}")
            elif t == "armor":
                if item_id == getattr(self.player, "armor", None):
                    self.add_action_button(label=f"卸下 {item_data['name']}", style=discord.ButtonStyle.danger, custom_id=f"unequip_{item_id}")
                else:
                    self.add_action_button(label=f"穿戴 {item_data['name']}", style=discord.ButtonStyle.primary, custom_id=f"equip_{item_id}")
            elif t == "accessory":
                if item_id == self.player.accessory:
                    self.add_action_button(label=f"卸下 {item_data['name']}", style=discord.ButtonStyle.danger, custom_id=f"unacc_{item_id}")
                else:
                    self.add_action_button(label=f"配戴 {item_data['name']}", style=discord.ButtonStyle.success, custom_id=f"acc_{item_id}")

        if not all_equips:
            self.log_message += "\n\n背包裡沒有可裝備的物品。"

        # 分頁按鈕
        if total_items > items_per_page:
            self.add_action_button(label="◀️ 上一頁", style=discord.ButtonStyle.secondary, custom_id="btn_prev_page", row=3)
            self.add_action_button(label="▶️ 下一頁", style=discord.ButtonStyle.secondary, custom_id="btn_next_page", row=3)

        self.add_action_button(label="返回", style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙", row=4)

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
    def _format_shop_item_line(self, item_id: str) -> str:
        item = self.cog.items.get(item_id, {})
        req = item.get("exclusive_level", 0)
        req_str = f" | 需 Lv.{req}" if req else ""
        line = f"• {item.get('name', item_id)}{req_str} | {item.get('price', 0)}$ | {item.get('desc', '')}"
        
        if item.get("type") in ("weapon", "armor", "accessory"):
            comp_str = self._get_equipment_comparison_string(item)
            if comp_str:
                line += f" {comp_str}"
                
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
        self.current_menu_state = "shop"

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
                
                comp_str = ""
                if item.get("type") in ("weapon", "armor", "accessory"):
                    comp_str = self._get_equipment_comparison_string(item)
                comp_suffix = f" {comp_str}" if comp_str else ""
                
                label_text = f"買 {item['name']}{req_label} ({item['price']}$){comp_suffix}"
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
                
                comp_str = ""
                if item.get("type") in ("weapon", "armor", "accessory"):
                    comp_str = self._get_equipment_comparison_string(item)
                comp_suffix = f" {comp_str}" if comp_str else ""
                
                self.add_action_button(
                    label=f"🎭 {item['name']} ({item['price']}$){comp_suffix}"[:80],
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
        user_bal = self.cog.get_bank_balance(self.user_id)
        
        if user_bal < cost:
            await self.handle_shop_menu(f"❌ 金幣不足！手動進貨需要支付 {cost}$ 給老闆。")
            return
            
        self.cog.adjust_bank(self.user_id, -cost)
        if not getattr(self.player, "stats", None):
            self.player.stats = {}
        self.player.stats["money_spent"] = self.player.stats.get("money_spent", 0) + cost
        
        self.player.shop_refresh_count = count + 1
        self.player.shop_items = []
        
        achv_text = self.check_achievements()
        notice_text = f"🔄 支付了 {cost}$ 刷新商店！老闆為你進了一批新貨。"
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
        if not item:
            await self._refresh_buy_menu("❌ 這個商品已經不在貨架上了。")
            return

        total_cost = item.get("price", 0) * amount
        user_bal = self.cog.get_bank_balance(self.user_id)
        if user_bal < total_cost:
            await self._refresh_buy_menu(f"❌ 金幣不足！購買 {amount} 個【{item['name']}】需要 {total_cost}$，但你只有 {user_bal}$。")
            return

        self.cog.adjust_bank(self.user_id, -total_cost)
        self.player.inventory[item_id] = self.player.inventory.get(item_id, 0) + amount
        if not getattr(self.player, "stats", None):
            self.player.stats = {}
        self.player.stats["money_spent"] = self.player.stats.get("money_spent", 0) + total_cost

        achv_text = self.check_achievements()
        notice_text = f"✅ 購買了 {amount} 個【{item['name']}】，花費 {total_cost}$！"
        if achv_text:
            notice_text += achv_text

        self.cog.save_players()
        await self._refresh_buy_menu(notice_text)

    async def execute_sell(self, item_id: str, amount: int):
        item = self.cog.items.get(item_id)
        owned = self.player.inventory.get(item_id, 0)
        if not item or owned <= 0:
            await self.handle_sell_menu("❌ 你並未持有這個物品。", paging=True)
            return

        if amount > owned:
            await self.handle_sell_menu(f"❌ 數量超過持有量！你只有 {owned} 個【{item['name']}】。", paging=True)
            return

        unit_price = get_sell_price(item_id, self.cog.items)
        total_price = unit_price * amount

        self.player.inventory[item_id] -= amount
        if self.player.inventory[item_id] <= 0:
            del self.player.inventory[item_id]
        self.cog.adjust_bank(self.user_id, total_price)

        self.cog.save_players()
        await self.handle_sell_menu(f"✅ 賣出了 {amount} 個【{item['name']}】，獲得 {total_price}$！", paging=True)

    async def handle_sell_menu(self, notice="", paging=False):
        self.clear_items()
        if not paging:
            self.inventory_page = 0
        self.current_menu_state = "sell"
        
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
            self.log_message = prefix + "💰 【出售物品】\n沒有可以賣給商店的東西。"
        else:
            self.log_message = prefix + f"💰 【出售物品】(第 {self.inventory_page + 1}/{max_page + 1} 頁)\n選擇要賣出的物品："
            for item_id in page_items:
                item = self.cog.items[item_id]
                price = get_sell_price(item_id, self.cog.items)
                count = self.player.inventory[item_id]
                self.add_action_button(
                    label=f"賣 {item['name']} ({price}$) x{count}",
                    style=discord.ButtonStyle.primary,
                    custom_id=f"sell_{item_id}",
                )

        if total_items > items_per_page:
            self.add_action_button(label="◀️ 上一頁", style=discord.ButtonStyle.secondary, custom_id="btn_prev_page", row=3)
            self.add_action_button(label="▶️ 下一頁", style=discord.ButtonStyle.secondary, custom_id="btn_next_page", row=3)

        self.add_action_button(label="返回商店", style=discord.ButtonStyle.secondary, custom_id="btn_shop_menu", emoji="🔙", row=4)

    async def handle_item_menu(self, paging=False):
        self.clear_items()
        if not paging:
            self.inventory_page = 0
        self.current_menu_state = "item"
        
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
            self.log_message = "❌ 背包裡沒有可用的道具。"
            if self.in_battle:
                self.build_battle_menu()
            else:
                self.build_main_menu()
            return

        self.log_message = f"🎒 選擇要使用的道具：(第 {self.inventory_page + 1}/{max_page + 1} 頁)"
        for item_id in page_items:
            item = self.cog.items[item_id]
            self.add_action_button(
                label=f"{item['name']} x{self.player.inventory[item_id]}",
                style=discord.ButtonStyle.secondary,
                custom_id=f"use_item_{item_id}",
            )
            
        if total_items > items_per_page:
            self.add_action_button(label="◀️ 上一頁", style=discord.ButtonStyle.secondary, custom_id="btn_prev_page", row=3)
            self.add_action_button(label="▶️ 下一頁", style=discord.ButtonStyle.secondary, custom_id="btn_next_page", row=3)

        back_id = "btn_back_battle" if self.in_battle else "btn_back_main"
        self.add_action_button(label="返回", style=discord.ButtonStyle.secondary, custom_id=back_id, emoji="🔙", row=4)

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
        if getattr(self, "viewing_leaderboard", False):
            return self.build_leaderboard_embed()
        embed = discord.Embed(color=discord.Color.dark_theme())
        p = self.player
        area_name = self.cog.areas.get(p.current_area, {}).get("area_name", "未知區域")
        user_bal = self.cog.get_bank_balance(self.user_id)
        status_text = format_status_list(p.status_effects, self.cog.status_effects)
        state_text = "⚔️ 戰鬥中" if self.in_battle else "🌿 探索中"

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

        # 戰鬥時顯示敵方狀態區塊（最多 3 格，前排優先顯示在最上面）
        if self.in_battle and self.monster_slots:
            front_seen = False
            for slot in self.monster_slots:
                m = slot["monster"]
                is_front = (not front_seen) and slot["hp"] > 0
                if slot["hp"] > 0:
                    front_seen = True
                row_tag = "🎯 前排" if is_front else ("　 後排" if slot["hp"] > 0 else "💀 已擊倒")
                monster_bar = self._generate_action_bar(slot["av"], atb_max)
                monster_desc = (
                    f"❤️ HP: `{max(0, slot['hp']):03d}/{m['max_hp']:03d}`\n"
                    f"⚔️ ATK: `{m['atk']}` | 🛡️ DEF: `{m['def']}` | 🚀 SPD: `{m.get('spd', 0)}`\n"
                    f"⚡ 行動: `[{monster_bar}]`"
                )
                embed.add_field(name=f"{row_tag}：{m['name']}", value=monster_desc, inline=len(self.monster_slots) > 1)

        embed.description = f"```\n{self.log_message}\n```"
        return embed

    async def handle_explore(self):
        if self.player.current_hp <= 0:
            self.log_message = "❌ 你已經倒下了，請先去旅館休息療傷！"
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
        
        monster_instance = dict(base_monster)
        monster_instance.setdefault("id", selected_id)

        # 👇 3. 套用名稱標籤與數值倍率
        monster_instance["level"] = m_level
        monster_instance["name"] = f"{base_monster['name']} (Lv.{m_level})"
        monster_instance["max_hp"] = max(1, int(base_monster["max_hp"] * scale))
        monster_instance["atk"] = max(1, int(base_monster["atk"] * scale))
        monster_instance["def"] = max(1, int(base_monster["def"] * scale))
        monster_instance["exp"] = max(1, int(base_monster.get("exp", 10) * scale))

        # 👇 4. 動態生成速度 (SPD)：如果有寫死就用，沒有就根據等級隨機生成
        default_spd = int(10 + m_level * 1.5 + random.randint(-2, 2))
        monster_instance["spd"] = base_monster.get("spd", default_spd)

        self.start_combat([monster_instance])
        self.cog.save_players()

        self.log_message = f"⚔️ 遭遇了【{monster_instance['name']}】！對方來勢洶洶！"
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
            self.cog.adjust_bank(self.user_id, rewards["gold"])
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
            bal = self.cog.get_bank_balance(self.user_id)
            loss_g = min(bal, event["gold_loss"])
            self.cog.adjust_bank(self.user_id, -loss_g)
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
        if skill_id not in getattr(self.player, "equipped_skills", []):
            self.log_message = "❌ 你尚未裝備這個技能。"
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
        # 戰鬥中只顯示裝備中的主動技能
        if not getattr(self.player, "equipped_skills", None):
            self.player.equipped_skills = []
        
        active_skills = [
            s for s in self.player.equipped_skills
            if self.cog.skills.get(s, {}).get("type") != "passive"
        ]
        if not active_skills:
            self.log_message = "❌ 你尚未裝備任何可施放的技能！請去教堂進行【技能配置】。"
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
            self.log_message = prefix + "📖 【學習魔法】\n背包裡沒有技能卷軸。可從商店購買，或討伐區域 BOSS 取得！"
        else:
            self.log_message = prefix + "📖 【學習魔法】\n選擇要研讀的卷軸（消耗 1 張）："
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

        self.add_action_button(label="返回教堂", style=discord.ButtonStyle.secondary, custom_id="btn_church_menu", emoji="🔙")

    async def handle_skill_equip_menu(self, notice=""):
        self.clear_items()
        prefix = notice + "\n\n" if notice else ""
        
        if not getattr(self.player, "equipped_skills", None):
            self.player.equipped_skills = []
            
        p_skills = [s for s in getattr(self.player, "skills", []) if self.cog.skills.get(s, {}).get("type") != "passive"]
        
        if not p_skills:
            self.log_message = prefix + "🔧 【技能配置】\n你尚未習得任何主動技能。請先【學習魔法】！"
            self.add_action_button(label="返回教堂", style=discord.ButtonStyle.secondary, custom_id="btn_church_menu", emoji="🔙")
            return

        equipped_count = len(self.player.equipped_skills)
        self.log_message = prefix + f"🔧 【技能配置】 (已裝備: {equipped_count}/8)\n點擊下方按鈕來裝備或卸下你的戰鬥技能。"
        
        for skill_id in p_skills:
            skill = self.cog.skills.get(skill_id, {})
            skill_name = skill.get("name", skill_id)
            
            if skill_id in self.player.equipped_skills:
                self.add_action_button(
                    label=f"🟢 卸下: {skill_name}",
                    style=discord.ButtonStyle.success,
                    custom_id=f"unequip_skill_{skill_id}"
                )
            else:
                is_full = equipped_count >= 8
                self.add_action_button(
                    label=f"⚪ 裝備: {skill_name}",
                    style=discord.ButtonStyle.secondary if is_full else discord.ButtonStyle.primary,
                    custom_id=f"equip_skill_{skill_id}"
                )
                
        self.add_action_button(label="返回教堂", style=discord.ButtonStyle.secondary, custom_id="btn_church_menu", emoji="🔙")

    async def handle_skill_equip_action(self, skill_id: str, equip: bool):
        if not getattr(self.player, "equipped_skills", None):
            self.player.equipped_skills = []
            
        if equip:
            if len(self.player.equipped_skills) >= 8:
                await self.handle_skill_equip_menu("❌ 技能裝備已達上限 (8/8)！請先卸下其他技能。")
                return
            if skill_id not in self.player.equipped_skills:
                self.player.equipped_skills.append(skill_id)
                self.cog.save_players()
                await self.handle_skill_equip_menu(f"✅ 已裝備技能：{self.cog.skills.get(skill_id, {}).get('name', skill_id)}")
        else:
            if skill_id in self.player.equipped_skills:
                self.player.equipped_skills.remove(skill_id)
                self.cog.save_players()
                await self.handle_skill_equip_menu(f"✅ 已卸下技能：{self.cog.skills.get(skill_id, {}).get('name', skill_id)}")

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
        req_level = area_data.get("req_level", 1)
        # 現在先不用動因為要測試遊戲
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

        user_bal = self.cog.get_bank_balance(self.user_id)

        status_embed = discord.Embed(title=f"📜 {interaction.user.name} 的詳細冒險狀態", color=discord.Color.blue())
        status_embed.add_field(name="等級與經驗", value=f"Lv.{p.level} (EXP: {p.exp}/{exp_to_next_level(p.level)})", inline=True)
        status_embed.add_field(name="錢包餘額", value=f"{user_bal} {self.cog.bot.baba.money_name}", inline=True)
        
        prestige = getattr(p, "prestige_count", 0)
        if prestige > 0:
            status_embed.add_field(name="轉生階級", value=f"🌟 {prestige} 轉 (全屬性 +{prestige*10}%)", inline=True)
            
        trophies = getattr(p, "trophies", [])
        if trophies:
            status_embed.add_field(name="🏆 榮譽勳章", value=" ".join(trophies), inline=False)

        alloc_text = format_stat_alloc_summary(p)
        status_embed.add_field(
            name="戰鬥核心數值",
            value=(
                f"❤️ HP: {p.current_hp}/{p.max_hp}\n"
                f"💧 MP: {p.current_mp}/{p.max_mp}\n"
                f"⚔️ ATK: {get_player_atk(p, self.cog.items, self.cog.status_effects)} | 🛡️ DEF: {get_player_def(p, self.cog.items)}\n"
                f"✨ MAG: {get_player_magic(p, self.cog.items, self.cog.status_effects)} | 🔰 RES: {getattr(p, 'base_res', 0)}\n"
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
        if unspent > 0:
            self.log_message += f"\n\n**您還有 {unspent} 點屬性點可以分配！**\n(💡 點擊「+1」按鈕投資1點，點擊「All-in」投資所有剩餘點數，或點擊「批量分配」填寫數字)"
            
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
            
            self.add_action_button(label="批量分配", style=discord.ButtonStyle.success, custom_id="btn_stat_bulk", row=2, emoji="⌨️")

        self.add_action_button(label="重置所有屬性點", style=discord.ButtonStyle.danger, custom_id="btn_stat_reset", row=2 if unspent > 0 else 0, emoji="🔄")
        self.add_action_button(label="返回", style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    async def handle_stat_add(self, stat_key: str, all_in: bool = False):
        from trpg_stats import get_unspent_points, recalc_player_stats, default_stat_alloc
        unspent = get_unspent_points(self.player)
        if unspent <= 0:
            await self.handle_stat_alloc_menu("❌ 你沒有可用的屬性點了。")
            return
            
        if not getattr(self.player, "stat_alloc", None):
            self.player.stat_alloc = default_stat_alloc()

        add_amount = unspent if all_in else 1
        self.player.stat_alloc[stat_key] = self.player.stat_alloc.get(stat_key, 0) + add_amount
        recalc_player_stats(self.player, self.cog.items, heal_full=False)
        self.cog.save_players()
        await self.handle_stat_alloc_menu(f"✅ 已將 {add_amount} 點投入【{stat_key.upper()}】。")

    async def handle_stat_reset(self):
        self.player.stat_alloc = default_stat_alloc()
        recalc_player_stats(self.player, self.cog.items, heal_full=False)
        self.cog.save_players()
        await self.handle_stat_alloc_menu("🔄 已重置所有屬性配點，請重新分配。")

    async def handle_rest(self):
        user_bal = self.cog.get_bank_balance(self.user_id)
        if user_bal < 20:
            self.log_message = "❌ 你身上的硬幣連旅館的乾草床都租不起！去打怪賺錢！"
            return
        if self.player.current_hp == self.player.max_hp and self.player.current_mp == self.player.max_mp and not self.player.status_effects:
            self.log_message = "❓ 你精神飽滿，去睡覺只是在浪費錢。"
            return

        self.cog.adjust_bank(self.user_id, -20)
        if not getattr(self.player, "stats", None):
            self.player.stats = {}
        self.player.stats["money_spent"] = self.player.stats.get("money_spent", 0) + 20
        
        self.player.current_hp = self.player.max_hp
        self.player.current_mp = self.player.max_mp
        clear_all_status(self.player)
        self.log_message = "💤 在村莊溫暖的旅店休息了一晚，體力、魔力恢復，異常狀態也清除了！(扣除 20$)"
        
        achv_text = self.check_achievements()
        if achv_text:
            self.log_message += achv_text
            
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

        # 點擊「挑戰」後，從共用怪物池依樓層抽怪；每 5 層换成該 tier 的 BOSS（與既有里程碑獎勵並存，純粹是額外的難度/外觀層）
        is_boss_floor = (floor % 5 == 0)
        tower_monster = pick_random_monster(self.cog.monster_pool, floor, want_boss=is_boss_floor, floor_scale=1.0)
        tower_monster["is_tower"] = True
        self.start_combat([tower_monster])

        if is_boss_floor:
            self.log_message = f"🗼 【魔塔第 {floor} 層 - 魔力凝聚！】\n空氣劇烈震動，一股強大的氣息擋住了去路——是這層的首領【{tower_monster['name']}】！"
        else:
            self.log_message = f"🗼 【魔塔第 {floor} 層】\n空氣越來越稀薄。一隻【{tower_monster['name']}】擋住了去路！"
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

    async def handle_tower_merchant(self, notice=""):
        self.clear_items()
        self.current_menu_state = "tower_merchant"
        floor = self.player.tower_floor
        items = getattr(self, "tower_merchant_items", [])

        prefix = notice + "\n\n" if notice else ""
        self.log_message = prefix + f"🎭 【第 {floor} 層 - 神祕商人】\n「稀有貨色，看看吧，過了這層樓可不一定還能再遇到我。」"
        if not items:
            self.log_message += "\n（他翻了翻行囊，似乎今天沒帶什麼貨。）"
        else:
            for item_id in items:
                item = self.cog.items.get(item_id)
                if not item:
                    continue
                self.add_action_button(
                    label=f"買 {item['name']} ({item['price']}$)"[:80],
                    style=discord.ButtonStyle.primary,
                    custom_id=f"buy_{item_id}",
                )

        self.add_action_button(label="返回", style=discord.ButtonStyle.secondary, custom_id="btn_tower_safe_room", emoji="🔙")

    def build_dungeon_menu(self):
        self.clear_items()
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
            self.log_message = "🕳️ **【無盡深淵地下城】**\n這裡有著奇異的規則，你的真實力量已被封印。你將從零開始，依賴這裡獲取的裝備與技能進行挑戰。只有通關或死亡才會結算真實獎勵！"

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
            self.log_message = f"🎉 你成功通關了地下城第 15 層！\n所有的臨時力量都消散了，但你帶回了豐厚的寶藏：\n💰 獲得 {reward_gold} 金幣\n✨ 獲得 {reward_exp} 經驗值"
            real_p.current_area = "area_00village"
            self.build_main_menu()
            return

        msg = self.log_message + f"\n\n🏰 **地下城 - 第 {floor}/15 層**\n⏳ 剩餘行動點數 (AP): {ap}\n\n你想要做什麼？"
        self.log_message = msg

        if ap > 0:
            self.add_action_button(label="🚪 探索房間 (1 AP)", style=discord.ButtonStyle.primary, custom_id="btn_dung_explore")
            self.add_action_button(label="🔥 休息 (1 AP)", style=discord.ButtonStyle.success, custom_id="btn_dung_rest")
            self.add_action_button(label="📊 整備與強化 (1 AP)", style=discord.ButtonStyle.secondary, custom_id="btn_dung_upgrade")
        else:
            self.add_action_button(label="🪜 前往下一層", style=discord.ButtonStyle.primary, custom_id="btn_dung_next")

        self.add_action_button(label="放棄探索", style=discord.ButtonStyle.danger, custom_id="btn_dung_flee", emoji="🏃")

    async def handle_dung_explore(self):
        d_state = self.player.real_player.dungeon_state if hasattr(self.player, 'real_player') else self.player.dungeon_state
        if d_state.get("ap", 0) <= 0: return
        self.clear_items()
        self.log_message = "🚪 你來到了三扇門前，你要進入哪一扇？"
        import random
        if not d_state.get("choices"):
            pool = ["monster", "event", "treasure"]
            d_state["choices"] = []
            for _ in range(3):
                t = random.choice(pool)
                lbl = "未知房間"
                emo = "❓"
                if t == "monster": lbl, emo = "怪物通道", "👹"
                elif t == "treasure": lbl, emo = "寶藏房間", "🎁"
                elif t == "event": lbl, emo = "隨機事件", "🌟"
                d_state["choices"].append({"type": t, "label": lbl, "emoji": emo})
            self.cog.save_players()
        for i, ch in enumerate(d_state["choices"]):
            self.add_action_button(label=f"進入 {ch['label']}", style=discord.ButtonStyle.primary, custom_id=f"dung_enter_{i}", emoji=ch["emoji"])
        self.add_action_button(label="返回", style=discord.ButtonStyle.secondary, custom_id="btn_back_dungeon")
        
    async def handle_dung_enter(self, idx: int):
        d_state = self.player.real_player.dungeon_state if hasattr(self.player, 'real_player') else self.player.dungeon_state
        if d_state.get("ap", 0) <= 0: return
        choices = d_state.get("choices", [])
        if idx >= len(choices): return
        ch = choices[idx]
        d_state["ap"] -= 1
        d_state["choices"] = [] # clear choices after selecting
        
        floor = d_state.get("floor", 1)
        if ch["type"] in ["monster", "elite", "boss"]:
            from trpg_monster_pool import pick_random_monster
            monster_def = pick_random_monster(self.cog.monster_pool, floor, want_boss=False, floor_scale=1.5)
            dungeon_monster = {
                "id": monster_def["id"],
                "name": f"💀 {monster_def['name']}",
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
            self.log_message = f"⚔️ 遭遇戰鬥！你遇到了 {dungeon_monster['name']}！"
            self.build_battle_menu()
        elif ch["type"] == "event":
            import random
            event_type = random.choice(["heal", "trap", "chest"])
            if event_type == "heal":
                heal = int(d_state["max_hp"] * 0.5)
                d_state["current_hp"] = min(d_state["max_hp"], d_state.get("current_hp", 0) + heal)
                self.log_message = f"✨ 你發現了一池散發著柔和光芒的泉水。回復了 {heal} 點 HP！"
            elif event_type == "trap":
                dmg = int(d_state["max_hp"] * 0.2)
                d_state["current_hp"] -= dmg
                self.log_message = f"💥 不小心踩到了陷阱！受到了 {dmg} 點傷害！"
                if d_state["current_hp"] <= 0:
                    self.log_message += "\n💀 你在地下城中喪命了..."
                    d_state["in_run"] = False
                    real_p = self.player.real_player if hasattr(self.player, 'real_player') else self.player
                    real_p.current_area = "area_00village"
                    self.build_main_menu()
                    return
            elif event_type == "chest":
                gold = random.randint(50, 150) * floor
                d_state["inventory"]["gold"] = d_state.get("inventory", {}).get("gold", 0) + gold
                self.log_message = f"🎁 你打開了一個寶箱，獲得了 {gold} 枚臨時金幣！"
            self.build_dungeon_menu()
        else:
            gold = 100 * floor
            d_state["inventory"]["gold"] = d_state.get("inventory", {}).get("gold", 0) + gold
            self.log_message = f"🎁 你打開了一個寶箱，獲得了 {gold} 枚臨時金幣！"
            self.build_dungeon_menu()

    async def handle_dung_rest(self):
        d_state = self.player.real_player.dungeon_state if hasattr(self.player, 'real_player') else self.player.dungeon_state
        if d_state.get("ap", 0) <= 0: return
        d_state["ap"] -= 1
        heal = int(d_state["max_hp"] * 0.3)
        d_state["current_hp"] = min(d_state["max_hp"], d_state.get("current_hp", 0) + heal)
        self.log_message = f"🔥 你升起營火稍作休息，回復了 {heal} 點 HP！"
        self.build_dungeon_menu()

    async def handle_dung_upgrade(self):
        d_state = self.player.real_player.dungeon_state if hasattr(self.player, 'real_player') else self.player.dungeon_state
        if d_state.get("ap", 0) <= 0: return
        d_state["ap"] -= 1
        points = d_state.get("stat_points", 0)
        self.log_message = f"📊 **整備與強化**\n你整理了行囊。目前尚有 {points} 點未分配屬性！(提示: 你可以點擊返回並使用一般配點功能，但需在此消耗 1 AP 打開權限)"
        self.build_dungeon_menu()

    async def handle_dung_next(self):
        d_state = self.player.real_player.dungeon_state if hasattr(self.player, 'real_player') else self.player.dungeon_state
        d_state["floor"] += 1
        d_state["ap"] = 3
        d_state["choices"] = []
        if d_state["floor"] == 15:
            self.log_message = f"🪜 你來到了第 {d_state['floor']} 層... 深處傳來恐怖的咆哮聲，Boss 就在前方！"
            # spawn boss immediately
            from trpg_monster_pool import pick_random_monster
            monster_def = pick_random_monster(self.cog.monster_pool, 15, want_boss=True, floor_scale=1.5)
            dungeon_monster = {
                "id": monster_def["id"],
                "name": f"💀 {monster_def['name']}",
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
            self.log_message = f"🪜 你小心翼翼地走下階梯，來到了第 {d_state['floor']} 層..."
            self.build_dungeon_menu()

    async def handle_dungeon_flee(self):
        d_state = self.player.real_player.dungeon_state if hasattr(self.player, 'real_player') else self.player.dungeon_state
        d_state["in_run"] = False
        self.log_message = "🏃 你帶著遺憾離開了地下城。所有的臨時裝備與經驗都化為烏有了。"
        real_p = self.player.real_player if hasattr(self.player, 'real_player') else self.player
        real_p.current_area = "area_00village"
        self.build_main_menu()

    def build_legend_cave_menu(self):
        self.clear_items()
        area_data = self.cog.areas.get("area_legend_cave", {})
        nodes = area_data.get("nodes", {})
        cave_state = self.player.cave_state
        node_id = cave_state.get("current_node", "entrance")
        node = nodes.get(node_id) or nodes.get("entrance", {})
        sword_id = area_data.get("sword_item", "hero_sword")

        if node.get("is_sword_room") and not self.player.inventory.get(sword_id, 0):
            self.player.inventory[sword_id] = self.player.inventory.get(sword_id, 0) + 1
            sword_name = self.cog.items.get(sword_id, {}).get("name", sword_id)
            self.cog.save_players()
            self.log_message = (
                f"🕯️ {node.get('desc', '')}\n\n"
                f"✨ 你從石墩上拔起了【{sword_name}】！這把劍似乎在期待著與魔王的決戰。\n"
                f"（記得回村莊把它裝備上！）"
            )
            self.add_action_button(label="離開洞窟", style=discord.ButtonStyle.success, custom_id="cave_dir_back", emoji="🚪")
            return

        hp_pct = int(self.player.current_hp / self.player.max_hp * 100) if self.player.max_hp else 0
        self.log_message = (
            f"🕯️ **【傳說洞窟】**\n{node.get('desc', '...')}\n\n"
            f"❤️ 目前生命值：{self.player.current_hp}/{self.player.max_hp} ({hp_pct}%)"
        )

        exits = node.get("exits", {})
        if exits.get("forward"):
            self.add_action_button(label="前進", style=discord.ButtonStyle.primary, custom_id="cave_dir_forward", emoji="⬆️")
        if exits.get("left"):
            self.add_action_button(label="左邊", style=discord.ButtonStyle.primary, custom_id="cave_dir_left", emoji="⬅️")
        if exits.get("right"):
            self.add_action_button(label="右邊", style=discord.ButtonStyle.primary, custom_id="cave_dir_right", emoji="➡️")
        self.add_action_button(label="返回", style=discord.ButtonStyle.secondary, custom_id="cave_dir_back", emoji="🔙")

    async def handle_cave_move(self, direction: str):
        area_data = self.cog.areas.get("area_legend_cave", {})
        nodes = area_data.get("nodes", {})
        cave_state = self.player.cave_state
        node_id = cave_state.get("current_node", "entrance")
        node = nodes.get(node_id) or nodes.get("entrance", {})
        history = cave_state.setdefault("history", [])

        if direction == "back":
            if node.get("is_sword_room") or not history:
                self.log_message = "🚪 你退出了傳說洞窟，回到了村莊。"
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
                f"🕯️ {target_node.get('desc', '')}\n\n💥 濃郁的魔力侵蝕了你最後的生命力，扣除了 {dmg} 點 HP！",
                "💀 你倒在了傳說洞窟的深處...",
            )
            return

        self.build_legend_cave_menu()

    async def handle_prestige_menu(self, notice=""):
        self.clear_items()
        prefix = notice + "\n\n" if notice else ""
        
        prestige = getattr(self.player, "prestige_count", 0)
        bonus = prestige * 10
        
        self.log_message = (
            prefix
            + "🌟 【轉生殿堂】\n"
            + "在這裡，你可以超越冒險者的極限，重獲新生！\n"
            + f"• 當前等級：Lv.{self.player.level} (轉生需要 Lv.30)\n"
            + f"• 當前轉生次數：{prestige} 轉\n"
            + f"• 當前轉生被動增幅：全屬性 +{bonus}%\n\n"
            + "⚠️ 【轉生規則說明】\n"
            + "1. 轉生將使你的等級重置回 Lv.1，EXP 歸零，並重置屬性配點。\n"
            + "2. 轉生後你將獲得 1 層永久被動增幅，所有戰鬥屬性額外 +10%！\n"
            + "3. 轉生會卸下你身上的武器、防具與飾品，並重置魔塔／地下城的目前樓層（已達成的里程碑勳章與獎杯不會消失）。\n"
            + "4. 轉生不會清除你的背包道具、金幣與已學會的技能。"
        )
        
        if self.player.level >= 30:
            self.add_action_button(label="確認轉生 (Lv.30+)", style=discord.ButtonStyle.danger, custom_id="btn_prestige_confirm", emoji="🌟")
        else:
            disabled_btn = discord.ui.Button(label="等級不足 Lv.30", style=discord.ButtonStyle.secondary, disabled=True, emoji="❌")
            self.add_item(disabled_btn)
            
        self.add_action_button(label="返回村莊", style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    async def handle_prestige_confirm(self):
        if self.player.level < 30:
            self.log_message = "❌ 轉生失敗：你的等級不足 Lv.30！"
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

        self.log_message = f"🌟 恭喜成功轉生！你已重回 Lv.1，並永久獲得 +{self.player.prestige_count * 10}% 的全屬性增幅！\n（魔塔/地下城樓層已重置，武器/防具/飾品已卸下）"
        self.build_main_menu()

    async def handle_craft_menu(self, notice=""):
        self.clear_items()
        prefix = notice + "\n\n" if notice else ""
        
        self.log_message = prefix + "🔨 【手藝工坊】\n利用冒險收集的材料合成強力的裝備吧！\n"
        
        user_bal = self.cog.get_bank_balance(self.user_id)
        
        for item_id, recipe in CRAFTING_RECIPES.items():
            materials_desc = []
            can_craft = True
            
            for mat_id, req_qty in recipe["materials"].items():
                mat_name = self.cog.items.get(mat_id, {}).get("name", mat_id)
                current_qty = self.player.inventory.get(mat_id, 0)
                materials_desc.append(f"{mat_name} ({current_qty}/{req_qty})")
                if current_qty < req_qty:
                    can_craft = False
                    
            if user_bal < recipe["gold"]:
                can_craft = False
                
            item_data = self.cog.items.get(item_id, {})
            comp_str = self._get_equipment_comparison_string(item_data)
            comp_suffix = f" {comp_str}" if comp_str else ""
            
            desc_line = f"• **{recipe['name']}**{comp_suffix} | {recipe['gold']}$ | 材料: {', '.join(materials_desc)}"
            self.log_message += f"\n{desc_line}"
            
            style = discord.ButtonStyle.primary if can_craft else discord.ButtonStyle.secondary
            
            btn_label = f"製作 {recipe['name']}"
            if comp_str:
                btn_label += f" {comp_str}"
                
            self.add_action_button(
                label=btn_label[:80],
                style=style,
                custom_id=f"craft_{item_id}"
            )
            
        self.add_action_button(label="返回村莊", style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    async def handle_craft_execute(self, item_id: str):
        recipe = CRAFTING_RECIPES.get(item_id)
        if not recipe: return
        
        user_bal = self.cog.get_bank_balance(self.user_id)
        if user_bal < recipe["gold"]:
            await self.handle_craft_menu("❌ 金幣不足！")
            return
            
        for mat_id, req_qty in recipe["materials"].items():
            current_qty = self.player.inventory.get(mat_id, 0)
            if current_qty < req_qty:
                await self.handle_craft_menu("❌ 材料不足！")
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
            equip_msg = "，已為你自動裝備"
        elif item_data.get("type") == "armor":
            self.player.armor = item_id
            equip_msg = "，已為你自動穿戴"
            
        recalc_player_stats(self.player, self.cog.items, heal_full=False)
        
        achv_text = self.check_achievements()
        notice_text = f"🎉 製作成功！你獲得了【{recipe['name']}】{equip_msg}！"
        if achv_text:
            notice_text += achv_text
            
        self.cog.save_players()
        await self.handle_craft_menu(notice_text)

    async def handle_blacksmith_menu(self, notice=""):
        self.clear_items()
        prefix = notice + "\n\n" if notice else ""
        
        p = self.player
        self.log_message = prefix + "⚒️ 【鐵匠鋪】\n把你的裝備交給熟練的鐵匠吧！花費金幣與怪物的材料，可以強化武器與防具。\n"
        
        # 取得目前裝備資訊
        w_id = getattr(p, "weapon", None)
        a_id = getattr(p, "armor", None)
        
        w_name = self.cog.items.get(w_id, {}).get("name", "無") if w_id else "無"
        a_name = self.cog.items.get(a_id, {}).get("name", "無") if a_id else "無"
        
        w_up = getattr(p, "weapon_upgrade", 0)
        a_up = getattr(p, "armor_upgrade", 0)
        
        self.log_message += f"\n⚔️ 目前武器：【{w_name}】" + (f" (+{w_up})" if w_id and w_up > 0 else "")
        self.log_message += f"\n🛡️ 目前防具：【{a_name}】" + (f" (+{a_up})" if a_id and a_up > 0 else "")
        self.log_message += "\n"
        
        user_bal = self.cog.get_bank_balance(self.user_id)
        
        # 武器強化資訊
        can_up_w = False
        w_desc = "無法強化（未裝備武器）"
        if w_id:
            if w_up >= 5:
                w_desc = "已達到最高強化等級 (+5)"
            else:
                next_lvl = w_up + 1
                cost = UPGRADE_COSTS[next_lvl]
                mat_name = self.cog.items.get(cost["material"], {}).get("name", cost["material"])
                current_qty = p.inventory.get(cost["material"], 0)
                
                w_desc = f"升級至 +{next_lvl} | 成功率: {cost['label']}\n花費: {cost['gold']}$ | 材料: {mat_name} ({current_qty}/{cost['mat_qty']})"
                
                if user_bal >= cost["gold"] and current_qty >= cost["mat_qty"]:
                    can_up_w = True
        
        self.log_message += f"\n**武器強化：**\n{w_desc}\n"
        
        # 防具強化資訊
        can_up_a = False
        a_desc = "無法強化（未裝備防具）"
        if a_id:
            if a_up >= 5:
                a_desc = "已達到最高強化等級 (+5)"
            else:
                next_lvl = a_up + 1
                cost = UPGRADE_COSTS[next_lvl]
                mat_name = self.cog.items.get(cost["material"], {}).get("name", cost["material"])
                current_qty = p.inventory.get(cost["material"], 0)
                
                a_desc = f"升級至 +{next_lvl} | 成功率: {cost['label']}\n花費: {cost['gold']}$ | 材料: {mat_name} ({current_qty}/{cost['mat_qty']})"
                
                if user_bal >= cost["gold"] and current_qty >= cost["mat_qty"]:
                    can_up_a = True
                    
        self.log_message += f"\n**防具強化：**\n{a_desc}\n"
        
        # 按鈕
        w_style = discord.ButtonStyle.primary if can_up_w else discord.ButtonStyle.secondary
        w_label = "強化武器"
        if w_id and w_up < 5:
            w_label += " [⚔️ATK+3▲]"
        self.add_action_button(
            label=w_label,
            style=w_style,
            custom_id="btn_upgrade_weapon" if can_up_w else "btn_disabled_w"
        )
        
        a_style = discord.ButtonStyle.primary if can_up_a else discord.ButtonStyle.secondary
        a_label = "強化防具"
        if a_id and a_up < 5:
            a_label += " [🛡️DEF+2▲ ❤️HP+15▲]"
        self.add_action_button(
            label=a_label,
            style=a_style,
            custom_id="btn_upgrade_armor" if can_up_a else "btn_disabled_a"
        )
        
        self.add_action_button(label="返回村莊", style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    async def handle_upgrade_execute(self, is_weapon: bool):
        p = self.player
        slot = "weapon" if is_weapon else "armor"
        item_id = getattr(p, slot, None)
        if not item_id:
            await self.handle_blacksmith_menu("❌ 你沒有裝備任何對應的裝備！")
            return
            
        current_up = getattr(p, f"{slot}_upgrade", 0)
        if current_up >= 5:
            await self.handle_blacksmith_menu("❌ 該裝備已達到最高強化等級 (+5)！")
            return
            
        next_lvl = current_up + 1
        cost = UPGRADE_COSTS[next_lvl]
        
        user_bal = self.cog.get_bank_balance(self.user_id)
        if user_bal < cost["gold"]:
            await self.handle_blacksmith_menu("❌ 金幣不足！")
            return
            
        current_qty = p.inventory.get(cost["material"], 0)
        if current_qty < cost["mat_qty"]:
            await self.handle_blacksmith_menu("❌ 強化材料不足！")
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
            item_name = self.cog.items.get(item_id, {}).get("name", item_id)
            notice_text = f"✨ 🌟 強化成功！\n你的【{item_name}】成功強化至 **+{next_lvl}**！"
            if achv_text:
                notice_text += achv_text
                
            self.cog.save_players()
            await self.handle_blacksmith_menu(notice_text)
        else:
            achv_text = self.check_achievements()
            notice_text = f"💥 強化失敗！\n材料被熔毀了，但是鐵匠拼命保住了你的裝備，等級維持在 **+{current_up}**。"
            if achv_text:
                notice_text += achv_text
                
            self.cog.save_players()
            await self.handle_blacksmith_menu(notice_text)

    def build_leaderboard_embed(self) -> discord.Embed:
        players = list(self.cog.players.values())
        
        # 1. 等級排行
        lvl_rank = sorted(players, key=lambda x: x.level, reverse=True)[:5]
        lvl_desc = "\n".join([f"🏆 **Rank {i+1}** | Lv.{p.level} - <@{p.id}>" for i, p in enumerate(lvl_rank)])
        if not lvl_desc: lvl_desc = "無資料"
        
        # 2. 魔塔排行
        tower_rank = sorted(players, key=lambda x: getattr(x, "tower_floor", 1), reverse=True)[:5]
        tower_desc = "\n".join([f"🏆 **Rank {i+1}** | {getattr(p, 'tower_floor', 1)}層 - <@{p.id}>" for i, p in enumerate(tower_rank)])
        if not tower_desc: tower_desc = "無資料"
        
        # 3. 擊殺排行
        kill_rank = sorted(players, key=lambda x: x.stats.get("monsters_killed", 0) if getattr(x, "stats", None) else 0, reverse=True)[:5]
        kill_desc = "\n".join([f"🏆 **Rank {i+1}** | {p.stats.get('monsters_killed', 0) if getattr(p, 'stats', None) else 0}隻 - <@{p.id}>" for i, p in enumerate(kill_rank)])
        if not kill_desc: kill_desc = "無資料"
        
        # 4. 金幣排行
        bank_rank = []
        for p in players:
            bal = self.cog.get_bank_balance(p.id)
            bank_rank.append((p.id, bal))
        bank_rank = sorted(bank_rank, key=lambda x: x[1], reverse=True)[:5]
        bank_desc = "\n".join([f"🏆 **Rank {i+1}** | {bal}$ - <@{uid}>" for i, (uid, bal) in enumerate(bank_rank)])
        if not bank_desc: bank_desc = "無資料"
        
        embed = discord.Embed(title="🏆 【皇家冒險者公會 - 全服英雄榜】", color=discord.Color.gold())
        embed.description = "冒險者們的傳奇戰績已被記錄於此。不斷前進，刻下你的名字吧！"
        embed.add_field(name="🎖️ 等級最高殿堂", value=lvl_desc, inline=False)
        embed.add_field(name="🗼 魔塔最高登頂層數", value=tower_desc, inline=False)
        embed.add_field(name="⚔️ 累計討伐魔物數量", value=kill_desc, inline=False)
        embed.add_field(name="💰 冒險財富榜", value=bank_desc, inline=False)
        return embed

    async def handle_leaderboard(self):
        self.viewing_leaderboard = True
        self.clear_items()
        self.add_action_button(label="返回村莊", style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    def record_combat_history(self, log_msg: str):
        if not hasattr(self.player, "combat_history"):
            self.player.combat_history = []
        self.player.combat_history.insert(0, log_msg)
        self.player.combat_history = self.player.combat_history[:3]

    async def handle_combat_history(self, interaction: discord.Interaction):
        history = getattr(self.player, "combat_history", [])
        if not history:
            await interaction.followup.send("📝 目前沒有任何戰鬥記錄。", ephemeral=True)
            return
            
        embed = discord.Embed(title="📜 最近 3 次冒險戰鬥記錄", color=discord.Color.blue())
        for i, log_entry in enumerate(history):
            snippet = log_entry.strip()
            # remove excessive empty lines to keep it clean
            snippet = "\n".join([line for line in snippet.splitlines() if line.strip()])
            embed.add_field(name=f"戰績 #{i+1}", value=f"```\n{snippet[:1000]}\n```", inline=False)
            
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
        self.achievements = {}
        self.monster_pool = {}
        self.dungeon_events = {}
        self.load_all_config()

    def get_bank_balance(self, user_id) -> int:
        uid = int(user_id)
        val = self.bank.get(uid, (0, False))
        if isinstance(val, (list, tuple)):
            return val[0]
        return 0

    def adjust_bank(self, user_id, amount: int):
        uid = int(user_id)
        val = self.bank.get(uid, (0, False))
        is_vip = val[1] if isinstance(val, (list, tuple)) and len(val) > 1 else False
        current_bal = val[0] if isinstance(val, (list, tuple)) else 0
        new_bal = max(0, current_bal + amount)
        self.bank[uid] = (new_bal, is_vip)
        self.bot.baba.refresh_bank_file()

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

        if os.path.exists(os.path.join(DATA_DIR, "achievements.json")):
            with open(os.path.join(DATA_DIR, "achievements.json"), "r", encoding="utf-8") as f:
                self.achievements = json.load(f)

        # 魔塔／地下城共用怪物池（分層，依樓層抽怪）
        self.monster_pool = load_monster_pool(DATA_DIR)

        if os.path.exists(os.path.join(DATA_DIR, "dungeon_events.json")):
            with open(os.path.join(DATA_DIR, "dungeon_events.json"), "r", encoding="utf-8") as f:
                self.dungeon_events = json.load(f)

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
        try:
            view.message = await interaction.original_response()
        except Exception:
            view.message = None




async def setup(bot):
    await bot.add_cog(TRPGCog(bot))
    print("TRPG 完全按鈕驅動系統載入完成！")