#!/usr/bin/env python3
"""Draw the pipelined sweep's figures, one chart per image, from the CSVs in a directory.

  bandwidth.png               end-to-end bandwidth, fitted latency-bandwidth model
  dram_vs_end_to_end.png      DRAM data-bus vs end-to-end bandwidth (needs dram.csv)
  fixed_cost.png              time of a sample outside the DRAM bus window (dram.csv)
  time_breakdown.png          where the time of a sample goes (dram.csv)
  ccds.png                    end to end, tCCD_S = 2 vs 4 (needs bandwidth_ccds2.csv)
  ccds_bus.png                DRAM data bus, tCCD_S = 2 vs 4 (dram.csv, dram_ccds2.csv)
  sequential_vs_pipelined.png  one vs eight copies in flight
                              (needs the sequential sweep's bandwidth.csv)
  bursts_512B_ccds{4,2}.png   every burst of a 512 B sample on the DRAM data bus
  requests_512B_ccds{4,2}.png  the same sample, request by request
  bursts_1MiB_ccds{4,2}.png   a 1 MiB sample around an 8 KiB boundary
                              (all three need trace_ccds4.csv / trace_ccds2.csv)

Figures whose inputs are missing are skipped.
"""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_dram_bandwidth_pipelined import trace_figures
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
        with_fit=True,
    )
    print(f"Plateau from {onset} bytes")
    alpha, limit = plot.fit(curve)
    print(f"Fit on 64-256 KiB: alpha = {alpha:.3f} ns per copy, B = {limit:.6f} GB/s")
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
    for ccds in (4, 2):
        path = data / f"trace_ccds{ccds}.csv"
        if not path.exists():
            continue
        trace = trace_figures.load_trace(path)
        small = trace[512, 0]
        trace_figures.bus_timeline_figure(
            small,
            out / f"bursts_512B_ccds{ccds}.png",
            f"512 B blocks, eight in flight: every burst on the DRAM data bus, tCCD_S = {ccds}",
        )
        trace_figures.pipeline_figure(
            small,
            out / f"requests_512B_ccds{ccds}.png",
            f"512 B blocks, eight in flight: requests on pseudo-channel 0, tCCD_S = {ccds}",
        )
        if (1 << 20, 0) in trace:
            trace_figures.bus_timeline_figure(
                trace[1 << 20, 0],
                out / f"bursts_1MiB_ccds{ccds}.png",
                "1 MiB blocks, 2 KiB either side of the 8 KiB boundary at 4 MiB: "
                f"DRAM data bus, tCCD_S = {ccds}",
                xlim=(-1, 66),
            )


if __name__ == "__main__":
    main()
