import unittest
from pathlib import Path

from simulation.engine_room_mc import (
    SCENARIOS,
    Combatant,
    degree,
    fight,
    run_suite,
    strike_damage,
)


class FixedRng:
    def __init__(self, values):
        self.values = iter(values)

    def randint(self, _low, _high):
        return next(self.values)


class DegreeTests(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(degree(10, 10, 20), 2)
        self.assertEqual(degree(10, 20, 20), 3)
        self.assertEqual(degree(10, 0, 20), 0)

    def test_natural_adjustments(self):
        self.assertEqual(degree(20, 0, 30), 1)
        self.assertEqual(degree(1, 30, 20), 2)

    def test_critical_strike_doubles_precision_damage(self):
        target = Combatant("Target", "enemy", 40, 19, 0, 0, 0, 0, "test")
        rng = FixedRng([20, 1, 1, 1, 1])
        damage, hit, critical = strike_damage(
            rng,
            13,
            target,
            (2, 6),
            4,
            extra_dice=(2, 6),
            extra_label="precision",
        )
        self.assertTrue(hit)
        self.assertTrue(critical)
        self.assertEqual(damage, 16)

    def test_wounded_increases_dying_on_repeat_drop(self):
        pc = Combatant("PC", "party", 10, 18, 0, 0, 0, 0, "test")
        pc.take_damage(10, critical_drop=True)
        self.assertEqual(pc.dying, 2)
        pc.heal(5)
        self.assertEqual(pc.wounded, 1)
        pc.take_damage(5)
        self.assertEqual(pc.dying, 2)
        pc.take_damage(1)
        self.assertEqual(pc.dying, 3)


class SimulationTests(unittest.TestCase):
    def test_fight_is_deterministic_for_a_seed(self):
        scenario = SCENARIOS[0]
        self.assertEqual(
            fight(123456, scenario, "focused"),
            fight(123456, scenario, "focused"),
        )

    def test_suite_counts_every_trial(self):
        report = run_suite(trials=20, seed=7)
        self.assertEqual(len(report["results"]), len(SCENARIOS) * 2)
        for row in report["results"]:
            self.assertEqual(sum(row["counts"].values()), 20)

    def test_trace_exposes_checks_damage_and_outcome(self):
        trace = []
        fight(20260713, SCENARIOS[0], "focused", trace=trace)
        text = "\n".join(trace)
        self.assertIn("Initiative order:", text)
        self.assertIn("vs DC", text)
        self.assertIn("HP 50/50 ->", text)
        self.assertIn("DROPPED (dying 2", text)
        self.assertIn("RESULT:", text)
        sample = Path(__file__).with_name("example-combat-log.txt").read_text().rstrip()
        self.assertEqual(text, sample)


if __name__ == "__main__":
    unittest.main()
