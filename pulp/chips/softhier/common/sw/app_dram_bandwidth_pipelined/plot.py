#!/usr/bin/env python3
"""Draw the pipelined sweep's figures, one chart per image, from the CSVs in a directory.

  bandwidth.png               end-to-end bandwidth
  dram_vs_end_to_end.png      DRAM data-bus vs end-to-end bandwidth (needs dram.csv)
  fixed_cost.png              time of a sample outside the DRAM bus window (dram.csv)
  time_breakdown.png          where the time of a sample goes (dram.csv)
  ccds.png                    end to end, tCCD_S = 2 vs 4 (needs bandwidth_ccds2.csv)
  ccds_bus.png                DRAM data bus, tCCD_S = 2 vs 4 (dram.csv, dram_ccds2.csv)
  sequential_vs_pipelined.png  one vs eight copies in flight
  pipelined_speedup.png       pipelined / sequential bandwidth
                              (both need the sequential sweep's bandwidth.csv)

Figures whose inputs are missing are skipped.
"""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dram_common import plot_sweep as plot

HERE = Path(__file__).resolve().parent
LABEL = "pipelined (eight copies in flight)"
CONFIGURATION = "asynchronous router, tCCD_S = 4"


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "directory",
        type=Path,
        nargs="?",
        default=HERE / "assets",
        help="directory with the CSVs (default: this app's assets/)",
    )
    parser.add_argument(
        "--output-dir", type=Path, help="where to write the figures (default: directory)"
    )
    parser.add_argument(
        "--sequential",
        type=Path,
        default=HERE.parent / "app_dram_bandwidth_sequential/assets",
        help="directory with the sequential sweep's bandwidth.csv",
    )
    args = parser.parse_args()
    data, out = args.directory, args.output_dir or args.directory
    curve = plot.load_curve(data / "bandwidth.csv")
    onset = plot.bandwidth_figure(
        curve,
        out / "bandwidth.png",
        f"Single cluster, local HBM: pipelined DMA reads ({CONFIGURATION})",
        plot.PIPELINED,
        f"End to end, {LABEL}, median and min-max of 3",
    )
    print(f"Plateau from {onset} bytes")
    samples = None
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
        plot.fixed_cost_figure(
            samples,
            out / "fixed_cost.png",
            f"Pipelined reads: time outside the DRAM bus window ({CONFIGURATION})",
        )
        plot.breakdown_figure(
            samples,
            out / "time_breakdown.png",
            f"Pipelined reads: where the time of a sample goes ({CONFIGURATION})",
        )
    if (data / "bandwidth_ccds2.csv").exists():
        plot.comparison_figure(
            [
                ("End to end, tCCD_S = 4 (final)", curve, plot.PIPELINED, "o"),
                (
                    "End to end, tCCD_S = 2 (shared DRAMSys configuration)",
                    plot.load_curve(data / "bandwidth_ccds2.csv"),
                    plot.OLDER[0],
                    "s",
                ),
            ],
            out / "ccds.png",
            "Pipelined reads, asynchronous router: tCCD_S = 2 vs 4, end to end",
        )
    if samples and (data / "dram_ccds2.csv").exists():
        plot.comparison_figure(
            [
                ("DRAM data bus, tCCD_S = 4 (final)", plot.bus_rows(samples), plot.DRAM, "o"),
                (
                    "DRAM data bus, tCCD_S = 2 (shared DRAMSys configuration)",
                    plot.bus_rows(plot.load_samples(data / "dram_ccds2.csv")),
                    plot.OLDER[0],
                    "s",
                ),
            ],
            out / "ccds_bus.png",
            "Pipelined reads, asynchronous router: tCCD_S = 2 vs 4, DRAM data bus",
            value="bus_GB_per_s",
        )
    if (args.sequential / "bandwidth.csv").exists():
        sequential = plot.load_curve(args.sequential / "bandwidth.csv")
        plot.comparison_figure(
            [
                ("Sequential (one copy in flight)", sequential, plot.SEQUENTIAL, "o"),
                ("Pipelined (eight copies in flight)", curve, plot.PIPELINED, "s"),
            ],
            out / "sequential_vs_pipelined.png",
            f"One vs eight copies in flight ({CONFIGURATION})",
        )
        plot.ratio_figure(
            sequential,
            curve,
            out / "pipelined_speedup.png",
            f"Pipelined over sequential bandwidth ({CONFIGURATION})",
            "Pipelined / sequential bandwidth",
            plot.PIPELINED,
        )


if __name__ == "__main__":
    main()
