"""Shared CSV and DRAMSys analysis for the two HBM benchmarks."""

import argparse
import csv
import sqlite3
import sys
from contextlib import ExitStack, closing
from pathlib import Path


def require_success(log, name="SoftHier DRAM", prefix="[dram]"):
    if f"{prefix} errors=0" not in log or f"{name}: OK" not in log or f"{name}: FAILED" in log:
        raise ValueError("Expected a successful data check")


def payload_bytes(row):
    directions = 2 if row["operation"] == "dma_write_readback" else 1
    return int(row["bytes"]) * int(row["repetitions"]) * directions


# Select requests generated during the CPU's measurement, including their
# responses even if they finish after the interval. X denotes maintenance
# transactions (e.g. refresh), not program reads/writes.
QUERY = """
SELECT t.Command, COUNT(*), SUM(t.DataLength),
       AVG(p.PhaseEnd - t.TimeOfGeneration),
       MIN(p.PhaseEnd - t.TimeOfGeneration),
       MAX(p.PhaseEnd - t.TimeOfGeneration)
FROM Transactions AS t
JOIN Phases AS p ON p.Transact = t.ID AND p.PhaseName = 'RESP'
WHERE t.Command IN ('R', 'W')
  AND t.TimeOfGeneration >= ? AND t.TimeOfGeneration < ?
  AND t.Address >= ? AND t.Address < ?
GROUP BY t.Command
ORDER BY t.Command
"""


def query_measurements(fields, measurements, directory):
    results = []
    with ExitStack() as stack:
        connections = {}
        for row in measurements:
            if "start_ps" not in row:
                raise ValueError(
                    "Rebuild the hardware: performance counters need absolute timestamps"
                )
            channel = row["channel"]
            if channel not in connections:
                paths = list(directory.glob(f"DRAMSysRecordable{channel}_*.tdb"))
                if len(paths) != 1:
                    raise ValueError(
                        f"Expected one database for channel {channel}, found {len(paths)}"
                    )
                connection = stack.enter_context(
                    closing(sqlite3.connect(paths[0].resolve().as_uri() + "?mode=ro", uri=True))
                )
                unit = connection.execute("SELECT UnitOfTime FROM GeneralInfo").fetchone()
                if unit != ("PS",):
                    raise ValueError(f"Expected picosecond timestamps, got {unit}")
                connections[channel] = connection
            # Shared-channel intervals overlap. The private HBM slot identifies
            # this worker's requests without counting another worker's traffic.
            lower = row.get("hbm_offset", 0)
            upper = (
                lower + int(row.get("hbm_span", row["bytes"])) if "hbm_offset" in row else 1 << 62
            )
            stats = (
                connections[channel]
                .execute(QUERY, (row["start_ps"], row["end_ps"], lower, upper))
                .fetchall()
            )
            commands = {stat[0] for stat in stats}
            expected = {"R", "W"} if row["operation"] == "dma_write_readback" else {"R"}
            if commands != expected:
                raise ValueError(f"Expected directions {expected}, found {commands}")
            for command, count, byte_count, avg, minimum, maximum in stats:
                results.append(
                    (
                        *(row[field] for field in fields),
                        command,
                        count,
                        byte_count,
                        f"{avg / 1000:.3f}",
                        f"{minimum / 1000:.3f}",
                        f"{maximum / 1000:.3f}",
                        f"{byte_count * 1000 / (row['end_ps'] - row['start_ps']):.6f}",
                    )
                )
    return results


def summarize_main(fields, measurements, aggregate=None, aggregate_fields=()):
    parser = argparse.ArgumentParser(description="Summarize HBM benchmark measurements")
    parser.add_argument("log", type=Path)
    if aggregate is not None:
        parser.add_argument(
            "--aggregate", action="store_true", help="report one row per concurrent case"
        )
    args = parser.parse_args()
    try:
        rows = measurements(args.log.read_text())
        if getattr(args, "aggregate", False):
            header = (
                *aggregate_fields,
                "total_ns",
                "payload_GB_per_s",
                "start_skew_ns",
                "slowest_cluster_ns",
            )
            output = list(aggregate(rows))
        else:
            header = (*fields, "total_ns", "ns_per_operation", "payload_GB_per_s")
            output = [
                (
                    *(row[field] for field in fields),
                    f"{row['total_ns']:.3f}",
                    f"{row['total_ns'] / int(row['repetitions']):.3f}",
                    f"{payload_bytes(row) / row['total_ns']:.6f}",
                )
                for row in rows
            ]
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"{error}\n")
    writer = csv.writer(sys.stdout)
    writer.writerow(header)
    writer.writerows(output)


def query_main(fields, measurements):
    parser = argparse.ArgumentParser(description="Query DRAMSys records for an HBM benchmark")
    parser.add_argument("log", type=Path)
    parser.add_argument("database_dir", type=Path)
    args = parser.parse_args()
    try:
        rows = query_measurements(fields, measurements(args.log.read_text()), args.database_dir)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"{error}\n")
    writer = csv.writer(sys.stdout)
    writer.writerow(
        (
            *fields,
            "command",
            "transactions",
            "dram_bytes",
            "avg_latency_ns",
            "min_latency_ns",
            "max_latency_ns",
            "dram_GB_per_s",
        )
    )
    writer.writerows(rows)
