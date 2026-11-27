"""Check the matmul tile-load accounting and the rejection of invalid sweeps."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_dram_bandwidth_matmul.summarize import (
    COPY_BYTES,
    REPEATS,
    RUNS,
    SAMPLES,
    layout,
    measurements,
    points,
    summarize,
)


def complete_log(rate=lambda run, stride: 10):
    """Synthetic sweep; `rate` gives bytes/ns for each (run, stride) point."""
    lines = [f"[dram-matmul] copy_bytes={COPY_BYTES} samples={SAMPLES} in_flight={REPEATS}"]
    start = 1_000_000
    for run, stride in points():
        copy_lines, height = layout(run, stride)
        for sample in range(SAMPLES):
            duration = round(REPEATS * copy_lines * run * 1000 / rate(run, stride))
            lines.append(
                f"[dram-matmul] cluster=0 channel=0 operation=dma_read_2d run={run} "
                f"stride={stride} lines={copy_lines} repetitions={REPEATS} height={height} "
                f"sample={sample} first_line={sample * REPEATS * copy_lines} "
                f"start_ps={start:#x} end_ps={start + duration:#x} errors=0"
            )
            start += duration + 1000
    return "\n".join([*lines, "[dram-matmul] errors=0", "SoftHier matmul DRAM bandwidth: OK"])


class MatmulAnalysisTest(unittest.TestCase):
    def test_one_point_per_run_and_stride(self):
        rows = measurements(complete_log(lambda run, stride: run / 64))
        self.assertEqual(len(rows), len(points()) * SAMPLES)
        curve = summarize(rows)
        self.assertEqual([(p["run"], p["stride"]) for p in curve], points())
        for point in curve:
            self.assertAlmostEqual(point["median_GB_per_s"], point["run"] / 64, places=3)
            self.assertEqual(point["packed"], int(point["run"] == point["stride"]))

    def test_packed_rows_first_and_the_extra_point_last(self):
        # Stride 0 in the firmware means stride = run: the same rows packed.
        self.assertEqual(points()[: len(RUNS)], [(run, run) for run in RUNS])
        self.assertEqual(points()[-1], (1792, 28672))

    def test_rejects_invalid_or_partial_sweeps(self):
        log = complete_log()
        first = next(line for line in log.splitlines() if "cluster=" in line)
        invalid = (
            log.replace(first + "\n", ""),
            log.replace(first, first + "\n" + first),
            log.replace(f"samples={SAMPLES}", "samples=2", 1),
            log.replace("errors=0", "errors=1", 1),
            log.replace("first_line=0 ", "first_line=1 ", 1),
            log.replace("operation=dma_read_2d", "operation=dma_read", 1),
            log.replace(
                "SoftHier matmul DRAM bandwidth: OK", "SoftHier matmul DRAM bandwidth: FAILED"
            ),
        )
        for broken in invalid:
            with self.assertRaises(ValueError):
                measurements(broken)


if __name__ == "__main__":
    unittest.main()
