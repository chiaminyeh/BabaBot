"""Deterministic, zero-consumable Bee Queen balance benchmark.

Players are built by the production stat/skill pipeline with ordinary shop gear. All
combat actions use TRPGCombat, including AP, cooldown, status, phase and summon paths.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from trpg.combat import TRPGCombat
from trpg.player import TRPGPlayer
from trpg.stats import grant_qualified_skills, recalc_player_stats

ARCHETYPES = ("knight", "rogue", "mage", "warlock")
COMMON_SKILLS = ("heal_light", "regeneration", "purify", "mana_surge")
GEAR = {
    "knight": ("berserker_axe", "bark_armor", "iron_will_ring"),
    "rogue": ("stinger_rapier", "bark_armor", "windrunner_pendant"),
    "mage": ("thorn_staff", "bark_armor", "iron_will_ring"),
    "warlock": ("thorn_staff", "bark_armor", "iron_will_ring"),
}


class BenchCog:
    def __init__(self):
        load = lambda name: json.loads((ROOT / "trpg_data" / name).read_text(encoding="utf-8"))
        self.skills = load("skills.json")
        self.items = load("items.json")
        self.monsters = load("monsters.json")
        self.status_effects = load("status_effects.json")
        self.world_areas = load("areas.json")
        self.monster_pool = []
        self.areas = {"area_10deep_forest": {**self.world_areas["area_10deep_forest"], "monsters": self.monsters}}

    def save_players(self, **_kwargs): pass
    def get_bank_balance(self, _uid): return 0
    def try_spend(self, *_args): return False


class BenchView:
    def __init__(self, player, cog):
        self.player, self.cog = player, cog
        self.user_id, self.in_battle, self.won = "bee-benchmark", True, False
        ids = tuple(cog.areas["area_10deep_forest"].get("boss_minions", ())) + ("bee_queen",)
        self.monster_slots = [
            {"monster": dict(cog.monsters[mid]), "hp": cog.monsters[mid]["max_hp"], "av": 0, "status": {}}
            for mid in ids
        ]

    def _front_slot(self):
        return next((s for s in self.monster_slots if s.get("hp", 0) > 0), None)

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
            from trpg.entity import absorb_monster_damage
            slot["hp"] = max(0, absorb_monster_damage(slot, value))

    def process_death(self, log, message):
        self.in_battle = False
        return f"{log}\n{message}"

    def build_main_menu(self): self.in_battle = False


def make_player(archetype: str, level: int, cog: BenchCog) -> TRPGPlayer:
    player = TRPGPlayer(f"bench-{archetype}-{level}")
    player.onboarding_done = True
    player.language = "en"
    player.current_area = "area_10deep_forest"
    player.level = level
    player.stat_alloc = {name: (level * 2 if name == archetype else 0) for name in ARCHETYPES}
    player.core_ability = archetype
    player.weapon, player.armor, player.accessory = GEAR[archetype]
    recalc_player_stats(player, cog.items, heal_full=True)
    grant_qualified_skills(player, cog.skills)
    qualified = [
        sid for sid in player.skills
        if (cog.skills[sid].get("req_points") or {}).get(archetype) is not None
        and cog.skills[sid].get("req_level", 1) <= level
    ]
    # The production UI permits eight. At these levels every qualified class skill fits.
    common = ("heal_light", "regeneration", "purify", "power_slash") if archetype == "knight" else COMMON_SKILLS
    if archetype == "warlock" and len(qualified) + len(common) > 8:
        common = tuple(sid for sid in common if sid != "regeneration")
    player.skills = list(dict.fromkeys(qualified + list(common)))
    player.equipped_skills = list(dict.fromkeys(qualified + list(common)))[:8]
    player.skill_levels = {sid: 1 for sid in player.skills}
    player.inventory = {}  # explicit zero-consumable benchmark
    return player


def _usable(combat, skill_id):
    skill = combat.cog.skills.get(skill_id, {})
    player = combat.player
    hp_cost = int(player.max_hp * skill.get("hp_cost_percent", 0))
    return (skill_id in player.equipped_skills and not combat.skill_cds.get(skill_id, 0)
            and player.current_mp >= skill.get("mp_cost", 0) and player.current_hp > hp_cost)


def choose_action(combat: TRPGCombat, archetype: str) -> tuple[str, str]:
    """Conservative explainable policy: answer telegraphs, stabilize, kill healer, burst."""
    player = combat.player
    alive = [s for s in combat.view.monster_slots if s["hp"] > 0]
    worker = next((s for s in alive if s["monster"].get("id") == "worker_bee"), None)
    queen = next((s for s in alive if s["monster"].get("id") == "bee_queen"), None)
    target = worker or queen or (alive[0] if alive else None)
    threatened = any((s.get("telegraph") or {}).get("effect", {}).get("type") in {"heavy_attack", "cast_skill"}
                     or s.get("is_charging") or s.get("bomb_fuse") for s in alive)

    if threatened:
        if archetype == "rogue": return combat.dodge(), "dodge"
        return combat.defend(), "defend"
    if any(status in player.status_effects for status in ("poison", "paralysis")) and _usable(combat, "purify"):
        return combat.use_skill("purify"), "skill:purify"
    if player.current_hp < player.max_hp * .48 and _usable(combat, "heal_light"):
        return combat.use_skill("heal_light"), "skill:heal_light"
    buffs = getattr(player, "combat_buffs", None) or {}
    if (player.current_hp < player.max_hp * .78 and not buffs.get("regen_turns")
            and _usable(combat, "regeneration")):
        return combat.use_skill("regeneration"), "skill:regeneration"
    if player.current_mp < player.max_mp * .35 and _usable(combat, "mana_surge"):
        return combat.use_skill("mana_surge"), "skill:mana_surge"
    one_time_support = getattr(player, "benchmark_one_time_support", set())
    player.benchmark_one_time_support = one_time_support
    support = {
        "knight": (("iron_skin", player.current_hp < player.max_hp * .72),
                   ("battle_cry", not buffs.get("atk_mult")),
                   ("flame_coating", not buffs.get("weapon_coating"))),
        "rogue": (("shadow_step", player.current_hp < player.max_hp * .65), ("frost_coating", True)),
        "mage": (),
        "warlock": (("hypnotic_gaze", not combat.demon_active and player.current_hp > player.max_hp * .65),
                    ("venom_cloud", not buffs.get("blood_feast_turns"))),
    }[archetype]
    one_time_ids = {"iron_skin", "battle_cry", "flame_coating", "shadow_step", "frost_coating", "hypnotic_gaze"}
    for sid, wanted in support:
        if sid in one_time_ids and sid in one_time_support:
            continue
        if wanted and _usable(combat, sid):
            if sid in one_time_ids:
                one_time_support.add(sid)
            return combat.use_skill(sid), f"skill:{sid}"
    attacks = {
        "knight": ("power_slash", "shield_bash"),
        "rogue": ("twin_shot", "double_strike"),
        "mage": ("water_ball", "fireball", "thunder_strike", "ice_spear"),
        "warlock": (("inferno",) if player.current_hp > player.max_hp * .90 and len(alive) > 1
                     and "inferno" not in one_time_support else ())
                   + (("blood_strike",) if player.current_hp > player.max_hp * .70 else ())
                   + ("dark_orb",),
    }[archetype]
    for sid in attacks:
        if _usable(combat, sid):
            legal = combat.legal_target_slots(combat.cog.skills[sid].get("target_type", "front"))
            selected = target if target in legal else (legal[0] if legal else None)
            if sid == "inferno":
                one_time_support.add(sid)
            return combat.use_skill(sid, target_slot=selected), f"skill:{sid}"
    return combat.player_attack(), "attack"


def simulate_once(archetype: str, level: int, seed: int, max_rounds: int = 100) -> dict:
    random.seed(seed)
    cog = BenchCog()
    player = make_player(archetype, level, cog)
    view = BenchView(player, cog)
    combat = TRPGCombat(view)
    combat.player_max_ap = combat.calculate_player_ap()
    combat.player_ap = combat.player_max_ap
    combat._process_victory = lambda: setattr(view, "won", True) or "\n<Victory>"
    damage_dealt = 0
    actions = Counter()
    phase_seen = summons = 0
    previous_ids = [s["monster"]["id"] for s in view.monster_slots]
    last_log = ""
    while player.current_hp > 0 and not view.won and combat.round_number < max_rounds:
        hp_before = sum(max(0, s["hp"]) for s in view.monster_slots)
        last_log, action = choose_action(combat, archetype)
        hp_after = sum(max(0, s["hp"]) for s in view.monster_slots)
        damage_dealt += max(0, hp_before - hp_after)
        actions[action] += 1
        phase_seen = max(phase_seen, int(any(s.get("phase2_triggered") for s in view.monster_slots)))
        ids = [s["monster"].get("id") for s in view.monster_slots]
        if ids != previous_ids:
            summons += sum(1 for mid in ids if mid not in previous_ids)
            previous_ids = ids

    if view.won: cause = "victory"
    elif player.current_hp > 0: cause = "round_limit"
    elif "poison" in player.status_effects: cause = "poison"
    elif "paralysis" in player.status_effects: cause = "paralysis"
    elif "skill" in last_log.lower() or "俯衝" in last_log: cause = "boss_skill"
    else: cause = "attack"
    return {
        "won": view.won, "rounds": max(1, combat.round_number), "damage_taken": player.max_hp - max(0, player.current_hp),
        "estimated_net_enemy_hp_reduction": damage_dealt, "death_cause": cause, "phase_triggers": phase_seen,
        "estimated_summons": summons, "skills_used": sum(v for k, v in actions.items() if k.startswith("skill:")),
        "defends": actions["defend"], "dodges": actions["dodge"],
    }


def run_benchmark(seeds: int = 200, levels=(14, 15)) -> list[dict]:
    rows = []
    for level in levels:
        for archetype in ARCHETYPES:
            # Use paired seeds across levels so progression trends compare the same RNG scenarios.
            runs = [simulate_once(archetype, level, seed) for seed in range(seeds)]
            rows.append({
                "level": level, "archetype": archetype, "runs": seeds, "wins": sum(r["won"] for r in runs),
                "win_rate": sum(r["won"] for r in runs) / seeds, "median_rounds": statistics.median(r["rounds"] for r in runs),
                "median_damage_taken": statistics.median(r["damage_taken"] for r in runs),
                "median_estimated_net_enemy_hp_reduction": statistics.median(r["estimated_net_enemy_hp_reduction"] for r in runs),
                "death_causes": dict(Counter(r["death_cause"] for r in runs)),
                "runs_reaching_phase2": sum(r["phase_triggers"] for r in runs),
                "estimated_summons": sum(r["estimated_summons"] for r in runs),
                "skills_used": sum(r["skills_used"] for r in runs), "defends": sum(r["defends"] for r in runs),
                "dodges": sum(r["dodges"] for r in runs), "real_combat_path": True,
            })
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=200)
    parser.add_argument("--levels", default="14,15")
    parser.add_argument("--csv")
    args = parser.parse_args()
    rows = run_benchmark(args.seeds, tuple(int(x) for x in args.levels.split(",")))
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0].keys()); writer.writeheader(); writer.writerows(rows)
    for row in rows:
        print(f"Lv{row['level']} {row['archetype']:<7} {row['wins']}/{row['runs']} ({row['win_rate']:.1%}) "
              f"rounds={row['median_rounds']} net_hp_est={row['median_estimated_net_enemy_hp_reduction']} taken={row['median_damage_taken']} "
              f"phase_runs={row['runs_reaching_phase2']} summon_est={row['estimated_summons']} deaths={row['death_causes']} "
              f"skills={row['skills_used']} defend={row['defends']} dodge={row['dodges']}")


if __name__ == "__main__": main()
