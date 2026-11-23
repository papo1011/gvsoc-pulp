#!/usr/bin/env python3
"""Draw the pipelined sweep's figures from the CSVs in a directory.

  bandwidth.png              end-to-end bandwidth
  dram_vs_end_to_end.png     DRAM data-bus vs end-to-end bandwidth (needs dram.csv)
  ccds.png                   tCCD_S = 2 against tCCD_S = 4 (needs bandwidth_ccds2.csv)
  streaming_vs_pipelined.png one against eight copies in flight
  time_breakdown.png         where the time of a sample goes, both sweeps
                             (both need the streaming sweep's CSVs)

Figures whose inputs are missing are skipped.
"""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dram_common import plot_sweep as plot

HERE = Path(__file__).resolve().parent
LABEL = "pipelined (eight copies in flight)"


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
        "--streaming",
        type=Path,
        default=HERE.parent / "app_dram_bandwidth_streaming/assets",
        help="directory with the streaming sweep's bandwidth.csv and dram.csv",
    )
    args = parser.parse_args()
    data, out = args.directory, args.output_dir or args.directory
    curve = plot.load_curve(data / "bandwidth.csv")
    onset = plot.bandwidth_figure(
        curve,
        out / "bandwidth.png",
        "Single cluster, local HBM: pipelined DMA reads (tCCD_S = 4)",
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
            "Pipelined reads: DRAM data bus vs end to end (tCCD_S = 4)",
            plot.PIPELINED,
            LABEL,
        )
    if (data / "bandwidth_ccds2.csv").exists():
        plot.comparison_figure(
            [
                ("tCCD_S = 4 (final)", curve, plot.PIPELINED, "o"),
                (
                    "tCCD_S = 2 (shared DRAMSys configuration)",
                    plot.load_curve(data / "bandwidth_ccds2.csv"),
                    plot.OLDER[0],
                    "s",
                ),
            ],
            out / "ccds.png",
            "Pipelined reads, asynchronous router: tCCD_S = 2 vs 4",
            relative="percent",
            relative_label="tCCD_S = 2 faster\nthan tCCD_S = 4 (%)",
        )
    if (args.streaming / "bandwidth.csv").exists():
        streaming = plot.load_curve(args.streaming / "bandwidth.csv")
        plot.comparison_figure(
            [
                ("Streaming (one copy in flight)", streaming, plot.STREAMING, "o"),
                ("Pipelined (eight copies in flight)", curve, plot.PIPELINED, "s"),
            ],
            out / "streaming_vs_pipelined.png",
            "One vs eight copies in flight, asynchronous router, tCCD_S = 4",
            relative="ratio",
            relative_label="Pipelined / streaming",
        )
    if samples and (args.streaming / "dram.csv").exists():
        plot.breakdown_figure(
            [
                ("Streaming (one copy in flight)", plot.load_samples(args.streaming / "dram.csv")),
                ("Pipelined (eight copies in flight)", samples),
            ],
            out / "time_breakdown.png",
            "Where the time of a sample goes (median sample per size, tCCD_S = 4)",
        )


if __name__ == "__main__":
    main()
