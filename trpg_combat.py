"""TRPG 戰鬥系統 — 集中處理攻擊、技能、掉落、異常狀態與勝負判定。"""

import random

from datetime import datetime



from trpg_status import (

    process_turn_start,

    try_apply_status,

    cure_by_item,

    clear_all_status,

    activate_jester_immunity,

    apply_status_to_monster,

    break_sleep_on_damage,

)

from trpg_stats import (

    recalc_player_stats,

    get_potion_heal_target,

)





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
    if getattr(player, "accessory", "") == "schrodinger_watch":
        if random.random() < 0.6:
            return dmg * 2, f"⏱️ 【薛丁格的懷錶】發動！傷害翻倍！\n{base_msg}"
        else:
            return max(1, dmg // 2), f"⏱️ 【薛丁格的懷錶】反噬！傷害減半！\n{base_msg}"
    return dmg, base_msg

def get_elemental_multiplier(attack_element: str, monster: dict) -> tuple[float, str]:
    """判定屬性相剋，回傳 (傷害倍率, 提示訊息)"""
    if not attack_element or attack_element in ["physical", "none"]:
        return 1.0, ""
    
    weaknesses = monster.get("weakness", [])
    resistances = monster.get("resistance", [])
    immunities = monster.get("immunity", [])
    
    if attack_element in weaknesses:
        return 1.5, "🌟 【屬性克制】效果拔群！"
    elif attack_element in resistances:
        return 0.75, "🛡️ 【屬性抵抗】效果微弱..."
    elif attack_element in immunities:
        return 0.0, "👻 【屬性免疫】完全無效！"
    return 1.0, ""

def calc_magic_damage(magic: int, defense: int, base_power: int, def_pierce: float = 0.5, magic_scaling: float = 1.0) -> int:

    effective_def = int(defense * (1 - def_pierce))

    dmg = max(1, int(base_power + (magic * magic_scaling) - effective_def))

    return int(dmg * random.uniform(0.90, 1.20))





def calc_monster_damage(monster_atk: int, player_def: int) -> int:

    dmg = max(1, monster_atk - player_def)

    return int(dmg * random.uniform(0.9, 1.1))




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
            log = self.view.process_death(log, "💀 異常狀態將你折磨至死...")
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
                    log += tick_revive(slot)
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
        status_log, m_can_act = process_monster_status(slot, self.cog.status_effects)
        if status_log: log += f"\n{status_log}"
        if slot["hp"] <= 0 or not m_can_act: return log

        return run_monster_ai(self, slot, log)

    def defend(self) -> str:
        dot_log, can_act = self._player_turn_start()
        if self.player.current_hp <= 0: return dot_log
        log = f"{dot_log}\n" if dot_log else ""
        if not can_act: return self.advance_time(log)
        
        self.is_defending = True
        log += "🛡️ 你舉起武器採取防禦姿態，準備迎接衝擊！"
        return self.advance_time(log)

    def dodge(self) -> str:
        dot_log, can_act = self._player_turn_start()
        if self.player.current_hp <= 0: return dot_log
        log = f"{dot_log}\n" if dot_log else ""
        if not can_act: return self.advance_time(log)
        
        self.is_dodging = True
        log += "💨 你全神貫注地盯著敵人，準備進行閃避！"
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
        ele_mult, ele_msg = get_elemental_multiplier(attack_elem, self.monster)
        
        spd_scale = weapon.get("spd_scaling", 0.0)
        p_spd = get_player_spd(self.player)
        multiplier = 1.0 + p_spd * spd_scale
        
        # 傳遞屬性與速度倍率給攻擊計算
        base_dmg, base_msg = self._do_physical_hit(multiplier=multiplier, ele_mult=ele_mult) 
        p_dmg, hit_msg = apply_schrodinger(self.player, base_dmg, base_msg)
        
        log += f"⚔️ 你攻擊了 {self.monster['name']}，{hit_msg}"
        self.monster_hp -= p_dmg
        wake_log = break_sleep_on_damage(self.monster_status)
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
            return self.advance_time(log + "\n💨 你試圖逃跑，但身體不聽使喚！")

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
                return log + "\n🏃 你成功逃跑了！但地下城危機四伏，你只能一路逃回村莊。"
            
            self.view.build_main_menu()
            return log + "\n🏃 你化作一陣風，成功甩開了怪物逃回村里。"

        return self.advance_time(log + "\n💨 逃跑失敗！你的速度不夠快，被攔截了！")
    

    def _apply_weapon_on_hit(self, log: str) -> str:
        weapon_id = self.player.weapon
        if not weapon_id:
            return log
        weapon = self.cog.items.get(weapon_id, {})
        if weapon.get("on_hit_status") and random.random() < weapon.get("on_hit_chance", 0.25):
            turns = weapon.get("on_hit_status_turns", 2)
            s_log = apply_status_to_monster(
                self.monster_status,
                weapon["on_hit_status"],
                turns,
                self.cog.status_effects,
                f"【{weapon.get('name', '武器')}】",
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
                log += f"\n✨ 聖光回湧，回復 {actual} HP！"
        return log

    def _do_physical_hit(self, multiplier: float = 1.0, crit_bonus: float = 0.0, ele_mult: float = 1.0) -> tuple[int, str]:
        p_atk = get_player_atk(self.player, self.cog.items, self.cog.status_effects)
        p_dmg = calc_physical_damage(p_atk, self.monster["def"], multiplier)
        
        # 👇 套用屬性倍率
        p_dmg = max(1, int(p_dmg * ele_mult))
        
        crit_rate = 0.1 + crit_bonus
        if random.random() < crit_rate:
            p_dmg = int(p_dmg * 1.6)
            return p_dmg, f"💥 暴擊！造成 {p_dmg} 點傷害！"
        return p_dmg, f"⚔️ 造成 {p_dmg} 點傷害。"

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
        skill = self.cog.skills.get(skill_id)
        if not skill: return "❌ 未知的技能。"
        if skill.get("type") == "passive": return "❌ 被動技能無法主動施放。"
        if skill_id not in getattr(self.player, "equipped_skills", []): return "❌ 技能未裝備，無法使用。"

        req_lv = skill.get("req_level", 1)
        if self.player.level < req_lv:
            return f"❌ 需要 Lv.{req_lv} 才能使用【{skill['name']}】。"

        if self.skill_cds.get(skill_id, 0) > 0:
            return f"⏳ 【{skill['name']}】冷卻中！（剩餘 {self.skill_cds[skill_id]} 回合）"

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
            return log + f"❌ MP 不足！需要 {mp_cost} 點，目前只有 {self.player.current_mp} 點。"
        if actual_hp_cost > 0:
            if self.player.current_hp <= actual_hp_cost:
                return log + f"❌ HP 不足！【{skill['name']}】需要獻祭 {actual_hp_cost} 點生命，你會把自己抽乾的！"
            
        if skill.get("cd", 0) > 0: self.skill_cds[skill_id] = skill["cd"]

        p_atk = get_player_atk(self.player, self.cog.items, self.cog.status_effects)
        p_magic = get_player_magic(self.player, self.cog.items, self.cog.status_effects)
        skill_type = skill.get("type", "physical")
        hits = skill.get("hits", 1)
        multiplier = skill.get("power_multiplier", 1.0)
        base_power = skill.get("base_power", 0)
        magic_scaling = skill.get("magic_scaling", 1.0)
        total_dmg = 0
        hit_logs = []

        if mp_cost > 0: self.player.current_mp -= mp_cost
        if actual_hp_cost > 0:
            self.player.current_hp -= actual_hp_cost
            log += f"🩸 你殘忍地獻祭了自己 {actual_hp_cost} 點生命值！\n"

        if skill_type == "support":
            if skill_id == "heal_light":
                heal = int(self.player.max_hp * 0.20 + 50)
            else:
                heal = skill.get("heal_amount", 20)
            before = self.player.current_hp
            self.player.current_hp = min(self.player.max_hp, self.player.current_hp + heal)
            log += f"✨ 【{skill['name']}】回復了 {self.player.current_hp - before} 點 HP！"
        elif skill_type == "flee":
            flee_chance = skill.get("flee_chance", 0.85)
            if random.random() < flee_chance:
                from trpg_status import clear_all_status
                clear_all_status(self.player)
                self._clear_battle_state()
                self.view.build_main_menu()
                return log + f"💨 使用了【{skill['name']}】，化作一團黑影成功脫離戰鬥！"
            else:
                return log + f"💦 嘗試使用逃跑，卻被 {self.monster['name']} 識破！" + self.advance_time("") # 👈 修正

        else:
            # 👇 抓取技能屬性與目標類型（front=只打前排／back=打後排／all=打全體，預設 front）
            skill_elem = skill.get("element", "magic") # 如果沒寫預設無屬性魔法
            target_type = skill.get("target_type", "front")
            targets = self._resolve_skill_targets(target_type)

            if not targets:
                log += "❌ 沒有可以攻擊的目標。"
            else:
                target_blocks = []
                for slot in targets:
                    monster = slot["monster"]
                    
                    if slot.get("magic_absorb_shield", 0) > 0 and skill_type == "magic":
                        # 吸滿魔法傷害
                        dmg = calc_magic_damage(p_magic, monster["def"], base_power, skill.get("def_pierce", 0.5), magic_scaling)
                        heal = max(1, int(dmg * hits))
                        slot["hp"] = min(monster["max_hp"], slot.get("hp", monster["max_hp"]) + heal)
                        target_blocks.append(f"🌀 {monster['name']} 的護盾吸收了魔法！回復了 {heal} HP！")
                        continue

                    ele_mult, ele_msg = get_elemental_multiplier(skill_elem, monster)
                    t_dmg = 0
                    hit_logs = []

                    for i in range(hits):
                        # 👇 燃燒生命換取傷害的特殊技能：傷害 = 實際扣除的 HP * 技能專屬倍率
                        if skill.get("hp_scaling_multiplier"):
                            base_dmg = int(actual_hp_cost * skill["hp_scaling_multiplier"])
                            dmg = max(1, int(base_dmg * ele_mult))
                        elif skill_type == "magic":
                            dmg = calc_magic_damage(p_magic, monster["def"], base_power, skill.get("def_pierce", 0.5), magic_scaling)
                            dmg = max(1, int(dmg * ele_mult))
                        else:
                            dmg = calc_physical_damage(p_atk, monster["def"], multiplier)
                            dmg = max(1, int(dmg * ele_mult))

                        # 物理跟 HP 獻祭流都可以暴擊
                        if skill_type != "magic" and random.random() < (0.12 + skill.get("crit_bonus", 0)):
                            dmg = int(dmg * 1.5)
                            hit_logs.append(f"  第{i + 1}擊暴擊 {dmg} 點！")
                        else:
                            hit_logs.append(f"  第{i + 1}擊 {dmg} 點")
                        t_dmg += dmg

                    slot["hp"] -= t_dmg
                    total_dmg += t_dmg

                    block = f"對 {monster['name']} 造成 {t_dmg} 點傷害！"
                    if ele_msg: block += f"\n   ↳ {ele_msg}"
                    if hits > 1: block += "\n" + "\n".join(hit_logs)

                    wake_log = break_sleep_on_damage(slot["status"])
                    if wake_log:
                        block += f"\n{wake_log}"

                    apply_status = skill.get("apply_status")
                    if apply_status:
                        s_log = apply_status_to_monster(
                            slot["status"], apply_status, skill.get("status_turns", 2), self.cog.status_effects, f"【{skill['name']}】"
                        )
                        if s_log:
                            block += f"\n{s_log}"

                    target_blocks.append(block)

                if len(targets) == 1:
                    log += f"✨ 【{skill['name']}】{target_blocks[0]}"
                else:
                    log += f"✨ 【{skill['name']}】命中了 {len(targets)} 個目標！\n" + "\n".join(target_blocks)

        if self._all_monsters_dead():
            return log + self._process_victory()

        return self.advance_time(log) # 👈 修正：統一交給時間條推進



    def use_potion(self, item_id: str) -> str:
        if self.player.inventory.get(item_id, 0) <= 0:
            return f"❌ 你包包裡沒有這個藥水了！"

        item_data = self.cog.items.get(item_id, {})
        heal_target = get_potion_heal_target(item_data, item_id)
        dot_log, can_act = self._player_turn_start()
        if self.player.current_hp <= 0: return dot_log
        log = f"{dot_log}\n" if dot_log else ""

        if heal_target == "mp" and self.player.current_mp >= self.player.max_mp:
            return log + "❓ 魔力已經滿了，別浪費藥水。"
        if heal_target == "hp" and self.player.current_hp >= self.player.max_hp:
            return log + "❓ 生命值已經滿了，別浪費藥水。"

        self.player.inventory[item_id] -= 1
        if self.player.inventory[item_id] <= 0: del self.player.inventory[item_id]

        if "heal_percent" in item_data:
            heal = int(getattr(self.player, f"max_{heal_target}") * item_data["heal_percent"])
        else:
            heal = item_data.get("heal", 50)
            
        setattr(self.player, f"current_{heal_target}", min(getattr(self.player, f"max_{heal_target}"), getattr(self.player, f"current_{heal_target}") + heal))
        log += f"🧪 你喝下藥水，回復了 {heal} 點{'魔力' if heal_target == 'mp' else '生命值'}。"

        if not can_act: log += "\n（麻痺/冰凍中，無法閃避反擊！）"
        return self.advance_time(log) # 👈 修正

    def use_cure_item(self, item_id: str) -> str:
        if self.player.inventory.get(item_id, 0) <= 0: return "❌ 背包裡沒有這個物品。"
        dot_log, can_act = self._player_turn_start()
        if self.player.current_hp <= 0: return dot_log
        log = f"{dot_log}\n" if dot_log else ""

        self.player.inventory[item_id] -= 1
        if self.player.inventory[item_id] <= 0: del self.player.inventory[item_id]

        log += cure_by_item(self.player, item_id, self.cog.items, self.cog.status_effects)
        if not can_act: log += "\n（本回合仍受異常影響，但已解除狀態。）"
        return self.advance_time(log) # 👈 修正




    def _gold_and_exp_for(self, monster: dict) -> tuple[int, int]:
        min_gold = monster.get("money_min", 0)
        max_gold = monster.get("money_max", 0)
        if max_gold < min_gold:
            max_gold = min_gold
        return random.randint(min_gold, max_gold), monster.get("exp", 0)

    def _roll_drops_for(self, monster: dict) -> str:
        drop_log = ""
        for item_id, rate in monster.get("drops", {}).items():
            if random.random() < rate:
                self.player.inventory[item_id] = self.player.inventory.get(item_id, 0) + 1
                item_name = self.cog.items.get(item_id, {}).get("name", item_id)
                drop_log += f"🎁 幸運獲得掉落物：{item_name}\n"
        return drop_log

    def _handle_boss_kill_rewards(self, monster: dict) -> str:
        if not monster.get("is_boss"):
            return ""
        # 魔塔/地下城的 BOSS 樓層只是難度層，獎勵已經由各自的里程碑/樓層推進邏輯處理，
        # 不套用「區域 BOSS 每日鎖定 + 卷軸首殺」這套只為野外區域 BOSS 設計的獎勵。
        if monster.get("is_tower") or monster.get("is_dungeon"):
            return ""

        today_str = datetime.today().strftime("%Y-%m-%d")
        self.player.daily_boss_kills[self.player.current_area] = today_str
        log = "👑 區域 BOSS 討伐成功！今日已無法再次挑戰。\n"

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
            scroll_name = self.cog.items.get(dropped_scroll, {}).get("name", dropped_scroll)
            log += f"🎁 討伐 BOSS 獎勵！獲得技能卷軸：{scroll_name} "
            if is_first_kill:
                log += "(✨ 首殺首通確定獎勵！)\n"
                killed_bosses.append(area_id)
                self.player.killed_bosses = killed_bosses
            else:
                log += "(⚡ 每日挑戰隨機獲得！)\n"
        return log

    def _update_kill_quest_progress(self, monster_id: str) -> str:
        quest_log = ""

        for quest_id, quest_data in self.player.active_quests.items():
            quest_info = self.cog.quests.get(quest_id)
            if quest_info and quest_info.get("quest_type") == "kill":
                if quest_info.get("target_monster") == monster_id:
                    quest_data["progress"] += 1
                    quest_log += f"\n📜 任務進度更新：{quest_info['title']} ({quest_data['progress']}/{quest_info['target_count']})"
                    if quest_data["progress"] >= quest_info["target_count"]:
                        quest_log += "\n✅ 任務達成！稍後會有委託人的訊息通知你領取獎勵。"

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
            names = "、".join(m["name"] for m in fled_monsters)
            flee_log = f"\n🏃 {names} 趁亂逃離了戰場，沒有獲得牠們的擊殺獎勵。"

        # 魔塔/地下城是整場戰鬥共享的情境旗標，用第一隻怪物的標記判斷即可
        primary_monster = all_monsters[0]

        tower_log = ""
        if primary_monster.get("is_tower"):
            self.player.tower_floor += 1
            self.view.tower_safe_room_visited = False  # 👈 這行是關鍵，重置狀態讓下一層有休息室
            tower_log = f"\n🧗 轟隆隆... 通往第 {self.player.tower_floor} 層的階梯緩緩降下了！"

            # Milestone rewards logic
            completed_floor = self.player.tower_floor - 1
            milestones = {
                10: {"trophy": "🥉 銅魔箱勳章", "gold": 5000, "item": None},
                25: {"trophy": "🥈 銀魔箱勳章", "gold": 0, "item": "mystery_power_ring"},
                50: {"trophy": "🥇 金魔箱勳章", "gold": 0, "item": "mystery_void_blade"},
                75: {"trophy": "💎 鑽石魔箱勳章", "gold": 0, "item": "immortal_totem"},
                99: {"trophy": "👑 冠軍魔箱勳章", "gold": 0, "item": "scroll_divine_thunder"}
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
                        gift_name = self.cog.items.get(gift_item, {}).get("name", gift_item)
                        item_msg = f" 與【{gift_name}】x1"

                    if not hasattr(self.player, "trophies"):
                        self.player.trophies = []
                    self.player.trophies.append(milestone_data["trophy"])

                    gold_msg = f"、💰 {milestone_data['gold']}金幣" if milestone_data.get("gold", 0) > 0 else ""
                    tower_log += f"\n🏆 **【魔塔里程碑達成！】**\n你征服了魔塔第 {completed_floor} 層！獲得了【{milestone_data['trophy']}】{gold_msg}{item_msg}！"

        dungeon_log = ""
        if primary_monster.get("is_dungeon"):
            self.view._advance_dungeon_floor()
            dungeon_log = f"\n🧗 轟隆隆... 通往深淵地下城第 {self.player.dungeon_state['floor']} 層的通道打開了！"

        log = f"\n🏆 戰鬥勝利！\n獲得了 {total_exp} 經驗值與 {total_gold} {self.cog.bot.baba.money_name}。\n{drop_log}{boss_log}{quest_log}{flee_log}{tower_log}{dungeon_log}"

        if lvl_up:
            log += f"\n🌟 升級了！你提升到了 Lv.{self.player.level}！"

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

