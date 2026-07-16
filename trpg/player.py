"""Player save model: TRPGPlayer, shop-level gating, and the dungeon stat wrapper."""

from trpg.stats import default_stat_alloc, grant_qualified_skills, recalc_player_stats, meets_skill_requirements
from trpg.combat import exp_to_next_level
from trpg.balance import ARCHETYPE_BALANCE_VERSION
from trpg.dungeon import SEALED_FIELDS

# Bump this when you change the save schema in a way that needs real migration
# logic (not just a new defaulted field). Stored on every player save.
SCHEMA_VERSION = 4

# Immutable scalar defaults (safe to share — never mutated in place).
_SCALAR_DEFAULTS = {
    "level": 1,
    "exp": 0,
    "max_hp": 60,
    "current_hp": 60,
    "base_atk": 12,
    "base_def": 4,
    "base_mdef": 2,
    "base_magic": 0,
    "max_mp": 25,
    "current_mp": 25,
    "weapon": None,
    "armor": None,
    "accessory": None,
    # 已淘汰（改用下面 weapon_upgrades/armor_upgrades 依裝備各自紀錄），
    # 只留著給 stats.migrate_player_stats 做一次性搬遷用。
    "weapon_upgrade": 0,
    "armor_upgrade": 0,
    "current_area": "area_00village",
    "current_subarea": None,
    "last_shop_refresh": "",
    "jester_immunity_date": "",
    "mystery_merchant_date": "",
    "mystery_shop_active": False,
    "tower_floor": 1,
    "dungeon_floor": 1,
    "prestige_count": 0,
    "last_quest_popup_ts": 0,
    "sargeras_defeat_count": 0,
    "legend_cave_unlocked": False,
    "language": "zh",  # 顯示語言："zh" 或 "en"
    "character_slot": "0",  # 角色存檔編號
    # 進行中戰鬥的快照（monster_slots/combat 狀態），讓 /trpg 重開或面板逾時都能接回原本的戰鬥，
    # 不再是白吃的免費逃跑。戰鬥結束（勝利/死亡/逃跑成功）時清回 None。
    "active_battle": None,
    # 新手引導（語言選擇 + 教學戰）是否已經跑完。這裡預設 False 是給「真正的新玩家」
    # （走 TRPGPlayer(uid) 這條全新建立路徑）用的；from_dict 對舊存檔會另外把這個欄位
    # 補回 True（見下方 from_dict），不然既有玩家下次登入會被誤判成新手，重新看一次教學。
    "onboarding_done": False,
    # 新角色直接使用目前平衡版本；舊存檔在 from_dict 會被標成 0，取得一次免費重置。
    "archetype_balance_version": ARCHETYPE_BALANCE_VERSION,
    "core_ability": None,
    "fortune": 0,
}


def _fresh_containers() -> dict:
    """Per-player mutable defaults — a NEW copy each call so saves never alias."""
    return {
        "inventory": {"health_potion": 2},  # 新玩家初始送兩罐藥水
        "shop_items": [],  # 已淘汰（改用 shop_state 依區域分開存），留著只為了舊存檔相容
        "weapon_upgrades": {},  # {item_id: level} — 強化跟著「這把武器」走，換武器不會繼承等級
        "armor_upgrades": {},   # {item_id: level}
        "shop_state": {},  # {area_id: {"items":[...], "mystery_active":False, "mystery_items":[...]} }
        "stats": {"monsters_killed": 0, "total_deaths": 0, "money_spent": 0},
        "achievements": [],          # 已解鎖成就 ID
        "active_quests": {},         # {"quest_001": {"progress": 2}}
        "completed_quests": [],      # 已完成任務 ID
        "skills": [],                # 已學技能 ID
        "equipped_skills": [],       # 裝備中的技能（上限 8）
        "killed_bosses": [],
        "tower_milestones": [],
        "trophies": [],
        "combat_history": [],
        "status_effects": {},        # {"poison": {"turns": 3}}
        "trade_inbox": [],
        "stat_alloc": default_stat_alloc(),
        "mystery_shop_items": [],
        "dungeon_state": {"floor": 1, "choices": [], "in_run": False},
        "hidden_quest_progress": {},
        "pending_event": {},  # unresolved choice event: {"event_id": ...}
        "repeatable_cooldowns": {},  # {quest_id: "YYYY-MM-DD"} 可重複/每日任務上次完成日期
        "dungeon_buffs": {},
        "dungeon_relic_effects": {},  # 地下城遺物/裝備彙整出的戰鬥 hook（僅 run 內生效）
        "combat_debuffs": {},      # 怪物技能造成的戰鬥內減益
        "combat_buffs": {},        # 玩家技能給的戰鬥內增益（攻防速強化 / 持續治癒）
        "cave_state": {"current_node": "entrance", "history": []},
        # 魔塔目前樓層的休息室狀態——之前這幾個欄位活在 View（不落存檔），關掉/重開
        # 面板或面板逾時都會被重置，玩家能靠反覆重開 /trpg 在同一層免費刷回滿血、
        # 反覆重骰神秘商人。移進玩家存檔後，同一層只會有一次休息室與一次商人機率。
        "tower_state": {"safe_room_visited": False, "merchant_spawned": False, "merchant_items": []},
        # 修羅鬥技場的連戰進度：round 0/1 是小怪輪，2 是冠軍戰；死亡會重置回第一輪。
        "colosseum_state": {"round": 0},
        # 技能升級系統：skill_levels 記錄各技能的強化等級（1~5），
        # skill_usage 記錄各技能累計施放次數（用於熟練度自動升級）。
        "skill_levels": {},   # {skill_id: level}，預設等級為 1（不存在 key 時視作 1）
        "skill_usage": {},    # {skill_id: cast_count}
    }


class TRPGPlayer:
    def __init__(self, user_id):
        self.id = str(user_id)
        for key, val in _SCALAR_DEFAULTS.items():
            setattr(self, key, val)
        for key, val in _fresh_containers().items():
            setattr(self, key, val)
        self.schema_version = SCHEMA_VERSION

    def remove_item(self, item_id: str, amount: int = 1) -> bool:
        current = self.inventory.get(item_id, 0)
        if current < amount:
            return False
        self.inventory[item_id] -= amount
        if self.inventory[item_id] <= 0:
            if item_id in self.inventory:
                del self.inventory[item_id]
        return True

    def add_exp(self, amount, items=None, skills_data=None):
        self.exp += amount
        needed = exp_to_next_level(self.level)
        leveled_up = False

        while self.exp >= needed:
            if self.level >= 99:
                self.level = 99
                self.exp = 0
                break
            self.exp -= needed
            self.level += 1
            needed = exp_to_next_level(self.level)
            leveled_up = True

        recalc_player_stats(self, items or {}, heal_full=leveled_up)
        if skills_data is not None:
            grant_qualified_skills(self, skills_data)
        return leveled_up

    def to_dict(self):
        return self.__dict__

    @classmethod
    def from_dict(cls, data):
        # __init__ seeds every field with its default, so any field missing from
        # an older save is already correct. We then overlay the saved values.
        player = cls(data["id"])
        container_defaults = _fresh_containers()
        for key, val in data.items():
            if key == "id":
                continue
            # Guard: a saved null/None must not clobber a container default with
            # the wrong type (older saves occasionally stored null for these).
            if key in container_defaults and not isinstance(val, type(container_defaults[key])):
                continue
            # 純量欄位若存檔是 null，但我們有實際預設值時，保留預設值（避免 None 覆蓋掉數值欄位）
            if val is None and _SCALAR_DEFAULTS.get(key) is not None:
                continue
            setattr(player, key, val)
        # Special case preserved from the old logic: if a save predates the
        # equipped-skills system, seed it from the first 8 learned skills.
        if "equipped_skills" not in data and player.skills:
            player.equipped_skills = player.skills[:8]
        # 這份存檔本來就存在（不是這次全新建立的），代表玩家早就玩過了——不管
        # 是不是在教學系統上線前建的檔，都不該讓老玩家下次登入被當新手重跑一次
        # 語言選擇＋教學戰。只有 TRPGPlayer(uid) 那條「真的第一次建檔」的路徑
        # 才會讓 onboarding_done 維持預設的 False。
        if "onboarding_done" not in data:
            player.onboarding_done = True
        if "archetype_balance_version" not in data:
            player.archetype_balance_version = 0
        if player.level > 99:
            player.level = 99
            player.exp = 0
        if getattr(player, "stat_alloc", None):
            for k in list(player.stat_alloc.keys()):
                if player.stat_alloc[k] > 99:
                    player.stat_alloc[k] = 99
        player.schema_version = SCHEMA_VERSION
        return player


def _item_shop_level_ok(player, item_id: str, item_data: dict, skills: dict) -> bool:
    if item_data.get("mystery_only"):
        return False
    req = item_data.get("exclusive_level", 0)
    if req > player.level:
        return False
    if item_data.get("type") == "skill_scroll":
        skill = skills.get(item_data.get("teaches", ""), {})
        if not meets_skill_requirements(player, skill)[0]:
            return False
    return True



class RoguePlayerWrapper:
    def __init__(self, real_player):
        self.real_player = real_player

    def _sealed(self) -> bool:
        return (getattr(self.real_player, 'current_area', '') == 'area_dungeon'
                and self.real_player.dungeon_state.get('in_run'))

    def __getattr__(self, name):
        # 地下城進行中時，封印欄位改讀 dungeon_state 裡的臨時角色數值
        if name in SEALED_FIELDS and self._sealed():
            ds = self.real_player.dungeon_state
            if name in ds:
                return ds[name]
            # 舊格式的 dungeon_state 可能缺少新欄位（例如 current_mp）：
            # 退回真實角色的值，避免回傳 None 造成崩潰
            return getattr(self.real_player, name, None)
        return getattr(self.real_player, name)

    def __setattr__(self, name, value):
        if name == 'real_player':
            super().__setattr__(name, value)
            return
        if name in SEALED_FIELDS and self._sealed():
            self.real_player.dungeon_state[name] = value
            return
        setattr(self.real_player, name, value)
