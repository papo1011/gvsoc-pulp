#!/usr/bin/env python3
"""Draw the pipelined sweep's figures, one chart per image, from the CSVs in a directory
(by default assets/data/, into assets/figures/).

  bandwidth.png               end-to-end bandwidth, fitted latency-bandwidth model
  dram_vs_end_to_end.png      DRAM data-bus vs end-to-end bandwidth (needs dram.csv)
  time_breakdown.png          where the time of a sample goes (dram.csv)
  timings.png                 HBM4 timings vs the old emulation timings
                              (needs bandwidth_ccds4.csv)
  sequential_vs_pipelined.png  one vs eight copies in flight
                              (needs the sequential sweep's bandwidth.csv)

Figures whose inputs are missing are skipped.
"""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dram_common import plot_sweep as plot

HERE = Path(__file__).resolve().parent
DATA, FIGURES = HERE / "assets/data", HERE / "assets/figures"
LABEL = "pipelined (eight copies in flight)"
CONFIGURATION = "HBM4 timings"


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
    parser.add_argument(
        "--sequential",
        type=Path,
        default=HERE.parent / "app_dram_bandwidth_sequential/assets/data",
        help="directory with the sequential sweep's bandwidth.csv",
    )
    args = parser.parse_args()
    data = args.directory
    out = args.output_dir or (FIGURES if data.resolve() == DATA else data)
    curve = plot.load_curve(data / "bandwidth.csv")
    onset = plot.bandwidth_figure(
        curve,
        out / "bandwidth.png",
        f"Single cluster, local HBM: pipelined DMA reads ({CONFIGURATION})",
        plot.PIPELINED,
        f"End to end, {LABEL}",
        with_fit=True,
    )
    print(f"Plateau from {onset} bytes")
    alpha, limit = plot.fit(curve)
    print(f"Fit on 64-256 KiB: alpha = {alpha:.3f} ns per copy, B = {limit:.6f} GB/s")
    for row in curve:
        measured = row["median_ns_per_transfer"]
        model = plot.model_time(row["bytes"], alpha, limit)
        print(
            f"  {row['bytes']:>7} B: measured {measured:9.3f} ns,"
            f" model {model:9.3f} ns ({100 * (measured / model - 1):+.1f}%)"
        )
    if (data / "dram.csv").exists():
        samples = plot.load_samples(data / "dram.csv")
        plot.dram_figure(
            curve,
            samples,
            out / "dram_vs_end_to_end.png",
            f"Pipelined reads: DRAM data bus vs end to end ({CONFIGURATION})",
            plot.PIPELINED,
            LABEL,
        )
        plot.breakdown_figure(
            samples,
            out / "time_breakdown.png",
            f"Pipelined reads: where the time of a sample goes ({CONFIGURATION})",
        )
    old = data / "bandwidth_ccds4.csv"
    if old.exists():
        plot.comparison_figure(
            [
                ("HBM4 timings (now)", curve, plot.PIPELINED, "o"),
                ("Old emulation timings", plot.load_curve(old), plot.OLDER[0], "s"),
            ],
            out / "timings.png",
            "Pipelined reads: HBM4 timings vs the old emulation timings",
        )
    if (args.sequential / "bandwidth.csv").exists():
        plot.comparison_figure(
            [
                (
                    "Sequential (one copy in flight)",
                    plot.load_curve(args.sequential / "bandwidth.csv"),
                    plot.SEQUENTIAL,
                    "o",
                ),
                ("Pipelined (eight copies in flight)", curve, plot.PIPELINED, "s"),
            ],
            out / "sequential_vs_pipelined.png",
            f"One vs eight copies in flight ({CONFIGURATION})",
        )


if __name__ == "__main__":
    main()
