import unittest

from simulation.engine_room_mc import SCENARIOS, degree, fight, run_suite


class DegreeTests(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(degree(10, 10, 20), 2)
        self.assertEqual(degree(10, 20, 20), 3)
        self.assertEqual(degree(10, 0, 20), 0)

    def test_natural_adjustments(self):
        self.assertEqual(degree(20, 0, 30), 1)
        self.assertEqual(degree(1, 30, 20), 2)


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


if __name__ == "__main__":
    unittest.main()
