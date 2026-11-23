#!/usr/bin/env python3
"""Summarize a single-cluster DMA size sweep and check for a stable plateau."""

import argparse
import csv
import re
from pathlib import Path
from statistics import median
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dram_common.analysis import payload_bytes, require_success

SIZES = tuple(1 << power for power in range(2, 20))
SAMPLES = 3
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


def measurements(log):
    """Reject partial sweeps rather than drawing conclusions from missing sizes."""
    require_success(log, name="SoftHier DRAM bandwidth", prefix="[dram-bandwidth]")
    limits = re.findall(r"^\[dram-bandwidth\] max_bytes=(\d+)$", log, re.M)
    if len(limits) > 1 or (limits and int(limits[0]) not in (SIZES[-1], 1 << 20)):
        raise ValueError("Invalid or duplicate sweep size limit")
    extended = bool(limits and int(limits[0]) == 1 << 20)
    sizes = (*SIZES, 1 << 20) if extended else SIZES
    expected = {(size, sample) for size in sizes for sample in range(SAMPLES)}
    sequential_offsets = {}
    cursor = 0x02000000
    for size in sizes:
        for sample in range(SAMPLES):
            if size == 1 << 20 and cursor >= 0x02000000:
                cursor = 0x00800000
            alignment = max(size, 32)
            cursor = (cursor + alignment - 1) & ~(alignment - 1)
            sequential_offsets[size, sample] = cursor
            cursor += 8 * size
    assert cursor <= 0x04000000
    patterns = set()
    intervals = []
    seen = set()
    rows = []
    for line in log.splitlines():
        if not line.startswith("[dram-bandwidth] cluster="):
            continue
        row = dict(field.split("=", 1) for field in line.split()[1:])
        row.setdefault("pattern", "warm")  # Original logs predate the pattern field.
        if row["pattern"] == "streaming":  # Logs from before the rename to sequential.
            row["pattern"] = "sequential"
        for field in (*FIELDS, "start_ps", "end_ps", "errors"):
            if field not in ("operation", "pattern"):
                row[field] = int(row[field], 0)
        key = row["bytes"], row["sample"]
        if key not in expected or key in seen:
            raise ValueError(f"Unexpected or duplicate sample: {key}")
        pattern = row["pattern"]
        if pattern not in ("warm", "sequential"):
            raise ValueError(f"Unknown access pattern: {pattern}")
        if extended and pattern != "sequential":
            raise ValueError("Whole-TCDM copies are supported by the sequential variant only")
        patterns.add(pattern)
        offset = sequential_offsets[key] if pattern == "sequential" else 0x04000000 - SIZES[-1]
        if (
            row["cluster"],
            row["channel"],
            row["operation"],
            row["repetitions"],
            row["hbm_offset"],
        ) != (0, 0, "dma_read", 8, offset):
            raise ValueError(f"Unexpected benchmark configuration: {key}")
        if row["errors"] or row["end_ps"] <= row["start_ps"]:
            raise ValueError(f"Data errors or invalid measurement interval: {key}")
        row["hbm_span"] = row["bytes"] * (8 if pattern == "sequential" else 1)
        intervals.append((row["start_ps"], row["end_ps"]))
        row["total_ns"] = (row["end_ps"] - row["start_ps"]) / 1000
        rows.append(row)
        seen.add(key)
    if seen != expected:
        raise ValueError(f"Expected {len(expected)} samples, found {len(seen)}")
    if len(patterns) != 1:
        raise ValueError("Cannot combine warm and sequential samples in one sweep")
    ordered = sorted(intervals)
    if any(first[1] > second[0] for first, second in zip(ordered, ordered[1:])):
        raise ValueError("Single-worker measurement windows must not overlap")
    return rows


def summarize(rows, peak):
    """Use medians for the curve, retaining the full sample range for inspection."""
    result = []
    for size in sorted({row["bytes"] for row in rows}):
        workers = [row for row in rows if row["bytes"] == size]
        rates = [payload_bytes(row) / row["total_ns"] for row in workers]
        rate = median(rates)
        result.append(
            {
                "bytes": size,
                "samples": len(workers),
                "repetitions": workers[0]["repetitions"],
                "median_ns_per_transfer": median(
                    row["total_ns"] / row["repetitions"] for row in workers
                ),
                "median_GB_per_s": rate,
                "min_GB_per_s": min(rates),
                "max_GB_per_s": max(rates),
                "nominal_peak_GB_per_s": peak,
                "nominal_utilization_percent": 100 * rate / peak,
            }
        )
    return result


def plateau(curve):
    """Require stability at the largest sizes; a transient flat spot is not enough.

    The last three medians must span at most 3%. The onset is the earliest size
    whose entire remaining suffix stays within 5% of the maximum median rate.
    This describes size saturation for this access pattern, not hardware proof.
    """
    rates = [row["median_GB_per_s"] for row in curve]
    if len(rates) < 3 or max(rates[-3:]) / min(rates[-3:]) > 1.03:
        return None
    threshold = max(rates) * 0.95
    for index in range(len(curve) - 2):
        if min(rates[index:]) >= threshold:
            return curve[index]["bytes"]
    return None


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
        rows = measurements(args.log.read_text())
        pattern = rows[0]["pattern"]
        curve = summarize(rows, args.peak_gbps)
        onset = plateau(curve)
        best = max(curve, key=lambda row: row["median_GB_per_s"])
        if args.report:
            status = (
                f"Plateau onset: **{onset} bytes**."
                if onset is not None
                else "**No stable plateau detected within the tested range.**"
            )
            args.report.write_text(
                "# Single-cluster HBM read bandwidth\n\n"
                f"{status}\n\n"
                f"Maximum median: **{best['median_GB_per_s']:.3f} GB/s** at "
                f"**{best['bytes']} bytes**, "
                f"**{best['nominal_utilization_percent']:.2f}%** of the "
                f"**{args.peak_gbps:g} GB/s** nominal bus peak.\n\n"
                "Plateau criterion: the last three size medians span at most 3%; "
                "from onset onward every median is at least 95% of the maximum.\n\n"
                f"Access pattern: **{pattern}**. "
                "Sequential uses distinct consecutive source blocks without read warm-up; "
                "warm mode repeatedly reads the same pre-read block. "
                "Both use aligned, contiguous DMA reading from local HBM 0 "
                "into cluster 0 TCDM, with one logical copy in flight. "
                "It measures useful payload including DMA issue/wait overhead. "
                "The hardware and DRAM timing configuration are unchanged. "
                "Utilization is relative to the nominal physical bus peak, not a "
                "guarantee that the emulation enforces it.\n"
            )
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"{error}\n")
    writer = csv.DictWriter(sys.stdout, fieldnames=curve[0].keys())
    writer.writeheader()
    writer.writerows(curve)


if __name__ == "__main__":
    main()
