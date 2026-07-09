import discord
from trpg.i18n import t, tf
from trpg.stats import get_unspent_points, format_stat_alloc_summary, prestige_required_level, PRESTIGE_LEVEL_STEP

class CharLayout:
    _STAT_ALLOC_BUTTONS = (
        ("ATK", "atk", "⚔️"), ("VIT", "vit", "🛡️"), ("INT", "int", "✨"),
        ("SPD", "spd", "💨"), ("RES", "res", "🔰"), ("LUCK", "luck", "🍀"),
    )

    @staticmethod
    def build_char_menu(view, notice=None):
        view.clear_items()
        view.current_menu_state = "char_menu"
        lang = view.player.language

        view.log_message = t(lang, "menu.char_prompt", "👥 【角色管理】\n在這裡你可以切換、創建、或刪除不同的角色存檔（最多 3 個角色）。")
        if notice:
            view.log_message = f"{notice}\n\n{view.log_message}"

        for slot_idx in range(3):
            slot = str(slot_idx)
            player_key = f"{view.user_id}_{slot}"
            exists = player_key in view.cog.players

            slot_label = t(lang, "menu.char_slot_label", "角色存檔 {num}", num=slot_idx + 1)

            if exists:
                p = view.cog.players[player_key]
                weapon_name = tf(view.cog.items.get(p.weapon, {}), "name", lang) if p.weapon else t(lang, "blacksmith.none", "None")
                slot_info = f" {slot_label} (Lv.{p.level} / {weapon_name})"

                is_active = view.cog.active_slots.get(view.user_id, "0") == slot

                if is_active:
                    view.add_action_button(
                        label=f"{slot_info} [Active]",
                        style=discord.ButtonStyle.success,
                        custom_id=f"char_active_{slot}",
                        disabled=True,
                        row=slot_idx
                    )
                else:
                    view.add_action_button(
                        label=f"Switch: {slot_info}",
                        style=discord.ButtonStyle.primary,
                        custom_id=f"char_switch_{slot}",
                        row=slot_idx
                    )

                view.add_action_button(
                    label=t(lang, "menu.char_delete", "刪除"),
                    style=discord.ButtonStyle.danger,
                    custom_id=f"char_delete_ask_{slot}",
                    row=slot_idx
                )
            else:
                view.add_action_button(
                    label=t(lang, "menu.char_create_slot", "🆕 創立新角色: {slot_label}", slot_label=slot_label),
                    style=discord.ButtonStyle.primary,
                    custom_id=f"char_create_{slot}",
                    row=slot_idx
                )

        view.add_action_button(
            label=t(lang, "menu.btn_back", "返回"),
            style=discord.ButtonStyle.secondary,
            custom_id="btn_church_menu",
            row=4,
            emoji="🔙"
        )

    @staticmethod
    def handle_char_delete_ask(view, slot: str):
        view.clear_items()
        lang = view.player.language
        view.log_message = t(
            lang,
            "menu.char_delete_confirm_prompt",
            "⚠️ 警告：確定要刪除角色存檔 {num} 嗎？此操作將會永久清除所有等級、裝備與進度，且無法復原！",
            num=int(slot) + 1
        )
        view.add_action_button(
            label=t(lang, "menu.char_delete_confirm_yes", "💥 確定刪除"),
            style=discord.ButtonStyle.danger,
            custom_id=f"char_delete_confirm_{slot}",
            row=0
        )
        view.add_action_button(
            label=t(lang, "menu.char_delete_confirm_no", "取消"),
            style=discord.ButtonStyle.secondary,
            custom_id="btn_char_menu",
            row=0
        )

    @staticmethod
    def handle_stat_alloc_menu(view, notice=""):
        view.clear_items()
        lang = view.player.language
        prefix = notice + "\n\n" if notice else ""
        unspent = get_unspent_points(view.player)
        view.log_message = (
            prefix
            + t(lang, "char.stat_alloc_header", "📊 【屬性分配】每級 2 點，死亡後重置。\n")
            + format_stat_alloc_summary(view.player)
            + t(lang, "char.stat_alloc_legend", "\n\n攻擊+3 ATK/點 | 體力+12 HP & +2 DEF/點 | 魔力+4 MAG & +3 MP/點 | "
                "速度+2 SPD/點 | 抗性+2 RES/點 | 運氣+1 LUCK/點（提升暴擊率與掉寶率）")
        )
        if unspent > 0:
            view.log_message += t(
                lang,
                "char.stat_alloc_unspent",
                "\n\n**您還有 {unspent} 點屬性點可以分配！**\n(💡 點擊「+1」按鈕投資1點，點擊「手動輸入」打數字精準分配，數字打超過剩餘點數就等於全押)",
                unspent=unspent,
            )

            for label, key, emoji in CharLayout._STAT_ALLOC_BUTTONS:
                row = 0 if key != "luck" else 1
                view.add_action_button(label=f"+1 {label}", style=discord.ButtonStyle.primary, custom_id=f"stat_add_{key}", row=row, emoji=emoji)
            for label, key, emoji in CharLayout._STAT_ALLOC_BUTTONS:
                row = 2 if key != "luck" else 3
                view.add_action_button(label=t(lang, "char.btn_stat_manual", "輸入 {stat}", stat=label), style=discord.ButtonStyle.success, custom_id=f"stat_manual_{key}", row=row, emoji=emoji)

        last_row = 4 if unspent > 0 else 0
        reset_cost = view._stat_reset_cost()
        reset_label = (t(lang, "char.btn_reset_stats_paid", "重置所有屬性點（{cost}$）", cost=reset_cost) if reset_cost > 0
                       else t(lang, "char.btn_reset_stats", "重置所有屬性點"))
        view.add_action_button(label=reset_label, style=discord.ButtonStyle.danger, custom_id="btn_stat_reset", row=last_row, emoji="🔄")
        view.add_action_button(label=t(lang, "char.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", row=last_row, emoji="🔙")

    @staticmethod
    def handle_prestige_menu(view, notice=""):
        view.clear_items()
        lang = view.player.language
        prefix = notice + "\n\n" if notice else ""

        prestige = getattr(view.player, "prestige_count", 0)
        bonus = prestige * 10
        req_level = prestige_required_level(prestige)

        view.log_message = (
            prefix
            + t(lang, "prestige.hall_title", "🌟 【轉生殿堂】\n")
            + t(lang, "prestige.hall_intro", "在這裡，你可以超越冒險者的極限，重獲新生！\n")
            + t(lang, "prestige.current_level", "• 當前等級：Lv.{level} (轉生需要 Lv.{req})\n", level=view.player.level, req=req_level)
            + t(lang, "prestige.current_count", "• 當前轉生次數：{prestige} 轉\n", prestige=prestige)
            + t(lang, "prestige.current_bonus", "• 當前轉生被動增幅：全屬性 +{bonus}%\n\n", bonus=bonus)
            + t(lang, "prestige.rules_title", "⚠️ 【轉生規則說明】\n")
            + t(lang, "prestige.rule_1", "1. 轉生將使你的等級重置回 Lv.1，EXP 歸零，並重置屬性配點。\n")
            + t(lang, "prestige.rule_2", "2. 轉生後你將獲得 1 層永久被動增幅，所有戰鬥屬性額外 +10%！\n")
            + t(lang, "prestige.rule_3", "3. 轉生會卸下你身上的武器、防具與飾品，並重置魔塔／地下城的目前樓層（已達成的里程碑勳章與獎杯不會消失）。\n")
            + t(lang, "prestige.rule_4", "4. 轉生不會清除你的背包道具、金幣與已學會的技能。\n")
            + t(lang, "prestige.rule_5", "5. 每次轉生後，下一次轉生所需的等級都會提高 {step} 級。", step=PRESTIGE_LEVEL_STEP)
        )

        if view.player.level >= req_level:
            view.add_action_button(label=t(lang, "prestige.btn_confirm", "確認轉生 (Lv.{req}+)", req=req_level), style=discord.ButtonStyle.danger, custom_id="btn_prestige_confirm", emoji="🌟")
        else:
            disabled_btn = discord.ui.Button(label=t(lang, "prestige.btn_level_locked", "等級不足 Lv.{req}", req=req_level), style=discord.ButtonStyle.secondary, disabled=True, emoji="❌")
            view.add_item(disabled_btn)

        view.add_action_button(label=t(lang, "prestige.btn_back_village", "返回村莊"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    @staticmethod
    def handle_learn_skill_menu(view, notice=""):
        lang = view.player.language
        view.clear_items()
        scrolls = []
        for item_id, count in view.player.inventory.items():
            if count > 0:
                item = view.cog.items.get(item_id)
                if item and item.get("type") == "skill_scroll":
                    scrolls.append(item_id)

        prefix = notice + "\n\n" if notice else ""
        if not scrolls:
            view.log_message = prefix + t(lang, "skill.learn_menu_no_scrolls", "📖 【學習魔法】\n背包裡沒有技能卷軸。可從商店購買，或討伐區域 BOSS 取得！")
        else:
            view.log_message = prefix + t(lang, "skill.learn_menu_choose_scroll", "📖 【學習魔法】\n選擇要研讀的卷軸（消耗 1 張）：")
            learnable = []
            learned_lines = []
            for scroll_id in scrolls:
                item = view.cog.items[scroll_id]
                skill_id = item.get("teaches", "")
                skill = view.cog.skills.get(skill_id, {})
                skill_name = tf(skill, "name", lang) if skill else skill_id
                if skill_id in view.player.skills:
                    learned_lines.append(t(lang, "skill.already_learned_label", "已學會：{skill_name}", skill_name=skill_name))
                else:
                    req_lv = skill.get("req_level", 1)
                    item_name = tf(item, "name", lang) or scroll_id
                    label = item_name if view.player.level >= req_lv else t(lang, "skill.opt_locked", "🔒 {skill_name}（需 Lv.{req}）", skill_name=item_name, req=req_lv)
                    learnable.append((label, f"learn_{scroll_id}", (tf(skill, "desc", lang) or "")[:100], "📜"))
            if learned_lines:
                view.log_message += "\n" + "\n".join(f"✅ {ln}" for ln in learned_lines[:20])
            if learnable:
                view.add_action_select(t(lang, "skill.select_learn", "📜 選擇要研讀的卷軸"), learnable[:25], row=0, custom_id="sel_learn_scroll")
            else:
                view.log_message += "\n\n" + t(lang, "skill.all_scrolls_learned", "（背包裡卷軸的技能都已學會）")

        view.add_action_button(label=t(lang, "skill.btn_back_to_church", "返回教堂"), style=discord.ButtonStyle.secondary, custom_id="btn_church_menu", emoji="🔙")

    @staticmethod
    def handle_skill_equip_menu(view, notice="", paging=False):
        lang = view.player.language
        view.clear_items()
        view.current_menu_state = "skill_equip"
        prefix = notice + "\n\n" if notice else ""

        if not getattr(view.player, "equipped_skills", None):
            view.player.equipped_skills = []

        p_skills = list(dict.fromkeys(getattr(view.player, "skills", [])))

        if not p_skills:
            view.log_message = prefix + t(lang, "skill.equip_menu_no_skills", "🔧 【技能配置】\n你尚未習得任何技能。請先【學習魔法】！")
            view.add_action_button(label=t(lang, "skill.btn_back_to_church", "返回教堂"), style=discord.ButtonStyle.secondary, custom_id="btn_church_menu", emoji="🔙")
            return

        equipped_count = len(view.player.equipped_skills)
        equipped_list = [s for s in p_skills if s in view.player.equipped_skills]
        unequipped_list = [s for s in p_skills if s not in view.player.equipped_skills]

        per_page = 25
        if not paging:
            view.inventory_page = 0
        max_page = max(0, (len(unequipped_list) - 1) // per_page)
        view.inventory_page = min(view.inventory_page, max_page)
        start = view.inventory_page * per_page

        header = prefix + t(lang, "skill.equip_menu_header_select", "🔧 【技能配置】 (已裝備: {equipped_count}/8)\n用下方選單裝備／卸下你的戰鬥技能。", equipped_count=equipped_count)
        if max_page > 0:
            header += t(lang, "skill.equip_page_suffix", "  (第 {page}/{max_page} 頁)", page=view.inventory_page + 1, max_page=max_page + 1)
        lines = [header]
        for skill_id in equipped_list:
            skill = view.cog.skills.get(skill_id, {})
            lines.append(f"🟢 {tf(skill, 'name', lang) or skill_id}: {tf(skill, 'desc', lang) or ''}")

        if unequipped_list:
            options = []
            for skill_id in unequipped_list[start:start + per_page]:
                skill = view.cog.skills.get(skill_id, {})
                skill_name = tf(skill, "name", lang) or skill_id
                req_lv = skill.get("req_level", 1)
                if view.player.level < req_lv:
                    label = t(lang, "skill.opt_locked", "🔒 {skill_name}（需 Lv.{req}）", skill_name=skill_name, req=req_lv)
                else:
                    label = skill_name
                options.append((label, f"equip_skill_{skill_id}", (tf(skill, "desc", lang) or "")[:100], "⚪"))
            view.add_action_select(t(lang, "skill.select_equip", "⚪ 裝備技能：選擇要裝上的技能"), options, row=0, custom_id="sel_skill_equip")

        if equipped_list:
            options = []
            for skill_id in equipped_list[:25]:
                skill = view.cog.skills.get(skill_id, {})
                skill_name = tf(skill, "name", lang) or skill_id
                options.append((skill_name, f"unequip_skill_{skill_id}", (tf(skill, "desc", lang) or "")[:100], "🟢"))
            view.add_action_select(t(lang, "skill.select_unequip", "🟢 卸下技能：選擇要卸下的技能"), options, row=1, custom_id="sel_skill_unequip")

        view.log_message = "\n".join(lines)
        if len(unequipped_list) > per_page:
            view._add_pagination_buttons(len(unequipped_list), per_page)
        view.add_action_button(label=t(lang, "skill.btn_back_to_church", "返回教堂"), style=discord.ButtonStyle.secondary, custom_id="btn_church_menu", emoji="🔙", row=4)
