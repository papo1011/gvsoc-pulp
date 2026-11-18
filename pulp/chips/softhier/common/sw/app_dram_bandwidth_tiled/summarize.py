#!/usr/bin/env python3
"""Compare complete HBM datasets read through two TCDM tile sizes."""

import argparse
import csv
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_dram_bandwidth_warm.summarize import summarize as summarize_curve
from dram_common.analysis import require_success

SIZES = tuple(kib * 1024 for kib in (512, 1020, 1024, 1028, 1032, 1036, 2048))
DMA_LIMITS = (256 * 1024, 1024 * 1024)
SAMPLES = 3
HBM_OFFSET = 32 * 1024 * 1024
FIELDS = ("bytes", "dma_limit_bytes", "dma_commands", "last_tile_bytes", "sample", "hbm_offset")


def measurements(log):
    """Accept only complete, nonoverlapping measurements with valid DMA bounds."""
    require_success(log, name="SoftHier tiled DRAM bandwidth", prefix="[dram-tiled]")
    headers = re.findall(r"^\[dram-tiled\] tcdm_bytes=(\d+) max_bytes=(\d+)$", log, re.M)
    if headers != [(str(DMA_LIMITS[-1]), str(SIZES[-1]))]:
        raise ValueError("Expected the 1 MiB TCDM / 2 MiB dataset configuration")
    expected = {
        (size, limit, sample) for size in SIZES for limit in DMA_LIMITS for sample in range(SAMPLES)
    }
    seen = set()
    rows = []
    for line in log.splitlines():
        if not line.startswith("[dram-tiled] cluster="):
            continue
        row = dict(field.split("=", 1) for field in line.split()[1:])
        row = {key: value if key == "operation" else int(value, 0) for key, value in row.items()}
        key = row["bytes"], row["dma_limit_bytes"], row["sample"]
        if key not in expected or key in seen:
            raise ValueError(f"Unexpected or duplicate sample: {key}")
        commands = (row["bytes"] + row["dma_limit_bytes"] - 1) // row["dma_limit_bytes"]
        config = (
            row["cluster"],
            row["channel"],
            row["operation"],
            row["repetitions"],
            row["hbm_offset"],
            row["dma_commands"],
            row["last_tile_bytes"],
        )
        last_bytes = row["bytes"] - (commands - 1) * row["dma_limit_bytes"]
        if config != (0, 0, "dma_read", 1, HBM_OFFSET, commands, last_bytes):
            raise ValueError(f"Unexpected DMA configuration: {key}")
        if row["errors"] or row["end_ps"] <= row["start_ps"]:
            raise ValueError(f"Data errors or invalid measurement interval: {key}")
        row["hbm_span"] = row["bytes"]
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
    """Reuse the existing bandwidth calculation, grouping by the DMA size limit."""
    result = []
    for limit in DMA_LIMITS:
        curve = summarize_curve([row for row in rows if row["dma_limit_bytes"] == limit], 64)
        for point in curve:
            point["median_ns_per_dataset"] = point.pop("median_ns_per_transfer")
            commands = (point["bytes"] + limit - 1) // limit
            result.append(
                {
                    "dma_limit_bytes": limit,
                    "dma_commands": commands,
                    "last_tile_bytes": point["bytes"] - (commands - 1) * limit,
                    **point,
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
