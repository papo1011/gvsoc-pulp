#!/usr/bin/env python3
"""Draw the embeddings sweep's figures, one chart per image, from the CSVs in a directory
(by default assets/data/, into assets/figures/).

  bandwidth.png     end-to-end bandwidth against the piece read per token, one line per order
  row_openings.png  32 B bursts read per DRAM row opening (needs dram.csv)
"""

import argparse
import csv
from pathlib import Path
from statistics import median
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dram_common import plot_sweep as plot

plt = plot.plt
HERE = Path(__file__).resolve().parent
DATA, FIGURES = HERE / "assets/data", HERE / "assets/figures"
PIECES = (256, 512, 1024, 2048, 4096, 8192)
XLIM = (PIECES[0] / 1.3, PIECES[-1] * 1.3)
TFAW_NS = 15  # memspec FAW = 60 cycles of 0.25 ns
# Orders in fixed order with fixed colors, so an order keeps its color in every figure.
ORDERS = {
    "block": ("Contiguous rows, one DMA (reference)", "#6f6f6f", "--", "s"),
    "contiguous": ("Contiguous rows, one DMA per token", "#2a78d6", "-", "o"),
    "sorted": ("Random rows, sorted", "#1baf7a", "-", "D"),
    "random": ("Random rows (embedding lookup)", "#eb6834", "-", "o"),
}


def number(value):
    for kind in (int, float):
        try:
            return kind(value)
        except ValueError:
            pass
    return value


def load(path):
    with Path(path).open() as source:
        return [
            {key: number(value) for key, value in row.items()} for row in csv.DictReader(source)
        ]


def axes(title, ylabel):
    figure, axis = plt.subplots(figsize=(8, 4.6))
    axis.set_xscale("log", base=2)
    axis.set_xticks(PIECES, [plot.size_label(piece) for piece in PIECES])
    axis.minorticks_off()
    axis.set_xlim(*XLIM)
    axis.set_xlabel("Bytes read per token (8 KiB row split over 32 ... 1 channels)")
    axis.set_ylabel(ylabel)
    axis.set_title(title, loc="left")
    return figure, axis


def save(figure, axis, path, legend="lower right"):
    axis.legend(loc=legend)
    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=160)
    figure.savefig(path.with_suffix(".svg"), metadata={"Date": None})
    plt.close(figure)
    print(path)


def draw(axis, rows, order, key):
    label, color, style, marker = ORDERS[order]
    points = sorted((row["piece"], row[key]) for row in rows if row["order"] == order)
    axis.plot(
        [piece for piece, _ in points],
        [value for _, value in points],
        style,
        color=color,
        marker=marker,
        label=label,
    )


def bandwidth_figure(rows, path):
    figure, axis = axes(
        "Embedding lookup, 64 tokens, one cluster and its HBM channel", "Bandwidth (GB/s)"
    )
    for order in ORDERS:
        draw(axis, rows, order, "median_GB_per_s")
    axis.axhline(
        plot.PEAK_GB_PER_S,
        color=plot.REFERENCE,
        linewidth=1,
        linestyle="--",
        label=f"Channel peak ({plot.PEAK_GB_PER_S:g} GB/s)",
    )
    axis.set_ylim(0, 70)
    save(figure, axis, path)


def row_openings_figure(dram, path):
    per_point = {}
    for row in dram:
        per_point.setdefault((row["piece"], row["order"]), []).append(row["bursts_per_activate"])
    rows = [
        {"piece": piece, "order": order, "bursts_per_activate": median(values)}
        for (piece, order), values in per_point.items()
    ]
    figure, axis = axes("32 B bursts read per DRAM row opening", "Bursts per row opening")
    for order in ORDERS:
        draw(axis, rows, order, "bursts_per_activate")
    # Four row openings per tFAW (15 ns) on each of the two pseudo-channels: below this
    # many bursts per opening, the channel cannot reach its 64 GB/s.
    limit = plot.PEAK_GB_PER_S * TFAW_NS / (2 * 4 * 32)
    axis.axhline(
        limit,
        color=plot.REFERENCE,
        linewidth=1,
        linestyle="--",
        label=f"Below: tFAW keeps the channel under {plot.PEAK_GB_PER_S:g} GB/s",
    )
    axis.set_yscale("log", base=2)
    axis.set_yticks([1, 2, 4, 8, 16, 32], ["1", "2", "4", "8", "16", "32"])
    axis.set_ylim(1, 48)
    save(figure, axis, path, legend="lower right")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "directory",
        type=Path,
        nargs="?",
        default=DATA,
        help="directory with the CSVs (default: this app's assets/data/)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="where to write the figures (default: assets/figures/ for the app's own"
        " data, the CSV directory otherwise)",
    )
    args = parser.parse_args()
    data = args.directory
    out = args.output_dir or (FIGURES if data.resolve() == DATA else data)
    bandwidth_figure(load(data / "bandwidth.csv"), out / "bandwidth.png")
    if (data / "dram.csv").exists():
        row_openings_figure(load(data / "dram.csv"), out / "row_openings.png")


if __name__ == "__main__":
    main()
