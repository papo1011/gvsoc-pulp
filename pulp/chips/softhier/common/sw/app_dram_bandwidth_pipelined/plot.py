#!/usr/bin/env python3
"""Plot pipelined contiguous read bandwidth around its plateau.

One panel with a narrow bandwidth axis, from half the plateau onset to
512 KiB, so the flatness of the plateau is visible; smaller sizes are in the
CSV. The onset uses the warm sweep's criterion (from there on every median is
at least 95% of the maximum); the plateau level is the mean of the last three
sizes, whose medians that criterion requires to span at most 3%.
"""

import argparse
import csv
from pathlib import Path
from statistics import mean
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_dram_bandwidth_warm.summarize import plateau

COLOR = "#287f8e"
PEAK_COLOR = "#ba6540"


def size_label(size):
    return f"{size // 1024} KiB" if size >= 1024 else f"{size} B"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="bandwidth.csv produced by summarize.py")
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).parent / "assets/bandwidth.png"
    )
    args = parser.parse_args()
    with args.csv.open() as source:
        rows = list(csv.DictReader(source))
    points = sorted(
        (
            {"bytes": int(row["bytes"]), "median_GB_per_s": float(row["median_GB_per_s"])}
            for row in rows
        ),
        key=lambda point: point["bytes"],
    )
    peak = float(rows[0]["nominal_peak_GB_per_s"])
    sizes = [point["bytes"] for point in points]
    onset = plateau(points)
    level = mean(point["median_GB_per_s"] for point in points[-3:]) if onset else None
    # From half the plateau onset (or the last four sizes) to the end.
    shown = [point for point in points if point["bytes"] >= (onset or sizes[-4]) / 2]
    x = [point["bytes"] for point in shown]
    y = [point["median_GB_per_s"] for point in shown]

    fig, ax = plt.subplots(figsize=(10, 5.5), layout="constrained")
    ax.plot(
        x,
        y,
        marker="o",
        color=COLOR,
        linewidth=1.8,
        markersize=5,
        label="Read (HBM → TCDM), median",
    )
    for size, rate in zip(x, y):
        ax.annotate(
            f"{rate:.2f}",
            (size, rate),
            xytext=(0, -9),
            textcoords="offset points",
            ha="center",
            va="top",
            fontsize=8,
            color=COLOR,
        )
    if onset:
        ax.axvspan(
            onset / 1.2,
            x[-1] * 1.3,
            color=COLOR,
            alpha=0.06,
            linewidth=0,
            label="Plateau region (≥ 95% of the measured maximum)",
        )
        ax.axhline(
            level,
            color=COLOR,
            linestyle=":",
            linewidth=1.4,
            label=f"Plateau from {size_label(onset)}: {level:.2f} GB/s",
        )
    ax.axhline(
        peak,
        color=PEAK_COLOR,
        linestyle="--",
        linewidth=1.2,
        label=f"Theoretical bandwidth ({peak:g} GB/s)",
    )

    low, high = min(y + [peak]), max(y + [peak])
    margin = max((high - low) * 0.12, 0.5)
    ax.set_xscale("log", base=2)
    ax.set_xticks(x, [size_label(size) for size in x])
    ax.set_xlim(x[0] / 1.3, x[-1] * 1.3)
    ax.set_ylim(low - margin, high + margin)
    ax.grid(True, alpha=0.2)
    ax.set_xlabel("Block size (8 consecutive blocks in flight per sample)")
    ax.set_ylabel("Useful bandwidth (GB/s)")
    ax.set_title("Single cluster, local HBM: pipelined contiguous DMA reads")
    ax.legend(loc="lower right", fontsize=9)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=160)
    fig.savefig(args.output.with_suffix(".svg"))
    plt.close(fig)
    print(args.output)
    if onset:
        print(f"Plateau from {onset} bytes at {level:.3f} GB/s")


if __name__ == "__main__":
    main()
