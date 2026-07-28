import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from trpg.cog import TRPGCog
from trpg.combat import TRPGCombat
from trpg import dungeon as dungeon_data
from trpg.balance import DUNGEON_MINIBOSS_FLOORS
from trpg.monster_ai import ai_thief, run_monster_ai, _apply_monster_stat_mod, _maybe_transform_phase2, _tick_stat_mods
from trpg.monster_pool import instantiate_monster
from trpg.player import TRPGPlayer, _item_shop_level_ok
from trpg.quest_popup import can_accept_quest
from trpg.skill_progression import validate_paid_upgrade
from trpg.stats import migrate_player_stats
from trpg.status import process_turn_start
from trpg.view_shop import ShopMixin
from trpg.view import TRPGGameView
from trpg.views.upgrade_menu import handle_upgrade_skill


ROOT = Path(__file__).parents[1]
DATA = ROOT / "trpg_data"


class DummyCog:
    def __init__(self, items=None):
        self.items = items or {}
        self.skills = {}
        self.status_effects = {}
        self.balance = 1000
        self.saved = 0

    def get_bank_balance(self, _user_id):
        return self.balance

    def adjust_bank(self, _user_id, delta):
        self.balance += int(delta)
        return self.balance

    def try_spend(self, user_id, player, amount):
        if self.balance < amount:
            return False
        self.adjust_bank(user_id, -amount)
        player.stats["money_spent"] = player.stats.get("money_spent", 0) + amount
        return True

    def save_players(self, **_kwargs):
        self.saved += 1


class DummyCombat:
    def __init__(self, player, cog, slot):
        self.player = player
        self.cog = cog
        self.is_dodging = False
        self.is_defending = False
        self.view = SimpleNamespace(monster_slots=[slot], user_id="u", process_death=lambda log, _msg: log)

    def apply_player_damage(self, damage):
        self.player.current_hp -= damage
        return damage


class DummyShopView(ShopMixin):
    def __init__(self, player, cog):
        self.player = player
        self.cog = cog
        self.user_id = "u"
        self.current_menu_state = "main"
        self.last_notice = ""

    def build_main_menu(self):
        self.current_menu_state = "main"

    async def handle_shop_menu(self, notice=""):
        self.current_menu_state = "shop"
        self.last_notice = notice

    async def handle_mystery_merchant(self, notice=""):
        self.current_menu_state = "mystery_merchant"
        self.last_notice = notice

    async def handle_tower_merchant(self, notice=""):
        self.current_menu_state = "tower_merchant"
        self.last_notice = notice

    def check_achievements(self, *_fields):
        return ""

    async def handle_craft_menu(self, notice=""):
        self.last_notice = notice


class DummyFollowup:
    def __init__(self):
        self.messages = []

    async def send(self, message, **_kwargs):
        self.messages.append(message)


class DummyUpgradeView:
    def __init__(self, player, cog):
        self.player = player
        self.cog = cog
        self.user_id = "u"
        self.log_message = ""

    def clear_items(self):
        pass

    def add_action_button(self, **_kwargs):
        pass


class TrpgFullAuditRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = json.loads((DATA / "items.json").read_text(encoding="utf-8-sig"))
        cls.skills = json.loads((DATA / "skills.json").read_text(encoding="utf-8-sig"))
        cls.status_effects = json.loads((DATA / "status_effects.json").read_text(encoding="utf-8-sig"))
        cls.quests = json.loads((DATA / "quests.json").read_text(encoding="utf-8-sig"))

    def test_active_skill_intervals_advance_once_per_complete_monster_round(self):
        skill = {
            "id": "clockwork_blast",
            "name": "蓄力砲",
            "name_en": "Charged Cannon",
            "trigger": {"type": "interval", "value": 2},
            "effect": {"type": "heavy_attack", "mult": 2},
        }
        monster = {"name": "高速機兵", "name_en": "Fast Automaton", "max_hp": 100, "atk": 1, "def": 0, "spd": 100, "ai": "none", "active_skills": [skill]}
        slot = {"monster": monster, "hp": 100, "status": {}}
        player = TRPGPlayer("u")
        player.current_hp = player.max_hp = 1000
        combat = DummyCombat(player, DummyCog(), slot)

        run_monster_ai(combat, slot, "", round_start=True)
        self.assertEqual(slot["skill_turn_counters"]["clockwork_blast"], 1)
        run_monster_ai(combat, slot, "", round_start=False)
        self.assertEqual(slot["skill_turn_counters"]["clockwork_blast"], 1)
        self.assertNotIn("telegraph", slot)
        run_monster_ai(combat, slot, "", round_start=True)
        self.assertEqual(slot.get("telegraph"), skill)

    def test_thief_stealing_gold_does_not_count_as_player_spending(self):
        player = TRPGPlayer("u")
        player.current_hp = player.max_hp = 1000
        cog = DummyCog()
        slot = {"monster": {"name": "盜賊", "name_en": "Thief", "max_hp": 100, "atk": 1, "def": 0, "spd": 10, "level": 10}, "hp": 100, "status": {}}
        combat = DummyCombat(player, cog, slot)

        with patch("trpg.monster_ai.random.random", return_value=0.0):
            ai_thief(combat, slot, "")

        self.assertLess(cog.balance, 1000)
        self.assertEqual(player.stats["money_spent"], 0)
        self.assertGreater(slot.get("stolen_gold", 0), 0)

    def test_buy_submission_revalidates_current_menu_and_stock(self):
        player = TRPGPlayer("u")
        item = {"name": "藥水", "name_en": "Potion", "type": "potion", "price": 10}
        cog = DummyCog({"health_potion": item})
        view = DummyShopView(player, cog)

        asyncio.run(view.execute_buy("health_potion", 1))

        self.assertEqual(player.inventory.get("health_potion", 0), 2)
        self.assertEqual(cog.balance, 1000)
        self.assertIn("失效", view.log_message)

    def test_valid_mystery_merchant_purchase_refreshes_the_real_handler(self):
        player = TRPGPlayer("u")
        item = {"name": "藥水", "name_en": "Potion", "type": "potion", "price": 10}
        player.shop_state[player.current_area] = {
            "mystery_active": True,
            "mystery_items": ["health_potion"],
        }
        cog = DummyCog({"health_potion": item})
        view = DummyShopView(player, cog)
        view.current_menu_state = "mystery_merchant"

        asyncio.run(view.execute_buy("health_potion", 1))

        self.assertEqual(player.inventory["health_potion"], 3)
        self.assertEqual(cog.balance, 990)
        self.assertIn("購買", view.last_notice)

    def test_retired_quest_remains_settleable_for_old_saves_but_cannot_be_accepted(self):
        retired = self.quests["quest_010"]
        self.assertTrue(retired.get("retired"))
        self.assertFalse(retired.get("board"))
        self.assertFalse(retired.get("npc_offer"))
        view = SimpleNamespace(player=SimpleNamespace(level=99, active_quests={}, completed_quests=[], repeatable_cooldowns={}))
        self.assertFalse(can_accept_quest(view, "quest_010", retired))

    def test_all_quest_item_rewards_and_skill_scrolls_reference_live_content(self):
        missing = []
        for quest_id, quest in self.quests.items():
            for item_id in (quest.get("reward_items") or {}):
                if item_id not in self.items:
                    missing.append(f"{quest_id} rewards missing item {item_id}")
        for item_id, item in self.items.items():
            if item.get("type") == "skill_scroll" and item.get("teaches") not in self.skills:
                missing.append(f"{item_id} teaches missing skill {item.get('teaches')}")
        self.assertEqual([], missing)

    def test_legacy_battle_focus_scroll_migrates_to_skill_manual(self):
        player = TRPGPlayer("legacy-scroll")
        player.inventory = {"scroll_battle_focus": 2}
        player.shop_state = {"area_00village": {"items": ["scroll_battle_focus"], "mystery_items": ["scroll_battle_focus"]}}

        migrate_player_stats(player, self.items, self.skills)

        self.assertEqual(player.inventory, {"skill_manual": 2})
        self.assertEqual(player.shop_state["area_00village"]["items"], ["skill_manual"])
        self.assertEqual(player.shop_state["area_00village"]["mystery_items"], ["skill_manual"])
        self.assertFalse(_item_shop_level_ok(player, "bad_scroll", {"type": "skill_scroll", "teaches": "missing"}, self.skills))

    def test_paid_skill_upgrade_validates_against_the_real_bank_balance(self):
        player = TRPGPlayer("u")
        player.equipped_skills = ["power_slash"]
        player.skill_usage["power_slash"] = 20
        self.assertFalse(hasattr(player, "money"))

        ok, _message, cost = validate_paid_upgrade(
            player,
            self.skills["power_slash"],
            "power_slash",
            available_money=1000,
        )

        self.assertTrue(ok)
        self.assertEqual(cost, 500)

    def test_paid_skill_upgrade_handler_spends_bank_once(self):
        player = TRPGPlayer("u")
        player.equipped_skills = ["power_slash"]
        player.skill_usage["power_slash"] = 20
        cog = DummyCog(self.items)
        cog.skills = self.skills
        view = DummyUpgradeView(player, cog)
        interaction = SimpleNamespace(followup=DummyFollowup())

        asyncio.run(handle_upgrade_skill(view, interaction, "power_slash"))

        self.assertEqual(cog.balance, 500)
        self.assertEqual(player.skill_levels["power_slash"], 2)
        self.assertEqual(player.skill_usage["power_slash"], 0)
        self.assertEqual(player.stats["money_spent"], 500)
        self.assertFalse(hasattr(player, "money"))
        self.assertEqual(interaction.followup.messages, [])

    def test_all_view_generations_for_one_user_share_a_mutation_lock(self):
        cog = object.__new__(TRPGCog)
        cog._player_mutation_locks = {}
        first = cog.player_mutation_lock("123")
        second = cog.player_mutation_lock(123)
        other = cog.player_mutation_lock("456")

        self.assertIs(first, second)
        self.assertIsNot(first, other)

    def test_finish_battle_state_clears_runtime_and_durable_snapshot(self):
        player = TRPGPlayer("u")
        player.active_battle = {"monster_slots": [{"hp": 0}]}
        view = SimpleNamespace(player=player, in_battle=True, monster_slots=[{"hp": 0}])
        combat = object.__new__(TRPGCombat)
        combat.view = view
        combat.skill_cds = {"fireball": 2}
        combat.player_ap = 1
        combat.player_max_ap = 2
        combat.round_started = True
        combat.round_number = 3
        combat.next_round_ap_penalty = 0
        combat.is_defending = False
        combat.is_dodging = False
        combat.blood_shield = 10
        combat.demon_skill_level = 1
        combat._demon_last_round = 2

        combat._finish_battle_state()

        self.assertFalse(view.in_battle)
        self.assertEqual(view.monster_slots, [])
        self.assertIsNone(player.active_battle)
        self.assertEqual(combat.skill_cds, {})

    def test_lethal_dot_reports_full_preclamp_damage_to_blood_shield(self):
        player = TRPGPlayer("u")
        player.max_hp = 1000
        player.current_hp = 10
        player.status_effects = {"poison": {"turns": 2, "tick": 1}}
        ledger = []

        process_turn_start(player, {"poison": {"dot_ratio": 0.05}}, ledger)

        self.assertEqual(ledger, [50])
        view = SimpleNamespace(player=player)
        combat = object.__new__(TRPGCombat)
        combat.view = view
        combat.blood_shield = 100
        player.current_hp = 10
        combat.apply_player_damage(sum(ledger))
        self.assertLessEqual(player.current_hp, 0)
        self.assertEqual(combat.blood_shield, 75)

    def test_phase_attack_base_survives_temporary_debuff_expiry(self):
        slot = {
            "monster": {
                "name": "蜂后",
                "max_hp": 100,
                "atk": 50,
                "phase2": {"hp_below": 0.5, "atk_mult": 1.4},
            },
            "hp": 50,
        }
        _apply_monster_stat_mod(slot, "atk", 0.8, 2)
        combat = SimpleNamespace(player=SimpleNamespace(language="zh"))

        _maybe_transform_phase2(combat, slot, "")
        self.assertEqual(slot["monster"]["atk"], 56)
        _tick_stat_mods(slot)
        _tick_stat_mods(slot)
        self.assertEqual(slot["monster"]["atk"], 70)

    def test_lethal_phase_one_damage_cannot_skip_boss_phase_two(self):
        player = TRPGPlayer("u")
        slot = {
            "monster": {
                "name": "蜂后",
                "max_hp": 100,
                "atk": 50,
                "phase2": {"hp_below": 0.5, "heal_pct": 0.1, "atk_mult": 1.4},
            },
            "hp": 0,
        }
        view = SimpleNamespace(player=player, monster_slots=[slot])
        combat = object.__new__(TRPGCombat)
        combat.view = view

        log = combat._trigger_pending_boss_phases("")

        self.assertTrue(slot["phase2_triggered"])
        self.assertEqual(slot["hp"], 11)
        self.assertFalse(combat._all_monsters_dead())
        self.assertIn("🌀", log)

    def test_pool_instances_keep_vitality_and_runtime_floor_level(self):
        instance = instantiate_monster(
            {
                "id": "skeleton",
                "name": "骷髏",
                "base_hp": 10,
                "base_atk": 4,
                "base_def": 1,
                "base_spd": 5,
                "vitality_type": "soul",
            },
            floor=17,
        )
        self.assertEqual(instance["vitality_type"], "soul")
        self.assertEqual(instance["level"], 17)

    def test_crafting_unqualified_equipment_keeps_it_in_the_bag(self):
        player = TRPGPlayer("u")
        player.current_area = "area_05forest"
        player.completed_quests = ["npc_elune_1", "npc_elune_2"]
        player.inventory["void_crystal"] = 3
        cog = DummyCog(self.items)
        cog.balance = 100_000
        view = DummyShopView(player, cog)

        asyncio.run(view.handle_craft_execute("abyssal_cloak"))

        self.assertEqual(player.inventory["abyssal_cloak"], 1)
        self.assertIsNone(player.armor)
        self.assertIn("背包", view.last_notice)

    def test_respec_unequips_items_whose_stat_requirements_are_lost(self):
        player = TRPGPlayer("u")
        player.level = 80
        player.stat_alloc["rogue"] = 80
        player.armor = "abyssal_cloak"
        player.archetype_balance_version = 999
        cog = DummyCog(self.items)
        cog.balance = 100_000
        cog.skills = self.skills
        notices = []

        async def show_notice(message):
            notices.append(message)

        view = SimpleNamespace(
            player=player,
            cog=cog,
            user_id="u",
            _has_balance_respec=lambda: False,
            _stat_reset_cost=lambda: 0,
            handle_stat_alloc_menu=show_notice,
        )
        asyncio.run(TRPGGameView.handle_stat_reset(view))

        self.assertIsNone(player.armor)
        self.assertTrue(any("深淵潛航者披風" in notice for notice in notices))

    def test_every_forced_dungeon_miniboss_has_a_reachable_unique_definition(self):
        self.assertEqual(set(DUNGEON_MINIBOSS_FLOORS), set(dungeon_data.DUNGEON_MINIBOSSES))
        for floor in DUNGEON_MINIBOSS_FLOORS:
            self.assertEqual(dungeon_data.forced_boss_kind(floor), "miniboss")

    def test_drop_values_above_one_are_guaranteed_quantities(self):
        player = TRPGPlayer("u")
        view = SimpleNamespace(player=player, cog=SimpleNamespace(items=self.items))
        combat = object.__new__(TRPGCombat)
        combat.view = view
        monster = {"drops": {"demon_core": 3.0, "void_crystal": 5.0}}

        with patch("trpg.combat.random.random", return_value=0.999):
            log = combat._roll_drops_for(monster)

        self.assertEqual(player.inventory["demon_core"], 3)
        self.assertEqual(player.inventory["void_crystal"], 5)
        self.assertIn("x3", log)
        self.assertIn("x5", log)

    def test_empty_revive_window_rejects_offensive_skill_before_costs(self):
        player = TRPGPlayer("u")
        player.equipped_skills = ["dark_orb"]
        player.current_mp = 100
        cog = DummyCog(self.items)
        cog.skills = self.skills
        cog.status_effects = self.status_effects
        slot = {
            "monster": {"id": "reviver", "revive_once": {"turns": 3}},
            "hp": 0,
            "status": {},
        }
        view = SimpleNamespace(player=player, cog=cog, monster_slots=[slot])
        combat = TRPGCombat(view)
        combat.player_ap = 3

        result = combat.use_skill("dark_orb")

        self.assertIn("沒有可以攻擊", result)
        self.assertEqual(player.current_mp, 100)
        self.assertEqual(combat.player_ap, 3)
        self.assertEqual(combat.skill_cds, {})
        self.assertEqual(player.skill_usage.get("dark_orb", 0), 0)

    def _combat_for_control_test(self, player):
        cog = DummyCog(self.items)
        cog.skills = self.skills
        cog.status_effects = self.status_effects
        slot = {
            "monster": {
                "id": "dummy",
                "name": "木樁",
                "max_hp": 100,
                "atk": 0,
                "def": 0,
                "spd": 1,
            },
            "hp": 100,
            "status": {},
        }
        view = SimpleNamespace(player=player, cog=cog, monster_slots=[slot], in_battle=True)
        combat = TRPGCombat(view)
        combat.player_ap = 2
        view.combat = combat
        return combat

    def test_sleep_prevents_potion_consumption(self):
        player = TRPGPlayer("u")
        player.current_hp = 20
        player.inventory["health_potion"] = 1
        player.status_effects = {"sleep": {"turns": 2, "tick": 1}}
        combat = self._combat_for_control_test(player)

        combat.use_potion("health_potion")

        self.assertLessEqual(player.current_hp, 20)
        self.assertEqual(player.inventory["health_potion"], 1)

    def test_freeze_prevents_normal_flee_before_rng_roll(self):
        player = TRPGPlayer("u")
        player.status_effects = {"freeze": {"turns": 2, "tick": 1}}
        combat = self._combat_for_control_test(player)

        with patch("trpg.combat.random.random", return_value=0.0):
            result = combat.attempt_flee()

        self.assertIn("冰凍", result)
        self.assertTrue(combat.view.in_battle)
        self.assertNotEqual(combat.view.monster_slots, [])


if __name__ == "__main__":
    unittest.main()
