"""怪物 AI 行為註冊表 — 每隻怪物依 monster dict 的 "ai" 欄位查表決定預設行動方式，
並疊加「主動技能」引擎：怪物可以在 JSON 設定 active_skills，觸發時先「蓄力」警告玩家一回合，
下一次行動才真正發動效果，讓玩家有機會應對（補血/防禦/打斷）。

monster dict 可選欄位：
- "active_skills": [{"id","name","trigger":{"type":"chance"|"hp_below"|"interval","value":...},"effect":{...}}]
  effect.type 支援：heavy_attack / debuff_atk / debuff_def / debuff_spd / buff_self_atk / buff_self_def / buff_self_spd /
  summon_minion / cast_skill（effect.skill_id 指向 skills.json 任意技能，讓怪物也能用魔法，跟玩家技能共用 execute_skill）
- "phase2": {"hp_below":0.5,"ai":"...","name_suffix":"...","heal_pct":0.1,"transform_text":"...","active_skills":[...]}
  ——HP 低於門檻時觸發一次性轉變（變更 ai/active_skills/名稱並回一點血），等同 BOSS 二階段。
- "revive_once": {"turns":3,"hp_pct":0.3} —— 死亡後倒數幾個時間刻度，自動以該比例HP復活一次（僅一次）。
"""

import random

from trpg_i18n import t, tf


def _plain_attack(combat, slot: dict, log: str, atk_mult: float = 1.0) -> str:
    from trpg_combat import calc_monster_damage, get_player_def, get_player_spd
    from trpg_status import try_monster_apply_status

    lang = combat.player.language
    monster = slot["monster"]
    m_atk = int(monster["atk"] * atk_mult)
    if slot["status"].get("paralysis"):
        m_atk = int(m_atk * combat.cog.status_effects.get("paralysis", {}).get("atk_mult", 0.70))
    m_dmg = calc_monster_damage(m_atk, get_player_def(combat.player, combat.cog.items))

    # 閃避判定
    if combat.is_dodging:
        p_spd = get_player_spd(combat.player)
        m_spd = monster.get("spd", int(10 + combat.player.level * 2.2))
        dodge_chance = min(0.85, max(0.1, 0.3 + (p_spd - m_spd) * 0.015))
        if random.random() < dodge_chance:
            return log + t(lang, "monster_ai.dodge_attack", "\n💨 {name} 發動攻擊，被你靈巧地閃避了！", name=tf(monster, "name", lang))
        else:
            log += t(lang, "monster_ai.dodge_fail", "\n💦 你試圖閃避，但還是被擊中了！")

    # 防禦判定
    if combat.is_defending:
        m_dmg = max(1, m_dmg // 2)
        log += t(lang, "monster_ai.defend_block", "\n🛡️ 防禦姿態擋下了大量傷害！")

    combat.player.current_hp -= m_dmg
    log += t(lang, "monster_ai.attack_hit", "\n🥊 {name} 行動！使你受到了 {dmg} 點傷害。", name=tf(monster, "name", lang), dmg=m_dmg)

    from trpg_status import break_sleep_on_damage
    wake_log = break_sleep_on_damage(combat.player.status_effects, combat.player.language)
    if wake_log:
        log += f"\n{wake_log}"

    status_log = try_monster_apply_status(combat.player, monster, combat.cog.status_effects)
    if status_log:
        log += f"\n{status_log}"

    if combat.player.current_hp <= 0:
        return combat.view.process_death(log, t(lang, "monster_ai.death_attack", "💀 承受不住 {name} 的攻擊，你倒下了...", name=tf(monster, "name", lang)))
    combat.cog.save_players()
    return log


# ---------------------------------------------------------------------------
# 基礎行為原型（沒有觸發主動技能時的預設行動）
# ---------------------------------------------------------------------------

def ai_none(combat, slot, log):
    return _plain_attack(combat, slot, log)


def ai_lifesteal(combat, slot, log):
    """攻擊後依造成傷害的比例回血（吸血蝙蝠、吸血鬼系怪物）。"""
    lang = combat.player.language
    monster = slot["monster"]
    before_hp = combat.player.current_hp
    log = _plain_attack(combat, slot, log)
    dmg_dealt = before_hp - combat.player.current_hp
    if dmg_dealt > 0 and slot["hp"] > 0:
        heal = max(1, int(dmg_dealt * 0.4))
        before = slot["hp"]
        slot["hp"] = min(monster["max_hp"], slot["hp"] + heal)
        actual = slot["hp"] - before
        if actual > 0:
            log += t(lang, "monster_ai.lifesteal", "\n🩸 {name} 吸取了你的血肉，回復了 {actual} HP！", name=tf(monster, "name", lang), actual=actual)
    return log


def ai_berserk_low_hp(combat, slot, log):
    """HP 低於 30% 時攻擊力提升 60%（瀕死反撲）。"""
    lang = combat.player.language
    monster = slot["monster"]
    hp_pct = slot["hp"] / monster["max_hp"] if monster.get("max_hp") else 1
    if 0 < hp_pct < 0.3:
        log = _plain_attack(combat, slot, log, atk_mult=1.6)
        log += t(lang, "monster_ai.berserk_low_hp", "\n🔥 {name} 陷入瀕死狂暴，攻擊力大幅提升！", name=tf(monster, "name", lang))
        return log
    return _plain_attack(combat, slot, log)


def ai_flee_low_hp(combat, slot, log):
    """HP 低於 20% 時先警告「準備逃跑」，給玩家一回合反應；下次行動才真正逃離（不計入擊殺獎勵）。"""
    lang = combat.player.language
    monster = slot["monster"]
    if slot.get("telegraph_flee"):
        slot["telegraph_flee"] = False
        slot["hp"] = 0
        slot["fled"] = True
        return log + t(lang, "monster_ai.flee", "\n🏃 {name} 趁機逃離了戰場！", name=tf(monster, "name", lang))

    hp_pct = slot["hp"] / monster["max_hp"] if monster.get("max_hp") else 1
    if 0 < hp_pct < 0.2 and random.random() < 0.6:
        slot["telegraph_flee"] = True
        return log + t(lang, "monster_ai.flee_telegraph", "\n😰 {name} 嚇得直打哆嗦，似乎想找機會逃跑！（盡快解決牠，否則牠下回合就會逃走）", name=tf(monster, "name", lang))
    return _plain_attack(combat, slot, log)


def ai_self_heal(combat, slot, log):
    """每次行動前有機率先回復自身一部分 HP，再進行攻擊。"""
    lang = combat.player.language
    monster = slot["monster"]
    if slot["hp"] < monster["max_hp"] and random.random() < 0.35:
        heal = max(1, int(monster["max_hp"] * 0.12))
        before = slot["hp"]
        slot["hp"] = min(monster["max_hp"], slot["hp"] + heal)
        actual = slot["hp"] - before
        if actual > 0:
            log += t(lang, "monster_ai.self_heal", "\n✨ {name} 施展了自我治癒，回復了 {actual} HP！", name=tf(monster, "name", lang), actual=actual)
    return _plain_attack(combat, slot, log)


def ai_shield_ally(combat, slot, log):
    """有機率優先治療場上 HP 比例最低的隊友，而非攻擊（肉盾/支援型怪物）。"""
    lang = combat.player.language
    allies = [s for s in combat.view.monster_slots if s is not slot and s["hp"] > 0]
    wounded = [s for s in allies if s["hp"] < s["monster"]["max_hp"]]
    if wounded and random.random() < 0.3:
        target = min(wounded, key=lambda s: s["hp"] / s["monster"]["max_hp"])
        heal = max(1, int(target["monster"]["max_hp"] * 0.18))
        before = target["hp"]
        target["hp"] = min(target["monster"]["max_hp"], target["hp"] + heal)
        actual = target["hp"] - before
        return log + t(lang, "monster_ai.shield_ally_heal", "\n💚 {healer} 守護著同伴，為【{target}】回復了 {actual} HP！", healer=tf(slot["monster"], "name", lang), target=tf(target["monster"], "name", lang), actual=actual)
    return _plain_attack(combat, slot, log)


def ai_multi_hit_flurry(combat, slot, log):
    """有機率連續攻擊兩次（各算一次完整傷害判定，傷害各自降低，避免一次性爆量）。"""
    lang = combat.player.language
    if random.random() < 0.4:
        log = _plain_attack(combat, slot, log, atk_mult=0.65)
        if combat.player.current_hp > 0:
            log += t(lang, "monster_ai.multi_hit_flurry", "\n💢 {name} 速度太快，竟然連續出手了第二次！", name=tf(slot["monster"], "name", lang))
            log = _plain_attack(combat, slot, log, atk_mult=0.65)
        return log
    return _plain_attack(combat, slot, log)


def ai_support_healer(combat, slot, log):
    """專職治療型衍生怪：優先治療場上 HP 比例最低的隊友（含 BOSS 本體），治療量與觸發率都比 shield_ally 更高。"""
    lang = combat.player.language
    allies = [s for s in combat.view.monster_slots if s is not slot and s["hp"] > 0]
    wounded = [s for s in allies if s["hp"] < s["monster"]["max_hp"]]
    if wounded and random.random() < 0.55:
        target = min(wounded, key=lambda s: s["hp"] / s["monster"]["max_hp"])
        heal = max(1, int(target["monster"]["max_hp"] * 0.25))
        before = target["hp"]
        target["hp"] = min(target["monster"]["max_hp"], target["hp"] + heal)
        actual = target["hp"] - before
        return log + t(lang, "monster_ai.support_healer", "\n💚 {healer} 全力施展治療術，為【{target}】回復了 {actual} HP！", healer=tf(slot["monster"], "name", lang), target=tf(target["monster"], "name", lang), actual=actual)
    return _plain_attack(combat, slot, log)


def ai_buffer(combat, slot, log):
    """支援型衍生怪：優先幫場上還沒被強化過的隊友附加攻擊力buff，而非攻擊。"""
    lang = combat.player.language
    allies = [s for s in combat.view.monster_slots if s is not slot and s["hp"] > 0]
    candidates = [s for s in allies if not s.get("stat_mods", {}).get("atk")]
    if candidates and random.random() < 0.35:
        target = max(candidates, key=lambda s: s["monster"].get("atk", 0))
        _apply_monster_stat_mod(target, "atk", 1.35, 3)
        return log + t(lang, "monster_ai.buffer", "\n📯 {buffer} 為【{target}】注入力量，攻擊力提升了！（3回合）", buffer=tf(slot["monster"], "name", lang), target=tf(target["monster"], "name", lang))
    return _plain_attack(combat, slot, log)


def ai_debuffer(combat, slot, log):
    """支援型衍生怪：有機率對玩家施加攻擊力/防禦力/速度其中一項減益，而非直接攻擊。"""
    lang = combat.player.language
    if random.random() < 0.35:
        stat_key, stat_name_key, stat_name_zh = random.choice([
            ("atk_mult", "monster_ai.stat_atk", "攻擊力"),
            ("def_mult", "monster_ai.stat_def", "防禦力"),
            ("spd_mult", "monster_ai.stat_spd", "速度"),
        ])
        stat_name = t(lang, stat_name_key, stat_name_zh)
        combat.player.combat_debuffs[stat_key] = 0.75
        combat.player.combat_debuffs["turns"] = max(combat.player.combat_debuffs.get("turns", 0), 3)
        return log + t(lang, "monster_ai.debuffer", "\n🌀 {name} 對你施加了詛咒，{stat}下降了！（3回合）", name=tf(slot["monster"], "name", lang), stat=stat_name)
    return _plain_attack(combat, slot, log)



def ai_heavy_tank(combat, slot, log):
    # 高防禦，機率替隊友抵擋傷害 (此處用加防禦來簡化)
    lang = combat.player.language
    import random
    if random.random() < 0.3 and slot.get("hp", 0) > 0:
        _apply_monster_stat_mod(slot, "def", 2.0, 2)
        log += t(lang, "monster_ai.heavy_tank", "\n🛡️ {name} 舉起了巨盾，進入防禦姿態！防禦力大幅提升！", name=tf(slot["monster"], "name", lang))
        return log
    return _plain_attack(combat, slot, log)

def ai_kamikaze(combat, slot, log):
    # 血量低於 30% 自爆
    lang = combat.player.language
    hp = slot.get("hp", 0)
    max_hp = slot["monster"]["max_hp"]
    if hp > 0 and hp < max_hp * 0.3:
        import random
        if random.random() < 0.8:  # 80% chance
            dmg = hp
            log += t(lang, "monster_ai.kamikaze_explode", "\n💥 **{name} 體內的魔力失去控制，發生了劇烈自爆！**", name=tf(slot["monster"], "name", lang))
            combat.player.current_hp -= dmg
            log += t(lang, "monster_ai.kamikaze_damage", "\n💥 對你造成了 {dmg} 點真實傷害！", dmg=dmg)
            from trpg_status import break_sleep_on_damage
            wake_log = break_sleep_on_damage(combat.player.status_effects, combat.player.language)
            if wake_log:
                log += f"\n{wake_log}"
            slot["hp"] = 0
            if combat.player.current_hp <= 0:
                return combat.view.process_death(log, t(lang, "monster_ai.death_kamikaze", "💀 承受不住 {name} 的自爆，你被炸死了...", name=tf(slot["monster"], "name", lang)))
            return log
    return _plain_attack(combat, slot, log)

def ai_charge_attack(combat, slot, log):
    # 如果已經在蓄力，則這回合打出 3 倍傷害
    lang = combat.player.language
    if slot.get("is_charging"):
        slot["is_charging"] = False
        log += t(lang, "monster_ai.charge_release", "\n⚡ **{name} 釋放了積蓄的能量！**", name=tf(slot["monster"], "name", lang))
        return _plain_attack(combat, slot, log, atk_mult=3.0)

    # 機率進入蓄力狀態
    import random
    if random.random() < 0.25:
        slot["is_charging"] = True
        log += t(lang, "monster_ai.charge_warning", "\n⚠️ **{name} 正在聚集毀滅性的能量，下一回合即將爆發！**", name=tf(slot["monster"], "name", lang))
        return log

    return _plain_attack(combat, slot, log)

def ai_magic_absorb(combat, slot, log):
    # 機率開啟魔法吸收護盾，如果開啟中，遭受魔法攻擊補血。
    # 這裡我們只在 AI 回合開啟護盾狀態，在 trpg_combat.py 需要判斷這個狀態來改變魔法攻擊邏輯。
    # 由於我們沒有修改戰鬥傷害結算，我們先寫一個會在被打到時由 combat 直接判斷的狀態，或者這回合直接吸血
    lang = combat.player.language
    import random
    if not slot.get("magic_absorb_shield"):
        if random.random() < 0.3:
            slot["magic_absorb_shield"] = 2  # 持續2回合
            log += t(lang, "monster_ai.magic_absorb_on", "\n🌀 {name} 展開了【魔法吸收護盾】！接下來將吸收所有魔法傷害轉化為生命值！", name=tf(slot["monster"], "name", lang))
            return log
    else:
        slot["magic_absorb_shield"] -= 1
        if slot["magic_absorb_shield"] <= 0:
            slot.pop("magic_absorb_shield")
            log += t(lang, "monster_ai.magic_absorb_off", "\n🌀 {name} 的【魔法吸收護盾】消失了。", name=tf(slot["monster"], "name", lang))
    return _plain_attack(combat, slot, log)

def ai_sargeras(combat, slot, log):
    """魔王本體：召喚交給 active_skills 的 summon_imp（每2回合）負責，
    這裡只負責「身為前排時，趁機縮到最後排躲在小鬼身後」的怯戰行為。"""
    lang = combat.player.language
    slots = combat.view.monster_slots
    alive_others = [s for s in slots if s is not slot and s["hp"] > 0]
    if alive_others and slots and slots[0] is slot:
        slots.remove(slot)
        slots.append(slot)
        return log + t(lang, "monster_ai.sargeras_retreat", "\n👹 {name} 冷笑一聲，將小鬼推到了前面擋著，自己退到了最後排！", name=tf(slot["monster"], "name", lang))
    return _plain_attack(combat, slot, log, atk_mult=1.1)


AI_REGISTRY = {
    "none": ai_none,
    "lifesteal": ai_lifesteal,
    "berserk_low_hp": ai_berserk_low_hp,
    "flee_low_hp": ai_flee_low_hp,
    "self_heal": ai_self_heal,
    "shield_ally": ai_shield_ally,
    "multi_hit_flurry": ai_multi_hit_flurry,
    "support_healer": ai_support_healer,
    "buffer": ai_buffer,
    "debuffer": ai_debuffer,
    "heavy_tank": ai_heavy_tank,
    "kamikaze": ai_kamikaze,
    "charge_attack": ai_charge_attack,
    "magic_absorb": ai_magic_absorb,
    "sargeras_ai": ai_sargeras,
}


# ---------------------------------------------------------------------------
# 怪物自身的暫時性數值增減（攻/防/速 buff、debuff）
# ---------------------------------------------------------------------------

def _apply_monster_stat_mod(slot: dict, stat: str, mult: float, turns: int):
    monster = slot["monster"]
    mods = slot.setdefault("stat_mods", {})
    base = mods[stat]["base"] if stat in mods else monster.get(stat, 0)
    monster[stat] = max(1, int(base * mult)) if stat != "def" else max(0, int(base * mult))
    mods[stat] = {"base": base, "turns_left": turns}


def _tick_stat_mods(slot: dict):
    mods = slot.get("stat_mods")
    if not mods:
        return
    monster = slot["monster"]
    for stat in list(mods.keys()):
        mods[stat]["turns_left"] -= 1
        if mods[stat]["turns_left"] <= 0:
            monster[stat] = mods[stat]["base"]
            del mods[stat]


# ---------------------------------------------------------------------------
# BOSS 二階段
# ---------------------------------------------------------------------------

def _maybe_transform_phase2(combat, slot: dict, log: str) -> str:
    lang = combat.player.language
    monster = slot["monster"]
    phase2 = monster.get("phase2")
    if not phase2 or slot.get("phase2_triggered") or slot["hp"] <= 0:
        return log

    hp_pct = slot["hp"] / monster["max_hp"] if monster.get("max_hp") else 1
    if hp_pct > phase2.get("hp_below", 0.5):
        return log

    slot["phase2_triggered"] = True
    if phase2.get("ai"):
        monster["ai"] = phase2["ai"]
    if "active_skills" in phase2:
        monster["active_skills"] = phase2["active_skills"]
        slot["skill_turn_counters"] = {}
    if phase2.get("name_suffix") and phase2["name_suffix"] not in monster["name"]:
        monster["name"] = f"{monster['name']}{phase2['name_suffix']}"
        if monster.get("name_en") and phase2.get("name_suffix_en") and phase2["name_suffix_en"] not in monster["name_en"]:
            monster["name_en"] = f"{monster['name_en']}{phase2['name_suffix_en']}"

    heal_pct = phase2.get("heal_pct", 0)
    if heal_pct > 0:
        slot["hp"] = min(monster["max_hp"], slot["hp"] + int(monster["max_hp"] * heal_pct))

    default_flavor = t(lang, "monster_ai.phase2_default", "{name} 的氣息驟然轉變，進入了更危險的狀態！", name=tf(monster, "name", lang))
    flavor = phase2.get("transform_text", default_flavor)
    if lang == "en" and phase2.get("transform_text_en"):
        flavor = phase2["transform_text_en"]
    return log + f"\n🌀 **{flavor}**"


# ---------------------------------------------------------------------------
# 主動技能引擎：蓄力警告 → 下回合執行
# ---------------------------------------------------------------------------

def _pick_active_skill(slot: dict):
    monster = slot["monster"]
    skills = monster.get("active_skills")
    if not skills:
        return None
    hp_pct = slot["hp"] / monster["max_hp"] if monster.get("max_hp") else 1
    counters = slot.setdefault("skill_turn_counters", {})

    for skill in skills:
        trigger = skill.get("trigger", {})
        ttype = trigger.get("type")
        if ttype == "chance":
            if random.random() < trigger.get("value", 0.2):
                return skill
        elif ttype == "hp_below":
            if hp_pct < trigger.get("value", 0.5):
                return skill
        elif ttype == "interval":
            count = counters.get(skill["id"], 0) + 1
            if count >= trigger.get("value", 4):
                counters[skill["id"]] = 0
                return skill
            counters[skill["id"]] = count
    return None


def _find_minion_def(combat, minion_id: str):
    """召喚目標可能在魔塔/地下城怪物池（含 boss_minions），也可能是目前區域的怪物或區域專屬衍生怪。"""
    from trpg_monster_pool import find_monster_def

    pool_def = find_monster_def(combat.cog.monster_pool, minion_id)
    if pool_def:
        return pool_def, "pool"

    area_data = combat.cog.areas.get(combat.player.current_area, {})
    area_def = area_data.get("boss_minions", {}).get(minion_id) or area_data.get("monsters", {}).get(minion_id)
    if area_def:
        return area_def, "area"
    return None, None


def _execute_summon_minion(combat, slot: dict, skill: dict, log: str) -> str:
    from trpg_monster_pool import instantiate_monster

    lang = combat.player.language
    monster = slot["monster"]
    summon_ids = skill.get("effect", {}).get("summon_ids") or []
    slots = combat.view.monster_slots
    alive_count = sum(1 for s in slots if s["hp"] > 0)

    if not summon_ids or alive_count >= 3:
        return log + t(lang, "monster_ai.summon_no_room", "\n📯 {name} 想召喚援軍，但戰場已經容不下更多敵人了！", name=tf(monster, "name", lang))

    minion_id = random.choice(summon_ids)
    minion_def, source = _find_minion_def(combat, minion_id)
    if not minion_def:
        return log + t(lang, "monster_ai.summon_fail", "\n📯 {name} 的召喚儀式似乎失敗了……", name=tf(monster, "name", lang))

    if source == "pool":
        floor_hint = max(1, monster.get("level", combat.player.level))
        minion = instantiate_monster(minion_def, floor_hint, is_boss=False, floor_scale=0.3)
    else:
        minion = dict(minion_def)
        # Apply scaling based on Boss level for area minions
        boss_level = monster.get("level", combat.player.level)
        area_req_level = combat.cog.areas.get(combat.player.current_area, {}).get("req_level", 1)
        scale = 1.0 + max(0, boss_level - area_req_level) * 0.15
        
        # Scale stats
        minion["max_hp"] = max(1, int(minion.get("base_hp", minion.get("max_hp", 10)) * scale))
        minion["hp"] = minion["max_hp"]
        minion["atk"] = max(1, int(minion.get("base_atk", minion.get("atk", 5)) * scale))
        minion["def"] = max(1, int(minion.get("base_def", minion.get("def", 2)) * scale))
        minion["spd"] = max(1, int(minion.get("base_spd", minion.get("spd", 5)) * scale))

    dead_slot = next((s for s in slots if s["hp"] <= 0 and not s.get("fled") and not s["monster"].get("revive_once")), None)
    if dead_slot:
        # Instead of replacing in place, remove the dead slot and insert at front
        slots.remove(dead_slot)
        slots.insert(0, {"monster": minion, "hp": minion["max_hp"], "av": 0, "status": {}})
    elif len(slots) < 3:
        slots.insert(0, {"monster": minion, "hp": minion["max_hp"], "av": 0, "status": {}})
    else:
        return log + t(lang, "monster_ai.summon_no_room", "\n📯 {name} 想召喚援軍，但戰場已經容不下更多敵人了！", name=tf(monster, "name", lang))

    return log + t(lang, "monster_ai.summon_success", "\n📯 {name} 發動【{skill}】，召喚了【{minion}】加入戰場！", name=tf(monster, "name", lang), skill=tf(skill, "name", lang), minion=tf(minion, "name", lang))


def _execute_cast_skill(combat, slot: dict, effect: dict, log: str) -> str:
    """讓怪物的主動技能直接引用 skills.json 裡任何一個技能（含魔法），
    跟玩家使用技能走同一套 execute_skill 傷害／異常狀態邏輯——怪物想放法術不用再寫第二份公式。"""
    from trpg_combat import execute_skill
    from trpg_entity import PlayerCombatant, MonsterCombatant

    lang = combat.player.language
    monster = slot["monster"]
    spell = combat.cog.skills.get(effect.get("skill_id"))
    if not spell:
        return _plain_attack(combat, slot, log)

    caster = MonsterCombatant(slot, combat.cog.status_effects, combat.player.language)
    target = PlayerCombatant(combat.player, combat.cog.items, combat.cog.status_effects)
    skill_log, _ = execute_skill(caster, [target], spell, combat.cog.status_effects)
    log += t(lang, "monster_ai.cast_skill", "\n🪄 {name} 發動了【{skill}】！\n{skill_log}", name=tf(monster, "name", lang), skill=tf(spell, "name", lang), skill_log=skill_log)

    if combat.player.current_hp <= 0:
        return combat.view.process_death(log, t(lang, "monster_ai.death_cast_skill", "💀 你被 {name} 的【{skill}】擊倒了...", name=tf(monster, "name", lang), skill=tf(spell, "name", lang)))
    combat.cog.save_players()
    return log


def _execute_active_skill(combat, slot: dict, skill: dict, log: str) -> str:
    lang = combat.player.language
    effect = skill.get("effect", {})
    etype = effect.get("type")
    monster = slot["monster"]

    if etype == "heavy_attack":
        log = _plain_attack(combat, slot, log, atk_mult=effect.get("mult", 2.0))
        return log + t(lang, "monster_ai.heavy_attack_hit", "\n💥 【{skill}】命中要害，造成了驚人的傷害！", skill=tf(skill, "name", lang))

    if etype in ("debuff_atk", "debuff_def", "debuff_spd"):
        stat_key = {"debuff_atk": "atk_mult", "debuff_def": "def_mult", "debuff_spd": "spd_mult"}[etype]
        stat_name_key = {"debuff_atk": "monster_ai.stat_atk", "debuff_def": "monster_ai.stat_def", "debuff_spd": "monster_ai.stat_spd"}[etype]
        stat_name_zh = {"debuff_atk": "攻擊力", "debuff_def": "防禦力", "debuff_spd": "速度"}[etype]
        stat_name = t(lang, stat_name_key, stat_name_zh)
        turns = effect.get("turns", 3)
        combat.player.combat_debuffs[stat_key] = effect.get("mult", 0.7)
        combat.player.combat_debuffs["turns"] = max(combat.player.combat_debuffs.get("turns", 0), turns)
        return log + t(lang, "monster_ai.skill_debuff", "\n🌀 {name} 發動【{skill}】，你的{stat}下降了！（{turns}回合）", name=tf(monster, "name", lang), skill=tf(skill, "name", lang), stat=stat_name, turns=turns)

    if etype in ("buff_self_atk", "buff_self_def", "buff_self_spd"):
        stat = etype.rsplit("_", 1)[1]
        stat_name_key = {"atk": "monster_ai.stat_atk", "def": "monster_ai.stat_def", "spd": "monster_ai.stat_spd"}[stat]
        stat_name_zh = {"atk": "攻擊力", "def": "防禦力", "spd": "速度"}[stat]
        stat_name = t(lang, stat_name_key, stat_name_zh)
        turns = effect.get("turns", 3)
        _apply_monster_stat_mod(slot, stat, effect.get("mult", 1.3), turns)
        return log + t(lang, "monster_ai.skill_self_buff", "\n💪 {name} 發動【{skill}】，自身{stat}大幅提升！（{turns}回合）", name=tf(monster, "name", lang), skill=tf(skill, "name", lang), stat=stat_name, turns=turns)

    if etype == "summon_minion":
        return _execute_summon_minion(combat, slot, skill, log)

    if etype == "cast_skill":
        return _execute_cast_skill(combat, slot, effect, log)

    return _plain_attack(combat, slot, log)


def tick_revive(slot: dict, lang: str = "zh") -> str:
    """死亡的怪物若設有 revive_once，每經過一次時間刻度倒數一次，時間到了就以一定比例HP復活（僅限一次）。"""
    if slot["hp"] > 0 or slot.get("fled"):
        return ""
    revive_cfg = slot["monster"].get("revive_once")
    if not revive_cfg or slot.get("revived"):
        return ""

    remaining = slot.get("revive_countdown", revive_cfg.get("turns", 3)) - 1
    if remaining > 0:
        slot["revive_countdown"] = remaining
        return ""

    hp_pct = revive_cfg.get("hp_pct", 0.3)
    slot["hp"] = max(1, int(slot["monster"]["max_hp"] * hp_pct))
    slot["revived"] = True
    slot.pop("revive_countdown", None)
    slot["status"] = {}
    return t(lang, "monster_ai.revive", "\n💀➡️✨ {name} 的屍體竟微微抽動——牠重新站了起來！（恢復至 {hp} HP，僅此一次）", name=tf(slot["monster"], "name", lang), hp=slot["hp"])


def run_monster_ai(combat, slot: dict, log: str) -> str:
    lang = combat.player.language
    slot["turns_acted"] = slot.get("turns_acted", 0) + 1
    _tick_stat_mods(slot)
    log = _maybe_transform_phase2(combat, slot, log)

    if slot.get("status", {}).get("berserk"):
        slot["telegraph"] = None
        mult = combat.cog.status_effects.get("berserk", {}).get("atk_mult", 1.6)
        return _plain_attack(combat, slot, log, atk_mult=mult)

    pending = slot.get("telegraph")
    if pending:
        slot["telegraph"] = None
        return _execute_active_skill(combat, slot, pending, log)

    skill = _pick_active_skill(slot)
    if skill:
        slot["telegraph"] = skill
        return log + t(lang, "monster_ai.skill_telegraph", "\n⚠️ {name} 開始蓄力，準備發動【{skill}】！下回合請做好準備！", name=tf(slot["monster"], "name", lang), skill=tf(skill, "name", lang))

    handler = AI_REGISTRY.get(slot["monster"].get("ai", "none"), ai_none)
    return handler(combat, slot, log)
