"""TRPG 異常狀態系統 — 中毒、燃燒、冰凍、麻痺等。"""
import random
from datetime import datetime

def format_status_list(status_effects: dict, status_defs: dict) -> str:
    if not status_effects:
        return "正常"
    parts = []
    for sid, data in status_effects.items():
        info = status_defs.get(sid, {})
        name = info.get("name", sid)
        emoji = info.get("emoji", "❓")
        turns = data.get("turns", 0)
        parts.append(f"{emoji}{name}({turns}回合)")
    return "、".join(parts)

def get_daily_jester_immunity(player, status_defs: dict) -> str:
    """根據伺服器日期與玩家 ID 生成當日固定的隨機免疫狀態。"""
    if getattr(player, "accessory", None) != "jester_mask":
        return ""
    if not status_defs:
        return ""
    
    today = datetime.today().strftime("%Y-%m-%d")
    # 利用今天的日期與玩家ID作為種子，確保今天之內每次呼叫都是同一個結果
    rng = random.Random(f"{today}_{player.id}")
    return rng.choice(list(status_defs.keys()))

# 保留原本的函式防止其他舊代碼報錯 (現在不需要手動紀錄日期了)
def activate_jester_immunity(player):
    pass

def try_apply_status(player, status_id: str, turns: int, status_defs: dict, source: str = "") -> str:
    if status_id not in status_defs:
        return ""

    # 檢查今天是不是剛好免疫這個狀態
    immune_id = get_daily_jester_immunity(player, status_defs)
    if status_id == immune_id:
        return f"🎭 小丑面具發出詭異笑聲，今日完全免疫了【{status_defs[status_id]['name']}】！"

    existing = player.status_effects.get(status_id, {})
    new_turns = max(existing.get("turns", 0), turns)
    
    # 紀錄疊加層數 (給燃燒用，重新施加時重置為 1)
    tick = 1
    player.status_effects[status_id] = {"turns": new_turns, "tick": tick}

    info = status_defs[status_id]
    src = f"{source}使" if source else ""
    return f"{info['emoji']} {src}你陷入了【{info['name']}】狀態！({new_turns}回合)"


def _status_resist_factor(res: int) -> float:
    return min(0.6, res * 0.02)


def _dot_reduction_factor(res: int) -> float:
    return min(0.5, res * 0.015)


def try_monster_apply_status(player, monster: dict, status_defs: dict) -> str:
    pool = monster.get("status_on_hit")
    chance = monster.get("status_chance", 0)
    if not pool or chance <= 0:
        return ""
    res = getattr(player, "base_res", 0)
    effective_chance = chance * (1 - _status_resist_factor(res))
    if random.random() > effective_chance:
        return ""

    status_id = random.choice(pool)
    turns = 3 if monster.get("is_boss") else 2
    return try_apply_status(player, status_id, turns, status_defs, monster.get("name", ""))

def process_turn_start(player, status_defs: dict) -> tuple[str, bool]:
    if not player.status_effects:
        return "", True

    log_parts = []
    can_act = True

    for sid in list(player.status_effects.keys()):
        data = player.status_effects[sid]
        info = status_defs.get(sid, {})
        turns = data.get("turns", 0)

        res = getattr(player, "base_res", 0)
        dot_reduce = _dot_reduction_factor(res)

        if sid == "poison":
            dmg = max(1, int(player.max_hp * info.get("dot_ratio", 0.05)))
            dmg = max(1, int(dmg * (1 - dot_reduce)))
            player.current_hp = max(0, player.current_hp - dmg)
            log_parts.append(f"☠️ 中毒發作，損失 {dmg} HP")

        elif sid == "burn":
            # 漸進式燃燒：2% -> 4% -> 6% 最大生命值，上限 5 層 (10%)
            tick = min(5, data.get("tick", 1))
            ratio = 0.02 * tick
            dmg = max(1, int(player.max_hp * ratio))
            dmg = max(1, int(dmg * (1 - dot_reduce)))
            player.current_hp = max(0, player.current_hp - dmg)
            log_parts.append(f"🔥 灼燒加劇！(第{tick}層) 損失 {dmg} HP")
            player.status_effects[sid]["tick"] = min(5, tick + 1)

        elif sid == "freeze":
            # 冰凍：100% 無法行動，且會流失部分 MP
            can_act = False
            mp_drain = max(1, int(player.max_mp * 0.1))
            player.current_mp = max(0, player.current_mp - mp_drain)
            log_parts.append(f"🥶 冰凍刺骨！你被凍結無法行動，且流失了 {mp_drain} 點 MP！")

        elif sid == "paralysis":
            # 麻痺：50% 機率跳過回合 (看臉)
            skip_chance = info.get("skip_chance", 0.5)
            if random.random() < skip_chance:
                can_act = False
                log_parts.append(f"⚡ 身體一陣麻痺！你這回合無法控制自己！")

        turns -= 1
        if turns <= 0:
            del player.status_effects[sid]
            log_parts.append(f"✨ 【{info.get('name', sid)}】解除了。")
        else:
            player.status_effects[sid]["turns"] = turns

    return "\n".join(log_parts), can_act

def apply_status_to_monster(monster_status_dict: dict, status_id: str, turns: int, status_defs: dict, source: str = "") -> str:
    """對敵人施加異常狀態"""
    if status_id not in status_defs:
        return ""
    
    existing = monster_status_dict.get(status_id, {})
    new_turns = max(existing.get("turns", 0), turns)
    
    # 給燃燒疊層用 (重新施加時重置為 1)
    tick = 1
    monster_status_dict[status_id] = {"turns": new_turns, "tick": tick}
    
    info = status_defs[status_id]
    return f"✨ {source}使敵人陷入了【{info['name']}】狀態！({new_turns}回合)"

def process_monster_status(combat_obj, status_defs: dict) -> tuple[str, bool]:
    """回合結束前，結算怪物身上的 DOT 傷害與行動限制"""
    if not getattr(combat_obj, "monster_status", None):
        return "", True

    log_parts = []
    can_act = True
    monster_status = combat_obj.monster_status

    for sid in list(monster_status.keys()):
        data = monster_status[sid]
        info = status_defs.get(sid, {})
        turns = data.get("turns", 0)

        if sid == "poison":
            dmg = max(1, int(combat_obj.monster["max_hp"] * info.get("dot_ratio", 0.05)))
            combat_obj.monster_hp -= dmg
            log_parts.append(f"☠️ 敵人中毒發作，損失 {dmg} HP")

        elif sid == "burn":
            tick = min(5, data.get("tick", 1))
            ratio = 0.02 * tick
            dmg = max(1, int(combat_obj.monster["max_hp"] * ratio))
            combat_obj.monster_hp -= dmg
            log_parts.append(f"🔥 敵人身上的灼燒加劇！(第{tick}層) 損失 {dmg} HP")
            monster_status[sid]["tick"] = min(5, tick + 1)

        elif sid == "freeze":
            can_act = False
            log_parts.append(f"🥶 敵人被冰塊凍結，無法行動！")

        elif sid == "paralysis":
            skip_chance = info.get("skip_chance", 0.5)
            if random.random() < skip_chance:
                can_act = False
                log_parts.append(f"⚡ 敵人因麻痺而抽搐，無法行動！")

        turns -= 1
        if turns <= 0:
            del monster_status[sid]
            log_parts.append(f"✨ 敵人的【{info.get('name', sid)}】解除了。")
        else:
            monster_status[sid]["turns"] = turns

    return "\n".join(log_parts), can_act

def cure_status(player, status_id: str, status_defs: dict) -> str:
    if status_id not in player.status_effects:
        return f"❌ 你並沒有【{status_defs.get(status_id, {}).get('name', status_id)}】狀態。"
    name = status_defs[status_id]["name"]
    del player.status_effects[status_id]
    return f"✨ 【{name}】已解除！"

def cure_by_item(player, item_id: str, items: dict, status_defs: dict) -> str:
    item = items.get(item_id, {})
    cures = item.get("cures")
    if not cures:
        return "❌ 這個物品無法解除異常狀態。"

    cured = []
    for sid in cures:
        if sid in player.status_effects:
            cured.append(status_defs.get(sid, {}).get("name", sid))
            del player.status_effects[sid]

    if not cured:
        return "❌ 你目前沒有這個藥草能解除的異常狀態。"
    return f"✨ 使用了【{item['name']}】，解除了：{'、'.join(cured)}"

def clear_all_status(player):
    player.status_effects.clear()