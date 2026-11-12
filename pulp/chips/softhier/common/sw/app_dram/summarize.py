#!/usr/bin/env python3
"""Convert an app_dram simulation log to CSV; reject incomplete/failed runs."""

import argparse
import csv
import re
import sys
from pathlib import Path


FIELDS = ("location", "channel", "mesh_hops", "operation", "bytes", "repetitions")


def measurements(log):
    pending = None
    rows = []
    for line in log.splitlines():
        if line.startswith("[dram] location="):
            if pending is not None:
                raise ValueError("Missing performance counter before the next measurement")
            pending = dict(field.split("=", 1) for field in line.split()[1:])
        match = re.search(r"\[Performance Counter\]: Execution period is (\d+) ns", line)
        if match and pending is not None:
            ns = int(match[1])
            if ns == 0:
                raise ValueError("Performance counter returned zero")
            pending["total_ns"] = ns
            timestamps = re.search(r"start_ps=(\d+) end_ps=(\d+)", line)
            if timestamps:
                pending["start_ps"], pending["end_ps"] = map(int, timestamps.groups())
            rows.append(pending)
            pending = None
    if (
        pending is not None
        or len(rows) != 14
        or "SoftHier DRAM: OK" not in log
        or "SoftHier DRAM: FAILED" in log
    ):
        raise ValueError("Expected 14 measurements and a successful data check")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    args = parser.parse_args()
    try:
        rows = measurements(args.log.read_text())
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    writer = csv.writer(sys.stdout)
    writer.writerow((*FIELDS, "total_ns", "ns_per_operation", "payload_GB_per_s"))
    for row in rows:
        count = int(row["repetitions"])
        byte_count = int(row["bytes"]) * count
        if row["operation"] == "dma_write_readback":
            byte_count *= 2
        ns = row["total_ns"]
        writer.writerow(
            (*(row[field] for field in FIELDS), ns, f"{ns / count:.3f}", f"{byte_count / ns:.6f}")
        )


if __name__ == "__main__":
    main()
