"""TRPG 戰鬥系統 — 集中處理攻擊、技能、掉落、異常狀態與勝負判定。"""

import random

from datetime import datetime

from trpg.i18n import t, tf

from trpg.balance import XP_CURVE_BASE, XP_CURVE_EXP, PHYSICAL_CRIT_CHANCE, SKILL_CRIT_CHANCE

from trpg.status import (

    process_turn_start,

    cure_by_item,

    clear_all_status,

    apply_status_to_monster,

    apply_status,

    break_sleep_on_damage,

)

from trpg.stats import (

    get_potion_heal_target,

)

from trpg.entity import PlayerCombatant, MonsterCombatant





def exp_to_next_level(level: int) -> int:

    return int(XP_CURVE_BASE * (level ** XP_CURVE_EXP))





def get_player_atk(player, items: dict, status_defs: dict = None) -> int:
    atk = player.base_atk
    status_defs = status_defs or {}
    status_effects = getattr(player, "status_effects", None)
    if status_effects and "paralysis" in status_effects:
        atk = int(atk * status_defs.get("paralysis", {}).get("atk_mult", 0.7))
    if status_effects and "berserk" in status_effects:
        atk = int(atk * status_defs.get("berserk", {}).get("atk_mult", 1.6))
    dungeon_buffs = getattr(player, "dungeon_buffs", None)
    if dungeon_buffs and dungeon_buffs.get("atk_mult"):
        atk = int(atk * dungeon_buffs["atk_mult"])
    combat_debuffs = getattr(player, "combat_debuffs", None)
    if combat_debuffs and combat_debuffs.get("atk_mult"):
        atk = max(1, int(atk * combat_debuffs["atk_mult"]))
    combat_buffs = getattr(player, "combat_buffs", None)
    if combat_buffs and combat_buffs.get("turns", 0) > 0 and combat_buffs.get("atk_mult"):
        atk = int(atk * combat_buffs["atk_mult"])
    return atk





def get_player_def(player, items: dict) -> int:
    df = player.base_def
    dungeon_buffs = getattr(player, "dungeon_buffs", None)
    if dungeon_buffs and dungeon_buffs.get("def_mult"):
        df = int(df * dungeon_buffs["def_mult"])
    combat_debuffs = getattr(player, "combat_debuffs", None)
    if combat_debuffs and combat_debuffs.get("def_mult"):
        df = max(0, int(df * combat_debuffs["def_mult"]))
    combat_buffs = getattr(player, "combat_buffs", None)
    if combat_buffs and combat_buffs.get("turns", 0) > 0 and combat_buffs.get("def_mult"):
        df = int(df * combat_buffs["def_mult"])
    return df


def get_player_mdef(player, items: dict) -> int:
    mdf = getattr(player, "base_mdef", 0)
    dungeon_buffs = getattr(player, "dungeon_buffs", None)
    if dungeon_buffs and dungeon_buffs.get("mdef_mult"):
        mdf = int(mdf * dungeon_buffs["mdef_mult"])
    combat_debuffs = getattr(player, "combat_debuffs", None)
    if combat_debuffs and combat_debuffs.get("mdef_mult"):
        mdf = max(0, int(mdf * combat_debuffs["mdef_mult"]))
    combat_buffs = getattr(player, "combat_buffs", None)
    if combat_buffs and combat_buffs.get("turns", 0) > 0 and combat_buffs.get("mdef_mult"):
        mdf = int(mdf * combat_buffs["mdef_mult"])
    return mdf


def get_player_spd(player) -> int:
    spd = max(5, getattr(player, "base_spd", 10))
    combat_debuffs = getattr(player, "combat_debuffs", None)
    if combat_debuffs and combat_debuffs.get("spd_mult"):
        spd = max(1, int(spd * combat_debuffs["spd_mult"]))
    combat_buffs = getattr(player, "combat_buffs", None)
    if combat_buffs and combat_buffs.get("turns", 0) > 0 and combat_buffs.get("spd_mult"):
        spd = int(spd * combat_buffs["spd_mult"])
    return spd











def get_sell_price(item_id: str, items: dict) -> int:

    item = items.get(item_id, {})

    if "sell_price" in item:

        return item["sell_price"]

    return max(1, item.get("price", 0) // 2)





def calc_physical_damage(atk: int, defense: int, multiplier: float = 1.0) -> int:
    # 👇 倍率要先套用在攻擊力上，再扣防禦：(atk - def) * mult 會讓高倍率技能對付高防禦
    # 目標時完全打不動（甚至可能倍率越高、扣掉防禦後反而更接近下限傷害）。
    dmg = max(1, int(atk * multiplier - defense))

    return int(dmg * random.uniform(0.85, 1.15))

def get_player_magic(player, items: dict, status_defs: dict = None) -> int:
    mag = getattr(player, "base_int", 0)
    status_defs = status_defs or {}
    if getattr(player, "status_effects", None) and "paralysis" in player.status_effects:
        mag = int(mag * status_defs.get("paralysis", {}).get("atk_mult", 0.7))
    return mag

def apply_schrodinger(player, dmg: int, base_msg: str) -> tuple[int, str]:
    lang = getattr(player, "language", "zh")
    if getattr(player, "accessory", "") == "schrodinger_watch":
        if random.random() < 0.6:
            return dmg * 2, t(lang, "combat.schrodinger_double", "⏱️ 【薛丁格的懷錶】發動！傷害翻倍！\n{base_msg}", base_msg=base_msg)
        else:
            return max(1, dmg // 2), t(lang, "combat.schrodinger_half", "⏱️ 【薛丁格的懷錶】反噬！傷害減半！\n{base_msg}", base_msg=base_msg)
    return dmg, base_msg

def get_elemental_multiplier(attack_element: str, monster: dict, lang: str = "zh") -> tuple[float, str]:
    """判定屬性相剋，回傳 (傷害倍率, 提示訊息)"""
    if not attack_element or attack_element in ["physical", "none"]:
        return 1.0, ""

    weaknesses = monster.get("weakness", [])
    resistances = monster.get("resistance", [])
    immunities = monster.get("immunity", [])

    if attack_element in weaknesses:
        return 1.5, t(lang, "combat.elemental_weak", "🌟 【屬性克制】效果拔群！")
    elif attack_element in resistances:
        return 0.75, t(lang, "combat.elemental_resist", "🛡️ 【屬性抵抗】效果微弱...")
    elif attack_element in immunities:
        return 0.0, t(lang, "combat.elemental_immune", "👻 【屬性免疫】完全無效！")
    return 1.0, ""

def calc_magic_damage(magic: int, defense: int, base_power: int, def_pierce: float = 0.5, magic_scaling: float = 1.0) -> int:

    effective_def = int(defense * (1 - def_pierce))

    dmg = max(1, int(base_power + (magic * magic_scaling) - effective_def))

    return int(dmg * random.uniform(0.90, 1.20))





def calc_monster_damage(monster_atk: int, player_def: int) -> int:

    dmg = max(1, monster_atk - player_def)

    return int(dmg * random.uniform(0.9, 1.1))


# 技能的傷害輸出可以改吃別的數值，不是永遠都吃 atk——這樣 VIT 流（高防高血）
# 也能有自己的輸出手段：skill.json 設 "power_stat":"def" 就會改用防禦力計算傷害。
_POWER_STAT_GETTERS = {
    "atk": lambda c: c.atk,
    "def": lambda c: c.def_,
    "mdef": lambda c: c.mdef,
    "hp": lambda c: c.hp,
    "spd": lambda c: c.spd,
}


def execute_skill(caster, targets: list, skill: dict, status_defs: dict, hp_cost: int = 0) -> tuple[str, int]:
    """對 targets 套用 skill 的傷害／治療與異常狀態，回傳 (戰鬥紀錄文字, 造成的總傷害)。

    caster／targets 都是 Combatant（PlayerCombatant 或 MonsterCombatant），玩家用技能、
    怪物用 active_skills 的 cast_skill 效果，都共用這一條路徑——新增怪物想放任何
    skills.json 裡的技能，不需要再寫第二份傷害計算。
    hp_cost 是這次施放實際被扣掉的 HP（給 hp_scaling_multiplier 這種獻祭流技能用，沒有的話傳 0）。
    """
    skill_type = skill.get("type", "physical")
    skill_elem = skill.get("element", "magic")
    if skill.get("element_source") == "armor":
        # 像「盾擊」這種招式：屬性不是寫死在技能上，而是看你裝備的盾牌（防具欄）決定，
        # 換一面盾就能換屬性，不用為每種屬性各出一招技能。
        player_obj = getattr(caster, "player", None)
        items_dict = getattr(caster, "items", None)
        armor_id = getattr(player_obj, "armor", None) if player_obj is not None else None
        armor_item = (items_dict or {}).get(armor_id, {}) if armor_id else {}
        skill_elem = armor_item.get("element", "physical")
    hits = skill.get("hits", 1)
    multiplier = skill.get("power_multiplier", 1.0)
    base_power = skill.get("base_power", 0)
    magic_scaling = skill.get("magic_scaling", 1.0)
    def_pierce = skill.get("def_pierce", 0.5)
    crit_bonus = skill.get("crit_bonus", 0)
    hp_scaling_mult = skill.get("hp_scaling_multiplier")
    debuff_target = skill.get("debuff_target")
    power_stat = skill.get("power_stat", "atk")
    atk = _POWER_STAT_GETTERS.get(power_stat, _POWER_STAT_GETTERS["atk"])(caster)
    magic = caster.magic

    total_dmg = 0
    target_blocks = []

    for target in targets:
        debuff_msg = ""
        # 「先削弱、再攻擊」的物理技能：debuff_target 在算傷害前就把目標的對應數值降低，
        # 這一擊本身就會吃到變弱後的數值（不用另外補一顆技能才能達成削防再打）。
        if debuff_target and hasattr(target, "slot"):
            from trpg.monster_ai import _apply_monster_stat_mod
            stat = debuff_target.get("stat", "def")
            mult = debuff_target.get("mult", 0.75)
            turns = debuff_target.get("turns", 2)
            _apply_monster_stat_mod(target.slot, stat, mult, turns)
            stat_name = {"def": t(target.lang, "monster_ai.stat_def", "防禦力"), "atk": t(target.lang, "monster_ai.stat_atk", "攻擊力"), "spd": t(target.lang, "monster_ai.stat_spd", "速度")}.get(stat, stat)
            debuff_msg = t(target.lang, "combat.debuff_target_applied", "🔻 {target} 的{stat}被削弱了！（{turns}回合）\n", target=target.name, stat=stat_name, turns=turns)

        if target.magic_absorb_shield > 0 and skill_type == "magic":
            dmg = calc_magic_damage(magic, target.mdef, base_power, def_pierce, magic_scaling)
            heal = max(1, int(dmg * hits))
            target.hp = min(target.max_hp, target.hp + heal)
            target_blocks.append(t(target.lang, "combat.shield_absorb_magic", "🌀 {target} 的護盾吸收了魔法！回復了 {heal} HP！", target=target.name, heal=heal))
            continue

        ele_mult, ele_msg = get_elemental_multiplier(skill_elem, target.elemental_dict, target.lang)
        t_dmg = 0
        hit_logs = []

        for i in range(hits):
            if hp_scaling_mult:
                dmg = max(1, int(hp_cost * hp_scaling_mult * ele_mult))
            elif skill_type == "magic":
                dmg = calc_magic_damage(magic, target.mdef, base_power, def_pierce, magic_scaling)
                dmg = max(1, int(dmg * ele_mult))
            else:
                dmg = calc_physical_damage(atk, target.def_, multiplier)
                dmg = max(1, int(dmg * ele_mult))

            # 物理跟 HP 獻祭流都可以暴擊，魔法傷害不會
            if skill_type != "magic" and random.random() < (SKILL_CRIT_CHANCE + crit_bonus):
                dmg = int(dmg * 1.5)
                hit_logs.append(t(target.lang, "combat.skill_hit_crit", "  第{n}擊暴擊 {dmg} 點！", n=i + 1, dmg=dmg))
            else:
                hit_logs.append(t(target.lang, "combat.skill_hit_normal", "  第{n}擊 {dmg} 點", n=i + 1, dmg=dmg))
            t_dmg += dmg

        target.hp -= t_dmg
        total_dmg += t_dmg

        block = debuff_msg + t(target.lang, "combat.deal_damage", "對 {target} 造成 {dmg} 點傷害！", target=target.name, dmg=t_dmg)
        if ele_msg: block += f"\n   ↳ {ele_msg}"
        if hits > 1: block += "\n" + "\n".join(hit_logs)

        wake_log = break_sleep_on_damage(target.status_effects, target.lang)
        if wake_log:
            block += f"\n{wake_log}"

        apply_status_id = skill.get("apply_status")
        if apply_status_id:
            skill_name = tf(skill, "name", target.lang)
            s_log = apply_status(target, apply_status_id, skill.get("status_turns", 2), status_defs, f"【{skill_name}】")
            if s_log:
                block += f"\n{s_log}"

        target_blocks.append(block)

    skill_lang = targets[0].lang if targets else caster.lang
    skill_name = tf(skill, "name", skill_lang)
    if len(targets) == 1:
        return t(skill_lang, "combat.skill_hit_single", "✨ 【{skill}】{block}", skill=skill_name, block=target_blocks[0]), total_dmg
    return t(skill_lang, "combat.skill_hit_multi", "✨ 【{skill}】命中了 {count} 個目標！\n{blocks}", skill=skill_name, count=len(targets), blocks="\n".join(target_blocks)), total_dmg


class TRPGCombat:

    """綁定 TRPGGameView，處理所有戰鬥回合邏輯。"""



    def __init__(self, view):

        self.view = view
        self.skill_cds = {}
        self.player_av = 100
        self.is_defending = False
        self.is_dodging = False



    def _clear_battle_state(self):
        self.skill_cds.clear()
        self.player_av = 100
        self.is_defending = False
        self.is_dodging = False
        self.player.combat_debuffs = {}
        self.player.combat_buffs = {}

    def _all_monsters_dead(self) -> bool:
        for slot in self.view.monster_slots:
            if slot["hp"] > 0:
                return False
            if slot.get("fled"):
                continue
            revive_cfg = slot["monster"].get("revive_once")
            if revive_cfg and not slot.get("revived"):
                return False
        return True



    @property

    def player(self):

        return self.view.player



    @property

    def cog(self):

        return self.view.cog



    @property

    def monster(self):

        return self.view.active_monster



    @property

    def monster_hp(self):

        return self.view.monster_hp



    @monster_hp.setter

    def monster_hp(self, value):

        self.view.monster_hp = value

    @property
    def monster_status(self):
        """前排目標的異常狀態字典 — 普攻/武器附帶效果永遠只影響前排。"""
        front = self.view._front_slot()
        return front["status"] if front else {}



    def _player_turn_start(self) -> tuple[str, bool]:
        # 輪到玩家時，重置上一回合的防禦與閃避
        self.is_defending = False
        self.is_dodging = False
        
        for sid in list(self.skill_cds.keys()):
            if self.skill_cds[sid] > 0: self.skill_cds[sid] -= 1
            if self.skill_cds[sid] <= 0: del self.skill_cds[sid]

        debuffs = getattr(self.player, "combat_debuffs", None)
        if debuffs and debuffs.get("turns", 0) > 0:
            debuffs["turns"] -= 1
            if debuffs["turns"] <= 0:
                self.player.combat_debuffs = {}

        buff_log = self._tick_combat_buffs()

        # 地下城：遺物/裝備的每回合自動回血
        eff = getattr(self.player, "dungeon_relic_effects", None) or {}
        if eff.get("regen_pct", 0) > 0 and self.player.current_hp > 0:
            heal = max(1, int(self.player.max_hp * eff["regen_pct"]))
            before = self.player.current_hp
            self.player.current_hp = min(self.player.max_hp, self.player.current_hp + heal)
            if self.player.current_hp - before > 0:
                regen_line = t(self.player.language, "combat.dungeon_regen", "🌿 遺物回復了 {heal} HP。", heal=self.player.current_hp - before)
                buff_log = f"{buff_log}\n{regen_line}" if buff_log else regen_line

        log, can_act = process_turn_start(self.player, self.cog.status_effects)
        if buff_log:
            log = f"{buff_log}\n{log}" if log else buff_log
        if self.player.current_hp <= 0:
            log = self.view.process_death(log, t(self.player.language, "combat.death_by_status", "💀 異常狀態將你折磨至死..."))
            return log, False
        return log, can_act

    def _tick_combat_buffs(self) -> str:
        """結算玩家自身的戰鬥增益：持續治癒（HoT）回血，並倒數攻防速強化的回合數。"""
        buffs = getattr(self.player, "combat_buffs", None)
        if not isinstance(buffs, dict) or not buffs:
            return ""
        lang = self.player.language
        parts = []
        # 持續治癒（HoT）
        if buffs.get("regen_turns", 0) > 0:
            heal = int(self.player.max_hp * buffs.get("regen_pct", 0)) + buffs.get("regen_amount", 0)
            if heal > 0 and self.player.current_hp > 0:
                before = self.player.current_hp
                self.player.current_hp = min(self.player.max_hp, self.player.current_hp + heal)
                gained = self.player.current_hp - before
                if gained > 0:
                    parts.append(t(lang, "combat.regen_tick", "🌿 持續治癒回復了 {heal} 點 HP。", heal=gained))
            buffs["regen_turns"] -= 1
            if buffs["regen_turns"] <= 0:
                for k in ("regen_pct", "regen_amount", "regen_turns"):
                    buffs.pop(k, None)
        # 攻防速強化倒數
        if buffs.get("turns", 0) > 0:
            buffs["turns"] -= 1
            if buffs["turns"] <= 0:
                for k in ("atk_mult", "def_mult", "spd_mult", "turns"):
                    buffs.pop(k, None)
                parts.append(t(lang, "combat.buff_expired", "💨 你的強化效果消退了。"))
        return "\n".join(parts)
    
    def _slow_factor(self, status_dict) -> float:
        """冰凍狀態會降低該單位在行動條上的速度。"""
        if status_dict and status_dict.get("freeze"):
            return self.cog.status_effects.get("freeze", {}).get("spd_mult", 0.5)
        return 1.0

    def _effective_player_spd(self) -> int:
        return max(1, int(get_player_spd(self.player) * self._slow_factor(getattr(self.player, "status_effects", {}))))

    def _effective_monster_spd(self, slot: dict) -> int:
        m_base = 15 if slot["monster"].get("is_boss") else 10
        m_spd = max(5, slot["monster"].get("spd", int(m_base + self.player.level * 2.2)))
        return max(1, int(m_spd * self._slow_factor(slot.get("status"))))

    def predict_monster_actions(self, slot: dict) -> int:
        """預估玩家下一次行動後，這隻怪物會行動幾次（給行動條 ❗ 提示用，冰凍已納入計算）。"""
        import math
        if slot["hp"] <= 0:
            return 0
        p_spd = self._effective_player_spd()
        m_spd = self._effective_monster_spd(slot)
        start = self.player_av - 100
        iters = max(0, math.ceil((100 - start) / p_spd)) if start < 100 else 0
        total_av = slot.get("av", 0) + m_spd * iters
        return max(0, int(total_av // 100))

    def advance_time(self, log: str) -> str:
        """推進時間條：玩家 AV 滿 100 前，場上每隻活著的怪物各自依自己的速度累積 AV 並行動。"""
        self.player_av -= 100
        p_spd = self._effective_player_spd()

        while self.player_av < 100:
            self.player_av += p_spd

            for slot in list(self.view.monster_slots):
                if slot["hp"] <= 0:
                    from trpg.monster_ai import tick_revive
                    log += tick_revive(slot, self.player.language)
                    continue
                m_spd = self._effective_monster_spd(slot)
                slot["av"] += m_spd

                while slot["av"] >= 100:
                    slot["av"] -= 100
                    if slot["hp"] > 0 and self.player.current_hp > 0:
                        log = self._monster_act_slot(slot, log)
                    if self.player.current_hp <= 0:
                        return log
                    if self._all_monsters_dead():
                        return log + self._process_victory()

        # 場上沒有活著的怪物、卻還有等待復活的怪物時，立即快轉完成復活，
        # 避免玩家面對「敵人全倒、戰鬥卻沒結束也沒復活」的空回合（高速度時尤其明顯）
        return self._resolve_pending_revives(log)

    def _any_monster_alive(self) -> bool:
        return any(s["hp"] > 0 for s in self.view.monster_slots)

    def _resolve_pending_revives(self, log: str) -> str:
        from trpg.monster_ai import tick_revive
        guard = 0
        while not self._any_monster_alive() and guard < 30:
            pending = [
                s for s in self.view.monster_slots
                if s["hp"] <= 0 and not s.get("fled")
                and s["monster"].get("revive_once") and not s.get("revived")
            ]
            if not pending:
                break
            guard += 1
            for slot in pending:
                log += tick_revive(slot, self.player.language)
        return log

    def _monster_act_slot(self, slot: dict, log: str) -> str:
        from trpg.status import process_monster_status
        from trpg.monster_ai import run_monster_ai
        status_log, m_can_act = process_monster_status(slot, self.cog.status_effects, self.player.language)
        if status_log: log += f"\n{status_log}"
        if slot["hp"] <= 0 or not m_can_act: return log

        return run_monster_ai(self, slot, log)

    def defend(self) -> str:
        dot_log, can_act = self._player_turn_start()
        if self.player.current_hp <= 0: return dot_log
        log = f"{dot_log}\n" if dot_log else ""
        if not can_act: return self.advance_time(log)
        
        self.is_defending = True
        log += t(self.player.language, "combat.defend_stance", "🛡️ 你舉起武器採取防禦姿態，準備迎接衝擊！")
        return self.advance_time(log)

    def dodge(self) -> str:
        dot_log, can_act = self._player_turn_start()
        if self.player.current_hp <= 0: return dot_log
        log = f"{dot_log}\n" if dot_log else ""
        if not can_act: return self.advance_time(log)

        self.is_dodging = True
        log += t(self.player.language, "combat.dodge_stance", "💨 你全神貫注地盯著敵人，準備進行閃避！")
        return self.advance_time(log)
    
    def player_attack(self) -> str:
        dot_log, can_act = self._player_turn_start()
        if self.player.current_hp <= 0: return dot_log
        log = f"{dot_log}\n" if dot_log else ""

        if not can_act:
            self.cog.save_players()
            return self.advance_time(log) # 👈 被麻痺就直接過回合

        # 前排目前沒有活著的目標（例如 BOSS 即將復活的空檔）：推進時間讓復活／小怪行動，避免攻擊到 None
        if self.monster is None:
            return self.advance_time(log)

        # 取出武器的速度加成
        weapon = self.cog.items.get(self.player.weapon, {})

        attack_elem = weapon.get("element", "physical")
        ele_mult, ele_msg = get_elemental_multiplier(attack_elem, self.monster, self.player.language)
        
        spd_scale = weapon.get("spd_scaling", 0.0)
        p_spd = get_player_spd(self.player)
        multiplier = 1.0 + p_spd * spd_scale
        
        lang = self.player.language
        eff = getattr(self.player, "dungeon_relic_effects", None) or {}

        # 傳遞屬性與速度倍率給攻擊計算（地下城遺物/裝備可加暴擊率）
        base_dmg, base_msg = self._do_physical_hit(multiplier=multiplier, crit_bonus=eff.get("crit_bonus", 0.0), ele_mult=ele_mult)
        p_dmg, hit_msg = apply_schrodinger(self.player, base_dmg, base_msg)

        monster_name = tf(self.monster, "name", lang)
        log += t(lang, "combat.player_attacks", "⚔️ 你攻擊了 {monster}，{hit_msg}", monster=monster_name, hit_msg=hit_msg)
        self.monster_hp -= p_dmg

        # 地下城：吸血
        if eff.get("lifesteal", 0) > 0 and p_dmg > 0:
            heal = max(1, int(p_dmg * eff["lifesteal"]))
            before = self.player.current_hp
            self.player.current_hp = min(self.player.max_hp, self.player.current_hp + heal)
            if self.player.current_hp - before > 0:
                log += "\n" + t(lang, "combat.lifesteal", "🩸 吸血回復了 {heal} HP！", heal=self.player.current_hp - before)

        # 地下城：腐蝕附加
        if eff.get("corrosion_on_hit", 0) > 0 and self.monster_status is not None:
            from trpg.dungeon import corrosion_params
            from trpg.status import apply_corrosion
            stacks, per = corrosion_params(self.player.dungeon_state, eff)
            if stacks > 0:
                total = apply_corrosion(self.monster_status, stacks, per)
                log += "\n" + t(lang, "combat.corrosion_apply", "🧪 附加了腐蝕，目前共 {stacks} 層！", stacks=total)

        wake_log = break_sleep_on_damage(self.monster_status, lang)
        if wake_log:
            log += f"\n{wake_log}"
        log = self._apply_weapon_on_hit(log)

        if self._all_monsters_dead():
            return log + self._process_victory()

        return self.advance_time(log) # 👈 行動結束，交給時間流逝

    def attempt_flee(self) -> str:
        dot_log, can_act = self._player_turn_start()
        if self.player.current_hp <= 0: return dot_log
        log = f"{dot_log}\n" if dot_log else ""

        if not can_act:
            self.cog.save_players()
            return self.advance_time(log + "\n" + t(self.player.language, "combat.flee_paralyzed", "💨 你試圖逃跑，但身體不聽使喚！"))

        p_spd = get_player_spd(self.player)
        m_spd = self.monster.get("spd", int(10 + self.player.level * 2.2)) if self.monster else 10
        flee_chance = min(0.95, max(0.2, 0.4 + (p_spd - m_spd) * 0.015))

        if random.random() < flee_chance:
            from trpg.status import clear_all_status
            clear_all_status(self.player)
            self._clear_battle_state()
            if self.monster.get("is_dungeon", False):
                self.player.dungeon_state["in_run"] = False
                self.player.dungeon_state["choices"] = []
                self.player.dungeon_state["floor"] = 1
                self.player.current_area = "area_00village"
                self.cog.save_players()
                self.view.build_main_menu()
                return log + "\n" + t(self.player.language, "combat.flee_success_dungeon", "🏃 你成功逃跑了！但地下城危機四伏，你只能一路逃回村莊。")

            self.view.build_main_menu()
            return log + "\n" + t(self.player.language, "combat.flee_success", "🏃 你化作一陣風，成功甩開了怪物逃回村里。")

        return self.advance_time(log + "\n" + t(self.player.language, "combat.flee_fail", "💨 逃跑失敗！你的速度不夠快，被攔截了！"))
    

    def _apply_weapon_on_hit(self, log: str) -> str:
        weapon_id = self.player.weapon
        if not weapon_id:
            return log
        weapon = self.cog.items.get(weapon_id, {})
        if weapon.get("on_hit_status") and random.random() < weapon.get("on_hit_chance", 0.25):
            turns = weapon.get("on_hit_status_turns", 2)
            weapon_name = tf(weapon, "name", self.player.language) or t(self.player.language, "combat.generic_weapon", "武器")
            s_log = apply_status_to_monster(
                self.monster_status,
                weapon["on_hit_status"],
                turns,
                self.cog.status_effects,
                f"【{weapon_name}】",
                lang=self.player.language,
            )
            if s_log:
                log += f"\n{s_log}"
        heal_pct = weapon.get("on_hit_heal_percent", 0)
        if heal_pct > 0:
            heal = max(1, int(self.player.max_hp * heal_pct))
            before = self.player.current_hp
            self.player.current_hp = min(self.player.max_hp, self.player.current_hp + heal)
            actual = self.player.current_hp - before
            if actual > 0:
                log += "\n" + t(self.player.language, "combat.weapon_lifesteal", "✨ 聖光回湧，回復 {heal} HP！", heal=actual)
        return log

    def _do_physical_hit(self, multiplier: float = 1.0, crit_bonus: float = 0.0, ele_mult: float = 1.0) -> tuple[int, str]:
        p_atk = get_player_atk(self.player, self.cog.items, self.cog.status_effects)
        p_dmg = calc_physical_damage(p_atk, self.monster["def"], multiplier)

        # 👇 套用屬性倍率
        p_dmg = max(1, int(p_dmg * ele_mult))

        crit_rate = PHYSICAL_CRIT_CHANCE + crit_bonus
        if random.random() < crit_rate:
            p_dmg = int(p_dmg * 1.6)
            return p_dmg, t(self.player.language, "combat.player_crit_hit", "💥 暴擊！造成 {dmg} 點傷害！", dmg=p_dmg)
        return p_dmg, t(self.player.language, "combat.player_normal_hit", "⚔️ 造成 {dmg} 點傷害。", dmg=p_dmg)

    def _resolve_skill_targets(self, target_type: str) -> list:
        """依技能的 target_type 從活著的怪物欄位中選出目標：front=最前排、back=非前排的那一個、all=全部。"""
        alive = [s for s in self.view.monster_slots if s["hp"] > 0]
        if not alive:
            return []
        if target_type == "all":
            return alive
        if target_type == "back":
            return [alive[1]] if len(alive) > 1 else alive[:1]
        return alive[:1]  # "front"（預設）：永遠只打最前面那一個

    def use_skill(self, skill_id: str) -> str:
        lang = self.player.language
        skill = self.cog.skills.get(skill_id)
        if not skill: return t(lang, "combat.skill_unknown", "❌ 未知的技能。")
        if skill.get("type") == "passive": return t(lang, "combat.skill_passive_only", "❌ 被動技能無法主動施放。")
        if skill_id not in getattr(self.player, "equipped_skills", []): return t(lang, "combat.skill_not_equipped", "❌ 技能未裝備，無法使用。")

        skill_name = tf(skill, "name", lang)

        req_lv = skill.get("req_level", 1)
        if self.player.level < req_lv:
            return t(lang, "combat.skill_level_too_low", "❌ 需要 Lv.{lv} 才能使用【{skill}】。", lv=req_lv, skill=skill_name)

        if self.skill_cds.get(skill_id, 0) > 0:
            return t(lang, "combat.skill_on_cooldown", "⏳ 【{skill}】冷卻中！（剩餘 {turns} 回合）", skill=skill_name, turns=self.skill_cds[skill_id])

        # 👇 資源檢查必須在 _player_turn_start() 之前：否則 MP/HP 不足而施放失敗時，
        # 回合根本沒有真的發生（怪物不會行動），卻已經先扣了增益/冷卻的剩餘回合數。
        mp_cost = skill.get("mp_cost", 0)
        hp_cost_pct = skill.get("hp_cost_percent", 0.0)
        actual_hp_cost = int(self.player.max_hp * hp_cost_pct)

        if mp_cost > 0 and self.player.current_mp < mp_cost:
            return t(lang, "combat.skill_mp_insufficient", "❌ MP 不足！需要 {cost} 點，目前只有 {have} 點。", cost=mp_cost, have=self.player.current_mp)
        if actual_hp_cost > 0:
            if self.player.current_hp <= actual_hp_cost:
                return t(lang, "combat.skill_hp_insufficient", "❌ HP 不足！【{skill}】需要獻祭 {cost} 點生命，你會把自己抽乾的！", skill=skill_name, cost=actual_hp_cost)

        dot_log, can_act = self._player_turn_start()
        if self.player.current_hp <= 0: return dot_log
        log = f"{dot_log}\n" if dot_log else ""

        if not can_act:
            self.cog.save_players()
            return self.advance_time(log) # 👈 修正：拔掉 _monster_counter

        if skill.get("cd", 0) > 0: self.skill_cds[skill_id] = skill["cd"]

        skill_type = skill.get("type", "physical")

        if mp_cost > 0: self.player.current_mp -= mp_cost
        if actual_hp_cost > 0:
            self.player.current_hp -= actual_hp_cost
            log += t(lang, "combat.skill_hp_sacrifice", "🩸 你殘忍地獻祭了自己 {cost} 點生命值！\n", cost=actual_hp_cost)

        if skill_type == "support":
            log += self._apply_support_skill(skill, skill_id, skill_name, lang)
        elif skill_type == "flee":
            flee_chance = skill.get("flee_chance", 0.85)
            if random.random() < flee_chance:
                from trpg.status import clear_all_status
                clear_all_status(self.player)
                self._clear_battle_state()
                self.view.build_main_menu()
                return log + t(lang, "combat.skill_flee_success", "💨 使用了【{skill}】，化作一團黑影成功脫離戰鬥！", skill=skill_name)
            else:
                monster_name = tf(self.monster, "name", lang)
                return log + t(lang, "combat.skill_flee_fail", "💦 嘗試使用逃跑，卻被 {monster} 識破！", monster=monster_name) + self.advance_time("") # 👈 修正

        else:
            # 👇 target_type 決定打誰：front=只打前排／back=打後排／all=打全體，預設 front
            target_type = skill.get("target_type", "front")
            target_slots = self._resolve_skill_targets(target_type)

            if not target_slots:
                log += t(lang, "combat.skill_no_target", "❌ 沒有可以攻擊的目標。")
            else:
                caster = PlayerCombatant(self.player, self.cog.items, self.cog.status_effects)
                targets = [MonsterCombatant(slot, self.cog.status_effects, self.player.language) for slot in target_slots]
                skill_log, _ = execute_skill(caster, targets, skill, self.cog.status_effects, hp_cost=actual_hp_cost)
                log += skill_log

        if self._all_monsters_dead():
            return log + self._process_victory()

        return self.advance_time(log) # 👈 修正：統一交給時間條推進



    def _apply_support_skill(self, skill: dict, skill_id: str, skill_name: str, lang: str) -> str:
        """資料驅動的輔助技能：治癒 / 回魔 / 攻防速強化 / 持續治癒(HoT) / 淨化。
        在 skills.json 加上對應欄位即可組合多種效果，不需改程式。"""
        p = self.player
        parts = []

        # 立即治癒：heal_percent（最大HP比例）+ heal_amount（固定值）
        heal = 0
        if skill.get("heal_percent"):
            heal += int(p.max_hp * skill["heal_percent"])
        if skill.get("heal_amount"):
            heal += skill["heal_amount"]
        # 相容舊資料：heal_light 沒帶數值時用原本的預設公式
        if heal == 0 and skill_id == "heal_light":
            heal = int(p.max_hp * 0.20 + 50)
        if heal > 0:
            before = p.current_hp
            p.current_hp = min(p.max_hp, p.current_hp + heal)
            parts.append(t(lang, "combat.skill_heal", "✨ 【{skill}】回復了 {heal} 點 HP！", skill=skill_name, heal=p.current_hp - before))

        # 回魔
        if skill.get("mp_restore"):
            before = p.current_mp
            p.current_mp = min(p.max_mp, p.current_mp + skill["mp_restore"])
            parts.append(t(lang, "combat.skill_mp_restore", "🔷 【{skill}】回復了 {mp} 點 MP！", skill=skill_name, mp=p.current_mp - before))

        if not isinstance(getattr(p, "combat_buffs", None), dict):
            p.combat_buffs = {}

        # 攻防速強化（buff）：{"atk_mult":..,"def_mult":..,"spd_mult":..,"turns":..}
        buff = skill.get("buff")
        if buff:
            for k in ("atk_mult", "def_mult", "spd_mult"):
                if buff.get(k):
                    p.combat_buffs[k] = buff[k]
            p.combat_buffs["turns"] = max(p.combat_buffs.get("turns", 0), buff.get("turns", 3))
            parts.append(t(lang, "combat.skill_buff", "💪 【{skill}】強化了你的戰鬥能力！（{turns}回合）", skill=skill_name, turns=buff.get("turns", 3)))

        # 持續治癒（HoT）：{"pct":..,"amount":..,"turns":..}
        regen = skill.get("regen")
        if regen:
            p.combat_buffs["regen_pct"] = regen.get("pct", 0)
            p.combat_buffs["regen_amount"] = regen.get("amount", 0)
            p.combat_buffs["regen_turns"] = regen.get("turns", 3)
            parts.append(t(lang, "combat.skill_regen", "🌿 【{skill}】賦予了你持續治癒之力！（{turns}回合）", skill=skill_name, turns=regen.get("turns", 3)))

        # 淨化：清除身上的異常狀態與戰鬥減益
        if skill.get("cleanse"):
            p.status_effects.clear()
            p.combat_debuffs = {}
            parts.append(t(lang, "combat.skill_cleanse", "🧼 【{skill}】淨化了你身上的異常狀態與減益！", skill=skill_name))

        if not parts:
            parts.append(t(lang, "combat.skill_cast_generic", "✨ 你施放了【{skill}】。", skill=skill_name))
        return "\n".join(parts)

    def use_potion(self, item_id: str) -> str:
        lang = self.player.language
        if self.player.inventory.get(item_id, 0) <= 0:
            return t(lang, "combat.potion_not_owned", "❌ 你包包裡沒有這個藥水了！")

        item_data = self.cog.items.get(item_id, {})
        heal_target = get_potion_heal_target(item_data, item_id)

        # 👇 同樣的道理：喝藥水前先檢查滿血/滿魔，失敗就不算一個回合，不要先扣增益/冷卻。
        if heal_target == "mp" and self.player.current_mp >= self.player.max_mp:
            return t(lang, "combat.potion_mp_full", "❓ 魔力已經滿了，別浪費藥水。")
        if heal_target == "hp" and self.player.current_hp >= self.player.max_hp:
            return t(lang, "combat.potion_hp_full", "❓ 生命值已經滿了，別浪費藥水。")

        dot_log, can_act = self._player_turn_start()
        if self.player.current_hp <= 0: return dot_log
        log = f"{dot_log}\n" if dot_log else ""

        self.player.inventory[item_id] -= 1
        if self.player.inventory[item_id] <= 0: del self.player.inventory[item_id]

        if "heal_percent" in item_data:
            heal = int(getattr(self.player, f"max_{heal_target}") * item_data["heal_percent"])
        else:
            heal = item_data.get("heal", 50)

        setattr(self.player, f"current_{heal_target}", min(getattr(self.player, f"max_{heal_target}"), getattr(self.player, f"current_{heal_target}") + heal))
        resource_name = t(lang, "combat.resource_mp", "魔力") if heal_target == "mp" else t(lang, "combat.resource_hp", "生命值")
        log += t(lang, "combat.potion_drink", "🧪 你喝下藥水，回復了 {heal} 點{resource}。", heal=heal, resource=resource_name)

        if not can_act: log += "\n" + t(lang, "combat.potion_still_paralyzed", "（麻痺/冰凍中，無法閃避反擊！）")
        return self.advance_time(log) # 👈 修正

    def use_cure_item(self, item_id: str) -> str:
        lang = self.player.language
        if self.player.inventory.get(item_id, 0) <= 0: return t(lang, "combat.cure_item_not_owned", "❌ 背包裡沒有這個物品。")
        dot_log, can_act = self._player_turn_start()
        if self.player.current_hp <= 0: return dot_log
        log = f"{dot_log}\n" if dot_log else ""

        self.player.inventory[item_id] -= 1
        if self.player.inventory[item_id] <= 0: del self.player.inventory[item_id]

        log += cure_by_item(self.player, item_id, self.cog.items, self.cog.status_effects)
        if not can_act: log += "\n" + t(lang, "combat.cure_item_still_affected", "（本回合仍受異常影響，但已解除狀態。）")
        return self.advance_time(log) # 👈 修正

    def use_buff_item(self, item_id: str) -> str:
        """給時光沙漏這類「用掉就給自己一段時間強化」的道具用，效果格式跟技能的
        support "buff" 欄位共用（atk_mult/def_mult/spd_mult + turns）。"""
        lang = self.player.language
        if self.player.inventory.get(item_id, 0) <= 0:
            return t(lang, "combat.buff_item_not_owned", "❌ 你包包裡沒有這個道具了！")

        item_data = self.cog.items.get(item_id, {})
        buff = item_data.get("buff")
        if not buff:
            return t(lang, "combat.buff_item_no_effect", "❌ 這個道具沒有可用的強化效果。")

        dot_log, can_act = self._player_turn_start()
        if self.player.current_hp <= 0: return dot_log
        log = f"{dot_log}\n" if dot_log else ""

        self.player.inventory[item_id] -= 1
        if self.player.inventory[item_id] <= 0: del self.player.inventory[item_id]

        if not isinstance(getattr(self.player, "combat_buffs", None), dict):
            self.player.combat_buffs = {}
        for k in ("atk_mult", "def_mult", "spd_mult"):
            if buff.get(k):
                self.player.combat_buffs[k] = buff[k]
        turns = buff.get("turns", 3)
        self.player.combat_buffs["turns"] = max(self.player.combat_buffs.get("turns", 0), turns)

        item_name = tf(item_data, "name", lang) or item_id
        log += t(lang, "combat.buff_item_used", "⏳ 你使用了【{item_name}】，感覺自己的動作變得飛快！（{turns}回合）", item_name=item_name, turns=turns)

        if not can_act: log += "\n" + t(lang, "combat.potion_still_paralyzed", "（麻痺/冰凍中，無法閃避反擊！）")
        return self.advance_time(log)

    def _gold_and_exp_for(self, monster: dict) -> tuple[int, int]:
        min_gold = monster.get("money_min", 0)
        max_gold = monster.get("money_max", 0)
        if max_gold < min_gold:
            max_gold = min_gold
        return random.randint(min_gold, max_gold), monster.get("exp", 0)

    def _roll_drops_for(self, monster: dict) -> str:
        lang = self.player.language
        drop_log = ""
        for item_id, rate in monster.get("drops", {}).items():
            if random.random() < rate:
                self.player.inventory[item_id] = self.player.inventory.get(item_id, 0) + 1
                item_name = tf(self.cog.items.get(item_id, {}), "name", lang) or item_id
                drop_log += t(lang, "combat.drop_obtained", "🎁 幸運獲得掉落物：{item}\n", item=item_name)
        return drop_log

    def _handle_boss_kill_rewards(self, monster: dict) -> str:
        lang = self.player.language
        if not monster.get("is_boss"):
            return ""
        # 魔塔/地下城的 BOSS 樓層只是難度層，獎勵已經由各自的里程碑/樓層推進邏輯處理，
        # 不套用「區域 BOSS 每日鎖定 + 卷軸首殺」這套只為野外區域 BOSS 設計的獎勵。
        if monster.get("is_tower") or monster.get("is_dungeon"):
            return ""

        today_str = datetime.today().strftime("%Y-%m-%d")
        self.player.daily_boss_kills[self.player.current_area] = today_str
        log = t(lang, "combat.boss_defeated", "👑 區域 BOSS 討伐成功！今日已無法再次挑戰。\n")

        # Guaranteed scroll drop logic (100% first kill, 30% daily)
        boss_id = monster.get("id")
        scroll_pool = [item_id for item_id in monster.get("drops", {}).keys() if "scroll" in item_id]
        if not scroll_pool:
            FALLBACK_BOSS_SCROLLS = {
                "goblin_chief": ["scroll_heal_light", "scroll_power_slash", "scroll_fireball"],
                "forest_guardian": ["scroll_shadow_step", "scroll_combo_attack"],
                "bee_queen": ["scroll_blood_strike", "scroll_double_strike", "ice_spear_scroll"],
                "mad_doctor": ["scroll_inferno", "scroll_divine_thunder", "scroll_blizzard"],
                "lich": ["scroll_divine_thunder", "scroll_blizzard"],
                "vampire_lord": ["scroll_divine_thunder", "scroll_inferno"],
                "rat_king": ["scroll_venom_cloud"],
                "bone_knight": ["scroll_frost_nova"],
                "orc_warlord": ["scroll_earthquake"],
                "abyss_overlord": ["scroll_meteor_swarm", "scroll_void_eruption"],
                "sargeras": ["scroll_meteor_swarm", "scroll_holy_smash", "scroll_divine_thunder"]
            }
            scroll_pool = FALLBACK_BOSS_SCROLLS.get(boss_id, ["scroll_heal_light"])

        area_id = self.player.current_area
        killed_bosses = getattr(self.player, "killed_bosses", [])
        is_first_kill = area_id not in killed_bosses

        if is_first_kill or random.random() < 0.30:
            dropped_scroll = random.choice(scroll_pool)
            self.player.inventory[dropped_scroll] = self.player.inventory.get(dropped_scroll, 0) + 1
            scroll_name = tf(self.cog.items.get(dropped_scroll, {}), "name", lang) or dropped_scroll
            log += t(lang, "combat.boss_scroll_reward", "🎁 討伐 BOSS 獎勵！獲得技能卷軸：{scroll} ", scroll=scroll_name)
            if is_first_kill:
                log += t(lang, "combat.boss_first_kill_tag", "(✨ 首殺首通確定獎勵！)\n")
                killed_bosses.append(area_id)
                self.player.killed_bosses = killed_bosses
            else:
                log += t(lang, "combat.boss_daily_tag", "(⚡ 每日挑戰隨機獲得！)\n")
        return log

    def _update_kill_quest_progress(self, monster: dict) -> str:
        lang = self.player.language
        quest_log = ""
        monster_id = monster.get("id")
        is_boss = bool(monster.get("is_boss"))

        def _matches(quest_info) -> bool:
            qtype = quest_info.get("quest_type")
            if qtype == "kill":
                return quest_info.get("target_monster") == monster_id
            if qtype == "boss_kill":
                return is_boss and quest_info.get("target_monster") == monster_id
            return False

        for quest_id, quest_data in self.player.active_quests.items():
            quest_info = self.cog.quests.get(quest_id)
            if quest_info and _matches(quest_info):
                quest_data["progress"] += 1
                quest_title = tf(quest_info, "title", lang)
                quest_log += t(lang, "combat.quest_progress_update", "\n📜 任務進度更新：{title} ({progress}/{target})", title=quest_title, progress=quest_data["progress"], target=quest_info["target_count"])
                if quest_data["progress"] >= quest_info["target_count"]:
                    quest_log += t(lang, "combat.quest_completed", "\n✅ 任務達成！稍後會有委託人的訊息通知你領取獎勵。")

        # 👇 隱藏任務進度更新（kill/boss_kill；隱藏任務不需要「接受」，符合等級的玩家都視為已在追蹤）
        hidden_progress = getattr(self.player, "hidden_quest_progress", None)
        if not isinstance(hidden_progress, dict):
            hidden_progress = {}
            self.player.hidden_quest_progress = hidden_progress
        for quest_id, quest_info in self.cog.quests.items():
            if not quest_info.get("hidden"):
                continue
            if quest_id in self.player.completed_quests:
                continue
            if self.player.level < quest_info.get("req_level", 1):
                continue
            if not _matches(quest_info):
                continue
            hidden_progress[quest_id] = hidden_progress.get(quest_id, 0) + 1

        return quest_log

    def _process_dungeon_victory(self, killed_monsters: list) -> str:
        """地下城戰鬥勝利：不結算真實 exp/金幣，改觸發戰利品/通關，並處理擊殺回血。"""
        lang = self.player.language
        eff = getattr(self.player, "dungeon_relic_effects", None) or {}
        clear_all_status(self.player)

        kill_log = ""
        heal_pct = eff.get("on_kill_heal_pct", 0)
        if heal_pct > 0:
            heal = max(1, int(self.player.max_hp * heal_pct))
            before = self.player.current_hp
            self.player.current_hp = min(self.player.max_hp, self.player.current_hp + heal)
            if self.player.current_hp - before > 0:
                kill_log = "\n" + t(lang, "combat.on_kill_heal", "💚 擊殺回復了 {heal} HP！", heal=self.player.current_hp - before)

        self._clear_battle_state()
        self.view.in_battle = False
        self.view.monster_slots = []
        is_boss = any(m.get("is_boss") for m in killed_monsters)
        is_elite = any(m.get("is_elite") for m in killed_monsters)
        base_log = t(lang, "combat.dungeon_victory", "🏆 戰鬥勝利！") + kill_log
        self.view.on_dungeon_victory(is_elite=is_elite, is_boss=is_boss, base_log=base_log)
        self.cog.save_players()
        return self.view.log_message

    def _process_victory(self) -> str:
        lang = self.player.language
        all_monsters = [slot["monster"] for slot in self.view.monster_slots]
        if not all_monsters:
            return ""

        killed_monsters = [slot["monster"] for slot in self.view.monster_slots if not slot.get("fled")]
        fled_monsters = [slot["monster"] for slot in self.view.monster_slots if slot.get("fled")]

        # 地下城走獨立的勝利結算（戰利品/通關），不給真實 exp/金幣
        if all_monsters[0].get("is_dungeon"):
            return self._process_dungeon_victory(killed_monsters)

        total_gold = 0
        total_exp = 0
        drop_log = ""
        boss_log = ""
        quest_log = ""

        for monster in killed_monsters:
            gold, exp = self._gold_and_exp_for(monster)
            total_gold += gold
            total_exp += exp
            drop_log += self._roll_drops_for(monster)
            boss_log += self._handle_boss_kill_rewards(monster)
            quest_log += self._update_kill_quest_progress(monster)

        self.cog.adjust_bank(self.view.user_id, total_gold)
        lvl_up = self.player.add_exp(total_exp, self.cog.items)
        self.player.stats["monsters_killed"] = self.player.stats.get("monsters_killed", 0) + len(killed_monsters)
        clear_all_status(self.player)

        flee_log = ""
        if fled_monsters:
            names = "、".join(tf(m, "name", lang) for m in fled_monsters)
            flee_log = "\n" + t(lang, "combat.monsters_fled", "🏃 {names} 趁亂逃離了戰場，沒有獲得牠們的擊殺獎勵。", names=names)

        # 魔塔/地下城是整場戰鬥共享的情境旗標，用第一隻怪物的標記判斷即可
        primary_monster = all_monsters[0]

        tower_log = ""
        if primary_monster.get("is_tower"):
            self.player.tower_floor += 1
            self.view.tower_safe_room_visited = False  # 👈 這行是關鍵，重置狀態讓下一層有休息室
            tower_log = "\n" + t(lang, "combat.tower_floor_open", "🧗 轟隆隆... 通往第 {floor} 層的階梯緩緩降下了！", floor=self.player.tower_floor)

            # Milestone rewards logic
            completed_floor = self.player.tower_floor - 1
            milestones = {
                10: {"trophy": t(lang, "combat.trophy_bronze", "🥉 銅魔箱勳章"), "gold": 5000, "item": None},
                25: {"trophy": t(lang, "combat.trophy_silver", "🥈 銀魔箱勳章"), "gold": 0, "item": "mystery_power_ring"},
                50: {"trophy": t(lang, "combat.trophy_gold", "🥇 金魔箱勳章"), "gold": 0, "item": "mystery_void_blade"},
                75: {"trophy": t(lang, "combat.trophy_diamond", "💎 鑽石魔箱勳章"), "gold": 0, "item": "immortal_totem"},
                99: {"trophy": t(lang, "combat.trophy_champion", "👑 冠軍魔箱勳章"), "gold": 0, "item": "scroll_divine_thunder"}
            }
            if completed_floor in milestones:
                milestone_data = milestones[completed_floor]
                if not hasattr(self.player, "tower_milestones"):
                    self.player.tower_milestones = []
                if completed_floor not in self.player.tower_milestones:
                    self.player.tower_milestones.append(completed_floor)
                    if milestone_data.get("gold", 0) > 0:
                        self.cog.adjust_bank(self.view.user_id, milestone_data["gold"])

                    item_msg = ""
                    gift_item = milestone_data["item"]
                    if gift_item:
                        self.player.inventory[gift_item] = self.player.inventory.get(gift_item, 0) + 1
                        gift_name = tf(self.cog.items.get(gift_item, {}), "name", lang) or gift_item
                        item_msg = t(lang, "combat.milestone_item_suffix", " 與【{item}】x1", item=gift_name)

                    if not hasattr(self.player, "trophies"):
                        self.player.trophies = []
                    self.player.trophies.append(milestone_data["trophy"])

                    gold_msg = t(lang, "combat.milestone_gold_suffix", "、💰 {gold}金幣", gold=milestone_data["gold"]) if milestone_data.get("gold", 0) > 0 else ""
                    tower_log += t(
                        lang, "combat.tower_milestone_reached",
                        "\n🏆 **【魔塔里程碑達成！】**\n你征服了魔塔第 {floor} 層！獲得了【{trophy}】{gold_msg}{item_msg}！",
                        floor=completed_floor, trophy=milestone_data["trophy"], gold_msg=gold_msg, item_msg=item_msg,
                    )

        log = t(
            lang, "combat.victory_summary",
            "\n🏆 戰鬥勝利！\n獲得了 {exp} 經驗值與 {gold} {money_name}。\n{drop_log}{boss_log}{quest_log}{flee_log}{tower_log}{dungeon_log}",
            exp=total_exp, gold=total_gold, money_name=self.cog.bot.baba.money_name,
            drop_log=drop_log, boss_log=boss_log, quest_log=quest_log, flee_log=flee_log, tower_log=tower_log, dungeon_log="",
        )

        if lvl_up:
            log += "\n" + t(lang, "combat.level_up", "🌟 升級了！你提升到了 Lv.{level}！", level=self.player.level)

        achv_text = self.view.check_achievements()
        if achv_text:
            log += achv_text

        self.view.record_combat_history(log)
        self.cog.save_players()
        self._clear_battle_state()
        self.view.in_battle = False
        self.view.monster_slots = []
        self.view.build_main_menu()
        return log

