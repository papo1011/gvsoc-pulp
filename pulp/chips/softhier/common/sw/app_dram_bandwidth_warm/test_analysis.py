"""Validate sweep completeness and plateau decisions using controlled curves."""

from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_dram_bandwidth_warm.summarize import SAMPLES, SIZES, measurements, plateau, summarize
from dram_common.analysis import query_measurements


def log_for_sweep():
    lines = []
    for size in SIZES:
        for sample in range(SAMPLES):
            start = (1 << 32) + SIZES.index(size) * 10000000000 + sample * 1000000000
            end = start + size * 1000
            lines.append(
                f"[dram-bandwidth] cluster=0 channel=0 operation=dma_read bytes={size} "
                f"repetitions=8 sample={sample} hbm_offset={0x04000000 - SIZES[-1]} "
                f"start_ps={start:#x} end_ps={end:#x} errors=0"
            )
    return "\n".join([*lines, "[dram-bandwidth] errors=0", "SoftHier DRAM bandwidth: OK"])


class BandwidthAnalysisTest(unittest.TestCase):
    def test_payload_units_and_nominal_utilization(self):
        rows = measurements(log_for_sweep())
        self.assertEqual(len(rows), 54)
        curve = summarize(rows, 64)
        self.assertEqual(len(curve), 18)
        for row in curve:
            self.assertEqual(row["median_GB_per_s"], 8)
            self.assertEqual(row["nominal_utilization_percent"], 12.5)
        self.assertEqual(plateau(curve), 4)

    def test_rejects_incomplete_duplicate_and_invalid_samples(self):
        log = log_for_sweep()
        lines = log.splitlines()
        for broken in (
            "\n".join(lines[1:]),
            "\n".join([lines[0], *lines]),
            log.replace("errors=0", "errors=1", 1),
            log.replace("channel=0", "channel=1", 1),
            log.replace("bytes=4 ", "bytes=3 ", 1),
            log.replace("repetitions=8", "repetitions=0", 1),
            log.replace("end_ps=0x100000fa0", "end_ps=0x1", 1),
        ):
            with self.subTest(broken=broken[:200]), self.assertRaises(ValueError):
                measurements(broken)

    def test_streaming_offsets_are_distinct_and_query_covers_all_eight_blocks(self):
        lines = log_for_sweep().splitlines()
        cursor = 0x02000000
        for index, (size, sample) in enumerate(
            (size, sample) for size in SIZES for sample in range(SAMPLES)
        ):
            alignment = max(size, 32)
            cursor = (cursor + alignment - 1) & ~(alignment - 1)
            lines[index] = lines[index].replace(
                f"hbm_offset={0x04000000 - SIZES[-1]}", f"pattern=streaming hbm_offset={cursor}"
            )
            cursor += 8 * size
        rows = measurements("\n".join(lines))
        ordered = sorted(rows, key=lambda row: row["hbm_offset"])
        for row in ordered:
            self.assertEqual(row["hbm_span"], 8 * row["bytes"])
        for first, second in zip(ordered, ordered[1:]):
            self.assertLessEqual(first["hbm_offset"] + first["hbm_span"], second["hbm_offset"])
        with self.assertRaises(ValueError):
            measurements("\n".join(lines).replace("pattern=streaming", "pattern=invalid", 1))

    def test_database_query_includes_the_last_streaming_block(self):
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
                    CREATE TABLE Phases (Transact INTEGER, PhaseName TEXT, PhaseEnd INTEGER);
                """)
                # The ninth block is outside the sample and must not be counted.
                for index in range(9):
                    db.execute(
                        "INSERT INTO Transactions VALUES (?, 'R', 64, 110, ?)",
                        (index, 4096 + index * 64),
                    )
                    db.execute("INSERT INTO Phases VALUES (?, 'RESP', 150)", (index,))
            results = query_measurements((), [row], directory)
        self.assertEqual(results[0][1:3], (8, 512))

    def test_extended_sweep_keeps_old_offsets_and_adds_disjoint_one_mib_windows(self):
        lines = []
        cursor = 0x02000000
        ranges = []
        for size in (*SIZES, 1 << 20):
            for sample in range(SAMPLES):
                if size == 1 << 20 and cursor >= 0x02000000:
                    cursor = 0x00800000
                alignment = max(32, size)
                cursor = (cursor + alignment - 1) & ~(alignment - 1)
                start = (len(lines) + 1) * 2000000000
                lines.append(
                    f"[dram-bandwidth] cluster=0 channel=0 operation=dma_read bytes={size} "
                    f"repetitions=8 sample={sample} pattern=streaming hbm_offset={cursor} "
                    f"start_ps={start} end_ps={start + size * 1000} errors=0"
                )
                ranges.append((cursor, cursor + 8 * size))
                cursor += 8 * size
        log = "\n".join(
            [
                "[dram-bandwidth] max_bytes=1048576",
                *lines,
                "[dram-bandwidth] errors=0",
                "SoftHier DRAM bandwidth: OK",
            ]
        )
        rows = measurements(log)
        self.assertEqual(len(rows), 57)
        self.assertEqual(len(summarize(rows, 64)), 19)
        self.assertEqual(
            [row["hbm_offset"] for row in rows[-3:]], [0x00800000, 0x01000000, 0x01800000]
        )
        ordered = sorted(ranges)
        self.assertTrue(all(first[1] <= second[0] for first, second in zip(ordered, ordered[1:])))
        with self.assertRaises(ValueError):
            measurements(log.replace(lines[-1] + "\n", ""))

    def test_plateau_requires_stable_tail_not_a_temporary_flat_spot(self):
        def curve(rates):
            return [{"bytes": 4 << i, "median_GB_per_s": rate} for i, rate in enumerate(rates)]

        self.assertIsNone(plateau(curve([10, 10, 10, 20, 30])))
        self.assertIsNone(plateau(curve([10, 20, 30, 40, 50])))
        self.assertEqual(plateau(curve([10, 20, 49, 49.5, 50])), 16)
        self.assertIsNone(plateau(curve([10, 20, 48, 49, 52])))


if __name__ == "__main__":
    unittest.main()
