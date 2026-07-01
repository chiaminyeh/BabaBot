"""TRPGCog — Discord cog: config loading, player persistence, and slash commands."""

import discord
from discord.ext import commands
from discord import app_commands
import json
import os

from trpg.i18n import t
from trpg.monster_pool import load_monster_pool
from trpg.stats import recalc_player_stats, migrate_player_stats
from trpg.status import activate_jester_immunity
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

    @staticmethod
    def _load_json(filename: str, default):
        """讀取 trpg_data 下的單一 JSON 檔，找不到就回傳 default。"""
        path = os.path.join(DATA_DIR, filename)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
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

        # 掃描動態區域 JSON
        self.areas = {}
        for file in os.listdir(DATA_DIR):
            if file.startswith("area_") and file.endswith(".json"):
                area_id = file.replace(".json", "")
                self.areas[area_id] = self._load_json(file, {})

        # 讀取玩家存檔
        raw = self._load_json(os.path.basename(self.players_file), {})
        self.players = {k: TRPGPlayer.from_dict(v) for k, v in raw.items()}

    def save_players(self):
        """原子寫入：先寫入暫存檔再 os.replace，避免中途中斷造成存檔損毀。"""
        serialized = {k: v.to_dict() for k, v in self.players.items()}
        tmp_path = self.players_file + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(serialized, f, ensure_ascii=False, indent=4)
        os.replace(tmp_path, self.players_file)

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
