"""Slide-sized figure drawn from a request trace extracted by query_trace.py.

pipeline_figure: the first copies of a sample, one row per 32 B request of one
pseudo-channel, from its arrival at the DRAM controller to the end of its
burst on the data bus. A burst that started while the previous burst of the
same pseudo-channel was still on its wires is drawn in red.
"""

import csv
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_dram_bandwidth_pipelined.query_trace import bursts
from dram_common import plot_sweep as plot

import matplotlib.patches as patches
import matplotlib.pyplot as plt

QUEUE = "#d9d9d9"
LATENCY = "#9a9a9a"
BURST = plot.SEQUENTIAL
OVERLAP = "#c0392b"
FONT = 15


def load_trace(path):
    """Requests per (bytes, sample), as written by query_trace.py (times in ps)."""
    samples = {}
    with Path(path).open() as source:
        for row in csv.DictReader(source):
            request = {key: int(value) for key, value in row.items() if value != ""}
            samples.setdefault((request["bytes"], request["sample"]), []).append(request)
    return samples


def first_copies(requests, copies, pseudo_channel=0):
    """Requests of the first copies on one pseudo-channel, in arrival order.

    The requests of one copy reach the controller together, so a copy is a
    group of requests with the same arrival time. Each request carries
    `overlap`, computed over the whole pseudo-channel.
    """
    channel = {b["request"]: b for b in bursts(requests, pseudo_channel)}
    arrivals = sorted({r["generated_ps"] for r in channel.values()})[:copies]
    rows = sorted(
        (r for r in channel.values() if r["generated_ps"] in arrivals),
        key=lambda r: (r["generated_ps"], r["burst_begin_ps"]),
    )
    return rows, arrivals


def time_range(rows):
    """First arrival and last burst end, in ns from the first burst."""
    t0 = min(r["burst_begin_ps"] for r in rows)
    return (
        (min(r["generated_ps"] for r in rows) - t0) / 1000,
        (max(r["burst_end_ps"] for r in rows) - t0) / 1000,
    )


def pipeline_figure(requests, output, title, copies=3, xlim=None):
    rows, arrivals = first_copies(requests, copies)
    t0 = min(r["burst_begin_ps"] for r in rows)
    fig, ax = plt.subplots(figsize=(12, 6.8), layout="constrained")
    ax.set_axisbelow(True)
    ax.grid(axis="y", visible=False)
    for index, request in enumerate(rows):
        generated, command, begin, end = (
            (request[key] - t0) / 1000
            for key in ("generated_ps", "read_command_ps", "burst_begin_ps", "burst_end_ps")
        )
        ax.barh(index, command - generated, left=generated, height=0.75, color=QUEUE)
        ax.barh(index, begin - command, left=command, height=0.75, color=LATENCY)
        ax.barh(
            index,
            end - begin,
            left=begin,
            height=0.75,
            color=OVERLAP if request.get("overlap") else BURST,
        )
    # One label per copy, centred on its block of requests.
    centres, labels = [], []
    for number, arrival in enumerate(arrivals, 1):
        indices = [i for i, r in enumerate(rows) if r["generated_ps"] == arrival]
        centres.append(sum(indices) / len(indices))
        labels.append(f"copy {number}")
    ax.set_yticks(centres, labels, fontsize=FONT)
    ax.tick_params(axis="y", length=0)
    ax.tick_params(axis="x", labelsize=FONT - 2)
    ax.invert_yaxis()
    handles = [
        patches.Patch(color=QUEUE, label="Waiting in the controller"),
        patches.Patch(color=LATENCY, label="Read command to data"),
        patches.Patch(color=BURST, label="Burst on the data bus (1 ns)"),
    ]
    if any(r.get("overlap") for r in rows):
        handles.append(patches.Patch(color=OVERLAP, label="Burst overlapping another burst"))
    ax.legend(handles=handles, loc="lower left", fontsize=FONT - 2)
    ax.set_xlim(*(xlim or time_range(rows)))
    ax.set_xlabel("Time since the first burst (ns)", fontsize=FONT)
    ax.set_title(title, fontsize=FONT + 2)
    plot.save(fig, output)
