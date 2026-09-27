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

    def test_two_point_expectation_matches_exhaustive_sequences(self):
        for days in range(1, 9):
            final = [model.simulate_queue(np.array(sequence), 10)["backlog"][-1]
                     for sequence in itertools.product([8, 12], repeat=days)]
            exact, empty = model.exact_expectation(days, 2, "two-point")
            self.assertAlmostEqual(exact[-1], np.mean(final))
            self.assertAlmostEqual(empty[-1], np.mean(np.array(final) == 0))

    def test_uniform_expectation_matches_exhaustive_sequences(self):
        for days in range(1, 6):
            final = [model.simulate_queue(np.array(sequence), 10)["backlog"][-1]
                     for sequence in itertools.product(range(8, 13), repeat=days)]
            exact, empty = model.exact_expectation(days, 2, "uniform")
            self.assertAlmostEqual(exact[-1], np.mean(final))
            self.assertAlmostEqual(empty[-1], np.mean(np.array(final) == 0))

    def test_default_arrivals_include_all_five_counts(self):
        result = model.run_experiment(days=200, runs=10)
        self.assertEqual(set(result["sample_arrivals"]), {8, 9, 10, 11, 12})
        self.assertEqual(result["arrival_probabilities"], dict.fromkeys(range(8, 13), 0.2))

    def test_arrival_laws_keep_mean_capacity_but_have_different_variance(self):
        for distribution, variance in (("uniform", 2), ("two-point", 4)):
            law = model.arrival_probabilities(10, 2, distribution)
            self.assertAlmostEqual(sum(n * p for n, p in law.items()), 10)
            self.assertAlmostEqual(sum((n - 10)**2 * p for n, p in law.items()), variance)
        with self.assertRaises(ValueError):
            model.run_experiment(arrival_distribution="unknown")

    def test_original_two_point_path_is_still_reproducible(self):
        result = model.run_experiment(days=500, runs=10, arrival_distribution="two-point")
        self.assertEqual(result["sample"]["backlog"][-1], 50)
        self.assertAlmostEqual(result["exact_mean"][-1], 34.70031019890241)

    def test_zero_variability_control_has_no_backlog(self):
        result = model.run_experiment(days=50, runs=100, variation=0)
        np.testing.assert_array_equal(result["mean"], 0)
        np.testing.assert_array_equal(result["exact_mean"], 0)
        np.testing.assert_array_equal(result["exact_empty_probability"], 1)

    def test_monte_carlo_agrees_with_exact_mean(self):
        for distribution in ("uniform", "two-point"):
            with self.subTest(distribution=distribution):
                result = model.run_experiment(days=100, runs=20000, seed=101,
                                              arrival_distribution=distribution)
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

    def test_live_phases_preserve_fifo_feature_identity_and_cost(self):
        result = model.run_experiment(days=30, runs=10, feature_cost="999.99")
        payload = model.live_payload(result)
        waiting = []
        next_id = 1
        for index, day in enumerate(payload["live_days"]):
            phase_queues = [waiting.copy()]
            arrivals = list(range(next_id, next_id + day["arrivals"]))
            self.assertEqual(day["arrival_first"], next_id)
            next_id += len(arrivals)
            waiting.extend(arrivals)
            phase_queues.append(waiting.copy())
            tested = waiting[:10]
            waiting = waiting[10:]
            phase_queues.append(waiting.copy())
            self.assertEqual(day["tested"], len(tested))
            self.assertEqual(list(range(day["tested_first"], day["tested_first"] + day["tested"])), tested)
            for phase, queue in enumerate(phase_queues):
                first, count = day["queue_first"][phase], day["queue_counts"][phase]
                self.assertEqual(list(range(first, first + count)), queue)
                self.assertEqual(day["queue_cost_cents"][phase], len(queue) * 99999)
            self.assertEqual(day["queue_counts"][2], payload["backlog"][index + 1])
            self.assertEqual(day["queue_cost_cents"][2], payload["bound_cost_cents"][index + 1])


if __name__ == "__main__":
    unittest.main()
