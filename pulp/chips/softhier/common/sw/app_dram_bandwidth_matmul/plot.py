#!/usr/bin/env python3
"""Draw the matmul sweep's figures, one chart per image, from the CSVs in a directory
(by default assets/data/hbm4/, into assets/figures/).

  bandwidth.png    end-to-end bandwidth against run, one line per stride
  padding.png      power-of-two strides against the same strides padded by 64 B
  row_openings.png DRAM bursts per row opening (needs dram.csv)
  timings.png      stride 8 KiB with the HBM4 and the old emulation timings
                   (default data only, needs assets/data/ccds4/)
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
ROOT, FIGURES = HERE / "assets/data", HERE / "assets/figures"
DATA = ROOT / "hbm4"
RUNS = (32, 64, 128, 256, 512, 1024, 2048)
XLIM = (RUNS[0] / 1.3, RUNS[-1] * 1.3)
PACKED = "#6f6f6f"
# Strides in fixed order with fixed colors, so a stride keeps its color in every figure.
STRIDES = {
    2048: ("2 KiB (rows of 1024)", "#2a78d6"),
    4096: ("4 KiB (rows of 2048)", "#1baf7a"),
    6144: ("6 KiB (rows of 3072)", "#eda100"),
    8192: ("8 KiB (rows of 4096)", "#eb6834"),
    28672: ("28 KiB (rows of 14336)", "#6250d6"),
}
PADDED = {8256: 8192, 28736: 28672}
# DRAM timing sets, one directory each in assets/data/, in the order of the legend.
MEMSPECS = {
    "ccds4": ("Old emulation timings", "#6f6f6f", "--"),
    "hbm4": ("HBM4 timings (repository config)", "#eb6834", "-"),
}


def stride_label(stride):
    if stride in STRIDES:
        return STRIDES[stride][0]
    return f"{plot.size_label(PADDED[stride])} + 64 B"


def load(path):
    with Path(path).open() as source:
        return [
            {key: float(value) if "GB" in key else int(value) for key, value in row.items()}
            for row in csv.DictReader(source)
        ]


def series(rows, stride):
    """(run, row) pairs of one stride; stride None selects the packed control."""
    if stride is None:
        chosen = [row for row in rows if row["packed"]]
    else:
        chosen = [row for row in rows if row["stride"] == stride and not row["packed"]]
    return sorted((row["run"], row) for row in chosen if row["run"] in RUNS)


def axes(title, ylabel):
    figure, axis = plt.subplots(figsize=(8, 4.6))
    axis.set_xscale("log", base=2)
    axis.set_xticks(RUNS, [plot.size_label(run) for run in RUNS])
    axis.minorticks_off()
    axis.set_xlim(*XLIM)
    axis.set_xlabel("Run: contiguous bytes read from each matrix row")
    axis.set_ylabel(ylabel)
    axis.set_title(title, loc="left")
    return figure, axis


def save(figure, axis, path):
    axis.legend(loc="lower right")
    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=160)
    figure.savefig(path.with_suffix(".svg"), metadata={"Date": None})
    plt.close(figure)


def draw(axis, rows, stride, key, color, label, style="-", marker="o"):
    points = series(rows, stride)
    axis.plot(
        [run for run, _ in points],
        [row[key] for _, row in points],
        style,
        color=color,
        marker=marker,
        label=label,
    )


def bandwidth_figure(rows, path):
    figure, axis = axes(
        "Matmul tile loads (2D DMA), one cluster and its HBM channel", "Bandwidth (GB/s)"
    )
    draw(axis, rows, None, "median_GB_per_s", PACKED, "Same rows packed (stride = run)", "--", "s")
    for stride, (label, color) in STRIDES.items():
        draw(axis, rows, stride, "median_GB_per_s", color, f"Stride {label}")
    axis.set_ylim(0, plot.PEAK_GB_PER_S * 1.05)
    save(figure, axis, path)


def padding_figure(rows, path):
    figure, axis = axes("Padding a row by 64 B", "Bandwidth (GB/s)")
    draw(axis, rows, None, "median_GB_per_s", PACKED, "Same rows packed (stride = run)", "--", "s")
    for padded, stride in PADDED.items():
        label, color = STRIDES[stride]
        draw(axis, rows, stride, "median_GB_per_s", color, f"Stride {label}", ":", "o")
        draw(axis, rows, padded, "median_GB_per_s", color, f"Stride {stride_label(padded)}")
    axis.set_ylim(0, plot.PEAK_GB_PER_S * 1.05)
    save(figure, axis, path)


def row_openings_figure(rows, dram, path):
    per_point = {}
    for row in dram:
        per_point.setdefault((row["run"], row["stride"]), []).append(row["bursts_per_activate"])
    for row in rows:
        row["bursts_per_activate"] = median(per_point[row["run"], row["stride"]])
    figure, axis = axes("32 B bursts read per DRAM row opening", "Bursts per row opening")
    draw(axis, rows, None, "bursts_per_activate", PACKED, "Same rows packed", "--", "s")
    for stride, (label, color) in STRIDES.items():
        draw(axis, rows, stride, "bursts_per_activate", color, f"Stride {label}")
    axis.set_yscale("log", base=2)
    save(figure, axis, path)


def timings_figure(root, path, stride=8192):
    figure, axis = axes(
        f"Stride {plot.size_label(stride)}: HBM4 timings vs the old emulation timings",
        "Bandwidth (GB/s)",
    )
    for name, (label, color, style) in MEMSPECS.items():
        draw(
            axis,
            load(root / name / "bandwidth.csv"),
            stride,
            "median_GB_per_s",
            color,
            label,
            style,
        )
    axis.axhline(
        plot.PEAK_GB_PER_S,
        color=plot.REFERENCE,
        linewidth=1,
        linestyle="--",
        label=f"Channel peak ({plot.PEAK_GB_PER_S:g} GB/s)",
    )
    axis.set_ylim(0, 75)
    save(figure, axis, path)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("directory", type=Path, nargs="?", default=DATA)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    data = args.directory
    out = args.output_dir or (FIGURES if data.resolve() == DATA else data)
    rows = load(data / "bandwidth.csv")
    bandwidth_figure(rows, out / "bandwidth.png")
    padding_figure(rows, out / "padding.png")
    if (data / "dram.csv").exists():
        dram = [
            {
                "run": int(row["run"]),
                "stride": int(row["stride"]),
                "bursts_per_activate": float(row["bursts_per_activate"]),
            }
            for row in csv.DictReader((data / "dram.csv").open())
        ]
        row_openings_figure(rows, dram, out / "row_openings.png")
    if data.resolve() == DATA and (ROOT / "ccds4/bandwidth.csv").exists():
        timings_figure(ROOT, out / "timings.png")


if __name__ == "__main__":
    main()
