"""統一的戰鬥單位介面 — 讓玩家與怪物在戰鬥運算層級上共用同一套寫法。

Combatant 是玩家／怪物共同實作的介面：hp、mp、atk、def_、magic、spd、status_effects。
- PlayerCombatant 包裝 TRPGPlayer（或 RoguePlayerWrapper），不改動任何存檔欄位。
- MonsterCombatant 包裝 monster_slots 裡的單一 slot dict，不改動任何 JSON 資料格式。

新增狀態、怪物、技能、區域時，只要照現有資料格式寫，自動同時適用玩家與怪物，
不需要再各寫一份「玩家版」和「怪物版」的處理邏輯。
"""


from trpg.i18n import t, tf


def absorb_monster_damage(slot: dict, new_hp: int) -> int:
    """所有對怪物的扣血都要先過這一關（MonsterCombatant.hp setter 與 view.monster_hp
    setter 都會呼叫），統一結算三種防禦性頭目機制，回傳修正後的 new_hp：

    - divine_shield（聖盾，靈感來自爐石）：完全抵銷下一次受到的傷害，然後破裂。
      slot["divine_shield"] = True 開啟。
    - intangible（無實體，靈感來自殺戮尖塔）：狀態存在期間，任何一次傷害最多 1 點。
      掛在 slot["status"]["intangible"] 上，回合數照一般狀態倒數。
    - damage_cap（傷害上限，靈感來自 FF 系列的傷害閾值戰）：monster["damage_cap"]
      設定牠「兩次行動之間」最多能承受的總傷害，超過的部分直接無效；每次牠行動時
      重置額度（見 monster_ai.run_monster_ai）。

    被吸收/削減時會把原因寫進 slot["last_absorb"]，呼叫端（player_attack /
    execute_skill）可以取走並顯示給玩家，不然玩家只會看到「造成 500 傷害」但
    血條紋風不動，以為遊戲壞了。"""
    old_hp = slot.get("hp", 0)
    dmg = old_hp - new_hp
    if dmg <= 0:  # 治療或無變化不經過任何吸收
        return new_hp

    if slot.get("divine_shield"):
        slot["divine_shield"] = False
        slot["last_absorb"] = "shield"
        return old_hp

    if (slot.get("status") or {}).get("intangible"):
        if dmg > 1:
            slot["last_absorb"] = "intangible"
        dmg = min(dmg, 1)

    cap = slot.get("monster", {}).get("damage_cap")
    if cap:
        taken = slot.get("dmg_taken_since_act", 0)
        allowed = max(0, cap - taken)
        if dmg > allowed:
            slot["last_absorb"] = "cap"
        dmg = min(dmg, allowed)

    # 「兩次行動之間的承傷」對所有怪物都要記（不只 damage_cap）：復仇（avenger）、
    # 反擊架勢（counter_stance）等特性靠這個窗口判斷「你剛才打了我多少」。
    slot["dmg_taken_since_act"] = slot.get("dmg_taken_since_act", 0) + dmg

    return old_hp - dmg


class Combatant:
    """共同介面，不直接實例化。"""

    @property
    def name(self) -> str:
        raise NotImplementedError

    @property
    def hp(self) -> int:
        raise NotImplementedError

    @hp.setter
    def hp(self, value: int):
        raise NotImplementedError

    @property
    def max_hp(self) -> int:
        raise NotImplementedError

    @property
    def mp(self) -> int:
        raise NotImplementedError

    @mp.setter
    def mp(self, value: int):
        raise NotImplementedError

    @property
    def max_mp(self) -> int:
        raise NotImplementedError

    @property
    def atk(self) -> int:
        raise NotImplementedError

    @property
    def def_(self) -> int:
        raise NotImplementedError

    @property
    def mdef(self) -> int:
        raise NotImplementedError

    @property
    def magic(self) -> int:
        raise NotImplementedError

    @property
    def spd(self) -> int:
        raise NotImplementedError

    @property
    def res(self) -> int:
        raise NotImplementedError

    @property
    def luck(self) -> int:
        raise NotImplementedError

    @property
    def status_effects(self) -> dict:
        raise NotImplementedError

    @property
    def elemental_dict(self) -> dict:
        """{"weakness":[...], "resistance":[...], "immunity":[...]} 給 get_elemental_multiplier 用。"""
        raise NotImplementedError

    def is_alive(self) -> bool:
        return self.hp > 0

    def pop_absorb_note(self) -> str:
        """取走「上一次傷害被聖盾/無實體/傷害上限吸收」的原因代號（沒有就回傳空字串）。
        玩家沒有這些機制，預設空實作；MonsterCombatant 會覆寫。"""
        return ""


class PlayerCombatant(Combatant):
    def __init__(self, player, items: dict, status_defs: dict):
        self.player = player
        self.items = items
        self.status_defs = status_defs

    @property
    def lang(self) -> str:
        return getattr(self.player, "language", "zh")

    @property
    def name(self) -> str:
        return t(self.lang, "entity.player_name", "你")

    @property
    def hp(self) -> int:
        return self.player.current_hp

    @hp.setter
    def hp(self, value: int):
        self.player.current_hp = max(0, value)

    @property
    def max_hp(self) -> int:
        return self.player.max_hp

    @property
    def mp(self) -> int:
        return self.player.current_mp

    @mp.setter
    def mp(self, value: int):
        self.player.current_mp = max(0, value)

    @property
    def max_mp(self) -> int:
        return self.player.max_mp

    @property
    def atk(self) -> int:
        from trpg.combat import get_player_atk
        return get_player_atk(self.player, self.items, self.status_defs)

    @property
    def def_(self) -> int:
        from trpg.combat import get_player_def
        return get_player_def(self.player, self.items)

    @property
    def mdef(self) -> int:
        from trpg.combat import get_player_mdef
        return get_player_mdef(self.player, self.items)

    @property
    def magic(self) -> int:
        from trpg.combat import get_player_magic
        return get_player_magic(self.player, self.items, self.status_defs)

    @property
    def spd(self) -> int:
        from trpg.combat import get_player_spd
        return get_player_spd(self.player)

    @property
    def res(self) -> int:
        return getattr(self.player, "base_res", 0)

    @property
    def luck(self) -> int:
        return getattr(self.player, "base_luck", 0)

    @property
    def status_effects(self) -> dict:
        return self.player.status_effects

    @property
    def elemental_dict(self) -> dict:
        return {
            "weakness": getattr(self.player, "weakness", []),
            "resistance": getattr(self.player, "resistance", []),
            "immunity": getattr(self.player, "immunity", []),
        }

    @property
    def magic_absorb_shield(self) -> int:
        return 0


# 怪物身上沒有獨立的「魔攻」數值時，套用這個比例反推（讓舊怪物資料不用全部補欄位也能施法）
_MONSTER_MAGIC_FALLBACK_RATIO = 1.0
# 怪物身上沒有獨立的「魔防」欄位時，用物防的比例反推
_MONSTER_MDEF_FALLBACK_RATIO = 0.6


class MonsterCombatant(Combatant):
    def __init__(self, slot: dict, status_defs: dict, lang: str = "zh"):
        self.slot = slot
        self.status_defs = status_defs
        self.lang = lang

    @property
    def monster_dict(self) -> dict:
        return self.slot["monster"]

    @property
    def name(self) -> str:
        return tf(self.monster_dict, "name", self.lang) or t(self.lang, "entity.enemy_name", "敵人")

    @property
    def hp(self) -> int:
        return self.slot["hp"]

    @hp.setter
    def hp(self, value: int):
        self.slot["hp"] = max(0, absorb_monster_damage(self.slot, value))

    def pop_absorb_note(self) -> str:
        return self.slot.pop("last_absorb", "") or ""

    @property
    def max_hp(self) -> int:
        return self.monster_dict.get("max_hp", 1)

    @property
    def mp(self) -> int:
        return self.slot.get("mp", self.monster_dict.get("max_mp", 0))

    @mp.setter
    def mp(self, value: int):
        self.slot["mp"] = max(0, value)

    @property
    def max_mp(self) -> int:
        return self.monster_dict.get("max_mp", 0)

    def _status_mult(self, status_id: str, default: float) -> float:
        return self.status_defs.get(status_id, {}).get("atk_mult", default)

    @property
    def atk(self) -> int:
        atk = self.monster_dict.get("atk", 0)
        status = self.slot.get("status", {})
        if "paralysis" in status:
            atk = int(atk * self._status_mult("paralysis", 0.70))
        if "berserk" in status:
            atk = int(atk * self.status_defs.get("berserk", {}).get("atk_mult", 1.6))
        return atk

    @property
    def def_(self) -> int:
        return self.monster_dict.get("def", 0)

    @property
    def mdef(self) -> int:
        # 舊怪物資料沒有獨立的魔防欄位時，用物防的比例反推，不用全部怪物補欄位
        return self.monster_dict.get("mdef", int(self.monster_dict.get("def", 0) * _MONSTER_MDEF_FALLBACK_RATIO))

    @property
    def magic(self) -> int:
        return self.monster_dict.get("magic", int(self.monster_dict.get("atk", 0) * _MONSTER_MAGIC_FALLBACK_RATIO))

    @property
    def spd(self) -> int:
        return self.monster_dict.get("spd", 10)

    @property
    def res(self) -> int:
        return self.monster_dict.get("res", 0)

    @property
    def luck(self) -> int:
        return self.monster_dict.get("luck", 0)

    @property
    def status_effects(self) -> dict:
        return self.slot.setdefault("status", {})

    @property
    def elemental_dict(self) -> dict:
        return self.monster_dict

    @property
    def magic_absorb_shield(self) -> int:
        return self.slot.get("magic_absorb_shield", 0)
