#!/usr/bin/env python3
"""Summarize the single-cluster HBM benchmark."""

import re
import sys
from pathlib import Path

# Keep these scripts runnable directly from the SDK without installing a package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dram_common.analysis import require_success, summarize_main

FIELDS = ("location", "channel", "mesh_hops", "operation", "bytes", "repetitions")


def measurements(log):
    """Read the original baseline, whose descriptions precede timer output."""
    require_success(log)
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
    if pending is not None or len(rows) != 14:
        raise ValueError("Expected 14 baseline measurements")
    return rows


if __name__ == "__main__":
    summarize_main(FIELDS, measurements)
