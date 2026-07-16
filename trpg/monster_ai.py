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
- "traits": ["pack_hunter", ...] —— 被動特性，可疊加多個並與任何 ai 組合（見 TRAIT_REGISTRY）。
  設計目標：同一族的怪物共用一個「族群招牌特性」（哥布林=群體狩獵、吸血鬼=嗜血、石像=強固...），
  再各自搭配不同的主動 ai，讓每隻怪物打起來都不一樣、但同族之間有明顯的呼應。
  特性有三個掛勾點：turn_start（行動前，可劫持整回合，例如裝死/分裂）、
  atk_mult（普攻傷害倍率修正）、after_attack（普攻命中後的附帶效果，例如吸血/破甲/汲魔）。
- "ai_spells": ["fireball", ...] —— 給 random_caster 用的施法清單（skills.json 的技能 id）。
- "summon_ids": ["worker_bee", ...] —— 給 summoner ai 用的召喚清單。
"""

import random

from trpg.i18n import t, tf
from trpg.balance import DODGE_BASE_CHANCE, DODGE_MIN_CHANCE, DODGE_MAX_CHANCE, DODGE_SPD_FACTOR


def _plain_attack(combat, slot: dict, log: str, atk_mult: float = 1.0) -> str:
    from trpg.combat import calc_monster_damage, get_player_def, get_player_spd
    from trpg.status import try_monster_apply_status

    lang = combat.player.language
    monster = slot["monster"]
    m_atk = int(monster["atk"] * atk_mult)

    # 被動特性：普攻傷害倍率修正（群體狩獵/處刑/復仇...），提示訊息集中收集，命中後才顯示
    trait_notes = []
    for tname in monster.get("traits", ()):
        hook = TRAIT_REGISTRY.get(tname, {}).get("atk_mult")
        if hook:
            mult, note = hook(combat, slot)
            if mult != 1.0:
                m_atk = max(1, int(m_atk * mult))
                if note:
                    trait_notes.append(note)

    if slot["status"].get("paralysis"):
        m_atk = int(m_atk * combat.cog.status_effects.get("paralysis", {}).get("atk_mult", 0.70))
    m_dmg = calc_monster_damage(m_atk, get_player_def(combat.player, combat.cog.items))
    # 閃避判定
    if combat.is_dodging:
        p_spd = get_player_spd(combat.player)
        m_spd = monster.get("spd", int(10 + combat.player.level * 2.2))
        dodge_chance = min(DODGE_MAX_CHANCE, max(DODGE_MIN_CHANCE, DODGE_BASE_CHANCE + (p_spd - m_spd) * DODGE_SPD_FACTOR))
        if random.random() < dodge_chance:
            return log + t(lang, "monster_ai.dodge_attack", "\n💨 {name} 發動攻擊，被你靈巧地閃避了！", name=tf(monster, "name", lang))
        else:
            log += t(lang, "monster_ai.dodge_fail", "\n💦 你試圖閃避，但還是被擊中了！")

    # 防禦判定
    if combat.is_defending:
        m_dmg = max(1, m_dmg // 2)
        log += t(lang, "monster_ai.defend_block", "\n🛡️ 防禦姿態擋下了大量傷害！")

    combat.player.current_hp -= m_dmg
    for note in trait_notes:
        log += f"\n{note}"
    log += t(lang, "monster_ai.attack_hit", "\n🥊 {name} 行動！使你受到了 {dmg} 點傷害。", name=tf(monster, "name", lang), dmg=m_dmg)

    from trpg.status import break_sleep_on_damage
    wake_log = break_sleep_on_damage(combat.player.status_effects, combat.player.language)
    if wake_log:
        log += f"\n{wake_log}"

    status_log = try_monster_apply_status(combat.player, monster, combat.cog.status_effects)
    if status_log:
        log += f"\n{status_log}"

    # 被動特性：命中後的附帶效果（嗜血回血/破甲/魔力汲取...）——玩家死亡時跳過
    if combat.player.current_hp > 0 and m_dmg > 0:
        for tname in monster.get("traits", ()):
            hook = TRAIT_REGISTRY.get(tname, {}).get("after_attack")
            if hook:
                log = hook(combat, slot, m_dmg, log)

    if combat.player.current_hp <= 0:
        return combat.view.process_death(log, t(lang, "monster_ai.death_attack", "💀 承受不住 {name} 的攻擊，你倒下了...", name=tf(monster, "name", lang)))
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
    """支援型衍生怪：攻擊的同時有機率附加減益——原本是「35% 純減益不攻擊、65% 純攻擊
    不減益」，兩者互斥導致純減益的那 35% 回合完全沒有傷害輸出，比同代價的其他 ai 弱一截。
    改成每次都攻擊（倍率略降到 0.85 反映雙重效果），減益機率獨立判定疊加在攻擊上。"""
    lang = combat.player.language
    log = _plain_attack(combat, slot, log, atk_mult=0.85)
    if combat.player.current_hp > 0 and random.random() < 0.35:
        stat_key, stat_name_key, stat_name_zh = random.choice([
            ("atk_mult", "monster_ai.stat_atk", "攻擊力"),
            ("def_mult", "monster_ai.stat_def", "防禦力"),
            ("spd_mult", "monster_ai.stat_spd", "速度"),
        ])
        stat_name = t(lang, stat_name_key, stat_name_zh)
        combat.player.combat_debuffs[stat_key] = 0.75
        combat.player.combat_debuffs["turns"] = max(combat.player.combat_debuffs.get("turns", 0), 3)
        log += t(lang, "monster_ai.debuffer", "\n🌀 {name} 順勢對你施加了詛咒，{stat}下降了！（3回合）", name=tf(slot["monster"], "name", lang), stat=stat_name)
    return log



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
            # 自爆傷害：怪物最大 HP 的 15%，並給予玩家 40% DEF 減傷（非完全真實傷害）
            from trpg.combat import get_player_def
            base_dmg = int(max_hp * 0.15)
            player_def_reduction = int(get_player_def(combat.player, combat.cog.items) * 0.4)
            dmg = max(1, base_dmg - player_def_reduction)
            log += t(lang, "monster_ai.kamikaze_explode", "\n💥 **{name} 體內的魔力失去控制，發生了劇烈自爆！**", name=tf(slot["monster"], "name", lang))
            combat.player.current_hp -= dmg
            log += t(lang, "monster_ai.kamikaze_damage", "\n💥 對你造成了 {dmg} 點真實傷害！", dmg=dmg)
            from trpg.status import break_sleep_on_damage
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

def ai_poison_spitter(combat, slot, log):
    """攻擊之餘有機率朝玩家吐出毒液（劇毒蛛后等）——這個 ai 名字在怪物資料裡
    用了很久，但註冊表裡一直沒有實作，之前默默退化成普通攻擊。"""
    from trpg.status import try_apply_status
    log = _plain_attack(combat, slot, log)
    if combat.player.current_hp > 0 and slot["hp"] > 0 and random.random() < 0.35:
        s_log = try_apply_status(combat.player, "poison", 3, combat.cog.status_effects,
                                 tf(slot["monster"], "name", combat.player.language) or "")
        if s_log:
            log += f"\n{s_log}"
    return log


def ai_evasive(combat, slot, log):
    """行動時有機率殘影閃避（音速蝙蝠等）：短暫進入無實體狀態，下一次受到的傷害
    最多 1 點。同樣是資料裡引用已久、卻從未被實作的 ai。"""
    lang = combat.player.language
    if not slot.get("status", {}).get("intangible") and random.random() < 0.3:
        slot.setdefault("status", {})["intangible"] = {"turns": 1, "tick": 1}
        log += t(lang, "monster_ai.evasive", "\n💨 {name} 的身影變得模糊不清，攻擊彷彿會直接穿過牠！", name=tf(slot["monster"], "name", lang))
        return _plain_attack(combat, slot, log, atk_mult=0.8)
    return _plain_attack(combat, slot, log)


def ai_void_mage(combat, slot, log):
    """虛空法師：有機率以虛空低語沉默玩家（技能全鎖），否則普通攻擊。"""
    from trpg.status import try_apply_status
    if random.random() < 0.3:
        s_log = try_apply_status(combat.player, "silence", 2, combat.cog.status_effects,
                                 tf(slot["monster"], "name", combat.player.language) or "")
        if s_log:
            return log + f"\n{s_log}"
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


def ai_counter_stance(combat, slot, log):
    """反擊架勢：擺出架勢的那回合不攻擊；若玩家在牠下次行動前打了牠，
    下次行動變成 1.8 倍的猛烈反擊——玩家要學會「看到架勢就換目標或防禦」。"""
    lang = combat.player.language
    monster = slot["monster"]
    if slot.pop("counter_stance", False):
        if slot.get("dmg_last_window", 0) > 0:
            log += t(lang, "monster_ai.counter_stance_hit", "\n🥋💥 你上當了！{name} 抓住你出手的破綻，發動了致命反擊！", name=tf(monster, "name", lang))
            return _plain_attack(combat, slot, log, atk_mult=1.8)
        log += t(lang, "monster_ai.counter_stance_idle", "\n🥋 {name} 見你沒有上當，悻悻地收起了架勢。", name=tf(monster, "name", lang))
        return _plain_attack(combat, slot, log)
    if random.random() < 0.3:
        slot["counter_stance"] = True
        return log + t(lang, "monster_ai.counter_stance_enter", "\n🥋 {name} 收起攻勢，擺出了反擊的架勢……（此時攻擊牠將遭到猛烈反擊！）", name=tf(monster, "name", lang))
    return _plain_attack(combat, slot, log)


def ai_bomber(combat, slot, log):
    """炸彈客：點燃引信警告一回合，下回合引爆 2.2 倍傷害並高機率附加燃燒。
    與 charge_attack 的差異：倍率較低但帶 DOT，且引信點燃後無法被打斷（只能殺掉牠或防禦）。"""
    from trpg.status import try_apply_status
    lang = combat.player.language
    monster = slot["monster"]
    if slot.pop("bomb_fuse", False):
        log += t(lang, "monster_ai.bomber_boom", "\n💣💥 炸彈在你面前炸開了！", name=tf(monster, "name", lang))
        log = _plain_attack(combat, slot, log, atk_mult=2.2)
        if combat.player.current_hp > 0 and random.random() < 0.7:
            s_log = try_apply_status(combat.player, "burn", 2, combat.cog.status_effects, tf(monster, "name", lang) or "")
            if s_log:
                log += f"\n{s_log}"
        return log
    if random.random() < 0.3:
        slot["bomb_fuse"] = True
        return log + t(lang, "monster_ai.bomber_fuse", "\n💣 {name} 掏出一顆冒著火花的炸彈，點燃了引信！（下回合爆炸，做好防禦準備！）", name=tf(monster, "name", lang))
    return _plain_attack(combat, slot, log)


def ai_thief(combat, slot, log):
    """盜賊：有機率偷走玩家金幣（贓款計入牠的掉落金額，殺掉就能拿回來）；
    重傷時會警告一回合後帶著贓款逃跑——玩家得在「追殺回本」與「見好就收」間抉擇。"""
    lang = combat.player.language
    monster = slot["monster"]
    if slot.get("telegraph_flee"):
        slot["telegraph_flee"] = False
        slot["hp"] = 0
        slot["fled"] = True
        stolen = slot.get("stolen_gold", 0)
        if stolen > 0:
            return log + t(lang, "monster_ai.thief_flee_gone", "\n💰🏃 {name} 帶著你的 {amount} 枚金幣逃之夭夭了……", name=tf(monster, "name", lang), amount=stolen)
        return log + t(lang, "monster_ai.flee", "\n🏃 {name} 趁機逃離了戰場！", name=tf(monster, "name", lang))

    hp_pct = slot["hp"] / monster["max_hp"] if monster.get("max_hp") else 1
    if 0 < hp_pct < 0.25 and random.random() < 0.6:
        slot["telegraph_flee"] = True
        return log + t(lang, "monster_ai.thief_flee_warn", "\n💰😰 {name} 抱著贓款想要開溜！（下回合就會帶著你的錢逃走，盡快解決牠！）", name=tf(monster, "name", lang))

    if random.random() < 0.3:
        amount = min(combat.cog.get_bank_balance(combat.view.user_id), 15 + monster.get("level", combat.player.level) * 3)
        if amount > 0 and combat.cog.try_spend(combat.view.user_id, combat.player, amount):
            slot["stolen_gold"] = slot.get("stolen_gold", 0) + amount
            monster["money_min"] = monster.get("money_min", 0) + amount
            monster["money_max"] = monster.get("money_max", 0) + amount
            return log + t(lang, "monster_ai.thief_steal", "\n💰 {name} 身手矯健地摸走了你 {amount} 枚金幣！（擊敗牠就能拿回來）", name=tf(monster, "name", lang), amount=amount)
    return _plain_attack(combat, slot, log)


def ai_time_thief(combat, slot, log):
    """時間竊賊：整場最多 3 次，使玩家下一完整回合失去 1 AP。"""
    lang = combat.player.language
    if slot.get("time_steals", 0) < 3 and combat.next_round_ap_penalty < 1 and random.random() < 0.35:
        slot["time_steals"] = slot.get("time_steals", 0) + 1
        combat.next_round_ap_penalty = 1
        return log + t(lang, "monster_ai.time_thief", "\n⏳ {name} 偷走了一段時間：你下回合將失去 1 AP！", name=tf(slot["monster"], "name", lang))
    return _plain_attack(combat, slot, log)


def ai_trickster(combat, slot, log):
    """幻術師：有機率跟另一隻同伴瞬間交換站位（打亂玩家的集火順序），換位時短暫無實體。"""
    lang = combat.player.language
    slots = combat.view.monster_slots
    others = [s for s in slots if s is not slot and s["hp"] > 0]
    if others and random.random() < 0.3:
        other = random.choice(others)
        i, j = slots.index(slot), slots.index(other)
        slots[i], slots[j] = slots[j], slots[i]
        slot.setdefault("status", {})["intangible"] = {"turns": 1, "tick": 1}
        return log + t(lang, "monster_ai.trickster_swap", "\n🃏 {name} 施展幻術，跟【{other}】瞬間交換了位置！", name=tf(slot["monster"], "name", lang), other=tf(other["monster"], "name", lang))
    return _plain_attack(combat, slot, log)


def ai_glass_cannon(combat, slot, log):
    """玻璃大炮：每次都不顧一切地猛攻（1.4 倍），但有 15% 機率用力過猛跌倒空過一回合。"""
    lang = combat.player.language
    if random.random() < 0.15:
        return log + t(lang, "monster_ai.glass_cannon_stumble", "\n💫 {name} 用力過猛，一頭栽在地上，這回合什麼也沒做！", name=tf(slot["monster"], "name", lang))
    log += t(lang, "monster_ai.glass_cannon_smash", "\n⚡ {name} 不顧防禦地全力猛攻！", name=tf(slot["monster"], "name", lang))
    return _plain_attack(combat, slot, log, atk_mult=1.4)


def ai_phase_shifter(combat, slot, log):
    """相位穿梭：固定節奏交替——潛入相位（該回合不攻擊、近乎免傷）→ 暴起重擊（1.5 倍）。
    節奏完全可預測，獎勵讀懂節奏的玩家（防禦重擊那一拍、免傷拍全力輸出無效要忍住）。"""
    lang = combat.player.language
    if slot.get("turns_acted", 1) % 2 == 1:
        slot.setdefault("status", {})["intangible"] = {"turns": 1, "tick": 1}
        return log + t(lang, "monster_ai.phase_shifter_out", "\n🌫️ {name} 的身體潛入了相位夾縫……（本回合牠不會攻擊，且幾乎免疫傷害）", name=tf(slot["monster"], "name", lang))
    log += t(lang, "monster_ai.phase_shifter_strike", "\n🌫️⚔️ {name} 從相位夾縫中暴起突襲！", name=tf(slot["monster"], "name", lang))
    return _plain_attack(combat, slot, log, atk_mult=1.5)


def ai_mass_healer(combat, slot, log):
    """群體治療：有機率一口氣治療全場所有受傷的敵人（含自己）各 10% 最大HP。
    跟單體治療師的差異：多怪戰時必須優先處理，否則清場速度會被整隊回血抵銷。"""
    lang = combat.player.language
    wounded = [s for s in combat.view.monster_slots if s["hp"] > 0 and s["hp"] < s["monster"]["max_hp"]]
    if wounded and random.random() < 0.45:
        for s in wounded:
            heal = max(1, int(s["monster"]["max_hp"] * 0.10))
            s["hp"] = min(s["monster"]["max_hp"], s["hp"] + heal)
        return log + t(lang, "monster_ai.mass_heal", "\n💞 {name} 詠唱了群體治療術，全場敵人的傷勢都恢復了！", name=tf(slot["monster"], "name", lang))
    return _plain_attack(combat, slot, log)


def ai_bodyguard(combat, slot, log):
    """捨身護衛：見到前排同伴重傷（<40%HP）就挺身而出換到前排頂替，並提升自身防禦。"""
    lang = combat.player.language
    slots = combat.view.monster_slots
    front = next((s for s in slots if s["hp"] > 0), None)
    if front is not None and front is not slot and front["hp"] < front["monster"]["max_hp"] * 0.4:
        slots.remove(slot)
        slots.insert(0, slot)
        _apply_monster_stat_mod(slot, "def", 1.5, 2)
        return log + t(lang, "monster_ai.bodyguard_swap", "\n🛡️ {name} 挺身而出，擋在了重傷的同伴身前！（防禦力提升）", name=tf(slot["monster"], "name", lang))
    return _plain_attack(combat, slot, log)


def ai_tempo_howler(combat, slot, log):
    """戰場嚎叫：每第 3 次行動（含第 1 次）發出嚎叫——全體同伴加速、玩家減速，掌控戰場節奏。"""
    lang = combat.player.language
    if slot.get("turns_acted", 1) % 3 == 1:
        for s in combat.view.monster_slots:
            if s["hp"] > 0:
                _apply_monster_stat_mod(s, "spd", 1.15, 3)
        combat.player.combat_debuffs["spd_mult"] = 0.85
        combat.player.combat_debuffs["turns"] = max(combat.player.combat_debuffs.get("turns", 0), 3)
        return log + t(lang, "monster_ai.tempo_howl", "\n🐺 {name} 發出震天的嚎叫！同伴的速度提升了，你的速度下降了！（3回合）", name=tf(slot["monster"], "name", lang))
    return _plain_attack(combat, slot, log)


def ai_curse_weaver(combat, slot, log):
    """詛咒編織：依序輪流削弱玩家的攻擊→防禦→速度（各 -15%，3回合）——
    跟 debuffer（隨機挑一項砍 25%）不同，這是穩定全面地把玩家越纏越弱。"""
    lang = combat.player.language
    if random.random() < 0.4:
        cycle = [
            ("atk_mult", "monster_ai.stat_atk", "攻擊力"),
            ("def_mult", "monster_ai.stat_def", "防禦力"),
            ("spd_mult", "monster_ai.stat_spd", "速度"),
        ]
        idx = slot.get("curse_idx", 0) % 3
        slot["curse_idx"] = idx + 1
        stat_key, stat_name_key, stat_name_zh = cycle[idx]
        stat_name = t(lang, stat_name_key, stat_name_zh)
        combat.player.combat_debuffs[stat_key] = min(combat.player.combat_debuffs.get(stat_key, 1.0), 0.85)
        combat.player.combat_debuffs["turns"] = max(combat.player.combat_debuffs.get("turns", 0), 3)
        return log + t(lang, "monster_ai.curse_weave", "\n🕯️ {name} 編織詛咒纏上你的四肢，{stat}下降了！（3回合）", name=tf(slot["monster"], "name", lang), stat=stat_name)
    return _plain_attack(combat, slot, log)


def ai_random_caster(combat, slot, log):
    """混沌詠唱：有機率從 ai_spells 清單（沒設定就用預設三系初階法術）隨機施放一發法術，
    走玩家同一套 execute_skill 傷害/異常公式——會施法的怪物不用再另寫傷害公式。"""
    spells = slot["monster"].get("ai_spells") or ["fireball", "ice_spear", "thunder_strike"]
    if random.random() < 0.35:
        return _execute_cast_skill(combat, slot, {"skill_id": random.choice(spells)}, log)
    return _plain_attack(combat, slot, log)


def ai_summoner(combat, slot, log):
    """召喚師：戰場未滿時有機率呼叫 summon_ids 裡的援軍——把原本只有 BOSS 主動技能
    才能用的召喚引擎開放給一般怪物當作基礎 ai。"""
    monster = slot["monster"]
    summon_ids = monster.get("summon_ids") or []
    alive_count = sum(1 for s in combat.view.monster_slots if s["hp"] > 0)
    if summon_ids and alive_count < 3 and random.random() < 0.3:
        ritual = {"name": "召喚儀式", "name_en": "Summoning Ritual", "effect": {"summon_ids": summon_ids}}
        return _execute_summon_minion(combat, slot, ritual, log)
    return _plain_attack(combat, slot, log)


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
    "poison_spitter": ai_poison_spitter,
    "evasive": ai_evasive,
    "void_mage": ai_void_mage,
    "assassin": ai_charge_attack,  # 資料裡沿用已久的別名：刺客 = 蓄力重擊型
    "sargeras_ai": ai_sargeras,
    "counter_stance": ai_counter_stance,
    "bomber": ai_bomber,
    "thief": ai_thief,
    "time_thief": ai_time_thief,
    "trickster": ai_trickster,
    "glass_cannon": ai_glass_cannon,
    "phase_shifter": ai_phase_shifter,
    "mass_healer": ai_mass_healer,
    "bodyguard": ai_bodyguard,
    "tempo_howler": ai_tempo_howler,
    "curse_weaver": ai_curse_weaver,
    "random_caster": ai_random_caster,
    "summoner": ai_summoner,
}


# ---------------------------------------------------------------------------
# 被動特性（traits）：與任何 ai 疊加組合的族群機制
#   turn_start(combat, slot, log) -> (log, proceed)  proceed=False 表示劫持了這回合
#   atk_mult(combat, slot) -> (mult, note)           普攻傷害倍率與提示訊息
#   after_attack(combat, slot, dmg, log) -> log      普攻命中後的附帶效果
# ---------------------------------------------------------------------------

def _trait_pack_hunter(combat, slot):
    allies = sum(1 for s in combat.view.monster_slots if s is not slot and s["hp"] > 0)
    if allies <= 0:
        return 1.0, ""
    lang = combat.player.language
    return 1.0 + 0.25 * allies, t(lang, "monster_ai.trait_pack_hunter", "🐺 【群體狩獵】同伴環伺，{name} 的攻勢更加兇猛！", name=tf(slot["monster"], "name", lang))


def _trait_executioner(combat, slot):
    if combat.player.current_hp >= combat.player.max_hp * 0.35:
        return 1.0, ""
    lang = combat.player.language
    return 1.5, t(lang, "monster_ai.trait_executioner", "🩸 【處刑本能】{name} 嗅到了你瀕死的氣息，出手狠辣無比！", name=tf(slot["monster"], "name", lang))


def _trait_opportunist(combat, slot):
    if not combat.player.status_effects:
        return 1.0, ""
    lang = combat.player.language
    return 1.4, t(lang, "monster_ai.trait_opportunist", "😈 【趁虛而入】{name} 盯上了你的異常狀態，攻擊又快又重！", name=tf(slot["monster"], "name", lang))


def _trait_desperado(combat, slot):
    max_hp = slot["monster"].get("max_hp") or 1
    hp_pct = slot["hp"] / max_hp
    if hp_pct >= 0.8:
        return 1.0, ""
    mult = 1.0 + min(0.8, (1.0 - hp_pct) * 0.8)
    lang = combat.player.language
    note = t(lang, "monster_ai.trait_desperado", "🔥 【背水一戰】{name} 傷得越重，打得越狠！", name=tf(slot["monster"], "name", lang)) if hp_pct < 0.4 else ""
    return mult, note


def _trait_avenger(combat, slot):
    """受傷後固定疊攻擊；不再按玩家剛造成的傷害量反擊。"""
    if slot.get("dmg_last_window", 0) <= 0:
        return 1.0, ""
    stacks = min(3, slot.get("avenger_atk_stacks", 0) + 1)
    slot["avenger_atk_stacks"] = stacks
    lang = combat.player.language
    return 1.0 + stacks * 0.15, t(lang, "monster_ai.trait_avenger", "💢 【復仇怒火】{name} 受到傷害後更加憤怒，攻擊力提升！", name=tf(slot["monster"], "name", lang))


def _trait_bloodthirst(combat, slot, dmg, log):
    if slot["hp"] <= 0:
        return log
    monster = slot["monster"]
    heal = max(1, int(dmg * 0.3))
    before = slot["hp"]
    slot["hp"] = min(monster["max_hp"], slot["hp"] + heal)
    actual = slot["hp"] - before
    if actual > 0:
        lang = combat.player.language
        log += t(lang, "monster_ai.trait_bloodthirst", "\n🩸 【嗜血】{name} 舔舐著你的鮮血，回復了 {heal} HP！", name=tf(monster, "name", lang), heal=actual)
    return log


def _trait_shield_breaker(combat, slot, dmg, log):
    lang = combat.player.language
    cur = combat.player.combat_debuffs.get("def_mult", 1.0)
    if cur <= 0.6:
        return log
    combat.player.combat_debuffs["def_mult"] = max(0.6, cur * 0.9)
    combat.player.combat_debuffs["turns"] = max(combat.player.combat_debuffs.get("turns", 0), 3)
    return log + t(lang, "monster_ai.trait_shield_breaker", "\n🔨 【破甲】你的護甲被鑿出了裂痕，防禦力下降了！", name=tf(slot["monster"], "name", lang))


def _trait_mana_burn(combat, slot, dmg, log):
    drain = min(combat.player.current_mp, max(1, int(combat.player.max_mp * 0.06)))
    if drain <= 0:
        return log
    combat.player.current_mp -= drain
    monster = slot["monster"]
    if slot["hp"] > 0:
        slot["hp"] = min(monster["max_hp"], slot["hp"] + drain)
    lang = combat.player.language
    return log + t(lang, "monster_ai.trait_mana_burn", "\n🔮 【魔力汲取】{name} 抽走了你 {mp} 點 MP，化為自己的生命力！", name=tf(monster, "name", lang), mp=drain)


def _trait_dot_leech(combat, slot, log):
    p_status = combat.player.status_effects or {}
    if slot["hp"] > 0 and ("poison" in p_status or "burn" in p_status):
        monster = slot["monster"]
        heal = max(1, int(monster["max_hp"] * 0.06))
        before = slot["hp"]
        slot["hp"] = min(monster["max_hp"], slot["hp"] + heal)
        if slot["hp"] - before > 0:
            lang = combat.player.language
            log += t(lang, "monster_ai.trait_dot_leech", "\n🕸️ 【毒液盛宴】{name} 汲取你體內侵蝕的毒素，回復了 {heal} HP！", name=tf(monster, "name", lang), heal=slot["hp"] - before)
    return log, True


def _trait_regenerator(combat, slot, log):
    monster = slot["monster"]
    if slot["hp"] <= 0 or slot["hp"] >= monster["max_hp"]:
        return log, True
    lang = combat.player.language
    if "burn" in (slot.get("status") or {}):
        return log + t(lang, "monster_ai.trait_regen_burned", "\n🔥 傷口被烈焰灼燒，{name} 的超再生失效了！", name=tf(monster, "name", lang)), True
    heal = max(1, int(monster["max_hp"] * monster.get("regen_pct", 0.08)))
    before = slot["hp"]
    slot["hp"] = min(monster["max_hp"], slot["hp"] + heal)
    if slot["hp"] - before > 0:
        log += t(lang, "monster_ai.trait_regenerator", "\n💚 【超再生】{name} 的傷口以肉眼可見的速度癒合，回復了 {heal} HP！", name=tf(monster, "name", lang), heal=slot["hp"] - before)
    return log, True


def _trait_wrath_stacker(combat, slot, log):
    stacks = slot.get("rage_stacks", 0)
    if stacks >= 5:
        return log, True
    monster = slot["monster"]
    if "rage_base_atk" not in slot:
        slot["rage_base_atk"] = monster.get("atk", 1)
    stacks += 1
    slot["rage_stacks"] = stacks
    monster["atk"] = max(1, int(slot["rage_base_atk"] * (1.0 + 0.15 * stacks)))
    lang = combat.player.language
    return log + t(lang, "monster_ai.trait_wrath_stack", "\n😤 【怒意滋長】{name} 的怒火節節攀升，攻擊力提升至 +{pct}%！", name=tf(monster, "name", lang), pct=15 * stacks), True


def _trait_fortify(combat, slot, log):
    stacks = slot.get("fortify_stacks", 0)
    if stacks >= 5:
        return log, True
    monster = slot["monster"]
    if "fortify_base_def" not in slot:
        slot["fortify_base_def"] = monster.get("def", 0)
    stacks += 1
    slot["fortify_stacks"] = stacks
    monster["def"] = max(0, int(slot["fortify_base_def"] * (1.0 + 0.10 * stacks)) + stacks)
    lang = combat.player.language
    return log + t(lang, "monster_ai.trait_fortify", "\n🪨 【石化強固】{name} 的軀殼越戰越硬，防禦力提升至 +{pct}%！", name=tf(monster, "name", lang), pct=10 * stacks), True


_ELEM_CYCLE = ("fire", "ice", "thunder")
_ELEM_DISPLAY = {
    "fire": ("🔥", "monster_ai.elem_fire", "火"),
    "ice": ("❄️", "monster_ai.elem_ice", "冰"),
    "thunder": ("⚡", "monster_ai.elem_thunder", "雷"),
}


def _trait_elemental_shifter(combat, slot, log):
    monster = slot["monster"]
    idx = slot.get("elem_cycle_idx", random.randrange(len(_ELEM_CYCLE)))
    slot["elem_cycle_idx"] = (idx + 1) % len(_ELEM_CYCLE)
    attuned = _ELEM_CYCLE[idx]
    weak = _ELEM_CYCLE[(idx + 1) % len(_ELEM_CYCLE)]
    # 一律用「賦值」而非就地修改：monster dict 是 start_combat 的淺拷貝，
    # weakness/resistance list 仍與 JSON 原始資料共用參照，append 會污染區域資料。
    monster["weakness"] = [weak]
    monster["resistance"] = [attuned]
    lang = combat.player.language
    emoji, elem_key, elem_zh = _ELEM_DISPLAY[weak]
    elem_name = f"{emoji}{t(lang, elem_key, elem_zh)}"
    return log + t(lang, "monster_ai.trait_elemental_shift", "\n🌈 【元素變換】{name} 的能量核心轉換了相位——現在的弱點是{elem}！", name=tf(monster, "name", lang), elem=elem_name), True


def _trait_splitter(combat, slot, log):
    """首次跌破 50% HP 時分裂出一隻縮小版的自己（佔用當回合行動，僅一次，分裂體不會再分裂）。"""
    monster = slot["monster"]
    max_hp = monster.get("max_hp") or 1
    if slot.get("has_split") or slot["hp"] <= 0 or slot["hp"] >= max_hp * 0.5:
        return log, True
    slots = combat.view.monster_slots
    if sum(1 for s in slots if s["hp"] > 0) >= 3:
        return log, True
    slot["has_split"] = True
    child = dict(monster)
    child["max_hp"] = max(1, int(max_hp * 0.4))
    child["atk"] = max(1, int(monster.get("atk", 1) * 0.8))
    child["exp"] = int(monster.get("exp", 0) * 0.3)
    child["money_min"] = int(monster.get("money_min", 0) * 0.3)
    child["money_max"] = int(monster.get("money_max", 0) * 0.3)
    child["traits"] = [tr for tr in monster.get("traits", ()) if tr != "splitter"]
    child.pop("active_skills", None)
    child.pop("phase2", None)
    child.pop("revive_once", None)
    slots.insert(0, {"monster": child, "hp": child["max_hp"], "av": 0, "status": {}, "has_split": True})
    lang = combat.player.language
    return log + t(lang, "monster_ai.trait_split", "\n🫧 【分裂】{name} 猛地一顫，分裂出了另一隻【{child}】！", name=tf(monster, "name", lang), child=tf(child, "name", lang)), False


def _trait_play_dead(combat, slot, log):
    """首次跌破 30% HP 時倒地裝死：接下來 2 次行動一動不動（近乎免傷），
    第 3 次行動暴起 2 倍偷襲並回復 20% HP（僅一次）。玩家該學會：別浪費輸出在「屍體」上。"""
    lang = combat.player.language
    monster = slot["monster"]
    if slot.get("playing_dead"):
        slot["playing_dead"] -= 1
        if slot["playing_dead"] > 0:
            slot.setdefault("status", {})["intangible"] = {"turns": 1, "tick": 1}
            return log + t(lang, "monster_ai.trait_play_dead_still", "\n🎭 {name} 依然癱在地上毫無動靜……", name=tf(monster, "name", lang)), False
        slot.pop("playing_dead", None)
        slot["hp"] = min(monster["max_hp"], slot["hp"] + int(monster["max_hp"] * 0.2))
        log += t(lang, "monster_ai.trait_play_dead_burst", "\n🎭💥 {name} 突然暴起偷襲！原來牠一直在裝死！", name=tf(monster, "name", lang))
        return _plain_attack(combat, slot, log, atk_mult=2.0), False

    max_hp = monster.get("max_hp") or 1
    if not slot.get("played_dead") and 0 < slot["hp"] < max_hp * 0.3:
        slot["played_dead"] = True
        slot["playing_dead"] = 2
        slot.setdefault("status", {})["intangible"] = {"turns": 1, "tick": 1}
        return log + t(lang, "monster_ai.trait_play_dead_start", "\n🎭 {name} 轟然倒地，一動也不動……牠真的死了嗎？", name=tf(monster, "name", lang)), False
    return log, True


TRAIT_REGISTRY = {
    # 傷害倍率型（族群招牌：哥布林/狼群/鷹身女妖/龍族/蜂群）
    "pack_hunter": {"atk_mult": _trait_pack_hunter},
    "executioner": {"atk_mult": _trait_executioner},
    "opportunist": {"atk_mult": _trait_opportunist},
    "desperado": {"atk_mult": _trait_desperado},
    "avenger": {"atk_mult": _trait_avenger},
    # 命中後附帶型（吸血鬼/惡魔/虛空系）
    "bloodthirst": {"after_attack": _trait_bloodthirst},
    "shield_breaker": {"after_attack": _trait_shield_breaker},
    "mana_burn": {"after_attack": _trait_mana_burn},
    # 行動前結算型（蜘蛛/再生系/獸人/魔像/元素系）
    "dot_leech": {"turn_start": _trait_dot_leech},
    "regenerator": {"turn_start": _trait_regenerator},
    "wrath_stacker": {"turn_start": _trait_wrath_stacker},
    "fortify": {"turn_start": _trait_fortify},
    "elemental_shifter": {"turn_start": _trait_elemental_shifter},
    # 劫持整回合型（史萊姆/骷髏系的一次性大招）
    "splitter": {"turn_start": _trait_splitter},
    "play_dead": {"turn_start": _trait_play_dead},
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
    from trpg.monster_pool import find_monster_def

    pool_def = find_monster_def(combat.cog.monster_pool, minion_id)
    if pool_def:
        return pool_def, "pool"

    area_data = combat.cog.areas.get(combat.player.current_area, {})
    area_def = area_data.get("boss_minions", {}).get(minion_id) or area_data.get("monsters", {}).get(minion_id)
    if area_def:
        return area_def, "area"
    return None, None


def _execute_summon_minion(combat, slot: dict, skill: dict, log: str) -> str:
    from trpg.monster_pool import instantiate_monster

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
        # 情境旗標跟著召喚者走：魔塔/地下城/鬥技場的戰鬥裡召喚出來的援軍也屬於
        # 同一個情境，勝利結算（樓層推進、地下城封印、輪次推進）依賴這些旗標。
        for ctx_flag in ("is_tower", "is_dungeon", "is_colosseum"):
            if monster.get(ctx_flag):
                minion[ctx_flag] = True
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
        minion["def"] = max(0, int(minion.get("base_def", minion.get("def", 0)) * scale))
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
    from trpg.combat import execute_skill
    from trpg.entity import PlayerCombatant, MonsterCombatant

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

    if etype == "inflict_status":
        # 對玩家施加任意異常狀態（例如沉默）——走 try_apply_status，所以抗性減免
        # 與小丑面具免疫照常生效。
        from trpg.status import try_apply_status
        s_log = try_apply_status(
            combat.player, effect.get("status_id", ""), effect.get("turns", 2),
            combat.cog.status_effects, f"【{tf(skill, 'name', lang)}】",
        )
        if s_log:
            return log + f"\n{s_log}"
        return _plain_attack(combat, slot, log)

    if etype == "self_status":
        # 對自己掛狀態（例如無實體）——直接寫進 slot["status"]，回合數照一般狀態倒數。
        sid = effect.get("status_id", "")
        if sid:
            slot.setdefault("status", {})[sid] = {"turns": effect.get("turns", 2), "tick": 1}
            sdef = combat.cog.status_effects.get(sid, {})
            s_name = tf(sdef, "name", lang) or sid
            return log + t(lang, "monster_ai.self_status", "\n{emoji} {name} 發動【{skill}】，進入了【{status}】狀態！（{turns}回合）",
                           emoji=sdef.get("emoji", "✨"), name=tf(monster, "name", lang), skill=tf(skill, "name", lang),
                           status=s_name, turns=effect.get("turns", 2))
        return _plain_attack(combat, slot, log)

    if etype == "divine_shield":
        # 聖盾（靈感來自爐石）：完全抵銷下一次受到的傷害。已有聖盾時改為普攻。
        if not slot.get("divine_shield"):
            slot["divine_shield"] = True
            return log + t(lang, "monster_ai.divine_shield_up", "\n🛡️ {name} 發動【{skill}】，一層神聖的護盾包覆了牠！（完全抵銷下一次傷害）",
                           name=tf(monster, "name", lang), skill=tf(skill, "name", lang))
        return _plain_attack(combat, slot, log)

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


def run_monster_ai(combat, slot: dict, log: str, round_start: bool = True) -> str:
    lang = combat.player.language
    if round_start:
        slot["turns_acted"] = slot.get("turns_acted", 0) + 1
        # Damage caps and turn-start traits reset/tick once per complete round,
        # never once per AP action.
        slot["dmg_last_window"] = slot.get("dmg_taken_since_act", 0)
        slot["dmg_taken_since_act"] = 0
        _tick_stat_mods(slot)
        log = _maybe_transform_phase2(combat, slot, log)

        for tname in slot["monster"].get("traits", ()):
            hook = TRAIT_REGISTRY.get(tname, {}).get("turn_start")
            if hook:
                log, proceed = hook(combat, slot, log)
                if not proceed:
                    slot["_skip_remaining_ap"] = True
                    return log

    if slot.get("status", {}).get("berserk"):
        slot["telegraph"] = None
        mult = combat.cog.status_effects.get("berserk", {}).get("atk_mult", 1.6)
        return _plain_attack(combat, slot, log, atk_mult=mult)

    # 沉默對怪物同樣有效：封鎖主動技能（含正在蓄力中的），只能普通攻擊
    if slot.get("status", {}).get("silence"):
        if slot.get("telegraph"):
            slot["telegraph"] = None
            log += t(lang, "monster_ai.silence_interrupt", "\n🤐 {name} 被沉默了，蓄力中的技能被打斷！", name=tf(slot["monster"], "name", lang))
        return _plain_attack(combat, slot, log)

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
