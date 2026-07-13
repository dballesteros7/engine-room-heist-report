# Monte Carlo model

This directory contains the reproducible model behind the published encounter report.

The initial web report was produced from an unpublished scratch simulation. This checked-in `v1` reconstruction makes the mechanics and positioning assumptions inspectable, uses a fixed seed, and is now the source of truth for the percentages on the webpage.

## Run it

Python 3.10 or newer is sufficient; there are no third-party dependencies.

```sh
python3 simulation/engine_room_mc.py
```

The publication run performs 20,000 fights for each of eight scenario/tactic rows: 160,000 fights in total.

```text
Model: engine-room-heist-mc-v1 | seed=20260713 | trials/row=20,000
Wang initiative: before >=3 PCs 59.54% | before all 4 39.20%

Scenario                                         Tactic        Clean   Strained   Defeat
As written                                       focused       7.17%     39.90%   52.94%
As written                                       boss-first    7.05%     26.54%   66.41%
Remove one mephit; retain the automatic vents    focused      19.09%     54.66%   26.25%
Remove one mephit; retain the automatic vents    boss-first   20.66%     47.16%   32.17%
One mephit; remove the automatic vents           focused      21.66%     57.63%   20.70%
One mephit; remove the automatic vents           boss-first   25.06%     49.91%   25.03%
Weak Wang; one mephit; retain the automatic vent focused      68.80%     30.36%    0.84%
Weak Wang; one mephit; retain the automatic vent boss-first   68.61%     29.86%    1.53%
```

For machine-readable output:

```sh
python3 simulation/engine_room_mc.py --json
```

For a quick development run:

```sh
python3 simulation/engine_room_mc.py --trials 1000 --seed 42
python3 -m unittest simulation/test_engine_room_mc.py
```

## What is modeled

- PF2e four-degree checks, including natural 1 and natural 20 adjustments
- initiative and fixed turn order
- multiple attack penalty
- a representative Fighter, Rogue, Cleric, and Wizard at level 4
- four two-action rank-2 heals and three rank-2 offensive spells
- unconscious, dying, recovery, and a “dropped at least once” flag
- Wang's Overclock Boiler, wrench attacks, Reactive Strike, and probabilistic core pushes
- mephit breath cooldowns, claws, and Explosive Demise
- automatic steam vents, weighted positioning exposure, and occasional corrected Panic Valve timing
- focused target selection versus attacking Wang first

## Important abstractions

This is not a complete virtual tabletop or a claim that every party plays identically. Movement, cover, exact squares, cone placement, off-guard uptime, target choice, Reactive Strike opportunities, and vent exposure are represented as explicit probabilities. Those choices are visible in the functions that use `random()` and `weighted_choice()`.

The model assumes a fully rested, competently played party with ordinary level-appropriate equipment. It does not optimize every feat, spell list, consumable, cold-weakness exploit, or cover interaction. Enemies choose useful targets but do not attack unconscious PCs.

The results are most useful comparatively: they show how much the second mephit, the arena, the weak adjustment, and target priority change the same representative fight.

## Editing scenarios

The scenario matrix is the `SCENARIOS` tuple near the top of `engine_room_mc.py`. Character statistics are in `make_party()` and `make_enemies()`. Tactical actions are split into named turn functions so that a changed assumption can be reviewed and tested independently.
