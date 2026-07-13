#!/usr/bin/env python3
"""Monte Carlo encounter model for The Engine Room Heist (Pathfinder 2e).

This is a tactical abstraction, not a complete PF2e rules engine. It resolves the
mechanics that most strongly affect this encounter: initiative, degrees of
success, MAP, healing and recovery, area damage, breath cooldowns, mephit death
bursts, Reactive Strike, vents, and Wang's shove toward the core.

The model uses only Python's standard library so that the published results can
be reproduced with one command.
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import asdict, dataclass, field
from typing import Iterable, Literal, Sequence


Tactic = Literal["focused", "boss-first"]
Outcome = Literal["clean", "strained", "defeat"]


@dataclass(frozen=True)
class Scenario:
    slug: str
    label: str
    mephits: int
    automatic_vents: bool
    weak_wang: bool = False


SCENARIOS = (
    Scenario("as-written", "As written", mephits=2, automatic_vents=True),
    Scenario(
        "one-mephit",
        "Remove one mephit; retain the automatic vents",
        mephits=1,
        automatic_vents=True,
    ),
    Scenario(
        "one-mephit-no-vents",
        "One mephit; remove the automatic vents",
        mephits=1,
        automatic_vents=False,
    ),
    Scenario(
        "weak-wang-one-mephit",
        "Weak Wang; one mephit; retain the automatic vents",
        mephits=1,
        automatic_vents=True,
        weak_wang=True,
    ),
)


@dataclass
class Combatant:
    name: str
    side: Literal["party", "enemy"]
    max_hp: int
    ac: int
    fort: int
    reflex: int
    will: int
    initiative: int
    role: str
    hp: int = field(init=False)
    dropped_once: bool = False
    dying: int = 0
    dead: bool = False
    persistent_fire: int = 0
    breath_cooldown: int = 0

    def __post_init__(self) -> None:
        self.hp = self.max_hp

    @property
    def standing(self) -> bool:
        return self.hp > 0 and not self.dead

    def take_damage(self, amount: int) -> bool:
        """Apply damage and return True only when this damage drops the target."""
        if amount <= 0 or self.dead or self.hp <= 0:
            return False
        self.hp = max(0, self.hp - amount)
        if self.hp == 0:
            self.dropped_once = True
            if self.side == "party":
                self.dying = max(1, self.dying)
            else:
                self.dead = True
            return True
        return False

    def heal(self, amount: int) -> None:
        if self.dead or amount <= 0:
            return
        was_dying = self.hp == 0
        self.hp = min(self.max_hp, self.hp + amount)
        if was_dying and self.hp > 0:
            self.dying = 0


@dataclass
class FightState:
    rng: random.Random
    scenario: Scenario
    tactic: Tactic
    party: list[Combatant]
    enemies: list[Combatant]
    heal_slots: int = 4
    wizard_slots: int = 3
    panic_round: int = 0
    wang_reaction_used: bool = False
    round_number: int = 0

    def standing_party(self) -> list[Combatant]:
        return [actor for actor in self.party if actor.standing]

    def active_enemies(self) -> list[Combatant]:
        return [actor for actor in self.enemies if actor.standing]

    def wang(self) -> Combatant | None:
        return next((enemy for enemy in self.enemies if enemy.role == "wang"), None)


def d20(rng: random.Random) -> int:
    return rng.randint(1, 20)


def roll(rng: random.Random, count: int, sides: int, flat: int = 0) -> int:
    return sum(rng.randint(1, sides) for _ in range(count)) + flat


def degree(check: int, modifier: int, dc: int) -> int:
    """Return PF2e degree: 0 crit fail, 1 fail, 2 success, 3 crit success."""
    total = check + modifier
    if total >= dc + 10:
        result = 3
    elif total >= dc:
        result = 2
    elif total <= dc - 10:
        result = 0
    else:
        result = 1
    if check == 20:
        result = min(3, result + 1)
    elif check == 1:
        result = max(0, result - 1)
    return result


def strike_damage(
    rng: random.Random,
    attack_bonus: int,
    target: Combatant,
    dice: tuple[int, int],
    flat: int,
) -> tuple[int, bool]:
    result = degree(d20(rng), attack_bonus, target.ac)
    if result < 2:
        return 0, False
    damage = roll(rng, dice[0], dice[1], flat)
    if result == 3:
        damage *= 2
    return damage, True


def basic_save_damage(
    rng: random.Random,
    target: Combatant,
    save_name: Literal["fort", "reflex", "will"],
    dc: int,
    raw_damage: int,
) -> int:
    result = degree(d20(rng), getattr(target, save_name), dc)
    if result == 0:
        return raw_damage * 2
    if result == 1:
        return raw_damage
    if result == 2:
        return raw_damage // 2
    return 0


def make_party() -> list[Combatant]:
    return [
        Combatant("Fighter", "party", 64, 23, 12, 10, 9, 10, "front"),
        Combatant("Rogue", "party", 50, 22, 9, 12, 10, 11, "front"),
        Combatant("Cleric", "party", 52, 20, 11, 8, 12, 9, "back"),
        Combatant("Wizard", "party", 42, 20, 8, 10, 12, 8, "back"),
    ]


def make_enemies(scenario: Scenario) -> list[Combatant]:
    wang = Combatant(
        "Chief Engineer Wang",
        "enemy",
        85 if scenario.weak_wang else 105,
        22 if scenario.weak_wang else 24,
        13 if scenario.weak_wang else 15,
        9 if scenario.weak_wang else 11,
        12 if scenario.weak_wang else 14,
        12 if scenario.weak_wang else 14,
        "wang",
    )
    mephits = [
        Combatant(f"Mephit {index + 1}", "enemy", 40, 19, 9, 11, 7, 9, "mephit")
        for index in range(scenario.mephits)
    ]
    apprentice = Combatant("Apprentice", "enemy", 15, 15, 5, 7, 4, 4, "apprentice")
    return [wang, *mephits, apprentice]


def weighted_choice(
    rng: random.Random, actors: Sequence[Combatant], weight_fn
) -> Combatant:
    weights = [max(0.01, float(weight_fn(actor))) for actor in actors]
    return rng.choices(list(actors), weights=weights, k=1)[0]


def choose_enemy_target(state: FightState, attacker_role: str) -> Combatant | None:
    targets = state.standing_party()
    if not targets:
        return None

    def weight(actor: Combatant) -> float:
        exposure = 1.8 if actor.role == "front" else 1.0
        if attacker_role == "mephit":
            exposure = 1.35 if actor.role == "front" else 1.0
        injury = 1.0 + (1.0 - actor.hp / actor.max_hp) * 0.65
        return exposure * injury

    return weighted_choice(state.rng, targets, weight)


def ordered_enemy_targets(state: FightState) -> list[Combatant]:
    alive = state.active_enemies()
    if state.tactic == "focused":
        priority = {"mephit": 0, "apprentice": 1, "wang": 2}
    else:
        priority = {"wang": 0, "mephit": 1, "apprentice": 2}
    return sorted(alive, key=lambda actor: (priority[actor.role], actor.hp / actor.max_hp))


def mephit_death_burst(
    state: FightState, source: Combatant | None, ranged: bool
) -> None:
    candidates = state.standing_party()
    if not candidates:
        return
    target: Combatant | None = None
    if source is not None and source.standing and not ranged:
        target = source
    elif state.rng.random() < 0.28:
        target = choose_enemy_target(state, "mephit")
    if target is not None:
        raw = roll(state.rng, 2, 6)
        target.take_damage(basic_save_damage(state.rng, target, "reflex", 19, raw))


def damage_enemy(
    state: FightState,
    target: Combatant,
    amount: int,
    source: Combatant | None,
    ranged: bool,
) -> None:
    dropped = target.take_damage(amount)
    if dropped and target.role == "mephit":
        mephit_death_burst(state, source, ranged)


def wang_reactive_strike(state: FightState, mover: Combatant) -> None:
    wang = state.wang()
    active_adds = sum(
        enemy.standing and enemy.role == "mephit" for enemy in state.enemies
    )
    reaction_chance = 0.45 if state.scenario.weak_wang else 0.42
    if state.tactic == "boss-first" and active_adds:
        if state.scenario.weak_wang:
            reaction_chance += 0.45
        else:
            reaction_chance += 0.40 if active_adds >= 2 else 0.10
    if (
        wang is None
        or not wang.standing
        or state.wang_reaction_used
        or not mover.standing
        or state.rng.random() >= reaction_chance
    ):
        return
    state.wang_reaction_used = True
    weak = state.scenario.weak_wang
    damage, _ = strike_damage(
        state.rng,
        15 if weak else 17,
        mover,
        (2, 8),
        3 if weak else 5,
    )
    mover.take_damage(damage)


def fighter_turn(state: FightState, actor: Combatant) -> None:
    for attack_index in range(2):
        targets = ordered_enemy_targets(state)
        if not targets or not actor.standing:
            return
        target = targets[0]
        if target.role == "wang" and attack_index == 0:
            wang_reactive_strike(state, actor)
            if not actor.standing:
                return
        damage, _ = strike_damage(
            state.rng, 14 - 5 * attack_index, target, (2, 8), 5
        )
        damage_enemy(state, target, damage, actor, ranged=False)


def rogue_turn(state: FightState, actor: Combatant) -> None:
    for attack_index in range(2):
        targets = ordered_enemy_targets(state)
        if not targets or not actor.standing:
            return
        target = targets[0]
        if target.role == "wang" and attack_index == 0:
            wang_reactive_strike(state, actor)
            if not actor.standing:
                return
        off_guard = state.rng.random() < (0.72 if attack_index == 0 else 0.52)
        original_ac = target.ac
        if off_guard:
            target.ac -= 2
        damage, hit = strike_damage(
            state.rng, 13 - 4 * attack_index, target, (2, 6), 4
        )
        target.ac = original_ac
        if hit and off_guard:
            damage += roll(state.rng, 2, 6)
        damage_enemy(state, target, damage, actor, ranged=False)


def cleric_turn(state: FightState, actor: Combatant) -> None:
    if state.heal_slots > 0:
        downed = [ally for ally in state.party if ally.hp == 0 and not ally.dead]
        injured = [
            ally
            for ally in state.party
            if ally.standing and ally.hp / ally.max_hp <= 0.38
        ]
        if downed or injured:
            target = min(downed or injured, key=lambda ally: ally.hp / ally.max_hp)
            target.heal(roll(state.rng, 2, 8, 16))
            state.heal_slots -= 1
            return
    targets = ordered_enemy_targets(state)
    if not targets:
        return
    target = targets[0]
    damage, _ = strike_damage(state.rng, 12, target, (3, 4), 4)
    damage_enemy(state, target, damage, actor, ranged=True)


def wizard_spell_targets(state: FightState) -> list[Combatant]:
    targets = ordered_enemy_targets(state)
    if not targets:
        return []
    primary = targets[0]
    if len(targets) == 1 or state.tactic == "boss-first":
        return [primary]
    if state.tactic == "focused":
        extras = [target for target in targets[1:] if target.role == primary.role]
    else:
        extras = targets[1:]
    return [primary, *(extras[:1])]


def wizard_turn(state: FightState, actor: Combatant) -> None:
    targets = wizard_spell_targets(state)
    if not targets:
        return
    if state.wizard_slots > 0:
        raw = roll(state.rng, 4, 6)
        for target in list(targets):
            damage = basic_save_damage(state.rng, target, "reflex", 21, raw)
            damage_enemy(state, target, damage, actor, ranged=True)
        state.wizard_slots -= 1
    else:
        target = targets[0]
        raw = roll(state.rng, 3, 4, 4)
        damage = basic_save_damage(state.rng, target, "reflex", 21, raw)
        damage_enemy(state, target, damage, actor, ranged=True)


def recovery_check(state: FightState, actor: Combatant) -> None:
    if actor.hp > 0 or actor.dead:
        return
    result = degree(d20(state.rng), 0, 10 + actor.dying)
    if result == 3:
        actor.dying = max(0, actor.dying - 2)
    elif result == 2:
        actor.dying = max(0, actor.dying - 1)
    elif result == 1:
        actor.dying += 1
    else:
        actor.dying += 2
    if actor.dying >= 4:
        actor.dead = True


def persistent_damage(state: FightState, actor: Combatant) -> None:
    if not actor.standing or actor.persistent_fire <= 0:
        return
    actor.take_damage(roll(state.rng, actor.persistent_fire, 6))
    if d20(state.rng) >= 15:
        actor.persistent_fire = 0


def party_turn(state: FightState, actor: Combatant) -> None:
    if not actor.standing:
        recovery_check(state, actor)
        return
    if actor.name == "Fighter":
        fighter_turn(state, actor)
    elif actor.name == "Rogue":
        rogue_turn(state, actor)
    elif actor.name == "Cleric":
        cleric_turn(state, actor)
    else:
        wizard_turn(state, actor)
    persistent_damage(state, actor)


def apply_area(
    state: FightState,
    targets: Iterable[Combatant],
    dice: tuple[int, int],
    dc: int,
    damage_adjustment: int = 0,
) -> None:
    raw = max(1, roll(state.rng, dice[0], dice[1]) + damage_adjustment)
    for target in list(targets):
        if target.standing:
            target.take_damage(
                basic_save_damage(state.rng, target, "reflex", dc, raw)
            )


def choose_area_targets(state: FightState, count: int) -> list[Combatant]:
    pool = state.standing_party()
    chosen: list[Combatant] = []
    while pool and len(chosen) < count:
        target = weighted_choice(
            state.rng, pool, lambda actor: 1.4 if actor.role == "front" else 1.0
        )
        chosen.append(target)
        pool.remove(target)
    return chosen


def wang_turn(state: FightState, actor: Combatant) -> None:
    party = state.standing_party()
    if not party:
        return
    weak = state.scenario.weak_wang
    overclock_frequency = 0.88 if weak else 0.90
    use_overclock = len(party) >= 2 and (
        state.round_number == 1 or state.rng.random() < overclock_frequency
    )
    if use_overclock:
        apply_area(
            state,
            choose_area_targets(state, 2),
            (4, 6),
            22 if weak else 24,
            -2 if weak else 0,
        )
        target = choose_enemy_target(state, "wang")
        if target is not None:
            damage, _ = strike_damage(
                state.rng,
                15 if weak else 17,
                target,
                (2, 8),
                3 if weak else 5,
            )
            target.take_damage(damage)
        return

    target = choose_enemy_target(state, "wang")
    if target is None:
        return
    damage, hit = strike_damage(
        state.rng,
        15 if weak else 17,
        target,
        (2, 8),
        3 if weak else 5,
    )
    target.take_damage(damage)
    core_chance = 0.20
    if hit and target.standing and state.rng.random() < core_chance:
        target.take_damage(roll(state.rng, 2, 6))
        target.persistent_fire = 1
    target = choose_enemy_target(state, "wang")
    if target is not None:
        damage, _ = strike_damage(
            state.rng,
            10 if weak else 12,
            target,
            (2, 8),
            3 if weak else 5,
        )
        target.take_damage(damage)


def mephit_turn(state: FightState, actor: Combatant) -> None:
    if actor.breath_cooldown == 0 and len(state.standing_party()) >= 2:
        # The flying mephit attacks the most useful plausible cluster rather
        # than defaulting to the front line.
        targets = state.standing_party()
        state.rng.shuffle(targets)
        apply_area(state, targets[:2], (3, 6), 19)
        actor.breath_cooldown = state.rng.randint(1, 4)
        target = choose_enemy_target(state, "mephit")
        if target is not None:
            damage, _ = strike_damage(state.rng, 11, target, (1, 6), 1)
            target.take_damage(damage + (roll(state.rng, 1, 6) if damage else 0))
        return

    if actor.breath_cooldown > 0:
        actor.breath_cooldown -= 1
    for attack_bonus in (11, 7, 3):
        target = choose_enemy_target(state, "mephit")
        if target is None:
            return
        damage, _ = strike_damage(state.rng, attack_bonus, target, (1, 6), 1)
        target.take_damage(damage + (roll(state.rng, 1, 6) if damage else 0))


def apprentice_turn(state: FightState, actor: Combatant) -> None:
    # Panic Valve competes with movement and self-preservation. The timing fix
    # proposed in the report is represented as an occasional extra eruption on
    # the following round rather than a guaranteed second vent every round.
    if state.scenario.automatic_vents and state.rng.random() < 0.22:
        state.panic_round = state.round_number + 1
    if state.rng.random() < 0.35:
        target = choose_enemy_target(state, "apprentice")
        if target is not None:
            damage, _ = strike_damage(state.rng, 6, target, (1, 6), 0)
            target.take_damage(damage)


def enemy_turn(state: FightState, actor: Combatant) -> None:
    if not actor.standing:
        return
    if actor.role == "wang":
        wang_turn(state, actor)
    elif actor.role == "mephit":
        mephit_turn(state, actor)
    else:
        apprentice_turn(state, actor)


def vent_routine(state: FightState) -> None:
    if not state.scenario.automatic_vents:
        return
    eruptions = 2 if state.panic_round == state.round_number else 1
    if state.panic_round <= state.round_number:
        state.panic_round = 0
    for _ in range(eruptions):
        party = state.standing_party()
        if not party or state.rng.random() > 0.58:
            continue
        target = weighted_choice(
            state.rng, party, lambda actor: 1.75 if actor.role == "front" else 0.75
        )
        raw = roll(state.rng, 2, 6)
        target.take_damage(
            basic_save_damage(state.rng, target, "reflex", 19, raw)
        )


def initiative_order(state: FightState) -> list[tuple[int, Combatant | None]]:
    entries: list[tuple[int, Combatant | None]] = []
    for actor in [*state.party, *state.enemies]:
        entries.append((d20(state.rng) + actor.initiative, actor))
    if state.scenario.automatic_vents:
        entries.append((20, None))
    state.rng.shuffle(entries)
    return sorted(entries, key=lambda entry: entry[0], reverse=True)


def fight(seed: int, scenario: Scenario, tactic: Tactic) -> Outcome:
    state = FightState(
        rng=random.Random(seed),
        scenario=scenario,
        tactic=tactic,
        party=make_party(),
        enemies=make_enemies(scenario),
    )
    order = initiative_order(state)

    for round_number in range(1, 16):
        state.round_number = round_number
        state.wang_reaction_used = False
        for _, actor in order:
            if not state.active_enemies():
                return "strained" if any(pc.dropped_once for pc in state.party) else "clean"
            if not state.standing_party():
                return "defeat"
            if actor is None:
                vent_routine(state)
            elif actor.side == "party":
                party_turn(state, actor)
            else:
                enemy_turn(state, actor)

    # A 15-round stalemate is treated conservatively as a defeat.
    return "defeat"


def initiative_estimate(trials: int, seed: int) -> dict[str, float]:
    rng = random.Random(seed)
    party_modifiers = [10, 11, 9, 8]
    before_three = 0
    before_all = 0
    for _ in range(trials):
        wang = d20(rng) + 14
        party = [d20(rng) + modifier for modifier in party_modifiers]
        beaten = sum(wang > score for score in party)
        before_three += beaten >= 3
        before_all += beaten == 4
    return {
        "before_at_least_three_pct": round(100 * before_three / trials, 2),
        "before_all_four_pct": round(100 * before_all / trials, 2),
    }


def run_suite(trials: int, seed: int) -> dict:
    results: list[dict] = []
    seed_source = random.Random(seed)
    for scenario in SCENARIOS:
        for tactic in ("focused", "boss-first"):
            counts = {"clean": 0, "strained": 0, "defeat": 0}
            for _ in range(trials):
                outcome = fight(seed_source.getrandbits(64), scenario, tactic)
                counts[outcome] += 1
            results.append(
                {
                    "scenario": asdict(scenario),
                    "tactic": tactic,
                    "trials": trials,
                    "counts": counts,
                    "percent": {
                        key: round(100 * value / trials, 2)
                        for key, value in counts.items()
                    },
                }
            )
    return {
        "model": "engine-room-heist-mc-v1",
        "seed": seed,
        "trials_per_row": trials,
        "initiative": initiative_estimate(trials, seed ^ 0xA11CE),
        "results": results,
    }


def print_table(report: dict) -> None:
    print(
        f"Model: {report['model']} | seed={report['seed']} | "
        f"trials/row={report['trials_per_row']:,}"
    )
    initiative = report["initiative"]
    print(
        "Wang initiative: "
        f"before >=3 PCs {initiative['before_at_least_three_pct']:.2f}% | "
        f"before all 4 {initiative['before_all_four_pct']:.2f}%\n"
    )
    print(f"{'Scenario':48} {'Tactic':10} {'Clean':>8} {'Strained':>10} {'Defeat':>8}")
    print("-" * 90)
    for row in report["results"]:
        pct = row["percent"]
        print(
            f"{row['scenario']['label'][:48]:48} {row['tactic']:10} "
            f"{pct['clean']:7.2f}% {pct['strained']:9.2f}% {pct['defeat']:7.2f}%"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=20_000, help="fights per row")
    parser.add_argument("--seed", type=int, default=20_260_713, help="master RNG seed")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args()
    if args.trials < 1:
        parser.error("--trials must be positive")
    return args


def main() -> None:
    args = parse_args()
    report = run_suite(args.trials, args.seed)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_table(report)


if __name__ == "__main__":
    main()
