"""TRPGCog — Discord cog: config loading, player persistence, and slash commands."""

import discord
from discord.ext import commands
from discord import app_commands
import json
import os

from trpg.i18n import t
from trpg.monster_pool import load_monster_pool
from trpg.stats import recalc_player_stats, migrate_player_stats
from trpg.player import TRPGPlayer
from trpg.view import TRPGGameView

DATA_DIR = "trpg_data"


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
        self.dungeon_relics = {}
        self.dungeon_items = {}
        # user_id(str) -> 該玩家目前開著的那份 TRPGGameView。用來在 /trpg 重開時停用
        # 舊面板——不然兩份面板的 active_battle 快照會共用同一個 monster_slots list
        # 物件，玩家能兩邊面板輪流點按鈕，等於一回合打兩次（見 start_trpg）。
        self.active_views = {}
        self.active_slots = {}
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

    def try_spend(self, user_id, player, amount: int) -> bool:
        """扣款 + 累計 money_spent 統計，供商店／鍛造／鐵匠／旅館等所有花錢動作共用。
        餘額不足時完全不動作、回傳 False；文案（要顯示什麼不足訊息）交給呼叫端，
        這裡只保證「錢有沒有夠、扣了沒、money_spent 統計有沒有記」三件事一致，不會
        有地方漏記 money_spent（漏記過去發生過，因為每個花錢的地方都各自兜一次）。"""
        if amount <= 0:
            return True
        if self.get_bank_balance(user_id) < amount:
            return False
        self.adjust_bank(user_id, -amount)
        if not isinstance(getattr(player, "stats", None), dict):
            player.stats = {}
        player.stats["money_spent"] = player.stats.get("money_spent", 0) + amount
        return True

    @staticmethod
    def _load_json(filename: str, default):
        """讀取 trpg_data 下的單一 JSON 檔，找不到就回傳 default。"""
        path = os.path.join(DATA_DIR, filename)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8-sig") as f:
                return json.load(f)
        return default

    def load_all_config(self):
        os.makedirs(DATA_DIR, exist_ok=True)

        # 單檔設定：欄位名稱 -> JSON 檔名
        for attr, filename in (
            ("items", "items.json"),
            ("skills", "skills.json"),
            ("status_effects", "status_effects.json"),
            ("quests", "quests.json"),
            ("events", "events.json"),
            ("achievements", "achievements.json"),
            ("dungeon_events", "dungeon_events.json"),
            ("dungeon_relics", "dungeon_relics.json"),
            ("dungeon_items", "dungeon_items.json"),
        ):
            setattr(self, attr, self._load_json(filename, {}))

        # 魔塔／地下城共用怪物池（分層，依樓層抽怪）
        self.monster_pool = load_monster_pool(DATA_DIR)

        # 讀取並動態水合區域設定
        flat_monsters = self._load_json("monsters.json", {})
        self.areas = self._load_json("areas.json", {})
        for area_id, area_data in self.areas.items():
            # 水合 monsters 欄位
            if "monsters" in area_data and isinstance(area_data["monsters"], dict):
                hydrated_monsters = {}
                for m_id, m_cfg in area_data["monsters"].items():
                    m_def = flat_monsters.get(m_id)
                    if m_def:
                        # 複製並合併 spawn_rate 等設定
                        m_instance = dict(m_def)
                        m_instance.update(m_cfg)
                        hydrated_monsters[m_id] = m_instance
                area_data["monsters"] = hydrated_monsters

            # 水合 boss 欄位
            if "boss" in area_data and isinstance(area_data["boss"], str):
                m_def = flat_monsters.get(area_data["boss"])
                if m_def:
                    area_data["boss"] = dict(m_def)

            # 水合 boss_minions 欄位
            if "boss_minions" in area_data and isinstance(area_data["boss_minions"], list):
                hydrated_minions = {}
                for m_id in area_data["boss_minions"]:
                    m_def = flat_monsters.get(m_id)
                    if m_def:
                        hydrated_minions[m_id] = m_def
                area_data["boss_minions"] = hydrated_minions

        # 讀取玩家存檔
        raw = self._load_json(os.path.basename(self.players_file), {})
        self.players = {}
        
        # 解決舊存檔/新存檔的主鍵衝突 (優先採用明確的 uid_slot)
        parsed = {}  # (uid, slot) -> (is_legacy, dict_val)
        for k, v in raw.items():
            parts = k.split("_")
            uid = parts[0]
            slot = parts[1] if len(parts) > 1 else "0"
            is_legacy = len(parts) == 1
            
            key = (uid, slot)
            if key not in parsed:
                parsed[key] = (is_legacy, v)
            else:
                # 衝突處理：如果已有紀錄且現有紀錄是 legacy 格式，而新讀入的是明確的 slot 格式，則覆蓋之。
                existing_is_legacy, _ = parsed[key]
                if existing_is_legacy and not is_legacy:
                    parsed[key] = (is_legacy, v)

        for (uid, slot), (_, v) in parsed.items():
            v["id"] = uid
            v["character_slot"] = slot
            player_key = f"{uid}_{slot}"
            self.players[player_key] = TRPGPlayer.from_dict(v)

        # 讀取 active_slots，並強制標準化與驗證
        raw_active = self._load_json("trpg_active_slots.json", {})
        self.active_slots = {}
        for k, v in raw_active.items():
            uid_str = str(k).strip()
            slot_str = str(v).strip()
            if slot_str in ("0", "1", "2"):
                self.active_slots[uid_str] = slot_str

        # 防禦性檢查：若 active_slots 指向的存檔不存在，自動降級 fallback 回 "0"
        for uid, slot in list(self.active_slots.items()):
            if slot != "0":
                player_key = f"{uid}_{slot}"
                if player_key not in self.players:
                    self.active_slots[uid] = "0"

    def save_players(self):
        """原子寫入：先寫入暫存檔再 os.replace，避免中途中斷造成存檔損毀。"""
        serialized = {k: v.to_dict() for k, v in self.players.items()}
        tmp_path = self.players_file + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(serialized, f, ensure_ascii=False, indent=4)
        os.replace(tmp_path, self.players_file)

        # 儲存 active_slots
        active_slots_path = os.path.join(DATA_DIR, "trpg_active_slots.json")
        tmp_active_path = active_slots_path + ".tmp"
        with open(tmp_active_path, "w", encoding="utf-8") as f:
            json.dump(self.active_slots, f, ensure_ascii=False, indent=4)
        os.replace(tmp_active_path, active_slots_path)

    def get_player(self, user_id):
        uid = str(user_id)
        slot = self.active_slots.get(uid, "0")
        player_key = f"{uid}_{slot}"
        if player_key not in self.players:
            player = TRPGPlayer(uid)
            player.character_slot = slot
            recalc_player_stats(player, self.items, heal_full=True)
            self.players[player_key] = player
            self.save_players()
        else:
            migrate_player_stats(self.players[player_key], self.items)
            if not hasattr(self.players[player_key], "character_slot"):
                self.players[player_key].character_slot = slot
        return self.players[player_key]
    
    async def generate_npc_dialogue(self, prompt: str):
        # 抓取掛載在 bot 上的 response_cog
        ai_cog = self.bot.get_cog("response_cog")
        if ai_cog:
            return await ai_cog.generate_ai_response(prompt)
        return "（NPC 似乎中了沉默魔法，無法說話。）"

    @app_commands.command(name="trpg", description=" 登入並開啟你的專屬 TRPG 冒險面板")
    async def start_trpg(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)

        # 停用這名玩家還開著的舊面板（如果有）：兩份面板同時活著會共用同一個
        # active_battle 快照，讓玩家能兩邊輪流點按鈕變相多打一回合。
        old_view = self.active_views.get(uid)
        if old_view is not None and not old_view.is_finished():
            old_view.stop()
            if old_view.message is not None:
                try:
                    for child in old_view.children:
                        if hasattr(child, "disabled"):
                            child.disabled = True
                    lang = getattr(old_view.player, "language", "zh")
                    notice = t(lang, "menu.superseded_notice", "⚠️ 你在別處開啟了新的冒險面板，這份面板已停用。")
                    embed = old_view.generate_embed()
                    embed.description = f"```\n{notice}\n```"
                    await old_view.message.edit(embed=embed, view=old_view)
                except Exception:
                    pass

        # 初始化專屬此使用者的按鈕控制視圖
        view = TRPGGameView(self, interaction.user.id)
        self.active_views[uid] = view
        embed = view.generate_embed()
        await interaction.response.send_message(embed=embed, view=view)
        try:
            view.message = await interaction.original_response()
        except Exception:
            view.message = None

    @app_commands.command(name="language", description="切換 TRPG 顯示語言 / Switch TRPG display language")
    @app_commands.choices(lang=[
        app_commands.Choice(name="繁體中文", value="zh"),
        app_commands.Choice(name="English", value="en"),
    ])
    async def set_language(self, interaction: discord.Interaction, lang: app_commands.Choice[str]):
        player = self.get_player(interaction.user.id)
        player.language = lang.value
        self.save_players()
        await interaction.response.send_message(
            t(player.language, "lang.switched", "✅ 語言已切換為繁體中文。"),
            ephemeral=True,
        )




async def setup(bot):
    await bot.add_cog(TRPGCog(bot))


async def teardown(bot):
    # 👇 這個 cog 實際定義在 trpg/cog.py，但外面是用 trpg_cog.py 這個相容 shim 的名字
    # (`"trpg_cog"`) 去 load_extension/reload_extension——discord.py 判斷一個 cog
    # 是不是屬於某個 extension，是用「cog.__module__ 是不是等於或是 extension 名字
    # 的子模組」（_is_submodule），而 TRPGCog.__module__ 是 "trpg.cog"，跟 extension
    # 名字 "trpg_cog" 對不起來，所以 reload 時自動清除機制完全不會觸發，舊的 cog
    # 永遠留著，第二次 reload 就會撞上「Cog named 'TRPGCog' already loaded」。
    # 補一個明確的 teardown，reload 時就會先呼叫這個把舊 cog 卸掉，不再依賴那個
    # 對不上的自動比對。
    await bot.remove_cog("TRPGCog")
