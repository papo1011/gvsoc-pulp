# Local HBM bandwidth: streaming reads

How fast can one cluster read **successive** HBM blocks as the copy size grows?
The DM core of cluster 0 reads its local HBM 0 into TCDM. Sizes double from
**4 B to 1 MiB**: 19 sizes, three samples each, eight copies per sample.

> **Simulator setup.** These results use the asynchronous wide-port arbiter and
> DRAMSys with `CCDS = 4`. The earlier synchronous arbiter capped large copies
> at ~57.9 GB/s through a modeling artifact (see
> [Wide-port arbiter model](#wide-port-arbiter-model)); the shared DRAMSys
> configuration's `CCDS = 2` lets bursts overlap on the data bus and exceed
> 64 GB/s (see the [pipelined guide](../app_dram_bandwidth_pipelined/README.md#why-the-plateau-exceeds-64-gbs)).

## Streaming vs warm

The [warm app](../app_dram_bandwidth_warm/README.md) uses the same firmware
with two differences, for a block of `N` bytes:

```text
Streaming:  no read warm-up; time A, A+N, A+2N, ..., A+7N
Warm:       read A once outside the timer, then time A, A, A, ..., A
```

Every streaming sample reads a distinct source window. Both apps reuse one TCDM
buffer and wait for each DMA copy before starting the next. No warm-up does not
mean closed rows: consecutive blocks can still hit open rows. With the
synchronous arbiter the two apps differed by about 0.1% at 64 KiB (56.080 vs
56.140 GB/s): for large blocks moving the bytes dominates. Since both warm-up
and addresses change, this comparison does not isolate row effects. The warm
sweep has not been re-measured with the asynchronous arbiter.

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
`sh-data` is needed; the simulation takes about 15 minutes. The results below
use `CCDS = 4`: copy `dramsys_configs/` from the directory `DRAMSYS_PATH`
points to, set `"CCDS": 4` in `memspec/HBM4-emulation-4000.json`, and export
`DRAMSYS_PATH` pointing at the copy before `make sh-run`.

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

Each sweep with recording writes 2–4 GB of databases; delete the `.tdb` files
once `dram.csv` exists. The plateau criterion and the 64 GB/s reference are
described in the [warm guide](../app_dram_bandwidth_warm/README.md#reading-the-results).

## Results

![Streaming DMA read bandwidth](assets/bandwidth.png)

Measured on 2026-11-22 with the asynchronous wide-port arbiter and DRAMSys
with `CCDS = 4`. All 57 samples passed the data check, and the DRAM read exactly
the payload bytes (copies below 32 B read one 32 B burst). The plateau starts
at **64 KiB**; at **1 MiB** bandwidth is **63.786 GB/s**, 99.7% of the 64 GB/s
nominal, and DRAMSys delivers 63.79 GB/s during that sample. The shaded band is
the plateau region (every median from there on is at least 95% of the measured
maximum). The figure shows 1–128 KiB; all data is in
[assets/bandwidth.csv](assets/bandwidth.csv).

| Copy | `CCDS = 2` (2026-11-20) | `CCDS = 4` |
| ---: | ---: | ---: |
| 4 KiB | 37.621 GB/s | 35.046 GB/s |
| 16 KiB | 54.500 GB/s | 53.521 GB/s |
| 64 KiB | 61.558 GB/s | 61.042 GB/s |
| 256 KiB | 63.556 GB/s | 63.177 GB/s |
| 1 MiB | 64.076 GB/s | 63.786 GB/s |

With `CCDS = 2` the 1 MiB point exceeded the nominal peak. Small copies lose
the most: a 4 KiB copy takes 116.875 ns instead of 108.875 ns, because the
overlapping bursts that `CCDS = 2` allowed shortened every copy's data phase.

### How the asymptote is calculated

We fit the latency-bandwidth (alpha-beta/Hockney) model to the median time per copy:

$$
T(N)=\alpha+\beta N,\qquad B_{\infty}=1/\beta.
$$

`plot.py` fits the 64, 128 and 256 KiB points, giving `alpha = 50.125 ns` and
`1/beta = 63.938 GB/s` (64.06 ns per 4 KiB burst). The excluded 512 KiB and
1 MiB points are within 0.1% of the prediction (8242.375 and 16438.875 ns per
copy measured, 8250.125 and 16450.125 ns predicted). `alpha` is an effective
fixed cost, not a separate measurement of DMA startup. With `CCDS = 2` the same
fit gave 64.251 GB/s, above the nominal peak.

## Wide-port arbiter model

Each cluster's iDMA reaches the NoC through `wide_arbiter_<cluster>`
([softhier_system_base.py](../../../softhier_system_base.py)), a router with
`bandwidth=noc_link_width` (512 B/cycle) that models the cluster's wide AXI port.

**Synchronous router (previous default): artifact.** It charged a read burst's
data **when the request passed** (`BandwidthLimiter::apply_bandwidth` in
`core/models/interco/router/router.cpp`) and stored the wait in the request
latency, without delaying the request. The iDMA issues one 4 KiB burst per
cycle, each counted as 8 cycles, so burst *k* got **7·k cycles** of latency.
The DRAM then served the bursts at its own pace, and the iDMA AXI backend
(`pulp/idma/be/idma_be_axi.cpp`) **added** that latency to the real response:
the queueing was counted twice. Each 4 KiB burst cost 63.75 ns of DRAM time plus
7 ns, so 4096 / 70.75 ≈ 57.9 GB/s. In hardware the R channel carries the data
while it comes back, overlapping DRAM time.

**Asynchronous router (`synchronous=False`, current).** Requests are queued and
leave the port spaced by its bandwidth (one 4 KiB burst every 8 cycles);
responses pass straight back with no added latency. Against a DRAM that serves
one burst every ~64 ns the spacing costs nothing, while a fast target would
still be limited to 512 B/cycle. It throttles the request side rather than the
R channel, so it is not a beat-accurate AXI model, but the throughput is right
and nothing is counted twice. The router adds one cycle per request: it shows
on single-burst copies (4 KiB: 108.875 instead of 108.625 ns per copy in the
median) and on small write/readback copies, where the iDMA sends write chunks
of 512 B one at a time.

Streaming sweep, same firmware, both routers, both with the shared `CCDS = 2`
DRAMSys configuration (copies below 4 KiB vary by a few ns per sample with DRAM
state in both runs):

| Copy | Synchronous router | Asynchronous router |
| ---: | ---: | ---: |
| 4 KiB | 37.708 GB/s | 37.621 GB/s |
| 16 KiB | 51.421 GB/s | 54.500 GB/s |
| 64 KiB | 56.080 GB/s | 61.558 GB/s |
| 256 KiB | 57.429 GB/s | 63.556 GB/s |
| 1 MiB | 57.777 GB/s | 64.076 GB/s |

The trace shows the difference with
`--trace=/system/cluster_0/idma/axi_read --trace-level=trace`: the lines
`Handling end of read request (... latency N)` grow by 7 per burst with the
synchronous router and stay at 0 with the asynchronous one.

## Regenerate the figure

```bash
APP_DIR="$PWD/pulp/pulp/chips/softhier/common/sw/app_dram_bandwidth_streaming"
pulp/.venv/bin/python "$APP_DIR/plot.py" "$APP_DIR/assets/bandwidth.csv"
```

Use `"$RUN_DIR/bandwidth.csv"` for a new run, or `--output path.png` to save
elsewhere. The fit needs the 64/128/256 KiB points. Warm and streaming share
`dram_common/plot_bandwidth.py`.
