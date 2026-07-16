"""upgrade_menu.py — 技能升級介面（米酥學院強化技能功能）。

顯示玩家已裝備技能的目前等級、熟練度進度、下一等級的數值預覽，
以及花費金幣直接強化的費用與按鈕。

升到下一級所需金幣（對應計畫書）：
    Lv.1 → Lv.2: 500 金幣
    Lv.2 → Lv.3: 1500 金幣
    Lv.3 → Lv.4: 3000 金幣
    Lv.4 → Lv.5: 6000 金幣
"""

import discord

from trpg.i18n import t, tf
from trpg.skill_progression import (
    MAX_SKILL_LEVEL,
    normalize_skill_level,
    get_proficiency_requirement,
    get_upgrade_cost_with_discount,
    get_skill_effect_multiplier,
    get_skill_cost_multiplier,
    validate_paid_upgrade,
    apply_paid_upgrade,
    get_manual_cost,
    validate_manual_upgrade,
    apply_manual_upgrade,
)

def _skill_level(player, skill_id: str) -> int:
    return normalize_skill_level((getattr(player, "skill_levels", None) or {}).get(skill_id, 1))

def _skill_usage(player, skill_id: str) -> int:
    usage = (getattr(player, "skill_usage", None) or {}).get(skill_id, 0)
    try:
        return max(0, int(usage))
    except (ValueError, TypeError):
        return 0


def _format_upgrade_panel(player, skills_data: dict, lang: str) -> str:
    """組出目前所有已裝備技能的升級摘要文字。"""
    equipped = getattr(player, "equipped_skills", []) or []
    if not equipped:
        return t(lang, "upgrade.no_skills_equipped", "你尚未裝備任何技能。請先到【技能配置】設定戰鬥技能組合。")

    lines = [t(lang, "upgrade.panel_header", "⬆️ **【強化技能】**\n在這裡花費金幣直接強化已裝備的技能，或查看熟練度進度。\n")]
    money = getattr(player, "money", 0) or 0
    lines.append(t(lang, "upgrade.gold_display", "💰 目前持有金幣：{money}", money=money))
    lines.append("")

    manual_count = (getattr(player, "inventory", None) or {}).get("skill_manual", 0)
    for sid in equipped:
        skill = skills_data.get(sid)
        if not skill or skill.get("type") == "passive":
            continue
        name = tf(skill, "name", lang) or sid
        lv = _skill_level(player, sid)
        usage = _skill_usage(player, sid)

        if lv >= MAX_SKILL_LEVEL:
            progress = t(lang, "upgrade.max_level", "✅ 已達最高等級")
        else:
            threshold = get_proficiency_requirement(lv)
            progress = t(lang, "upgrade.proficiency_progress",
                         "熟練度：{used}/{need} 次",
                         used=usage, need=threshold)

        upgrade_line = ""
        if lv < MAX_SKILL_LEVEL:
            need_manuals = get_manual_cost(lv)
            if manual_count >= need_manuals:
                upgrade_line = t(lang, "upgrade.manual_available",
                                 "  📖 可用技能指南升級：消耗 {need} 本（持有 {have} 本）",
                                 need=need_manuals, have=manual_count)
            else:
                cost = get_upgrade_cost_with_discount(lv, usage)
                upgrade_line = t(lang, "upgrade.upgrade_cost", "  💰 花費金幣強化：{cost} G", cost=cost)

        # 效果預覽
        eff = get_skill_effect_multiplier(lv)
        next_eff = get_skill_effect_multiplier(lv + 1) if lv < MAX_SKILL_LEVEL else None
        cost_rate = get_skill_cost_multiplier(lv)
        next_cost_rate = get_skill_cost_multiplier(lv + 1) if lv < MAX_SKILL_LEVEL else None

        eff_str = f"×{eff:.2f}"
        cost_str = f"×{cost_rate:.2f}"
        if next_eff is not None:
            arrow = t(lang, "upgrade.arrow", " → ")
            eff_str += f"{arrow}×{next_eff:.2f}"
            cost_str += f"{arrow}×{next_cost_rate:.2f}"

        lines.append(
            t(lang, "upgrade.skill_row",
              "**{name}**  Lv.{lv}  |  {progress}\n"
              "  效果：{eff}  /  消耗：{cost}{upg}",
              name=name, lv=lv, progress=progress,
              eff=eff_str, cost=cost_str,
              upg=f"\n{upgrade_line}" if upgrade_line else "")
        )
        lines.append("")

    return "\n".join(lines).strip()


def build_upgrade_menu(view):
    """渲染升級選單介面：顯示技能清單與每個可升級技能的強化按鈕。"""
    view.clear_items()
    player = view.player
    lang = player.language
    skills_data = view.cog.skills
    equipped = getattr(player, "equipped_skills", []) or []

    view.log_message = _format_upgrade_panel(player, skills_data, lang)

    # 為每個「可升級且資料存在」的技能添加一顆強化按鈕
    manual_count = (getattr(player, "inventory", None) or {}).get("skill_manual", 0)
    btn_count = 0
    for sid in equipped:
        skill = skills_data.get(sid)
        if not skill or skill.get("type") == "passive":
            continue
        lv = _skill_level(player, sid)
        if lv >= MAX_SKILL_LEVEL:
            continue
        name = tf(skill, "name", lang) or sid
        usage = _skill_usage(player, sid)
        need_manuals = get_manual_cost(lv)
        if manual_count >= need_manuals:
            # 優先顯示指南選項
            label = f"⬆️ {name} (📖×{need_manuals})"
            can_act = True
            style = discord.ButtonStyle.primary
        else:
            cost = get_upgrade_cost_with_discount(lv, usage)
            money = getattr(player, "money", 0) or 0
            can_act = money >= cost
            label = f"⬆️ {name} ({cost}G)"
            style = discord.ButtonStyle.success if can_act else discord.ButtonStyle.secondary
        view.add_action_button(
            label=label,
            style=style,
            custom_id=f"btn_upgrade_skill_{sid}",
            disabled=not can_act,
        )
        btn_count += 1
        if btn_count >= 20:  # Discord 最多 25 個元件，留位置給返回鈕
            break

    view.add_action_button(
        label=t(lang, "menu.btn_back", "返回"),
        style=discord.ButtonStyle.secondary,
        custom_id="btn_back_school",
        emoji="🔙",
    )


async def handle_upgrade_skill(view, interaction: discord.Interaction, skill_id: str):
    """處理玩家點擊「強化技能」按鈕的邏輯：優先用技能指南，否則扣金幣。
    注意：global_callback 已在上游呼叫 interaction.response.defer()，
    所以這裡的錯誤訊息要用 followup.send，成功後用 interaction.message.edit。"""
    player = view.player
    lang = player.language
    skills_data = view.cog.skills
    skill = skills_data.get(skill_id)
    name = tf(skill, "name", lang) or skill_id
    lv = _skill_level(player, skill_id)

    # 優先嘗試技能指南升級
    ok_m, msg_m, need_manuals = validate_manual_upgrade(player, skill, skill_id)
    if ok_m:
        apply_manual_upgrade(player, skill_id, need_manuals)
        view.cog.save_players(player=getattr(player, "real_player", player))
        build_upgrade_menu(view)
        view.log_message = (
            t(lang, "upgrade.manual_success",
              "📖 消耗 {need} 本技能指南，成功強化【{skill}】至 Lv.{lv}！\n\n",
              skill=name, lv=lv + 1, need=need_manuals)
            + view.log_message
        )
        return

    # 否則走金幣路線
    ok, msg, cost = validate_paid_upgrade(player, skill, skill_id)
    if not ok:
        await interaction.followup.send(msg, ephemeral=True)
        return

    # 扣除金幣、提升等級、清零熟練度次數
    apply_paid_upgrade(player, skill_id, cost)

    view.cog.save_players(player=getattr(player, "real_player", player))

    # 重繪介面並通知成功
    build_upgrade_menu(view)
    view.log_message = (
        t(lang, "upgrade.success",
          "✅ 成功強化【{skill}】至 Lv.{lv}！消耗了 {cost} G。\n\n",
          skill=name, lv=lv + 1, cost=cost)
        + view.log_message
    )
