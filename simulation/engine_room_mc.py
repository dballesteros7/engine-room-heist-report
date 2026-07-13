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
from typing import Callable, Iterable, Literal, Sequence


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
    wounded: int = 0
    dead: bool = False
    persistent_fire: int = 0
    breath_cooldown: int = 0

    def __post_init__(self) -> None:
        self.hp = self.max_hp

    @property
    def standing(self) -> bool:
        return self.hp > 0 and not self.dead

    def take_damage(self, amount: int, critical_drop: bool = False) -> bool:
        """Apply damage and return True only when this damage drops the target."""
        if amount <= 0 or self.dead:
            return False
        if self.hp <= 0:
            if self.side == "party":
                if self.dying > 0:
                    self.dying += 2 if critical_drop else 1
                else:
                    self.dying = (2 if critical_drop else 1) + self.wounded
                if self.dying >= 4:
                    self.dead = True
            return False
        self.hp = max(0, self.hp - amount)
        if self.hp == 0:
            self.dropped_once = True
            if self.side == "party":
                dying_value = (2 if critical_drop else 1) + self.wounded
                self.dying = max(dying_value, self.dying)
                if self.dying >= 4:
                    self.dead = True
            else:
                self.dead = True
            return True
        return False

    def heal(self, amount: int) -> None:
        if self.dead or amount <= 0:
            return
        was_dying = self.dying > 0
        self.hp = min(self.max_hp, self.hp + amount)
        if was_dying and self.hp > 0:
            self.dying = 0
            self.wounded += 1


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
    trace: list[str] | None = None

    def log(self, message: str = "") -> None:
        if self.trace is not None:
            self.trace.append(message)

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


DEGREE_NAMES = ("critical failure", "failure", "success", "critical success")


def strike_damage(
    rng: random.Random,
    attack_bonus: int,
    target: Combatant,
    dice: tuple[int, int],
    flat: int,
    log: Callable[[str], None] | None = None,
    label: str = "Strike",
    extra_dice: tuple[int, int] | None = None,
    extra_label: str = "extra damage",
) -> tuple[int, bool, bool]:
    check = d20(rng)
    result = degree(check, attack_bonus, target.ac)
    total = check + attack_bonus
    if result < 2:
        if log is not None:
            log(
                f"  {label}: d20 {check} + {attack_bonus} = {total} vs "
                f"AC {target.ac} -> {DEGREE_NAMES[result]}; 0 damage"
            )
        return 0, False, False
    base_damage = roll(rng, dice[0], dice[1], flat)
    extra_damage = (
        roll(rng, extra_dice[0], extra_dice[1]) if extra_dice is not None else 0
    )
    raw_damage = base_damage + extra_damage
    damage = raw_damage
    if result == 3:
        damage *= 2
    if log is not None:
        base_formula = f"{dice[0]}d{dice[1]}" + (f"+{flat}" if flat else "")
        formula = f"{base_formula} = {base_damage}"
        if extra_dice is not None:
            formula += (
                f" + {extra_dice[0]}d{extra_dice[1]} {extra_label} = "
                f"{extra_damage}; raw total {raw_damage}"
            )
        log(
            f"  {label}: d20 {check} + {attack_bonus} = {total} vs "
            f"AC {target.ac} -> {DEGREE_NAMES[result]}; "
            f"{formula}; {damage} damage after degree"
        )
    return damage, True, result == 3


def basic_save_damage(
    rng: random.Random,
    target: Combatant,
    save_name: Literal["fort", "reflex", "will"],
    dc: int,
    raw_damage: int,
    log: Callable[[str], None] | None = None,
    label: str = "basic save",
) -> tuple[int, bool]:
    check = d20(rng)
    modifier = getattr(target, save_name)
    result = degree(check, modifier, dc)
    if result == 0:
        damage = raw_damage * 2
    elif result == 1:
        damage = raw_damage
    elif result == 2:
        damage = raw_damage // 2
    else:
        damage = 0
    if log is not None:
        log(
            f"  {target.name} {save_name.title()} {label}: d20 {check} + "
            f"{modifier} = {check + modifier} vs DC {dc} -> "
            f"{DEGREE_NAMES[result]}; {raw_damage} raw -> {damage} damage"
        )
    return damage, result == 0


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
        state.log(f"  Explosive Demise catches {target.name}: 2d6 = {raw}")
        damage, critical_failure = basic_save_damage(
            state.rng,
            target,
            "reflex",
            19,
            raw,
            log=state.log,
            label="save vs Explosive Demise",
        )
        apply_damage(
            state,
            target,
            damage,
            "Explosive Demise",
            critical_drop=critical_failure,
        )
    else:
        state.log("  Explosive Demise: no PC is adjacent")


def apply_damage(
    state: FightState,
    target: Combatant,
    amount: int,
    source_label: str,
    source: Combatant | None = None,
    ranged: bool = True,
    critical_drop: bool = False,
) -> bool:
    if amount <= 0:
        return False
    before = target.hp
    dying_before = target.dying
    dropped = target.take_damage(amount, critical_drop=critical_drop)
    state.log(
        f"  {source_label} -> {target.name}: {amount} damage; "
        f"HP {before}/{target.max_hp} -> {target.hp}/{target.max_hp}"
        + (
            f"; DROPPED (dying {target.dying}, wounded {target.wounded})"
            if dropped and target.side == "party"
            else "; DROPPED"
            if dropped
            else ""
        )
        + (
            f"; dying {dying_before} -> {target.dying}"
            if target.side == "party" and before == 0
            else ""
        )
    )
    if dropped and target.role == "mephit":
        mephit_death_burst(state, source, ranged)
    return dropped


def damage_enemy(
    state: FightState,
    target: Combatant,
    amount: int,
    source: Combatant | None,
    ranged: bool,
) -> None:
    apply_damage(
        state,
        target,
        amount,
        source.name if source is not None else "damage",
        source=source,
        ranged=ranged,
    )


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
    if wang is None or not wang.standing or state.wang_reaction_used or not mover.standing:
        return
    opportunity_roll = state.rng.random()
    state.log(
        f"  Reactive Strike opportunity for {mover.name}: positioning roll "
        f"{opportunity_roll:.3f} < {reaction_chance:.2f} -> "
        f"{'triggers' if opportunity_roll < reaction_chance else 'no trigger'}"
    )
    if opportunity_roll >= reaction_chance:
        return
    state.wang_reaction_used = True
    weak = state.scenario.weak_wang
    damage, _, critical = strike_damage(
        state.rng,
        15 if weak else 17,
        mover,
        (2, 8),
        3 if weak else 5,
        log=state.log,
        label="Wang Reactive Strike",
    )
    apply_damage(
        state,
        mover,
        damage,
        "Wang Reactive Strike",
        critical_drop=critical,
    )


def fighter_turn(state: FightState, actor: Combatant) -> None:
    for attack_index in range(2):
        targets = ordered_enemy_targets(state)
        if not targets or not actor.standing:
            return
        target = targets[0]
        state.log(f"  Fighter target: {target.name}")
        if target.role == "wang" and attack_index == 0:
            wang_reactive_strike(state, actor)
            if not actor.standing:
                return
        damage, _, _ = strike_damage(
            state.rng,
            14 - 5 * attack_index,
            target,
            (2, 8),
            5,
            log=state.log,
            label=f"Fighter Strike {attack_index + 1}",
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
        off_guard_chance = 0.72 if attack_index == 0 else 0.52
        off_guard_roll = state.rng.random()
        off_guard = off_guard_roll < off_guard_chance
        state.log(
            f"  Rogue target: {target.name}; off-guard positioning roll "
            f"{off_guard_roll:.3f} < {off_guard_chance:.2f} -> "
            f"{'yes (-2 AC)' if off_guard else 'no'}"
        )
        original_ac = target.ac
        if off_guard:
            target.ac -= 2
        damage, _, _ = strike_damage(
            state.rng,
            13 - 4 * attack_index,
            target,
            (2, 6),
            4,
            log=state.log,
            label=f"Rogue Strike {attack_index + 1}",
            extra_dice=(2, 6) if off_guard else None,
            extra_label="Sneak Attack",
        )
        target.ac = original_ac
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
            healing = roll(state.rng, 2, 8, 16)
            before = target.hp
            target.heal(healing)
            state.heal_slots -= 1
            state.log(
                f"  Cleric casts rank-2 Heal on {target.name}: 2d8+16 = {healing}; "
                f"HP {before}/{target.max_hp} -> {target.hp}/{target.max_hp}; "
                f"wounded {target.wounded}; {state.heal_slots} slots remain"
            )
            return
    targets = ordered_enemy_targets(state)
    if not targets:
        return
    target = targets[0]
    damage, _, _ = strike_damage(
        state.rng,
        12,
        target,
        (3, 4),
        4,
        log=state.log,
        label="Cleric spell attack",
    )
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
        state.log(
            f"  Wizard rank-2 spell targets {', '.join(target.name for target in targets)}: "
            f"4d6 = {raw}; {state.wizard_slots - 1} slots remain"
        )
        for target in list(targets):
            damage, _ = basic_save_damage(
                state.rng,
                target,
                "reflex",
                21,
                raw,
                log=state.log,
                label="save vs wizard spell",
            )
            damage_enemy(state, target, damage, actor, ranged=True)
        state.wizard_slots -= 1
    else:
        target = targets[0]
        raw = roll(state.rng, 3, 4, 4)
        state.log(f"  Wizard cantrip targets {target.name}: 3d4+4 = {raw}")
        damage, _ = basic_save_damage(
            state.rng,
            target,
            "reflex",
            21,
            raw,
            log=state.log,
            label="save vs wizard cantrip",
        )
        damage_enemy(state, target, damage, actor, ranged=True)


def recovery_check(state: FightState, actor: Combatant) -> None:
    if actor.hp > 0 or actor.dead:
        return
    check = d20(state.rng)
    dc = 10 + actor.dying
    result = degree(check, 0, dc)
    before = actor.dying
    if result == 3:
        actor.dying = max(0, actor.dying - 2)
    elif result == 2:
        actor.dying = max(0, actor.dying - 1)
    elif result == 1:
        actor.dying += 1
    else:
        actor.dying += 2
    if before > 0 and actor.dying == 0:
        actor.wounded += 1
    if actor.dying >= 4:
        actor.dead = True
    state.log(
        f"  {actor.name} recovery: d20 {check} vs DC {dc} -> "
        f"{DEGREE_NAMES[result]}; dying {before} -> {actor.dying}; "
        f"wounded {actor.wounded}"
        + ("; DEAD" if actor.dead else "")
    )


def persistent_damage(state: FightState, actor: Combatant) -> None:
    if actor.dead or actor.persistent_fire <= 0:
        return
    damage = roll(state.rng, actor.persistent_fire, 6)
    apply_damage(state, actor, damage, "persistent fire")
    flat_check = d20(state.rng)
    state.log(
        f"  {actor.name} persistent recovery flat check: d20 {flat_check} vs DC 15 -> "
        f"{'ends' if flat_check >= 15 else 'continues'}"
    )
    if flat_check >= 15:
        actor.persistent_fire = 0


def party_turn(state: FightState, actor: Combatant) -> None:
    state.log(f"-- {actor.name} acts --")
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
    label: str = "area effect",
) -> None:
    targets = list(targets)
    raw = max(1, roll(state.rng, dice[0], dice[1]) + damage_adjustment)
    adjustment = f"{damage_adjustment:+d}" if damage_adjustment else ""
    state.log(
        f"  {label} targets {', '.join(target.name for target in targets)}: "
        f"{dice[0]}d{dice[1]}{adjustment} = {raw} raw damage"
    )
    for target in targets:
        if target.standing:
            damage, critical_failure = basic_save_damage(
                state.rng,
                target,
                "reflex",
                dc,
                raw,
                log=state.log,
                label=f"save vs {label}",
            )
            apply_damage(
                state,
                target,
                damage,
                label,
                critical_drop=critical_failure,
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
    if len(party) < 2:
        use_overclock = False
        state.log("  Wang cannot catch two standing PCs with Overclock Boiler")
    elif state.round_number == 1:
        use_overclock = True
        state.log("  Wang uses the modeled round-1 Overclock Boiler priority")
    else:
        overclock_roll = state.rng.random()
        use_overclock = overclock_roll < overclock_frequency
        state.log(
            f"  Wang Overclock choice: tactical roll {overclock_roll:.3f} < "
            f"{overclock_frequency:.2f} -> {'use it' if use_overclock else 'use Strikes'}"
        )
    if use_overclock:
        apply_area(
            state,
            choose_area_targets(state, 2),
            (4, 6),
            22 if weak else 24,
            -2 if weak else 0,
            label="Wang Overclock Boiler",
        )
        target = choose_enemy_target(state, "wang")
        if target is not None:
            damage, _, critical = strike_damage(
                state.rng,
                15 if weak else 17,
                target,
                (2, 8),
                3 if weak else 5,
                log=state.log,
                label="Wang wrench Strike",
            )
            apply_damage(
                state,
                target,
                damage,
                "Wang wrench Strike",
                critical_drop=critical,
            )
        return

    target = choose_enemy_target(state, "wang")
    if target is None:
        return
    damage, hit, critical = strike_damage(
        state.rng,
        15 if weak else 17,
        target,
        (2, 8),
        3 if weak else 5,
        log=state.log,
        label="Wang Brutal Shove",
    )
    apply_damage(
        state,
        target,
        damage,
        "Wang Brutal Shove",
        critical_drop=critical,
    )
    core_chance = 0.20
    if hit and target.standing:
        core_roll = state.rng.random()
        state.log(
            f"  Core alignment after shove: positioning roll {core_roll:.3f} < "
            f"{core_chance:.2f} -> {'into core' if core_roll < core_chance else 'safe square'}"
        )
        if core_roll < core_chance:
            core_damage = roll(state.rng, 2, 6)
            apply_damage(state, target, core_damage, "engine core")
            target.persistent_fire = 1
            state.log(f"  {target.name} gains 1d6 persistent fire")
    target = choose_enemy_target(state, "wang")
    if target is not None:
        damage, _, critical = strike_damage(
            state.rng,
            10 if weak else 12,
            target,
            (2, 8),
            3 if weak else 5,
            log=state.log,
            label="Wang second wrench Strike (MAP -5)",
        )
        apply_damage(
            state,
            target,
            damage,
            "Wang second wrench Strike",
            critical_drop=critical,
        )


def mephit_turn(state: FightState, actor: Combatant) -> None:
    if actor.breath_cooldown == 0 and len(state.standing_party()) >= 2:
        # The flying mephit attacks the most useful plausible cluster rather
        # than defaulting to the front line.
        targets = state.standing_party()
        state.rng.shuffle(targets)
        apply_area(
            state,
            targets[:2],
            (3, 6),
            19,
            label=f"{actor.name} Breath Weapon",
        )
        actor.breath_cooldown = state.rng.randint(1, 4)
        state.log(
            f"  {actor.name} Breath Weapon cooldown: {actor.breath_cooldown} rounds"
        )
        target = choose_enemy_target(state, "mephit")
        if target is not None:
            damage, _, critical = strike_damage(
                state.rng,
                11,
                target,
                (1, 6),
                1,
                log=state.log,
                label=f"{actor.name} claw",
                extra_dice=(1, 6),
                extra_label="fire",
            )
            apply_damage(
                state,
                target,
                damage,
                f"{actor.name} claw",
                critical_drop=critical,
            )
        return

    if actor.breath_cooldown > 0:
        before = actor.breath_cooldown
        actor.breath_cooldown -= 1
        state.log(
            f"  {actor.name} Breath Weapon cooldown: {before} -> "
            f"{actor.breath_cooldown}; uses claws"
        )
    for attack_index, attack_bonus in enumerate((11, 7, 3), start=1):
        target = choose_enemy_target(state, "mephit")
        if target is None:
            return
        damage, _, critical = strike_damage(
            state.rng,
            attack_bonus,
            target,
            (1, 6),
            1,
            log=state.log,
            label=f"{actor.name} claw {attack_index}",
            extra_dice=(1, 6),
            extra_label="fire",
        )
        apply_damage(
            state,
            target,
            damage,
            f"{actor.name} claw",
            critical_drop=critical,
        )


def apprentice_turn(state: FightState, actor: Combatant) -> None:
    # Panic Valve competes with movement and self-preservation. The timing fix
    # proposed in the report is represented as an occasional extra eruption on
    # the following round rather than a guaranteed second vent every round.
    if state.scenario.automatic_vents:
        panic_roll = state.rng.random()
        state.log(
            f"  Panic Valve choice: tactical roll {panic_roll:.3f} < 0.22 -> "
            f"{'sets next round' if panic_roll < 0.22 else 'not used'}"
        )
        if panic_roll < 0.22:
            state.panic_round = state.round_number + 1
    hammer_roll = state.rng.random()
    state.log(
        f"  Apprentice hammer opportunity: positioning roll {hammer_roll:.3f} < "
        f"0.35 -> {'attack' if hammer_roll < 0.35 else 'no target'}"
    )
    if hammer_roll < 0.35:
        target = choose_enemy_target(state, "apprentice")
        if target is not None:
            damage, _, critical = strike_damage(
                state.rng,
                6,
                target,
                (1, 6),
                0,
                log=state.log,
                label="Apprentice light hammer",
            )
            apply_damage(
                state,
                target,
                damage,
                "Apprentice light hammer",
                critical_drop=critical,
            )


def enemy_turn(state: FightState, actor: Combatant) -> None:
    if not actor.standing:
        return
    state.log(f"-- {actor.name} acts --")
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
    state.log(f"-- Steam Vents routine: {eruptions} eruption(s) --")
    for eruption in range(1, eruptions + 1):
        party = state.standing_party()
        if not party:
            return
        exposure_roll = state.rng.random()
        state.log(
            f"  Eruption {eruption} exposure roll {exposure_roll:.3f} <= 0.58 -> "
            f"{'PC exposed' if exposure_roll <= 0.58 else 'empty vent area'}"
        )
        if exposure_roll > 0.58:
            continue
        target = weighted_choice(
            state.rng, party, lambda actor: 1.75 if actor.role == "front" else 0.75
        )
        raw = roll(state.rng, 2, 6)
        state.log(f"  Steam Vent catches {target.name}: 2d6 = {raw}")
        damage, critical_failure = basic_save_damage(
            state.rng,
            target,
            "reflex",
            19,
            raw,
            log=state.log,
            label="save vs Steam Vent",
        )
        apply_damage(
            state,
            target,
            damage,
            "Steam Vent",
            critical_drop=critical_failure,
        )


def initiative_order(state: FightState) -> list[tuple[int, Combatant | None]]:
    entries: list[tuple[int, Combatant | None]] = []
    for actor in [*state.party, *state.enemies]:
        check = d20(state.rng)
        total = check + actor.initiative
        state.log(
            f"Initiative {actor.name}: d20 {check} + {actor.initiative} = {total}"
        )
        entries.append((total, actor))
    if state.scenario.automatic_vents:
        entries.append((20, None))
        state.log("Initiative Steam Vents: fixed 20")
    state.rng.shuffle(entries)
    order = sorted(entries, key=lambda entry: entry[0], reverse=True)
    state.log(
        "Initiative order: "
        + " > ".join(
            f"{actor.name if actor is not None else 'Steam Vents'} ({score})"
            for score, actor in order
        )
    )
    return order


def fight(
    seed: int,
    scenario: Scenario,
    tactic: Tactic,
    trace: list[str] | None = None,
) -> Outcome:
    state = FightState(
        rng=random.Random(seed),
        scenario=scenario,
        tactic=tactic,
        party=make_party(),
        enemies=make_enemies(scenario),
        trace=trace,
    )
    state.log("ENGINE ROOM HEIST — SINGLE-FIGHT TRACE")
    state.log(f"Seed: {seed}")
    state.log(f"Scenario: {scenario.label}")
    state.log(f"Party tactic: {tactic}")
    state.log(
        "Party: "
        + ", ".join(f"{actor.name} HP {actor.hp} AC {actor.ac}" for actor in state.party)
    )
    state.log(
        "Enemies: "
        + ", ".join(
            f"{actor.name} HP {actor.hp} AC {actor.ac}" for actor in state.enemies
        )
    )
    state.log("")
    order = initiative_order(state)

    def finish(outcome: Outcome, reason: str) -> Outcome:
        state.log("")
        state.log(f"RESULT: {outcome.upper()} — {reason}")
        state.log(
            "Party final: "
            + ", ".join(
                f"{actor.name} {actor.hp}/{actor.max_hp} HP"
                + (" dead" if actor.dead else "")
                + (" dropped" if actor.dropped_once else "")
                + (f" wounded {actor.wounded}" if actor.wounded else "")
                for actor in state.party
            )
        )
        state.log(
            "Enemy final: "
            + ", ".join(
                f"{actor.name} {actor.hp}/{actor.max_hp} HP" for actor in state.enemies
            )
        )
        return outcome

    for round_number in range(1, 16):
        state.round_number = round_number
        state.wang_reaction_used = False
        state.log("")
        state.log(f"=== ROUND {round_number} ===")
        for _, actor in order:
            if not state.active_enemies():
                if any(pc.dropped_once for pc in state.party):
                    return finish("strained", "all enemies defeated after at least one PC dropped")
                return finish("clean", "all enemies defeated without a PC dropping")
            if not state.standing_party():
                return finish("defeat", "all four PCs are unable to continue")
            if actor is None:
                vent_routine(state)
            elif actor.side == "party":
                party_turn(state, actor)
            else:
                enemy_turn(state, actor)

    # A 15-round stalemate is treated conservatively as a defeat.
    return finish("defeat", "15-round limit reached")


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
        "model": "engine-room-heist-mc-v1.1",
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
    parser.add_argument(
        "--trace",
        action="store_true",
        help="print a detailed log for one fight instead of running the suite",
    )
    parser.add_argument(
        "--scenario",
        choices=[scenario.slug for scenario in SCENARIOS],
        default="as-written",
        help="scenario used with --trace",
    )
    parser.add_argument(
        "--tactic",
        choices=("focused", "boss-first"),
        default="focused",
        help="party target priority used with --trace",
    )
    args = parser.parse_args()
    if args.trials < 1:
        parser.error("--trials must be positive")
    return args


def main() -> None:
    args = parse_args()
    if args.trace:
        scenario = next(item for item in SCENARIOS if item.slug == args.scenario)
        trace: list[str] = []
        fight(args.seed, scenario, args.tactic, trace=trace)
        print("\n".join(trace))
        return
    report = run_suite(args.trials, args.seed)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_table(report)


if __name__ == "__main__":
    main()
