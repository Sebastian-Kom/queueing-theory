"""Checks of the queue's accounting and its analytical interpretation."""
import importlib.util
import itertools
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

path = Path(__file__).resolve().parents[1] / "scripts" / "01_balanced_capacity.py"
spec = importlib.util.spec_from_file_location("balanced_capacity", path)
model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model)


class BalancedCapacityTests(unittest.TestCase):
    def test_unused_capacity_cannot_be_carried_forward(self):
        result = model.simulate_queue(np.array([8, 12]), 10)
        np.testing.assert_array_equal(result["backlog"], [0, 0, 2])
        np.testing.assert_array_equal(result["completed"], [8, 10])
        np.testing.assert_array_equal(result["unused_capacity"], [2, 0])

    def test_queue_can_clear_again(self):
        result = model.simulate_queue(np.array([12, 8]), 10)
        np.testing.assert_array_equal(result["backlog"], [0, 2, 0])

    def test_no_features_are_lost_or_created(self):
        arrivals = np.array([0, 20, 15, 0, 2, 13, 10, 14])
        result = model.simulate_queue(arrivals, 10)
        np.testing.assert_array_equal(
            np.cumsum(arrivals) - np.cumsum(result["completed"]),
            result["backlog"][1:],
        )
        np.testing.assert_array_equal(result["completed"] + result["unused_capacity"], 10)
        self.assertTrue(np.all(result["backlog"] >= 0))

    def test_exact_expectation_matches_exhaustive_sequences(self):
        for days in range(1, 9):
            final = [model.simulate_queue(np.array(sequence), 10)["backlog"][-1]
                     for sequence in itertools.product([8, 12], repeat=days)]
            exact, empty = model.exact_expectation(days, 2)
            self.assertAlmostEqual(exact[-1], np.mean(final))
            self.assertAlmostEqual(empty[-1], np.mean(np.array(final) == 0))

    def test_zero_variability_control_has_no_backlog(self):
        result = model.run_experiment(days=50, runs=100, variation=0)
        np.testing.assert_array_equal(result["mean"], 0)
        np.testing.assert_array_equal(result["exact_mean"], 0)
        np.testing.assert_array_equal(result["exact_empty_probability"], 1)

    def test_monte_carlo_agrees_with_exact_mean(self):
        result = model.run_experiment(days=100, runs=20000, seed=101)
        self.assertLess(abs(result["mean"][-1] - result["exact_mean"][-1]),
                        5 * result["sem"][-1])

    def test_sample_is_independent_of_ensemble_size(self):
        small = model.run_experiment(days=20, runs=10, seed=7)
        large = model.run_experiment(days=20, runs=100, seed=7)
        np.testing.assert_array_equal(small["sample_arrivals"], large["sample_arrivals"])

    def test_cost_moves_from_waiting_to_tested_without_being_charged_again(self):
        arrivals = np.array([12, 10, 8])
        sample = model.simulate_queue(arrivals, 10)
        costs = model.feature_costs(arrivals, sample, model.cost_in_cents("1234.56"))
        # Two waiting features cost the same after another day of waiting.
        np.testing.assert_array_equal(costs["bound_cost_cents"], [0, 246912, 246912, 0])
        np.testing.assert_array_equal(
            costs["bound_cost_cents"] + costs["ready_cost_cents"],
            costs["total_development_cost_cents"],
        )

    def test_cost_changes_money_without_changing_the_queue(self):
        first = model.run_experiment(days=20, runs=10, feature_cost="12.34")
        second = model.run_experiment(days=20, runs=10, feature_cost="24.68")
        np.testing.assert_array_equal(first["sample"]["backlog"], second["sample"]["backlog"])
        np.testing.assert_array_equal(first["sample"]["bound_cost_cents"] * 2,
                                      second["sample"]["bound_cost_cents"])

    def test_cost_input_is_exact_and_validated(self):
        self.assertEqual(model.cost_in_cents("0.29"), 29)
        self.assertEqual(model.cost_in_cents("0"), 0)
        for value in ("-1", "NaN", "Infinity", "1.001", "not money"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                model.cost_in_cents(value)

    def test_player_embeds_the_same_path_and_exact_costs_as_python(self):
        result = model.run_experiment(days=20, runs=10, feature_cost="999.99")
        with tempfile.TemporaryDirectory() as tmp:
            html = model.write_live_view(result, Path(tmp)).read_text()
        encoded = html.split('data-role="model-data">', 1)[1].split('</script>', 1)[0]
        data = json.loads(encoded)
        self.assertEqual(data["backlog"], result["sample"]["backlog"].tolist())
        self.assertEqual(data["bound_cost_cents"], result["sample"]["bound_cost_cents"].tolist())
        self.assertNotIn("__QUEUE_DATA__", html)


if __name__ == "__main__":
    unittest.main()
