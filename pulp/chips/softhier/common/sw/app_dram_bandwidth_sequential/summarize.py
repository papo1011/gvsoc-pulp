#!/usr/bin/env python3
"""Summarize the sequential single-cluster read sweep and check for a stable plateau."""

import argparse
import csv
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dram_common.analysis import plateau, require_success, summarize

SIZES = tuple(1 << power for power in range(2, 21))  # 4 B .. 1 MiB
SAMPLES = 3
REPEATS = 8
FIELDS = (
    "pattern",
    "cluster",
    "channel",
    "operation",
    "bytes",
    "repetitions",
    "sample",
    "hbm_offset",
)


def offsets():
    """Mirror the firmware allocation: windows up to 512 KiB from 32 MiB, 1 MiB from 8 MiB."""
    result = {}
    cursor = 0x02000000
    for size in SIZES:
        for sample in range(SAMPLES):
            if size == 1 << 20 and cursor >= 0x02000000:
                cursor = 0x00800000
            alignment = max(size, 32)
            cursor = (cursor + alignment - 1) & ~(alignment - 1)
            result[size, sample] = cursor
            cursor += REPEATS * size
    assert cursor <= 0x04000000
    return result


def measurements(log):
    """Reject partial sweeps rather than drawing conclusions from missing sizes."""
    require_success(log, name="SoftHier DRAM bandwidth", prefix="[dram-bandwidth]")
    limits = re.findall(r"^\[dram-bandwidth\] max_bytes=(\d+)$", log, re.M)
    if limits != [str(SIZES[-1])]:
        raise ValueError("Missing, invalid or duplicate sweep size limit")
    expected = offsets()
    intervals = []
    seen = set()
    rows = []
    for line in log.splitlines():
        if not line.startswith("[dram-bandwidth] cluster="):
            continue
        row = dict(field.split("=", 1) for field in line.split()[1:])
        if row.get("pattern") == "streaming":  # Logs from before the rename to sequential.
            row["pattern"] = "sequential"
        if row.get("pattern") != "sequential":
            raise ValueError(f"Unknown access pattern: {row.get('pattern')}")
        for field in (*FIELDS, "start_ps", "end_ps", "errors"):
            if field not in ("operation", "pattern"):
                row[field] = int(row[field], 0)
        key = row["bytes"], row["sample"]
        if key not in expected or key in seen:
            raise ValueError(f"Unexpected or duplicate sample: {key}")
        if (
            row["cluster"],
            row["channel"],
            row["operation"],
            row["repetitions"],
            row["hbm_offset"],
        ) != (0, 0, "dma_read", REPEATS, expected[key]):
            raise ValueError(f"Unexpected benchmark configuration: {key}")
        if row["errors"] or row["end_ps"] <= row["start_ps"]:
            raise ValueError(f"Data errors or invalid measurement interval: {key}")
        row["hbm_span"] = REPEATS * row["bytes"]
        intervals.append((row["start_ps"], row["end_ps"]))
        row["total_ns"] = (row["end_ps"] - row["start_ps"]) / 1000
        rows.append(row)
        seen.add(key)
    if len(seen) != len(expected):
        raise ValueError(f"Expected {len(expected)} samples, found {len(seen)}")
    ordered = sorted(intervals)
    if any(first[1] > second[0] for first, second in zip(ordered, ordered[1:])):
        raise ValueError("Single-worker measurement windows must not overlap")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument(
        "--peak-gbps",
        type=float,
        default=64,
        help="nominal single-channel bus peak; does not constrain the simulator",
    )
    parser.add_argument("--report", type=Path, help="also write a Markdown plateau report")
    args = parser.parse_args()
    try:
        if not 0 < args.peak_gbps < float("inf"):
            raise ValueError("Peak bandwidth must be finite and positive")
        curve = summarize(measurements(args.log.read_text()), args.peak_gbps)
        onset = plateau(curve)
        best = max(curve, key=lambda row: row["median_GB_per_s"])
        if args.report:
            status = (
                f"Plateau onset: **{onset} bytes**."
                if onset is not None
                else "**No stable plateau detected within the tested range.**"
            )
            args.report.write_text(
                "# Sequential single-cluster HBM read bandwidth\n\n"
                f"{status}\n\n"
                f"Maximum median: **{best['median_GB_per_s']:.3f} GB/s** at "
                f"**{best['bytes']} bytes**, "
                f"**{best['nominal_utilization_percent']:.2f}%** of the "
                f"**{args.peak_gbps:g} GB/s** nominal bus peak.\n\n"
                "Plateau criterion: the last three size medians span at most 3%; "
                "from onset onward every median is at least 95% of the maximum.\n\n"
                "Each sample reads eight distinct consecutive blocks from local HBM 0 "
                "into cluster 0 TCDM, one DMA copy at a time. The bandwidth is useful "
                "payload including DMA issue and wait overhead, relative to the "
                "nominal physical bus peak.\n"
            )
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"{error}\n")
    writer = csv.DictWriter(sys.stdout, fieldnames=curve[0].keys())
    writer.writeheader()
    writer.writerows(curve)


if __name__ == "__main__":
    main()
