"""TRPG 戰鬥系統 — 集中處理攻擊、技能、掉落、異常狀態與勝負判定。"""

import random

from datetime import datetime

from trpg_i18n import t, tf

from trpg_status import (

    process_turn_start,

    try_apply_status,

    cure_by_item,

    clear_all_status,

    activate_jester_immunity,

    apply_status_to_monster,

    apply_status,

    break_sleep_on_damage,

)

from trpg_stats import (

    recalc_player_stats,

    get_potion_heal_target,

)

from trpg_entity import PlayerCombatant, MonsterCombatant





def exp_to_next_level(level: int) -> int:

    return int(50 * (level ** 1.8))





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
    return atk





def get_player_def(player, items: dict) -> int:
    df = player.base_def
    dungeon_buffs = getattr(player, "dungeon_buffs", None)
    if dungeon_buffs and dungeon_buffs.get("def_mult"):
        df = int(df * dungeon_buffs["def_mult"])
    combat_debuffs = getattr(player, "combat_debuffs", None)
    if combat_debuffs and combat_debuffs.get("def_mult"):
        df = max(0, int(df * combat_debuffs["def_mult"]))
    return df


def get_player_spd(player) -> int:
    spd = max(5, getattr(player, "base_spd", 10))
    combat_debuffs = getattr(player, "combat_debuffs", None)
    if combat_debuffs and combat_debuffs.get("spd_mult"):
        spd = max(1, int(spd * combat_debuffs["spd_mult"]))
    return spd











def get_sell_price(item_id: str, items: dict) -> int:

    item = items.get(item_id, {})

    if "sell_price" in item:

        return item["sell_price"]

    return max(1, item.get("price", 0) // 2)





def calc_physical_damage(atk: int, defense: int, multiplier: float = 1.0) -> int:

    dmg = max(1, int((atk - defense) * multiplier))

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


def execute_skill(caster, targets: list, skill: dict, status_defs: dict, hp_cost: int = 0) -> tuple[str, int]:
    """對 targets 套用 skill 的傷害／治療與異常狀態，回傳 (戰鬥紀錄文字, 造成的總傷害)。

    caster／targets 都是 Combatant（PlayerCombatant 或 MonsterCombatant），玩家用技能、
    怪物用 active_skills 的 cast_skill 效果，都共用這一條路徑——新增怪物想放任何
    skills.json 裡的技能，不需要再寫第二份傷害計算。
    hp_cost 是這次施放實際被扣掉的 HP（給 hp_scaling_multiplier 這種獻祭流技能用，沒有的話傳 0）。
    """
    skill_type = skill.get("type", "physical")
    skill_elem = skill.get("element", "magic")
    hits = skill.get("hits", 1)
    multiplier = skill.get("power_multiplier", 1.0)
    base_power = skill.get("base_power", 0)
    magic_scaling = skill.get("magic_scaling", 1.0)
    def_pierce = skill.get("def_pierce", 0.5)
    crit_bonus = skill.get("crit_bonus", 0)
    hp_scaling_mult = skill.get("hp_scaling_multiplier")
    atk = caster.atk
    magic = caster.magic

    total_dmg = 0
    target_blocks = []

    for target in targets:
        if target.magic_absorb_shield > 0 and skill_type == "magic":
            dmg = calc_magic_damage(magic, target.def_, base_power, def_pierce, magic_scaling)
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
                dmg = calc_magic_damage(magic, target.def_, base_power, def_pierce, magic_scaling)
                dmg = max(1, int(dmg * ele_mult))
            else:
                dmg = calc_physical_damage(atk, target.def_, multiplier)
                dmg = max(1, int(dmg * ele_mult))

            # 物理跟 HP 獻祭流都可以暴擊，魔法傷害不會
            if skill_type != "magic" and random.random() < (0.12 + crit_bonus):
                dmg = int(dmg * 1.5)
                hit_logs.append(t(target.lang, "combat.skill_hit_crit", "  第{n}擊暴擊 {dmg} 點！", n=i + 1, dmg=dmg))
            else:
                hit_logs.append(t(target.lang, "combat.skill_hit_normal", "  第{n}擊 {dmg} 點", n=i + 1, dmg=dmg))
            t_dmg += dmg

        target.hp -= t_dmg
        total_dmg += t_dmg

        block = t(target.lang, "combat.deal_damage", "對 {target} 造成 {dmg} 點傷害！", target=target.name, dmg=t_dmg)
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

        log, can_act = process_turn_start(self.player, self.cog.status_effects)
        if self.player.current_hp <= 0:
            log = self.view.process_death(log, t(self.player.language, "combat.death_by_status", "💀 異常狀態將你折磨至死..."))
            return log, False
        return log, can_act
    
    def advance_time(self, log: str) -> str:
        """推進時間條：玩家 AV 滿 100 前，場上每隻活著的怪物各自依自己的速度累積 AV 並行動。"""
        self.player_av -= 100
        p_spd = get_player_spd(self.player)

        while self.player_av < 100:
            self.player_av += p_spd

            for slot in list(self.view.monster_slots):
                if slot["hp"] <= 0:
                    from trpg_monster_ai import tick_revive
                    log += tick_revive(slot, self.player.language)
                    continue
                m_base = 15 if slot["monster"].get("is_boss") else 10
                m_spd = max(5, slot["monster"].get("spd", int(m_base + self.player.level * 2.2)))
                slot["av"] += m_spd

                while slot["av"] >= 100:
                    slot["av"] -= 100
                    if slot["hp"] > 0 and self.player.current_hp > 0:
                        log = self._monster_act_slot(slot, log)
                    if self.player.current_hp <= 0:
                        return log
                    if self._all_monsters_dead():
                        return log + self._process_victory()
        return log


    def _monster_act_slot(self, slot: dict, log: str) -> str:
        from trpg_status import process_monster_status
        from trpg_monster_ai import run_monster_ai
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

        # 取出武器的速度加成
        weapon = self.cog.items.get(self.player.weapon, {})

        attack_elem = weapon.get("element", "physical")
        ele_mult, ele_msg = get_elemental_multiplier(attack_elem, self.monster, self.player.language)
        
        spd_scale = weapon.get("spd_scaling", 0.0)
        p_spd = get_player_spd(self.player)
        multiplier = 1.0 + p_spd * spd_scale
        
        # 傳遞屬性與速度倍率給攻擊計算
        base_dmg, base_msg = self._do_physical_hit(multiplier=multiplier, ele_mult=ele_mult) 
        p_dmg, hit_msg = apply_schrodinger(self.player, base_dmg, base_msg)
        
        monster_name = tf(self.monster, "name", self.player.language)
        log += t(self.player.language, "combat.player_attacks", "⚔️ 你攻擊了 {monster}，{hit_msg}", monster=monster_name, hit_msg=hit_msg)
        self.monster_hp -= p_dmg
        wake_log = break_sleep_on_damage(self.monster_status, self.player.language)
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
        m_spd = self.monster.get("spd", int(10 + self.player.level * 2.2))
        flee_chance = min(0.95, max(0.2, 0.4 + (p_spd - m_spd) * 0.015))

        if random.random() < flee_chance:
            from trpg_status import clear_all_status
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

        crit_rate = 0.1 + crit_bonus
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

        dot_log, can_act = self._player_turn_start()
        if self.player.current_hp <= 0: return dot_log
        log = f"{dot_log}\n" if dot_log else ""

        if not can_act:
            self.cog.save_players()
            return self.advance_time(log) # 👈 修正：拔掉 _monster_counter

        mp_cost = skill.get("mp_cost", 0)
        hp_cost_pct = skill.get("hp_cost_percent", 0.0)
        actual_hp_cost = int(self.player.max_hp * hp_cost_pct)

        if mp_cost > 0 and self.player.current_mp < mp_cost:
            return log + t(lang, "combat.skill_mp_insufficient", "❌ MP 不足！需要 {cost} 點，目前只有 {have} 點。", cost=mp_cost, have=self.player.current_mp)
        if actual_hp_cost > 0:
            if self.player.current_hp <= actual_hp_cost:
                return log + t(lang, "combat.skill_hp_insufficient", "❌ HP 不足！【{skill}】需要獻祭 {cost} 點生命，你會把自己抽乾的！", skill=skill_name, cost=actual_hp_cost)

        if skill.get("cd", 0) > 0: self.skill_cds[skill_id] = skill["cd"]

        skill_type = skill.get("type", "physical")

        if mp_cost > 0: self.player.current_mp -= mp_cost
        if actual_hp_cost > 0:
            self.player.current_hp -= actual_hp_cost
            log += t(lang, "combat.skill_hp_sacrifice", "🩸 你殘忍地獻祭了自己 {cost} 點生命值！\n", cost=actual_hp_cost)

        if skill_type == "support":
            if skill_id == "heal_light":
                heal = int(self.player.max_hp * 0.20 + 50)
            else:
                heal = skill.get("heal_amount", 20)
            before = self.player.current_hp
            self.player.current_hp = min(self.player.max_hp, self.player.current_hp + heal)
            log += t(lang, "combat.skill_heal", "✨ 【{skill}】回復了 {heal} 點 HP！", skill=skill_name, heal=self.player.current_hp - before)
        elif skill_type == "flee":
            flee_chance = skill.get("flee_chance", 0.85)
            if random.random() < flee_chance:
                from trpg_status import clear_all_status
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



    def use_potion(self, item_id: str) -> str:
        lang = self.player.language
        if self.player.inventory.get(item_id, 0) <= 0:
            return t(lang, "combat.potion_not_owned", "❌ 你包包裡沒有這個藥水了！")

        item_data = self.cog.items.get(item_id, {})
        heal_target = get_potion_heal_target(item_data, item_id)
        dot_log, can_act = self._player_turn_start()
        if self.player.current_hp <= 0: return dot_log
        log = f"{dot_log}\n" if dot_log else ""

        if heal_target == "mp" and self.player.current_mp >= self.player.max_mp:
            return log + t(lang, "combat.potion_mp_full", "❓ 魔力已經滿了，別浪費藥水。")
        if heal_target == "hp" and self.player.current_hp >= self.player.max_hp:
            return log + t(lang, "combat.potion_hp_full", "❓ 生命值已經滿了，別浪費藥水。")

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

    def _update_kill_quest_progress(self, monster_id: str) -> str:
        lang = self.player.language
        quest_log = ""

        for quest_id, quest_data in self.player.active_quests.items():
            quest_info = self.cog.quests.get(quest_id)
            if quest_info and quest_info.get("quest_type") == "kill":
                if quest_info.get("target_monster") == monster_id:
                    quest_data["progress"] += 1
                    quest_title = tf(quest_info, "title", lang)
                    quest_log += t(lang, "combat.quest_progress_update", "\n📜 任務進度更新：{title} ({progress}/{target})", title=quest_title, progress=quest_data["progress"], target=quest_info["target_count"])
                    if quest_data["progress"] >= quest_info["target_count"]:
                        quest_log += t(lang, "combat.quest_completed", "\n✅ 任務達成！稍後會有委託人的訊息通知你領取獎勵。")

        # 👇 隱藏任務進度更新（Kill 類型；隱藏任務不需要「接受」，符合等級的玩家都視為已在追蹤）
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
            if quest_info.get("quest_type") != "kill" or quest_info.get("target_monster") != monster_id:
                continue
            hidden_progress[quest_id] = hidden_progress.get(quest_id, 0) + 1

        return quest_log

    def _process_victory(self) -> str:
        lang = self.player.language
        all_monsters = [slot["monster"] for slot in self.view.monster_slots]
        if not all_monsters:
            return ""

        killed_monsters = [slot["monster"] for slot in self.view.monster_slots if not slot.get("fled")]
        fled_monsters = [slot["monster"] for slot in self.view.monster_slots if slot.get("fled")]

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
            quest_log += self._update_kill_quest_progress(monster.get("id"))

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

        dungeon_log = ""
        if primary_monster.get("is_dungeon"):
            self.view._advance_dungeon_floor()
            dungeon_log = "\n" + t(lang, "combat.dungeon_floor_open", "🧗 轟隆隆... 通往深淵地下城第 {floor} 層的通道打開了！", floor=self.player.dungeon_state['floor'])

        log = t(
            lang, "combat.victory_summary",
            "\n🏆 戰鬥勝利！\n獲得了 {exp} 經驗值與 {gold} {money_name}。\n{drop_log}{boss_log}{quest_log}{flee_log}{tower_log}{dungeon_log}",
            exp=total_exp, gold=total_gold, money_name=self.cog.bot.baba.money_name,
            drop_log=drop_log, boss_log=boss_log, quest_log=quest_log, flee_log=flee_log, tower_log=tower_log, dungeon_log=dungeon_log,
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

