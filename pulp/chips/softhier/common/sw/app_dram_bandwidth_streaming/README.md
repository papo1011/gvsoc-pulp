# Local HBM bandwidth: streaming reads

How fast can one cluster read **successive** HBM blocks as the copy size grows?
The DM core of cluster 0 reads its local HBM 0 into TCDM. Sizes double from
**4 B to 1 MiB**: 19 sizes, three samples each, eight copies per sample.

> **Model caveat.** The ~57.9 GB/s plateau below is set by a modeling artifact
> in the cluster's wide-port router, not by the DRAM, which delivers data at
> ~64.3 GB/s. See [Model artifact](#model-artifact-wide-port-limiter).

## Streaming vs warm

The [warm app](../app_dram_bandwidth_warm/README.md) uses the same firmware
with two differences, for a block of `N` bytes:

```text
Streaming:  no read warm-up; time A, A+N, A+2N, ..., A+7N
Warm:       read A once outside the timer, then time A, A, A, ..., A
```

Every streaming sample reads a distinct source window. Both apps reuse one TCDM
buffer and wait for each DMA copy before starting the next. No warm-up does not
mean closed rows: consecutive blocks can still hit open rows. At 64 KiB the two
apps differ by about 0.1% (56.080 vs 56.140 GB/s): for large blocks moving the
bytes dominates. Since both warm-up and addresses change, this comparison does
not isolate row effects.

## What is timed

**Bandwidth = 8 × bytes per copy / elapsed time**, including DMA issue, waits
and clock reads. Setup, checks and printing are untimed.

Untimed DMA writes fill the source with a pattern and tag each block with its
address; a 100 µs wait lets them drain before measuring. After timing, the
buffer is checked against the last block. Up to 512 KiB, copies land at TCDM
offset `0x20000`; the 1 MiB copy uses the whole TCDM (the app has no persistent
`.l1` data and rejects it at runtime). Buffer layout: [bandwidth.h](../dram_common/bandwidth.h).

## Build and run

From the **SDK root**, after the [SoftHier setup](../../../README.md). No
`sh-data` is needed; the simulation takes several minutes.

```bash
set -e
APP_DIR="$PWD/pulp/pulp/chips/softhier/common/sw/app_dram_bandwidth_streaming"
ANALYSIS_DIR="$PWD/pulp/pulp/chips/softhier/common/sw/app_dram_bandwidth_warm"
make sh-hw TOPOLOGY=2d_mesh
make sh-sw TOPOLOGY=2d_mesh app="$APP_DIR"
mkdir -p .local-artifacts/dram-bandwidth-streaming
RUN_DIR="$(mktemp -d "$PWD/.local-artifacts/dram-bandwidth-streaming/run.XXXXXX")"
cp sw_build/softhier.elf "$RUN_DIR/softhier.elf"
make sh-run TOPOLOGY=2d_mesh SH_TRACE_ARGS= RUN_ARGS="--work-dir=$RUN_DIR" \
    > "$RUN_DIR/simulation.log" 2>&1
python3 "$ANALYSIS_DIR/summarize.py" "$RUN_DIR/simulation.log" \
    --report "$RUN_DIR/report.md" > "$RUN_DIR/bandwidth.csv"
```

A valid run ends with `[dram-bandwidth] errors=0` and
`SoftHier DRAM bandwidth: OK`; the summary rejects incomplete runs. With
`DatabaseRecording: true` in DRAMSys you can also extract physical DRAM traffic:

```bash
python3 "$ANALYSIS_DIR/query_dram.py" "$RUN_DIR/simulation.log" "$RUN_DIR" \
    > "$RUN_DIR/dram.csv"
```

Each sweep with recording writes ~2 GB of databases; delete the `.tdb` files
once `dram.csv` exists. The plateau criterion and the 64 GB/s reference are
described in the [warm guide](../app_dram_bandwidth_warm/README.md#reading-the-results).

## Results

![Streaming DMA read bandwidth](assets/bandwidth.png)

All 57 samples passed the data and DRAM traffic checks. The plateau starts at
**64 KiB**; at **1 MiB** bandwidth is **57.777 GB/s** (90.3% of the 64 GB/s
nominal). The figure shows 1–128 KiB; all data is in
[assets/bandwidth.csv](assets/bandwidth.csv).

### How the asymptote is calculated

We fit the latency-bandwidth (alpha-beta/Hockney) model to the median time per copy:

$$
T(N)=\alpha+\beta N,\qquad B_{\infty}=1/\beta.
$$

`plot.py` fits the 64, 128 and 256 KiB points, giving `alpha = 36.625 ns` and
`1/beta = 57.894 GB/s`. The excluded 512 KiB and 1 MiB points match the
prediction exactly (9092.625 and 18148.625 ns per copy). The fitted limit is
the artifact described next: about 7 ns lost per 4 KiB burst.

## Model artifact: wide-port limiter

Each cluster's iDMA reaches the NoC through `wide_arbiter_<cluster>`
([softhier_system_base.py](../../../softhier_system_base.py)), a synchronous
router with `bandwidth=noc_link_width` (512 B/cycle). It models the cluster's
wide AXI port, but:

1. The router charges a read burst's data **when the request passes**
   (`BandwidthLimiter::apply_bandwidth` in `core/models/interco/router/router.cpp`)
   and stores the wait in the request latency.
2. The iDMA splits a copy into 4 KiB bursts and issues one per cycle. Each
   counts as 8 cycles (4096 / 512), so burst *k* gets **7·k cycles** of latency.
3. The DRAM responds asynchronously. The iDMA AXI backend
   (`pulp/idma/be/idma_be_axi.cpp`) **adds** that latency to the real response
   time, so the queueing is counted twice.

A 4 KiB burst therefore costs 63.75 ns of DRAM time plus 7 ns, which gives
4096 / 70.75 ≈ 57.9 GB/s, while DRAMSys delivers data at ~64.3 GB/s. In hardware
the R channel would carry those 8 beats while the data comes back, overlapping
DRAM time; at 512 GB/s the port should never limit one HBM channel.

**Unaffected:** copies of a single 4 KiB burst or less, writes (the backend
ignores write latency) and the narrow path (scalar loads).

The trace shows it with
`--trace=/system/cluster_0/idma/axi_read --trace-level=trace`: lines
`Handling end of read request (... latency N)` grow by 7 per burst.

## Regenerate the figure

```bash
APP_DIR="$PWD/pulp/pulp/chips/softhier/common/sw/app_dram_bandwidth_streaming"
pulp/.venv/bin/python "$APP_DIR/plot.py" "$APP_DIR/assets/bandwidth.csv"
```

Use `"$RUN_DIR/bandwidth.csv"` for a new run, or `--output path.png` to save
elsewhere. The fit needs the 64/128/256 KiB points. Warm and streaming share
`dram_common/plot_bandwidth.py`.
