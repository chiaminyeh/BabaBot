"""ShopMixin — general store, sell menu, crafting workshop, and blacksmith upgrades.

Mixed into TRPGGameView (see trpg/view.py). Relies on attributes/methods defined on
the main view class and on other mixins (self.cog, self.player, self.clear_items,
self.add_action_button, self._paginate, self._add_pagination_buttons,
self._get_equipment_comparison_string, self.check_achievements, self.handle_tower_merchant,
self.handle_sell_menu, ...) — safe because Python resolves `self.x` against the whole
MRO of the final class, not just the class that physically defines a given method.
"""

import random
from datetime import datetime

import discord

from trpg.i18n import t, tf
from trpg.view_shared import item_emoji
from trpg.balance import AREA_SHOP_TIERS, SHOP_POTION_TIERS
from trpg.player import _item_shop_level_ok
from trpg.combat import get_sell_price
from trpg.stats import recalc_player_stats
from trpg.recipes import CRAFTING_RECIPES, UPGRADE_COSTS, MAX_UPGRADE_LEVEL


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
        return AREA_SHOP_TIERS.get(area_id, {"gear_range": (0, 4), "potion_tier": "basic"})

    def _shop_state(self) -> dict:
        """目前所在區域的商店狀態（每個區域各自獨立進貨/刷新，不再共用一份全域貨架）。"""
        if not isinstance(getattr(self.player, "shop_state", None), dict):
            self.player.shop_state = {}
        return self.player.shop_state.setdefault(self.player.current_area, {})

    def _roll_shop_stock(self):
        area_id = self.player.current_area
        cfg = self._shop_area_config(area_id)
        gear_min, gear_max = cfg.get("gear_range", (0, 4))
        hp_potion, mp_potion = SHOP_POTION_TIERS.get(cfg.get("potion_tier", "basic"), SHOP_POTION_TIERS["basic"])
        state = self._shop_state()
        today_str = datetime.today().strftime("%Y-%m-%d")

        if state.get("last_refresh") != today_str:
            state["last_refresh"] = today_str
            state["refresh_count"] = 0
            state["items"] = []

        if state.get("mystery_date") != today_str:
            state["mystery_date"] = today_str
            # 👇 之前這裡只對武器/防具/飾品套用等級門檻，藥水類神秘商品（萬靈藥、
            # 時光沙漏等）沒有 exclusive_level 又被類型判斷豁免，導致高階神秘消耗品
            # 能出現在新手村這種低階商店。現在所有神秘商品一律套用同一套 gear_range
            # 門檻（那 5 項消耗品已經在 items.json 補上對應的 exclusive_level）。
            mystery_pool = [
                k for k, v in self.cog.items.items()
                if v.get("mystery_only") and v.get("price", 0) > 0
                and gear_min <= v.get("exclusive_level", 0) <= gear_max
            ]
            if mystery_pool and random.random() < 0.20:
                state["mystery_active"] = True
                state["mystery_items"] = random.sample(mystery_pool, min(3, len(mystery_pool)))
            else:
                state["mystery_active"] = False
                state["mystery_items"] = []
        elif state.get("mystery_items"):
            # 👇 就算今天已經抽過了，也要重新檢查存檔裡舊的神秘商品清單還符不符合
            # 現在的等級門檻——不然只要玩家「今天已經逛過一次商店」，就算後續把
            # exclusive_level/gear_range 資料修正了，卡在存檔裡的舊清單在隔天重抽
            # 之前都不會消失，等於同一個漏洞換個形式繼續讓高階神秘商品出現在低階
            # 商店裡（這正是玩家回報「新手村出現神秘商人」的實際成因：商店今天稍早
            # 已經用修正前的邏輯抽過一次，存檔就一直卡著那份不符門檻的清單）。
            valid_items = [
                iid for iid in state["mystery_items"]
                if gear_min <= self.cog.items.get(iid, {}).get("exclusive_level", 0) <= gear_max
            ]
            if len(valid_items) != len(state["mystery_items"]):
                state["mystery_items"] = valid_items
                state["mystery_active"] = bool(valid_items)

        if not state.get("items"):
            general_pool = []
            general_weights = []
            for k, v in self.cog.items.items():
                w = v.get("shop_weight", 0)
                if w <= 0 or k in (hp_potion, mp_potion):
                    continue
                # 裝備才受區域等級帶限制；材料/卷軸/藥劑等維持原本只看玩家等級門檻
                if v.get("type") in ("weapon", "armor", "accessory"):
                    if not (gear_min <= v.get("exclusive_level", 0) <= gear_max):
                        continue
                if _item_shop_level_ok(self.player, k, v, self.cog.skills):
                    general_pool.append(k)
                    general_weights.append(w)

            picks = []
            if general_pool:
                for _ in range(5):
                    if not general_pool:
                        break
                    choice = random.choices(general_pool, weights=general_weights, k=1)[0]
                    picks.append(choice)
                    idx = general_pool.index(choice)
                    general_pool.pop(idx)
                    general_weights.pop(idx)

            state["items"] = [hp_potion, mp_potion] + picks
            self.cog.save_players()

    async def handle_shop_menu(self, notice=""):
        self._roll_shop_stock()
        self.current_menu_state = "shop"
        lang = self.player.language
        area_id = self.player.current_area
        state = self._shop_state()
        is_village = self.cog.areas.get(area_id, {}).get("is_village", False)
        area_name = tf(self.cog.areas.get(area_id, {}), "area_name", lang) or area_id

        self.clear_items()
        lines = []
        prefix = (notice + "\n\n" if notice else "")
        title_key = "shop.village_store_title" if is_village else "shop.area_store_title"
        title_fallback = "🛒 【村莊雜貨鋪】今日限定貨架：" if is_village else "🛒 【{area}商店】今日限定貨架："
        self.log_message = prefix + t(lang, title_key, title_fallback, area=area_name) + "\n"

        for item_id in state.get("items", []):
            item = self.cog.items.get(item_id)
            if item:
                lines.append(self._format_shop_item_line(item_id))
                req = item.get("exclusive_level", 0)
                req_label = f" Lv.{req}" if req else ""

                comp_str = ""
                if item.get("type") in ("weapon", "armor", "accessory"):
                    comp_str = self._get_equipment_comparison_string(item)
                comp_suffix = f" {comp_str}" if comp_str else ""

                item_name = f"{item_emoji(item)} {tf(item, 'name', lang)}"
                label_text = t(lang, "shop.btn_buy_item", "買 {name}{req_label} ({price}$){comp_suffix}", name=item_name, req_label=req_label, price=item['price'], comp_suffix=comp_suffix)
                self.add_action_button(
                    label=label_text[:80],
                    style=discord.ButtonStyle.primary,
                    custom_id=f"buy_{item_id}",
                )

        if state.get("mystery_active") and state.get("mystery_items"):
            self.log_message += "\n\n" + t(lang, "shop.mystery_merchant_title", "🎭 【神秘商人 · 今日限定】") + "\n"
            for item_id in state["mystery_items"]:
                item = self.cog.items.get(item_id)
                if not item:
                    continue
                lines.append(self._format_shop_item_line(item_id))

                comp_str = ""
                if item.get("type") in ("weapon", "armor", "accessory"):
                    comp_str = self._get_equipment_comparison_string(item)
                comp_suffix = f" {comp_str}" if comp_str else ""

                item_name = f"{item_emoji(item)} {tf(item, 'name', lang)}"
                label_text = t(lang, "shop.btn_buy_mystery_item", "🎭 {name} ({price}$){comp_suffix}", name=item_name, price=item['price'], comp_suffix=comp_suffix)
                self.add_action_button(
                    label=label_text[:80],
                    style=discord.ButtonStyle.success,
                    custom_id=f"buy_{item_id}",
                )

        self.log_message += "\n".join(lines)

        refresh_cost = 100 * (2 ** state.get("refresh_count", 0))

        self.add_action_button(label=t(lang, "shop.btn_refresh_shop", "刷新商店 ({cost}$)", cost=refresh_cost), style=discord.ButtonStyle.danger, custom_id="btn_shop_refresh", emoji="🔄")
        self.add_action_button(label=t(lang, "shop.btn_sell_items", "出售物品"), style=discord.ButtonStyle.success, custom_id="btn_shop_sell", emoji="💰")
        back_label = t(lang, "menu.btn_back_village", "返回村莊") if is_village else t(lang, "char.btn_back", "返回")
        self.add_action_button(label=back_label, style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    async def handle_shop_refresh(self):
        state = self._shop_state()
        count = state.get("refresh_count", 0)
        cost = 100 * (2 ** count)

        if not self.cog.try_spend(self.user_id, self.player, cost):
            await self.handle_shop_menu(t(self.player.language, "shop.refresh_insufficient_gold", "❌ 金幣不足！手動進貨需要支付 {cost}$ 給老闆。", cost=cost))
            return

        state["refresh_count"] = count + 1
        state["items"] = []

        achv_text = self.check_achievements()
        notice_text = t(self.player.language, "shop.refresh_success", "🔄 支付了 {cost}$ 刷新商店！老闆為你進了一批新貨。", cost=cost)
        if achv_text:
            notice_text += achv_text

        self.cog.save_players()
        await self.handle_shop_menu(notice_text)

    async def _refresh_buy_menu(self, notice: str):
        if getattr(self, "current_menu_state", None) == "tower_merchant":
            await self.handle_tower_merchant(notice)
        else:
            await self.handle_shop_menu(notice)

    async def execute_buy(self, item_id: str, amount: int):
        item = self.cog.items.get(item_id)
        lang = self.player.language
        if not item:
            await self._refresh_buy_menu(t(lang, "shop.item_no_longer_available", "❌ 這個商品已經不在貨架上了。"))
            return

        item_name = tf(item, "name", lang)
        total_cost = item.get("price", 0) * amount
        user_bal = self.cog.get_bank_balance(self.user_id)
        if not self.cog.try_spend(self.user_id, self.player, total_cost):
            await self._refresh_buy_menu(t(lang, "shop.buy_insufficient_gold", "❌ 金幣不足！購買 {amount} 個【{name}】需要 {total_cost}$，但你只有 {user_bal}$。", amount=amount, name=item_name, total_cost=total_cost, user_bal=user_bal))
            return

        self.player.inventory[item_id] = self.player.inventory.get(item_id, 0) + amount

        achv_text = self.check_achievements()
        notice_text = t(lang, "shop.buy_success", "✅ 購買了 {amount} 個【{name}】，花費 {total_cost}$！", amount=amount, name=item_name, total_cost=total_cost)
        if achv_text:
            notice_text += achv_text

        self.cog.save_players()
        await self._refresh_buy_menu(notice_text)

    async def execute_sell(self, item_id: str, amount: int):
        item = self.cog.items.get(item_id)
        lang = self.player.language
        owned = self.player.inventory.get(item_id, 0)
        if not item or owned <= 0:
            await self.handle_sell_menu(t(lang, "shop.item_not_owned", "❌ 你並未持有這個物品。"), paging=True)
            return
        # 裝備中的武器/防具/飾品不能賣掉——賣掉之後 inventory 沒有這個 item_id 了，但
        # player.weapon/armor/accessory 還指著它，get_equipment_bonuses 照樣讀得到，等於
        # 賣一次留一份數值加成（還連強化等級一起保留，因為 weapon_upgrades 是照 item_id 存的）。
        if item_id in (self.player.weapon, getattr(self.player, "armor", None), self.player.accessory):
            await self.handle_sell_menu(t(lang, "shop.sell_equipped_blocked", "❌ 這件裝備正穿在身上，請先卸下才能出售。"), paging=True)
            return

        item_name = tf(item, "name", lang)
        if amount > owned:
            await self.handle_sell_menu(t(lang, "shop.sell_amount_exceeds_owned", "❌ 數量超過持有量！你只有 {owned} 個【{name}】。", owned=owned, name=item_name), paging=True)
            return

        unit_price = get_sell_price(item_id, self.cog.items)
        total_price = unit_price * amount

        self.player.inventory[item_id] -= amount
        if self.player.inventory[item_id] <= 0:
            del self.player.inventory[item_id]
        self.cog.adjust_bank(self.user_id, total_price)

        self.cog.save_players()
        await self.handle_sell_menu(t(lang, "shop.sell_success", "✅ 賣出了 {amount} 個【{name}】，獲得 {total_price}$！", amount=amount, name=item_name, total_price=total_price), paging=True)

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

        total_items = len(sellable)
        page_items, _, max_page = self._paginate(sellable, 25, paging)

        prefix = notice + "\n\n" if notice else ""
        if not sellable:
            self.log_message = prefix + t(lang, "shop.sell_menu_empty", "💰 【出售物品】\n沒有可以賣給商店的東西。")
        else:
            self.log_message = prefix + t(lang, "shop.sell_menu_title_select", "💰 【出售物品】\n從下方選單挑選要賣出的物品（選中後輸入數量）：")
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

        if total_items > 25:
            self._add_pagination_buttons(total_items, 25)
        self.add_action_button(label=t(lang, "shop.btn_back_to_shop", "返回商店"), style=discord.ButtonStyle.secondary, custom_id="btn_shop_menu", emoji="🔙", row=4)

    async def handle_craft_menu(self, notice="", paging=False):
        self.clear_items()
        self.current_menu_state = "craft"
        lang = self.player.language
        prefix = notice + "\n\n" if notice else ""

        recipe_ids = list(CRAFTING_RECIPES.keys())
        page_ids, _, max_page = self._paginate(recipe_ids, 25, paging)

        self.log_message = prefix + t(
            lang, "craft.title_select", "🔨 【手藝工坊】\n利用冒險收集的材料合成強力的裝備吧！從下方選單挑選配方：\n",
        )
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

            item_data = self.cog.items.get(item_id, {})
            comp_str = self._get_equipment_comparison_string(item_data)
            comp_suffix = f" {comp_str}" if comp_str else ""

            recipe_name = tf(recipe, "name", lang)
            desc_line = t(
                lang,
                "craft.recipe_line",
                "• **{name}**{comp_suffix} | {gold}$ | 材料: {materials}",
                name=recipe_name,
                comp_suffix=comp_suffix,
                gold=recipe["gold"],
                materials=", ".join(materials_desc),
            )
            self.log_message += f"\n{desc_line}"

            mark = "✅" if can_craft else "❌"
            label = f"{mark} {recipe_name}（{recipe['gold']}$）"
            opt_desc = ", ".join(materials_desc)[:100]
            options.append((label, f"craft_{item_id}", opt_desc, "🔨"))

        self.add_action_select(t(lang, "craft.select_placeholder", "🔨 選擇要製作的配方（✅=材料齊全）"), options, row=0, custom_id="sel_craft")

        if len(recipe_ids) > 25:
            self._add_pagination_buttons(len(recipe_ids), 25)
        self.add_action_button(label=t(lang, "menu.btn_back_village", "返回村莊"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙", row=4)

    async def handle_craft_execute(self, item_id: str):
        lang = self.player.language
        recipe = CRAFTING_RECIPES.get(item_id)
        if not recipe: return

        for mat_id, req_qty in recipe["materials"].items():
            current_qty = self.player.inventory.get(mat_id, 0)
            if current_qty < req_qty:
                await self.handle_craft_menu(t(lang, "craft.err_no_materials", "❌ 材料不足！"))
                return

        if not self.cog.try_spend(self.user_id, self.player, recipe["gold"]):
            await self.handle_craft_menu(t(lang, "craft.err_no_gold", "❌ 金幣不足！"))
            return

        # 扣除材料
        for mat_id, req_qty in recipe["materials"].items():
            self.player.inventory[mat_id] -= req_qty
            if self.player.inventory[mat_id] <= 0:
                del self.player.inventory[mat_id]

        # 給予物品
        self.player.inventory[item_id] = self.player.inventory.get(item_id, 0) + 1

        # 裝備自動穿戴邏輯
        item_data = self.cog.items.get(item_id, {})
        equip_msg = ""
        if item_data.get("type") == "weapon":
            self.player.weapon = item_id
            equip_msg = t(lang, "craft.auto_equip_weapon", "，已為你自動裝備")
        elif item_data.get("type") == "armor":
            self.player.armor = item_id
            equip_msg = t(lang, "craft.auto_equip_armor", "，已為你自動穿戴")

        recalc_player_stats(self.player, self.cog.items, heal_full=False)

        achv_text = self.check_achievements()
        notice_text = t(
            lang,
            "craft.success",
            "🎉 製作成功！你獲得了【{name}】{equip_msg}！",
            name=tf(recipe, "name", lang),
            equip_msg=equip_msg,
        )
        if achv_text:
            notice_text += achv_text

        self.cog.save_players()
        await self.handle_craft_menu(notice_text)

    async def handle_blacksmith_menu(self, notice=""):
        self.clear_items()
        lang = self.player.language
        prefix = notice + "\n\n" if notice else ""

        p = self.player
        self.log_message = prefix + t(lang, "blacksmith.title", "⚒️ 【鐵匠鋪】\n把你的裝備交給熟練的鐵匠吧！花費金幣與怪物的材料，可以強化武器與防具。\n")

        # 取得目前裝備資訊
        w_id = getattr(p, "weapon", None)
        a_id = getattr(p, "armor", None)

        none_label = t(lang, "blacksmith.none", "無")
        w_name = tf(self.cog.items.get(w_id, {}), "name", lang) if w_id else none_label
        a_name = tf(self.cog.items.get(a_id, {}), "name", lang) if a_id else none_label

        w_up = getattr(p, "weapon_upgrades", {}).get(w_id, 0) if w_id else 0
        a_up = getattr(p, "armor_upgrades", {}).get(a_id, 0) if a_id else 0

        self.log_message += "\n" + t(lang, "blacksmith.current_weapon", "⚔️ 目前武器：【{name}】", name=w_name) + (f" (+{w_up})" if w_id and w_up > 0 else "")
        self.log_message += "\n" + t(lang, "blacksmith.current_armor", "🛡️ 目前防具：【{name}】", name=a_name) + (f" (+{a_up})" if a_id and a_up > 0 else "")
        self.log_message += "\n"

        user_bal = self.cog.get_bank_balance(self.user_id)

        # 武器強化資訊
        can_up_w = False
        w_desc = t(lang, "blacksmith.cannot_upgrade_no_weapon", "無法強化（未裝備武器）")
        if w_id:
            if w_up >= MAX_UPGRADE_LEVEL:
                w_desc = t(lang, "blacksmith.max_level_reached", "已達到最高強化等級 (+{max})", max=MAX_UPGRADE_LEVEL)
            else:
                next_lvl = w_up + 1
                cost = UPGRADE_COSTS[next_lvl]
                mat_name = tf(self.cog.items.get(cost["material"], {}), "name", lang) or cost["material"]
                current_qty = p.inventory.get(cost["material"], 0)

                w_desc = t(
                    lang,
                    "blacksmith.upgrade_info",
                    "升級至 +{next_lvl} | 成功率: {rate_label}\n花費: {gold}$ | 材料: {mat_name} ({current_qty}/{mat_qty})",
                    next_lvl=next_lvl,
                    rate_label=cost["label"],
                    gold=cost["gold"],
                    mat_name=mat_name,
                    current_qty=current_qty,
                    mat_qty=cost["mat_qty"],
                )

                if user_bal >= cost["gold"] and current_qty >= cost["mat_qty"]:
                    can_up_w = True

        self.log_message += "\n" + t(lang, "blacksmith.weapon_upgrade_section", "**武器強化：**\n{desc}\n", desc=w_desc)

        # 防具強化資訊
        can_up_a = False
        a_desc = t(lang, "blacksmith.cannot_upgrade_no_armor", "無法強化（未裝備防具）")
        if a_id:
            if a_up >= MAX_UPGRADE_LEVEL:
                a_desc = t(lang, "blacksmith.max_level_reached", "已達到最高強化等級 (+{max})", max=MAX_UPGRADE_LEVEL)
            else:
                next_lvl = a_up + 1
                cost = UPGRADE_COSTS[next_lvl]
                mat_name = tf(self.cog.items.get(cost["material"], {}), "name", lang) or cost["material"]
                current_qty = p.inventory.get(cost["material"], 0)

                a_desc = t(
                    lang,
                    "blacksmith.upgrade_info",
                    "升級至 +{next_lvl} | 成功率: {rate_label}\n花費: {gold}$ | 材料: {mat_name} ({current_qty}/{mat_qty})",
                    next_lvl=next_lvl,
                    rate_label=cost["label"],
                    gold=cost["gold"],
                    mat_name=mat_name,
                    current_qty=current_qty,
                    mat_qty=cost["mat_qty"],
                )

                if user_bal >= cost["gold"] and current_qty >= cost["mat_qty"]:
                    can_up_a = True

        self.log_message += "\n" + t(lang, "blacksmith.armor_upgrade_section", "**防具強化：**\n{desc}\n", desc=a_desc)

        # 按鈕：不能強化時要真的用 disabled=True 讓按鈕變灰不可點，之前只是換一個沒有
        # 註冊路由的 custom_id（按下去仍會被 global_callback 接住、空跑一次存檔/重繪），
        # 玩家看起來像能點，點了卻什麼事也沒發生。
        w_style = discord.ButtonStyle.primary if can_up_w else discord.ButtonStyle.secondary
        w_label = t(lang, "blacksmith.btn_upgrade_weapon", "強化武器")
        if w_id and w_up < MAX_UPGRADE_LEVEL:
            w_label += " [⚔️ATK+3▲]"
        self.add_action_button(
            label=w_label,
            style=w_style,
            custom_id="btn_upgrade_weapon",
            disabled=not can_up_w,
        )

        a_style = discord.ButtonStyle.primary if can_up_a else discord.ButtonStyle.secondary
        a_label = t(lang, "blacksmith.btn_upgrade_armor", "強化防具")
        if a_id and a_up < MAX_UPGRADE_LEVEL:
            a_label += " [🛡️DEF+2▲ ❤️HP+15▲]"
        self.add_action_button(
            label=a_label,
            style=a_style,
            custom_id="btn_upgrade_armor",
            disabled=not can_up_a,
        )

        self.add_action_button(label=t(lang, "menu.btn_back_village", "返回村莊"), style=discord.ButtonStyle.secondary, custom_id="btn_back_main", emoji="🔙")

    async def handle_upgrade_execute(self, is_weapon: bool):
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

        current_qty = p.inventory.get(cost["material"], 0)
        if current_qty < cost["mat_qty"]:
            await self.handle_blacksmith_menu(t(lang, "blacksmith.err_no_materials", "❌ 強化材料不足！"))
            return

        if not self.cog.try_spend(self.user_id, p, cost["gold"]):
            await self.handle_blacksmith_menu(t(lang, "blacksmith.err_no_gold", "❌ 金幣不足！"))
            return

        # 扣除材料
        p.inventory[cost["material"]] -= cost["mat_qty"]
        if p.inventory[cost["material"]] <= 0:
            del p.inventory[cost["material"]]

        # 強化判定
        success = (random.random() < cost["rate"])

        if success:
            upgrades_dict[item_id] = next_lvl
            recalc_player_stats(p, self.cog.items, heal_full=False)

            achv_text = self.check_achievements()
            item_name = tf(self.cog.items.get(item_id, {}), "name", lang) or item_id
            notice_text = t(
                lang,
                "blacksmith.upgrade_success",
                "✨ 🌟 強化成功！\n你的【{name}】成功強化至 **+{next_lvl}**！",
                name=item_name,
                next_lvl=next_lvl,
            )
            if achv_text:
                notice_text += achv_text

            self.cog.save_players()
            await self.handle_blacksmith_menu(notice_text)
        else:
            achv_text = self.check_achievements()
            notice_text = t(
                lang,
                "blacksmith.upgrade_fail",
                "💥 強化失敗！\n材料被熔毀了，但是鐵匠拼命保住了你的裝備，等級維持在 **+{current_up}**。",
                current_up=current_up,
            )
            if achv_text:
                notice_text += achv_text

            self.cog.save_players()
            await self.handle_blacksmith_menu(notice_text)
