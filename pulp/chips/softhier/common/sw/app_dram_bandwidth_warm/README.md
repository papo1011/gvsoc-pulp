# Local HBM bandwidth: warm reads

This benchmark asks: **how fast can one cluster reread the same HBM block as
its size grows?** The DM core of cluster 0 reads local HBM 0 into TCDM.

## Warm or streaming?

There are **two differences** from the
[streaming app](../app_dram_bandwidth_streaming/README.md): the read warm-up and
the source addresses used during measurement. For a block size of `N` bytes:

```text
Warm:       read A once outside the timer, then time A, A, A, ..., A
Streaming:  no read warm-up; time A, A+N, A+2N, ..., A+7N
```

The warm-up happens **before every sample**. It can leave useful DRAM rows open,
so the first timed read may avoid opening them again. However, a large block
visits many rows: the last rows left open may differ from those needed when the
next read restarts at the beginning. All eight timed copies still read HBM.

Both apps overwrite the same TCDM buffer and **wait after each DMA copy**. The
DMA may have several internal requests in flight. Streaming also uses distinct
source windows across samples and sizes; it does not force DRAM rows closed.

**Why are the results close?** For large blocks, moving the bytes dominates the
total time in both apps. Consecutive streaming accesses can also reuse open
rows and use different banks. At 64 KiB, warm measured **56.140 GB/s** and
streaming **56.080 GB/s**, a difference of about **0.11%**. Smaller blocks are
more sensitive to initial delays. These are plausible explanations; since both
warm-up and addresses change, the comparison does not isolate row effects.

Use warm reads to study repeated data and streaming to study successive data.

## What is timed?

Sizes double from 4 B to 512 KiB: **18 sizes**, three samples per size, eight
copies per sample. Setup, warm-up, full-buffer checks and printing are untimed;
DMA issue, completion waits and clock-read overhead are included.

The source is the last 512 KiB of HBM 0. The reused destination starts at TCDM
offset `0x20000`; a separate 64 KiB staging buffer prepares the source data.

**Bandwidth = 8 × bytes per copy / elapsed time.** The CSV reports median,
minimum and maximum sample bandwidth. Small payloads can generate larger
physical DRAM reads, reported separately by the optional database analysis.

## Build and run

Run from the **SDK root**, after the [SoftHier environment setup](../../../README.md).
Build hardware once, or again after changing the model. The app prepares its
own data and needs no `sh-data`.

```bash
set -e
APP_DIR="$PWD/pulp/pulp/chips/softhier/common/sw/app_dram_bandwidth_warm"
make sh-hw TOPOLOGY=2d_mesh
make sh-sw TOPOLOGY=2d_mesh app="$APP_DIR"
mkdir -p .local-artifacts/dram-bandwidth
RUN_DIR="$(mktemp -d "$PWD/.local-artifacts/dram-bandwidth/run.XXXXXX")"
cp sw_build/softhier.elf "$RUN_DIR/softhier.elf"
make sh-run TOPOLOGY=2d_mesh SH_TRACE_ARGS= RUN_ARGS="--work-dir=$RUN_DIR" \
    > "$RUN_DIR/simulation.log" 2>&1
python3 "$APP_DIR/summarize.py" "$RUN_DIR/simulation.log" \
    --report "$RUN_DIR/report.md" > "$RUN_DIR/bandwidth.csv"
```

The summary accepts a complete run with `[dram-bandwidth] errors=0` and
`SoftHier DRAM bandwidth: OK`. Check these rather than relying on simulator
exit status alone. For physical DRAM statistics, with database recording enabled:

```bash
python3 "$APP_DIR/query_dram.py" "$RUN_DIR/simulation.log" "$RUN_DIR" \
    > "$RUN_DIR/dram.csv"
```

## Reading the results

The report marks a plateau when the last three size medians differ by at most
3% (max/min ≤ 1.03). Its onset is the earliest size, with two larger sizes
remaining, after which every median stays within 5% of the maximum. Otherwise
it reports that no plateau was found. The full sweep always runs.

The reference is **64 GB/s**, the nominal theoretical bus bandwidth of one HBM
channel. Utilization is `100 × measured / 64`; `--peak-gbps` selects another
reference. The emulation does not enforce this peak for every traffic pattern.

The plateau describes this DMA path and access pattern, rather than proving
full bus utilization. Both apps share firmware and analysis helpers and keep
the same hardware and memory timings.

## Bandwidth plot

![Warm DMA read bandwidth](assets/bandwidth.png)

The figure shows **1 to 128 KiB** on linear axes. The full 18-size input is in
[assets/bandwidth.csv](assets/bandwidth.csv).

The dashed line is the **64 GB/s theoretical bandwidth** reference. The dotted
line estimates the large-copy limit at **57.894 GB/s**. We fit
`T(N) = alpha + beta * N` to the 64/128/256 KiB times, giving `alpha = 35.375 ns`
and a limit of `1 / beta`. The excluded 512 KiB point matches the prediction
(9091.375 ns) at CSV precision. This estimate describes this DMA pattern; see the
[model explanation](../app_dram_bandwidth_streaming/README.md#how-the-asymptote-is-calculated).

To regenerate the image from the SDK root, using the local Python with Matplotlib:

```bash
APP_DIR="$PWD/pulp/pulp/chips/softhier/common/sw/app_dram_bandwidth_warm"
pulp/.venv/bin/python "$APP_DIR/plot.py" "$APP_DIR/assets/bandwidth.csv"
```

For a new run, use `"$RUN_DIR/bandwidth.csv"` as input. The script saves PNG and
SVG in this app's `assets/`; `--output /path/to/figure.png` selects another path.
Both apps use `dram_common/plot_bandwidth.py`, so their plots stay consistent.
