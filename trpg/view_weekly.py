"""Guild bulletin UI for the rotating weekly event season."""

import discord

from trpg.i18n import t, tf
from trpg.weekly_events import current_weekly_event, ensure_weekly_progress


class WeeklyEventMixin:
    def _weekly_monster_name(self, monster_id: str, lang: str) -> str:
        for area in self.cog.areas.values():
            monster = (area.get("monsters") or {}).get(monster_id)
            if monster:
                return tf(monster, "name", lang) or monster_id
            boss = area.get("boss")
            if isinstance(boss, dict) and boss.get("id") == monster_id:
                return tf(boss, "name", lang) or monster_id
        return monster_id.replace("_", " ").title()

    def build_weekly_event_menu(self, notice: str = ""):
        self.clear_items()
        self.current_menu_state = "weekly_event"
        lang = self.player.language
        event = current_weekly_event(getattr(self.cog, "weekly_events", []))
        if not event:
            self.log_message = t(lang, "weekly.unavailable", "公會目前沒有發布每週活動。")
            self.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_guild_menu", emoji="🔙")
            return

        progress = ensure_weekly_progress(self.player, event)
        goal = max(1, int(event.get("goal_kills", 5) or 5))
        title = tf(event, "title", lang) or event.get("id", "Weekly Event")
        description = tf(event, "description", lang) or ""
        bonus = tf(event, "bonus", lang) or ""
        if event.get("featured_all"):
            featured = t(lang, "weekly.all_monsters", "所有常規魔物")
        else:
            names = [self._weekly_monster_name(monster_id, lang) for monster_id in event.get("featured_monsters", [])]
            featured = "、".join(names[:6]) or t(lang, "weekly.no_featured", "本週無指定魔物")

        rewards = []
        reward_gold = max(0, int(event.get("reward_gold", 0) or 0))
        if reward_gold:
            rewards.append(t(lang, "weekly.reward_gold", "{gold} 金幣", gold=reward_gold))
        for item_id, qty in (event.get("reward_items") or {}).items():
            item_name = tf(self.cog.items.get(item_id, {}), "name", lang) or item_id
            rewards.append(f"{item_name} x{qty}")
        status = (
            t(lang, "weekly.progress_complete", "✅ 本週目標已完成（{goal}/{goal}）", goal=goal)
            if progress["rewarded"]
            else t(lang, "weekly.progress", "🎯 本週討伐進度：{kills}/{goal}", kills=progress["kills"], goal=goal)
        )
        lines = [
            t(lang, "weekly.board_title", "📌 【公會每週活動公告】"),
            f"🌟 **{title}**",
            description,
            "",
            t(lang, "weekly.featured", "👹 精選魔物：{monsters}", monsters=featured),
            f"✨ {bonus}",
            status,
            t(lang, "weekly.rewards", "🎁 完成獎勵：{rewards}", rewards="、".join(rewards)),
            t(lang, "weekly.reset", "⏳ 每週一 00:00 UTC 輪替"),
        ]
        if notice:
            lines.extend(["", notice])
        self.log_message = "\n".join(line for line in lines if line)
        self.add_action_button(label=t(lang, "weekly.btn_hunt", "前往活動地區"), style=discord.ButtonStyle.success, custom_id="btn_weekly_hunt", emoji="⚔️")
        self.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_guild_menu", emoji="🔙")

    async def handle_weekly_hunt(self):
        lang = self.player.language
        event = current_weekly_event(getattr(self.cog, "weekly_events", []))
        target_id = event.get("recommended_area", "area_01grassland") if event else "area_01grassland"
        area = self.cog.areas.get(target_id, {})
        real = getattr(self.player, "real_player", self.player)
        req_level = max(1, int(area.get("req_level", 1) or 1))
        if not area or real.level < req_level or not self._area_unlocked(area) or area.get("is_colosseum"):
            target_id = "area_01grassland"
            area = self.cog.areas.get(target_id, {})
            fallback = True
        else:
            fallback = False
        real.current_area = target_id
        real.current_subarea = None

        featured_ids = set(event.get("featured_monsters", [])) if event else set()
        visible = self._visible_subareas(area)
        preferred = next((sub for sub in visible if featured_ids.intersection(sub.get("monsters") or [])), None)
        if preferred is None and visible:
            preferred = visible[0]
        if preferred:
            real.current_subarea = preferred.get("id")
        self.build_main_menu()
        area_name = tf(area, "area_name", lang) or target_id
        if fallback:
            self.log_message = t(
                lang,
                "weekly.hunt_fallback",
                "⚔️ 精選魔物所在區域尚未解鎖，先前往【{area}】巡邏；擊敗任何魔物都會累積本週進度。",
                area=area_name,
            )
        else:
            self.log_message = t(lang, "weekly.hunt_arrived", "⚔️ 已前往【{area}】。點選「探索」開始本週狩獵！", area=area_name)
