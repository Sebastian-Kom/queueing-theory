#!/usr/bin/env python3
"""Experiment 02: independently fluctuating development and testing capacities.

Run: python scripts/02_fluctuating_capacities.py --live --output-dir outputs/local
Daily counts are max(0, floor(mean + std * Z + 0.5)), Z ~ N(0, 1).
All new features arrive before testing; equal effort, FIFO, no rework or sales.
"""
from __future__ import annotations

import argparse
from collections import deque
import csv
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
import webbrowser

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MAX_PARAMETER = 1000


def validate_parameters(mean: float, std: float) -> None:
    if not all(math.isfinite(x) and 0 <= x <= MAX_PARAMETER for x in (mean, std)):
        raise ValueError("means and standard deviations must be finite numbers from 0 to 1000")


def cost_in_cents(value: str | float) -> int:
    try:
        euros = Decimal(str(value))
        if not euros.is_finite() or euros < 0 or euros * 100 != (euros * 100).to_integral_value():
            raise ValueError
        cents = int(euros * 100)
        if cents > 2**53 - 1:
            raise ValueError
        return cents
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise ValueError("feature cost must be nonnegative euros with at most two decimal places") from exc


def counts_from_shocks(shocks: np.ndarray, mean: float, std: float) -> np.ndarray:
    validate_parameters(mean, std)
    return np.maximum(0, np.floor(mean + std * np.asarray(shocks) + 0.5)).astype(np.int64)


def capacity_law(mean: float, std: float) -> dict:
    """Moments of rounded, zero-clamped Gaussian counts; omit only the >9-sigma tail.

    P(C=0) = Phi((0.5-mean)/std); for k>=1 use the normal mass in
    [k-0.5, k+0.5). Use survival probabilities for numerical stability.
    These are distribution moments, not a particular run's observed averages.
    """
    validate_parameters(mean, std)
    if std == 0:
        k = math.floor(mean + 0.5)
        return {"mean": float(k), "std": 0.0, "counts": [k], "probabilities": [1.0],
                "negative_probability": 0.0}
    maximum = max(1, math.ceil(mean + 9 * std + 0.5))
    counts = np.arange(maximum + 1)
    survival = np.array([0.5 * math.erfc((k - 0.5 - mean) / (std * math.sqrt(2)))
                         for k in range(1, maximum + 2)])
    probabilities = np.r_[1 - survival[0], survival[:-1] - survival[1:]]
    actual_mean = float(np.dot(counts, probabilities))
    variance = float(np.dot((counts - actual_mean)**2, probabilities))
    return {"mean": actual_mean, "std": math.sqrt(max(0, variance)),
            "counts": counts.tolist(), "probabilities": probabilities.tolist(),
            "negative_probability": 0.5 * math.erfc(mean / (std * math.sqrt(2)))}


def simulate_queue(arrivals: np.ndarray, capacities: np.ndarray, unit_cost_cents: int = 100000) -> dict:
    """Track counts, FIFO cohorts, exact costs and completed-feature waiting days.

    Same-day completion has waiting age 0. No within-day service-time claim is
    made. Cohorts avoid allocating an object for every feature in a large run.
    """
    arrivals, capacities = np.asarray(arrivals), np.asarray(capacities)
    if (arrivals.ndim != 1 or capacities.shape != arrivals.shape
            or not np.issubdtype(arrivals.dtype, np.integer)
            or not np.issubdtype(capacities.dtype, np.integer)
            or np.any(arrivals < 0) or np.any(capacities < 0)):
        raise ValueError("arrivals and capacities must be matching nonnegative integer arrays")
    if not isinstance(unit_cost_cents, int) or unit_cost_cents < 0:
        raise ValueError("feature cost must be a nonnegative integer number of cents")
    if sum(map(int, arrivals)) * unit_cost_cents > 2**53 - 1:
        raise ValueError("total cost exceeds exact browser integer accounting")
    backlog, completed, unused, live_days = [0], [], [], []
    wait_mean = [None]
    cohorts: deque = deque()
    total_arrivals = total_tested = total_wait = 0
    for index, (incoming, capacity) in enumerate(zip(arrivals, capacities)):
        incoming, capacity = int(incoming), int(capacity)
        day = index + 1
        oldest_before = day - cohorts[0][0] if cohorts else None
        if incoming:
            cohorts.append([day, incoming])
        oldest_joined = day - cohorts[0][0] if cohorts else None
        tested = min(backlog[-1] + incoming, capacity)
        remaining, wait_today = tested, 0
        while remaining:
            take = min(remaining, cohorts[0][1])
            wait_today += take * (day - cohorts[0][0])
            remaining -= take
            cohorts[0][1] -= take
            if cohorts[0][1] == 0:
                cohorts.popleft()
        after = backlog[-1] + incoming - tested
        counts = [backlog[-1], backlog[-1] + incoming, after]
        live_days.append({
            "arrival_first": total_arrivals + 1, "arrivals": incoming,
            "capacity": capacity, "tested_first": total_tested + 1, "tested": tested,
            "unused": capacity - tested,
            "queue_first": [total_tested + 1, total_tested + 1, total_tested + tested + 1],
            "queue_counts": counts, "queue_cost_cents": [q * unit_cost_cents for q in counts],
            "oldest_wait": [oldest_before, oldest_joined, day - cohorts[0][0] if cohorts else None],
            "mean_wait_today": wait_today / tested if tested else None,
        })
        total_arrivals += incoming
        total_tested += tested
        total_wait += wait_today
        backlog.append(after)
        completed.append(tested)
        unused.append(capacity - tested)
        wait_mean.append(total_wait / total_tested if total_tested else None)
    return {"arrivals": arrivals.tolist(), "capacities": capacities.tolist(),
            "backlog": backlog, "completed": completed, "unused": unused,
            "live_days": live_days, "mean_completed_wait": wait_mean,
            "bound_cost_cents": [q * unit_cost_cents for q in backlog]}


def run_experiment(days: int = 500, runs: int = 10000, development_mean: float = 10,
                   development_std: float = 2, testing_mean: float = 10,
                   testing_std: float = 2, seed: int = 42,
                   feature_cost: str | float = "1000") -> dict:
    validate_parameters(development_mean, development_std)
    validate_parameters(testing_mean, testing_std)
    if not 1 <= days <= 5000 or not 2 <= runs <= 20000 or days * runs > 20_000_000:
        raise ValueError("need 1–5000 days, 2–20000 runs, and at most 20 million run-days")
    if not isinstance(seed, int) or seed < 0:
        raise ValueError("seed must be a nonnegative integer")
    cents = cost_in_cents(feature_cost)
    # Independent streams; common standardized shocks make parameter changes comparable.
    seeds = np.random.SeedSequence(seed).spawn(4)
    dev_shocks = np.random.default_rng(seeds[0]).standard_normal(days)
    test_shocks = np.random.default_rng(seeds[1]).standard_normal(days)
    arrivals = counts_from_shocks(dev_shocks, development_mean, development_std)
    capacities = counts_from_shocks(test_shocks, testing_mean, testing_std)
    sample = simulate_queue(arrivals, capacities, cents)
    dev_rng, test_rng = (np.random.default_rng(s) for s in seeds[2:])
    paths = np.zeros((runs, days + 1), dtype=np.int64)
    total_arrivals = total_capacity = 0
    for day in range(days):
        incoming = counts_from_shocks(dev_rng.standard_normal(runs), development_mean, development_std)
        available = counts_from_shocks(test_rng.standard_normal(runs), testing_mean, testing_std)
        total_arrivals += int(incoming.sum())
        total_capacity += int(available.sum())
        paths[:, day + 1] = np.maximum(0, paths[:, day] + incoming - available)
    mean = paths.mean(axis=0)
    sem = paths.std(axis=0, ddof=1) / math.sqrt(runs)
    p10, p90 = np.quantile(paths, [0.1, 0.9], axis=0)
    return {
        "parameters": {"days": days, "runs": runs, "development_mean": development_mean,
                       "development_std": development_std, "testing_mean": testing_mean,
                       "testing_std": testing_std, "seed": seed, "feature_cost_cents": cents,
                       "currency": "EUR"},
        "development_shocks": dev_shocks.tolist(), "testing_shocks": test_shocks.tolist(),
        "development_law": capacity_law(development_mean, development_std),
        "testing_law": capacity_law(testing_mean, testing_std),
        "sample": sample, "mean": mean, "sem": sem, "p10": p10, "p90": p90,
        "ensemble_arrivals_per_day": total_arrivals / (runs * days),
        "ensemble_capacity_per_day": total_capacity / (runs * days),
        "ensemble_completed_per_day": (total_arrivals - int(paths[:, -1].sum())) / (runs * days),
    }


def live_payload(result: dict) -> dict:
    return {**result["parameters"], "development_shocks": result["development_shocks"],
            "testing_shocks": result["testing_shocks"], "reference": result["sample"]}


def write_live_view(result: dict, directory: Path, fragment_path: Path | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    template = (ROOT / "templates" / "02_capacity_player.html").read_text()
    model_js = (ROOT / "templates" / "02_queue_model.js").read_text()
    fragment = template.replace("__MODEL_JS__", model_js).replace(
        "__QUEUE_DATA__", json.dumps(live_payload(result), separators=(",", ":"), allow_nan=False))
    if fragment_path:
        fragment_path.write_text(fragment, encoding="utf-8")
    style = (ROOT / "templates" / "02_standalone.css").read_text()
    html = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Queueing Theory · Fluctuating capacities</title><style>' + style
            + '</style></head><body>' + fragment + '</body></html>')
    path = directory / "02_live_queue.html"
    path.write_text(html, encoding="utf-8")
    return path.resolve()


def write_results(result: dict, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    sample, p = result["sample"], result["parameters"]
    with (directory / "02_sample_path.csv").open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["working_day", "development_output", "testing_capacity", "completed",
                         "unused_capacity", "backlog_end_of_day", "bound_development_cost_eur",
                         "oldest_waiting_age_days", "mean_wait_completed_today_days",
                         "mean_wait_all_completed_days"])
        for i, day in enumerate(sample["live_days"]):
            writer.writerow([i + 1, day["arrivals"], day["capacity"], day["tested"], day["unused"],
                             sample["backlog"][i + 1],
                             str(Decimal(sample["bound_cost_cents"][i + 1]) / 100),
                             day["oldest_wait"][2], day["mean_wait_today"],
                             sample["mean_completed_wait"][i + 1]])
    with (directory / "02_ensemble.csv").open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["working_day", "mean_backlog", "mean_standard_error", "p10", "p90"])
        writer.writerows([i, *[float(result[k][i]) for k in ("mean", "sem", "p10", "p90")]]
                         for i in range(p["days"] + 1))
    summary = {"experiment": "02_fluctuating_capacities", "parameters": p,
               "count_rule": "max(0, floor(mean + std * Z + 0.5)), independent standard normal Z",
               "effective_distributions": {key: {k: result[key][k] for k in ("mean", "std", "negative_probability")}
                                           for key in ("development_law", "testing_law")},
               "sample_final_backlog": sample["backlog"][-1],
               "sample_final_bound_cost_eur": sample["bound_cost_cents"][-1] / 100,
               "sample_mean_completed_wait_days": sample["mean_completed_wait"][-1],
               "final_ensemble_mean_backlog": float(result["mean"][-1]),
               "final_ensemble_mean_standard_error": float(result["sem"][-1]),
               "ensemble_arrivals_per_day": result["ensemble_arrivals_per_day"],
               "ensemble_capacity_per_day": result["ensemble_capacity_per_day"],
               "money_definition": "Already-paid development spend of waiting features, not revenue or cash recovered.",
               "wait_definition": "Completion day minus arrival day in working-day steps; same-day completion is zero. Unfinished features are excluded from completed-feature averages."}
    (directory / "02_summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")


def plot_results(result: dict, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    p, sample = result["parameters"], result["sample"]
    days = np.arange(p["days"] + 1)
    with plt.rc_context({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "svg.fonttype": "none"}):
        fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True, layout="constrained")
        fig.suptitle("Fluctuating development and testing capacities\n"
                     f"Development: mean {p['development_mean']:g}, SD {p['development_std']:g} · "
                     f"Testing: mean {p['testing_mean']:g}, SD {p['testing_std']:g}", fontsize=15)
        axes[0].step(days, sample["backlog"], where="post", color="#637649", lw=1.5)
        axes[0].set_title(f"One department · seed {p['seed']}", loc="left")
        axes[1].fill_between(days, result["p10"], result["p90"], color="#dde4d5",
                             label="10th–90th percentiles of individual queues")
        axes[1].plot(days, result["mean"], color="#637649", label=f"Mean of {p['runs']:,} independent runs")
        axes[1].set_title("Across repeated simulations", loc="left")
        axes[1].legend(frameon=False)
        axes[1].set_xlabel("Working days")
        for ax in axes:
            ax.set_ylabel("Waiting features")
            ax.set_ylim(bottom=0)
            ax.set_xlim(0, p["days"])
            ax.grid(axis="y", alpha=.2)
        for extension in ("png", "svg"):
            fig.savefig(directory / f"02_fluctuating_capacities.{extension}", dpi=160)
        plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=500)
    parser.add_argument("--runs", type=int, default=10000)
    for key, default in (("development-mean", 10), ("development-std", 2),
                         ("testing-mean", 10), ("testing-std", 2)):
        parser.add_argument("--" + key, type=float, default=default)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--feature-cost", default="1000")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=ROOT)
    parser.add_argument("--fragment-output", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        result = run_experiment(args.days, args.runs, args.development_mean, args.development_std,
                                args.testing_mean, args.testing_std, args.seed, args.feature_cost)
    except ValueError as exc:
        parser.error(str(exc))
    write_results(result, args.output_dir / "results")
    plot_results(result, args.output_dir / "figures")
    player = write_live_view(result, args.output_dir / "figures", args.fragment_output)
    for label, key in (("Development", "development_law"), ("Testing", "testing_law")):
        print(f"{label} effective counts: mean {result[key]['mean']:.5f}, SD {result[key]['std']:.5f}")
    print(f"Day {args.days}: mean backlog {result['mean'][-1]:.3f}; standard error {result['sem'][-1]:.3f}")
    print(f"Live player: {player}")
    if args.live and not webbrowser.open(player.as_uri()):
        print("Open the live player file above manually.")


if __name__ == "__main__":
    main()
