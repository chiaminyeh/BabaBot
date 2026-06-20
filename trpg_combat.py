"""TRPG 戰鬥系統 — 集中處理攻擊、技能、掉落、異常狀態與勝負判定。"""

import random

from datetime import datetime



from trpg_status import (

    process_turn_start,

    try_monster_apply_status,

    try_apply_status,

    cure_by_item,

    clear_all_status,

    activate_jester_immunity,

    apply_status_to_monster,

)

from trpg_stats import (

    recalc_player_stats,

    get_potion_heal_target,

)





def exp_to_next_level(level: int) -> int:

    return int(50 * (level ** 1.8))





def get_player_atk(player, items: dict) -> int:

    return player.base_atk





def get_player_def(player, items: dict) -> int:

    return player.base_def





def get_player_magic(player, items: dict) -> int:

    return getattr(player, "base_magic", 0)





def get_sell_price(item_id: str, items: dict) -> int:

    item = items.get(item_id, {})

    if "sell_price" in item:

        return item["sell_price"]

    return max(1, item.get("price", 0) // 2)





def calc_physical_damage(atk: int, defense: int, multiplier: float = 1.0) -> int:

    dmg = max(1, int((atk - defense) * multiplier))

    return int(dmg * random.uniform(0.85, 1.15))

def get_player_magic(player, items: dict) -> int:
    return getattr(player, "base_int", 0) # 改抓智力

def apply_schrodinger(player, dmg: int, base_msg: str) -> tuple[int, str]:
    if getattr(player, "accessory", "") == "schrodinger_watch":
        if random.random() < 0.5:
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

def calc_magic_damage(magic: int, defense: int, multiplier: float, def_pierce: float = 0.5) -> int:

    effective_def = int(defense * (1 - def_pierce))

    dmg = max(1, int((magic - effective_def) * multiplier))

    return int(dmg * random.uniform(0.90, 1.20))





def calc_monster_damage(monster_atk: int, player_def: int) -> int:

    dmg = max(1, monster_atk - player_def)

    return int(dmg * random.uniform(0.9, 1.1))




class TRPGCombat:

    """綁定 TRPGGameView，處理所有戰鬥回合邏輯。"""



    def __init__(self, view):

        self.view = view
        self.skill_cds = {}
        self.monster_status = {}
        self.player_av = 100
        self.monster_av = 0
        self.is_defending = False
        self.is_dodging = False



    def _clear_battle_state(self):
        self.skill_cds.clear()
        self.monster_status.clear()
        self.player_av = 100
        self.monster_av = 0
        self.is_defending = False
        self.is_dodging = False



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



    def _player_turn_start(self) -> tuple[str, bool]:
        # 輪到玩家時，重置上一回合的防禦與閃避
        self.is_defending = False
        self.is_dodging = False
        
        for sid in list(self.skill_cds.keys()):
            if self.skill_cds[sid] > 0: self.skill_cds[sid] -= 1
            if self.skill_cds[sid] <= 0: del self.skill_cds[sid]

        log, can_act = process_turn_start(self.player, self.cog.status_effects)
        if self.player.current_hp <= 0:
            log = self.view.process_death(log, "💀 異常狀態將你折磨至死...")
            return log, False
        return log, can_act
    
    def advance_time(self, log: str) -> str:
        """推進時間條，處理怪物回合，直到玩家 AV 滿 100"""
        self.player_av -= 100
        p_spd = max(5, getattr(self.player, "base_spd", 10))
        m_base = 15 if self.monster.get("is_boss") else 10
        m_spd = max(5, self.monster.get("spd", int(m_base + self.player.level * 2.2)))

        # 當玩家 AV 不滿 100 時，雙方根據速度賽跑
        while self.player_av < 100:
            self.player_av += p_spd
            self.monster_av += m_spd
            
            # 如果怪物滿 100 了，牠就行動！(速度如果差一倍，這裡就會觸發兩次以上)
            while self.monster_av >= 100:
                self.monster_av -= 100
                if self.monster_hp > 0 and self.player.current_hp > 0:
                    log = self._monster_act(log)
                if self.player.current_hp <= 0: return log
                if self.monster_hp <= 0: return log + self._process_victory()
        return log


    def _monster_act(self, log: str) -> str:
        from trpg_status import process_monster_status
        status_log, m_can_act = process_monster_status(self, self.cog.status_effects)
        if status_log: log += f"\n{status_log}"
        if self.monster_hp <= 0 or not m_can_act: return log

        m_dmg = calc_monster_damage(self.monster["atk"], get_player_def(self.player, self.cog.items))

        # 閃避判定
        if self.is_dodging:
            p_spd = getattr(self.player, "base_spd", 10)
            m_spd = self.monster.get("spd", int(10 + self.player.level * 2.2))
            # 閃避率依賴速度差
            dodge_chance = min(0.85, max(0.1, 0.3 + (p_spd - m_spd) * 0.015))
            if random.random() < dodge_chance:
                return log + f"\n💨 {self.monster['name']} 發動攻擊，被你靈巧地閃避了！"
            else:
                log += "\n💦 你試圖閃避，但還是被擊中了！"
        
        # 防禦判定
        if self.is_defending:
            m_dmg = max(1, m_dmg // 2)
            log += "\n🛡️ 防禦姿態擋下了大量傷害！"

        self.player.current_hp -= m_dmg
        log += f"\n🥊 {self.monster['name']} 行動！使你受到了 {m_dmg} 點傷害。"
        
        status_log = try_monster_apply_status(self.player, self.monster, self.cog.status_effects)
        if status_log: log += f"\n{status_log}"

        if self.player.current_hp <= 0:
            return self.view.process_death(log, f"💀 承受不住 {self.monster['name']} 的攻擊，你倒下了...")
        self.cog.save_players()
        return log

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
        
        # 傳遞屬性倍率給攻擊計算
        base_dmg, base_msg = self._do_physical_hit(ele_mult=ele_mult) 
        p_dmg, hit_msg = apply_schrodinger(self.player, base_dmg, base_msg)

        spd_scale = weapon.get("spd_scaling", 0.0)
        p_spd = getattr(self.player, "base_spd", 10)
        
        # 計算傷害並過懷錶
        p_atk = get_player_atk(self.player, self.cog.items)
        base_dmg, base_msg = self._do_physical_hit() # 原本的方法裡面記得補傳參數
        
        # (因為篇幅，請記得在原本算物理與魔法傷害的地方加上 apply_schrodinger)
        p_dmg, hit_msg = apply_schrodinger(self.player, base_dmg, base_msg)
        
        log += f"⚔️ 你攻擊了 {self.monster['name']}，{hit_msg}"
        self.monster_hp -= p_dmg
        log = self._apply_weapon_on_hit(log)

        if self.monster_hp <= 0:
            return log + self._process_victory()

        return self.advance_time(log) # 👈 行動結束，交給時間流逝

    def attempt_flee(self) -> str:
        dot_log, can_act = self._player_turn_start()
        if self.player.current_hp <= 0: return dot_log
        log = f"{dot_log}\n" if dot_log else ""

        if not can_act:
            self.cog.save_players()
            return self.advance_time(log + "\n💨 你試圖逃跑，但身體不聽使喚！")

        p_spd = getattr(self.player, "base_spd", 10)
        m_spd = self.monster.get("spd", int(10 + self.player.level * 2.2))
        flee_chance = min(0.95, max(0.2, 0.4 + (p_spd - m_spd) * 0.015))

        if random.random() < flee_chance:
            from trpg_status import clear_all_status
            clear_all_status(self.player)
            self._clear_battle_state()
            self.view.build_main_menu()
            return log + "🏃 你化作一陣風，成功甩開了怪物逃回村里。"

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
        p_atk = get_player_atk(self.player, self.cog.items)
        p_dmg = calc_physical_damage(p_atk, self.monster["def"], multiplier)
        
        # 👇 套用屬性倍率
        p_dmg = max(1, int(p_dmg * ele_mult))
        
        crit_rate = 0.1 + crit_bonus
        if random.random() < crit_rate:
            p_dmg = int(p_dmg * 1.6)
            return p_dmg, f"💥 暴擊！造成 {p_dmg} 點傷害！"
        return p_dmg, f"⚔️ 造成 {p_dmg} 點傷害。"

    def use_skill(self, skill_id: str) -> str:
        skill = self.cog.skills.get(skill_id)
        if not skill: return "❌ 未知的技能。"
        if skill.get("type") == "passive": return "❌ 被動技能無法主動施放。"

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

        p_atk = get_player_atk(self.player, self.cog.items)
        p_magic = get_player_magic(self.player, self.cog.items)
        skill_type = skill.get("type", "physical")
        hits = skill.get("hits", 1)
        multiplier = skill.get("power_multiplier", 1.0)
        total_dmg = 0
        hit_logs = []

        if mp_cost > 0: self.player.current_mp -= mp_cost
        if actual_hp_cost > 0:
            self.player.current_hp -= actual_hp_cost
            log += f"🩸 你殘忍地獻祭了自己 {actual_hp_cost} 點生命值！\n"

        if skill_type == "support":
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
            # 👇 抓取技能屬性
            skill_elem = skill.get("element", "magic") # 如果沒寫預設無屬性魔法
            ele_mult, ele_msg = get_elemental_multiplier(skill_elem, self.monster)

            for i in range(hits):
                # 👇 新增這段判斷：如果是燃燒生命換取傷害的特殊技能
                if skill.get("hp_scaling_multiplier"):
                    # 傷害 = 實際扣除的 HP * 技能專屬倍率
                    base_dmg = int(actual_hp_cost * skill["hp_scaling_multiplier"])
                    dmg = max(1, int(base_dmg * ele_mult))
                elif skill_type == "magic":
                    dmg = calc_magic_damage(p_magic, self.monster["def"], multiplier, skill.get("def_pierce", 0.5))
                    dmg = max(1, int(dmg * ele_mult))
                else:
                    dmg = calc_physical_damage(p_atk, self.monster["def"], multiplier)
                    dmg = max(1, int(dmg * ele_mult))
                
                # 物理跟 HP 獻祭流都可以暴擊
                if skill_type != "magic" and random.random() < (0.12 + skill.get("crit_bonus", 0)):
                    dmg = int(dmg * 1.5)
                    hit_logs.append(f"  第{i + 1}擊暴擊 {dmg} 點！")
                else:
                    hit_logs.append(f"  第{i + 1}擊 {dmg} 點")
                total_dmg += dmg

            self.monster_hp -= total_dmg
            detail = "\n".join(hit_logs) if hits > 1 else ""
            log += f"✨ 【{skill['name']}】對 {self.monster['name']} 造成 {total_dmg} 點傷害！"
            if ele_msg: log += f"\n   ↳ {ele_msg}"
            if detail: log += f"\n{detail}"

            apply_status = skill.get("apply_status")
            if apply_status:
                s_log = apply_status_to_monster(
                    self.monster_status, apply_status, skill.get("status_turns", 2), self.cog.status_effects, f"【{skill['name']}】"
                )
                log += f"\n{s_log}"

        if self.monster_hp <= 0:
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




    def _process_victory(self) -> str:
        if not self.monster:
            return ""

        # 👇 加上防呆機制，如果在 JSON 沒寫就預設為 0
        min_gold = self.monster.get("money_min", 0)
        max_gold = self.monster.get("money_max", 0)
        
        # 確保 max 不會小於 min，避免 random.randint 報錯
        if max_gold < min_gold:
            max_gold = min_gold
            
        gold = random.randint(min_gold, max_gold)
        exp = self.monster.get("exp", 0)



        uid = int(self.view.user_id)

        current_bal = self.cog.bank.get(uid, [0])[0]

        self.cog.bank[uid] = (current_bal + gold, self.cog.bank.get(uid, (0, False))[1])

        self.cog.bot.baba.refresh_bank_file()



        lvl_up = self.player.add_exp(exp, self.cog.items)

        self.player.stats["monsters_killed"] = self.player.stats.get("monsters_killed", 0) + 1

        clear_all_status(self.player)



        drop_log = ""

        for item_id, rate in self.monster.get("drops", {}).items():

            if random.random() < rate:

                self.player.inventory[item_id] = self.player.inventory.get(item_id, 0) + 1

                item_name = self.cog.items.get(item_id, {}).get("name", item_id)

                drop_log += f"🎁 幸運獲得掉落物：{item_name}\n"



        if self.monster.get("is_boss"):

            today_str = datetime.today().strftime("%Y-%m-%d")

            self.player.daily_boss_kills[self.player.current_area] = today_str

            drop_log += "👑 區域 BOSS 討伐成功！今日已無法再次挑戰。\n"



        quest_log = ""

        monster_id = self.monster.get("id") or self.view.active_monster_id

        for quest_id, quest_data in self.player.active_quests.items():

            quest_info = self.cog.quests.get(quest_id)

            if quest_info and quest_info.get("quest_type") == "kill":

                if quest_info.get("target_monster") == monster_id:

                    quest_data["progress"] += 1

                    quest_log += f"\n📜 任務進度更新：{quest_info['title']} ({quest_data['progress']}/{quest_info['target_count']})"

                    if quest_data["progress"] >= quest_info["target_count"]:

                        quest_log += "\n✅ 任務完成！請回村莊找 NPC 領取獎勵！"

        # 👇 新增：如果是魔塔怪物，勝利後爬升一層
        tower_log = ""
        if self.monster.get("is_tower"):
            self.player.tower_floor += 1
            self.view.tower_safe_room_visited = False  # 👈 這行是關鍵，重置狀態讓下一層有休息室
            tower_log = f"\n🧗 轟隆隆... 通往第 {self.player.tower_floor} 層的階梯緩緩降下了！"

        log = f"\n🏆 戰鬥勝利！\n獲得了 {exp} 經驗值與 {gold} {self.cog.bot.baba.money_name}。\n{drop_log}{quest_log}"

        if lvl_up:

            log += f"\n🌟 升級了！你提升到了 Lv.{self.player.level}！"



        self.cog.save_players()

        self._clear_battle_state()

        self.view.in_battle = False

        self.view.active_monster = None

        self.view.build_main_menu()

        return log

