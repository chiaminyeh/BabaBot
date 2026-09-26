import json
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from trpg.combat import TRPGCombat
from trpg.life_skills import (
    CROPS,
    DAILY_ENERGY,
    consume_life_food,
    ensure_life_state,
    farm_needs_water,
    farm_ready,
    gather,
    harvest_farm,
    life_level,
    plant_crop,
    plant_wheat,
    spend_energy,
    water_farm,
)
from trpg.player import CONTENT_UPDATE_VERSION, TRPGPlayer
from trpg.recipes import UPGRADE_COSTS, upgrade_materials
from trpg.view_shop import ShopMixin
from trpg.view_tutorial import TutorialMixin
from trpg.weekly_events import (
    current_weekly_event,
    ensure_weekly_progress,
    featured_spawn_weights,
    record_weekly_kills,
    reward_multipliers,
)
from trpg.views.main_menu import build_adventure_compass


ROOT = Path(__file__).resolve().parents[1]


class FixedRng:
    def __init__(self, random_value=0.0, randint_value=1):
        self.random_value = random_value
        self.randint_value = randint_value

    def random(self):
        return self.random_value

    def randint(self, _lo, _hi):
        return self.randint_value


class LifeSkillLoopTests(unittest.TestCase):
    def test_daily_energy_resets_once_per_utc_day(self):
        player = TRPGPlayer("life")
        state = ensure_life_state(player, date(2026, 8, 13))
        self.assertEqual(set(state["energy"].values()), {DAILY_ENERGY})
        for skill in ("woodcutting", "fishing", "mining", "farming"):
            for _ in range(DAILY_ENERGY):
                self.assertTrue(spend_energy(state, skill))
            self.assertFalse(spend_energy(state, skill))
        self.assertEqual(set(ensure_life_state(player, date(2026, 8, 13))["energy"].values()), {0})
        self.assertEqual(set(ensure_life_state(player, date(2026, 8, 14))["energy"].values()), {DAILY_ENERGY})

    def test_farm_requires_one_watering_per_day_and_missed_days_only_pause_growth(self):
        player = TRPGPlayer("farmer")
        state = ensure_life_state(player, date(2026, 8, 13))
        self.assertTrue(plant_wheat(state, date(2026, 8, 13)))
        self.assertFalse(plant_crop(state, "hearty_carrot", date(2026, 8, 13)))
        self.assertTrue(farm_needs_water(state, date(2026, 8, 13)))
        self.assertFalse(farm_ready(state, date(2026, 8, 13)))

        first_water = water_farm(state, date(2026, 8, 13))
        self.assertEqual(first_water["waterings"], 1)
        self.assertIsNone(water_farm(state, date(2026, 8, 13)))
        self.assertFalse(farm_needs_water(state, date(2026, 8, 13)))

        # Missing many days does not kill or auto-grow the crop; the next watering resumes it.
        self.assertFalse(farm_ready(state, date(2026, 8, 30)))
        second_water = water_farm(state, date(2026, 8, 30))
        self.assertTrue(second_water["ready"])
        self.assertTrue(farm_ready(state, date(2026, 8, 30)))

        result = harvest_farm(state, date(2026, 8, 30), FixedRng())
        self.assertGreaterEqual(result["drops"]["wheat_bundle"], 8)
        self.assertEqual(result["drops"]["golden_wheat"], 1)
        self.assertIsNone(state["farm"]["crop"])
        self.assertEqual(state["farming_xp"], 40)

    def test_gathering_levels_up_and_rare_chance_is_bounded(self):
        player = TRPGPlayer("gatherer")
        state = ensure_life_state(player, date(2026, 8, 13))
        for _ in range(20):
            result = gather(state, "woodcutting", FixedRng())
        self.assertEqual(life_level(state, "woodcutting"), 2)
        self.assertEqual(result["new_level"], 2)
        level_two_result = gather(state, "woodcutting", FixedRng())
        self.assertEqual(level_two_result["drops"]["rare_lumber"], 1)
        self.assertEqual(level_two_result["drops"]["mystic_sap"], 1)

    def test_wood_and_mining_unlock_long_term_material_tiers(self):
        state = ensure_life_state(TRPGPlayer("miner"), date(2026, 8, 13))
        state["woodcutting_xp"] = 600
        wood = gather(state, "woodcutting", FixedRng(random_value=0.01))
        self.assertIn("legendary_lumber", wood["drops"])

        state["mining_xp"] = 400
        ore = gather(state, "mining", FixedRng(random_value=0.0))
        self.assertGreaterEqual(ore["drops"]["stone"], 2)
        self.assertEqual(ore["drops"]["diamond"], 1)

    def test_higher_level_crops_can_be_eaten_or_sold(self):
        items = json.loads((ROOT / "trpg_data" / "items.json").read_text(encoding="utf-8"))
        player = TRPGPlayer("grower")
        state = ensure_life_state(player, date(2026, 8, 13))
        state["farming_xp"] = 200
        self.assertTrue(plant_crop(state, "hearty_carrot", date(2026, 8, 13)))
        for day in (13, 14, 15):
            self.assertIsNotNone(water_farm(state, date(2026, 8, day)))
        result = harvest_farm(state, date(2026, 8, 15), FixedRng())
        self.assertIn("hearty_carrot", result["drops"])
        player.inventory.update(result["drops"])
        state["bonus_energy"] = 0
        restored = consume_life_food(
            state,
            player.inventory,
            "hearty_carrot",
            items["hearty_carrot"]["life_energy_restore"],
        )
        self.assertEqual(restored, 1)
        self.assertEqual(state["bonus_energy"], 1)
        for crop in CROPS.values():
            self.assertGreater(items[crop["item"]]["sell_price"], 0)

    def test_old_save_receives_safe_life_defaults(self):
        player = TRPGPlayer.from_dict({"id": "legacy", "level": 9})
        state = ensure_life_state(player)
        self.assertEqual(set(state["energy"].values()), {DAILY_ENERGY})
        self.assertEqual(state["mining_xp"], 0)
        self.assertIsNone(player.life_skills["farm"]["crop"])
        self.assertEqual(player.life_skills["farm"]["waterings"], 0)
        self.assertEqual(player.life_skills["farm"]["watered_on"], "")
        self.assertEqual(player.content_update_version, 0)
        self.assertEqual(TRPGPlayer("new").content_update_version, CONTENT_UPDATE_VERSION)

    def test_legacy_scalar_energy_migrates_to_four_fresh_twenty_action_pools(self):
        player = TRPGPlayer("legacy_energy")
        player.life_skills = {
            "daily_date": "2026-08-13",
            "energy": 2,
            "woodcutting_xp": 10,
            "fishing_xp": 0,
            "farming_xp": 0,
            "farm": {"crop": None, "planted_on": ""},
        }
        state = ensure_life_state(player, date(2026, 8, 13))
        self.assertEqual(set(state["energy"].values()), {20})
        self.assertEqual(state["bonus_energy"], 0)
        self.assertEqual(state["farm"]["waterings"], 0)
        self.assertEqual(state["farm"]["watered_on"], "")

    def test_crop_food_cannot_create_an_infinite_farming_loop(self):
        state = ensure_life_state(TRPGPlayer("bounded_farmer"), date(2026, 8, 13))
        state["energy"]["farming"] = 0
        state["energy"]["mining"] = 0
        state["bonus_energy"] = 3
        self.assertFalse(spend_energy(state, "farming"))
        self.assertEqual(state["bonus_energy"], 3)
        self.assertTrue(spend_energy(state, "mining"))
        self.assertEqual(state["bonus_energy"], 2)


class ForgeGatheredMaterialTests(unittest.IsolatedAsyncioTestCase):
    class FakeForge(ShopMixin):
        def __init__(self, inventory):
            self.player = TRPGPlayer("smith")
            self.player.current_area = "area_20lab"
            self.player.weapon = "wooden_sword"
            self.player.inventory = dict(inventory)
            self.user_id = "smith"
            self.notice = ""
            self.spent = 0
            items = json.loads((ROOT / "trpg_data" / "items.json").read_text(encoding="utf-8"))

            def try_spend(_uid, _player, amount):
                self.spent += amount
                return True

            self.cog = SimpleNamespace(items=items, try_spend=try_spend, save_players=lambda **_kwargs: None)

        async def handle_blacksmith_menu(self, notice=""):
            self.notice = notice

        def check_achievements(self, *_args, **_kwargs):
            return ""

    def test_all_upgrade_levels_use_gathered_lumber_and_minerals(self):
        allowed = {"fresh_lumber", "rare_lumber", "premium_lumber", "legendary_lumber", "stone", "gold_ore", "diamond"}
        for level, cost in UPGRADE_COSTS.items():
            materials = upgrade_materials(cost)
            self.assertTrue(materials, level)
            self.assertTrue(set(materials).issubset(allowed), (level, materials))

    async def test_upgrade_requires_and_consumes_every_material_atomically(self):
        cost = UPGRADE_COSTS[1]
        materials = upgrade_materials(cost)
        missing = self.FakeForge({"fresh_lumber": materials["fresh_lumber"]})
        await missing.handle_upgrade_execute(is_weapon=True)
        self.assertEqual(missing.spent, 0)
        self.assertEqual(missing.player.inventory["fresh_lumber"], materials["fresh_lumber"])

        forge = self.FakeForge(materials)
        await forge.handle_upgrade_execute(is_weapon=True)
        self.assertEqual(forge.spent, cost["gold"])
        self.assertEqual(forge.player.weapon_upgrades["wooden_sword"], 1)
        for material in materials:
            self.assertNotIn(material, forge.player.inventory)


class WeeklyEventLoopTests(unittest.TestCase):
    def setUp(self):
        self.events = json.loads((ROOT / "trpg_data" / "weekly_events.json").read_text(encoding="utf-8"))

    def test_eight_week_event_season_has_valid_featured_monsters_and_rewards(self):
        monsters = json.loads((ROOT / "trpg_data" / "monsters.json").read_text(encoding="utf-8"))
        items = json.loads((ROOT / "trpg_data" / "items.json").read_text(encoding="utf-8"))
        self.assertEqual(len(self.events), 8)
        self.assertEqual(len({event["id"] for event in self.events}), 8)
        for event in self.events:
            for monster_id in event.get("featured_monsters", []):
                self.assertIn(monster_id, monsters, (event["id"], monster_id))
            for item_id in event.get("reward_items", {}):
                self.assertIn(item_id, items, (event["id"], item_id))

    def test_rotation_is_stable_within_week_and_changes_next_week(self):
        thursday = current_weekly_event(self.events, date(2026, 8, 13))
        sunday = current_weekly_event(self.events, date(2026, 8, 16))
        monday = current_weekly_event(self.events, date(2026, 8, 17))
        self.assertEqual(thursday["id"], sunday["id"])
        self.assertNotEqual(thursday["id"], monday["id"])

    def test_progress_resets_and_completion_cannot_repeat(self):
        player = TRPGPlayer("weekly")
        event = dict(self.events[0], goal_kills=5)
        progress, completed = record_weekly_kills(player, event, 3, date(2026, 8, 13))
        self.assertEqual(progress["kills"], 3)
        self.assertFalse(completed)
        progress, completed = record_weekly_kills(player, event, 2, date(2026, 8, 13))
        self.assertTrue(completed)
        progress["rewarded"] = True
        _, completed = record_weekly_kills(player, event, 1, date(2026, 8, 13))
        self.assertFalse(completed)
        reset = ensure_weekly_progress(player, event, date(2026, 8, 17))
        self.assertEqual(reset["kills"], 0)
        self.assertFalse(reset["rewarded"])

    def test_featured_monsters_receive_spawn_and_reward_bonuses(self):
        event = self.events[0]
        weights = featured_spawn_weights(["slime", "goblin"], [1.0, 1.0], event)
        self.assertGreater(weights[0], weights[1])
        gold_mult, exp_mult = reward_multipliers(event, "slime")
        self.assertGreater(gold_mult, 1.0)
        self.assertGreater(exp_mult, 1.0)
        self.assertEqual(reward_multipliers(event, "goblin"), (1.0, 1.0))

    def test_combat_grants_weekly_reward_only_once(self):
        event = dict(self.events[0], goal_kills=1, reward_gold=60, reward_items={"health_potion": 1})
        player = TRPGPlayer("reward")
        bank_awards = []
        cog = SimpleNamespace(
            weekly_events=[event],
            items={"health_potion": {"name": "生命藥水", "name_en": "Health Potion"}},
            adjust_bank=lambda _uid, amount: bank_awards.append(amount),
        )
        combat = TRPGCombat(SimpleNamespace(player=player, cog=cog, user_id="reward"))

        first = combat._apply_weekly_event_progress([{"id": "slime"}])
        second = combat._apply_weekly_event_progress([{"id": "slime"}])

        self.assertIn("完成", first)
        self.assertEqual(second.count("完成"), 0)
        self.assertEqual(bank_awards, [60])
        self.assertEqual(player.inventory["health_potion"], 3)


class OnboardingContentTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_adventure_is_short_and_guarantees_weapon(self):
        class FakeTutorial(TutorialMixin):
            def __init__(self):
                self.player = SimpleNamespace(language="zh")
                self.started = None
                self.log_message = ""

            def start_combat(self, monsters):
                self.started = monsters

            def build_battle_menu(self):
                pass

        sent = []
        interaction = SimpleNamespace(followup=SimpleNamespace(send=lambda *args, **kwargs: None))

        async def send(*args, **kwargs):
            sent.append((args, kwargs))

        interaction.followup.send = send
        tutorial = FakeTutorial()
        await tutorial.start_tutorial_battle(interaction)

        self.assertEqual(len(tutorial._TUTORIAL_TIPS), 2)
        self.assertEqual(tutorial.started[0]["id"], "tutorial_slime")
        self.assertEqual(tutorial.started[0]["drops"]["wooden_sword"], 1)
        self.assertTrue(sent)

    async def test_compass_prioritizes_equipping_then_first_patrol(self):
        player = TRPGPlayer("starter")
        player.onboarding_done = True
        player.current_area = "area_00village"
        player.inventory["wooden_sword"] = 1
        player.stats["monsters_killed"] = 1
        view = SimpleNamespace(
            player=player,
            cog=SimpleNamespace(areas={"area_00village": {"area_name": "米酥村", "is_village": True}}, quests={}),
        )
        _, equip_value = build_adventure_compass(view, 0)
        self.assertIn("裝備", equip_value)

        player.weapon = "wooden_sword"
        _, patrol_value = build_adventure_compass(view, 0)
        self.assertIn("移動", patrol_value)
