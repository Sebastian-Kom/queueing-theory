"""Independent accounting, distribution, and Python/browser agreement checks."""
import importlib.util
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fluctuating", ROOT / "scripts/02_fluctuating_capacities.py")
model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model)


class FluctuatingCapacityTests(unittest.TestCase):
    def test_unused_capacity_expires_and_queue_can_clear(self):
        sample = model.simulate_queue(np.array([8, 12, 8]), np.array([12, 8, 12]), 29)
        self.assertEqual(sample["backlog"], [0, 0, 4, 0])
        self.assertEqual(sample["completed"], [8, 8, 12])
        self.assertEqual(sample["unused"], [4, 0, 0])
        self.assertEqual(sample["bound_cost_cents"], [0, 0, 116, 0])

    def test_fifo_and_waiting_times_against_individual_features(self):
        rng = np.random.default_rng(25)
        arrivals, capacities = rng.integers(0, 16, (2, 80))
        sample = model.simulate_queue(arrivals, capacities, 99999)
        waiting, waits, arrived, tested = [], [], 0, 0
        for index, day in enumerate(sample["live_days"]):
            for phase in range(3):
                if phase == 1:
                    waiting.extend((arrived + i + 1, index + 1) for i in range(day["arrivals"]))
                    arrived += day["arrivals"]
                if phase == 2:
                    departures = waiting[:day["capacity"]]
                    waiting = waiting[day["capacity"]:]
                    waits.extend(index + 1 - arrival_day for _, arrival_day in departures)
                    tested += len(departures)
                    self.assertEqual(day["tested"], len(departures))
                    self.assertEqual(day["unused"] + len(departures), day["capacity"])
                self.assertEqual(day["queue_counts"][phase], len(waiting))
                self.assertEqual(day["queue_cost_cents"][phase], len(waiting)*99999)
                self.assertEqual(day["oldest_wait"][phase], index + 1 - waiting[0][1] if waiting else None)
                if waiting:
                    self.assertEqual(day["queue_first"][phase], waiting[0][0])
            self.assertEqual(arrived - tested, len(waiting))
            self.assertEqual(sample["mean_completed_wait"][index+1], sum(waits)/len(waits) if waits else None)

    def test_no_completions_do_not_report_zero_wait(self):
        sample = model.simulate_queue(np.array([2, 2, 2]), np.array([0, 0, 0]))
        self.assertEqual(sample["mean_completed_wait"], [None]*4)
        self.assertEqual(sample["live_days"][-1]["oldest_wait"][2], 2)

    def test_zero_variance_and_zero_capacity(self):
        equal = model.run_experiment(days=20, runs=10, development_std=0, testing_std=0)
        np.testing.assert_array_equal(equal["mean"], 0)
        shortage = model.run_experiment(days=3, runs=2, development_mean=2,
                                        development_std=0, testing_mean=0, testing_std=0)
        self.assertEqual(shortage["sample"]["backlog"], [0, 2, 4, 6])

    def test_rounding_and_zero_clamp_are_explicit(self):
        np.testing.assert_array_equal(model.counts_from_shocks(np.array([-10, -.51, -.5, 0, .49, .5]), 0, 1),
                                      [0, 0, 0, 0, 0, 1])
        self.assertEqual(model.capacity_law(2.5, 0)["mean"], 3)

    def test_effective_moments_distinguish_parameters_from_counts(self):
        law = model.capacity_law(10, 2)
        self.assertAlmostEqual(sum(law["probabilities"]), 1)
        self.assertAlmostEqual(law["mean"], 10, places=5)
        self.assertAlmostEqual(law["std"]**2, 4 + 1/12, places=4)
        clamped = model.capacity_law(0, 2)
        self.assertGreater(clamped["mean"], .7)
        self.assertEqual(clamped["negative_probability"], .5)

    def test_capacity_draws_match_the_count_distribution(self):
        draws = model.counts_from_shocks(np.random.default_rng(17).standard_normal(200000), 1, 4)
        law = model.capacity_law(1, 4)
        self.assertLess(abs(draws.mean() - law["mean"]), 5*law["std"]/math.sqrt(len(draws)))

    def test_same_draws_make_parameter_comparisons_reproducible(self):
        first = model.run_experiment(days=100, runs=2)
        reserve = model.run_experiment(days=100, runs=10, testing_mean=12)
        self.assertEqual(first["development_shocks"], reserve["development_shocks"])
        self.assertEqual(first["testing_shocks"], reserve["testing_shocks"])
        self.assertEqual(first["sample"]["arrivals"], reserve["sample"]["arrivals"])
        self.assertTrue(np.all(np.array(reserve["sample"]["backlog"]) <= first["sample"]["backlog"]))

    def test_capacity_streams_are_distinct(self):
        result = model.run_experiment(days=100, runs=2)
        self.assertNotEqual(result["development_shocks"], result["testing_shocks"])
        self.assertGreater(len(set(result["sample"]["capacities"])), 1)

    def test_money_changes_neither_counts_nor_waiting(self):
        a = model.run_experiment(days=20, runs=2, feature_cost="0.29")
        b = model.run_experiment(days=20, runs=2, feature_cost="0.58")
        self.assertEqual(a["sample"]["backlog"], b["sample"]["backlog"])
        self.assertEqual(a["sample"]["mean_completed_wait"], b["sample"]["mean_completed_wait"])
        self.assertEqual([2*x for x in a["sample"]["bound_cost_cents"]], b["sample"]["bound_cost_cents"])

    def test_bad_inputs_and_cost_overflow_are_rejected(self):
        for bad in (-1, float("nan"), float("inf"), 1001):
            with self.assertRaises(ValueError):
                model.capacity_law(bad, 2)
        for bad in ("-1", "NaN", "Infinity", "1.001", "invalid"):
            with self.assertRaises(ValueError):
                model.cost_in_cents(bad)
        with self.assertRaises(ValueError):
            model.simulate_queue(np.array([2]), np.array([0]), 2**53 - 1)
        with self.assertRaises(ValueError):
            model.simulate_queue(np.array([1]), np.array([1, 2]))

    @unittest.skipUnless(shutil.which("node"), "Node required for cross-language check")
    def test_browser_and_python_recompute_the_same_features_and_costs(self):
        result = model.run_experiment(days=40, runs=2, feature_cost="999.99")
        cases = [(10, 2, 10, 2), (10, 2, 12, 2), (0, 0, 0, 0), (1, 20, 0, 5), (10, 0, 10, 0)]
        payload = model.live_payload(result)
        payload["cases"] = cases
        source = """
const fs=require('node:fs'), model=require(process.argv[1]);
const p=JSON.parse(fs.readFileSync(0,'utf8'));
console.log(JSON.stringify(p.cases.map(([dm,ds,tm,ts])=>({
  sample:model.simulate(model.counts(p.development_shocks,dm,ds),model.counts(p.testing_shocks,tm,ts),p.feature_cost_cents),
  dev:model.law(dm,ds), test:model.law(tm,ts)
}))));
"""
        output = subprocess.check_output(["node", "-e", source, str(ROOT / "templates/02_queue_model.js")],
                                         input=json.dumps(payload), text=True)
        for values, js in zip(cases, json.loads(output)):
            dm, ds, tm, ts = values
            expected = model.simulate_queue(model.counts_from_shocks(np.array(payload["development_shocks"]), dm, ds),
                                            model.counts_from_shocks(np.array(payload["testing_shocks"]), tm, ts),
                                            payload["feature_cost_cents"])
            self.assertEqual(js["sample"], expected)
            for mean, std, key in ((dm, ds, "dev"), (tm, ts, "test")):
                law = model.capacity_law(mean, std)
                self.assertAlmostEqual(js[key]["mean"], law["mean"], places=4)
                self.assertAlmostEqual(js[key]["std"], law["std"], places=4)

    def test_standalone_player_contains_reference_and_has_no_external_dependencies(self):
        result = model.run_experiment(days=3, runs=2)
        with tempfile.TemporaryDirectory() as tmp:
            html = model.write_live_view(result, Path(tmp)).read_text()
        payload = json.loads(html.split('data-role="model-data">', 1)[1].split('</script>', 1)[0])
        self.assertEqual(payload["reference"], result["sample"])
        self.assertNotIn("__MODEL_JS__", html)
        self.assertNotIn("__QUEUE_DATA__", html)
        self.assertNotIn("<script src=", html)


if __name__ == "__main__":
    unittest.main()
