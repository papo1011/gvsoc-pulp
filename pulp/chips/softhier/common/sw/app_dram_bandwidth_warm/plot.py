#!/usr/bin/env python3
"""Draw the warm sweep's bandwidth figure on the shared 4 B - 1 MiB axis."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dram_common import plot_sweep as plot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "csv",
        type=Path,
        nargs="?",
        default=Path(__file__).parent / "assets/bandwidth.csv",
        help="bandwidth.csv produced by summarize.py (default: this app's assets/)",
    )
    parser.add_argument("--output", type=Path, help="figure path (default: next to the CSV)")
    args = parser.parse_args()
    curve = plot.load_curve(args.csv)
    plot.bandwidth_figure(
        curve,
        args.output or args.csv.with_name("bandwidth.png"),
        "Single cluster, local HBM: warm DMA reads (synchronous router, tCCD_S = 2)",
        "#287f8e",
        "End to end, warm (same block reread), median and min-max of 3",
        with_fit=True,
    )
    alpha, limit = plot.fit(curve)
    print(f"Fit on 64-256 KiB: alpha = {alpha:.3f} ns, B = {limit:.6f} GB/s")


if __name__ == "__main__":
    main()
