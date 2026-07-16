"""TRPG 戰鬥系統 — 集中處理攻擊、技能、掉落、異常狀態與勝負判定。"""

import random

from datetime import datetime

from trpg.i18n import t, tf
from trpg.archetypes import core_active

from trpg.balance import (
    XP_CURVE_BASE, XP_CURVE_EXP, PHYSICAL_CRIT_CHANCE, SKILL_CRIT_CHANCE,
    FLEE_BASE_CHANCE, FLEE_MIN_CHANCE, FLEE_MAX_CHANCE, FLEE_SPD_FACTOR,
    SCHRODINGER_DOUBLE_CHANCE, SCHRODINGER_DOUBLE_MULT, SCHRODINGER_HALVE_MULT,
    BOSS_DAILY_REWARD_CHANCE, BOSS_SPECIFIC_REWARDS, TOWER_MILESTONES,
    LUCK_CRIT_BONUS_PER_POINT, LUCK_DROP_RATE_BONUS_PER_POINT,
    LUCK_EXP_BONUS_PER_POINT, LUCK_GOLD_BONUS_PER_POINT,
)

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
    meets_skill_requirements,
    format_skill_point_requirements,
)

from trpg.skill_progression import (
    record_successful_skill_use,
    get_skill_effect_multiplier,
    get_skill_cost_multiplier,
)

from trpg.entity import PlayerCombatant, MonsterCombatant
from trpg import dungeon as dg


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
    if core_active(player, "knight"):
        df = int(df * 1.20)
    return max(0, df)


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
    return max(0, mdf)


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
    combat_buffs = getattr(player, "combat_buffs", None) or {}
    if combat_buffs.get("turns", 0) > 0 and combat_buffs.get("magic_mult"):
        mag = int(mag * combat_buffs["magic_mult"])
    return mag


def _format_physical_hit_msg(lang: str, dmg: int, is_crit: bool) -> str:
    if is_crit:
        return t(lang, "combat.player_crit_hit", "💥 暴擊！造成 {dmg} 點傷害！", dmg=dmg)
    return t(lang, "combat.player_normal_hit", "⚔️ 造成 {dmg} 點傷害。", dmg=dmg)


def _target_has_status(target_statuses: dict) -> bool:
    return bool(target_statuses) and any(
        isinstance(value, dict) and value.get("turns", 0) > 0
        for value in target_statuses.values()
    )


def _status_synergy_multiplier_from_skills(player, skills: dict, target_statuses: dict) -> float:
    """Compatibility shim: status bonus is no longer a passive/global effect."""
    return 1.0


def apply_schrodinger(player, dmg: int, is_crit: bool = False) -> tuple[int, str]:
    """薛丁格的懷錶：命中時賭一把翻倍或減半。訊息一律根據賭盤結果後的最終傷害重新組字，
    不能沿用賭盤前算好的舊訊息——沿用會讓玩家看到跟實際扣血量對不上的數字。"""
    lang = getattr(player, "language", "zh")
    if getattr(player, "accessory", "") == "schrodinger_watch":
        if random.random() < SCHRODINGER_DOUBLE_CHANCE:
            final_dmg = dmg * SCHRODINGER_DOUBLE_MULT
            base_msg = _format_physical_hit_msg(lang, final_dmg, is_crit)
            return final_dmg, t(lang, "combat.schrodinger_double", "⏱️ 【薛丁格的懷錶】發動！傷害翻倍！\n{base_msg}", base_msg=base_msg)
        else:
            final_dmg = max(1, int(dmg * SCHRODINGER_HALVE_MULT))
            base_msg = _format_physical_hit_msg(lang, final_dmg, is_crit)
            return final_dmg, t(lang, "combat.schrodinger_half", "⏱️ 【薛丁格的懷錶】反噬！傷害減半！\n{base_msg}", base_msg=base_msg)
    return dmg, _format_physical_hit_msg(lang, dmg, is_crit)


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


def apply_elemental(dmg: int, ele_mult: float) -> int:
    """套用屬性倍率：完全免疫（倍率0）要真的打出 0 傷害，不能被下面慣用的
    max(1, ...) 下限蓋掉變成「免疫但還是打1點」的矛盾結果。"""
    if ele_mult <= 0:
        return 0
    return max(1, int(dmg * ele_mult))


def luck_crit_bonus(fortune: int) -> float:
    """Hidden Fortune modifies crit in both directions within its bounded range."""
    return max(-0.05, min(0.05, int(fortune) * LUCK_CRIT_BONUS_PER_POINT))


def luck_drop_rate_mult(luck: int) -> float:
    """運氣換算成的掉寶率相對加成倍率（乘在每一項 drop 機率上，非疊加到 100% 之外的絕對值）。"""
    return max(0.75, min(1.25, 1.0 + int(luck) * LUCK_DROP_RATE_BONUS_PER_POINT))


def luck_exp_mult(luck: int) -> float:
    return max(0.85, min(1.15, 1.0 + int(luck) * LUCK_EXP_BONUS_PER_POINT))


def luck_gold_mult(luck: int) -> float:
    return max(0.85, min(1.15, 1.0 + int(luck) * LUCK_GOLD_BONUS_PER_POINT))


def absorb_note_text(reason: str, lang: str) -> str:
    """把 absorb_monster_damage 記下的吸收原因翻成給玩家看的說明——沒有這行說明，
    玩家只會看到戰報寫「造成 500 點傷害」但血條紋風不動，以為遊戲壞掉了。"""
    if reason == "shield":
        return t(lang, "combat.absorb_shield", "🛡️ 對方的【聖盾】完全擋下了這次傷害，聖盾破裂了！")
    if reason == "intangible":
        return t(lang, "combat.absorb_intangible", "👻 對方處於【無實體】狀態，這次攻擊只造成了 1 點傷害！")
    if reason == "cap":
        return t(lang, "combat.absorb_cap", "🧱 對方的【傷害屏障】啟動，超出上限的傷害被無效化了！")
    return ""


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

    player_obj = getattr(caster, "player", None)
    relic_crit = 0.0
    if player_obj is not None:
        relic_effects = getattr(player_obj, "dungeon_relic_effects", None) or {}
        relic_crit = relic_effects.get("crit_bonus", 0.0)

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
            status_synergy = float(skill.get("status_target_damage_mult", 1.0)) if _target_has_status(target.status_effects) else 1.0
            if hp_scaling_mult:
                dmg = apply_elemental(int(hp_cost * hp_scaling_mult), ele_mult)
            elif skill_type == "magic":
                dmg = calc_magic_damage(magic, target.mdef, base_power, def_pierce, magic_scaling)
                dmg = apply_elemental(dmg, ele_mult)
            else:
                effective_def = target.def_
                if skill.get("physical_def_pierce"):
                    effective_def = int(effective_def * (1.0 - float(skill["physical_def_pierce"])))
                dmg = calc_physical_damage(atk, effective_def, multiplier)
                dmg = apply_elemental(dmg, ele_mult)

            if dmg > 0 and status_synergy != 1.0:
                dmg = max(1, int(dmg * status_synergy))
            caster_player = getattr(caster, "player", None)
            if dmg > 0 and caster_player is not None and core_active(caster_player, "warlock") and skill.get("req_points", {}).get("warlock"):
                pact_stacks = min(5, int((getattr(caster_player, "combat_buffs", None) or {}).get("blood_pact_stacks", 0)))
                dmg = max(1, int(dmg * (1.0 + pact_stacks * 0.12)))

            # 物理跟 HP 獻祭流都可以暴擊，魔法傷害不會
            caster_fortune = getattr(getattr(caster, "player", None), "fortune", 0)
            if skill_type != "magic" and random.random() < (SKILL_CRIT_CHANCE + crit_bonus + relic_crit + luck_crit_bonus(caster_fortune)):
                dmg = int(dmg * 1.5)
                hit_logs.append(t(target.lang, "combat.skill_hit_crit", "  第{n}擊暴擊 {dmg} 點！", n=i + 1, dmg=dmg))
            else:
                hit_logs.append(t(target.lang, "combat.skill_hit_normal", "  第{n}擊 {dmg} 點", n=i + 1, dmg=dmg))
            t_dmg += dmg

        target.hp -= t_dmg
        total_dmg += t_dmg

        block = debuff_msg + t(target.lang, "combat.deal_damage", "對 {target} 造成 {dmg} 點傷害！", target=target.name, dmg=t_dmg)
        if ele_msg: block += f"\n   ↳ {ele_msg}"
        # 聖盾/無實體/傷害上限吸收提示（只有 MonsterCombatant 會有值，玩家目標回傳空字串）
        absorb = absorb_note_text(target.pop_absorb_note(), target.lang)
        if absorb: block += f"\n   ↳ {absorb}"
        if hits > 1: block += "\n" + "\n".join(hit_logs)

        wake_log = break_sleep_on_damage(target.status_effects, target.lang)
        if wake_log:
            block += f"\n{wake_log}"

        apply_status_id = skill.get("apply_status")
        if apply_status_id and random.random() < skill.get("apply_status_chance", 1.0):
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
        self.player_ap = 0
        self.player_max_ap = 1
        self.round_started = False
        self.round_number = 0
        self.next_round_ap_penalty = 0
        self.is_defending = False
        self.is_dodging = False



    def _clear_battle_state(self):
        self.skill_cds.clear()
        self.player_ap = 0
        self.player_max_ap = 1
        self.round_started = False
        self.round_number = 0
        self.next_round_ap_penalty = 0
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



    @staticmethod
    def action_points_for_speed(actor_spd: int, opponent_spd: int) -> int:
        """Return 1..3 AP with diminishing speed thresholds."""
        actor_spd = max(1, int(actor_spd))
        opponent_spd = max(1, int(opponent_spd))
        ratio = actor_spd / opponent_spd
        if ratio >= 2.5:
            return 3
        if ratio >= 1.5:
            return 2
        return 1

    def calculate_player_ap(self) -> int:
        enemy_speeds = [self._effective_monster_spd(slot) for slot in self.view.monster_slots if slot.get("hp", 0) > 0]
        reference = max(enemy_speeds, default=self._effective_player_spd())
        return self.action_points_for_speed(self._effective_player_spd(), reference)

    def monster_action_points(self, slot: dict) -> int:
        if slot.get("hp", 0) <= 0:
            return 0
        return self.action_points_for_speed(self._effective_monster_spd(slot), self._effective_player_spd())

    def _reset_combo_after_damage(self, before_hp: int) -> str:
        if self.player.current_hp >= before_hp:
            return ""
        buffs = getattr(self.player, "combat_buffs", None) or {}
        if int(buffs.get("combo_stacks", 0)) <= 0:
            return ""
        buffs["combo_stacks"] = 0
        return t(self.player.language, "combat.combo_broken", "💥 受到傷害，連擊節奏歸零！")

    def _player_turn_start(self) -> tuple[str, bool]:
        if self.round_started:
            return "", True
        self.round_started = True
        self.round_number += 1
        self.player_max_ap = max(1, self.calculate_player_ap() - self.next_round_ap_penalty)
        self.next_round_ap_penalty = 0
        self.player_ap = self.player_max_ap
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

        # Automatic mana regeneration is the Mage core identity, not a global
        # benefit shared by every archetype.
        mp_regen = 0
        if core_active(self.player, "mage"):
            mp_regen = max(3, int(self.player.max_mp * 0.04 + getattr(self.player, "base_int", 0) * 0.05))
        if mp_regen > 0 and self.player.current_hp > 0 and self.player.current_mp < self.player.max_mp:
            before_mp = self.player.current_mp
            self.player.current_mp = min(self.player.max_mp, self.player.current_mp + mp_regen)
            gained_mp = self.player.current_mp - before_mp
            if gained_mp > 0:
                mp_regen_line = t(self.player.language, "combat.mp_regen_tick", "🔷 冥想般地回復了 {mp} 點 MP。", mp=gained_mp)
                buff_log = f"{buff_log}\n{mp_regen_line}" if buff_log else mp_regen_line

        before_status_hp = self.player.current_hp
        log, can_act = process_turn_start(self.player, self.cog.status_effects)
        combo_log = self._reset_combo_after_damage(before_status_hp)
        if combo_log:
            log = f"{log}\n{combo_log}" if log else combo_log
        if buff_log:
            log = f"{buff_log}\n{log}" if log else buff_log
        if self.player.current_hp <= 0:
            log = self.view.process_death(log, t(self.player.language, "combat.death_by_status", "💀 異常狀態將你折磨至死..."))
            return log, False
        if not can_act:
            # A control effect skips the complete round, regardless of speed.
            self.player_ap = 1
            self.player_max_ap = 1
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
                for k in ("atk_mult", "def_mult", "mdef_mult", "magic_mult", "spd_mult", "turns"):
                    buffs.pop(k, None)
                parts.append(t(lang, "combat.buff_expired", "💨 你的強化效果消退了。"))
        coating = buffs.get("weapon_coating")
        if isinstance(coating, dict) and coating.get("turns", 0) > 0:
            coating["turns"] -= 1
            if coating["turns"] <= 0:
                buffs.pop("weapon_coating", None)
                parts.append(t(lang, "combat.coating_expired", "💨 武器上的元素塗層消散了。"))
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
        """Compatibility name used by the UI; AP is now deterministic per round."""
        return self.monster_action_points(slot)

    def _check_action_status(self) -> tuple[bool, str]:
        """Per-action check for Paralysis and Freeze. Returns (can_act, log)."""
        status_effects = getattr(self.player, "status_effects", {})
        if not status_effects:
            return True, ""
        lang = self.player.language

        if "freeze" in status_effects:
            del status_effects["freeze"]
            return False, t(lang, "combat.freeze_break", "🧊 你用力掙脫了冰凍，但也失去了這次行動的機會！")

        if "paralysis" in status_effects:
            info = self.cog.status_effects.get("paralysis", {})
            skip_chance = info.get("skip_chance", 0.5)
            if random.random() < skip_chance:
                return False, t(lang, "status.paralysis_skip", "⚡ 你的身體一陣麻痺，無法順利行動！")

        return True, ""

    @staticmethod
    def _has_ultimate_warning(slot: dict) -> bool:
        return bool(slot.get("telegraph") or slot.get("is_charging") or slot.get("bomb_fuse"))

    def advance_time(self, log: str, ap_cost: int = 1, force_end: bool = False) -> str:
        """Consume player AP. Monsters act only when AP is exhausted or the player ends the round."""
        if not self.round_started:
            start_log, can_act = self._player_turn_start()
            if start_log:
                log = f"{start_log}\n{log}" if log else start_log
            if not can_act:
                force_end = True
        if force_end or self.is_defending or self.is_dodging:
            self.player_ap = 0
        else:
            self.player_ap = max(0, self.player_ap - max(1, int(ap_cost)))
        if self.player_ap > 0:
            return log
        return self.end_turn(log)

    def end_turn(self, log: str = "") -> str:
        """End the complete player round, resolve every monster's AP, then start the next round."""
        from trpg.monster_ai import run_monster_ai, tick_revive
        from trpg.status import process_monster_status

        self.player_ap = 0
        skipped_rounds = 0
        while skipped_rounds < 10 and self.player.current_hp > 0:
            for slot in list(self.view.monster_slots):
                if slot.get("hp", 0) <= 0:
                    log += tick_revive(slot, self.player.language)

            for slot in list(self.view.monster_slots):
                if slot.get("hp", 0) <= 0 or self.player.current_hp <= 0:
                    continue
                status_log, can_act = process_monster_status(slot, self.cog.status_effects, self.player.language)
                if status_log:
                    log += f"\n{status_log}"
                if slot.get("hp", 0) <= 0 or not can_act:
                    continue
                actions = self.monster_action_points(slot)
                for _ in range(actions):
                    if slot.get("hp", 0) <= 0 or self.player.current_hp <= 0:
                        break
                    warned_before = self._has_ultimate_warning(slot)
                    before_hp = self.player.current_hp
                    log = run_monster_ai(self, slot, log, round_start=(_ == 0))
                    combo_log = self._reset_combo_after_damage(before_hp)
                    if combo_log:
                        log += f"\n{combo_log}"
                    if slot.pop("_skip_remaining_ap", False):
                        break
                    if not warned_before and self._has_ultimate_warning(slot):
                        break  # Telegraphing a large move ends this monster's phase.
                    if self._all_monsters_dead():
                        return log + self._process_victory()

            if self.player.current_hp <= 0:
                return log
            if self._all_monsters_dead():
                return log + self._process_victory()

            self.round_started = False
            start_log, can_act = self._player_turn_start()
            if start_log:
                log += f"\n{start_log}"
            if self.player.current_hp <= 0 or can_act:
                return self._resolve_pending_revives(log)
            self.player_ap = 0
            skipped_rounds += 1
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
        
        action_can, action_log = self._check_action_status()
        if not action_can:
            log += f"\n{action_log}" if log else action_log
            return self.advance_time(log, ap_cost=1)

        self.is_defending = True
        log += "\n" + t(self.player.language, "combat.defend_stance", "🛡️ 你舉起武器採取防禦姿態，準備迎接衝擊！")
        return self.advance_time(log)

    def dodge(self) -> str:
        dot_log, can_act = self._player_turn_start()
        if self.player.current_hp <= 0: return dot_log
        log = f"{dot_log}\n" if dot_log else ""
        if not can_act: return self.advance_time(log)

        action_can, action_log = self._check_action_status()
        if not action_can:
            log += f"\n{action_log}" if log else action_log
            return self.advance_time(log, ap_cost=1)

        self.is_dodging = True
        log += "\n" + t(self.player.language, "combat.dodge_stance", "💨 你全神貫注地盯著敵人，準備進行閃避！")
        return self.advance_time(log)
    
    def player_attack(self) -> str:
        dot_log, can_act = self._player_turn_start()
        if self.player.current_hp <= 0: return dot_log
        log = f"{dot_log}\n" if dot_log else ""

        if not can_act:
            return self.advance_time(log)

        action_can, action_log = self._check_action_status()
        if not action_can:
            log += f"\n{action_log}" if log else action_log
            return self.advance_time(log, ap_cost=1) # 👈 被麻痺就直接過回合

        # 前排目前沒有活著的目標（例如 BOSS 即將復活的空檔）：推進時間讓復活／小怪行動，避免攻擊到 None
        if self.monster is None:
            return self.advance_time(log)

        # 取出武器的速度加成
        weapon = self.cog.items.get(self.player.weapon, {})

        coating = (getattr(self.player, "combat_buffs", None) or {}).get("weapon_coating") or {}
        attack_elem = coating.get("element") or weapon.get("element", "physical")
        ele_mult, ele_msg = get_elemental_multiplier(attack_elem, self.monster, self.player.language)

        spd_scale = weapon.get("spd_scaling", 0.0)
        p_spd = get_player_spd(self.player)
        multiplier = 1.0 + (p_spd * spd_scale / 100.0)

        lang = self.player.language
        eff = getattr(self.player, "dungeon_relic_effects", None) or {}

        # 👇 在造成傷害前先鎖定「這一擊實際打中的目標」的 status dict：self.monster_status
        # 永遠讀「目前前排」，如果這一擊剛好把前排打死，扣血後前排會立刻換成下一隻怪，
        # 之後的吸血/腐蝕/喚醒/武器附加狀態就會全部誤套用到「被打死那隻的下一隻」身上。
        target_slot = self.view._front_slot()
        target_status = target_slot["status"] if target_slot else {}

        # 傳遞屬性與速度倍率給攻擊計算（地下城遺物/裝備可加暴擊率）
        base_dmg, is_crit = self._do_physical_hit(multiplier=multiplier, crit_bonus=eff.get("crit_bonus", 0.0), ele_mult=ele_mult)
        synergy_mult = self._status_synergy_multiplier(target_status)
        if base_dmg > 0 and synergy_mult != 1.0:
            base_dmg = max(1, int(base_dmg * synergy_mult))
        p_dmg, hit_msg = apply_schrodinger(self.player, base_dmg, is_crit)

        monster_name = tf(self.monster, "name", lang)
        log += t(lang, "combat.player_attacks", "⚔️ 你攻擊了 {monster}，{hit_msg}", monster=monster_name, hit_msg=hit_msg)
        if ele_msg:
            log += f"\n   ↳ {ele_msg}"
        self.monster_hp -= p_dmg
        absorb_note = absorb_note_text(target_slot.pop("last_absorb", "") if target_slot else "", lang)
        if absorb_note:
            log += f"\n   ↳ {absorb_note}"

        # 地下城：吸血
        if eff.get("lifesteal", 0) > 0 and p_dmg > 0:
            heal = max(1, int(p_dmg * eff["lifesteal"]))
            before = self.player.current_hp
            self.player.current_hp = min(self.player.max_hp, self.player.current_hp + heal)
            if self.player.current_hp - before > 0:
                log += "\n" + t(lang, "combat.lifesteal", "🩸 吸血回復了 {heal} HP！", heal=self.player.current_hp - before)

        # 地下城：腐蝕附加
        if eff.get("corrosion_on_hit", 0) > 0:
            from trpg.dungeon import corrosion_params
            from trpg.status import apply_corrosion
            stacks, per = corrosion_params(self.player.dungeon_state, eff)
            if stacks > 0:
                total = apply_corrosion(target_status, stacks, per)
                log += "\n" + t(lang, "combat.corrosion_apply", "🧪 附加了腐蝕，目前共 {stacks} 層！", stacks=total)

        wake_log = break_sleep_on_damage(target_status, lang)
        if wake_log:
            log += f"\n{wake_log}"
        log = self._apply_weapon_on_hit(log, target_status, target_slot["monster"] if target_slot else None)
        if coating.get("apply_status") and target_slot and target_slot.get("hp", 0) > 0 and random.random() < coating.get("status_chance", 0):
            coating_log = apply_status_to_monster(
                target_status, coating["apply_status"], coating.get("status_turns", 2),
                self.cog.status_effects, t(lang, "combat.weapon_coating_source", "【元素塗層】"),
                lang=lang, monster=target_slot["monster"],
            )
            if coating_log:
                log += f"\n{coating_log}"
        combo_log = self._gain_combo() if p_dmg > 0 else ""
        if combo_log:
            log += f"\n{combo_log}"

        if self._all_monsters_dead():
            return log + self._process_victory()

        return self.advance_time(log) # 👈 行動結束，交給時間流逝

    def attempt_flee(self) -> str:
        dot_log, can_act = self._player_turn_start()
        if self.player.current_hp <= 0: return dot_log
        log = f"{dot_log}\n" if dot_log else ""

        if not can_act:
            return self.advance_time(log + "\n" + t(self.player.language, "combat.flee_paralyzed", "💨 你試圖逃跑，但身體不聽使喚！"))

        # 前排目前沒有活著的目標（例如 BOSS 即將復活的空檔）：跟 player_attack 一樣先讓時間流逝，
        # 不能直接讀 self.monster.get(...)——那時 self.monster 是 None，會直接炸掉整個互動。
        if self.monster is None:
            return self.advance_time(log)

        p_spd = get_player_spd(self.player)
        m_spd = self.monster.get("spd", int(10 + self.player.level * 2.2))
        flee_chance = min(FLEE_MAX_CHANCE, max(FLEE_MIN_CHANCE, FLEE_BASE_CHANCE + (p_spd - m_spd) * FLEE_SPD_FACTOR))

        if random.random() < flee_chance:
            if self._end_combat_via_flee():
                return log + "\n" + t(self.player.language, "combat.flee_success_dungeon", "🏃 你成功逃跑了！但地下城危機四伏，你只能一路逃回村莊。")
            return log + "\n" + t(self.player.language, "combat.flee_success", "🏃 你化作一陣風，成功脫離了戰鬥。")

        return self.advance_time(log + "\n" + t(self.player.language, "combat.flee_fail", "💨 逃跑失敗！你的速度不夠快，被攔截了！"), force_end=True)

    def _end_combat_via_flee(self) -> bool:
        """成功逃跑後共用的收尾：清狀態、清戰鬥快照，並在地下城裡把逃跑視同放棄本次
        探索（跟 handle_dungeon_flee 走一樣的收尾），不能讓地下城逃跑只是普通逃回原地
        ——這裡統一給 attempt_flee 與 flee 型技能共用，避免各自兜一份收尾邏輯又漏東西。
        回傳 True 代表這次逃跑發生在地下城探索中（給呼叫端決定要顯示哪一種訊息）。"""
        from trpg.status import clear_all_status
        clear_all_status(self.player)
        is_dungeon_run = bool(self.monster and self.monster.get("is_dungeon", False))
        if is_dungeon_run:
            dg.end_run(self.player)
            self.player.dungeon_state["in_run"] = False
            self.player.dungeon_state["choices"] = []
            self.player.dungeon_state["floor"] = 1
            self.player.current_area = "area_00village"
            self.player.current_subarea = None
        self._clear_battle_state()
        self.view.in_battle = False
        self.view.monster_slots = []
        self.cog.save_players(player=self.player)
        self.view.build_main_menu()
        return is_dungeon_run

    def _equipped_passive_value(self, field: str):
        """查詢玩家目前裝備的被動技能裡有沒有帶著這個欄位（例如連續攻擊的
        extra_attack_multiplier），有的話回傳數值；沒有裝備任何相關被動就回傳 None。"""
        for skill_id in getattr(self.player, "equipped_skills", None) or []:
            skill = self.cog.skills.get(skill_id, {})
            if skill.get("type") == "passive" and skill.get(field):
                return skill[field]
        return None

    def _gain_combo(self) -> str:
        if not core_active(self.player, "rogue"):
            return ""
        buffs = self.player.combat_buffs
        before = min(5, int(buffs.get("combo_stacks", 0)))
        after = min(5, before + 1)
        buffs["combo_stacks"] = after
        if after == before:
            return ""
        return t(self.player.language, "combat.combo_gained", "🔄 連擊節奏提升至 {stacks}/5。", stacks=after)

    def _consume_combo(self, amount: int | None = None) -> int:
        buffs = self.player.combat_buffs
        current = min(5, int(buffs.get("combo_stacks", 0)))
        spent = current if amount is None else min(current, max(0, amount))
        buffs["combo_stacks"] = current - spent
        return spent

    def _apply_weapon_on_hit(self, log: str, target_status: dict, target_monster: dict = None) -> str:
        weapon_id = self.player.weapon
        if not weapon_id:
            return log
        weapon = self.cog.items.get(weapon_id, {})
        if weapon.get("on_hit_status") and random.random() < weapon.get("on_hit_chance", 0.25):
            turns = weapon.get("on_hit_status_turns", 2)
            weapon_name = tf(weapon, "name", self.player.language) or t(self.player.language, "combat.generic_weapon", "武器")
            s_log = apply_status_to_monster(
                target_status,
                weapon["on_hit_status"],
                turns,
                self.cog.status_effects,
                f"【{weapon_name}】",
                lang=self.player.language,
                monster=target_monster,
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

    def _do_physical_hit(self, multiplier: float = 1.0, crit_bonus: float = 0.0, ele_mult: float = 1.0) -> tuple[int, bool]:
        """回傳 (最終傷害, 是否暴擊)。組訊息交給呼叫端——薛丁格的懷錶要先賭盤決定最終
        傷害，訊息才能跟著最終數字組字，不能在這裡就把賭盤前的數字焗進字串裡。"""
        p_atk = get_player_atk(self.player, self.cog.items, self.cog.status_effects)
        p_dmg = calc_physical_damage(p_atk, self.monster["def"], multiplier)
        p_dmg = apply_elemental(p_dmg, ele_mult)

        combo_crit = 0.0
        if core_active(self.player, "rogue"):
            combo_crit = min(5, int((getattr(self.player, "combat_buffs", None) or {}).get("combo_stacks", 0))) * 0.03
        crit_rate = PHYSICAL_CRIT_CHANCE + crit_bonus + combo_crit + luck_crit_bonus(getattr(self.player, "fortune", 0))
        is_crit = random.random() < crit_rate
        if is_crit:
            p_dmg = int(p_dmg * 1.6)
        return p_dmg, is_crit

    def _status_synergy_multiplier(self, target_status: dict) -> float:
        return _status_synergy_multiplier_from_skills(self.player, self.cog.skills, target_status)

    def _record_skill_usage(self, skill_id: str, skill_name: str, lang: str) -> str:
        """增加技能施放計數，若達到熟練度門檻則自動升級並回傳升級通知文字。"""
        return record_successful_skill_use(self.player, skill_id, skill_name, lang)

    def _resolve_skill_targets(self, target_type: str) -> list:

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
        ok_req, missing_req, _ = meets_skill_requirements(self.player, skill)
        if not ok_req and missing_req:
            req_text = format_skill_point_requirements(skill, lang=lang)
            return t(lang, "combat.skill_point_requirement_fail", "❌ 你的流派點數不足，無法使用【{skill}】（{req_text}）。", skill=skill_name, req_text=req_text)

        # 沉默狀態：完全封鎖技能（普攻/防禦/道具不受影響）。跟資源檢查一樣放在
        # _player_turn_start() 之前——施放失敗不該吃掉玩家的回合。
        if "silence" in (getattr(self.player, "status_effects", None) or {}):
            return t(lang, "combat.skill_silenced", "🤐 你被【沉默】了，無法唸出咒語或施展技能！（普通攻擊與道具不受影響）")

        if self.skill_cds.get(skill_id, 0) > 0:
            return t(lang, "combat.skill_on_cooldown", "⏳ 【{skill}】冷卻中！（剩餘 {turns} 回合）", skill=skill_name, turns=self.skill_cds[skill_id])

        # 👇 資源檢查必須在 _player_turn_start() 之前：否則 MP/HP 不足而施放失敗時，
        # 回合根本沒有真的發生（怪物不會行動），卻已經先扣了增益/冷卻的剩餘回合數。
        # 包含等級切換所需的消耗塉算
        _p_skill_level = (getattr(self.player, "skill_levels", None) or {}).get(skill_id, 1)
        _eff_mult = get_skill_effect_multiplier(_p_skill_level)
        _cost_mult = get_skill_cost_multiplier(_p_skill_level)

        mp_cost = int(skill.get("mp_cost", 0) * _cost_mult)
        hp_cost_pct = skill.get("hp_cost_percent", 0.0) * _cost_mult
        actual_hp_cost = int(self.player.max_hp * hp_cost_pct)
        combo_cost = int(skill.get("combo_cost", 0) or 0)
        ap_cost = max(1, int(skill.get("ap_cost", 1) or 1))

        if mp_cost > 0 and self.player.current_mp < mp_cost:
            return t(lang, "combat.skill_mp_insufficient", "❌ MP 不足！需要 {cost} 點，目前只有 {have} 點。", cost=mp_cost, have=self.player.current_mp)
        if actual_hp_cost > 0:
            if self.player.current_hp <= actual_hp_cost:
                return t(lang, "combat.skill_hp_insufficient", "❌ HP 不足！【{skill}】需要獻祭 {cost} 點生命，你會把自己抽乾的！", skill=skill_name, cost=actual_hp_cost)
        if combo_cost > int((getattr(self.player, "combat_buffs", None) or {}).get("combo_stacks", 0)):
            return t(lang, "combat.skill_combo_insufficient", "❌ 【{skill}】需要 {cost} 層連擊。", skill=skill_name, cost=combo_cost)
        if ap_cost > self.player_ap:
            return t(lang, "combat.skill_ap_insufficient", "❌ AP 不足！【{skill}】需要 {cost} AP，目前只有 {have}。", skill=skill_name, cost=ap_cost, have=self.player_ap)

        dot_log, can_act = self._player_turn_start()
        if self.player.current_hp <= 0: return dot_log
        log = f"{dot_log}\n" if dot_log else ""

        if not can_act:
            return self.advance_time(log)

        action_can, action_log = self._check_action_status()
        if not action_can:
            log += f"\n{action_log}" if log else action_log
            return self.advance_time(log, ap_cost=ap_cost)

        if skill.get("cd", 0) > 0: self.skill_cds[skill_id] = skill["cd"]

        skill_type = skill.get("type", "physical")

        if mp_cost > 0: self.player.current_mp -= mp_cost
        if actual_hp_cost > 0:
            self.player.current_hp -= actual_hp_cost
            log += t(lang, "combat.skill_hp_sacrifice", "🩸 你殘忍地獻祭了自己 {cost} 點生命值！\n", cost=actual_hp_cost)
            if core_active(self.player, "warlock"):
                current_pact = min(5, int(self.player.combat_buffs.get("blood_pact_stacks", 0)))
                self.player.combat_buffs["blood_pact_stacks"] = min(5, current_pact + 1)
                log += t(lang, "combat.blood_pact_gained", "🩸 血契累積至 {stacks}/5。\\n", stacks=self.player.combat_buffs["blood_pact_stacks"])

        skill_for_cast = dict(skill)
        # 套用技能等級的效果加成到 skill_for_cast
        if _p_skill_level > 1:
            for _eff_key in ("base_power", "heal_amount", "mp_restore"):
                if _eff_key in skill_for_cast and skill_for_cast[_eff_key]:
                    skill_for_cast[_eff_key] = int(skill_for_cast[_eff_key] * _eff_mult)
            for _eff_key in ("heal_percent",):
                if _eff_key in skill_for_cast and skill_for_cast[_eff_key]:
                    skill_for_cast[_eff_key] = skill_for_cast[_eff_key] * _eff_mult
            if skill_for_cast.get("power_multiplier"):
                skill_for_cast["power_multiplier"] = float(skill_for_cast["power_multiplier"]) * _eff_mult
            if skill_for_cast.get("hp_scaling_multiplier"):
                skill_for_cast["hp_scaling_multiplier"] = float(skill_for_cast["hp_scaling_multiplier"]) * _eff_mult
        if combo_cost:
            self._consume_combo(combo_cost)
        if skill.get("combo_consume_all"):
            spent = self._consume_combo(None)
            skill_for_cast["power_multiplier"] = float(skill_for_cast.get("power_multiplier", 1.0)) + spent * float(skill.get("combo_power_per_stack", 0.0))
            if spent >= 5:
                skill_for_cast["crit_bonus"] = 1.0
            log += t(lang, "combat.combo_spent", "🔄 消耗 {stacks} 層連擊強化【{skill}】。\n", stacks=spent, skill=skill_name)

        if skill_type == "support":
            log += self._apply_support_skill(skill_for_cast, skill_id, skill_name, lang)
        elif skill_type == "flee":
            flee_chance = skill.get("flee_chance", 0.85)
            if random.random() < flee_chance:
                if self._end_combat_via_flee():
                    return log + t(lang, "combat.skill_flee_success_dungeon", "💨 使用了【{skill}】，化作一團黑影成功脫離戰鬥！但地下城危機四伏，你只能一路逃回村莊。", skill=skill_name)
                return log + t(lang, "combat.skill_flee_success", "💨 使用了【{skill}】，化作一團黑影成功脫離戰鬥！", skill=skill_name)
            else:
                monster_name = tf(self.monster, "name", lang)
                log += t(lang, "combat.skill_flee_fail", "💦 嘗試使用逃跑，卻被 {monster} 識破！", monster=monster_name)
                return self.advance_time(log, force_end=True)

        else:
            # 👇 target_type 決定打誰：front=只打前排／back=打後排／all=打全體，預設 front
            target_type = skill.get("target_type", "front")
            target_slots = self._resolve_skill_targets(target_type)

            if not target_slots:
                log += t(lang, "combat.skill_no_target", "❌ 沒有可以攻擊的目標。")
            else:
                caster = PlayerCombatant(self.player, self.cog.items, self.cog.status_effects, self.cog.skills)
                targets = [MonsterCombatant(slot, self.cog.status_effects, self.player.language) for slot in target_slots]
                skill_log, total_damage = execute_skill(caster, targets, skill_for_cast, self.cog.status_effects, hp_cost=actual_hp_cost)
                log += skill_log
                pact_stacks = min(5, int(self.player.combat_buffs.get("blood_pact_stacks", 0))) if core_active(self.player, "warlock") else 0
                is_warlock_skill = bool((skill.get("req_points") or {}).get("warlock"))
                lifesteal = float(skill.get("lifesteal", 0.0)) + (pact_stacks * 0.06 if is_warlock_skill else 0.0)
                if total_damage > 0 and lifesteal > 0:
                    before_hp = self.player.current_hp
                    self.player.current_hp = min(self.player.max_hp, self.player.current_hp + max(1, int(total_damage * lifesteal)))
                    gained = self.player.current_hp - before_hp
                    if gained > 0:
                        log += "\n" + t(lang, "combat.skill_lifesteal", "🩸 【{skill}】汲取了 {heal} HP！", skill=skill_name, heal=gained)
                if total_damage > 0 and skill.get("grants_combo"):
                    combo_log = self._gain_combo()
                    if combo_log:
                        log += f"\n{combo_log}"

        # 累積技能施放次數，並處理熟練度自動升級
        log += self._record_skill_usage(skill_id, skill_name, lang)

        if self._all_monsters_dead():
            return log + self._process_victory()

        return self.advance_time(log, ap_cost=ap_cost)



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
            for k in ("atk_mult", "def_mult", "mdef_mult", "magic_mult", "spd_mult"):
                if buff.get(k):
                    p.combat_buffs[k] = buff[k]
            p.combat_buffs["turns"] = max(p.combat_buffs.get("turns", 0), buff.get("turns", 3))
            parts.append(t(lang, "combat.skill_buff", "💪 【{skill}】強化了你的戰鬥能力！（{turns}回合）", skill=skill_name, turns=buff.get("turns", 3)))

        coating = skill.get("weapon_coating")
        if coating:
            coating_state = dict(coating)
            # Buffs tick at the beginning of the next player turn. Add one
            # internal tick so a displayed 3-turn coating empowers 3 attacks.
            coating_state["turns"] = int(coating.get("turns", 3)) + 1
            p.combat_buffs["weapon_coating"] = coating_state
            parts.append(t(lang, "combat.skill_coating", "⚗️ 【{skill}】為武器附上 {element} 元素（{turns}回合）；新的塗層會覆蓋舊塗層。", skill=skill_name, element=coating.get("element", "?"), turns=coating.get("turns", 3)))

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

        self_status = skill.get("self_status")
        if self_status:
            turns = skill.get("self_status_turns", 1)
            s_log = apply_status(PlayerCombatant(p, self.cog.items, self.cog.status_effects, self.cog.skills), self_status, turns, self.cog.status_effects, f"【{skill_name}】")
            if s_log:
                parts.append(s_log)

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

        action_can, action_log = self._check_action_status()
        if not action_can:
            log += f"\n{action_log}" if log else action_log
            return self.advance_time(log, ap_cost=1)

        self.player.inventory[item_id] -= 1
        if self.player.inventory[item_id] <= 0: del self.player.inventory[item_id]

        if "heal_percent" in item_data:
            heal = int(getattr(self.player, f"max_{heal_target}") * item_data["heal_percent"])
        else:
            heal = item_data.get("heal", 50)

        setattr(self.player, f"current_{heal_target}", min(getattr(self.player, f"max_{heal_target}"), getattr(self.player, f"current_{heal_target}") + heal))
        resource_name = t(lang, "combat.resource_mp", "魔力") if heal_target == "mp" else t(lang, "combat.resource_hp", "生命值")
        log += "\n" + t(lang, "combat.potion_drink", "🧪 你喝下藥水，回復了 {heal} 點{resource}。", heal=heal, resource=resource_name)

        return self.advance_time(log)

    def use_cure_item(self, item_id: str) -> str:
        lang = self.player.language
        if self.player.inventory.get(item_id, 0) <= 0: return t(lang, "combat.cure_item_not_owned", "❌ 背包裡沒有這個物品。")
        item_data = self.cog.items.get(item_id, {})
        cures = item_data.get("cures") or []
        if not any(status_id in getattr(self.player, "status_effects", {}) for status_id in cures):
            return t(lang, "status.cure_item_none_active", "❌ 你目前沒有這個藥草能解除的異常狀態。")
        dot_log, can_act = self._player_turn_start()
        if self.player.current_hp <= 0: return dot_log
        log = f"{dot_log}\n" if dot_log else ""

        action_can, action_log = self._check_action_status()
        if not action_can:
            log += f"\n{action_log}" if log else action_log
            return self.advance_time(log, ap_cost=1)

        self.player.inventory[item_id] -= 1
        if self.player.inventory[item_id] <= 0: del self.player.inventory[item_id]

        log += cure_by_item(self.player, item_id, self.cog.items, self.cog.status_effects)
        return self.advance_time(log)

    def use_damage_item(self, item_id: str) -> str:
        """Use a battle-only thrown/released item that deals fixed, unmitigated damage."""
        lang = self.player.language
        if self.player.inventory.get(item_id, 0) <= 0:
            return t(lang, "combat.damage_item_not_owned", "❌ 背包裡沒有這個戰鬥道具。")
        item_data = self.cog.items.get(item_id, {})
        damage = int(item_data.get("damage", 0) or 0)
        if damage <= 0:
            return t(lang, "combat.damage_item_no_effect", "❌ 這個戰鬥道具沒有可用的傷害效果。")

        dot_log, can_act = self._player_turn_start()
        if self.player.current_hp <= 0:
            return dot_log
        log = f"{dot_log}\n" if dot_log else ""

        action_can, action_log = self._check_action_status()
        if not action_can:
            log += f"\n{action_log}" if log else action_log
            return self.advance_time(log, ap_cost=1)

        self.player.inventory[item_id] -= 1
        if self.player.inventory[item_id] <= 0:
            del self.player.inventory[item_id]

        item_name = tf(item_data, "name", lang) or item_id
        # Fixed true damage intentionally skips ATK, DEF, MDEF, resistance, and crit.
        self.view.monster_hp -= damage
        log += "\n" + t(lang, "combat.damage_item_used", "✝️ 你釋放【{item_name}】，造成 {damage} 點神聖真實傷害！", item_name=item_name, damage=damage)

        if self._all_monsters_dead():
            self.player_ap = max(0, self.player_ap - 1)
            return log + self._process_victory()
        return self.advance_time(log)

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

        action_can, action_log = self._check_action_status()
        if not action_can:
            log += f"\n{action_log}" if log else action_log
            return self.advance_time(log, ap_cost=1)

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
        luck = getattr(self.player, "fortune", 0)
        gold = int(random.randint(min_gold, max_gold) * luck_gold_mult(luck))
        exp = int(monster.get("exp", 0) * luck_exp_mult(luck))
        return gold, exp

    def _roll_drops_for(self, monster: dict) -> str:
        lang = self.player.language
        drop_log = ""
        luck_mult = luck_drop_rate_mult(getattr(self.player, "fortune", 0))
        for item_id, rate in monster.get("drops", {}).items():
            effective_rate = min(1.0, rate * luck_mult)
            if random.random() < effective_rate:
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
        log = t(lang, "combat.boss_defeated", "👑 區域 BOSS 討伐成功！今日已無法再次挑戰。\n")

        # Guaranteed reward drop logic (100% first kill, BOSS_DAILY_REWARD_CHANCE daily)
        boss_id = monster.get("id")
        reward_pool = BOSS_SPECIFIC_REWARDS.get(boss_id, ["medium_health_potion"])

        area_id = self.player.current_area
        if not isinstance(getattr(self.player, "daily_boss_kills", None), dict):
            self.player.daily_boss_kills = {}
        self.player.daily_boss_kills[area_id] = today_str
        killed_bosses = getattr(self.player, "killed_bosses", [])
        is_first_kill = area_id not in killed_bosses

        if is_first_kill or random.random() < BOSS_DAILY_REWARD_CHANCE:
            dropped = random.choice(reward_pool)
            if isinstance(dropped, (list, tuple)):
                dropped_item, qty = dropped[0], dropped[1]
            else:
                dropped_item, qty = dropped, 1

            self.player.inventory[dropped_item] = self.player.inventory.get(dropped_item, 0) + qty
            item_name = tf(self.cog.items.get(dropped_item, {}), "name", lang) or dropped_item
            qty_text = f" x{qty}" if qty > 1 else ""
            log += t(lang, "combat.boss_item_reward", "🎁 討伐 BOSS 獎勵！獲得：{item}{qty} ", item=item_name, qty=qty_text)
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
        self.cog.save_players(player=self.player)
        return self.view.log_message

    def _process_victory(self) -> str:
        lang = self.player.language
        all_monsters = [slot["monster"] for slot in self.view.monster_slots]
        if not all_monsters:
            return ""

        killed_monsters = [slot["monster"] for slot in self.view.monster_slots if not slot.get("fled")]
        fled_monsters = [slot["monster"] for slot in self.view.monster_slots if slot.get("fled")]

        # 👇 情境旗標（地下城/魔塔/鬥技場）要掃「全部」怪物，不能只看第一格：
        # 召喚出來的援軍會被插到第 0 格，而援軍身上沒有這些旗標——只看第一格的話，
        # 魔塔頭目召喚過小怪就會導致樓層不推進、地下城戰鬥更會漏走封印結算、
        # 直接發放真實經驗金幣（每個魔塔/地下城的層主都會召喚，必踩）。
        if any(m.get("is_dungeon") for m in all_monsters):
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
        skills_before = set(self.player.skills)
        equipped_before = set(self.player.equipped_skills)
        lvl_up = self.player.add_exp(total_exp, self.cog.items, self.cog.skills)
        unlocked_skills = [skill_id for skill_id in self.player.skills if skill_id not in skills_before]
        self.player.stats["monsters_killed"] = self.player.stats.get("monsters_killed", 0) + len(killed_monsters)
        clear_all_status(self.player)

        flee_log = ""
        if fled_monsters:
            names = "、".join(tf(m, "name", lang) for m in fled_monsters)
            flee_log = "\n" + t(lang, "combat.monsters_fled", "🏃 {names} 趁亂逃離了戰場，沒有獲得牠們的擊殺獎勵。", names=names)

        # 魔塔/鬥技場旗標同樣掃全部怪物（見上：召喚援軍會佔據第一格且不帶旗標）
        primary_monster = next((m for m in all_monsters if m.get("is_tower") or m.get("is_colosseum")), all_monsters[0])

        tower_log = ""
        if primary_monster.get("is_tower"):
            self.player.tower_floor += 1
            self.player.tower_state["safe_room_visited"] = False  # 👈 這行是關鍵，重置狀態讓下一層有休息室
            tower_log = "\n" + t(lang, "combat.tower_floor_open", "🧗 轟隆隆... 通往第 {floor} 層的階梯緩緩降下了！", floor=self.player.tower_floor)

            # Milestone rewards logic (table lives in balance.TOWER_MILESTONES)
            completed_floor = self.player.tower_floor - 1
            if completed_floor in TOWER_MILESTONES:
                cfg = TOWER_MILESTONES[completed_floor]
                trophy_text = t(lang, cfg["trophy_key"], cfg["trophy_fallback"])
                if not hasattr(self.player, "tower_milestones"):
                    self.player.tower_milestones = []
                if completed_floor not in self.player.tower_milestones:
                    self.player.tower_milestones.append(completed_floor)
                    if cfg.get("gold", 0) > 0:
                        self.cog.adjust_bank(self.view.user_id, cfg["gold"])

                    item_msg = ""
                    gift_item = cfg["item"]
                    if gift_item:
                        self.player.inventory[gift_item] = self.player.inventory.get(gift_item, 0) + 1
                        gift_name = tf(self.cog.items.get(gift_item, {}), "name", lang) or gift_item
                        item_msg = t(lang, "combat.milestone_item_suffix", " 與【{item}】x1", item=gift_name)

                    if not hasattr(self.player, "trophies"):
                        self.player.trophies = []
                    self.player.trophies.append(trophy_text)

                    gold_msg = t(lang, "combat.milestone_gold_suffix", "、💰 {gold}金幣", gold=cfg["gold"]) if cfg.get("gold", 0) > 0 else ""
                    tower_log += t(
                        lang, "combat.tower_milestone_reached",
                        "\n🏆 **【魔塔里程碑達成！】**\n你征服了魔塔第 {floor} 層！獲得了【{trophy}】{gold_msg}{item_msg}！",
                        floor=completed_floor, trophy=trophy_text, gold_msg=gold_msg, item_msg=item_msg,
                    )

        # 修羅鬥技場：推進連戰輪次／發放通關獎勵（輪次狀態由 view 管理）
        if primary_monster.get("is_colosseum"):
            tower_log += self.view.on_colosseum_victory(primary_monster)

        log = t(
            lang, "combat.victory_summary",
            "\n🏆 戰鬥勝利！\n獲得了 {exp} 經驗值與 {gold} {money_name}。\n{drop_log}{boss_log}{quest_log}{flee_log}{tower_log}{dungeon_log}",
            exp=total_exp, gold=total_gold, money_name=self.cog.bot.baba.money_name,
            drop_log=drop_log, boss_log=boss_log, quest_log=quest_log, flee_log=flee_log, tower_log=tower_log, dungeon_log="",
        )

        if lvl_up:
            log += "\n" + t(lang, "combat.level_up", "🌟 升級了！你提升到了 Lv.{level}！", level=self.player.level)
        if unlocked_skills:
            names = "、".join(tf(self.cog.skills[skill_id], "name", lang) for skill_id in unlocked_skills)
            log += "\n" + t(lang, "combat.skills_unlocked", "✨ 達成流派條件，學會了：{skills}", skills=names)
            auto_names = [tf(self.cog.skills[skill_id], "name", lang) for skill_id in unlocked_skills if skill_id in self.player.equipped_skills and skill_id not in equipped_before]
            if auto_names:
                log += "\n" + t(lang, "combat.skills_auto_equipped", "✅ 技能欄有空位，已自動裝備：{skills}", skills="、".join(auto_names))

        achv_text = self.view.check_achievements({"monsters_killed", "level", "killed_bosses", "tower_floor", "prestige_count"})
        if achv_text:
            log += achv_text

        self.view.record_combat_history(log)
        self.cog.save_players(player=self.player)
        self._clear_battle_state()
        self.view.in_battle = False
        self.view.monster_slots = []
        self.view.build_main_menu()
        return log

