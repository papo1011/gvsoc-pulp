#!/usr/bin/env python3
"""Summarize the multi-cluster HBM DMA sweep."""

from itertools import product
import sys
from pathlib import Path

# Keep these scripts runnable directly from the SDK without installing a package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dram_common.analysis import payload_bytes, require_success, summarize_main

FIELDS = (
    "scenario",
    "active_clusters",
    "cluster",
    "channel",
    "mesh_hops",
    "operation",
    "bytes",
    "repetitions",
    "hbm_offset",
)
CASE_FIELDS = ("scenario", "active_clusters", "operation", "bytes", "repetitions")
COUNTS = (2, 4, 8, 16)
SCENARIOS = ("local", "remote", "shared")
SIZES = (4096, 65536)
OPERATIONS = ("dma_read", "dma_write_readback")


def measurements(log):
    """Require the complete sweep, including every worker exactly once per case."""
    require_success(log, name="SoftHier DRAM multi", prefix="[dram-multi]")
    expected = {
        (scenario, count, operation, size, cluster)
        for scenario, count, operation, size in product(SCENARIOS, COUNTS, OPERATIONS, SIZES)
        for cluster in range(count)
    }
    rows = []
    seen = set()
    for line in log.splitlines():
        if not line.startswith("[dram-multi] scenario="):
            continue
        row = dict(field.split("=", 1) for field in line.split()[1:])
        required = (*FIELDS, "start_ps", "end_ps", "errors")
        if any(field not in row for field in required):
            raise ValueError("Incomplete multi-cluster measurement")
        for field in required:
            if field not in ("scenario", "operation"):
                row[field] = int(row[field], 0)
        key = tuple(row[field] for field in (*CASE_FIELDS[:4], "cluster"))
        if key not in expected or key in seen:
            raise ValueError(f"Unexpected or duplicate multi-cluster measurement: {key}")
        if row["errors"] or row["end_ps"] <= row["start_ps"] or row["repetitions"] != 8:
            raise ValueError(f"Failed data check or invalid interval: {key}")
        cluster = row["cluster"]
        remote = ((cluster // 4 + 2) % 4) * 4 + (cluster % 4 + 2) % 4
        channel = {"local": cluster, "remote": remote, "shared": 15}[row["scenario"]]
        hops = abs(cluster % 4 - channel % 4) + abs(cluster // 4 - channel // 4)
        if row["channel"] != channel or row["mesh_hops"] != hops:
            raise ValueError(f"Unexpected destination or distance: {key}")
        if row["hbm_offset"] != 0x04000000 - (cluster + 2) * 65536 - 4096:
            raise ValueError(f"Unexpected HBM buffer slot: {key}")
        row["total_ns"] = (row["end_ps"] - row["start_ps"]) / 1000
        seen.add(key)
        rows.append(row)
    if seen != expected:
        raise ValueError(f"Expected 360 multi-cluster measurements, found {len(seen)}")
    return rows


def aggregate(rows):
    """Use wall time from first start to last finish, not a sum of worker rates."""
    groups = {}
    for row in rows:
        key = tuple(row[field] for field in CASE_FIELDS)
        groups.setdefault(key, []).append(row)
    for key, workers in groups.items():
        start = min(row["start_ps"] for row in workers)
        end = max(row["end_ps"] for row in workers)
        ns = (end - start) / 1000
        skew = (max(row["start_ps"] for row in workers) - start) / 1000
        yield (
            *key,
            f"{ns:.3f}",
            f"{sum(map(payload_bytes, workers)) / ns:.6f}",
            f"{skew:.3f}",
            f"{max(row['total_ns'] for row in workers):.3f}",
        )


if __name__ == "__main__":
    summarize_main(FIELDS, measurements, aggregate, CASE_FIELDS)
