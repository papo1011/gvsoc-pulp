#!/usr/bin/env python3
"""Draw the sequential sweep's figures, one chart per image, from the CSVs in a directory
(by default assets/data/, into assets/figures/).

  bandwidth.png           end-to-end bandwidth, fitted latency-bandwidth model
  dram_vs_end_to_end.png  DRAM data-bus vs end-to-end bandwidth (needs dram.csv)
  time_breakdown.png      where the time of a sample goes (dram.csv)
  read_chunks.png         the iDMA writing whole bursts vs 512 B chunks to the TCDM
                          (needs bandwidth_whole_burst.csv)
  simulator_fixes.png     the same sweep before and after each simulator fix
                          (needs bandwidth_whole_burst.csv,
                          bandwidth_sync_router_ccds2.csv and bandwidth_ccds2.csv)

Figures whose inputs are missing are skipped.
"""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dram_common import plot_sweep as plot

LABEL = "sequential (one copy in flight)"
CONFIGURATION = "asynchronous router, tCCD_S = 4"
HERE = Path(__file__).resolve().parent
DATA, FIGURES = HERE / "assets/data", HERE / "assets/figures"


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
    curve = plot.load_curve(data / "bandwidth.csv")
    plot.bandwidth_figure(
        curve,
        out / "bandwidth.png",
        f"Single cluster, local HBM: sequential DMA reads ({CONFIGURATION})",
        plot.SEQUENTIAL,
        f"End to end, {LABEL}",
        with_fit=True,
    )
    alpha, limit = plot.fit(curve)
    print(f"Fit on 64-256 KiB: alpha = {alpha:.3f} ns per copy, B = {limit:.6f} GB/s")
    if (data / "dram.csv").exists():
        samples = plot.load_samples(data / "dram.csv")
        plot.dram_figure(
            curve,
            samples,
            out / "dram_vs_end_to_end.png",
            f"Sequential reads: DRAM data bus vs end to end ({CONFIGURATION})",
            plot.SEQUENTIAL,
            LABEL,
        )
        plot.breakdown_figure(
            samples,
            out / "time_breakdown.png",
            f"Sequential reads: where the time of a sample goes ({CONFIGURATION})",
        )
    whole = data / "bandwidth_whole_burst.csv"
    if whole.exists():
        plot.comparison_figure(
            [
                ("TCDM written in 512 B chunks (now)", curve, plot.SEQUENTIAL, "o"),
                (
                    "TCDM written once the whole burst is read",
                    plot.load_curve(whole),
                    plot.OLDER[0],
                    "s",
                ),
            ],
            out / "read_chunks.png",
            f"Sequential reads before and after the iDMA read fix ({CONFIGURATION})",
        )
    older = [data / "bandwidth_sync_router_ccds2.csv", data / "bandwidth_ccds2.csv"]
    if whole.exists() and all(path.exists() for path in older):
        plot.comparison_figure(
            [
                (
                    "Final: asynchronous router, tCCD_S = 4, 512 B chunks",
                    curve,
                    plot.SEQUENTIAL,
                    "o",
                ),
                ("Asynchronous router, tCCD_S = 4", plot.load_curve(whole), plot.OLDER[2], "D"),
                ("Asynchronous router, tCCD_S = 2", plot.load_curve(older[1]), plot.OLDER[0], "s"),
                ("Synchronous router, tCCD_S = 2", plot.load_curve(older[0]), plot.OLDER[1], "^"),
            ],
            out / "simulator_fixes.png",
            "Sequential reads before and after each simulator fix",
        )

if __name__ == "__main__":
    main()
