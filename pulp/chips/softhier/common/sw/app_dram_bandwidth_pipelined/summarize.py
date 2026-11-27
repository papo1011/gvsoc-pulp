#!/usr/bin/env python3
"""Summarize the pipelined contiguous read sweep and detect its plateau."""

import argparse
import csv
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from dram_common.analysis import plateau, require_success, summarize

NAME = "SoftHier pipelined DRAM bandwidth"
PREFIX = "[dram-pipelined]"
SIZES = tuple(1 << power for power in range(2, 21))  # 4 B .. 1 MiB
SAMPLES = 3
REPEATS = 8
REGION = 4 << 20
REGION_BYTES = 52 << 20  # 48 MiB before the extra small-block samples
FIELDS = ("operation", "cluster", "channel", "bytes", "repetitions", "sample", "hbm_offset")


def samples(size, small_bytes=0, small_samples=SAMPLES):
    """Samples per size: blocks up to small_bytes get small_samples (older logs: 3 for all)."""
    return small_samples if size <= small_bytes else SAMPLES


def offsets(small_bytes=0, small_samples=SAMPLES):
    """Mirror the firmware allocation: aligned, consecutive windows per sample."""
    result = {}
    cursor = REGION
    for size in SIZES:
        alignment = max(size, 32)
        for sample in range(samples(size, small_bytes, small_samples)):
            cursor = (cursor + alignment - 1) & ~(alignment - 1)
            result[size, sample] = cursor
            cursor += REPEATS * size
    assert cursor <= REGION + REGION_BYTES
    return result


def measurements(log):
    """Accept only complete, error-free, nonoverlapping sweeps."""
    require_success(log, name=NAME, prefix=PREFIX)
    header = re.findall(
        r"^\[dram-pipelined\] first_bytes=(\d+) last_bytes=(\d+) samples=(\d+) "
        r"in_flight=(\d+)(?: small_bytes=(\d+) small_samples=(\d+))?$",
        log,
        re.M,
    )
    if len(header) != 1 or header[0][:4] != (
        str(SIZES[0]),
        str(SIZES[-1]),
        str(SAMPLES),
        str(REPEATS),
    ):
        raise ValueError("Unexpected or duplicate sweep configuration")
    small_bytes, small_samples = (int(header[0][4] or 0), int(header[0][5] or SAMPLES))
    expected = offsets(small_bytes, small_samples)
    seen = set()
    rows = []
    for line in log.splitlines():
        if not line.startswith(f"{PREFIX} cluster="):
            continue
        row = dict(field.split("=", 1) for field in line.split()[1:])
        row = {key: value if key == "operation" else int(value, 0) for key, value in row.items()}
        key = row["bytes"], row["sample"]
        if key not in expected or key in seen:
            raise ValueError(f"Unexpected or duplicate sample: {key}")
        configuration = (
            row["operation"],
            row["cluster"],
            row["channel"],
            row["repetitions"],
            row["hbm_offset"],
        )
        if configuration != ("dma_read", 0, 0, REPEATS, expected[key]):
            raise ValueError(f"Unexpected benchmark configuration: {key}")
        if row["errors"] or row["end_ps"] <= row["start_ps"]:
            raise ValueError(f"Data errors or invalid measurement interval: {key}")
        row["hbm_span"] = REPEATS * row["bytes"]
        row["total_ns"] = (row["end_ps"] - row["start_ps"]) / 1000
        rows.append(row)
        seen.add(key)
    if len(seen) != len(expected):
        raise ValueError(f"Expected {len(expected)} samples, found {len(seen)}")
    intervals = sorted((row["start_ps"], row["end_ps"]) for row in rows)
    if any(first[1] > second[0] for first, second in zip(intervals, intervals[1:])):
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
        if args.report:
            onset = plateau(curve)
            best = max(curve, key=lambda point: point["median_GB_per_s"])
            status = f"Plateau from **{onset} bytes**" if onset else "**No stable plateau**"
            args.report.write_text(
                "# Pipelined contiguous HBM read bandwidth\n\n"
                f"{status}; maximum median **{best['median_GB_per_s']:.3f} GB/s** at "
                f"{best['bytes']} bytes ({best['nominal_utilization_percent']:.2f}% of "
                f"{args.peak_gbps:g} GB/s).\n\n"
                "Plateau criterion: the last three size medians span at most 3%; from the "
                "onset onward every median is at least 95% of the maximum.\n"
            )
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"{error}\n")
    writer = csv.DictWriter(sys.stdout, fieldnames=curve[0].keys())
    writer.writeheader()
    writer.writerows(curve)


if __name__ == "__main__":
    main()
