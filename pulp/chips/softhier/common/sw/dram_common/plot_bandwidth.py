#!/usr/bin/env python3
"""Plot measured DMA bandwidth against the theoretical bandwidth reference."""

import argparse
import csv
from pathlib import Path
from statistics import linear_regression
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_dram_bandwidth_warm.summarize import plateau


def estimate_limit(rows):
    """Fit 64/128/256 KiB times; larger available points remain outside the fit."""
    sizes = (64 * 1024, 128 * 1024, 256 * 1024)
    times = {int(row["bytes"]): float(row["median_ns_per_transfer"]) for row in rows}
    if any(size not in times for size in sizes):
        raise ValueError("The asymptotic fit requires the 64, 128 and 256 KiB measurements")
    beta, alpha = linear_regression(sizes, [times[size] for size in sizes])
    if beta <= 0:
        raise ValueError("The fitted time-per-byte slope must be positive")
    return 1 / beta, alpha


def main(pattern, assets):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="bandwidth.csv produced by summarize.py")
    parser.add_argument("--output", type=Path, default=Path(assets) / "bandwidth.png")
    args = parser.parse_args()
    with args.csv.open() as source:
        all_rows = list(csv.DictReader(source))
    limit, alpha = estimate_limit(all_rows)
    onset = plateau(
        sorted(
            (
                {"bytes": int(row["bytes"]), "median_GB_per_s": float(row["median_GB_per_s"])}
                for row in all_rows
            ),
            key=lambda point: point["bytes"],
        )
    )
    rows = sorted(
        (row for row in all_rows if 1024 <= int(row["bytes"]) <= 128 * 1024),
        key=lambda row: int(row["bytes"]),
    )
    x = [int(row["bytes"]) / 1024 for row in rows]
    y = [float(row["median_GB_per_s"]) for row in rows]
    peak = float(rows[0]["nominal_peak_GB_per_s"])
    fig, ax = plt.subplots(figsize=(12, 5), layout="constrained")
    ax.plot(x, y, "o-", color="#287f8e", label="Measured DMA read bandwidth (median)")
    # Below and right of each point, away from the rising curve; the last
    # point is labelled on its left to stay inside the axes.
    for size, rate in zip(x, y):
        last = size == x[-1]
        ax.annotate(
            f"{rate:.2f}",
            (size, rate),
            xytext=(-6 if last else 6, -6),
            textcoords="offset points",
            ha="right" if last else "left",
            va="top",
            fontsize=9,
            color="#287f8e",
        )
    if onset and onset <= 128 * 1024:
        ax.axvspan(
            onset / 1024 - 4,
            133,
            color="#287f8e",
            alpha=0.06,
            linewidth=0,
            label="Plateau region (≥ 95% of the measured maximum)",
        )
    ax.axhline(peak, color="#ba6540", linestyle="--", label="Theoretical bandwidth")
    ax.axhline(
        limit, color="#8660a8", linestyle=":", linewidth=1.5, label="Estimated asymptotic bandwidth"
    )
    best = max(y)
    ax.set(
        xlabel="DMA transfer size (KiB)",
        ylabel="Useful bandwidth (GB/s)",
        title=f"Single cluster, local HBM: {pattern} read bandwidth",
        xlim=(0, 133),
        ylim=(0, max(peak, best) * 1.13),
    )
    ax.set_xticks(x, [f"{size:g}" for size in x], rotation=0, ha="center", fontsize=9)
    for size, label in zip(x, ax.get_xticklabels()):
        if size == 1:
            label.set_ha("right")
        elif size == 2:
            label.set_ha("left")
    # Replace nearby standard ticks with the estimate to keep labels legible.
    # When the estimate is too close to the theoretical reference for two axis
    # labels, write its value next to its line instead.
    close = abs(limit - peak) < 3
    ticks = sorted(
        {value for value in range(0, 71, 10) if abs(value - limit) >= 3 and abs(value - peak) >= 3}
        | {peak}
        | (set() if close else {limit})
    )
    if close:
        ax.annotate(
            f"{limit:.2f}",
            (1, limit),
            xycoords=("axes fraction", "data"),
            xytext=(-4, 3),
            textcoords="offset points",
            ha="right",
            va="bottom",
            fontsize=9,
            fontweight="bold",
            color="#8660a8",
        )
    ax.set_yticks(ticks, [f"{value:.2f}" if value == limit else f"{value:g}" for value in ticks])
    for tick, label in zip(ax.get_yticks(), ax.get_yticklabels()):
        if tick == peak:
            label.set_color("#ba6540")
            label.set_fontweight("bold")
        elif tick == limit:
            label.set_color("#8660a8")
            label.set_fontweight("bold")
    ax.grid(True, alpha=0.2)
    ax.legend(loc="lower right", fontsize=9)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=180)
    fig.savefig(args.output.with_suffix(".svg"))
    plt.close(fig)
    print(args.output)
    print(f"Estimated asymptote: {limit:.6f} GB/s; effective intercept: {alpha:.3f} ns")
