import random
import discord

from trpg.balance import MYSTERY_MERCHANT_STOCK_COUNT, area_shop_config, area_shop_stock
from trpg.combat import get_sell_price
from trpg.i18n import t, tf
from trpg.player import _item_shop_level_ok
from trpg.recipes import CRAFTING_RECIPES, MAX_UPGRADE_LEVEL, UPGRADE_COSTS
from trpg.stats import format_item_stat_requirements, recalc_player_stats
from trpg.view_shared import item_emoji


ELUNE_CRAFT_QUESTS = frozenset(("npc_elune_1", "npc_elune_2"))


def crafting_station_unlocked(player) -> bool:
    real = getattr(player, "real_player", player)
    return (
        real.current_area == "area_05forest"
        and ELUNE_CRAFT_QUESTS.issubset(set(real.completed_quests))
    )


def blacksmith_accessible(player) -> bool:
    real = getattr(player, "real_player", player)
    return real.current_area == "area_20lab"


class ShopMixin:
    def _format_shop_item_line(self, item_id: str) -> str:
        lang = self.player.language
        item = self.cog.items.get(item_id, {})
        req = item.get("exclusive_level", 0)
        req_str = t(lang, "shop.req_level_suffix", " | 需 Lv.{req}", req=req) if req else ""
        item_name = tf(item, "name", lang) if item else item_id
        item_desc = tf(item, "desc", lang) if item else ""
        line = f"• {item_emoji(item)} {item_name}{req_str} | {item.get('price', 0)}$ | {item_desc}"

        if item.get("type") in ("weapon", "armor", "accessory"):
            stat_req = format_item_stat_requirements(item, lang=lang)
            if stat_req:
                line += f" [{stat_req}]"
            comp_str = self._get_equipment_comparison_string(item)
            if comp_str:
                line += f" {comp_str}"
            effect_str = self._format_item_effect_tags(item)
            if effect_str:
                line += f" {effect_str}"

        if item.get("type") == "skill_scroll":
            skill = self.cog.skills.get(item.get("teaches", ""), {})
            skill_desc = tf(skill, "desc", lang) if skill else ""
            if skill_desc:
                line += f"\n  ↳ {skill_desc}"
        return line

    def _shop_area_config(self, area_id: str) -> dict:
        return area_shop_config(area_id)

    def _shop_state(self) -> dict:
        if not isinstance(getattr(self.player, "shop_state", None), dict):
            self.player.shop_state = {}
        return self.player.shop_state.setdefault(self.player.current_area, {})

    def _roll_shop_stock(self):
        """Area shops are fixed; keep the player state as a cache for UI/buy flow."""
        state = self._shop_state()
        fixed_stock = [item_id for item_id in area_shop_stock(self.player.current_area) if item_id in self.cog.items]
        if state.get("items") != fixed_stock:
            state["items"] = fixed_stock
            self.cog.save_players(player=self.player)

    def _mystery_merchant_pool(self) -> list[str]:
        pool = []
        for item_id, item in self.cog.items.items():
            if item.get("shop_weight", 0) <= 0:
                continue
            if not _item_shop_level_ok(self.player, item_id, item, self.cog.skills):
                continue
            pool.append(item_id)
        return pool

    def _roll_mystery_merchant_stock(self, force: bool = False) -> list[str]:
        state = self._shop_state()
        if force or not state.get("mystery_items"):
            pool = self._mystery_merchant_pool()
            count = min(MYSTERY_MERCHANT_STOCK_COUNT, len(pool))
            state["mystery_items"] = random.sample(pool, count) if count else []
            state["mystery_active"] = True
            self.cog.save_players(player=self.player)
        return [item_id for item_id in state.get("mystery_items", []) if item_id in self.cog.items]

    async def handle_shop_menu(self, notice=""):
        self._roll_shop_stock()
        self.current_menu_state = "shop"
        lang = self.player.language
        area_id = self.player.current_area
        state = self._shop_state()
        area_cfg = self._shop_area_config(area_id)
        is_village = self.cog.areas.get(area_id, {}).get("is_village", False)
        shop_name = area_cfg.get("name_en" if lang == "en" else "name_zh") or area_id

        self.clear_items()
        prefix = notice + "\n\n" if notice else ""
        lines = [prefix + t(lang, "shop.fixed_store_title", "🛒 【{shop_name}】固定貨架：", shop_name=shop_name)]

        options = []
        for item_id in state.get("items", []):
            item = self.cog.items.get(item_id)
            if not item:
                continue
            lines.append(self._format_shop_item_line(item_id))
            req = item.get("exclusive_level", 0)
            req_label = f" Lv.{req}" if req else ""
            comp_str = ""
            if item.get("type") in ("weapon", "armor", "accessory"):
                comp_str = self._get_equipment_comparison_string(item)
            comp_suffix = f" {comp_str}" if comp_str else ""
            item_name = f"{item_emoji(item)} {tf(item, 'name', lang)}"
            label_text = item_name
            desc_text = (tf(item, "desc", lang) or "")
            if comp_str:
                desc_text = f"[{comp_str}] {desc_text}"
            options.append((label_text[:100], f"buy_{item_id}", desc_text[:100], item_emoji(item)))

        self.log_message = "\n".join(lines)
        if options:
            self.add_action_select(t(lang, "shop.select_buy_placeholder", "🛒 選擇要購買的物品"), options, row=0, custom_id="sel_buy_item")

        self.add_action_button(label=t(lang, "shop.btn_sell_items", "出售物品"), style=discord.ButtonStyle.primary, custom_id="btn_shop_sell", emoji="💰", row=4)
        back_label = t(lang, "menu.btn_back_village", "返回村莊") if is_village else t(lang, "char.btn_back", "返回")
        self.add_action_button(label=back_label, style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙", row=4)

    async def handle_shop_refresh(self):
        await self.handle_shop_menu(t(self.player.language, "shop.fixed_no_refresh", "📌 這間店的貨架是固定的，不需要刷新。"))

    async def handle_mystery_merchant(self, notice=""):
        self.current_menu_state = "mystery_merchant"
        lang = self.player.language
        items = self._roll_mystery_merchant_stock()
        self.clear_items()
        prefix = notice + "\n\n" if notice else ""
        lines = [prefix + t(lang, "shop.mystery_merchant_title", "🎭 【神秘商人】只賣這次遇到的 5 件貨")]
        options = []
        if not items:
            lines.append(t(lang, "shop.mystery_no_stock", "（他翻了翻行囊，今天似乎沒有能賣的東西。）"))
        for item_id in items:
            item = self.cog.items.get(item_id)
            if not item:
                continue
            lines.append(self._format_shop_item_line(item_id))
            comp_str = self._get_equipment_comparison_string(item) if item.get("type") in ("weapon", "armor", "accessory") else ""
            comp_suffix = f" {comp_str}" if comp_str else ""
            item_name = f"{item_emoji(item)} {tf(item, 'name', lang)}"
            label_text = t(
                lang,
                "shop.btn_buy_mystery_item",
                "🎭 {name} ({price}$){comp_suffix}",
                name=item_name,
                price=item.get("price", 0),
                comp_suffix=comp_suffix,
            )
            options.append((label_text[:100], f"buy_{item_id}", (tf(item, "desc", lang) or "")[:100], item_emoji(item)))
        self.log_message = "\n".join(lines)
        if options:
            self.add_action_select(t(lang, "shop.select_buy_placeholder", "🛒 選擇要購買的物品"), options, row=0, custom_id="sel_buy_item")
        self.add_action_button(label=t(lang, "shop.btn_sell_items", "出售物品"), style=discord.ButtonStyle.success, custom_id="btn_shop_sell", emoji="💰")
        self.add_action_button(label=t(lang, "char.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    async def _refresh_buy_menu(self, notice: str):
        if getattr(self, "current_menu_state", None) == "tower_merchant":
            await self.handle_tower_merchant(notice)
        elif getattr(self, "current_menu_state", None) == "mystery_merchant":
            await self.handle_mystery_merchant(notice)
        else:
            await self.handle_shop_menu(notice)

    async def execute_buy(self, item_id: str, amount: int):
        item = self.cog.items.get(item_id)
        lang = self.player.language
        if not item:
            await self._refresh_buy_menu(t(lang, "shop.item_no_longer_available", "❌ 這個商品已經不在貨架上了。"))
            return

        if amount <= 0:
            await self._refresh_buy_menu(t(lang, "shop.invalid_amount", "❌ 購買數量不正確。"))
            return

        item_name = tf(item, "name", lang) or item_id
        total_cost = item.get("price", 0) * amount
        if not self.cog.try_spend(self.user_id, self.player, total_cost):
            await self._refresh_buy_menu(t(lang, "shop.buy_insufficient_gold", "❌ 金幣不足！購買 {amount} 個【{name}】需要 {total_cost}$。", amount=amount, name=item_name, total_cost=total_cost))
            return

        self.player.inventory[item_id] = self.player.inventory.get(item_id, 0) + amount
        self.cog.save_players(player=self.player)
        achv_text = self.check_achievements({"money_spent"})
        notice_text = t(lang, "shop.buy_success", "✅ 購買了 {amount} 個【{name}】！", amount=amount, name=item_name, total_cost=total_cost)
        if achv_text:
            notice_text += achv_text
        await self._refresh_buy_menu(notice_text)
    async def execute_sell(self, item_id: str, amount: int):
        item = self.cog.items.get(item_id)
        lang = self.player.language
        owned = self.player.inventory.get(item_id, 0)
        if not item or owned <= 0:
            await self.handle_sell_menu(t(lang, "shop.item_not_owned", "❌ 你並未持有這個物品。"), paging=True)
            return

        if item_id in (self.player.weapon, getattr(self.player, "armor", None), self.player.accessory):
            await self.handle_sell_menu(t(lang, "shop.sell_equipped_blocked", "❌ 這件裝備正在穿戴中，請先卸下再出售。"), paging=True)
            return

        if amount > owned:
            await self.handle_sell_menu(t(lang, "shop.sell_amount_exceeds_owned", "❌ 數量超過持有量！你只有 {owned} 個【{name}】。", owned=owned, name=tf(item, "name", lang) or item_id), paging=True)
            return

        unit_price = get_sell_price(item_id, self.cog.items)
        total_price = unit_price * amount
        self.player.inventory[item_id] -= amount
        if self.player.inventory[item_id] <= 0:
            del self.player.inventory[item_id]
        self.cog.adjust_bank(self.user_id, total_price)
        self.cog.save_players(player=self.player)
        await self.handle_sell_menu(t(lang, "shop.sell_success", "✅ 賣出了 {amount} 個【{name}】，獲得 {total_price}$！", amount=amount, name=tf(item, "name", lang) or item_id, total_price=total_price), paging=True)

    async def handle_sell_menu(self, notice="", paging=False):
        self.clear_items()
        self.current_menu_state = "sell"
        lang = self.player.language
        equipped = (self.player.weapon, getattr(self.player, "armor", None), self.player.accessory)
        sellable = []
        for item_id, count in self.player.inventory.items():
            if count > 0 and item_id != "jester_mask" and item_id not in equipped:
                item = self.cog.items.get(item_id)
                if item and get_sell_price(item_id, self.cog.items) > 0:
                    sellable.append(item_id)

        page_items, _, max_page = self._paginate(sellable, 25, paging)
        prefix = notice + "\n\n" if notice else ""
        if not sellable:
            self.log_message = prefix + t(lang, "shop.sell_menu_empty", "💰 【出售物品】\n沒有可以賣給商店的東西。")
        else:
            self.log_message = prefix + t(lang, "shop.sell_menu_title_select", "💰 【出售物品】\n從下方選單挑選要賣出的物品：")
            if max_page > 0:
                self.log_message += t(lang, "equip.page_suffix", "（第 {page}/{max_page} 頁）", page=self.inventory_page + 1, max_page=max_page + 1)
            options = []
            for item_id in page_items:
                item = self.cog.items[item_id]
                price = get_sell_price(item_id, self.cog.items)
                count = self.player.inventory[item_id]
                label = t(lang, "shop.opt_sell_item", "{name}（{price}$）x{count}", name=tf(item, "name", lang), price=price, count=count)
                options.append((label, f"sell_{item_id}", (tf(item, "desc", lang) or "")[:100], item_emoji(item)))
            self.add_action_select(t(lang, "shop.select_sell_placeholder", "💰 選擇要賣出的物品"), options, row=0, custom_id="sel_sell_item")

        if len(sellable) > 25:
            self._add_pagination_buttons(len(sellable), 25)
        self.add_action_button(label=t(lang, "shop.btn_back_to_shop", "返回商店"), style=discord.ButtonStyle.secondary, custom_id="btn_shop_menu", emoji="🔙", row=4)

    async def handle_craft_menu(self, notice="", paging=False):
        if not crafting_station_unlocked(self.player):
            self.build_main_menu()
            self.log_message = t(self.player.language, "craft.locked_elune", "🔒 合成台由精靈長老艾露娜保管。完成她的兩項委託後，才能在迷霧森林使用。")
            return
        self.clear_items()
        self.current_menu_state = "craft"
        lang = self.player.language
        prefix = notice + "\n\n" if notice else ""
        recipe_ids = list(CRAFTING_RECIPES.keys())
        page_ids, _, max_page = self._paginate(recipe_ids, 25, paging)

        self.log_message = prefix + t(lang, "craft.title_select", "🔨 【合成台】\n從下方選單挑選配方：")
        if max_page > 0:
            self.log_message += t(lang, "equip.page_suffix", "（第 {page}/{max_page} 頁）", page=self.inventory_page + 1, max_page=max_page + 1)

        user_bal = self.cog.get_bank_balance(self.user_id)
        options = []
        for item_id in page_ids:
            recipe = CRAFTING_RECIPES[item_id]
            materials_desc = []
            can_craft = True
            for mat_id, req_qty in recipe["materials"].items():
                mat_name = tf(self.cog.items.get(mat_id, {}), "name", lang) or mat_id
                current_qty = self.player.inventory.get(mat_id, 0)
                materials_desc.append(f"{mat_name} ({current_qty}/{req_qty})")
                if current_qty < req_qty:
                    can_craft = False
            if user_bal < recipe["gold"]:
                can_craft = False

            recipe_name = tf(recipe, "name", lang)
            self.log_message += "\n" + t(lang, "craft.recipe_line", "• **{name}**", name=recipe_name, gold=recipe["gold"], materials=", ".join(materials_desc))
            mark = "✅" if can_craft else "❌"
            options.append((f"{mark} {recipe_name}（{recipe['gold']}$）", f"craft_{item_id}", ", ".join(materials_desc)[:100], "🔨"))

        self.add_action_select(t(lang, "craft.select_placeholder", "🔨 選擇要製作的配方"), options, row=0, custom_id="sel_craft")
        if len(recipe_ids) > 25:
            self._add_pagination_buttons(len(recipe_ids), 25)
        self.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙", row=4)

    async def handle_craft_execute(self, item_id: str):
        if not crafting_station_unlocked(self.player):
            self.build_main_menu()
            self.log_message = t(self.player.language, "craft.locked_elune", "🔒 合成台由精靈長老艾露娜保管。完成她的兩項委託後，才能在迷霧森林使用。")
            return
        lang = self.player.language
        recipe = CRAFTING_RECIPES.get(item_id)
        if not recipe:
            return

        for mat_id, req_qty in recipe["materials"].items():
            if self.player.inventory.get(mat_id, 0) < req_qty:
                await self.handle_craft_menu(t(lang, "craft.err_no_materials", "❌ 材料不足！"))
                return

        if not self.cog.try_spend(self.user_id, self.player, recipe["gold"]):
            await self.handle_craft_menu(t(lang, "craft.err_no_gold", "❌ 金幣不足！"))
            return

        for mat_id, req_qty in recipe["materials"].items():
            self.player.inventory[mat_id] -= req_qty
            if self.player.inventory[mat_id] <= 0:
                del self.player.inventory[mat_id]

        self.player.inventory[item_id] = self.player.inventory.get(item_id, 0) + 1
        item_data = self.cog.items.get(item_id, {})
        equip_msg = ""
        if item_data.get("type") == "weapon":
            self.player.weapon = item_id
            equip_msg = t(lang, "craft.auto_equip_weapon", "，已為你自動裝備")
        elif item_data.get("type") == "armor":
            self.player.armor = item_id
            equip_msg = t(lang, "craft.auto_equip_armor", "，已為你自動穿戴")

        recalc_player_stats(self.player, self.cog.items, heal_full=False)
        self.cog.save_players(player=self.player)
        notice_text = t(lang, "craft.success", "🎉 製作成功！你獲得了【{name}】{equip_msg}！", name=tf(recipe, "name", lang), equip_msg=equip_msg)
        notice_text += self.check_achievements()
        await self.handle_craft_menu(notice_text)

    async def handle_blacksmith_menu(self, notice=""):
        if not blacksmith_accessible(self.player):
            self.build_main_menu()
            self.log_message = t(self.player.language, "blacksmith.locked_lab", "🔒 鐵匠鋪設在瘋狂博士實驗室，請前往 Lv.20 區域使用。")
            return
        self.clear_items()
        self.current_menu_state = "blacksmith"
        lang = self.player.language
        prefix = notice + "\n\n" if notice else ""
        p = self.player
        self.log_message = prefix + t(lang, "blacksmith.title", "⚒️ 【鐵匠鋪】\n把你的裝備交給熟練的鐵匠吧！")

        w_id = getattr(p, "weapon", None)
        a_id = getattr(p, "armor", None)
        none_label = t(lang, "blacksmith.none", "無")
        w_name = tf(self.cog.items.get(w_id, {}), "name", lang) if w_id else none_label
        a_name = tf(self.cog.items.get(a_id, {}), "name", lang) if a_id else none_label
        w_up = getattr(p, "weapon_upgrades", {}).get(w_id, 0) if w_id else 0
        a_up = getattr(p, "armor_upgrades", {}).get(a_id, 0) if a_id else 0
        self.log_message += "\n" + t(lang, "blacksmith.current_weapon", "⚔️ 目前武器：【{name}】", name=w_name) + (f" (+{w_up})" if w_id and w_up > 0 else "")
        self.log_message += "\n" + t(lang, "blacksmith.current_armor", "🛡️ 目前防具：【{name}】", name=a_name) + (f" (+{a_up})" if a_id and a_up > 0 else "")

        user_bal = self.cog.get_bank_balance(self.user_id)
        can_up_w = False
        can_up_a = False
        w_desc = t(lang, "blacksmith.cannot_upgrade_no_weapon", "無法強化（未裝備武器）")
        a_desc = t(lang, "blacksmith.cannot_upgrade_no_armor", "無法強化（未裝備防具）")

        if w_id:
            if w_up >= MAX_UPGRADE_LEVEL:
                w_desc = t(lang, "blacksmith.max_level_reached", "已達到最高強化等級 (+{max})", max=MAX_UPGRADE_LEVEL)
            else:
                next_lvl = w_up + 1
                cost = UPGRADE_COSTS[next_lvl]
                mat_name = tf(self.cog.items.get(cost["material"], {}), "name", lang) or cost["material"]
                current_qty = p.inventory.get(cost["material"], 0)
                w_desc = t(lang, "blacksmith.upgrade_info", "升級至 +{next_lvl} | 成功率: {rate_label}\n花費: {gold}$ | 材料: {mat_name} ({current_qty}/{mat_qty})", next_lvl=next_lvl, rate_label=cost["label"], gold=cost["gold"], mat_name=mat_name, current_qty=current_qty, mat_qty=cost["mat_qty"])
                can_up_w = user_bal >= cost["gold"] and current_qty >= cost["mat_qty"]

        if a_id:
            if a_up >= MAX_UPGRADE_LEVEL:
                a_desc = t(lang, "blacksmith.max_level_reached", "已達到最高強化等級 (+{max})", max=MAX_UPGRADE_LEVEL)
            else:
                next_lvl = a_up + 1
                cost = UPGRADE_COSTS[next_lvl]
                mat_name = tf(self.cog.items.get(cost["material"], {}), "name", lang) or cost["material"]
                current_qty = p.inventory.get(cost["material"], 0)
                a_desc = t(lang, "blacksmith.upgrade_info", "升級至 +{next_lvl} | 成功率: {rate_label}\n花費: {gold}$ | 材料: {mat_name} ({current_qty}/{mat_qty})", next_lvl=next_lvl, rate_label=cost["label"], gold=cost["gold"], mat_name=mat_name, current_qty=current_qty, mat_qty=cost["mat_qty"])
                can_up_a = user_bal >= cost["gold"] and current_qty >= cost["mat_qty"]

        self.log_message += "\n" + t(lang, "blacksmith.weapon_upgrade_section", "**武器強化：**\n{desc}\n", desc=w_desc)
        self.log_message += "\n" + t(lang, "blacksmith.armor_upgrade_section", "**防具強化：**\n{desc}\n", desc=a_desc)

        # --- Forging Stone section ---
        p_inv = getattr(p, "inventory", {}) or {}
        forging_stones = p_inv.get("forging_stone", 0)
        stone_name = tf(self.cog.items.get("forging_stone", {}), "name", lang) or t(lang, "blacksmith.forging_stone", "極致鍛造石")

        def _stone_cost(current_up: int) -> int:
            """鍛造石消耗量 = 目前強化等級（+0→+1 需 1 顆，+4→+5 需 4 顆），最小為 1。"""
            return max(1, current_up)

        can_up_w_stone = False
        can_up_a_stone = False
        if w_id and w_up < MAX_UPGRADE_LEVEL:
            need_w_stone = _stone_cost(w_up)
            can_up_w_stone = forging_stones >= need_w_stone
        if a_id and a_up < MAX_UPGRADE_LEVEL:
            need_a_stone = _stone_cost(a_up)
            can_up_a_stone = forging_stones >= need_a_stone

        if forging_stones > 0:
            self.log_message += "\n" + t(lang, "blacksmith.forging_stone_hint",
                "🔥 **鍛造石（持有 {have} 顆）**：可無視金幣與材料直接強化，必定成功！",
                have=forging_stones)

        self.add_action_button(label=t(lang, "blacksmith.btn_upgrade_weapon", "強化武器"), style=discord.ButtonStyle.primary if can_up_w else discord.ButtonStyle.secondary, custom_id="btn_upgrade_weapon", disabled=not can_up_w)
        self.add_action_button(label=t(lang, "blacksmith.btn_upgrade_armor", "強化防具"), style=discord.ButtonStyle.primary if can_up_a else discord.ButtonStyle.secondary, custom_id="btn_upgrade_armor", disabled=not can_up_a)
        if can_up_w_stone:
            need_w_stone = _stone_cost(w_up)
            self.add_action_button(label=t(lang, "blacksmith.btn_stone_weapon", "🔥 鍛造石強化武器 (×{n})", n=need_w_stone), style=discord.ButtonStyle.success, custom_id="btn_stone_upgrade_weapon")
        if can_up_a_stone:
            need_a_stone = _stone_cost(a_up)
            self.add_action_button(label=t(lang, "blacksmith.btn_stone_armor", "🔥 鍛造石強化防具 (×{n})", n=need_a_stone), style=discord.ButtonStyle.success, custom_id="btn_stone_upgrade_armor")
        self.add_action_button(label=t(lang, "menu.btn_back", "返回"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    async def handle_stone_upgrade_execute(self, is_weapon: bool):
        """使用鍛造石強化武器或防具（無視材料/金幣，必定成功）。"""
        if not blacksmith_accessible(self.player):
            self.build_main_menu()
            self.log_message = t(self.player.language, "blacksmith.locked_lab", "🔒 鐵匠鋪設在瘋狂博士實驗室，請前往 Lv.20 區域使用。")
            return
        p = self.player
        lang = p.language
        slot = "weapon" if is_weapon else "armor"
        item_id = getattr(p, slot, None)
        if not item_id:
            await self.handle_blacksmith_menu(t(lang, "blacksmith.err_no_equipment", "❌ 你沒有裝備任何對應的裝備！"))
            return

        upgrades_dict = getattr(p, f"{slot}_upgrades", None)
        if not isinstance(upgrades_dict, dict):
            upgrades_dict = {}
            setattr(p, f"{slot}_upgrades", upgrades_dict)
        current_up = upgrades_dict.get(item_id, 0)
        if current_up >= MAX_UPGRADE_LEVEL:
            await self.handle_blacksmith_menu(t(lang, "blacksmith.err_max_level_v2", "❌ 該裝備已達到最高強化等級 (+{max})！", max=MAX_UPGRADE_LEVEL))
            return

        need = max(1, current_up)
        inv = getattr(p, "inventory", {}) or {}
        have = inv.get("forging_stone", 0)
        if have < need:
            stone_name = tf(self.cog.items.get("forging_stone", {}), "name", lang) or t(lang, "blacksmith.forging_stone", "極致鍛造石")
            await self.handle_blacksmith_menu(
                t(lang, "blacksmith.err_no_stones",
                  "❌ 鍛造石不足！需要 {need} 顆，目前持有 {have} 顆。",
                  need=need, have=have)
            )
            return

        # 消耗鍛造石
        inv["forging_stone"] = have - need
        if inv["forging_stone"] == 0:
            del inv["forging_stone"]

        # 必定成功強化
        next_lvl = current_up + 1
        upgrades_dict[item_id] = next_lvl
        recalc_player_stats(p, self.cog.items, heal_full=False)
        item_name = tf(self.cog.items.get(item_id, {}), "name", lang) or item_id
        notice_text = t(lang, "blacksmith.stone_upgrade_success",
                        "🔥 鍛造石強化成功！消耗 {need} 顆鍛造石，你的【{name}】已提升至 +{lvl}！（必定成功）",
                        need=need, name=item_name, lvl=next_lvl)

        self.cog.save_players(player=self.player)
        notice_text += self.check_achievements()
        await self.handle_blacksmith_menu(notice_text)

    async def handle_upgrade_execute(self, is_weapon: bool):
        """Normal blacksmith upgrade using gold + materials (with chance of failure)."""
        if not blacksmith_accessible(self.player):
            self.build_main_menu()
            self.log_message = t(self.player.language, "blacksmith.locked_lab", "🔒 鐵匠鋪設在瘋狂博士實驗室，請前往 Lv.20 區域使用。")
            return
        p = self.player
        lang = p.language
        slot = "weapon" if is_weapon else "armor"
        item_id = getattr(p, slot, None)
        if not item_id:
            await self.handle_blacksmith_menu(t(lang, "blacksmith.err_no_equipment", "❌ 你沒有裝備任何對應的裝備！"))
            return

        upgrades_dict = getattr(p, f"{slot}_upgrades", None)
        if not isinstance(upgrades_dict, dict):
            upgrades_dict = {}
            setattr(p, f"{slot}_upgrades", upgrades_dict)
        current_up = upgrades_dict.get(item_id, 0)
        if current_up >= MAX_UPGRADE_LEVEL:
            await self.handle_blacksmith_menu(t(lang, "blacksmith.err_max_level_v2", "❌ 該裝備已達到最高強化等級 (+{max})！", max=MAX_UPGRADE_LEVEL))
            return

        next_lvl = current_up + 1
        cost = UPGRADE_COSTS[next_lvl]
        if self.player.inventory.get(cost["material"], 0) < cost["mat_qty"]:
            await self.handle_blacksmith_menu(t(lang, "blacksmith.err_no_materials", "❌ 強化材料不足！"))
            return
        if not self.cog.try_spend(self.user_id, p, cost["gold"]):
            await self.handle_blacksmith_menu(t(lang, "blacksmith.err_no_gold", "❌ 金幣不足！"))
            return

        p.inventory[cost["material"]] -= cost["mat_qty"]
        if p.inventory[cost["material"]] <= 0:
            del p.inventory[cost["material"]]

        success = random.random() < cost["rate"]
        if success:
            upgrades_dict[item_id] = next_lvl
            recalc_player_stats(p, self.cog.items, heal_full=False)
            notice_text = t(lang, "blacksmith.upgrade_success", "✨ 強化成功！你的【{name}】已提升至 +{next_lvl}！", name=tf(self.cog.items.get(item_id, {}), "name", lang) or item_id, next_lvl=next_lvl)
        else:
            notice_text = t(lang, "blacksmith.upgrade_fail", "💥 強化失敗！材料被熔毀了，但裝備本體保住了。", current_up=current_up)

        self.cog.save_players(player=self.player)
        notice_text += self.check_achievements()
        await self.handle_blacksmith_menu(notice_text)
