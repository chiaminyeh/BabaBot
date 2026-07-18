import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from trpg.combat import TRPGCombat, can_pay_hp_cost, demon_attack_power, effective_hp_cost, format_blood_demon_status, vitality_icon
from trpg.monster_ai import _execute_cast_skill, ai_kamikaze
from trpg.player import TRPGPlayer
from trpg.views.battle import BattleLayout

ROOT = Path(__file__).parents[1]


class DummyCog:
    def __init__(self):
        self.items = {}
        self.skills = json.loads((ROOT / "trpg_data" / "skills.json").read_text(encoding="utf-8"))
        self.status_effects = json.loads((ROOT / "trpg_data" / "status_effects.json").read_text(encoding="utf-8"))

    def save_players(self, **kwargs):
        pass


class DummyView:
    def __init__(self, player, monsters):
        self.player = player
        self.cog = DummyCog()
        self.monster_slots = [
            {"monster": dict(m), "hp": m["max_hp"], "status": {}}
            for m in monsters
        ]
        self.in_battle = True
        self.user_id = "warlock-test"
        self.victories = 0

    def _front_slot(self):
        return next((s for s in self.monster_slots if s["hp"] > 0), None)

    @property
    def active_monster(self):
        slot = self._front_slot()
        return slot["monster"] if slot else None

    @property
    def monster_hp(self):
        slot = self._front_slot()
        return slot["hp"] if slot else 0

    @monster_hp.setter
    def monster_hp(self, value):
        slot = self._front_slot()
        if slot:
            slot["hp"] = max(0, value)

    def process_death(self, log, message):
        self.in_battle = False
        return f"{log}\n{message}"

    def build_main_menu(self):
        self.in_battle = False


def monster(mid="blood", vitality="blood", hp=500, atk=1, spd=10):
    return {"id": mid, "name": mid, "name_en": mid, "max_hp": hp, "atk": atk,
            "def": 0, "mdef": 0, "spd": spd, "vitality_type": vitality}


def make_combat(monsters=None, hp=500, max_hp=500, mp=0, max_mp=200, speed=100):
    player = TRPGPlayer("warlock-test")
    player.onboarding_done = True
    player.level = 80
    player.max_hp = max_hp
    player.current_hp = hp
    player.max_mp = max_mp
    player.current_mp = mp
    player.base_atk = 80
    player.base_int = 120
    player.base_def = player.base_mdef = 0
    player.base_spd = speed
    player.stat_alloc = {"knight": 0, "rogue": 0, "mage": 0, "warlock": 80}
    player.core_ability = "warlock"
    player.equipped_skills = ["blood_strike", "dark_orb", "hypnotic_gaze", "venom_cloud", "inferno", "void_eruption", "blood_awakening", "abyssal_requiem"]
    view = DummyView(player, monsters or [monster()])
    combat = TRPGCombat(view)
    combat.player_ap = combat.player_max_ap = 3
    combat.round_started = True
    combat._process_victory = lambda: "<victory>"
    return combat, view, player


class WarlockBloodCycleTests(unittest.TestCase):
    def test_skill_menu_projects_hp_as_current_over_max(self):
        combat, source_view, player = make_combat(hp=139, max_hp=195)
        player.equipped_skills = ["hypnotic_gaze"]
        labels = []
        ui = SimpleNamespace(
            player=player, cog=source_view.cog, combat=combat, in_battle=True,
            clear_items=lambda: None,
            add_action_button=lambda **kwargs: labels.append(kwargs["label"]),
            log_message="",
        )
        BattleLayout.handle_skill_menu(ui)
        self.assertTrue(any("（100/195）" in label for label in labels), labels)

    def test_blood_demon_ui_shows_shared_attack_formula_and_shield(self):
        combat, _view, player = make_combat(max_hp=195)
        combat.blood_shield = 77
        combat.demon_skill_level = 3
        attack = demon_attack_power(combat)
        status = format_blood_demon_status(combat, "zh")
        self.assertGreater(attack, 0)
        self.assertIn(f"ATK: `{attack}`", status)
        self.assertIn("77/195", status)

    def test_hp_cost_allows_exactly_one_hp_but_never_zero(self):
        self.assertTrue(can_pay_hp_cost(101, 100))
        self.assertFalse(can_pay_hp_cost(100, 100))

    def test_vitality_defaults_to_blood_and_icons_are_predictable(self):
        self.assertEqual(vitality_icon({}), "🩸")
        self.assertEqual(vitality_icon({"vitality_type": "soul"}), "🔷")
        self.assertEqual(vitality_icon({"vitality_type": "construct"}), "⚙️")

    def test_blood_strike_marks_target_and_pact_caps_at_three(self):
        combat, view, player = make_combat([monster(hp=5000)], hp=500)
        for _ in range(4):
            combat.skill_cds.clear()
            combat.player_ap = 3
            combat.use_skill("blood_strike", target_slot=view.monster_slots[0])
        self.assertEqual(player.combat_buffs["blood_pact_stacks"], 3)
        self.assertGreater(view.monster_slots[0]["status"]["blood_mark"]["turns"], 0)

    def test_failed_sacrifice_consumes_nothing(self):
        combat, _view, player = make_combat(hp=100, max_hp=500, mp=50)
        before = (player.current_hp, player.current_mp, combat.player_ap, dict(combat.skill_cds), dict(player.combat_buffs))
        combat.use_skill("hypnotic_gaze")
        after = (player.current_hp, player.current_mp, combat.player_ap, dict(combat.skill_cds), dict(player.combat_buffs))
        self.assertEqual(before, after)

    def test_summon_creates_shield_from_actual_sacrifice_without_attacking(self):
        combat, view, player = make_combat(hp=101, max_hp=500)
        before_enemy_hp = view.monster_slots[0]["hp"]
        combat.use_skill("hypnotic_gaze")
        self.assertEqual(player.current_hp, 1)
        self.assertEqual(combat.blood_shield, 100)
        self.assertTrue(combat.demon_active)
        self.assertEqual(view.monster_slots[0]["hp"], before_enemy_hp)

    def test_demon_attacks_front_once_per_complete_round_and_pays_fuel(self):
        combat, view, _player = make_combat()
        combat.blood_shield = 100
        combat.demon_skill_level = 1
        back = view.monster_slots[1] if len(view.monster_slots) > 1 else None
        front_hp = view.monster_slots[0]["hp"]
        log = combat._demon_round_action("")
        after_first = view.monster_slots[0]["hp"]
        combat._demon_round_action(log)
        self.assertLess(after_first, front_hp)
        self.assertEqual(view.monster_slots[0]["hp"], after_first)
        self.assertEqual(combat.blood_shield, 75)
        if back:
            self.assertEqual(back["hp"], back["monster"]["max_hp"])

    def test_demon_gets_mark_bonus_and_collaborates_with_blood_drain(self):
        combat, view, player = make_combat(hp=400)
        combat.blood_shield = 100
        marked = view.monster_slots[0]
        marked["status"]["blood_mark"] = {"turns": 3}
        before_hp, before_enemy = player.current_hp, marked["hp"]
        combat._demon_round_action("")
        self.assertGreater(player.current_hp, before_hp)
        self.assertGreater(before_enemy - marked["hp"], 100)

    def test_void_execution_consumes_pact_only_after_valid_cast(self):
        combat, view, player = make_combat(hp=500, mp=200)
        player.combat_buffs["blood_pact_stacks"] = 3
        combat.use_skill("void_eruption", target_slot=view.monster_slots[0])
        self.assertEqual(player.combat_buffs["blood_pact_stacks"], 0)

    def test_routed_damage_preserves_tail_and_demon_disappears_at_zero(self):
        combat, _view, player = make_combat(hp=500)
        combat.blood_shield = 20
        dealt = combat.apply_player_damage(100)
        self.assertEqual(dealt, 80)
        self.assertEqual(player.current_hp, 420)
        self.assertEqual(combat.blood_shield, 0)
        self.assertFalse(combat.demon_active)

    def test_status_monster_spell_and_kamikaze_all_route_through_blood_shield(self):
        combat, view, player = make_combat(hp=500, max_hp=500)
        combat.blood_shield = 100
        player.status_effects["poison"] = {"turns": 2}
        combat.round_started = False
        combat._player_turn_start()
        self.assertLess(combat.blood_shield, 100)
        self.assertGreater(player.current_hp, 475)

        player.current_hp, combat.blood_shield = 500, 100
        caster_slot = view.monster_slots[0]
        caster_slot["monster"].update({"magic": 100, "base_int": 100})
        _execute_cast_skill(combat, caster_slot, {"skill_id": "fireball"}, "")
        self.assertLess(combat.blood_shield, 100)
        self.assertGreater(player.current_hp, 400)

        player.current_hp, combat.blood_shield = 500, 100
        caster_slot["hp"] = 10
        caster_slot["monster"]["max_hp"] = 500
        with patch("random.random", return_value=0.0):
            ai_kamikaze(combat, caster_slot, "")
        self.assertLess(combat.blood_shield, 100)
        self.assertGreater(player.current_hp, 425)

    def test_blood_overheal_replenishes_shield_but_soul_and_construct_restore_mp(self):
        combat, _view, player = make_combat(hp=490, mp=0)
        combat.blood_shield = 20
        hp, mp, shield = combat.apply_drain(100, 0.5, "blood")
        self.assertEqual((hp, mp, shield), (10, 0, 40))
        self.assertEqual(combat.blood_shield, 60)
        hp, mp, shield = combat.apply_drain(100, 0.5, "soul")
        self.assertEqual((hp, mp, shield), (0, 35, 0))
        hp, mp, shield = combat.apply_drain(100, 0.5, "construct")
        self.assertEqual((hp, mp, shield), (0, 15, 0))

    def test_target_rules_preserve_formation_and_explicit_selection(self):
        combat, view, _player = make_combat([monster("front"), monster("back"), monster("rear")])
        self.assertEqual(combat.legal_target_slots("front"), [view.monster_slots[0]])
        self.assertEqual(combat.legal_target_slots("back"), view.monster_slots[1:])
        self.assertEqual(combat.legal_target_slots("any"), view.monster_slots)
        self.assertEqual(combat.legal_target_slots("all"), view.monster_slots)

    def test_target_menu_shows_vitality_mark_hp_percent_and_formation(self):
        combat, source_view, player = make_combat([
            monster("front", "blood", hp=500), monster("ghost", "soul", hp=400)
        ])
        source_view.monster_slots[1]["hp"] = 200
        source_view.monster_slots[1]["status"]["blood_mark"] = {"turns": 2}
        captured = {}
        ui = SimpleNamespace(
            combat=combat, player=player, monster_slots=source_view.monster_slots,
            clear_items=lambda: None,
            add_action_select=lambda placeholder, options, **kwargs: captured.update(options=options),
            add_action_button=lambda **kwargs: None,
        )
        self.assertTrue(BattleLayout.build_target_menu(ui, "skill", "void_eruption", "any"))
        front, back = captured["options"]
        self.assertIn("🩸", front[0])
        self.assertIn("🔷", back[0])
        self.assertIn("🩸", back[0])
        self.assertIn("50%", back[2])
        self.assertIn("後", back[2])
        old_ids = [option[1] for option in captured["options"]]
        self.assertTrue(BattleLayout.build_target_menu(ui, "skill", "dark_orb", "any"))
        new_ids = [option[1] for option in captured["options"]]
        self.assertNotEqual(old_ids, new_ids)
        self.assertEqual(ui.pending_target_action["nonce"], ui.target_action_nonce)

    def test_stale_selected_target_does_not_charge_skill(self):
        combat, view, player = make_combat([monster("front"), monster("rear")], hp=500, mp=100)
        stale = view.monster_slots[1]
        stale["hp"] = 0
        before = (player.current_hp, player.current_mp, combat.player_ap, dict(combat.skill_cds))
        combat.use_skill("void_eruption", target_slot=stale)
        self.assertEqual(before, (player.current_hp, player.current_mp, combat.player_ap, dict(combat.skill_cds)))

    def test_multi_target_drain_is_capped_once_per_cast(self):
        combat, view, player = make_combat([monster("a", hp=1000), monster("b", hp=1000), monster("c", hp=1000)], hp=1)
        player.current_mp = 200
        combat.use_skill("inferno")
        self.assertLessEqual(player.current_hp, 1 + int(player.max_hp * 0.35))

    def test_multi_target_drain_splits_blood_hp_and_soul_mp_under_one_cap(self):
        combat, view, player = make_combat(
            [monster("flesh", "blood", hp=5000), monster("ghost", "soul", hp=5000)],
            hp=300, max_hp=500, mp=100, max_mp=200,
        )
        combat.use_skill("inferno")
        self.assertGreater(player.current_hp, 225)  # after 15% offering, blood target restores HP
        self.assertGreater(player.current_mp, 75)   # after 25 MP cost, soul target restores MP
        total_recovery = (player.current_hp - 225) + (player.current_mp - 75)
        self.assertLessEqual(total_recovery, int(player.max_hp * 0.35))

    def test_bloodflame_applies_mark_bonus_per_target_without_consuming_marks(self):
        combat, view, player = make_combat([monster("marked", hp=2000), monster("plain", hp=2000)], hp=500)
        player.current_mp = 200
        view.monster_slots[0]["status"]["blood_mark"] = {"turns": 3}
        with patch("trpg.combat.random.uniform", return_value=1.0):
            combat.use_skill("inferno")
        marked_damage = 2000 - view.monster_slots[0]["hp"]
        plain_damage = 2000 - view.monster_slots[1]["hp"]
        self.assertAlmostEqual(marked_damage / plain_damage, 1.5, places=2)
        self.assertIn("blood_mark", view.monster_slots[0]["status"])

    def test_abyssal_requiem_bonuses_each_mark_and_leaves_unrelated_marks(self):
        combat, view, player = make_combat([monster("a", hp=3000), monster("b", hp=3000), monster("c", hp=3000)], hp=500)
        player.current_mp = 200
        view.monster_slots[0]["status"]["blood_mark"] = {"turns": 2}
        view.monster_slots[2]["status"]["blood_mark"] = {"turns": 2}
        with patch("trpg.combat.random.uniform", return_value=1.0), patch("trpg.combat.random.random", return_value=1.0):
            combat.use_skill("abyssal_requiem")
        damages = [3000 - slot["hp"] for slot in view.monster_slots]
        self.assertAlmostEqual(damages[0] / damages[1], 1.5, places=2)
        self.assertAlmostEqual(damages[2] / damages[1], 1.5, places=2)
        self.assertTrue(all("blood_mark" in view.monster_slots[i]["status"] for i in (0, 2)))

    def test_blood_awakening_overflow_discounts_next_sacrifice_once_and_is_bounded(self):
        combat, view, player = make_combat([monster(vitality="construct", hp=5000)], hp=500, mp=200)
        combat.use_skill("blood_awakening")
        combat.skill_cds.clear()
        combat.player_ap = 3
        player.current_hp = player.max_hp
        combat.apply_drain(9999, 1.0, "blood")
        self.assertEqual(player.combat_buffs["blood_sacrifice_discount"], 50)
        self.assertEqual(effective_hp_cost(player, 75), 25)
        combat.use_skill("inferno")
        self.assertEqual(player.current_hp, 475)
        self.assertNotIn("blood_sacrifice_discount", player.combat_buffs)
        combat.skill_cds.clear()
        combat.player_ap = 3
        combat.use_skill("inferno")
        self.assertEqual(player.current_hp, 400)

    def test_discount_never_makes_positive_cost_free_and_failed_cast_does_not_consume_it(self):
        combat, view, player = make_combat(hp=20, mp=0)
        player.combat_buffs["blood_sacrifice_discount"] = 999
        self.assertEqual(effective_hp_cost(player, 75), 1)
        combat.use_skill("inferno")
        self.assertEqual(player.combat_buffs["blood_sacrifice_discount"], 999)
        self.assertEqual(player.current_hp, 20)
        player.current_mp = 200
        stale = view.monster_slots[0]
        stale["hp"] = 0
        combat.use_skill("void_eruption", target_slot=stale)
        self.assertEqual(player.combat_buffs["blood_sacrifice_discount"], 999)

    def test_blood_feast_bonus_triggers_once_per_complete_round(self):
        combat, view, player = make_combat(hp=100)
        player.combat_buffs.update({"lifesteal_bonus": 0.25, "blood_feast_turns": 5})
        target = view.monster_slots[0]
        before = target["hp"]
        first = combat._apply_blood_feast_bonus(target, 40, "")
        after = target["hp"]
        second = combat._apply_blood_feast_bonus(target, 40, first)
        self.assertEqual(before - after, 20)
        self.assertEqual(target["hp"], after)
        for _ in range(5):
            combat._tick_combat_buffs()
        self.assertNotIn("blood_feast_turns", player.combat_buffs)
        self.assertNotIn("lifesteal_bonus", player.combat_buffs)
        self.assertEqual(combat._apply_blood_feast_bonus(target, 40, ""), "")

    def test_blood_shield_runtime_state_round_trips_and_legacy_defaults(self):
        combat, _view, _player = make_combat()
        combat.blood_shield = 77
        combat.demon_skill_level = 3
        state = combat.blood_runtime_state()
        other, _view2, _player2 = make_combat()
        other.load_blood_runtime_state(state)
        self.assertEqual((other.blood_shield, other.demon_skill_level), (77, 3))
        other.load_blood_runtime_state({})
        self.assertEqual((other.blood_shield, other.demon_skill_level), (0, 1))
        other.load_blood_runtime_state({"blood_shield": 10, "demon_last_round": 0})
        self.assertEqual(other._demon_last_round, 0)

    def test_skill_killing_blow_charges_ap_records_proficiency_and_settles_once(self):
        combat, view, player = make_combat([monster(hp=1)], hp=500, mp=200)
        victories = []
        combat._process_victory = lambda: victories.append(True) or "<victory>"
        before_uses = player.skill_usage.get("dark_orb", 0)
        combat.use_skill("dark_orb", target_slot=view.monster_slots[0])
        self.assertEqual(combat.player_ap, 2)
        self.assertEqual(player.skill_usage.get("dark_orb", 0), before_uses + 1)
        self.assertEqual(len(victories), 1)

    def test_warlock_skill_data_preserves_ids_and_new_contract(self):
        skills = DummyCog().skills
        ids = {"blood_strike", "dark_orb", "hypnotic_gaze", "venom_cloud", "inferno", "void_eruption", "blood_awakening", "abyssal_requiem"}
        self.assertTrue(ids.issubset(skills))
        self.assertEqual(skills["blood_strike"]["hp_cost_percent"], 0.12)
        self.assertEqual(skills["hypnotic_gaze"]["summon"], "blood_demon")
        self.assertNotIn("apply_status_chance", skills["hypnotic_gaze"])
        pact = skills["venom_cloud"]
        self.assertNotIn("def_mult", pact.get("buff", {}))
        self.assertNotIn("mdef_mult", pact.get("buff", {}))
        self.assertEqual(skills["inferno"]["target_type"], "all")
        self.assertEqual(skills["inferno"]["marked_target_damage_mult"], 1.5)
        self.assertEqual(skills["abyssal_requiem"]["marked_target_damage_mult"], 1.5)
        awakening = skills["blood_awakening"]
        self.assertNotIn("atk_mult", awakening.get("buff", {}))
        self.assertNotIn("magic_mult", awakening.get("buff", {}))
        self.assertNotIn("spd_mult", awakening.get("buff", {}))
        self.assertNotIn("regen", awakening)
        self.assertEqual(awakening["overflow_discount_cap_max_hp"], 0.1)


if __name__ == "__main__":
    unittest.main()
