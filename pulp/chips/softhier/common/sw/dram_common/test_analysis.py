"""Run with: python3 -m unittest discover -s <dram_common directory>."""

from itertools import product
from pathlib import Path
import sqlite3
import tempfile
import unittest

# Exercise both parsers in one process to catch cross-app import collisions.
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_dram.summarize import measurements
from app_dram_multi.query_dram import query
from app_dram_multi.summarize import (
    COUNTS,
    OPERATIONS,
    SCENARIOS,
    SIZES,
    aggregate,
    measurements as multi_measurements,
)
from dram_common.analysis import payload_bytes


def sweep_log():
    """Give each case its own window, with overlapping workers and known skew."""
    lines = []
    for case, (scenario, count, operation, size) in enumerate(
        product(SCENARIOS, COUNTS, OPERATIONS, SIZES)
    ):
        for cluster in range(count):
            channel = {
                "local": cluster,
                "remote": ((cluster // 4 + 2) % 4) * 4 + (cluster % 4 + 2) % 4,
                "shared": 15,
            }[scenario]
            hops = abs(cluster % 4 - channel % 4) + abs(cluster // 4 - channel // 4)
            start = 1000000 * (case + 1) + cluster * 1000
            offset = 0x04000000 - (cluster + 2) * 65536 - 4096
            lines.append(
                f"[dram-multi] scenario={scenario} active_clusters={count} cluster={cluster} "
                f"channel={channel} mesh_hops={hops} operation={operation} bytes={size} "
                f"repetitions=8 hbm_offset={offset} start_ps={start} end_ps={start + 100000} errors=0"
            )
    return "\n".join([*lines, "[dram-multi] errors=0", "SoftHier DRAM multi: OK"])


class AnalysisTest(unittest.TestCase):
    def test_aggregate_uses_full_completion_window(self):
        rows = multi_measurements(sweep_log())
        groups = list(aggregate(rows))
        self.assertEqual(len(rows), 360)
        self.assertEqual(len(groups), 48)
        for group in groups:
            scenario, count, operation, size, repetitions, ns, rate, skew, slowest = group
            self.assertEqual(float(ns), 100 + count - 1)
            self.assertEqual(float(skew), count - 1)
            self.assertEqual(float(slowest), 100)
            directions = 2 if operation == "dma_write_readback" else 1
            self.assertAlmostEqual(
                float(rate), count * size * repetitions * directions / float(ns), places=5
            )

    def test_rejects_partial_duplicate_or_failed_runs(self):
        lines = sweep_log().splitlines()
        for log in (
            "\n".join(lines[1:]),
            "\n".join([lines[0], *lines]),
            sweep_log().replace("errors=0", "errors=1", 1),
            sweep_log().replace("SoftHier DRAM multi: OK", "SoftHier DRAM multi: FAILED"),
            sweep_log().replace("end_ps=1100000", "end_ps=1", 1),
        ):
            with self.subTest(log=log[:150]), self.assertRaises(ValueError):
                multi_measurements(log)

    def test_shared_channel_queries_do_not_count_other_workers(self):
        log = sweep_log()
        rows = multi_measurements(log)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for channel in range(16):
                with sqlite3.connect(directory / f"DRAMSysRecordable{channel}_test.tdb") as db:
                    db.executescript("""
                        CREATE TABLE GeneralInfo (UnitOfTime TEXT);
                        INSERT INTO GeneralInfo VALUES ('PS');
                        CREATE TABLE Transactions (ID INTEGER, Command TEXT, DataLength INTEGER,
                                                   TimeOfGeneration INTEGER, Address INTEGER);
                        CREATE TABLE Phases (Transact INTEGER, PhaseName TEXT, PhaseEnd INTEGER);
                    """)
                    for identifier, row in enumerate(rows):
                        if row["channel"] != channel:
                            continue
                        commands = "RW" if row["operation"] == "dma_write_readback" else "R"
                        for direction, command in enumerate(commands):
                            tx = identifier * 2 + direction
                            db.execute(
                                "INSERT INTO Transactions VALUES (?, ?, ?, ?, ?)",
                                (
                                    tx,
                                    command,
                                    row["bytes"] * row["repetitions"],
                                    row["start_ps"] + 20000,
                                    row["hbm_offset"],
                                ),
                            )
                            # Responses beyond the CPU interval still belong to
                            # requests issued by this worker during its window.
                            db.execute(
                                "INSERT INTO Phases VALUES (?, 'RESP', ?)",
                                (tx, row["end_ps"] + 10000),
                            )
            results = query(log, directory)
        self.assertEqual(len(results), 540)
        self.assertEqual(sum(row[-5] for row in results), sum(map(payload_bytes, rows)))
        self.assertTrue(all(row[-6] == 1 for row in results))

    def test_original_log_remains_readable(self):
        lines = []
        for i in range(14):
            lines.extend(
                (
                    "[dram] location=local channel=0 mesh_hops=0 operation=dma_read bytes=4096 repetitions=8",
                    f"[Performance Counter]: Execution period is 10 ns (start_ps={i * 10000} end_ps={(i + 1) * 10000})",
                )
            )
        log = "\n".join([*lines, "[dram] errors=0", "SoftHier DRAM: OK"])
        self.assertEqual(len(measurements(log)), 14)
        with self.assertRaises(ValueError):
            multi_measurements(log)
        with self.assertRaises(ValueError):
            measurements(sweep_log())


if __name__ == "__main__":
    unittest.main()
