# Local HBM reads around the TCDM capacity

Does bandwidth dip just above the cluster's **1 MiB TCDM**? Cluster 0 reads
datasets from local HBM 0, split into DMA copies that fit in TCDM, with the same
hardware and DRAM timings as the [streaming benchmark](../app_dram_bandwidth_streaming/README.md).

> **Not re-measured.** These results come from the synchronous wide-port
> arbiter, whose modeling artifact costs about 7 ns per 4 KiB burst; see the
> [explanation](../app_dram_bandwidth_streaming/README.md#wide-port-arbiter-model). The dip comes from the extra DMA copy, so it should
> persist with the asynchronous arbiter now in use, but absolute values and the
> size of the dip will change.

## Datasets and DMA copies

Datasets of **512, 1020, 1024, 1028, 1032, 1036 and 2048 KiB**, each read with
copies of at most **256 KiB** or **1 MiB**: three samples per case, 42 in total.
Copies reuse one TCDM buffer, waiting before overwriting it. The last copy
carries only the remaining bytes and is never padded:

```text
1024 KiB dataset, 1 MiB copies:  [1024 KiB]
1028 KiB dataset, 1 MiB copies:  [1024 KiB] + [4 KiB]
1036 KiB dataset, 1 MiB copies:  [1024 KiB] + [12 KiB]
2048 KiB dataset, 1 MiB copies:  [1024 KiB] + [1024 KiB]
```

A small final copy adds a whole DMA issue/wait for few bytes. With 256 KiB
copies, 1024 KiB needs four copies and 1028 KiB needs five. Both limits read the
same initialized 2 MiB region, without warm-up; their order alternates across
samples to reduce order bias.

## What is timed

**Bandwidth = dataset bytes / elapsed time**, including every DMA issue/wait,
the loop and clock reads. Setup, checks and printing are untimed. This is a
transfer-only benchmark: tiles are overwritten, not processed, and there is no
double buffering.

## Results

![Bandwidth and DMA copies around the TCDM limit](assets/bandwidth.png)

Median bandwidth (three samples per case):

| Dataset (KiB) | 256 KiB copies | 1 MiB copies | Copies (256 KiB / 1 MiB) |
| ---: | ---: | ---: | ---: |
| 512 | 57.274 GB/s | 57.538 GB/s | 2 / 1 |
| 1020 | 57.310 GB/s | 57.712 GB/s | 4 / 1 |
| 1024 | 57.318 GB/s | 57.716 GB/s | 4 / 1 |
| 1028 | 57.139 GB/s | 57.520 GB/s | 5 / 2 |
| 1032 | 57.188 GB/s | 57.580 GB/s | 5 / 2 |
| 1036 | 57.193 GB/s | 57.584 GB/s | 5 / 2 |
| 2048 | 57.340 GB/s | 57.738 GB/s | 8 / 2 |

**A small dip appears right above 1024 KiB.** With 1 MiB copies, bandwidth
drops from 57.716 to 57.520 GB/s at 1028 KiB (**−0.34%**), where the second copy
carries only 4 KiB. It recovers as the tail grows and reaches 57.738 GB/s with
two full copies at 2048 KiB. The 256 KiB limit dips when the fifth copy appears.
This is the end-to-end cost of segmentation, not a change in HBM bandwidth.

All 42 samples passed the data check. The DRAM audit confirmed, for every
sample, the exact byte count, unique 32 B reads and the full address range,
tails included. Full values are in [assets/bandwidth.csv](assets/bandwidth.csv).

## Data checks and buffers

HBM is initialized at offset 32 MiB with an address marker on each 64 KiB page;
a 100 µs wait lets setup writes drain. The 256 KiB buffer starts at TCDM offset
`0x20000`; the 1 MiB buffer starts at 0 and uses the whole TCDM (the app rejects
persistent `.l1` data). After timing, every word of the final tile is checked;
earlier tiles are verified by the DRAM audit. The parser rejects partial runs,
errors, wrong tail sizes and overlapping windows.

## Build and run

From the **SDK root**, after the [SoftHier setup](../../../README.md):

```bash
set -e
APP_DIR="$PWD/pulp/pulp/chips/softhier/common/sw/app_dram_bandwidth_tiled"
make sh-hw TOPOLOGY=2d_mesh
make sh-sw TOPOLOGY=2d_mesh app="$APP_DIR"
mkdir -p .local-artifacts/dram-bandwidth-tiled
RUN_DIR="$(mktemp -d "$PWD/.local-artifacts/dram-bandwidth-tiled/run.XXXXXX")"
cp sw_build/softhier.elf "$RUN_DIR/softhier.elf"
make sh-run TOPOLOGY=2d_mesh SH_TRACE_ARGS= RUN_ARGS="--work-dir=$RUN_DIR" \
    > "$RUN_DIR/simulation.log" 2>&1
python3 "$APP_DIR/summarize.py" "$RUN_DIR/simulation.log" > "$RUN_DIR/bandwidth.csv"
python3 "$APP_DIR/query_dram.py" "$RUN_DIR/simulation.log" "$RUN_DIR" > "$RUN_DIR/dram.csv"
```

`query_dram.py` needs `DatabaseRecording: true` in DRAMSys. A valid log contains
`[dram-tiled] errors=0` and `SoftHier tiled DRAM bandwidth: OK`. To plot:

```bash
pulp/.venv/bin/python "$APP_DIR/plot.py" "$RUN_DIR/bandwidth.csv"
```

The figure (PNG and SVG in `assets/`) shows bandwidth on top and copies per
dataset below; the left column covers 512–2048 KiB, the right zooms into
1020–1036 KiB. A vertical line marks the 1024 KiB TCDM capacity.
