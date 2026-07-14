"""Dungeon mixin for the Endless Abyss roguelike flow."""

import random

import discord

from trpg import dungeon as dg
from trpg.balance import (
    DUNGEON_BOSS_FLOOR,
    DUNGEON_MAX_FLOOR,
    DUNGEON_MINIBOSS_FLOORS,
)
from trpg.i18n import t, tf
from trpg.view_shared import item_emoji


class DungeonMixin:
    def _dstate(self):
        return self.player.real_player.dungeon_state if hasattr(self.player, "real_player") else self.player.dungeon_state

    def build_dungeon_menu(self):
        self.clear_items()
        lang = self.player.language
        d_state = self._dstate()

        stale = "relics" not in d_state or "max_mp" not in d_state or "archetype" not in d_state
        if not d_state.get("in_run") or stale:
            dg.start_run(d_state)
            self.log_message = t(
                lang,
                "dungeon.intro",
                "**Endless Abyss Dungeon**\nStart from scratch inside the dungeon. Build your run with gear, relics, and temporary skills. Clear Floor 15 or die trying.",
            )
            self.cog.save_players(player=self.player)

        if not d_state.get("archetype"):
            self.build_archetype_menu()
            return

        if d_state.get("floor_state") == "resolved":
            self._render_floor_resolved_menu()
            return

        floor = d_state.get("floor", 1)
        if dg.forced_boss_kind(floor):
            self._resolve_floor_room()
            return

        if d_state.get("floor_state") != "choosing":
            d_state["doors"] = dg.roll_doors(floor)
            d_state["floor_state"] = "choosing"
            self.cog.save_players(player=self.player)
        self.build_door_menu()

    _DOOR_DISPLAY = {
        "monster": "Battle",
        "elite": "Elite",
        "rest": "Campfire",
        "event": "Unknown",
    }

    def build_door_menu(self):
        self.clear_items()
        lang = self.player.language
        d_state = self._dstate()
        floor = d_state.get("floor", 1)
        doors = d_state.get("doors") or dg.roll_doors(floor)

        prefix = f"{self.log_message}\n\n" if self.log_message else ""
        self.log_message = prefix + t(
            lang,
            "dungeon.door_prompt",
            "**Dungeon Floor {floor}/{maxf}**\nHP: {hp}/{maxhp}\n\nChoose one door.",
            floor=floor,
            maxf=DUNGEON_MAX_FLOOR,
            hp=d_state.get("current_hp", 0),
            maxhp=d_state.get("max_hp", 0),
        )
        for i, door_type in enumerate(doors):
            label = self._DOOR_DISPLAY.get(door_type, "Unknown")
            self.add_action_button(label=label, style=discord.ButtonStyle.primary, custom_id=f"ddoor_{i}")
        self.add_action_button(
            label=t(lang, "dungeon.btn_abandon", "Abandon Run"),
            style=discord.ButtonStyle.danger,
            custom_id="btn_dung_flee",
        )

    async def handle_dung_door(self, arg: str):
        d_state = self._dstate()
        if d_state.get("floor_state") != "choosing":
            return
        doors = d_state.get("doors") or []
        try:
            idx = int(arg)
        except ValueError:
            return
        if not (0 <= idx < len(doors)):
            return
        room_type = doors[idx]
        d_state["floor_state"] = "pending"
        d_state["doors"] = []
        self.cog.save_players(player=self.player)
        self._resolve_floor_room(room_type)

    def build_archetype_menu(self):
        self.clear_items()
        lang = self.player.language
        lines = [self.log_message, "", t(lang, "dungeon.archetype_prompt", "Choose a starting archetype for this run.")]
        for aid, adef in dg.ARCHETYPES.items():
            name = tf(adef, "name", lang)
            desc = tf(adef, "desc", lang)
            lines.append(f"- **{name}** - {desc}")
            self.add_action_button(label=name, style=discord.ButtonStyle.primary, custom_id=f"darch_{aid}")
        self.log_message = "\n".join(lines)

    async def handle_dung_archetype(self, archetype_id: str):
        lang = self.player.language
        d_state = self._dstate()
        if not dg.apply_archetype(self.player, d_state, self.cog, archetype_id):
            self.build_archetype_menu()
            return
        adef = dg.ARCHETYPES[archetype_id]
        self.log_message = t(
            lang,
            "dungeon.archetype_chosen",
            "You chose {name}: {desc}",
            name=tf(adef, "name", lang),
            desc=tf(adef, "desc", lang),
        )
        self.cog.save_players(player=self.player)
        self.build_dungeon_menu()

    def _render_floor_resolved_menu(self):
        lang = self.player.language
        d_state = self._dstate()
        floor = d_state.get("floor", 1)
        relics = len(d_state.get("relics", []))
        hp_line = f"{d_state.get('current_hp', 0)}/{d_state.get('max_hp', 0)}"
        prefix = f"{self.log_message}\n\n" if self.log_message else ""
        self.log_message = prefix + t(
            lang,
            "dungeon.floor_cleared_status",
            "**Dungeon Floor {floor}/{maxf}**\nHP: {hp} | Relics: {relics}",
            floor=floor,
            maxf=DUNGEON_MAX_FLOOR,
            hp=hp_line,
            relics=relics,
        )
        self.add_action_button(label=t(lang, "dungeon.btn_next_floor", "Next Floor"), style=discord.ButtonStyle.primary, custom_id="btn_dung_next")
        self.add_action_button(label=t(lang, "dungeon.btn_abandon", "Abandon Run"), style=discord.ButtonStyle.danger, custom_id="btn_dung_flee")

    def _resolve_floor_room(self, room_type: str = None):
        """Resolve the room for the current dungeon floor."""
        lang = self.player.language
        d_state = self._dstate()
        floor = d_state.get("floor", 1)

        boss_kind = dg.forced_boss_kind(floor)
        if boss_kind:
            mon = dg.build_monster(self.cog, floor, "boss")
            self.start_combat([mon])
            if boss_kind == "final_boss":
                self.log_message = t(lang, "dungeon.boss_floor_arrival", "Floor {floor}. The Abyss Lord awaits.", floor=floor)
            else:
                self.log_message = t(lang, "dungeon.miniboss_floor_arrival", "Floor {floor}. A powerful miniboss blocks the path.", floor=floor)
            self.build_battle_menu()
            return

        rt = room_type or "event"
        if rt in ("monster", "elite"):
            mon = dg.build_monster(self.cog, floor, rt)
            self.start_combat([mon])
            if rt == "elite":
                self.log_message = t(lang, "dungeon.elite_encounter", "💠 精英怪物擋住了去路！\n你遭遇了【{monster_name}】！", monster_name=tf(mon, "name", lang))
            else:
                self.log_message = t(lang, "dungeon.monster_encounter", "⚔️ 怪物從陰影中現身！\n你遭遇了【{monster_name}】！", monster_name=tf(mon, "name", lang))
            self.build_battle_menu()
            return

        if rt == "rest":
            heal = int(d_state["max_hp"] * 0.4)
            d_state["current_hp"] = min(d_state["max_hp"], d_state.get("current_hp", 0) + heal)
            self.log_message = t(lang, "dungeon.room_rest_done", "You rest at the campfire and recover {heal} HP.", heal=heal)
            d_state["floor_state"] = "resolved"
            self.cog.save_players(player=self.player)
            self.build_dungeon_menu()
            return

        roll = random.random()
        if roll < 0.25:
            heal = int(d_state["max_hp"] * 0.3)
            d_state["current_hp"] = min(d_state["max_hp"], d_state.get("current_hp", 0) + heal)
            self.log_message = t(lang, "dungeon.event_heal", "A healing spring restores {heal} HP.", heal=heal)
            d_state["floor_state"] = "resolved"
            self.cog.save_players(player=self.player)
            self.build_dungeon_menu()
            return
        if roll < 0.45:
            dmg = int(d_state["max_hp"] * 0.15)
            d_state["current_hp"] -= dmg
            self.log_message = t(lang, "dungeon.event_trap", "A trap deals {dmg} damage.", dmg=dmg)
            if d_state["current_hp"] <= 0:
                self._dungeon_run_over(t(lang, "dungeon.death_in_run", "\nYou died in the dungeon."))
                return
            d_state["floor_state"] = "resolved"
            self.cog.save_players(player=self.player)
            self.build_dungeon_menu()
            return
        if roll < 0.65:
            mon = dg.build_monster(self.cog, floor, "monster")
            self.start_combat([mon])
            self.log_message = t(lang, "dungeon.event_ambush", "😱 是陷阱！【{monster_name}】從陰影中撲了出來！", monster_name=tf(mon, "name", lang))
            self.build_battle_menu()
            return
        if roll < 0.825:
            bonus = d_state.setdefault("event_bonuses", {})
            gain = 3 + floor // 3
            bonus["atk"] = bonus.get("atk", 0) + gain
            dg.recompute_loadout(self.player, d_state, self.cog)
            self.log_message = t(lang, "dungeon.event_whetstone", "You find a whetstone. ATK increases by {gain} for this run.", gain=gain)
            d_state["floor_state"] = "resolved"
            self.cog.save_players(player=self.player)
            self.build_dungeon_menu()
            return

        bonus = d_state.setdefault("event_bonuses", {})
        gain = 12 + floor
        bonus["hp"] = bonus.get("hp", 0) + gain
        dg.recompute_loadout(self.player, d_state, self.cog)
        d_state["current_hp"] = min(d_state["max_hp"], d_state.get("current_hp", 0) + gain)
        self.log_message = t(lang, "dungeon.event_vitality_spring", "A vitality spring increases max HP by {gain} for this run.", gain=gain)
        d_state["floor_state"] = "resolved"
        self.cog.save_players(player=self.player)
        self.build_dungeon_menu()

    async def handle_dung_next(self):
        d_state = self._dstate()
        if d_state.get("floor_state") != "resolved":
            return
        d_state["floor"] = d_state.get("floor", 1) + 1
        d_state["floor_state"] = "pending"
        dg.recompute_loadout(self.player, d_state, self.cog)
        self.log_message = ""
        self.cog.save_players(player=self.player)
        self.build_dungeon_menu()

    def on_dungeon_victory(self, is_elite=False, is_boss=False, base_log=""):
        lang = self.player.language
        d_state = self._dstate()
        floor = d_state.get("floor", 1)

        if is_boss and floor >= DUNGEON_BOSS_FLOOR:
            real = self.player.real_player if hasattr(self.player, "real_player") else self.player
            reward_gold = real.level * 2000
            reward_exp = real.level * 1500
            self.cog.adjust_bank(self.user_id, reward_gold)
            d_state["in_run"] = False
            dg.end_run(self.player)
            leveled_up = real.add_exp(reward_exp, self.cog.items, self.cog.skills)
            real.current_area = "area_00village"
            real.current_subarea = None
            self.log_message = base_log + t(
                lang,
                "dungeon.cleared",
                "\nYou cleared the Endless Abyss.\nFinal rewards:\n{reward_gold} gold\n{reward_exp} EXP",
                reward_gold=reward_gold,
                reward_exp=reward_exp,
            )
            if leveled_up:
                self.log_message += "\n" + t(lang, "dungeon.level_up", "🌟 地下城獎勵讓你升到了 Lv.{level}！", level=real.level)
            self.build_main_menu()
            return

        d_state["floor_state"] = "resolved"

        if is_boss and floor in DUNGEON_MINIBOSS_FLOORS:
            relics = dg.roll_relics(self.cog, d_state, floor, 3)
            if relics:
                d_state["pending_relics"] = relics
                self.cog.save_players(player=self.player)
                self.build_dungeon_relic_menu(base_log)
                return
        elif is_elite:
            skills = dg.roll_dungeon_skills(self.cog, d_state, 3)
            if skills:
                d_state["pending_skills"] = skills
                self.cog.save_players(player=self.player)
                self.build_dungeon_skill_menu(base_log)
                return

        loot = dg.roll_loot(self.cog, floor, 3)
        d_state["pending_loot"] = loot
        self.cog.save_players(player=self.player)
        self.build_dungeon_loot_menu(base_log)

    def build_dungeon_loot_menu(self, base_log=""):
        self.clear_items()
        lang = self.player.language
        d_state = self._dstate()
        loot = d_state.get("pending_loot", [])
        lines = [base_log, "", t(lang, "dungeon.loot_header", "Choose one piece of loot.")]

        cur_names = []
        for slot, slot_label in (("weapon", "Weapon:"), ("armor", "Armor:"), ("accessory", "Accessory:")):
            cur_id = d_state.get(slot)
            cur_def = self.cog.dungeon_items.get(cur_id, {}) if cur_id else {}
            cur_name = tf(cur_def, "name", lang) if cur_id else t(lang, "dungeon.loot_slot_empty", "Empty")
            cur_names.append(f"{slot_label} {cur_name}")
        lines.append(t(lang, "dungeon.loot_current_gear", "Current gear: {gear}", gear=" | ".join(cur_names)))

        for i, iid in enumerate(loot):
            idef = self.cog.dungeon_items.get(iid, {})
            name = tf(idef, "name", lang)
            desc = tf(idef, "desc", lang)
            rarity = idef.get("rarity", "common")
            comp_str = self._dungeon_equipment_comparison_string(idef)
            comp_suffix = f" {comp_str}" if comp_str else ""
            effect_str = self._format_dungeon_item_effect_tags(idef)
            effect_suffix = f" {effect_str}" if effect_str else ""
            lines.append(f"{i + 1}. [{rarity}] {item_emoji(idef)} {name} - {desc}{comp_suffix}{effect_suffix}")
            self.add_action_button(label=f"{item_emoji(idef)} {name}"[:70], style=discord.ButtonStyle.success, custom_id=f"dpick_{i}")
        self.add_action_button(label=t(lang, "dungeon.loot_skip", "Skip Loot"), style=discord.ButtonStyle.secondary, custom_id="dpick_skip")
        self.log_message = "\n".join(lines)

    async def handle_dung_loot_pick(self, arg: str):
        lang = self.player.language
        d_state = self._dstate()
        loot = d_state.get("pending_loot", [])
        if arg != "skip":
            try:
                idx = int(arg)
            except ValueError:
                idx = -1
            if 0 <= idx < len(loot):
                dg.equip_item(self.player, d_state, self.cog, loot[idx])
                idef = self.cog.dungeon_items.get(loot[idx], {})
                self.log_message = t(lang, "dungeon.loot_equipped", "Equipped {name}.", name=tf(idef, "name", lang))
        d_state["pending_loot"] = []
        self.cog.save_players(player=self.player)
        self.build_dungeon_menu()

    def build_dungeon_relic_menu(self, base_log=""):
        self.clear_items()
        lang = self.player.language
        d_state = self._dstate()
        relics = d_state.get("pending_relics", [])
        lines = [base_log, "", t(lang, "dungeon.relic_header", "Choose one relic.")]
        for i, rid in enumerate(relics):
            rdef = self.cog.dungeon_relics.get(rid, {})
            name = tf(rdef, "name", lang)
            desc = tf(rdef, "desc", lang)
            rarity = rdef.get("rarity", "common")
            lines.append(f"{i + 1}. [{rarity}] {name} - {desc}")
            self.add_action_button(label=f"{name}"[:70], style=discord.ButtonStyle.success, custom_id=f"drelic_{i}")
        self.add_action_button(label=t(lang, "dungeon.relic_skip", "Skip Relic"), style=discord.ButtonStyle.secondary, custom_id="drelic_skip")
        self.log_message = "\n".join(lines)

    async def handle_dung_relic_pick(self, arg: str):
        lang = self.player.language
        d_state = self._dstate()
        relics = d_state.get("pending_relics", [])
        if arg != "skip":
            try:
                idx = int(arg)
            except ValueError:
                idx = -1
            if 0 <= idx < len(relics):
                dg.grant_relic(self.player, d_state, self.cog, relics[idx])
                rdef = self.cog.dungeon_relics.get(relics[idx], {})
                self.log_message = t(
                    lang,
                    "dungeon.relic_taken",
                    "You obtained the relic {name}.\n{desc}",
                    name=tf(rdef, "name", lang),
                    desc=tf(rdef, "desc", lang),
                )
        d_state["pending_relics"] = []
        self.cog.save_players(player=self.player)
        self.build_dungeon_menu()

    def build_dungeon_skill_menu(self, base_log=""):
        self.clear_items()
        lang = self.player.language
        d_state = self._dstate()
        skills = d_state.get("pending_skills", [])
        lines = [base_log, "", t(lang, "dungeon.skill_header", "Choose one temporary skill for this run.")]
        for i, sid in enumerate(skills):
            sdef = self.cog.skills.get(sid, {})
            name = tf(sdef, "name", lang)
            desc = tf(sdef, "desc", lang)
            lines.append(f"{i + 1}. {name} - {desc}")
            self.add_action_button(label=f"{name}"[:70], style=discord.ButtonStyle.success, custom_id=f"dskill_{i}")
        self.add_action_button(label=t(lang, "dungeon.skill_skip", "Skip Skill"), style=discord.ButtonStyle.secondary, custom_id="dskill_skip")
        self.log_message = "\n".join(lines)

    async def handle_dung_skill_pick(self, arg: str):
        lang = self.player.language
        d_state = self._dstate()
        skills = d_state.get("pending_skills", [])
        if arg != "skip":
            try:
                idx = int(arg)
            except ValueError:
                idx = -1
            if 0 <= idx < len(skills):
                dg.grant_skill(d_state, skills[idx])
                sdef = self.cog.skills.get(skills[idx], {})
                self.log_message = t(
                    lang,
                    "dungeon.skill_learned",
                    "You learned {name} for this run.\n{desc}",
                    name=tf(sdef, "name", lang),
                    desc=tf(sdef, "desc", lang),
                )
        d_state["pending_skills"] = []
        self.cog.save_players(player=self.player)
        self.build_dungeon_menu()

    def _dungeon_run_over(self, reason_log: str):
        """End the current dungeon run and return to the village."""
        d_state = self._dstate()
        d_state["in_run"] = False
        dg.end_run(self.player)
        real = self.player.real_player if hasattr(self.player, "real_player") else self.player
        real.current_area = "area_00village"
        real.current_subarea = None
        self.log_message = reason_log
        self.cog.save_players(player=self.player)
        self.build_main_menu()

    async def handle_dungeon_flee(self):
        lang = self.player.language
        self._dungeon_run_over(t(lang, "dungeon.flee_notice", "You abandon the dungeon run. Rewards from this run are lost."))
