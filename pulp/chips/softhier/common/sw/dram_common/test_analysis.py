"""Run with: python3 -m unittest discover -s <dram_common directory>."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_dram.summarize import measurements
from dram_common.analysis import payload_bytes


def original_log():
    lines = []
    for i in range(14):
        lines.extend(
            (
                "[dram] location=local channel=0 mesh_hops=0 operation=dma_read bytes=4096 "
                "repetitions=8",
                f"[Performance Counter]: Execution period is 10 ns "
                f"(start_ps={i * 10000} end_ps={(i + 1) * 10000})",
            )
        )
    return "\n".join([*lines, "[dram] errors=0", "SoftHier DRAM: OK"])


class AnalysisTest(unittest.TestCase):
    def test_original_log_remains_readable(self):
        rows = measurements(original_log())
        self.assertEqual(len(rows), 14)
        self.assertEqual(rows[0]["start_ps"], 0)
        self.assertEqual(payload_bytes(rows[0]), 8 * 4096)

    def test_rejects_failed_or_partial_runs(self):
        log = original_log()
        for broken in (
            log.replace("SoftHier DRAM: OK", "SoftHier DRAM: FAILED"),
            log.replace("[dram] errors=0", "[dram] errors=1"),
            "\n".join(log.splitlines()[2:]),
        ):
            with self.subTest(broken=broken[-60:]), self.assertRaises(ValueError):
                measurements(broken)

    def test_write_readback_counts_both_directions(self):
        row = {"operation": "dma_write_readback", "bytes": "64", "repetitions": "8"}
        self.assertEqual(payload_bytes(row), 2 * 8 * 64)


if __name__ == "__main__":
    unittest.main()
