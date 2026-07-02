"""DungeonMixin — the roguelike "無盡深淵地下城" (Endless Abyss Dungeon) UI flow.

Mixed into TRPGGameView (see trpg/view.py). Relies on attributes/methods defined on
the main view class and other mixins (self.cog, self.player, self.clear_items,
self.add_action_button, self.start_combat, self.build_battle_menu, self.build_main_menu,
self._dungeon_equipment_comparison_string, self._format_dungeon_item_effect_tags, ...).
"""

import random

import discord

from trpg.i18n import t, tf
from trpg import dungeon as dg
from trpg.view_shared import item_emoji
from trpg.balance import DUNGEON_MAX_FLOOR, DUNGEON_BOSS_FLOOR, DUNGEON_MINIBOSS_FLOORS


class DungeonMixin:
    def _dstate(self):
        return self.player.real_player.dungeon_state if hasattr(self.player, 'real_player') else self.player.dungeon_state

    def build_dungeon_menu(self):
        self.clear_items()
        lang = self.player.language
        d_state = self._dstate()

        # 沒有進行中的 run，或偵測到「舊格式」的 run（缺少改版後的欄位）→ 重新開一場乾淨的探索
        stale = "relics" not in d_state or "max_mp" not in d_state or "archetype" not in d_state
        if not d_state.get("in_run") or stale:
            dg.start_run(d_state)
            self.log_message = t(lang, "dungeon.intro", "🕳️ **【無盡深淵地下城】**\n你的真實力量在此被封印，將從零開始。靠著撿到的裝備、遺物(relic)與菁英傳授的技能打造流派，撐到第 15 層擊敗深淵領主！只有通關或死亡才會結算真實獎勵。")
            self.cog.save_players()

        if not d_state.get("archetype"):
            self.build_archetype_menu()
            return

        if d_state.get("floor_state") == "resolved":
            self._render_floor_resolved_menu()
            return

        # 頭目層（4/8/12/15）直接開打，不擲門；其餘樓層先選路：三扇門，
        # 其中一扇永遠是未知事件（見 dungeon.roll_doors）。
        floor = d_state.get("floor", 1)
        if dg.forced_boss_kind(floor):
            self._resolve_floor_room()
            return

        if d_state.get("floor_state") != "choosing":
            d_state["doors"] = dg.roll_doors(floor)
            d_state["floor_state"] = "choosing"
            self.cog.save_players()
        self.build_door_menu()

    _DOOR_DISPLAY = {
        "monster": ("⚔️", "戰鬥", "Battle"),
        "elite": ("💠", "菁英", "Elite"),
        "rest": ("🔥", "營火", "Campfire"),
        "event": ("❓", "未知", "Unknown"),
    }

    def build_door_menu(self):
        self.clear_items()
        lang = self.player.language
        d_state = self._dstate()
        floor = d_state.get("floor", 1)
        doors = d_state.get("doors") or dg.roll_doors(floor)

        prefix = f"{self.log_message}\n\n" if self.log_message else ""
        self.log_message = prefix + t(
            lang, "dungeon.door_prompt",
            "🏰 **深淵地下城 - 第 {floor}/{maxf} 層**\n❤️ {hp}/{maxhp}\n\n眼前有三扇門，門後的氣息隱約可辨。選擇你的道路：",
            floor=floor, maxf=DUNGEON_MAX_FLOOR,
            hp=d_state.get("current_hp", 0), maxhp=d_state.get("max_hp", 0),
        )
        for i, door_type in enumerate(doors):
            emoji, zh, en = self._DOOR_DISPLAY.get(door_type, ("❓", "未知", "Unknown"))
            label = f"{emoji} {en}" if lang == "en" else f"{emoji} {zh}"
            self.add_action_button(label=label, style=discord.ButtonStyle.primary, custom_id=f"ddoor_{i}")
        self.add_action_button(label=t(lang, "dungeon.btn_abandon", "放棄探索"), style=discord.ButtonStyle.danger, custom_id="btn_dung_flee", emoji="🏃")

    async def handle_dung_door(self, arg: str):
        d_state = self._dstate()
        if d_state.get("floor_state") != "choosing":
            return  # 連點/過期按鈕防呆
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
        self.cog.save_players()
        self._resolve_floor_room(room_type)

    def build_archetype_menu(self):
        self.clear_items()
        lang = self.player.language
        lines = [self.log_message, "", t(lang, "dungeon.archetype_prompt", "⚔️ 在踏入深淵之前，選擇你的起手流派（之後仍可靠戰利品與菁英傳授的技能轉型）：")]
        for aid, adef in dg.ARCHETYPES.items():
            name = tf(adef, "name", lang)
            desc = tf(adef, "desc", lang)
            lines.append(f"• **{name}** — {desc}")
            self.add_action_button(label=name, style=discord.ButtonStyle.primary, custom_id=f"darch_{aid}")
        self.log_message = "\n".join(lines)

    async def handle_dung_archetype(self, archetype_id: str):
        lang = self.player.language
        d_state = self._dstate()
        if not dg.apply_archetype(self.player, d_state, self.cog, archetype_id):
            self.build_archetype_menu()
            return
        adef = dg.ARCHETYPES[archetype_id]
        self.log_message = t(lang, "dungeon.archetype_chosen", "✅ 你選擇了【{name}】流派！{desc}", name=tf(adef, "name", lang), desc=tf(adef, "desc", lang))
        self.cog.save_players()
        self.build_dungeon_menu()

    def _render_floor_resolved_menu(self):
        lang = self.player.language
        d_state = self._dstate()
        floor = d_state.get("floor", 1)
        relics = len(d_state.get("relics", []))
        hp_line = f"{d_state.get('current_hp', 0)}/{d_state.get('max_hp', 0)}"
        prefix = f"{self.log_message}\n\n" if self.log_message else ""
        self.log_message = prefix + t(
            lang, "dungeon.floor_cleared_status",
            "🏰 **深淵地下城 - 第 {floor}/{maxf} 層**\n❤️ {hp} | 🗿 遺物 {relics}",
            floor=floor, maxf=DUNGEON_MAX_FLOOR, hp=hp_line, relics=relics,
        )
        self.add_action_button(label=t(lang, "dungeon.btn_next_floor", "🪜 前往下一層"), style=discord.ButtonStyle.primary, custom_id="btn_dung_next")
        self.add_action_button(label=t(lang, "dungeon.btn_abandon", "放棄探索"), style=discord.ButtonStyle.danger, custom_id="btn_dung_flee", emoji="🏃")

    def _resolve_floor_room(self, room_type: str = None):
        """結算玩家選中的那扇門（戰鬥／菁英／營火／未知事件）。room_type=None 只會
        發生在頭目層——那些樓層不擲門，直接開打。"""
        lang = self.player.language
        d_state = self._dstate()
        floor = d_state.get("floor", 1)

        boss_kind = dg.forced_boss_kind(floor)
        if boss_kind:
            mon = dg.build_monster(self.cog, floor, "boss")
            self.start_combat([mon])
            if boss_kind == "final_boss":
                self.log_message = t(lang, "dungeon.boss_floor_arrival", "🪜 你來到了第 {floor} 層... 深處傳來恐怖的咆哮聲——深淵領主就在前方！", floor=floor)
            else:
                self.log_message = t(lang, "dungeon.miniboss_floor_arrival", "🪜 你來到了第 {floor} 層... 一名地下城守衛頭目擋住了去路！擊敗牠將獲得一個遺物。", floor=floor)
            self.build_battle_menu()
            return

        rt = room_type or "event"
        if rt in ("monster", "elite"):
            mon = dg.build_monster(self.cog, floor, rt)
            self.start_combat([mon])
            if rt == "elite":
                self.log_message = t(lang, "dungeon.elite_encounter", "💠 菁英怪物擋住去路！擊敗牠能學會一個新技能！\n你遇到了 {monster_name}！", monster_name=tf(mon, "name", lang))
            else:
                self.log_message = t(lang, "dungeon.monster_encounter", "⚔️ 遭遇戰鬥！你遇到了 {monster_name}！", monster_name=tf(mon, "name", lang))
            self.build_battle_menu()
            return

        if rt == "rest":
            heal = int(d_state["max_hp"] * 0.4)
            d_state["current_hp"] = min(d_state["max_hp"], d_state.get("current_hp", 0) + heal)
            self.log_message = t(lang, "dungeon.room_rest_done", "🔥 你在房間裡升起營火好好休息，回復了 {heal} 點 HP！", heal=heal)
            d_state["floor_state"] = "resolved"
            self.cog.save_players()
            self.build_dungeon_menu()
            return

        # event：治療、陷阱、伏擊，或找到一點永久小加成（磨刀石／秘力泉水）——
        # 隨機事件不再只有安全的兩種結果，也不會每次都跟戰鬥有關。
        roll = random.random()
        if roll < 0.25:
            heal = int(d_state["max_hp"] * 0.3)
            d_state["current_hp"] = min(d_state["max_hp"], d_state.get("current_hp", 0) + heal)
            self.log_message = t(lang, "dungeon.event_heal", "✨ 你發現一池散發柔光的泉水，回復了 {heal} 點 HP！", heal=heal)
            d_state["floor_state"] = "resolved"
            self.cog.save_players()
            self.build_dungeon_menu()
            return
        if roll < 0.45:
            dmg = int(d_state["max_hp"] * 0.15)
            d_state["current_hp"] -= dmg
            self.log_message = t(lang, "dungeon.event_trap", "💥 不小心踩到陷阱！受到了 {dmg} 點傷害！", dmg=dmg)
            if d_state["current_hp"] <= 0:
                self._dungeon_run_over(t(lang, "dungeon.death_in_run", "\n💀 你在地下城中喪命了...所有臨時力量都消散了。"))
                return
            d_state["floor_state"] = "resolved"
            self.cog.save_players()
            self.build_dungeon_menu()
            return
        if roll < 0.65:
            # 30%：隨機事件其實是普通怪物的伏擊
            mon = dg.build_monster(self.cog, floor, "monster")
            self.start_combat([mon])
            self.log_message = t(lang, "dungeon.event_ambush", "😱 這根本是陷阱！一隻怪物從暗處撲了出來！\n你遇到了 {monster_name}！", monster_name=tf(mon, "name", lang))
            self.build_battle_menu()
            return
        if roll < 0.825:
            # 17.5%：磨刀石，永久（本次探索）小幅提升攻擊力
            bonus = d_state.setdefault("event_bonuses", {})
            gain = 3 + floor // 3
            bonus["atk"] = bonus.get("atk", 0) + gain
            dg.recompute_loadout(self.player, d_state, self.cog)
            self.log_message = t(lang, "dungeon.event_whetstone", "🗡️ 你找到一塊磨刀石，仔細打磨了武器！攻擊力永久提升 {gain} 點（本次探索有效）！", gain=gain)
            d_state["floor_state"] = "resolved"
            self.cog.save_players()
            self.build_dungeon_menu()
            return
        # 17.5%：秘力泉水，永久（本次探索）小幅提升最大HP，並當場回滿新增的血量
        bonus = d_state.setdefault("event_bonuses", {})
        gain = 12 + floor
        bonus["hp"] = bonus.get("hp", 0) + gain
        dg.recompute_loadout(self.player, d_state, self.cog)
        d_state["current_hp"] = min(d_state["max_hp"], d_state.get("current_hp", 0) + gain)
        self.log_message = t(lang, "dungeon.event_vitality_spring", "💧 你喝下了散發神秘力量的泉水，最大HP永久提升 {gain} 點（本次探索有效）！", gain=gain)
        d_state["floor_state"] = "resolved"
        self.cog.save_players()
        self.build_dungeon_menu()

    async def handle_dung_next(self):
        d_state = self._dstate()
        # 防呆：這顆按鈕只該在「本層已結算」時才有效——面板來不及重繪時連點兩次會
        # 讓 floor 一次跳兩層，還會把剛骰出來的下一層房間直接洗掉重骰一次。
        if d_state.get("floor_state") != "resolved":
            return
        d_state["floor"] = d_state.get("floor", 1) + 1
        d_state["floor_state"] = "pending"
        dg.recompute_loadout(self.player, d_state, self.cog)
        self.log_message = ""
        self.cog.save_players()
        self.build_dungeon_menu()

    def on_dungeon_victory(self, is_elite=False, is_boss=False, base_log=""):
        lang = self.player.language
        d_state = self._dstate()
        floor = d_state.get("floor", 1)

        if is_boss and floor >= DUNGEON_BOSS_FLOOR:
            real = self.player.real_player if hasattr(self.player, 'real_player') else self.player
            reward_gold = real.level * 2000
            reward_exp = real.level * 1500
            self.cog.adjust_bank(self.user_id, reward_gold)
            d_state["in_run"] = False
            dg.end_run(self.player)
            real.add_exp(reward_exp, self.cog.items)
            real.current_area = "area_00village"
            self.log_message = base_log + t(
                lang, "dungeon.cleared",
                "\n🎉 你擊敗了深淵領主，通關了無盡深淵！\n所有臨時力量消散，但你帶回了豐厚寶藏：\n💰 {reward_gold} 金幣\n✨ {reward_exp} 經驗值",
                reward_gold=reward_gold, reward_exp=reward_exp,
            )
            self.build_main_menu()
            return

        d_state["floor_state"] = "resolved"

        # 樓層 5/10 的守衛頭目 → 遺物三選一（遺物只從這兩個頭目取得，避免太浮濫）；
        # 菁英 → 技能三選一（讓玩家中途轉型流派）；一般怪 → 裝備三選一。
        if is_boss and floor in DUNGEON_MINIBOSS_FLOORS:
            relics = dg.roll_relics(self.cog, d_state, floor, 3)
            if relics:
                d_state["pending_relics"] = relics
                self.cog.save_players()
                self.build_dungeon_relic_menu(base_log)
                return
            # 遺物已收集完 → 退而給裝備
        elif is_elite:
            skills = dg.roll_dungeon_skills(self.cog, d_state, 3)
            if skills:
                d_state["pending_skills"] = skills
                self.cog.save_players()
                self.build_dungeon_skill_menu(base_log)
                return
            # 技能池已收集完 → 退而給裝備

        loot = dg.roll_loot(self.cog, floor, 3)
        d_state["pending_loot"] = loot
        self.cog.save_players()
        self.build_dungeon_loot_menu(base_log)

    def build_dungeon_loot_menu(self, base_log=""):
        self.clear_items()
        lang = self.player.language
        d_state = self._dstate()
        loot = d_state.get("pending_loot", [])
        lines = [base_log, "", t(lang, "dungeon.loot_header", "🎁 戰利品！選擇一件帶走：")]

        # 👇 先列出目前身上封印裝備的名稱，讓玩家在比較加成前，先知道自己現在
        # 到底穿著什麼——單看數值增減提示，脫離脈絡的話容易看不懂在跟誰比。
        cur_names = []
        for slot, slot_label in (("weapon", "⚔️"), ("armor", "🛡️"), ("accessory", "💍")):
            cur_id = d_state.get(slot)
            cur_def = self.cog.dungeon_items.get(cur_id, {}) if cur_id else {}
            cur_name = tf(cur_def, "name", lang) if cur_id else t(lang, "dungeon.loot_slot_empty", "（無）")
            cur_names.append(f"{slot_label}{cur_name}")
        lines.append(t(lang, "dungeon.loot_current_gear", "目前裝備：{gear}", gear=" ".join(cur_names)))

        for i, iid in enumerate(loot):
            idef = self.cog.dungeon_items.get(iid, {})
            name = tf(idef, "name", lang)
            desc = tf(idef, "desc", lang)
            rarity = idef.get("rarity", "common")
            comp_str = self._dungeon_equipment_comparison_string(idef)
            comp_suffix = f" {comp_str}" if comp_str else ""
            effect_str = self._format_dungeon_item_effect_tags(idef)
            effect_suffix = f" {effect_str}" if effect_str else ""
            lines.append(f"{i+1}. [{rarity}] {item_emoji(idef)} {name} — {desc}{comp_suffix}{effect_suffix}")
            self.add_action_button(label=f"{item_emoji(idef)} {name}"[:70], style=discord.ButtonStyle.success, custom_id=f"dpick_{i}", emoji="🎁")
        self.add_action_button(label=t(lang, "dungeon.loot_skip", "略過（不更換裝備）"), style=discord.ButtonStyle.secondary, custom_id="dpick_skip")
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
                self.log_message = t(lang, "dungeon.loot_equipped", "✅ 你裝備了【{name}】！", name=tf(idef, "name", lang))
        d_state["pending_loot"] = []
        self.cog.save_players()
        self.build_dungeon_menu()

    def build_dungeon_relic_menu(self, base_log=""):
        self.clear_items()
        lang = self.player.language
        d_state = self._dstate()
        relics = d_state.get("pending_relics", [])
        lines = [base_log, "", t(lang, "dungeon.relic_header", "💠 擊敗守衛頭目！選擇一個遺物（永久強化本次探索）：")]
        for i, rid in enumerate(relics):
            rdef = self.cog.dungeon_relics.get(rid, {})
            name = tf(rdef, "name", lang)
            desc = tf(rdef, "desc", lang)
            rarity = rdef.get("rarity", "common")
            lines.append(f"{i+1}. [{rarity}] 🗿 {name} — {desc}")
            self.add_action_button(label=f"🗿 {name}"[:70], style=discord.ButtonStyle.success, custom_id=f"drelic_{i}")
        self.add_action_button(label=t(lang, "dungeon.relic_skip", "略過"), style=discord.ButtonStyle.secondary, custom_id="drelic_skip")
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
                self.log_message = t(lang, "dungeon.relic_taken", "🗿 你獲得了遺物【{name}】！\n{desc}", name=tf(rdef, "name", lang), desc=tf(rdef, "desc", lang))
        d_state["pending_relics"] = []
        self.cog.save_players()
        self.build_dungeon_menu()

    def build_dungeon_skill_menu(self, base_log=""):
        self.clear_items()
        lang = self.player.language
        d_state = self._dstate()
        skills = d_state.get("pending_skills", [])
        lines = [base_log, "", t(lang, "dungeon.skill_header", "💠 擊敗菁英！選擇一個技能學會（立即可用，能讓你臨時轉換流派）：")]
        for i, sid in enumerate(skills):
            sdef = self.cog.skills.get(sid, {})
            name = tf(sdef, "name", lang)
            desc = tf(sdef, "desc", lang)
            lines.append(f"{i+1}. ✨ {name} — {desc}")
            self.add_action_button(label=f"✨ {name}"[:70], style=discord.ButtonStyle.success, custom_id=f"dskill_{i}")
        self.add_action_button(label=t(lang, "dungeon.skill_skip", "略過"), style=discord.ButtonStyle.secondary, custom_id="dskill_skip")
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
                self.log_message = t(lang, "dungeon.skill_learned", "✨ 你學會了技能【{name}】！\n{desc}", name=tf(sdef, "name", lang), desc=tf(sdef, "desc", lang))
        d_state["pending_skills"] = []
        self.cog.save_players()
        self.build_dungeon_menu()

    def _dungeon_run_over(self, reason_log: str):
        """地下城死亡/結束：清掉 run 狀態與遺物效果，回到村莊。"""
        d_state = self._dstate()
        d_state["in_run"] = False
        dg.end_run(self.player)
        real = self.player.real_player if hasattr(self.player, 'real_player') else self.player
        real.current_area = "area_00village"
        self.log_message = reason_log
        self.cog.save_players()
        self.build_main_menu()

    async def handle_dungeon_flee(self):
        lang = self.player.language
        self._dungeon_run_over(t(lang, "dungeon.flee_notice", "🏃 你帶著遺憾離開了地下城。所有臨時裝備、遺物與經驗都化為烏有了。"))
