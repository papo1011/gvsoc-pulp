"""Check dataset accounting and reject invalid or incomplete tiled sweeps."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_dram_bandwidth_tiled.summarize import (
    DMA_LIMITS,
    HBM_OFFSET,
    SAMPLES,
    SIZES,
    measurements,
    summarize,
)


def complete_log():
    lines = ["[dram-tiled] tcdm_bytes=1048576 max_bytes=2097152"]
    for size in SIZES:
        for sample in range(SAMPLES):
            for limit in DMA_LIMITS:
                start = len(lines) * 1000000000
                commands = (size + limit - 1) // limit
                last_bytes = size - (commands - 1) * limit
                lines.append(
                    f"[dram-tiled] cluster=0 channel=0 operation=dma_read bytes={size} dma_limit_bytes={limit} dma_commands={commands} last_tile_bytes={last_bytes} repetitions=1 sample={sample} hbm_offset={HBM_OFFSET} start_ps={start} end_ps={start + size * 100} errors=0"
                )
    return "\n".join([*lines, "[dram-tiled] errors=0", "SoftHier tiled DRAM bandwidth: OK"])


class TiledAnalysisTest(unittest.TestCase):
    def test_counts_capacity_crossing_and_dataset_payload(self):
        rows = measurements(complete_log())
        self.assertEqual(len(rows), 42)
        curve = summarize(rows)
        self.assertEqual(len(curve), 14)
        for point in curve:
            self.assertEqual(point["median_GB_per_s"], 10)
            self.assertEqual(point["median_ns_per_dataset"], point["bytes"] / 10)
            self.assertEqual(point["samples"], 3)
        full = [point for point in curve if point["dma_limit_bytes"] == 1 << 20]
        self.assertEqual([point["dma_commands"] for point in full], [1, 1, 1, 2, 2, 2, 2])
        self.assertEqual(
            [point["last_tile_bytes"] for point in full],
            [size * 1024 for size in (512, 1020, 1024, 4, 8, 12, 1024)],
        )
        for row in rows:
            self.assertEqual(row["hbm_span"], row["bytes"])
            self.assertEqual(row["hbm_offset"], HBM_OFFSET)

    def test_rejects_wrong_limits_counts_errors_and_partial_runs(self):
        log = complete_log()
        first = log.splitlines()[1]
        invalid = (
            log.replace("tcdm_bytes=1048576", "tcdm_bytes=2097152"),
            log.replace("dma_limit_bytes=262144", "dma_limit_bytes=2097152", 1),
            log.replace("dma_commands=2", "dma_commands=1", 1),
            log.replace("last_tile_bytes=4096", "last_tile_bytes=1048576", 1),
            log.replace("errors=0", "errors=1", 1),
            log.replace("repetitions=1", "repetitions=8", 1),
            log.replace(first + "\n", ""),
            log + "\n" + first,
            log.replace("start_ps=1000000000", "start_ps=2000000000", 1),
            log.replace("SoftHier tiled DRAM bandwidth: OK", ""),
        )
        for broken in invalid:
            with self.subTest(broken=broken[:150]), self.assertRaises(ValueError):
                measurements(broken)


if __name__ == "__main__":
    unittest.main()
