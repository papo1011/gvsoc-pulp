"""Check the embeddings accounting and the rejection of invalid sweeps."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_dram_bandwidth_embeddings.summarize import (
    ORDERS,
    PIECES,
    SAMPLES,
    TABLE_BYTES,
    TOKENS,
    ids,
    measurements,
    summarize,
)


def complete_log(rate=lambda piece, order: 10):
    """Synthetic sweep; `rate` gives bytes/ns for each point."""
    lines = [
        f"[dram-embeddings] tokens=64 samples={SAMPLES} table=4194304 table_bytes={TABLE_BYTES}"
    ]
    start = 1_000_000
    for piece in PIECES:
        for order in ORDERS:
            for sample in range(SAMPLES):
                sample_ids = ids(piece, order, sample)
                duration = round(TOKENS * piece * 1000 / rate(piece, order))
                lines.append(
                    f"[dram-embeddings] cluster=0 channel=0 operation=dma_read order={order} "
                    f"piece={piece} tokens=64 sample={sample} rows={TABLE_BYTES // piece} "
                    f"first_id={sample_ids[0]} last_id={sample_ids[-1]} "
                    f"start_ps={start:#x} end_ps={start + duration:#x} errors=0"
                )
                start += duration + 1000
    return "\n".join([*lines, "[dram-embeddings] errors=0", "SoftHier DRAM embeddings: OK"])


class EmbeddingsAnalysisTest(unittest.TestCase):
    def test_one_point_per_piece_and_order(self):
        rows = measurements(complete_log(lambda piece, order: piece / 64))
        self.assertEqual(len(rows), len(PIECES) * len(ORDERS) * SAMPLES)
        curve = summarize(rows)
        self.assertEqual(len(curve), len(PIECES) * len(ORDERS))
        for point in curve:
            self.assertAlmostEqual(point["median_GB_per_s"], point["piece"] / 64, places=3)
            self.assertAlmostEqual(point["median_ns_per_token"], 64, places=2)
            self.assertEqual(point["samples"], SAMPLES)

    def test_ids_follow_the_firmware_layout(self):
        self.assertEqual(ids(256, "block", 0), list(range(64)))
        self.assertEqual(ids(256, "contiguous", 0)[0], 64)
        self.assertEqual(ids(512, "block", 0)[0], 2 * SAMPLES * TOKENS * 256 // 512)
        shuffled, ordered = ids(1024, "random", 3), ids(1024, "sorted", 3)
        self.assertEqual(ordered, sorted(ordered))
        self.assertNotEqual(sorted(shuffled), ordered)  # fresh rows, not the same ones sorted
        self.assertTrue(all(0 <= row < TABLE_BYTES // 1024 for row in shuffled))

    def test_rejects_invalid_or_partial_sweeps(self):
        log = complete_log()
        first = next(line for line in log.splitlines() if "cluster=" in line)
        invalid = (
            log.replace(first + "\n", ""),
            log.replace(first, first + "\n" + first),
            log.replace("tokens=64 samples", "tokens=32 samples"),
            log.replace("errors=0", "errors=1", 1),
            log.replace("first_id=0 ", "first_id=1 ", 1),
            log.replace("operation=dma_read", "operation=dma_write", 1),
            log.replace("SoftHier DRAM embeddings: OK", "SoftHier DRAM embeddings: FAILED"),
        )
        for broken in invalid:
            with self.assertRaises(ValueError):
                measurements(broken)


if __name__ == "__main__":
    unittest.main()
