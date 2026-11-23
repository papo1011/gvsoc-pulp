"""Validate sweep completeness, offsets, DRAM queries and plateau decisions."""

from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_dram_bandwidth_sequential.summarize import SAMPLES, SIZES, measurements, offsets
from dram_common.analysis import plateau, query_measurements, summarize


def log_for_sweep(rate=lambda size: 8):
    """Synthetic complete sweep; `rate` gives bytes/ns for each size."""
    lines = ["[dram-bandwidth] max_bytes=1048576"]
    windows = offsets()
    start = 1 << 32
    for size in SIZES:
        for sample in range(SAMPLES):
            end = start + round(8 * size * 1000 / rate(size))
            lines.append(
                f"[dram-bandwidth] cluster=0 channel=0 operation=dma_read bytes={size} "
                f"repetitions=8 sample={sample} pattern=sequential "
                f"hbm_offset={windows[size, sample]} start_ps={start:#x} end_ps={end:#x} errors=0"
            )
            start = end + 1000
    return "\n".join([*lines, "[dram-bandwidth] errors=0", "SoftHier DRAM bandwidth: OK"])


class SequentialAnalysisTest(unittest.TestCase):
    def test_payload_units_and_nominal_utilization(self):
        rows = measurements(log_for_sweep())
        self.assertEqual(len(rows), 57)
        curve = summarize(rows, 64)
        self.assertEqual([row["bytes"] for row in curve], list(SIZES))
        for row in curve:
            self.assertAlmostEqual(row["median_GB_per_s"], 8, places=3)
            self.assertAlmostEqual(row["nominal_utilization_percent"], 12.5, places=3)

    def test_windows_are_distinct_and_one_mib_samples_move_below_32_mib(self):
        rows = measurements(log_for_sweep())
        self.assertEqual(
            [row["hbm_offset"] for row in rows[-3:]], [0x00800000, 0x01000000, 0x01800000]
        )
        ordered = sorted(rows, key=lambda row: row["hbm_offset"])
        for row in ordered:
            self.assertEqual(row["hbm_span"], 8 * row["bytes"])
        for first, second in zip(ordered, ordered[1:]):
            self.assertLessEqual(first["hbm_offset"] + first["hbm_span"], second["hbm_offset"])

    def test_accepts_logs_from_before_the_rename(self):
        log = log_for_sweep().replace("pattern=sequential", "pattern=streaming")
        self.assertEqual(len(measurements(log)), 57)

    def test_rejects_incomplete_duplicate_and_invalid_samples(self):
        log = log_for_sweep()
        lines = log.splitlines()
        first = lines[1]
        for broken in (
            log.replace(first + "\n", ""),
            log.replace(first, first + "\n" + first),
            log.replace("[dram-bandwidth] max_bytes=1048576\n", ""),
            log.replace("max_bytes=1048576", "max_bytes=524288"),
            log.replace("errors=0", "errors=1", 1),
            log.replace("channel=0", "channel=1", 1),
            log.replace("repetitions=8", "repetitions=1", 1),
            log.replace("pattern=sequential", "pattern=warm", 1),
            log.replace("hbm_offset=33554432", "hbm_offset=33554464", 1),
            log.replace("SoftHier DRAM bandwidth: OK", ""),
        ):
            with self.subTest(broken=broken[:200]), self.assertRaises(ValueError):
                measurements(broken)

    def test_database_query_includes_the_last_block(self):
        row = dict(
            channel=0,
            operation="dma_read",
            bytes=64,
            repetitions=8,
            hbm_offset=4096,
            hbm_span=512,
            start_ps=100,
            end_ps=200,
        )
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
                # The ninth block is outside the sample and must not be counted.
                for index in range(9):
                    db.execute(
                        "INSERT INTO Transactions VALUES (?, 'R', 64, 110, ?)",
                        (index, 4096 + index * 64),
                    )
                    db.execute("INSERT INTO Phases VALUES (?, 'RESP', 150, 0, 0, 0)", (index,))
                    # Two bursts on alternating pseudo-channels, 2 ns per pair.
                    db.execute(
                        "INSERT INTO Phases VALUES (?, 'RD', 0, ?, ?, ?)",
                        (index, 1000 + index * 1000, 2000 + index * 1000, index % 2),
                    )
            results = query_measurements((), [row], directory)
        self.assertEqual(results[0][1:3], (8, 512))
        # Bursts 0..7 span 1..9 ns: 512 B / 8 ns, each pseudo-channel busy half the time.
        self.assertEqual(results[0][-3:], ("8.000", "64.000000", "50.00"))

    def test_plateau_requires_stable_tail_not_a_temporary_flat_spot(self):
        def curve(rates):
            return [{"bytes": 4 << i, "median_GB_per_s": rate} for i, rate in enumerate(rates)]

        self.assertIsNone(plateau(curve([10, 10, 10, 20, 30])))
        self.assertIsNone(plateau(curve([10, 20, 30, 40, 50])))
        self.assertEqual(plateau(curve([10, 20, 49, 49.5, 50])), 16)
        self.assertIsNone(plateau(curve([10, 20, 48, 49, 52])))
        rows = measurements(log_for_sweep(lambda size: 64 * size / (size + 4096)))
        self.assertEqual(plateau(summarize(rows, 64)), 131072)


if __name__ == "__main__":
    unittest.main()
