"""Shared CSV and DRAMSys analysis for the HBM benchmarks."""

import argparse
import csv
import sqlite3
import sys
from contextlib import ExitStack, closing
from pathlib import Path
from statistics import median


def require_success(log, name="SoftHier DRAM", prefix="[dram]"):
    if f"{prefix} errors=0" not in log or f"{name}: OK" not in log or f"{name}: FAILED" in log:
        raise ValueError("Expected a successful data check")


def payload_bytes(row):
    directions = 2 if row["operation"] == "dma_write_readback" else 1
    return int(row["bytes"]) * int(row["repetitions"]) * directions


# Select requests generated during the CPU's measurement, including their
# responses even if they finish after the interval. X denotes maintenance
# transactions (e.g. refresh), not program reads/writes. The column command's
# data strobe is when its burst occupies the data bus of its pseudo-channel
# (Rank): from the first burst's start to the last burst's end is the DRAM-side
# window, without the cost of reaching the DRAM or returning the data.
QUERY = """
SELECT t.Command, COUNT(*), SUM(t.DataLength),
       AVG(p.PhaseEnd - t.TimeOfGeneration),
       MIN(p.PhaseEnd - t.TimeOfGeneration),
       MAX(p.PhaseEnd - t.TimeOfGeneration),
       MIN(d.DataStrobeBegin), MAX(d.DataStrobeEnd),
       SUM(d.DataStrobeEnd - d.DataStrobeBegin), COUNT(DISTINCT d.Rank)
FROM Transactions AS t
JOIN Phases AS p ON p.Transact = t.ID AND p.PhaseName = 'RESP'
JOIN Phases AS d ON d.Transact = t.ID AND d.PhaseName IN ('RD', 'RDA', 'WR', 'WRA')
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
            for command, count, byte_count, avg, minimum, maximum, *bus in stats:
                bus_begin, bus_end, busy, pseudo_channels = bus
                window = bus_end - bus_begin
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
                        f"{window / 1000:.3f}",
                        f"{byte_count * 1000 / window:.6f}",
                        f"{busy * 100 / (window * pseudo_channels):.2f}",
                    )
                )
    return results


def summarize(rows, peak):
    """One point per size: median, min and max bandwidth of its samples."""
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


def summarize_main(fields, measurements):
    parser = argparse.ArgumentParser(description="Summarize HBM benchmark measurements")
    parser.add_argument("log", type=Path)
    args = parser.parse_args()
    try:
        rows = measurements(args.log.read_text())
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
            "bus_window_ns",
            "bus_GB_per_s",
            "bus_occupancy_percent",
        )
    )
    writer.writerows(rows)
