"""TRPG 異常狀態系統 — 中毒、燃燒、冰凍、麻痺、睡眠、狂暴等。

對外函式簽名維持不變（process_turn_start / process_monster_status / try_apply_status /
apply_status_to_monster...），但內部已統一為同一套邏輯，分別透過 PlayerCombatant /
MonsterCombatant 操作，避免「玩家版」「怪物版」各寫一份。新增狀態只要在這裡的
_tick_status() 加一個 elif 分支，玩家與怪物會同時套用。

語言：玩家相關函式 (try_apply_status / cure_status / cure_by_item / try_monster_apply_status)
直接從傳入的 player.language 取得語言；只操作 raw dict（沒有 player 物件可取）的函式
(format_status_list / break_sleep_on_damage / apply_status_to_monster / process_monster_status)
則額外帶一個 lang 參數，由呼叫端傳入 self.player.language。
"""
import random
from datetime import datetime

from trpg.entity import PlayerCombatant, MonsterCombatant
from trpg.i18n import t, tf
from trpg.balance import (
    CORROSION_TURNS, CORROSION_MAX_STACKS,
)


def apply_corrosion(status_dict: dict, stacks_add: int, dmg_per_stack: int, turns: int = CORROSION_TURNS) -> int:
    """地下城專屬：對 status_dict（怪物的異常狀態字典）疊加腐蝕層數。
    每層每回合造成固定傷害（不吃 %HP）。層數有上限（CORROSION_MAX_STACKS）——
    沒有上限的話腐蝕流每打一下都在永久加碼 DOT，中後期會滾雪球到把頭目
    兩三回合直接融掉，比任何正面輸出流派都強太多。回傳目前總層數。"""
    cur = status_dict.get("corrosion")
    if cur:
        cur["stacks"] = min(CORROSION_MAX_STACKS, cur.get("stacks", 0) + stacks_add)
        cur["dmg_per_stack"] = max(cur.get("dmg_per_stack", 0), dmg_per_stack)
        cur["turns"] = turns
    else:
        status_dict["corrosion"] = {"turns": turns, "stacks": min(CORROSION_MAX_STACKS, stacks_add), "dmg_per_stack": dmg_per_stack}
    return status_dict["corrosion"]["stacks"]


def format_status_list(status_effects: dict, status_defs: dict, lang: str = "zh") -> str:
    if not status_effects:
        return t(lang, "status.normal", "正常")
    parts = []
    for sid, data in status_effects.items():
        info = status_defs.get(sid, {})
        name = tf(info, "name", lang) or sid
        emoji = info.get("emoji", "❓")
        if sid == "corrosion":
            parts.append(t(lang, "status.list_entry_stacks", "{emoji}{name}x{stacks}", emoji=emoji, name=name, stacks=data.get("stacks", 1)))
        else:
            turns = data.get("turns", 0)
            parts.append(t(lang, "status.list_entry", "{emoji}{name}({turns}回合)", emoji=emoji, name=name, turns=turns))
    sep = ", " if lang == "en" else "、"
    return sep.join(parts)


def get_daily_jester_immunity(player, status_defs: dict) -> str:
    """根據伺服器日期與玩家 ID 生成當日固定的隨機免疫狀態。
    只從「會施加在玩家身上」的狀態抽選——標記 jester_immune=false 的狀態
    （例如地下城專屬、只由玩家施加給怪物的腐蝕）不列入，避免抽到沒用的免疫。"""
    if getattr(player, "accessory", None) != "jester_mask":
        return ""
    if not status_defs:
        return ""

    pool = [sid for sid, info in status_defs.items() if info.get("jester_immune", True)]
    if not pool:
        return ""

    today = datetime.today().strftime("%Y-%m-%d")
    # 利用今天的日期與玩家ID作為種子，確保今天之內每次呼叫都是同一個結果
    rng = random.Random(f"{today}_{player.id}")
    return rng.choice(pool)


def _set_status_entry(status_dict: dict, status_id: str, turns: int, status_defs: dict, source: str, is_player: bool, lang: str = "zh") -> str:
    if status_id not in status_defs:
        return ""

    existing = status_dict.get(status_id, {})
    new_turns = max(existing.get("turns", 0), turns)

    # 紀錄疊加層數 (給燃燒用，重新施加時重置為 1)
    status_dict[status_id] = {"turns": new_turns, "tick": 1}

    info = status_defs[status_id]
    name = tf(info, "name", lang) or status_id
    target_label = t(lang, "status.target_player", "你") if is_player else t(lang, "status.target_enemy", "敵人")
    src = f"{source}使" if source else ""
    return t(
        lang, "status.afflicted", "{emoji} {src}{target}陷入了【{name}】狀態！({turns}回合)",
        emoji=info.get("emoji", ""), src=src, target=target_label, name=name, turns=new_turns,
    )


def try_apply_status(player, status_id: str, turns: int, status_defs: dict, source: str = "") -> str:
    if status_id not in status_defs:
        return ""

    lang = getattr(player, "language", "zh")
    info = status_defs[status_id]
    name = tf(info, "name", lang) or status_id

    # 檢查今天是不是剛好免疫這個狀態
    immune_id = get_daily_jester_immunity(player, status_defs)
    if status_id == immune_id:
        name = tf(status_defs[status_id], "name", lang) or status_id
        return t(lang, "status.jester_immune", "🎭 小丑面具發出詭異笑聲，今日完全免疫了【{name}】！", name=name)

    return _set_status_entry(player.status_effects, status_id, turns, status_defs, source, is_player=True, lang=lang)


def _monster_status_shrug(monster_dict: dict, status_id: str, status_defs: dict, lang: str) -> str:
    """怪物資料的 immunity / resistance 清單裡可以放異常狀態 id（例如骷髏免疫中毒、
    魔王抗麻痺）。回傳阻擋訊息；空字串代表沒有擋下、照常施加。

    這份資料一直存在於 26 隻怪物身上，但引擎過去只拿 resistance 清單判定「屬性傷害」
    相剋，狀態名寫在裡面完全沒有效果——魔王設計上抗中毒/麻痺/冰凍/燃燒，實際上被
    玩家照樣永凍。魔法之眼會把這些清單顯示給玩家看，顯示出來的抗性就必須是真的。
    規則：immunity 內 → 完全免疫；resistance 內 → 50% 機率抵抗。"""
    if not monster_dict:
        return ""
    info = status_defs.get(status_id, {})
    name = tf(info, "name", lang) or status_id
    monster_name = tf(monster_dict, "name", lang) or t(lang, "status.target_enemy", "敵人")
    if status_id in (monster_dict.get("immunity") or []):
        return t(lang, "status.monster_immune", "🛡️ {monster} 完全免疫【{name}】！", monster=monster_name, name=name)
    if status_id in (monster_dict.get("resistance") or []) and random.random() < 0.5:
        return t(lang, "status.monster_resisted", "🛡️ {monster} 抵抗了【{name}】！", monster=monster_name, name=name)
    return ""


def apply_status_to_monster(monster_status_dict: dict, status_id: str, turns: int, status_defs: dict, source: str = "", lang: str = "zh", monster: dict = None) -> str:
    """對敵人施加異常狀態。傳入 monster（怪物定義 dict）才能結算狀態免疫/抵抗；
    不傳則維持舊行為（無條件施加）。"""
    shrug = _monster_status_shrug(monster, status_id, status_defs, lang)
    if shrug:
        return shrug
    return _set_status_entry(monster_status_dict, status_id, turns, status_defs, source, is_player=False, lang=lang)


def apply_status(combatant, status_id: str, turns: int, status_defs: dict, source: str = "") -> str:
    """通用版本：傳入 PlayerCombatant 或 MonsterCombatant，自動決定要不要檢查小丑面具免疫
    （玩家）或狀態免疫/抵抗清單（怪物）。"""
    player = getattr(combatant, "player", None)
    if player is not None:
        return try_apply_status(player, status_id, turns, status_defs, source)
    return apply_status_to_monster(
        combatant.status_effects, status_id, turns, status_defs, source,
        lang=combatant.lang, monster=combatant.monster_dict,
    )





def try_monster_apply_status(player, monster: dict, status_defs: dict) -> str:
    pool = monster.get("status_on_hit")
    chance = monster.get("status_chance", 0)
    if not pool or chance <= 0:
        return ""
    if random.random() > chance:
        return ""

    status_id = random.choice(pool)
    turns = 3 if monster.get("is_boss") else 2
    return try_apply_status(
        player, status_id, turns, status_defs,
        tf(monster, "name", getattr(player, "language", "zh")) or "",
    )


def _tick_status(combatant, status_defs: dict, is_player: bool, lang: str = "zh") -> tuple[str, bool]:
    """每回合開頭結算 DOT 傷害與行動限制，玩家、怪物共用同一套規則。"""
    status_effects = combatant.status_effects
    if not status_effects:
        return "", True

    log_parts = []
    can_act = True
    target_label = t(lang, "status.target_player", "你") if is_player else t(lang, "status.target_enemy", "敵人")

    for sid in list(status_effects.keys()):
        data = status_effects[sid]
        info = status_defs.get(sid, {})
        turns = data.get("turns", 0)

        if sid == "poison":
            dmg = max(1, int(combatant.max_hp * info.get("dot_ratio", 0.05)))
            combatant.hp -= dmg
            log_parts.append(t(lang, "status.poison_tick", "☠️ {target}中毒發作，損失 {dmg} HP", target=target_label, dmg=dmg))

        elif sid == "burn":
            # 漸進式燃燒：tick_ratio% -> 2*tick_ratio% -> ... 最大生命值，上限 max_stacks 層
            max_stacks = info.get("max_stacks", 5)
            tick = min(max_stacks, data.get("tick", 1))
            ratio = info.get("tick_ratio", 0.04) * tick
            dmg = max(1, int(combatant.max_hp * ratio))
            combatant.hp -= dmg
            log_parts.append(t(lang, "status.burn_tick", "🔥 {target}身上的灼燒加劇！(第{tick}層) 損失 {dmg} HP", target=target_label, tick=tick, dmg=dmg))
            status_effects[sid]["tick"] = min(max_stacks, tick + 1)

        # 冰凍：不會讓目標無法行動，而是在行動條（AV）計算時降低速度，
        # 因此這裡不需要每回合的特殊處理（冰凍的減速在 combat.advance_time 套用）。

        elif sid == "paralysis":
            # 麻痺：機率跳過回合 (看臉)
            skip_chance = info.get("skip_chance", 0.5)
            if not is_player:
                if random.random() < skip_chance:
                    can_act = False
                    log_parts.append(t(lang, "status.paralysis_skip", "⚡ {target}身體一陣麻痺，這回合無法控制自己！", target=target_label))

        elif sid == "sleep":
            # 睡眠：100% 無法行動，但受到傷害會立刻清醒（在傷害發生處呼叫 break_sleep_on_damage）
            can_act = False
            log_parts.append(t(lang, "status.sleep", "💤 {target}陷入了沉睡，完全無法行動！", target=target_label))

        elif sid == "corrosion":
            # 地下城專屬腐蝕：層數 × 每層傷害的固定傷害，會一直累積（不吃 %HP）
            stacks = data.get("stacks", 1)
            per = data.get("dmg_per_stack", 4)
            dmg = max(1, stacks * per)
            combatant.hp -= dmg
            log_parts.append(t(lang, "status.corrosion_tick", "🧪 {target}被腐蝕侵蝕，{stacks} 層造成 {dmg} 點傷害！", target=target_label, stacks=stacks, dmg=dmg))

        turns -= 1
        if turns <= 0:
            del status_effects[sid]
            name = tf(info, "name", lang) or info.get("name", sid)
            log_parts.append(t(lang, "status.cleared", "✨ {target}的【{name}】解除了。", target=target_label, name=name))
        else:
            status_effects[sid]["turns"] = turns

    return "\n".join(log_parts), can_act


def process_turn_start(player, status_defs: dict) -> tuple[str, bool]:
    lang = getattr(player, "language", "zh")
    return _tick_status(PlayerCombatant(player, {}, status_defs), status_defs, is_player=True, lang=lang)


def process_monster_status(slot: dict, status_defs: dict, lang: str = "zh") -> tuple[str, bool]:
    """回合結束前，結算怪物身上的 DOT 傷害與行動限制。slot 是 monster_slots 裡的單一格子。"""
    if not slot.get("status"):
        return "", True
    return _tick_status(MonsterCombatant(slot, status_defs, lang), status_defs, is_player=False, lang=lang)


def break_sleep_on_damage(status_dict: dict, lang: str = "zh") -> str:
    """睡眠只要受到傷害就會立刻解除 —— 在每個會造成傷害的地方呼叫。"""
    if "sleep" in status_dict:
        del status_dict["sleep"]
        return t(lang, "status.wake_on_damage", "💤➡️ 受到傷害，瞬間清醒了過來！")
    return ""


def cure_status(player, status_id: str, status_defs: dict) -> str:
    lang = getattr(player, "language", "zh")
    if status_id not in player.status_effects:
        name = tf(status_defs.get(status_id, {}), "name", lang) or status_id
        return t(lang, "status.cure_not_found", "❌ 你並沒有【{name}】狀態。", name=name)
    name = tf(status_defs[status_id], "name", lang) or status_id
    del player.status_effects[status_id]
    return t(lang, "status.cure_success", "✨ 【{name}】已解除！", name=name)


def cure_by_item(player, item_id: str, items: dict, status_defs: dict) -> str:
    lang = getattr(player, "language", "zh")
    item = items.get(item_id, {})
    cures = item.get("cures")
    if not cures:
        return t(lang, "status.cure_item_no_effect", "❌ 這個物品無法解除異常狀態。")

    cured = []
    for sid in cures:
        if sid in player.status_effects:
            cured.append(tf(status_defs.get(sid, {}), "name", lang) or sid)
            del player.status_effects[sid]

    if not cured:
        return t(lang, "status.cure_item_none_active", "❌ 你目前沒有這個藥草能解除的異常狀態。")
    sep = ", " if lang == "en" else "、"
    item_name = tf(item, "name", lang) or item_id
    return t(lang, "status.cure_item_success", "✨ 使用了【{item_name}】，解除了：{cured}", item_name=item_name, cured=sep.join(cured))


def clear_all_status(player):
    player.status_effects.clear()
