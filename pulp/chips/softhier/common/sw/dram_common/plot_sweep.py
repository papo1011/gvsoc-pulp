#!/usr/bin/env python3
"""Shared figures for the single-cluster HBM read sweeps.

Every figure uses the same block-size axis, 4 B to 1 MiB on a log2 scale, so
figures of different sweeps and simulator configurations can be compared
directly. The apps' plot.py scripts choose which figures to draw.

Inputs:
  bandwidth.csv  summarize.py output: median, min and max end-to-end bandwidth
                 per size (payload bytes / core-timed interval).
  dram.csv       query_dram.py output: DRAMSys traffic of every sample, with the
                 data-bus window from the first burst's start to the last
                 burst's end and the bus occupancy inside it.
"""

import csv
from pathlib import Path
from statistics import linear_regression, median
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_dram_bandwidth_warm.summarize import plateau

PEAK_GB_PER_S = 64.0
FIRST_BYTES, LAST_BYTES = 4, 1 << 20
TICKS = [4**power for power in range(1, 11)]  # 4 B, 16 B, ..., 1 MiB
XLIM = (FIRST_BYTES / 1.4, LAST_BYTES * 1.4)

STREAMING = "#2a78d6"
PIPELINED = "#eb6834"
DRAM = "#1baf7a"
IDLE = "#eda100"
OUTSIDE = "#6250d6"
OLDER = ("#6f6f6f", "#a3a3a3")
INK = "#3a3a3a"
REFERENCE = "#555555"

plt.rcParams.update(
    {
        "font.size": 11,
        "axes.titlesize": 12,
        "axes.edgecolor": "#888888",
        "axes.labelcolor": INK,
        "xtick.color": INK,
        "ytick.color": INK,
        "axes.grid": True,
        "grid.color": "#e4e4e4",
        "grid.linewidth": 0.8,
        "legend.fontsize": 10,
        "legend.frameon": False,
        "lines.linewidth": 2,
        "lines.markersize": 5,
        "savefig.facecolor": "white",
    }
)


def size_label(size):
    if size >= 1 << 20:
        return f"{size >> 20} MiB"
    return f"{size >> 10} KiB" if size >= 1024 else f"{size} B"


def load_curve(path):
    """Sorted rows of a bandwidth.csv with numeric fields."""
    with Path(path).open() as source:
        rows = [
            {
                "bytes": int(row["bytes"]),
                "median_GB_per_s": float(row["median_GB_per_s"]),
                "min_GB_per_s": float(row["min_GB_per_s"]),
                "max_GB_per_s": float(row["max_GB_per_s"]),
                "median_ns_per_transfer": float(row["median_ns_per_transfer"]),
            }
            for row in csv.DictReader(source)
        ]
    return sorted(rows, key=lambda row: row["bytes"])


def load_samples(path):
    """Per-size lists of DRAM samples with the derived time split.

    The end-to-end interval is recomputed from DRAM bytes and dram_GB_per_s,
    which query_dram.py derives from that same interval. Bus time is busy time
    per pseudo-channel; idle is the rest of the bus window; outside is the part
    of the interval before the first burst and after the last one.
    """
    samples = {}
    with Path(path).open() as source:
        for row in csv.DictReader(source):
            if row["command"] != "R":
                continue
            dram_bytes = int(row["dram_bytes"])
            end_to_end = dram_bytes / float(row["dram_GB_per_s"])
            window = float(row["bus_window_ns"])
            busy = window * float(row["bus_occupancy_percent"]) / 100
            samples.setdefault(int(row["bytes"]), []).append(
                {
                    "bus_GB_per_s": float(row["bus_GB_per_s"]),
                    "end_to_end_ns": end_to_end,
                    "window_ns": window,
                    "outside_ns": end_to_end - window,
                    "busy_ns": busy,
                    "idle_ns": window - busy,
                }
            )
    return dict(sorted(samples.items()))


def representative(samples):
    """The sample with the median end-to-end time (three samples per size)."""
    ordered = sorted(samples, key=lambda sample: sample["end_to_end_ns"])
    return ordered[len(ordered) // 2]


def fit(curve):
    """Latency-bandwidth fit T(N) = alpha + N / B on the 64, 128 and 256 KiB copies."""
    sizes = (64 * 1024, 128 * 1024, 256 * 1024)
    times = {row["bytes"]: row["median_ns_per_transfer"] for row in curve}
    if any(size not in times for size in sizes):
        raise ValueError("The fit requires the 64, 128 and 256 KiB measurements")
    beta, alpha = linear_regression(sizes, [times[size] for size in sizes])
    if beta <= 0:
        raise ValueError("The fitted time-per-byte slope must be positive")
    return alpha, 1 / beta


def size_axis(ax, label=True):
    ax.set_xscale("log", base=2)
    ax.set_xticks(TICKS, [size_label(size) for size in TICKS] if label else [])
    ax.minorticks_off()
    ax.set_xlim(*XLIM)


def peak_line(ax):
    ax.axhline(
        PEAK_GB_PER_S,
        color=REFERENCE,
        linestyle="--",
        linewidth=1.2,
        label=f"Theoretical channel peak ({PEAK_GB_PER_S:g} GB/s)",
    )


def bandwidth_axis(ax):
    ax.set_ylim(0, 70)
    ax.set_yticks(range(0, 71, 10))
    ax.set_ylabel("Bandwidth (GB/s)")


def label_point(ax, size, value, text, color=INK, below=True, left=False):
    """Direct label for a few chosen points: below-right by default."""
    ax.annotate(
        text,
        (size, value),
        xytext=(-6 if left else 6, -8 if below else 8),
        textcoords="offset points",
        ha="right" if left else "left",
        va="top" if below else "bottom",
        fontsize=9,
        color=color,
    )


def legend(ax):
    """Bottom right, under the plateau, where every rising curve leaves room."""
    ax.legend(loc="lower right")


def save(fig, output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=200)
    fig.savefig(output.with_suffix(".svg"))
    plt.close(fig)
    print(output)


def bandwidth_figure(curve, output, title, color, label, with_fit=False):
    """End-to-end bandwidth over the whole sweep, with its plateau region."""
    x = [row["bytes"] for row in curve]
    y = [row["median_GB_per_s"] for row in curve]
    onset = plateau(curve)
    fig, ax = plt.subplots(figsize=(10, 5.2), layout="constrained")
    if onset:
        ax.axvspan(
            onset / 1.25,
            XLIM[1],
            color="#000000",
            alpha=0.045,
            linewidth=0,
            label="Plateau region (every median ≥ 95% of the maximum)",
        )
    if with_fit:
        alpha, limit = fit(curve)
        last = x[-1].bit_length() - 1
        sizes = [2 ** (power / 8) for power in range(8 * 2, 8 * last + 1)]
        ax.plot(
            sizes,
            [size / (alpha + size / limit) for size in sizes],
            color=INK,
            linestyle=":",
            linewidth=1.4,
            label=f"Fit N / (α + N/B): α = {alpha:.1f} ns, B = {limit:.2f} GB/s",
        )
    ax.fill_between(
        x,
        [row["min_GB_per_s"] for row in curve],
        [row["max_GB_per_s"] for row in curve],
        color=color,
        alpha=0.18,
        linewidth=0,
    )
    ax.plot(x, y, marker="o", color=color, label=label)
    peak_line(ax)
    for size, rate in zip(x, y):
        if size == x[-1]:
            label_point(ax, size, rate, f"{size_label(size)}: {rate:.2f}", below=False, left=True)
        elif size in (4096, onset):
            label_point(ax, size, rate, f"{size_label(size)}: {rate:.2f}")
    size_axis(ax)
    bandwidth_axis(ax)
    ax.set_xlabel("Block size")
    ax.set_title(title)
    legend(ax)
    save(fig, output)
    return onset


def dram_figure(curve, samples, output, title, color, label, bus_label=None):
    """DRAM data-bus bandwidth against end-to-end bandwidth of the same samples."""
    fig, ax = plt.subplots(figsize=(10, 5.2), layout="constrained")
    sizes = list(samples)
    bus = [median(sample["bus_GB_per_s"] for sample in samples[size]) for size in sizes]
    end_to_end = {row["bytes"]: row["median_GB_per_s"] for row in curve}
    ax.plot(
        sizes,
        bus,
        marker="s",
        color=DRAM,
        label=bus_label or "DRAM data bus: bytes / (first burst start → last burst end)",
    )
    ax.plot(
        sizes,
        [end_to_end[size] for size in sizes],
        marker="o",
        color=color,
        label=f"End to end: {label}",
    )
    peak_line(ax)
    for size in (4096, sizes[-1]):
        index = sizes.index(size)
        label_point(ax, size, bus[index], f"{bus[index]:.2f}", below=False, left=True)
        label_point(ax, size, end_to_end[size], f"{end_to_end[size]:.2f}", left=size == sizes[-1])
    size_axis(ax)
    bandwidth_axis(ax)
    ax.set_xlabel("Block size")
    ax.set_title(title)
    legend(ax)
    save(fig, output)


def fixed_cost_figure(samples, output, title):
    """Time of each sample outside the DRAM bus window: end-to-end interval minus window."""
    fig, ax = plt.subplots(figsize=(10, 5.2), layout="constrained")
    sizes = list(samples)
    outside = [median(sample["outside_ns"] for sample in samples[size]) for size in sizes]
    ax.plot(
        sizes,
        outside,
        marker="o",
        color=OUTSIDE,
        label="Before the first burst and after the last, median of 3",
    )
    large = median(value for size, value in zip(sizes, outside) if size >= 4096)
    ax.axhline(
        large,
        color=OUTSIDE,
        linestyle=":",
        linewidth=1.2,
        label=f"Median from 4 KiB: {large:.1f} ns",
    )
    size_axis(ax)
    ax.set_ylim(0, max(80, max(outside) * 1.15))
    ax.set_xlabel("Block size")
    ax.set_ylabel("End-to-end time minus DRAM bus window (ns)")
    ax.set_title(title)
    ax.legend(loc="lower right")
    save(fig, output)


def comparison_figure(series, output, title, value="median_GB_per_s"):
    """Bandwidth of several sweeps on one axis.

    series: (label, rows, color, marker) tuples, rows with "bytes" and `value`.
    """
    fig, ax = plt.subplots(figsize=(10, 5.2), layout="constrained")
    for label, rows, color, marker in series:
        ax.plot(
            [row["bytes"] for row in rows],
            [row[value] for row in rows],
            marker=marker,
            color=color,
            label=label,
        )
    peak_line(ax)
    size_axis(ax)
    bandwidth_axis(ax)
    ax.set_xlabel("Block size")
    ax.set_title(title)
    legend(ax)
    save(fig, output)


def bus_rows(samples):
    """Median DRAM bus bandwidth per size, shaped like curve rows."""
    return [
        {"bytes": size, "bus_GB_per_s": median(s["bus_GB_per_s"] for s in group)}
        for size, group in samples.items()
    ]


def ratio_figure(reference, curve, output, title, ylabel, color):
    """Bandwidth of `curve` divided by that of `reference`, per size."""
    base = {row["bytes"]: row["median_GB_per_s"] for row in reference}
    points = [row for row in curve if row["bytes"] in base]
    ratios = [row["median_GB_per_s"] / base[row["bytes"]] for row in points]
    fig, ax = plt.subplots(figsize=(10, 5.2), layout="constrained")
    ax.plot([row["bytes"] for row in points], ratios, marker="s", color=color)
    ax.axhline(1, color=REFERENCE, linewidth=1)
    for row, ratio in zip(points, ratios):
        if row["bytes"] in (256, 4096, 65536):
            label_point(
                ax, row["bytes"], ratio, f"{size_label(row['bytes'])}: {ratio:.2f}×", below=False
            )
    size_axis(ax)
    ax.set_ylim(0, max(ratios) * 1.12)
    ax.set_xlabel("Block size")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    save(fig, output)


def breakdown_figure(samples, output, title):
    """Where the time of one sample goes, as shares of the end-to-end interval."""
    fig, ax = plt.subplots(figsize=(10, 5.6), layout="constrained")
    sizes = list(samples)
    chosen = [representative(samples[size]) for size in sizes]
    shares = {
        key: [100 * sample[key] / sample["end_to_end_ns"] for sample in chosen]
        for key in ("busy_ns", "idle_ns", "outside_ns")
    }
    ax.stackplot(
        sizes,
        shares["busy_ns"],
        shares["idle_ns"],
        shares["outside_ns"],
        colors=(DRAM, IDLE, OUTSIDE),
        edgecolor="white",
        linewidth=0.6,
        labels=(
            "DRAM bus transferring data",
            "DRAM bus idle between the first and last burst",
            "Before the first burst and after the last (on-chip path)",
        ),
    )
    size_axis(ax)
    ax.set_ylim(0, 100)
    ax.set_xlabel("Block size")
    ax.set_ylabel("Share of the end-to-end time (%)")
    ax.set_title(title)
    ax.grid(False)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.13), ncols=2, frameon=False)
    save(fig, output)
