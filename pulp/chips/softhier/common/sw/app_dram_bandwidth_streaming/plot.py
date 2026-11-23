#!/usr/bin/env python3
"""Draw the streaming sweep's figures from the CSVs in a directory.

  bandwidth.png           end-to-end bandwidth, fitted latency-bandwidth model
  dram_vs_end_to_end.png  DRAM data-bus vs end-to-end bandwidth (needs dram.csv)
  simulator_fixes.png     the same sweep before and after each simulator fix
                          (needs bandwidth_sync_router_ccds2.csv and
                          bandwidth_ccds2.csv)

Figures whose inputs are missing are skipped.
"""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dram_common import plot_sweep as plot

LABEL = "streaming (one copy in flight)"


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "directory",
        type=Path,
        nargs="?",
        default=Path(__file__).parent / "assets",
        help="directory with the CSVs (default: this app's assets/)",
    )
    parser.add_argument(
        "--output-dir", type=Path, help="where to write the figures (default: directory)"
    )
    args = parser.parse_args()
    data, out = args.directory, args.output_dir or args.directory
    curve = plot.load_curve(data / "bandwidth.csv")
    plot.bandwidth_figure(
        curve,
        out / "bandwidth.png",
        "Single cluster, local HBM: streaming DMA reads (tCCD_S = 4)",
        plot.STREAMING,
        f"End to end, {LABEL}, median and min-max of 3",
        with_fit=True,
    )
    alpha, limit = plot.fit(curve)
    print(f"Fit on 64-256 KiB: alpha = {alpha:.3f} ns, B = {limit:.6f} GB/s")
    if (data / "dram.csv").exists():
        plot.dram_figure(
            curve,
            plot.load_samples(data / "dram.csv"),
            out / "dram_vs_end_to_end.png",
            "Streaming reads: DRAM data bus vs end to end (tCCD_S = 4)",
            plot.STREAMING,
            LABEL,
        )
    older = [data / "bandwidth_sync_router_ccds2.csv", data / "bandwidth_ccds2.csv"]
    if all(path.exists() for path in older):
        plot.comparison_figure(
            [
                ("Final: asynchronous router, tCCD_S = 4", curve, plot.STREAMING, "o"),
                ("Asynchronous router, tCCD_S = 2", plot.load_curve(older[1]), plot.OLDER[0], "s"),
                ("Synchronous router, tCCD_S = 2", plot.load_curve(older[0]), plot.OLDER[1], "^"),
            ],
            out / "simulator_fixes.png",
            "Streaming reads before and after each simulator fix",
            relative="percent",
            relative_label="Difference from final (%)",
        )


if __name__ == "__main__":
    main()
