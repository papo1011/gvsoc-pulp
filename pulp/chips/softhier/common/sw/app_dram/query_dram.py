#!/usr/bin/env python3
"""Query DRAMSys SQLite records for each timed app_dram measurement."""

import argparse
import csv
import sqlite3
import sys
from contextlib import ExitStack, closing
from pathlib import Path

from summarize import FIELDS, measurements


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
GROUP BY t.Command
ORDER BY t.Command
"""


def query(log, directory):
    results = []
    with ExitStack() as stack:
        connections = {}
        for row in measurements(log):
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
            stats = connections[channel].execute(QUERY, (row["start_ps"], row["end_ps"])).fetchall()
            if not stats:
                raise ValueError(f"No DRAM transactions in the interval for {row}")
            for command, count, byte_count, avg, minimum, maximum in stats:
                results.append(
                    (
                        *(row[field] for field in FIELDS),
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("database_dir", type=Path)
    args = parser.parse_args()
    try:
        rows = query(args.log.read_text(), args.database_dir)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"{error}\n")
    writer = csv.writer(sys.stdout)
    writer.writerow(
        (
            *FIELDS,
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


if __name__ == "__main__":
    main()
