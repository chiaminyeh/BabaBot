import discord
from datetime import datetime
from trpg.i18n import t, tf
from trpg.inns import INN_ROOMS, SHOP_EMOJIS, area_inn_config, inn_room_cost, localized
from trpg.view_shared import baba_emoji


def select_main_menu_quests(active_quests: dict, quest_catalog: dict) -> tuple[list[str], int]:
    """Pick one main and one daily quest, falling back without duplicates."""
    quest_ids = [qid for qid in active_quests if qid in quest_catalog]
    main_id = next((qid for qid in quest_ids if quest_catalog[qid].get("quest_line") == "main"), None)
    daily_id = next((qid for qid in quest_ids if quest_catalog[qid].get("repeatable")), None)

    selected = []
    first = main_id or next((qid for qid in quest_ids if qid != daily_id), daily_id)
    if first:
        selected.append(first)
    second = daily_id if daily_id not in selected else None
    if second is None:
        second = next((qid for qid in quest_ids if qid not in selected), None)
    if second:
        selected.append(second)
    return selected, max(0, len(quest_ids) - len(selected))


def should_show_adventure_compass(current_menu_state: str, *, in_battle: bool) -> bool:
    """The compass belongs only on the non-battle source-of-truth panel."""
    return current_menu_state == "main" and not in_battle


def _compact_compass_field(name: str, value: str) -> tuple[str, str]:
    name = name[:256]
    if len(value) > 1024:
        value = value[:1021].rstrip() + "..."
    return name, value


def _priority_active_quest(active_quests: dict, quest_catalog: dict):
    quest_ids = [quest_id for quest_id in active_quests if quest_id in quest_catalog]
    main_id = next(
        (quest_id for quest_id in quest_ids if quest_catalog[quest_id].get("quest_line") == "main"),
        None,
    )
    if main_id:
        return main_id
    daily_id = next(
        (quest_id for quest_id in quest_ids if quest_catalog[quest_id].get("repeatable")),
        None,
    )
    return daily_id or next(iter(quest_ids), None)


def build_adventure_compass(view, unspent_points: int, *, today: str | None = None) -> tuple[str, str]:
    """Build one read-only, actionable next-step field from live player state."""
    from trpg.quest_popup import available_board_quests, describe_quest_goal, quest_progress_text

    player = getattr(view.player, "real_player", view.player)
    lang = getattr(player, "language", "zh")
    field_name = t(lang, "compass.field_name", "🧭 冒險指南")
    area_data = view.cog.areas.get(player.current_area, {})

    if unspent_points > 0:
        if area_data.get("is_village"):
            value = t(
                lang,
                "compass.unspent_village",
                "📊 尚有 {count} 點未分配。下一步：點選「屬性」立即配置。",
                count=unspent_points,
            )
        else:
            value = t(
                lang,
                "compass.unspent_travel",
                "📊 尚有 {count} 點未分配。下一步：「移動」回村莊 →「屬性」。",
                count=unspent_points,
            )
        return _compact_compass_field(field_name, value)

    quest_id = _priority_active_quest(player.active_quests, view.cog.quests)
    if quest_id:
        quest_info = view.cog.quests[quest_id]
        marker = "📖" if quest_info.get("quest_line") == "main" else "📅" if quest_info.get("repeatable") else "📌"
        progress_line = quest_progress_text(view, quest_id, quest_info, lang)
        goal = describe_quest_goal(view, quest_info, lang) or t(
            lang,
            "compass.active_default",
            "🎯 下一步：前往「公會 → 任務大廳」查看任務詳情。",
        )
        return _compact_compass_field(field_name, f"{marker} {progress_line}\n{goal}")

    available_count = len(available_board_quests(view))
    if available_count:
        value = t(
            lang,
            "compass.available_quests",
            "📜 有 {count} 個任務可接取。下一步：公會 → 任務大廳。",
            count=available_count,
        )
        return _compact_compass_field(field_name, value)

    area_name = tf(area_data, "area_name", lang) or t(lang, "explore.unknown_area", "未知區域")
    if area_data.get("is_village"):
        value = t(
            lang,
            "compass.fallback_village",
            "🗺️ 下一步：點選「移動」，前往可探索區域。",
        )
    else:
        today = today or datetime.today().strftime("%Y-%m-%d")
        boss_done = (getattr(player, "daily_boss_kills", {}) or {}).get(player.current_area) == today
        if area_data.get("boss") and not boss_done:
            value = t(
                lang,
                "compass.fallback_explore_boss",
                "⚔️ 下一步：在【{area}】點選「探索」，或挑戰「BOSS」。",
                area=area_name,
            )
        else:
            value = t(
                lang,
                "compass.fallback_explore",
                "⚔️ 下一步：在【{area}】點選「探索」繼續冒險。",
                area=area_name,
            )
    return _compact_compass_field(field_name, value)


def adventure_compass_for_main_panel(view, unspent_points: int) -> tuple[str, str]:
    """Refresh live quest counters with the canonical rules, then render the pure field."""
    from trpg.quest_popup import recompute_live_progress

    recompute_live_progress(view)
    return build_adventure_compass(view, unspent_points)


def npc_progress_state(npc: dict, completed_quests: set[str]) -> tuple[str | None, bool]:
    """Return a data-driven NPC dialogue stage and crafting unlock state."""
    requirements = npc.get("craft_unlock_requires") or []
    if not requirements:
        return None, False
    unlocked = all(quest_id in completed_quests for quest_id in requirements)
    if unlocked:
        return "dialogue_complete", True
    if requirements[0] in completed_quests:
        return "dialogue_after_first", False
    return "dialogue_before", False


class MainMenuLayout:
    @staticmethod
    def build_main_menu(view):
        if getattr(view, "in_battle", False) and getattr(view, "monster_slots", None):
            view.build_battle_menu()
            return
        view.clear_items()
        view.current_menu_state = "main"
        view.in_battle = False
        view.monster_slots = []
        view.viewing_leaderboard = False
        if view._refresh_daily_state():
            view.cog.save_players(player=view.player)

        # Route to dungeon if in dungeon
        if view.player.current_area == "area_dungeon":
            view.build_dungeon_menu()
            return

        if view.player.current_area == "area_legend_cave":
            view.build_legend_cave_menu()
            return

        if view.cog.areas.get(view.player.current_area, {}).get("is_colosseum"):
            view.build_colosseum_menu()
            return

        real = getattr(view.player, "real_player", view.player)
        pending_event_id = (getattr(real, "pending_event", None) or {}).get("event_id")
        if pending_event_id:
            pending_event = getattr(view.cog, "events", {}).get(pending_event_id, {})
            if pending_event.get("choices"):
                view.build_event_choice_menu()
                return
            real.pending_event = {}

        lang = view.player.language
        shop_button_label = t(lang, "shop.btn_shop", "商店")
        shop_emoji = SHOP_EMOJIS.get(view.player.current_area, "🛒")
        inn_cfg = area_inn_config(view.player.current_area)
        if view.cog.areas.get(view.player.current_area, {}).get("is_village"):
            view.add_action_button(label=t(lang, "menu.btn_move", "移動"), style=discord.ButtonStyle.primary, custom_id="btn_move_menu", row=0, emoji="🗺️")
            view.add_action_button(label=t(lang, "menu.btn_items", "物品"), style=discord.ButtonStyle.primary, custom_id="btn_status", row=0, emoji="🎒")
            view.add_action_button(label=t(lang, "menu.btn_equip", "裝備"), style=discord.ButtonStyle.primary, custom_id="btn_equip_menu", row=0, emoji="🛡️")
            view.add_action_button(label=shop_button_label, style=discord.ButtonStyle.primary, custom_id="btn_shop_menu", row=1, emoji=shop_emoji)
            view.add_action_button(label=t(lang, "menu.btn_inn", "旅館"), style=discord.ButtonStyle.primary, custom_id="btn_inn_menu", row=1, emoji=inn_cfg["emoji"])
            view.add_action_button(label=t(lang, "menu.btn_attributes", "屬性"), style=discord.ButtonStyle.primary, custom_id="btn_stat_alloc", row=1, emoji="📊")
            view.add_action_button(label=t(lang, "menu.btn_school", "學校"), style=discord.ButtonStyle.secondary, custom_id="btn_school_menu", row=2, emoji="🏫")
            view.add_action_button(label=t(lang, "menu.btn_guild", "公會"), style=discord.ButtonStyle.secondary, custom_id="btn_guild_menu", row=2, emoji="🏛️")
            view.add_action_button(label=t(lang, "menu.btn_village_chief", "Baba"), style=discord.ButtonStyle.secondary, custom_id="btn_ask_chief", row=2, emoji=baba_emoji(view.cog.bot))
        else:
            area_data = view.cog.areas.get(view.player.current_area, {})
            current_subarea = view._current_subarea_data(area_data)
            explore_label = t(lang, "menu.btn_explore", "探索")
            if current_subarea:
                current_subarea_name = tf(current_subarea, "name", lang) or current_subarea.get("id", "子區域")
                explore_label = t(lang, "menu.btn_resume_subarea", "探索：{subarea_name}", subarea_name=current_subarea_name)
            view.add_action_button(label=explore_label, style=discord.ButtonStyle.primary, custom_id="btn_explore", row=0, emoji="⚔️")
            today_str = datetime.today().strftime("%Y-%m-%d")
            boss_done_today = view.player.daily_boss_kills.get(view.player.current_area) == today_str
            if boss_done_today:
                view.add_action_button(label=t(lang, "menu.btn_area_boss_done", "✅ BOSS"), style=discord.ButtonStyle.secondary, custom_id="btn_boss_explore", row=0, emoji="👹", disabled=True)
            else:
                view.add_action_button(label=t(lang, "menu.btn_area_boss", "BOSS"), style=discord.ButtonStyle.danger, custom_id="btn_boss_explore", row=0, emoji="👹")
            view.add_action_button(label=t(lang, "menu.btn_move", "移動"), style=discord.ButtonStyle.secondary, custom_id="btn_move_menu", row=0, emoji="🗺️")
            view.add_action_button(label=t(lang, "menu.btn_items", "物品"), style=discord.ButtonStyle.secondary, custom_id="btn_status", row=1, emoji="🎒")
            view.add_action_button(label=t(lang, "menu.btn_equip", "裝備"), style=discord.ButtonStyle.secondary, custom_id="btn_equip_menu", row=1, emoji="🛡️")
            view.add_action_button(label=t(lang, "menu.btn_consumables", "消耗品"), style=discord.ButtonStyle.secondary, custom_id="b_itm", row=1, emoji="🧪")
            view.add_action_button(label=shop_button_label, style=discord.ButtonStyle.primary, custom_id="btn_shop_menu", row=2, emoji=shop_emoji)
            view.add_action_button(label=t(lang, "menu.btn_inn", "旅館"), style=discord.ButtonStyle.primary, custom_id="btn_inn_menu", row=2, emoji=inn_cfg["emoji"])
            if view.player.current_area == "area_20lab":
                view.add_action_button(label=t(lang, "menu.btn_blacksmith_shop", "鐵匠鋪"), style=discord.ButtonStyle.primary, custom_id="btn_blacksmith_menu", row=3, emoji="⚒️")
            if view.player.current_area == "area_40vampire_castle":
                view.add_action_button(label=t(lang, "menu.btn_prestige_hall", "轉生殿堂"), style=discord.ButtonStyle.success, custom_id="btn_prestige_menu", row=3, emoji="🌟")
            if current_subarea:
                view.add_action_button(label=t(lang, "menu.btn_change_subarea", "切換子區域"), style=discord.ButtonStyle.secondary, custom_id="btn_subarea_menu", row=3, emoji="🧭")
            if view.player.current_area == "area_01grassland":
                view.add_action_button(label=t(lang, "menu.btn_back_village", "返回村莊"), style=discord.ButtonStyle.secondary, custom_id="move_to_area_00village", row=2, emoji="🏠")
            area_npc = area_data.get("npc")
            if area_npc:
                npc_name = tf(area_npc, "name", lang) or "NPC"
                view.add_action_button(label=npc_name[:80], style=discord.ButtonStyle.success, custom_id="btn_area_npc", row=3, emoji=area_npc.get("emoji") or "🧑")

    @staticmethod
    def build_village_facilities_menu(view):
        # Backward compatibility for old Discord panels: the retired Village
        # Facilities button now opens the school that replaced it.
        MainMenuLayout.build_school_menu(view)

    @staticmethod
    def build_inn_menu(view, notice=""):
        view.clear_items()
        view.current_menu_state = "inn"
        lang = view.player.language
        config = area_inn_config(view.player.current_area)
        lines = []
        if notice:
            lines.extend([notice, ""])
        lines.append(f"{config['emoji']} " + t(lang, "inn.title", "【旅館】"))
        lines.extend([localized(config, "intro", lang), ""])
        for room_id, room in INN_ROOMS.items():
            cost = inn_room_cost(room_id, view.player.level)
            room_name = localized(room, "name", lang)
            if room_id == "cot":
                effect = t(lang, "inn.cot_effect", "恢復 35% HP／MP，不解除異常")
            elif room_id == "room":
                effect = t(lang, "inn.room_effect", "恢復 70% HP／MP，解除 1 個異常")
            else:
                effect = t(lang, "inn.suite_effect", "完全恢復 HP／MP，解除全部異常")
            lines.append(f"{room['emoji']} **{room_name}** — {cost}$\n　{effect}")
            view.add_action_button(
                label=t(lang, "inn.room_button", "{room}・{cost}$", room=room_name, cost=cost),
                style=discord.ButtonStyle.primary if room_id != "suite" else discord.ButtonStyle.success,
                custom_id=f"inn_rest_{room_id}", row=0, emoji=room["emoji"],
            )
        lines.append("\n" + t(lang, "inn.event_hint", "住宿時可能聽見這個地區獨有的傳聞或小插曲。"))
        view.log_message = "\n".join(lines)
        view.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", row=1, emoji="🔙")

    @staticmethod
    def build_subarea_menu(view):
        view.clear_items()
        view.current_menu_state = "subarea"
        lang = view.player.language
        area_data = view.cog.areas.get(view.player.current_area, {})
        area_name = tf(area_data, "area_name", lang) or t(lang, "explore.unknown_area", "未知區域")
        visible = view._visible_subareas(area_data)
        if not visible:
            view.log_message = t(lang, "explore.area_peaceful", "📍 這個區域目前很平靜，沒有可探索的子區域。")
            view.build_main_menu()
            return

        current = view._current_subarea_data(area_data)
        lines = [t(lang, "menu.subarea_prompt", "請選擇要探索的子區域：{area_name}", area_name=area_name)]
        if current:
            current_name = tf(current, "name", lang) or current.get("id", "")
            current_desc = tf(current, "desc", lang) or ""
            lines.append("")
            lines.append(t(lang, "menu.subarea_current", "目前子區域：{subarea_name}", subarea_name=current_name))
            if current_desc:
                lines.append(current_desc)
        view.log_message = "\n".join(lines)

        for sub in visible:
            sub_name = tf(sub, "name", lang) or sub.get("id", "Subarea")
            view.add_action_button(
                label=sub_name[:80],
                style=discord.ButtonStyle.primary,
                custom_id=f"subarea_{sub['id']}",
            )
        view.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    @staticmethod
    def build_guild_menu(view):
        view.clear_items()
        lang = view.player.language
        view.log_message = t(lang, "menu.guild_prompt", "🏛️ 【冒險者公會】\n請選擇你要進行的公會服務：")
        if view._daily_claimed_today():
            view.add_action_button(label=t(lang, "menu.btn_daily_claimed", "✅ 已簽到"), style=discord.ButtonStyle.secondary, custom_id="btn_daily_claim", emoji="🎁", disabled=True)
        else:
            view.add_action_button(label=t(lang, "menu.btn_daily", "每日簽到"), style=discord.ButtonStyle.success, custom_id="btn_daily_claim", emoji="🎁")
        view.add_action_button(label=t(lang, "menu.btn_quest_hall", "任務大廳"), style=discord.ButtonStyle.success, custom_id="btn_quest_hall", emoji="📜")
        view.add_action_button(label=t(lang, "menu.btn_lottery", "幸運抽獎"), style=discord.ButtonStyle.primary, custom_id="btn_lottery_menu", emoji="🎰")
        view.add_action_button(label=t(lang, "menu.btn_achievements", "成就"), style=discord.ButtonStyle.primary, custom_id="btn_achievements", emoji="🏅")
        view.add_action_button(label=t(lang, "menu.btn_leaderboard", "排行榜"), style=discord.ButtonStyle.secondary, custom_id="btn_leaderboard", emoji="🏆")
        view.add_action_button(label=t(lang, "menu.btn_char_menu", "角色管理"), style=discord.ButtonStyle.secondary, custom_id="btn_char_menu", emoji="👥")
        view.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    @staticmethod
    def handle_lottery_menu(view, notice=""):
        view.clear_items()
        lang = view.player.language
        prefix = notice + "\n\n" if notice else ""
        balance = view.cog.get_bank_balance(view.user_id)
        view.log_message = prefix + t(
            lang, "lottery.menu_text",
            "🎰 【公會幸運抽獎】\n公會接待員眨眨眼：「試試手氣嗎？每抽 {cost} 金幣，獎品從藥水、材料、"
            "技能卷軸到稀有飾品都有可能——聽說運氣好的人更容易抽到大獎喔。」\n\n💰 目前餘額：{balance} 金幣",
            cost=view.LOTTERY_COST, balance=balance,
        )
        can_draw = balance >= view.LOTTERY_COST
        view.add_action_button(
            label=t(lang, "lottery.btn_draw", "抽一次（{cost}$）", cost=view.LOTTERY_COST),
            style=discord.ButtonStyle.success if can_draw else discord.ButtonStyle.secondary,
            custom_id="btn_lottery_draw", emoji="🎰", disabled=not can_draw,
        )
        view.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_guild_menu", emoji="🔙")

    @staticmethod
    def build_quest_hall_menu(view, notice=""):
        from trpg.quest_popup import recompute_live_progress, available_board_quests, quest_progress_text, describe_quest_goal
        view.clear_items()
        lang = view.player.language
        p = view.player
        recompute_live_progress(view)

        lines = []
        if notice:
            lines.append(notice + "\n")
        lines.append(t(lang, "quest_hall.title", "📜 【任務大廳】"))

        active = [(qid, view.cog.quests.get(qid)) for qid in p.active_quests if view.cog.quests.get(qid)]
        if active:
            lines.append(t(lang, "quest_hall.active_header", "\n── 進行中的委託 ──"))
            for qid, qinfo in active:
                lines.append(quest_progress_text(view, qid, qinfo, lang))
                goal = describe_quest_goal(view, qinfo, lang)
                if goal:
                    lines.append(f"   {goal}")
        else:
            lines.append(t(lang, "quest_hall.no_active", "\n（目前沒有進行中的委託）"))

        avail = available_board_quests(view)
        if avail:
            lines.append(t(lang, "quest_hall.available_header", "\n── 可接取的委託 ──"))
            for qid, qinfo in avail[:5]:
                title = tf(qinfo, "title", lang)
                tag = t(lang, "quest_hall.daily_tag", "（每日）") if qinfo.get("repeatable") else ""
                reward = t(lang, "quest_hall.reward_brief", "獎勵 {exp} EXP / {money}$", exp=qinfo.get("reward_exp", 0), money=qinfo.get("reward_money", 0))
                lines.append(f"• {title}{tag} — {reward}")
                goal = describe_quest_goal(view, qinfo, lang)
                if goal:
                    lines.append(f"   {goal}")
                view.add_action_button(
                    label=t(lang, "quest_hall.btn_accept", "接取：{title}", title=title[:70]),
                    style=discord.ButtonStyle.success, custom_id=f"qaccept_{qid}", emoji="✅",
                )
        else:
            lines.append(t(lang, "quest_hall.no_available", "\n（暫時沒有可接取的看板委託，去打怪或升級後再來看看！）"))

        lines.append(t(lang, "quest_hall.hint", "\n💡 部分委託會由 NPC 隨機找上你，接取後一樣能在這裡查看進度。"))
        view.log_message = "\n".join(lines)
        view.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_guild_menu", emoji="🔙")

    @staticmethod
    def handle_area_npc(view, notice=""):
        from trpg.quest_popup import recompute_live_progress, can_accept_quest, quest_progress_text, describe_quest_goal
        view.clear_items()
        lang = view.player.language
        area_npc = view.cog.areas.get(view.player.current_area, {}).get("npc")
        if not area_npc:
            view.build_main_menu()
            return

        recompute_live_progress(view)
        real = getattr(view.player, "real_player", view.player)
        npc_name = tf(area_npc, "name", lang) or "NPC"
        lines = []
        if notice:
            lines.append(notice + "\n")
        lines.append(f"{area_npc.get('emoji', '🧑')} **{npc_name}**")
        lines.append(tf(area_npc, "intro", lang) or "")
        dialogue_key, craft_unlocked = npc_progress_state(area_npc, set(real.completed_quests))
        if dialogue_key:
            lines.extend(["", tf(area_npc, dialogue_key, lang) or ""])

        lines.append(t(lang, "npc.quest_header", "\n── 委託 ──"))
        for qid in area_npc.get("quest_ids", []):
            qinfo = view.cog.quests.get(qid)
            if not qinfo:
                continue
            title = tf(qinfo, "title", lang) or qid
            if qid in real.completed_quests:
                lines.append(t(lang, "npc.quest_done", "✅ {title}（已完成）", title=title))
            elif qid in real.active_quests:
                lines.append(quest_progress_text(view, qid, qinfo, lang))
                goal = describe_quest_goal(view, qinfo, lang)
                if goal:
                    lines.append(f"   {goal}")
            elif can_accept_quest(view, qid, qinfo):
                reward_bits = [f"{qinfo.get('reward_exp', 0)} EXP", f"{qinfo.get('reward_money', 0)}$"]
                for item_id in (qinfo.get("reward_items") or {}):
                    reward_bits.append(tf(view.cog.items.get(item_id, {}), "name", lang) or item_id)
                lines.append(t(lang, "npc.quest_available", "❗ {title} — 獎勵：{rewards}", title=title, rewards=" / ".join(reward_bits)))
                goal = describe_quest_goal(view, qinfo, lang)
                if goal:
                    lines.append(f"   {goal}")
                view.add_action_button(
                    label=t(lang, "quest_hall.btn_accept", "接取：{title}", title=title[:70]),
                    style=discord.ButtonStyle.success, custom_id=f"qaccept_{qid}", emoji="✅",
                )
            else:
                req_lv = qinfo.get("req_level", 1)
                if real.level < req_lv:
                    lines.append(t(lang, "npc.quest_locked_level", "🔒 {title}（需 Lv.{req}）", title=title, req=req_lv))
                else:
                    lines.append(t(lang, "npc.quest_locked_chain", "🔒 {title}（完成前一項委託後開啟）", title=title))

        if craft_unlocked:
            view.add_action_button(
                label=t(lang, "menu.btn_crafting_station", "合成台"),
                style=discord.ButtonStyle.success,
                custom_id="btn_craft_menu", emoji="🔨",
            )
        elif all(qid in real.completed_quests for qid in area_npc.get("quest_ids", [])):
            lines.append(t(lang, "npc.all_done_hint", "\n💠 這位 NPC 的委託已全部完成。集齊三色魔法碎片後，前往迷霧森林找精靈長老艾露娜，使用她的合成台製作【魔法之眼】吧！"))

        view.log_message = "\n".join(lines)
        view.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    @staticmethod
    def build_church_menu(view):
        view.clear_items()
        lang = view.player.language
        view.log_message = t(lang, "menu.church_prompt", "⛪ 【教堂】\n莊嚴的聖光籠罩著你。這裡能為你洗滌疲憊，指引未來的道路。")
        view.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    @staticmethod
    def build_school_menu(view):
        view.clear_items()
        lang = view.player.language
        view.log_message = t(lang, "menu.school_prompt", "🏫 【米酥學院】\n充滿魔法與智慧氣息的地方。在這裡你可以配置戰鬥技能、研讀卷軸學習魔法，或是查閱圖鑑瞭解冒險技能的奧秘。")
        view.add_action_button(label=t(lang, "menu.btn_core_ability", "核心能力"), style=discord.ButtonStyle.success, custom_id="btn_core_ability", emoji="🌟")
        view.add_action_button(label=t(lang, "menu.btn_skill_config", "技能配置"), style=discord.ButtonStyle.primary, custom_id="btn_skill_equip", emoji="🔧")
        view.add_action_button(label=t(lang, "menu.btn_skill_upgrade", "強化技能"), style=discord.ButtonStyle.primary, custom_id="btn_skill_upgrade", emoji="⬆️")
        view.add_action_button(label=t(lang, "menu.btn_learn_magic", "學習魔法"), style=discord.ButtonStyle.primary, custom_id="btn_skill_learn", emoji="📖")
        view.add_action_button(label=t(lang, "menu.btn_skill_codex", "技能圖鑑"), style=discord.ButtonStyle.primary, custom_id="btn_skill_codex", emoji="📚")
        view.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    @staticmethod
    def build_legend_cave_menu(view):
        view.clear_items()
        lang = view.player.language
        area_data = view.cog.areas.get("area_legend_cave", {})
        nodes = area_data.get("nodes", {})
        cave_state = view.player.cave_state
        node_id = cave_state.get("current_node", "entrance")
        node = nodes.get(node_id) or nodes.get("entrance", {})
        sword_id = area_data.get("sword_item", "hero_sword")
        has_eye = view._has_magic_eye()

        if node.get("is_sword_room") and not view.player.inventory.get(sword_id, 0):
            if not has_eye:
                view.log_message = t(
                    lang,
                    "cave.sword_room_hidden",
                    "🕯️ 你來到一座圓形石室——這裡的魔力波動濃烈得令人窒息，但放眼望去卻空無一物。\n"
                    "空氣中彷彿有什麼在微微震顫……肉眼看不見的東西。\n"
                    "（也許需要某種能「看穿萬物」的祕寶才能找到它。）",
                )
                view.add_action_button(label=t(lang, "char.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="cave_dir_back", emoji="🔙")
                return
            view.player.inventory[sword_id] = view.player.inventory.get(sword_id, 0) + 1
            sword_name = tf(view.cog.items.get(sword_id, {}), "name", lang) or sword_id
            view.cog.save_players(player=view.player)
            view.log_message = t(
                lang,
                "cave.sword_obtained_eye",
                "🕯️ {node_desc}\n\n🧿 魔法之眼灼熱地震動，石室中央的隱匿結界如薄紗般被撕開——"
                "石墩上，一把纏繞聖光的長劍顯露真身！\n"
                "✨ 你拔起了【{sword_name}】！這把劍似乎在期待著與魔王的決戰。\n（記得回村莊把它裝備上！）",
                node_desc=tf(node, "desc", lang) or "",
                sword_name=sword_name,
            )
            view.add_action_button(label=t(lang, "cave.btn_leave_cave", "離開洞窟"), style=discord.ButtonStyle.success, custom_id="cave_dir_back", emoji="🚪")
            return

        hp_pct = int(view.player.current_hp / view.player.max_hp * 100) if view.player.max_hp else 0
        view.log_message = t(
            lang,
            "cave.status_display",
            "🕯️ **【傳說洞窟】**\n{node_desc}\n\n❤️ 目前生命值：{current_hp}/{max_hp} ({hp_pct}%)",
            node_desc=tf(node, "desc", lang) or "...",
            current_hp=view.player.current_hp,
            max_hp=view.player.max_hp,
            hp_pct=hp_pct,
        )

        view.log_message += view._collect_cave_loot(node_id, node, lang)

        if has_eye and not view.player.inventory.get(sword_id, 0):
            direction = view._cave_direction_to_sword(nodes, node_id)
            if direction:
                dir_label = {"forward": "前", "left": "左", "right": "右", "back": "後"}.get(direction, direction)
                view.log_message += "\n" + t(lang, "cave.eye_guide", "🧿 魔法之眼發出柔和的微光，在牆面上投射出一個方向箭頭：指引你往【{dir_label}】走。", dir_label=dir_label)

        exits = node.get("exits", {})
        if exits.get("forward"):
            view.add_action_button(label=t(lang, "cave.dir_forward", "直行"), style=discord.ButtonStyle.primary, custom_id="cave_dir_forward", row=0, emoji="⬆️")
        if exits.get("left"):
            view.add_action_button(label=t(lang, "cave.dir_left", "往左"), style=discord.ButtonStyle.primary, custom_id="cave_dir_left", row=0, emoji="⬅️")
        if exits.get("right"):
            view.add_action_button(label=t(lang, "cave.dir_right", "往右"), style=discord.ButtonStyle.primary, custom_id="cave_dir_right", row=0, emoji="➡️")
        view.add_action_button(label=t(lang, "char.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="cave_dir_back", row=1, emoji="🔙")

    @staticmethod
    def build_colosseum_menu(view):
        view.clear_items()
        lang = view.player.language
        area = view.cog.areas.get(view.player.current_area, {})
        state = view._colosseum_state()
        total_rounds = len(area.get("rounds", [])) + 1
        cur_round = min(state.get("round", 0), total_rounds - 1)

        today_str = datetime.today().strftime("%Y-%m-%d")
        cleared_today = view.player.daily_boss_kills.get(view.player.current_area) == today_str

        lines = [t(lang, "colosseum.title",
                   "🏟️ **【修羅鬥技場】**\n觀眾的咆哮聲震耳欲聾。這裡沒有退路——連續三輪死鬥，輪與輪之間傷勢不會恢復，"
                   "終點站著從未嘗過敗績的冠軍「剎羅」。")]
        if cleared_today:
            lines.append(t(lang, "colosseum.cleared_today", "\n🏆 你今天已經站上冠軍寶座了。明天再來衛冕吧！"))
        else:
            lines.append(t(lang, "colosseum.round_status", "\n⚔️ 目前進度：第 {cur}/{total} 輪", cur=cur_round + 1, total=total_rounds))
            if cur_round == total_rounds - 1:
                lines.append(t(lang, "colosseum.champion_next", "👑 下一戰：不敗冠軍・剎羅本人！"))
        view.log_message = "\n".join(lines)

        view.add_action_button(
            label=t(lang, "colosseum.btn_fight", "進入下一輪") if not cleared_today else t(lang, "colosseum.btn_done", "✅ 今日已通關"),
            style=discord.ButtonStyle.danger, custom_id="btn_colo_fight", row=0, emoji="🏟️",
            disabled=cleared_today,
        )
        view.add_action_button(label=t(lang, "menu.btn_move", "移動"), style=discord.ButtonStyle.secondary, custom_id="btn_move_menu", row=0, emoji="🗺️")
        view.add_action_button(label=t(lang, "menu.btn_items", "物品"), style=discord.ButtonStyle.success, custom_id="btn_status", row=0, emoji="🎒")
        view.add_action_button(label=t(lang, "menu.btn_potions", "藥水"), style=discord.ButtonStyle.secondary, custom_id="b_itm", row=1, emoji="🎒")

    @staticmethod
    def handle_move_menu(view):
        view.clear_items()
        lang = view.player.language
        view.log_message = t(lang, "menu.move_prompt", "請選擇要前往的區域。")
        for area_id, area in view.cog.areas.items():
            if area_id == view.player.current_area:
                continue
            if not view._area_unlocked(area):
                continue
            req = area.get("req_level", 1)
            area_name = tf(area, "area_name", lang) if area.get("area_name") else t(lang, "menu.unknown_area", "未知區域")
            label = t(
                lang, "menu.move_to_label",
                "前往 {area_name}（建議 Lv.{req}）",
                area_name=area_name,
                req=req,
            )
            view.add_action_button(label=label, style=discord.ButtonStyle.primary, custom_id=f"move_to_{area_id}")
        view.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")
