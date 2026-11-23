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


def dram_figure(curve, samples, output, title, color, label):
    """DRAM data-bus bandwidth against end-to-end bandwidth, and the time between them."""
    fig, (top, bottom) = plt.subplots(
        2,
        1,
        figsize=(10, 7.2),
        layout="constrained",
        sharex=True,
        gridspec_kw={"height_ratios": (3, 2)},
    )
    sizes = list(samples)
    bus = [median(sample["bus_GB_per_s"] for sample in samples[size]) for size in sizes]
    end_to_end = {row["bytes"]: row["median_GB_per_s"] for row in curve}
    top.plot(
        sizes,
        bus,
        marker="s",
        color=DRAM,
        label="DRAM data bus: bytes / (first burst start → last burst end)",
    )
    top.plot(
        sizes,
        [end_to_end[size] for size in sizes],
        marker="o",
        color=color,
        label=f"End to end: {label}",
    )
    peak_line(top)
    for size in (4096, sizes[-1]):
        index = sizes.index(size)
        label_point(top, size, bus[index], f"{bus[index]:.2f}", below=False, left=True)
        label_point(top, size, end_to_end[size], f"{end_to_end[size]:.2f}", left=size == sizes[-1])
    size_axis(top, label=False)
    bandwidth_axis(top)
    top.set_title(title)
    legend(top)

    outside = [median(sample["outside_ns"] for sample in samples[size]) for size in sizes]
    bottom.plot(sizes, outside, marker="o", color=OUTSIDE)
    large = [value for size, value in zip(sizes, outside) if size >= 4096]
    bottom.axhline(
        median(large),
        color=OUTSIDE,
        linestyle=":",
        linewidth=1.2,
        label=f"Median from 4 KiB: {median(large):.1f} ns",
    )
    size_axis(bottom)
    bottom.set_ylim(0, max(80, max(outside) * 1.15))
    bottom.set_xlabel("Block size")
    bottom.set_ylabel("End-to-end time minus\nDRAM bus window (ns)")
    bottom.legend(loc="lower right")
    save(fig, output)


def comparison_figure(series, output, title, relative=None, relative_label=None):
    """End-to-end bandwidth of several sweeps on one axis.

    series: (label, curve, color, marker) tuples; the first is the reference.
    relative: None, "percent" (difference from the reference) or "ratio"
    (series / reference), drawn in a second panel for the other series and
    labelled relative_label.
    """
    rows = 2 if relative else 1
    fig, axes = plt.subplots(
        rows,
        1,
        figsize=(10, 7.2 if relative else 5.2),
        layout="constrained",
        sharex=True,
        squeeze=False,
        gridspec_kw={"height_ratios": (3, 2)[:rows]},
    )
    top = axes[0][0]
    for label, curve, color, marker in series:
        top.plot(
            [row["bytes"] for row in curve],
            [row["median_GB_per_s"] for row in curve],
            marker=marker,
            color=color,
            label=label,
        )
    peak_line(top)
    size_axis(top, label=not relative)
    bandwidth_axis(top)
    top.set_title(title)
    legend(top)
    if not relative:
        top.set_xlabel("Block size")
        save(fig, output)
        return
    bottom = axes[1][0]
    reference = series[0][1]
    base = {row["bytes"]: row["median_GB_per_s"] for row in reference}
    for label, curve, color, marker in series[1:]:
        points = [row for row in curve if row["bytes"] in base]
        values = [
            row["median_GB_per_s"] / base[row["bytes"]]
            if relative == "ratio"
            else 100 * (row["median_GB_per_s"] / base[row["bytes"]] - 1)
            for row in points
        ]
        bottom.plot([row["bytes"] for row in points], values, marker=marker, color=color)
    bottom.axhline(1 if relative == "ratio" else 0, color=REFERENCE, linewidth=1)
    size_axis(bottom)
    bottom.set_xlabel("Block size")
    bottom.set_ylabel(relative_label)
    save(fig, output)


def breakdown_figure(panels, output, title):
    """Where the time of one sample goes, as shares of the end-to-end interval.

    panels: (title, samples) pairs, drawn side by side.
    """
    fig, axes = plt.subplots(
        1, len(panels), figsize=(12, 5), layout="constrained", sharey=True, squeeze=False
    )
    for ax, (panel_title, samples) in zip(axes[0], panels):
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
        ax.set_title(panel_title)
        ax.set_xlabel("Block size")
        ax.grid(False)
    axes[0][0].set_ylabel("Share of the end-to-end time (%)")
    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="outside lower center", ncols=3)
    fig.suptitle(title)
    save(fig, output)
