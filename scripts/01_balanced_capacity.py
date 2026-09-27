#!/usr/bin/env python3
"""Experiment 01: fixed testing capacity, balanced but variable arrivals.

Run from the repository root:
    python scripts/01_balanced_capacity.py

The end-of-day backlog follows Q[d+1] = max(0, Q[d] + A[d] - C).
All features require equal effort and arrive before that day's testing begins.
This is a discrete-time batch queue, not an M/M/1 queue.
"""

from __future__ import annotations

import argparse
import csv
from decimal import Decimal, InvalidOperation
from io import BytesIO
import json
import platform
from pathlib import Path
import webbrowser

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
import numpy as np


ROOT = Path(__file__).resolve().parents[1]


def cost_in_cents(value: str | float | Decimal) -> int:
    """Read an exact, non-negative euro amount with at most two decimal places."""
    try:
        euros = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("feature cost must be a euro amount, for example 1000 or 1250.50") from error
    if not euros.is_finite() or euros < 0 or euros > Decimal(2**53 - 1) / 100:
        raise ValueError("feature cost must be finite, non-negative and representable in cents")
    cents = euros * 100
    if cents != cents.to_integral_value():
        raise ValueError("feature cost must have at most two decimal places")
    return int(cents)


def feature_costs(arrivals: np.ndarray, sample: dict, unit_cost_cents: int) -> dict:
    """Allocate already-paid development spend to waiting or tested features.

    This is a stock of development cost, not revenue or a cost charged per day.
    Integer cents keep the realized path's accounting exact.
    """
    if unit_cost_cents < 0:
        raise ValueError("feature cost cannot be negative")
    cumulative_arrivals = np.cumsum(arrivals, dtype=np.int64)
    if len(arrivals) and int(cumulative_arrivals[-1]) * unit_cost_cents > 2**53 - 1:
        raise ValueError("total development spend is too large for exact browser playback")
    return {
        "bound_cost_cents": sample["backlog"] * unit_cost_cents,
        "ready_cost_cents": np.r_[0, np.cumsum(sample["completed"])] * unit_cost_cents,
        "total_development_cost_cents": np.r_[0, cumulative_arrivals] * unit_cost_cents,
    }


def simulate_queue(arrivals: np.ndarray, capacity: int) -> dict[str, np.ndarray]:
    """Process a known arrival sequence; no work is discarded or preprocessed."""
    arrivals = np.asarray(arrivals)
    if arrivals.ndim != 1 or not np.issubdtype(arrivals.dtype, np.integer):
        raise ValueError("arrivals must be a one-dimensional integer array")
    if np.any(arrivals < 0) or capacity < 0:
        raise ValueError("arrivals and capacity must be non-negative")
    backlog = np.zeros(len(arrivals) + 1, dtype=np.int64)
    completed = np.zeros(len(arrivals), dtype=np.int64)
    for day, incoming in enumerate(arrivals):
        available = int(backlog[day]) + int(incoming)
        completed[day] = min(available, capacity)
        backlog[day + 1] = available - completed[day]
    return {
        "backlog": backlog,
        "completed": completed,
        "unused_capacity": capacity - completed,
    }


def exact_expectation(days: int, variation: int) -> tuple[np.ndarray, np.ndarray]:
    """Propagate the exact state probabilities of the reflected random walk.

    For positive variation, state k means a backlog of k * variation features.
    Above zero, the next state is k-1 or k+1, each with probability 1/2.
    At zero, it stays at zero or rises to one. No stationarity is assumed.
    Complexity is O(days**2); the default 500-day horizon is small.
    """
    if days < 0 or variation < 0:
        raise ValueError("days and variation must be non-negative")
    mean = np.zeros(days + 1)
    empty_probability = np.ones(days + 1)
    if variation == 0:
        return mean, empty_probability
    probability = np.array([1.0])
    for day in range(1, days + 1):
        next_probability = np.zeros(len(probability) + 1)
        next_probability[1:] += 0.5 * probability  # a busy arrival day
        next_probability[:-2] += 0.5 * probability[1:]  # a quiet day
        next_probability[0] += 0.5 * probability[0]  # unused capacity
        probability = next_probability
        states = variation * np.arange(len(probability))
        mean[day] = np.dot(states, probability)
        empty_probability[day] = probability[0]
    return mean, empty_probability


def run_experiment(
    days: int = 500,
    runs: int = 10000,
    capacity: int = 10,
    variation: int = 2,
    seed: int = 42,
    feature_cost: str | float | Decimal = "1000",
) -> dict:
    """Simulate one illustrative run and an independent Monte Carlo ensemble."""
    if days < 1 or runs < 2 or capacity < 1 or not 0 <= variation <= capacity:
        raise ValueError("Need days >= 1, runs >= 2, capacity >= 1, 0 <= variation <= capacity")
    if seed < 0:
        raise ValueError("seed must be non-negative")
    unit_cost_cents = cost_in_cents(feature_cost)

    # Separate streams keep the illustrative path unchanged when runs changes.
    sample_seed, ensemble_seed = np.random.SeedSequence(seed).spawn(2)
    sample_rng = np.random.default_rng(sample_seed)
    rng = np.random.default_rng(ensemble_seed)
    sample_arrivals = capacity + variation * (2 * sample_rng.integers(0, 2, days) - 1)
    sample = simulate_queue(sample_arrivals, capacity)
    sample.update(feature_costs(sample_arrivals, sample, unit_cost_cents))
    regular = simulate_queue(np.full(days, capacity, dtype=np.int64), capacity)

    backlogs = np.zeros((runs, days + 1), dtype=np.int64)
    total_arrivals = 0
    for day in range(days):
        arrivals = capacity + variation * (2 * rng.integers(0, 2, runs) - 1)
        total_arrivals += int(arrivals.sum())
        backlogs[:, day + 1] = np.maximum(0, backlogs[:, day] + arrivals - capacity)

    exact_mean, exact_empty = exact_expectation(days, variation)
    lower, upper = np.quantile(backlogs, [0.1, 0.9], axis=0)
    mean = backlogs.mean(axis=0)
    sem = backlogs.std(axis=0, ddof=1) / np.sqrt(runs)
    completed = total_arrivals - int(backlogs[:, -1].sum())
    return {
        "parameters": {"days": days, "runs": runs, "capacity": capacity,
                       "variation": variation, "seed": seed,
                       "feature_cost_cents": unit_cost_cents, "currency": "EUR"},
        "sample_arrivals": sample_arrivals,
        "sample": sample,
        "regular": regular,
        "mean": mean,
        "sem": sem,
        "p10": lower,
        "p90": upper,
        "empty_probability": (backlogs == 0).mean(axis=0),
        "exact_mean": exact_mean,
        "mean_bound_cost_eur": mean * unit_cost_cents / 100,
        "exact_expected_bound_cost_eur": exact_mean * unit_cost_cents / 100,
        "exact_empty_probability": exact_empty,
        "ensemble_arrivals_per_day": total_arrivals / (days * runs),
        "ensemble_completed_per_day": completed / (days * runs),
        "ensemble_unused_capacity_per_day": capacity - completed / (days * runs),
    }


def write_results(result: dict, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    p = result["parameters"]
    with (directory / "01_sample_path.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["working_day", "arrivals", "capacity", "completed",
                         "unused_capacity", "backlog_end_of_day", "bound_development_cost_eur",
                         "tested_development_cost_cumulative_eur", "development_spend_cumulative_eur"])
        for day in range(p["days"]):
            writer.writerow([day + 1, int(result["sample_arrivals"][day]), p["capacity"],
                             int(result["sample"]["completed"][day]),
                             int(result["sample"]["unused_capacity"][day]),
                             int(result["sample"]["backlog"][day + 1]),
                             *[format(Decimal(int(result["sample"][key][day + 1])) / 100, ".2f")
                               for key in ("bound_cost_cents", "ready_cost_cents",
                                           "total_development_cost_cents")]])
    with (directory / "01_ensemble.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["working_day", "simulation_mean", "simulation_mean_standard_error",
                         "p10", "p90", "exact_expected_backlog", "simulation_empty_probability",
                         "exact_empty_probability", "mean_bound_development_cost_eur",
                         "exact_expected_bound_development_cost_eur"])
        for day in range(p["days"] + 1):
            writer.writerow([day, *[float(result[key][day]) for key in (
                "mean", "sem", "p10", "p90", "exact_mean", "empty_probability",
                "exact_empty_probability", "mean_bound_cost_eur", "exact_expected_bound_cost_eur")]])
    summary = {
        "experiment": "01_balanced_capacity",
        "parameters": p,
        "model": "discrete-time batch arrivals; equal effort; all arrivals before daily service",
        "arrival_probabilities": {str(p["capacity"] - p["variation"]): 0.5,
                                  str(p["capacity"] + p["variation"]): 0.5}
        if p["variation"] else {str(p["capacity"]): 1.0},
        "nominal_load": 1.0,
        "sample_average_arrivals": float(result["sample_arrivals"].mean()),
        "sample_final_backlog": int(result["sample"]["backlog"][-1]),
        "sample_final_bound_development_cost_eur": int(result["sample"]["bound_cost_cents"][-1]) / 100,
        "final_exact_expected_bound_development_cost_eur": float(result["exact_expected_bound_cost_eur"][-1]),
        "money_definition": "Already-paid development cost of features awaiting testing at day end. Testing makes a feature ready to sell; sales and revenue are not modeled.",
        "ensemble_average_arrivals": result["ensemble_arrivals_per_day"],
        "ensemble_average_completions": result["ensemble_completed_per_day"],
        "ensemble_average_unused_capacity": result["ensemble_unused_capacity_per_day"],
        "final_mean_backlog": float(result["mean"][-1]),
        "final_mean_standard_error": float(result["sem"][-1]),
        "final_exact_expected_backlog": float(result["exact_mean"][-1]),
        "final_p10": float(result["p10"][-1]),
        "final_p90": float(result["p90"][-1]),
        "final_exact_probability_empty": float(result["exact_empty_probability"][-1]),
        "interpretation": (
            "Individual queues can empty again. With positive variation, the expected backlog grows without a finite limit; there is no stationary queue-length distribution."
            if p["variation"] else "With regular arrivals equal to capacity and an initially empty queue, the end-of-day backlog remains zero."
        ),
        "versions": {"python": platform.python_version(), "numpy": np.__version__,
                     "matplotlib": matplotlib.__version__},
    }
    (directory / "01_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


def plot_results(result: dict, directory: Path) -> None:
    """Save a static, publication-ready figure; no graphical desktop is required."""
    directory.mkdir(parents=True, exist_ok=True)
    p = result["parameters"]
    day = np.arange(p["days"] + 1)
    fluctuating = p["variation"] > 0
    green, ink, muted, band = "#206747", "#19382c", "#697971", "#dce9df"
    with plt.rc_context({"font.family": "DejaVu Sans", "font.size": 11,
                         "axes.labelcolor": ink, "text.color": ink,
                         "xtick.color": muted, "ytick.color": muted,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.edgecolor": "#a5b7aa", "svg.fonttype": "none"}):
        fig, axes = plt.subplots(2, 1, figsize=(11.4, 9.2), sharex=True)
        fig.subplots_adjust(top=0.79, bottom=0.14, left=0.105, right=0.865, hspace=0.45)
        fig.text(0.105, 0.96, "QUEUEING THEORY  /  EXPERIMENT 01", color=green,
                 size=11, weight="bold")
        fig.text(0.105, 0.909,
                 "Equal averages. Growing backlog." if fluctuating else "Regular arrivals. No backlog.",
                 size=25, weight="bold")
        fig.text(0.105, 0.865,
                 f"Fixed testing capacity: {p['capacity']} features/day. "
                 + (f"Random arrivals: {p['capacity'] - p['variation']} or "
                    f"{p['capacity'] + p['variation']} features/day, each with probability 50%."
                    if fluctuating else f"Exactly {p['capacity']} features arrive every morning."),
                 size=11, color=muted)
        fig.text(0.105, 0.839,
                 f"Development cost: €{p['feature_cost_cents'] / 100:,.2f} per feature. "
                 "Equal test effort. Empty queue at the start.",
                 size=10, color=muted)

        ax = axes[0]
        ax.set_title("One simulated testing department", loc="left", pad=12,
                     size=14, weight="bold")
        ax.fill_between(day, result["sample"]["backlog"], step="post", color=band)
        ax.step(day, result["sample"]["backlog"], where="post", color=green,
                lw=1.8, label=f"Variable arrivals · seed {p['seed']}" if fluctuating else "Regular arrivals")
        ax.plot(day, result["regular"]["backlog"], color=muted, ls="--", lw=1.6,
                label=f"Exactly {p['capacity']} arrivals/day · zero backlog", zorder=5)
        ax.legend(loc="upper left", frameon=False, fontsize=10)

        ax = axes[1]
        ax.set_title("Across many departments, the average keeps rising" if fluctuating
                     else "Without variability, every queue stays empty", loc="left",
                     pad=12, size=14, weight="bold")
        ax.fill_between(day, result["p10"], result["p90"], color=band,
                        label="10th–90th percentiles of individual queues")
        ax.plot(day, result["exact_mean"], color=green, lw=2.2, label="Exact expected backlog")
        points = np.unique(np.linspace(0, p["days"], min(21, p["days"] + 1), dtype=int))
        ax.plot(day[points], result["mean"][points], "o", ms=3.8, color=ink,
                label=f"Simulation mean · {p['runs']:,} independent runs")
        ax.legend(loc="upper left", frameon=False, fontsize=10)
        ax.set_xlabel("Working days", labelpad=10)

        for ax in axes:
            ax.set_ylabel("Unfinished features", labelpad=12)
            ax.set_xlim(0, p["days"])
            upper_limit = max(1, ax.get_ylim()[1])
            ax.set_ylim(-upper_limit * 0.025, upper_limit)
            ax.yaxis.set_major_locator(MaxNLocator(integer=True, nbins=5))
            ax.xaxis.set_major_locator(MaxNLocator(integer=True, nbins=6))
            ax.grid(axis="y", color="#e6ebe7", lw=0.8)
            ax.set_axisbelow(True)
            ax.tick_params(length=0, pad=6)
            if p["feature_cost_cents"]:
                thousands_per_feature = p["feature_cost_cents"] / 100000
                money_axis = ax.secondary_yaxis(
                    "right", functions=(lambda q: q * thousands_per_feature,
                                        lambda money: money / thousands_per_feature))
                money_axis.set_ylabel("Bound development cost (€ thousands)", labelpad=12)
                money_axis.yaxis.set_major_locator(MaxNLocator(nbins=5))
                money_axis.tick_params(length=0, pad=6)

        fig.text(0.105, 0.065,
                 "Individual queues can empty again. The expected backlog does not settle." if fluctuating
                 else "Without arrival variability, every feature completes on its arrival day.",
                 size=11, weight="bold")
        fig.text(0.105, 0.037,
                 "Shading shows variability between queues, not uncertainty in the mean. "
                 "Synthetic model, not company data.", size=9.5, color=muted)
        for extension in ("png", "svg"):
            # Finish encoding before writing, so the exported file is complete.
            buffer = BytesIO()
            fig.savefig(buffer, format=extension, dpi=180,
                        facecolor="white", metadata={"Creator": "Queueing Theory"})
            (directory / f"01_balanced_capacity.{extension}").write_bytes(buffer.getvalue())
        plt.close(fig)


def live_payload(result: dict) -> dict:
    """Only Python computes the model. The browser replays the saved sample path."""
    return {
        **result["parameters"],
        "arrivals": result["sample_arrivals"].tolist(),
        "tested": result["sample"]["completed"].tolist(),
        "backlog": result["sample"]["backlog"].tolist(),
        "bound_cost_cents": result["sample"]["bound_cost_cents"].tolist(),
    }


def write_live_view(result: dict, directory: Path) -> Path:
    """Create an offline, self-contained browser player with no server or CDN."""
    directory.mkdir(parents=True, exist_ok=True)
    template = (ROOT / "templates" / "01_live_queue.html").read_text(encoding="utf-8")
    data = json.dumps(live_payload(result), separators=(",", ":"), allow_nan=False)
    path = directory / "01_live_queue.html"
    path.write_text(template.replace("__QUEUE_DATA__", data), encoding="utf-8")
    return path.resolve()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=500, help="working days (default: 500)")
    parser.add_argument("--runs", type=int, default=10000, help="independent runs (default: 10000)")
    parser.add_argument("--capacity", type=int, default=10, help="fixed features/day (default: 10)")
    parser.add_argument("--variation", type=int, default=2, help="arrivals = capacity +/- this number")
    parser.add_argument("--seed", type=int, default=42, help="random seed (default: 42)")
    parser.add_argument("--feature-cost", default="1000",
                        help="fixed development cost per feature in euros (default: 1000)")
    parser.add_argument("--live", action="store_true", help="open the live player in your browser")
    parser.add_argument("--output-dir", type=Path, default=ROOT,
                        help="directory containing figures/ and results/ (default: repository root)")
    args = parser.parse_args()
    try:
        result = run_experiment(args.days, args.runs, args.capacity, args.variation,
                                args.seed, args.feature_cost)
    except ValueError as error:
        parser.error(str(error))
    write_results(result, args.output_dir / "results")
    plot_results(result, args.output_dir / "figures")
    live_path = write_live_view(result, args.output_dir / "figures")
    print(f"Fixed capacity: {args.capacity} features/day; expected arrivals: {args.capacity} features/day")
    print(f"Actual ensemble arrival average: {result['ensemble_arrivals_per_day']:.5f} features/day")
    print(f"Day {args.days}: simulation mean backlog = {result['mean'][-1]:.3f} features")
    print(f"Day {args.days}: exact expected backlog = {result['exact_mean'][-1]:.3f} features")
    print(f"Standard error of simulation mean: {result['sem'][-1]:.3f} features")
    print(f"Development cost per feature: €{result['parameters']['feature_cost_cents'] / 100:,.2f}")
    print(f"Sample final bound development cost: €{result['sample']['bound_cost_cents'][-1] / 100:,.2f}")
    print("A queue can empty again; positive variation at balanced mean capacity has no stationary backlog distribution."
          if args.variation else "Regular arrivals exactly match capacity; the end-of-day backlog stays zero.")
    print(f"Plots: {(args.output_dir / 'figures').resolve()}")
    print(f"Data:  {(args.output_dir / 'results').resolve()}")
    print(f"Live player: {live_path}")
    if args.live and not webbrowser.open(live_path.as_uri()):
        print("No browser opened automatically. Open the live player file above manually.")


if __name__ == "__main__":
    main()
