"""Run with: python3 -m unittest discover -s <dram_common directory>."""

from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dram_common.analysis import query_measurements, require_success, summarize

NAME, PREFIX = "SoftHier test", "[test]"


def sample(**fields):
    row = dict(channel=0, operation="dma_read", bytes=64, repetitions=8)
    row.update(hbm_offset=4096, hbm_span=512, start_ps=100, end_ps=200)
    row.update(fields)
    return row


def query(transactions, row):
    """Build a one-channel DRAMSys database and query `row` against it.

    Each transaction is (command, address, generation_ps, strobe_begin_ps).
    """
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        with sqlite3.connect(directory / "DRAMSysRecordable0_test.tdb") as db:
            db.executescript("""
                CREATE TABLE GeneralInfo (UnitOfTime TEXT);
                INSERT INTO GeneralInfo VALUES ('PS');
                CREATE TABLE Transactions (ID INTEGER, Command TEXT, DataLength INTEGER,
                                           TimeOfGeneration INTEGER, Address INTEGER);
                CREATE TABLE Phases (Transact INTEGER, PhaseName TEXT, PhaseEnd INTEGER,
                                     DataStrobeBegin INTEGER, DataStrobeEnd INTEGER,
                                     Rank INTEGER);
            """)
            for index, (command, address, generated, strobe) in enumerate(transactions):
                db.execute(
                    "INSERT INTO Transactions VALUES (?, ?, 64, ?, ?)",
                    (index, command, generated, address),
                )
                db.execute("INSERT INTO Phases VALUES (?, 'RESP', 150, 0, 0, 0)", (index,))
                db.execute(
                    "INSERT INTO Phases VALUES (?, ?, 0, ?, ?, 0)",
                    (index, "RD" if command == "R" else "WR", strobe, strobe + 1000),
                )
        return query_measurements(("bytes",), [row], directory)


class AnalysisTest(unittest.TestCase):
    def test_requires_a_successful_run(self):
        log = f"{PREFIX} errors=0\n{NAME}: OK"
        require_success(log, NAME, PREFIX)
        for broken in (
            log.replace("errors=0", "errors=1"),
            log.replace("OK", "FAILED"),
            log.splitlines()[0],
        ):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                require_success(broken, NAME, PREFIX)

    def test_summary_reports_median_and_range_per_size(self):
        rows = [sample(bytes=64, total_ns=ns) for ns in (64, 32, 128)]
        rows.append(sample(bytes=128, total_ns=64))
        curve = summarize(rows, 64)
        self.assertEqual([row["bytes"] for row in curve], [64, 128])
        self.assertEqual(curve[0]["samples"], 3)
        self.assertEqual(
            (curve[0]["min_GB_per_s"], curve[0]["median_GB_per_s"], curve[0]["max_GB_per_s"]),
            (4, 8, 16),
        )
        self.assertEqual(curve[1]["nominal_utilization_percent"], 25)

    def test_query_counts_only_the_sample_addresses(self):
        reads = [("R", 4096 + i * 64, 110, 1000 + i * 1000) for i in range(8)]
        # Another sample's address, read in the same interval, is not counted.
        results = query([*reads, ("R", 8192, 110, 9000)], sample())
        self.assertEqual(results[0][1:4], ("R", 8, 512))
        self.assertEqual(results[0][-3:-1], ("8.000", "64.000000"))

    def test_query_rejects_a_setup_write_inside_the_window(self):
        reads = [("R", 4096 + i * 64, 110, 1000 + i * 1000) for i in range(8)]
        with self.assertRaises(ValueError):
            query([*reads, ("W", 4096, 120, 9000)], sample())


if __name__ == "__main__":
    unittest.main()
