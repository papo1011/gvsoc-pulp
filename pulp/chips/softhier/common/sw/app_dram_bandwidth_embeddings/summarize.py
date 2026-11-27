#!/usr/bin/env python3
"""Summarize the embeddings sweep: one row per (piece, order) point."""

import argparse
import csv
from pathlib import Path
import re
import sys
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dram_common.analysis import require_success

NAME = "SoftHier DRAM embeddings"
PREFIX = "[dram-embeddings]"
ORDERS = ("block", "contiguous", "sorted", "random")
PIECES = (256, 512, 1024, 2048, 4096, 8192)
TOKENS = 64
SAMPLES = 21
TABLE = 4 << 20
TABLE_BYTES = 48 << 20
FIELDS = ("operation", "cluster", "channel", "order", "piece", "sample")
MASK = 0xFFFFFFFF


def window_row(piece, order, sample):
    """First row of a block or contiguous sample, as in the firmware."""
    below = sum(2 * SAMPLES * TOKENS * other for other in PIECES if other < piece)
    return below // piece + (2 * sample + (order == "contiguous")) * TOKENS


assert 2 * SAMPLES * TOKENS * sum(PIECES) <= TABLE_BYTES, "contiguous windows overflow the table"


def ids(piece, order, sample):
    """Token IDs of a sample in issue order, as the firmware generates them."""
    if order in ("block", "contiguous"):
        first = window_row(piece, order, sample)
        return list(range(first, first + TOKENS))
    index = ORDERS.index(order)
    state = 0x9E3779B9 ^ (piece * 0x85EBCA6B & MASK) ^ (index * 0xC2B2AE35 & MASK)
    state = (state ^ (sample * 0x27D4EB2F & MASK)) or 1
    rows = TABLE_BYTES // piece
    result = []
    for _ in range(TOKENS):
        state ^= state << 13 & MASK
        state ^= state >> 17
        state ^= state << 5 & MASK
        result.append(state % rows)
    return sorted(result) if order == "sorted" else result


def expected_samples():
    return {
        (piece, order, sample) for piece in PIECES for order in ORDERS for sample in range(SAMPLES)
    }


def measurements(log):
    """Accept only complete, error-free, nonoverlapping sweeps."""
    require_success(log, name=NAME, prefix=PREFIX)
    header = re.findall(
        r"^\[dram-embeddings\] tokens=(\d+) samples=(\d+) table=(\d+) table_bytes=(\d+)$", log, re.M
    )
    if header != [(str(TOKENS), str(SAMPLES), str(TABLE), str(TABLE_BYTES))]:
        raise ValueError("Unexpected or duplicate sweep configuration")
    expected = expected_samples()
    rows = []
    seen = set()
    for line in log.splitlines():
        if not line.startswith(f"{PREFIX} cluster="):
            continue
        row = dict(field.split("=", 1) for field in line.split()[1:])
        row = {
            key: value if key in ("operation", "order") else int(value, 0)
            for key, value in row.items()
        }
        key = row["piece"], row["order"], row["sample"]
        if key not in expected or key in seen:
            raise ValueError(f"Unexpected or duplicate sample: {key}")
        configuration = (
            row["operation"],
            row["cluster"],
            row["channel"],
            row["tokens"],
            row["rows"],
        )
        if configuration != ("dma_read", 0, 0, TOKENS, TABLE_BYTES // row["piece"]):
            raise ValueError(f"Unexpected benchmark configuration: {key}")
        sample_ids = ids(*key)
        if (row["first_id"], row["last_id"]) != (sample_ids[0], sample_ids[-1]):
            raise ValueError(f"Unexpected token IDs: {key}")
        if row["errors"] or row["end_ps"] <= row["start_ps"]:
            raise ValueError(f"Data errors or invalid measurement interval: {key}")
        row["bytes"] = TOKENS * row["piece"]
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
    for piece in PIECES:
        for order in ORDERS:
            samples = [row for row in rows if (row["piece"], row["order"]) == (piece, order)]
            rates = [row["bytes"] / row["total_ns"] for row in samples]
            times = [row["total_ns"] / TOKENS for row in samples]
            result.append(
                {
                    "piece": piece,
                    "order": order,
                    "samples": len(samples),
                    "bytes_per_sample": TOKENS * piece,
                    "median_ns_per_token": median(times),
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
