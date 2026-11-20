"""Check the read accounting, plateau detection and rejection of invalid sweeps."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_dram_bandwidth_pipelined.summarize import (
    REPEATS,
    SAMPLES,
    SIZES,
    measurements,
    offsets,
)
from app_dram_bandwidth_warm.summarize import plateau, summarize


def complete_log(rate=lambda size: 10):
    """Synthetic sweep; `rate` gives bytes/ns for each size."""
    lines = ["[dram-pipelined] first_bytes=64 last_bytes=524288 samples=3 in_flight=8"]
    windows = offsets()
    start = 1_000_000
    for size in SIZES:
        for sample in range(SAMPLES):
            duration = round(REPEATS * size * 1000 / rate(size))
            lines.append(
                f"[dram-pipelined] cluster=0 channel=0 operation=dma_read bytes={size} "
                f"repetitions=8 sample={sample} hbm_offset={windows[size, sample]} "
                f"start_ps={start:#x} end_ps={start + duration:#x} errors=0"
            )
            start += duration + 1000
    return "\n".join([*lines, "[dram-pipelined] errors=0", "SoftHier pipelined DRAM bandwidth: OK"])


class PipelinedAnalysisTest(unittest.TestCase):
    def test_counts_samples_and_payload(self):
        rows = measurements(complete_log())
        self.assertEqual(len(rows), len(SIZES) * SAMPLES)
        curve = summarize(rows, 64)
        self.assertEqual([point["bytes"] for point in curve], list(SIZES))
        for point in curve:
            self.assertAlmostEqual(point["median_GB_per_s"], 10, places=3)
            self.assertEqual(point["samples"], SAMPLES)
        for row in rows:
            self.assertEqual(row["hbm_span"], 8 * row["bytes"])

    def test_detects_the_plateau(self):
        rows = measurements(complete_log(lambda size: 64 * size / (size + 4096)))
        self.assertEqual(plateau(summarize(rows, 64)), 131072)

    def test_rejects_invalid_or_partial_sweeps(self):
        log = complete_log()
        first = next(line for line in log.splitlines() if "cluster=" in line)
        invalid = (
            log.replace("last_bytes=524288", "last_bytes=1048576"),
            log.replace("errors=0", "errors=1", 1),
            log.replace("repetitions=8", "repetitions=1", 1),
            log.replace("operation=dma_read", "operation=dma_write", 1),
            log.replace("hbm_offset=4194304", "hbm_offset=4194368", 1),
            log.replace(first + "\n", ""),
            log + "\n" + first,
            log.replace("start_ps=0xf4240", "start_ps=0xfffff0", 1),
            log.replace("SoftHier pipelined DRAM bandwidth: OK", ""),
        )
        for broken in invalid:
            with self.subTest(broken=broken[:120]), self.assertRaises(ValueError):
                measurements(broken)


if __name__ == "__main__":
    unittest.main()
