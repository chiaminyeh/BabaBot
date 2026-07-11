import discord
from trpg.i18n import t, tf
from trpg.view_shared import item_emoji

class BattleLayout:
    @staticmethod
    def build_battle_menu(view):
        view.clear_items()
        view.in_battle = True
        lang = view.player.language

        if "berserk" in getattr(view.player, "status_effects", {}):
            view.add_action_button(label=t(lang, "menu.btn_berserk_attack", "狂暴攻擊"), style=discord.ButtonStyle.danger, custom_id="b_atk", row=0, emoji="😡")
            view.add_action_button(label=t(lang, "menu.btn_flee", "逃跑"), style=discord.ButtonStyle.secondary, custom_id="b_fle", row=0, emoji="🏃")
            view.add_action_button(label=t(lang, "menu.btn_status", "狀態"), style=discord.ButtonStyle.success, custom_id="b_sta", row=0, emoji="📜")
            return

        view.add_action_button(label=t(lang, "menu.btn_attack", "攻擊"), style=discord.ButtonStyle.danger, custom_id="b_atk", row=0, emoji="🗡️")
        view.add_action_button(label=t(lang, "menu.btn_skill", "技能"), style=discord.ButtonStyle.success, custom_id="b_ski", row=0, emoji="✨")
        view.add_action_button(label=t(lang, "menu.btn_defend", "防禦"), style=discord.ButtonStyle.primary, custom_id="b_def", row=0, emoji="🛡️")
        view.add_action_button(label=t(lang, "menu.btn_dodge", "閃避"), style=discord.ButtonStyle.primary, custom_id="b_dod", row=0, emoji="💨")

        view.add_action_button(label=t(lang, "menu.btn_item", "道具"), style=discord.ButtonStyle.secondary, custom_id="b_itm", row=1, emoji="🎒")
        view.add_action_button(label=t(lang, "menu.btn_flee", "逃跑"), style=discord.ButtonStyle.secondary, custom_id="b_fle", row=1, emoji="🏃")
        view.add_action_button(label=t(lang, "menu.btn_status", "狀態"), style=discord.ButtonStyle.success, custom_id="b_sta", row=1, emoji="📜")

    @staticmethod
    def handle_skill_menu(view):
        lang = view.player.language
        if not getattr(view.player, "equipped_skills", None):
            view.player.equipped_skills = []

        active_skills = list(dict.fromkeys(
            s for s in view.player.equipped_skills
            if view.cog.skills.get(s, {}).get("type") != "passive"
        ))
        if not active_skills:
            view.log_message = t(lang, "skill.no_active_skills_equipped", "❌ 你尚未裝備任何可施放的技能！請去教堂進行【技能配置】。")
            return

        view.clear_items()
        view.in_battle = True
        lines = [t(lang, "battle.choose_skill_to_cast", "✨ 選擇要施放的技能：")]
        for skill_id in active_skills:
            skill = view.cog.skills.get(skill_id)
            if not skill:
                continue
            skill_name = tf(skill, "name", lang)
            skill_desc = tf(skill, "desc", lang)
            lines.append(f"• {skill_name}: {skill_desc}")

            cd_left = view.combat.skill_cds.get(skill_id, 0)

            cost_texts = []
            if skill.get("mp_cost"):
                cost_texts.append(f"MP:{skill['mp_cost']}")
            if skill.get("hp_cost_percent"):
                cost_texts.append(f"HP:{int(view.player.max_hp * skill['hp_cost_percent'])}")

            cost_str = " (" + ", ".join(cost_texts) + ")" if cost_texts else ""
            cd_text = f" [CD:{cd_left}]" if cd_left > 0 else ""

            mp_cost = skill.get("mp_cost", 0)
            hp_cost_pct = skill.get("hp_cost_percent", 0.0)
            actual_hp_cost = int(view.player.max_hp * hp_cost_pct)
            cant_afford = (mp_cost > 0 and view.player.current_mp < mp_cost) or (
                actual_hp_cost > 0 and view.player.current_hp <= actual_hp_cost
            )
            disabled = cd_left > 0 or cant_afford

            if cd_left > 0:
                style = discord.ButtonStyle.secondary
            elif cant_afford:
                style = discord.ButtonStyle.danger
            else:
                style = discord.ButtonStyle.success

            view.add_action_button(
                label=f"{skill_name}{cost_str}{cd_text}"[:80],
                style=style,
                custom_id=f"skill_{skill_id}",
                disabled=disabled,
            )
        view.log_message = "\n".join(lines)
        view.add_action_button(label=t(lang, "battle.btn_back_to_battle", "返回戰鬥"), style=discord.ButtonStyle.secondary, custom_id="btn_back_battle", emoji="🔙")

    @staticmethod
    def handle_item_menu(view, paging=False):
        view.clear_items()
        view.current_menu_state = "item"
        lang = view.player.language

        usable = []
        for item_id, count in view.player.inventory.items():
            if count > 0:
                item = view.cog.items.get(item_id)
                if item and item.get("type") in ("potion", "cure", "buff_item"):
                    usable.append(item_id)

        total_items = len(usable)
        page_items, _, max_page = view._paginate(usable, 25, paging)

        if not usable:
            view.log_message = t(lang, "menu.no_usable_items", "❌ 背包裡沒有可用的道具。")
            if view.in_battle:
                view.build_battle_menu()
            else:
                view.build_main_menu()
            return

        view.log_message = t(lang, "menu.choose_item_to_use_select", "🎒 從下方選單挑選要使用的道具：")
        if max_page > 0:
            view.log_message += t(lang, "equip.page_suffix", "（第 {page}/{max_page} 頁）", page=view.inventory_page + 1, max_page=max_page + 1)
        options = []
        for item_id in page_items:
            item = view.cog.items[item_id]
            label = f"{tf(item, 'name', lang)} x{view.player.inventory[item_id]}"
            options.append((label, f"use_item_{item_id}", (tf(item, "desc", lang) or "")[:100], item_emoji(item)))
        view.add_action_select(t(lang, "menu.select_item_placeholder", "🎒 選擇道具"), options, row=0, custom_id="sel_use_item")

        if total_items > 25:
            view._add_pagination_buttons(total_items, 25)
        back_id = "btn_back_battle" if view.in_battle else "btn_back_main"
        view.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id=back_id, emoji="🔙", row=4)
