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
    samples,
)
from app_dram_bandwidth_pipelined.query_trace import bursts, size_spec, spacing
from dram_common.analysis import plateau, summarize


def complete_log(rate=lambda size: 10, small=(0, 3)):
    """Synthetic sweep; `rate` gives bytes/ns for each size, `small` is
    (small_bytes, small_samples), (0, 3) for a log from before the extra samples."""
    header = "[dram-pipelined] first_bytes=4 last_bytes=1048576 samples=3 in_flight=8"
    if small[0]:
        header += f" small_bytes={small[0]} small_samples={small[1]}"
    lines = [header]
    windows = offsets(*small)
    start = 1_000_000
    for size in SIZES:
        for sample in range(samples(size, *small)):
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

    def test_small_blocks_get_more_samples(self):
        rows = measurements(complete_log(small=(8192, 15)))
        self.assertEqual(len(rows), 12 * 15 + 7 * 3)
        curve = summarize(rows, 64)
        self.assertEqual([point["samples"] for point in curve], [15] * 12 + [3] * 7)

    def test_detects_the_plateau(self):
        rows = measurements(complete_log(lambda size: 64 * size / (size + 4096)))
        self.assertEqual(plateau(summarize(rows, 64)), 131072)

    def test_rejects_invalid_or_partial_sweeps(self):
        log = complete_log()
        first = next(line for line in log.splitlines() if "cluster=" in line)
        invalid = (
            log.replace("last_bytes=1048576", "last_bytes=524288"),
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


def request(begin_ns, bank_group, pseudo_channel=0):
    begin = round(begin_ns * 1000)
    return dict(
        bytes=512,
        sample=0,
        pseudo_channel=pseudo_channel,
        bank_group=bank_group,
        burst_begin_ps=begin,
        burst_end_ps=begin + 1000,
    )


class TraceTest(unittest.TestCase):
    def test_spacing_rule_and_overlap(self):
        # Same group 1 ns apart (L), another group 0.5 ns later (S, overlapping),
        # and a burst on the other pseudo-channel that must not count.
        trace = [request(0, 4), request(1, 4), request(1.5, 6), request(0.2, 4, 1)]
        channel = bursts(trace, 0)
        self.assertEqual([b.get("rule") for b in channel], [None, "L", "S"])
        self.assertEqual([b.get("overlap") for b in channel], [None, False, True])
        self.assertEqual(channel[2]["gap_ps"], 500)
        self.assertEqual(channel[2]["from_bank_group"], 4)

    def test_spacing_counts_both_pseudo_channels(self):
        trace = [request(0, 4), request(1, 5), request(0, 4, 1), request(0.5, 5, 1)]
        counts = {(r["rule"], r["gap_ns"], r["overlap"]): r["count"] for r in spacing(trace)}
        self.assertEqual(counts, {("S", 1.0, 0): 1, ("S", 0.5, 1): 1})

    def test_size_with_address_window(self):
        self.assertEqual(size_spec("512"), (512, None))
        self.assertEqual(size_spec("1048576:0x3ff800:0x400800"), (1 << 20, (0x3FF800, 0x400800)))


if __name__ == "__main__":
    unittest.main()
