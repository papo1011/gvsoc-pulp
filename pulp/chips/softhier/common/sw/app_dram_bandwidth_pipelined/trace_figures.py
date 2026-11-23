"""Figures drawn from a request trace extracted by query_trace.py.

bus_timeline_figure: every burst on the DRAM data bus of each pseudo-channel,
coloured by bank group, with the spacing rule (tCCD_S or tCCD_L) that applied
between consecutive bursts and every overlap marked.

pipeline_figure: one row per request of one pseudo-channel, from its arrival at
the controller to the end of its burst.
"""

import csv
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_dram_bandwidth_pipelined.query_trace import bursts
from dram_common import plot_sweep as plot

import matplotlib.patches as patches
import matplotlib.pyplot as plt

# Four hues for eight bank groups: a figure shows at most four neighbouring
# groups of one half (0-3 or 4-7) plus, around an 8 KiB boundary, the other
# half. The group number is also written inside every burst.
GROUP_COLORS = (plot.SEQUENTIAL, plot.PIPELINED, plot.DRAM, plot.OUTSIDE)
OVERLAP = "#c0392b"
QUEUE = "#d9d9d9"
LATENCY = "#9a9a9a"


def load_trace(path):
    """Requests per (bytes, sample), as written by query_trace.py (times in ps)."""
    samples = {}
    with Path(path).open() as source:
        for row in csv.DictReader(source):
            request = {key: int(value) for key, value in row.items() if value != ""}
            samples.setdefault((request["bytes"], request["sample"]), []).append(request)
    return samples


def ns(ps):
    return ps / 1000


def bank_groups(requests):
    """Colour per bank group, and legend labels for the groups sharing each colour."""
    groups = sorted({r["bank_group"] for r in requests})
    colors = {group: GROUP_COLORS[group % len(GROUP_COLORS)] for group in groups}
    labels = {}
    for group in groups:
        labels.setdefault(colors[group], []).append(str(group))
    legend = [
        patches.Patch(color=color, label=f"Bank group {' and '.join(names)}")
        for color, names in labels.items()
    ]
    return colors, legend


def lanes(channel_bursts):
    """Lowest lane where each burst does not overlap the burst before it there."""
    ends = []
    result = []
    for burst in channel_bursts:
        lane = next((i for i, end in enumerate(ends) if end <= burst["burst_begin_ps"]), len(ends))
        if lane == len(ends):
            ends.append(0)
        ends[lane] = burst["burst_end_ps"]
        result.append(lane)
    return result


def bus_timeline_figure(requests, output, title, xlim=None):
    """Bursts of both pseudo-channels; a second lane holds bursts that overlap another.

    With xlim the figure is a zoom: time 0 is still the first burst of the
    extracted requests, and the per-channel totals are left out.
    """
    colors, legend = bank_groups(requests)
    channels = sorted({r["pseudo_channel"] for r in requests})
    t0 = min(ns(r["burst_begin_ps"]) for r in requests)
    fig, ax = plt.subplots(figsize=(14, 1.4 + 2.0 * len(channels)), layout="constrained")
    ax.grid(False)
    height, step = 0.7, 0.8
    for position, pseudo_channel in enumerate(channels):
        base = (len(channels) - 1 - position) * 2.4
        channel_bursts = bursts(requests, pseudo_channel)
        for burst, lane in zip(channel_bursts, lanes(channel_bursts)):
            begin = ns(burst["burst_begin_ps"]) - t0
            width = ns(burst["burst_end_ps"] - burst["burst_begin_ps"])
            y = base + lane * step
            if burst.get("overlap"):
                # Two bursts on the data bus at the same time.
                previous_end = begin - ns(burst["gap_ps"]) + width
                ax.add_patch(
                    patches.Rectangle(
                        (begin, base - 0.05),
                        previous_end - begin,
                        step + height + 0.1,
                        facecolor=OVERLAP,
                        alpha=0.3,
                        linewidth=0,
                        zorder=0,
                    )
                )
            ax.add_patch(
                patches.Rectangle(
                    (begin, y),
                    width,
                    height,
                    facecolor=colors[burst["bank_group"]],
                    edgecolor="white",
                    linewidth=1,
                )
            )
            ax.text(
                begin + width / 2,
                y + height / 2,
                str(burst["bank_group"]),
                ha="center",
                va="center",
                fontsize=6.5,
                color="white",
            )
            if burst.get("activate_ps", "") != "":
                ax.plot(
                    begin + width / 2,
                    y + height + 0.08,
                    marker="v",
                    markersize=4,
                    color=plot.INK,
                    linestyle="",
                )
            if "rule" in burst:
                ax.text(
                    begin + width / 2,
                    base - 0.3,
                    burst["rule"],
                    ha="center",
                    va="center",
                    fontsize=7,
                    color=OVERLAP if burst["overlap"] else plot.INK,
                    fontweight="bold" if burst["overlap"] else "normal",
                )
        ax.text(
            (xlim[0] if xlim else -0.5) - 0.7,
            base + height / 2,
            f"PC {pseudo_channel}",
            ha="right",
            va="center",
            fontsize=10,
            color=plot.INK,
        )
        if not xlim:
            first = ns(channel_bursts[0]["burst_begin_ps"]) - t0
            end = ns(channel_bursts[-1]["burst_end_ps"]) - t0
            ideal = first + len(channel_bursts)
            ax.plot([ideal, ideal], [base - 0.1, base + 1.6], color=plot.REFERENCE, linestyle="--")
            ax.text(
                max(end, ideal) + 0.6,
                base + height / 2,
                f"{len(channel_bursts)} bursts in {end - first:g} ns",
                va="center",
                fontsize=9,
                color=plot.INK,
            )
    legend.append(patches.Patch(color=OVERLAP, alpha=0.3, label="Two bursts on the bus at once"))
    legend.append(
        plt.Line2D(
            [], [], color=plot.INK, marker="v", markersize=4, linestyle="", label="Row opened (ACT)"
        )
    )
    if not xlim:
        legend.append(
            plt.Line2D(
                [], [], color=plot.REFERENCE, linestyle="--", label="End at one burst per ns"
            )
        )
    ax.legend(handles=legend, loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=4)
    last = max(ns(r["burst_end_ps"]) for r in requests) - t0
    ax.set_xlim(*(xlim or (-6, last + 14)))
    ax.set_ylim(-0.6, (len(channels) - 1) * 2.4 + step + height + 0.2)
    ax.set_yticks([])
    ax.spines[["left", "right", "top"]].set_visible(False)
    ax.set_xlabel(
        "Time (ns).  Under each burst, the rule that spaced it from the previous one: "
        "S = other bank group (tCCD_S), L = same bank group (tCCD_L)"
    )
    ax.set_title(title)
    plot.save(fig, output)


def pipeline_figure(requests, output, title, pseudo_channel=0):
    colors, groups = bank_groups(requests)
    rows = sorted(
        (r for r in requests if r["pseudo_channel"] == pseudo_channel),
        key=lambda r: (r["generated_ps"], r["burst_begin_ps"]),
    )
    t0 = min(ns(r["burst_begin_ps"]) for r in rows)
    fig, ax = plt.subplots(figsize=(12, 9), layout="constrained")
    ax.set_axisbelow(True)
    for index, request in enumerate(rows):
        generated = ns(request["generated_ps"]) - t0
        command = ns(request["read_command_ps"]) - t0
        begin = ns(request["burst_begin_ps"]) - t0
        end = ns(request["burst_end_ps"]) - t0
        ax.barh(index, command - generated, left=generated, height=0.8, color=QUEUE)
        ax.barh(index, begin - command, left=command, height=0.8, color=LATENCY)
        ax.barh(index, end - begin, left=begin, height=0.8, color=colors[request["bank_group"]])
        if request.get("activate_ps", "") != "":
            ax.plot(
                ns(request["activate_ps"]) - t0, index, marker="|", color=plot.INK, markersize=8
            )
    handles = [
        patches.Patch(color=QUEUE, label="Waiting in the controller"),
        patches.Patch(color=LATENCY, label="Read command to data"),
        *groups,
        plt.Line2D([], [], color=plot.INK, marker="|", linestyle="", label="Row opened (ACT)"),
    ]
    ax.legend(handles=handles, loc="lower left")
    ax.invert_yaxis()
    ax.set_ylabel(f"Request on pseudo-channel {pseudo_channel}, in arrival order")
    ax.set_xlabel("Time since the first burst (ns)")
    ax.set_title(title)
    plot.save(fig, output)
