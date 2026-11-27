#!/usr/bin/env python3
"""Summarize the matmul tile-load sweep: one row per (run, stride) point."""

import argparse
import csv
from pathlib import Path
import re
import sys
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dram_common.analysis import require_success

NAME = "SoftHier matmul DRAM bandwidth"
PREFIX = "[dram-matmul]"
RUNS = (32, 64, 128, 256, 512, 1024, 2048)
STRIDES = (0, 2048, 4096, 6144, 8192, 8256, 28672, 28736)
EXTRA = ((1792, 28672),)
SAMPLES = 5
REPEATS = 8
COPY_BYTES = 32 * 1024
REGION = 4 << 20
REGION_BYTES = 48 << 20
FIELDS = ("operation", "cluster", "channel", "run", "stride", "sample")


def points():
    """Mirror the firmware order; stride 0 becomes stride = run (packed rows)."""
    result = [
        (run, stride or run) for stride in STRIDES for run in RUNS if stride == 0 or run < stride
    ]
    return result + list(EXTRA)


def layout(run, stride):
    lines = COPY_BYTES // run
    height = REGION_BYTES // stride // lines * lines
    return lines, height


def line_offset(run, stride, height, line):
    return REGION + (line % height) * stride + (line // height) * run


def measurements(log):
    """Accept only complete, error-free, nonoverlapping sweeps."""
    require_success(log, name=NAME, prefix=PREFIX)
    header = re.findall(
        r"^\[dram-matmul\] copy_bytes=(\d+) samples=(\d+) in_flight=(\d+)$", log, re.M
    )
    if header != [(str(COPY_BYTES), str(SAMPLES), str(REPEATS))]:
        raise ValueError("Unexpected or duplicate sweep configuration")
    expected = {(run, stride, sample) for run, stride in points() for sample in range(SAMPLES)}
    rows = []
    seen = set()
    for line in log.splitlines():
        if not line.startswith(f"{PREFIX} cluster="):
            continue
        row = dict(field.split("=", 1) for field in line.split()[1:])
        row = {key: value if key == "operation" else int(value, 0) for key, value in row.items()}
        key = row["run"], row["stride"], row["sample"]
        if key not in expected or key in seen:
            raise ValueError(f"Unexpected or duplicate sample: {key}")
        lines, height = layout(row["run"], row["stride"])
        configuration = (row["operation"], row["cluster"], row["channel"], row["repetitions"])
        if configuration != ("dma_read_2d", 0, 0, REPEATS) or (row["lines"], row["height"]) != (
            lines,
            height,
        ):
            raise ValueError(f"Unexpected benchmark configuration: {key}")
        if row["first_line"] != row["sample"] * REPEATS * lines:
            raise ValueError(f"Unexpected first line: {key}")
        if row["errors"] or row["end_ps"] <= row["start_ps"]:
            raise ValueError(f"Data errors or invalid measurement interval: {key}")
        row["bytes"] = REPEATS * lines * row["run"]
        row["total_ns"] = (row["end_ps"] - row["start_ps"]) / 1000
        rows.append(row)
        seen.add(key)
    if seen != expected:
        raise ValueError(f"Expected {len(expected)} samples, found {len(seen)}")
    intervals = sorted((row["start_ps"], row["end_ps"]) for row in rows)
    if any(first[1] > second[0] for first, second in zip(intervals, intervals[1:])):
        raise ValueError("Single-worker measurement windows must not overlap")
    return rows


def summarize(rows):
    result = []
    for run, stride in points():
        samples = [row for row in rows if (row["run"], row["stride"]) == (run, stride)]
        rates = [row["bytes"] / row["total_ns"] for row in samples]
        result.append(
            {
                "run": run,
                "stride": stride,
                "packed": int(run == stride),
                "bytes_per_sample": samples[0]["bytes"],
                "median_GB_per_s": median(rates),
                "min_GB_per_s": min(rates),
                "max_GB_per_s": max(rates),
            }
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    args = parser.parse_args()
    try:
        curve = summarize(measurements(args.log.read_text()))
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"{error}\n")
    writer = csv.DictWriter(sys.stdout, fieldnames=curve[0].keys())
    writer.writeheader()
    writer.writerows(curve)


if __name__ == "__main__":
    main()
