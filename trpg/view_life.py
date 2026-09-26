"""Discord menu for the daily gathering, mining, farming, and food loop."""

import discord

from trpg.i18n import t, tf
from trpg.life_skills import (
    CROPS,
    DAILY_ENERGY,
    available_energy,
    consume_life_food,
    ensure_life_state,
    farm_needs_water,
    farm_ready,
    gather,
    harvest_farm,
    life_level,
    plant_crop,
    spend_energy,
    water_farm,
)


class LifeSkillMixin:
    def _life_crop_name(self, crop: str, lang: str) -> str:
        names = {
            "wheat": t(lang, "life.crop_wheat", "小麥"),
            "hearty_carrot": t(lang, "life.crop_hearty_carrot", "元氣胡蘿蔔"),
            "moon_berry": t(lang, "life.crop_moon_berry", "月光莓"),
        }
        return names.get(crop, crop)

    def _life_drop_text(self, drops: dict, lang: str) -> str:
        parts = []
        for item_id, qty in drops.items():
            item_name = tf(self.cog.items.get(item_id, {}), "name", lang) or item_id
            parts.append(f"{item_name} x{qty}")
        return "、".join(parts)

    def _grant_life_drops(self, drops: dict) -> None:
        real = getattr(self.player, "real_player", self.player)
        for item_id, qty in drops.items():
            real.inventory[item_id] = real.inventory.get(item_id, 0) + qty

    def build_life_menu(self, notice: str = ""):
        self.clear_items()
        self.current_menu_state = "life"
        lang = self.player.language
        state = ensure_life_state(self.player)
        farm = state["farm"]
        if not farm.get("crop"):
            farm_status = t(lang, "life.farm_empty", "🌱 農田空著。選擇作物播種後，每天記得澆水。")
        elif farm_ready(state):
            farm_status = t(
                lang,
                "life.farm_ready",
                "🌾 {crop}成熟了！已完成 {waterings}/{required} 次澆水，可以收成。",
                crop=self._life_crop_name(farm["crop"], lang),
                waterings=farm["waterings"],
                required=CROPS[farm["crop"]]["waterings_required"],
            )
        elif farm_needs_water(state):
            farm_status = t(
                lang,
                "life.farm_needs_water",
                "💧 {crop}今天還沒澆水（{waterings}/{required}）。漏澆只會停止生長，不會枯死。",
                crop=self._life_crop_name(farm["crop"], lang),
                waterings=farm["waterings"],
                required=CROPS[farm["crop"]]["waterings_required"],
            )
        else:
            farm_status = t(
                lang,
                "life.farm_watered_today",
                "💦 {crop}今天已澆水（{waterings}/{required}），明天再來照料。",
                crop=self._life_crop_name(farm["crop"], lang),
                waterings=farm["waterings"],
                required=CROPS[farm["crop"]]["waterings_required"],
            )
        energy = state["energy"]
        lines = [
            t(lang, "life.title", "🏡 【米酥村休閒生活】"),
            t(lang, "life.intro", "每天伐木、釣魚、採礦、農作各有 20 次。農作物每天可澆水一次，成熟後能食用補充採集行動力，也能賣錢。"),
            "",
            t(
                lang,
                "life.energy",
                "⚡ 今日額度：伐木 {wood}/20｜釣魚 {fish}/20｜採礦 {mine}/20｜農作 {farm}/20｜額外 {bonus}/20",
                wood=energy["woodcutting"],
                fish=energy["fishing"],
                mine=energy["mining"],
                farm=energy["farming"],
                bonus=state["bonus_energy"],
            ),
            t(
                lang,
                "life.levels",
                "🪓 伐木 Lv.{wood}　🎣 釣魚 Lv.{fish}　⛏️ 採礦 Lv.{mine}　🌾 農耕 Lv.{farm}",
                wood=life_level(state, "woodcutting"),
                fish=life_level(state, "fishing"),
                mine=life_level(state, "mining"),
                farm=life_level(state, "farming"),
            ),
            farm_status,
        ]
        if notice:
            lines.extend(["", notice])
        self.log_message = "\n".join(lines)
        self.add_action_button(label=t(lang, "life.btn_woodcut", "砍樹"), style=discord.ButtonStyle.success, custom_id="life_woodcut", emoji="🪓", disabled=available_energy(state, "woodcutting") <= 0, row=0)
        self.add_action_button(label=t(lang, "life.btn_fish", "釣魚"), style=discord.ButtonStyle.primary, custom_id="life_fish", emoji="🎣", disabled=available_energy(state, "fishing") <= 0, row=0)
        self.add_action_button(label=t(lang, "life.btn_mine", "採礦"), style=discord.ButtonStyle.secondary, custom_id="life_mine", emoji="⛏️", disabled=available_energy(state, "mining") <= 0, row=0)

        if not farm.get("crop"):
            farming_level = life_level(state, "farming")
            crop_options = []
            for crop, crop_data in CROPS.items():
                if farming_level < crop_data["unlock_level"]:
                    continue
                crop_name = self._life_crop_name(crop, lang)
                description = t(
                    lang,
                    "life.crop_plant_desc",
                    "需要每天澆水，共 {days} 次",
                    days=crop_data["waterings_required"],
                )
                crop_options.append((crop_name, f"life_plant_{crop}", description, "🌱"))
            self.add_action_select(t(lang, "life.select_crop", "🌱 選擇要播種的作物"), crop_options, row=1, custom_id="sel_life_crop")
        elif farm_ready(state):
            self.add_action_button(
                label=t(lang, "life.btn_harvest", "收成"),
                style=discord.ButtonStyle.success,
                custom_id="life_harvest",
                emoji="🌾",
                row=1,
                disabled=available_energy(state, "farming") <= 0,
            )
        else:
            self.add_action_button(
                label=t(lang, "life.btn_water", "澆水"),
                style=discord.ButtonStyle.primary,
                custom_id="life_water",
                emoji="💧",
                row=1,
                disabled=not farm_needs_water(state) or available_energy(state, "farming") <= 0,
            )

        if state["bonus_energy"] < DAILY_ENERGY:
            food_options = []
            for item_id, qty in self.player.inventory.items():
                item = self.cog.items.get(item_id, {})
                restore = int(item.get("life_energy_restore", 0) or 0)
                if qty <= 0 or restore <= 0:
                    continue
                name = tf(item, "name", lang) or item_id
                label = t(lang, "life.food_option", "{name} x{qty}（+{restore}）", name=name, qty=qty, restore=restore)
                food_options.append((label, f"life_eat_{item_id}", t(lang, "life.food_desc", "食用後補充生活行動力"), "🍽️"))
            self.add_action_select(t(lang, "life.select_food", "🍽️ 食用作物補充額外行動力"), food_options, row=2, custom_id="sel_life_food")
        self.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙", row=4)

    async def handle_life_gather(self, skill: str):
        lang = self.player.language
        state = ensure_life_state(self.player)
        if not spend_energy(state, skill):
            self.build_life_menu(t(lang, "life.no_energy", "這項活動今天的 20 次額度與額外行動力都用完了。"))
            return
        result = gather(state, skill)
        self._grant_life_drops(result["drops"])
        activities = {
            "woodcutting": t(lang, "life.activity_woodcutting", "砍樹"),
            "fishing": t(lang, "life.activity_fishing", "釣魚"),
            "mining": t(lang, "life.activity_mining", "採礦"),
        }
        activity = activities.get(skill, skill)
        notice = t(
            lang,
            "life.gather_result",
            "{activity}完成！獲得：{items}（熟練度 +{xp}）",
            activity=activity,
            items=self._life_drop_text(result["drops"], lang),
            xp=result["xp"],
        )
        if result["new_level"] > result["old_level"]:
            notice += "\n" + t(lang, "life.level_up", "🎉 {activity}提升至 Lv.{level}！", activity=activity, level=result["new_level"])
        self.build_life_menu(notice)

    async def handle_life_plant(self, crop: str = "wheat"):
        state = ensure_life_state(self.player)
        lang = self.player.language
        crop_name = self._life_crop_name(crop, lang)
        crop_data = CROPS.get(crop)
        if not crop_data:
            notice = t(lang, "life.invalid_crop", "這種作物不存在。")
        elif life_level(state, "farming") < crop_data["unlock_level"]:
            notice = t(lang, "life.crop_locked", "🔒 種植{crop}需要農耕 Lv.{level}。", crop=crop_name, level=crop_data["unlock_level"])
        elif state["farm"].get("crop"):
            notice = t(lang, "life.plot_busy", "這塊田已經種著作物了，收成後才能播種下一批。")
        elif available_energy(state, "farming") <= 0:
            notice = t(lang, "life.no_energy", "這項活動今天的 20 次額度已經用完了。")
        elif plant_crop(state, crop):
            spend_energy(state, "farming")
            notice = t(
                lang,
                "life.planted",
                "🌱 已播種{crop}！需要每天澆水，共 {days} 次才會成熟。今天就可以先澆第一次水。",
                crop=crop_name,
                days=crop_data["waterings_required"],
            )
        else:
            notice = t(lang, "life.plot_busy", "這塊田已經種著作物了。")
        self.build_life_menu(notice)

    async def handle_life_water(self):
        state = ensure_life_state(self.player)
        lang = self.player.language
        farm = state["farm"]
        if not farm.get("crop"):
            self.build_life_menu(t(lang, "life.not_ready", "請先選擇一種農作物。"))
            return
        if farm_ready(state):
            self.build_life_menu(t(lang, "life.already_mature", "作物已經成熟，直接收成即可。"))
            return
        if not farm_needs_water(state):
            self.build_life_menu(t(lang, "life.already_watered", "今天已經澆過水了，明天再來吧！"))
            return
        if not spend_energy(state, "farming"):
            self.build_life_menu(t(lang, "life.no_energy", "這項活動今天的 20 次額度已經用完了。"))
            return
        result = water_farm(state)
        crop_name = self._life_crop_name(result["crop"], lang)
        if result["ready"]:
            notice = t(
                lang,
                "life.water_ready",
                "💧 已為{crop}澆水（{waterings}/{required}）！作物成熟了，現在可以收成。",
                crop=crop_name,
                waterings=result["waterings"],
                required=result["required"],
            )
        else:
            notice = t(
                lang,
                "life.water_result",
                "💧 已為{crop}澆水（{waterings}/{required}）。明天再來照料吧！（農耕熟練度 +{xp}）",
                crop=crop_name,
                waterings=result["waterings"],
                required=result["required"],
                xp=result["xp"],
            )
        if result["new_level"] > result["old_level"]:
            notice += "\n" + t(lang, "life.level_up", "🎉 {activity}提升至 Lv.{level}！", activity=t(lang, "life.activity_farming", "農耕"), level=result["new_level"])
        self.build_life_menu(notice)

    async def handle_life_eat(self, item_id: str):
        state = ensure_life_state(self.player)
        lang = self.player.language
        item = self.cog.items.get(item_id, {})
        item_name = tf(item, "name", lang) or item_id
        restored = consume_life_food(state, self.player.inventory, item_id, item.get("life_energy_restore", 0))
        if restored:
            notice = t(lang, "life.food_eaten", "🍽️ 吃下【{name}】，恢復 {amount} 點生活行動力！", name=item_name, amount=restored)
        elif state["bonus_energy"] >= DAILY_ENERGY:
            notice = t(lang, "life.energy_full", "額外生活行動力已經全滿，不需要進食。")
        else:
            notice = t(lang, "life.food_missing", "背包裡沒有可食用的【{name}】。", name=item_name)
        self.build_life_menu(notice)

    async def handle_life_harvest(self):
        state = ensure_life_state(self.player)
        lang = self.player.language
        if not farm_ready(state):
            self.build_life_menu(t(lang, "life.not_ready", "請先選擇一種農作物。"))
            return
        if not spend_energy(state, "farming"):
            self.build_life_menu(t(lang, "life.no_energy", "這項活動今天的 20 次額度與額外行動力都用完了。"))
            return
        result = harvest_farm(state)
        if result is None:
            self.build_life_menu(t(lang, "life.not_ready", "請先選擇一種農作物。"))
            return
        self._grant_life_drops(result["drops"])
        notice = t(
            lang,
            "life.harvest_result",
            "🌾 收成完成！獲得：{items}（農耕熟練度 +{xp}）",
            items=self._life_drop_text(result["drops"], lang),
            xp=result["xp"],
        )
        if result["new_level"] > result["old_level"]:
            notice += "\n" + t(lang, "life.level_up", "🎉 {activity}提升至 Lv.{level}！", activity=t(lang, "life.activity_farming", "農耕"), level=result["new_level"])
        self.build_life_menu(notice)
