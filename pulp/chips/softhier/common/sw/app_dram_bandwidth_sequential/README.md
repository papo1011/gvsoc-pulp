# Local HBM bandwidth: sequential reads

How fast can one cluster read from its local HBM channel if it waits for every
copy before starting the next? The DM core of cluster 0 copies blocks from
HBM 0 into TCDM, from **4 B to 1 MiB** (doubling each time: 19 sizes, three
samples of eight copies each). The
[pipelined sweep](../app_dram_bandwidth_pipelined/README.md) does the same
with eight copies in flight. (Until 2026-11-23 this app was called
`app_dram_bandwidth_streaming`; older logs print `pattern=streaming` and are
still accepted.)

> **Simulator setup.** Asynchronous wide-port arbiter, iDMA writing read data
> to TCDM in 512 B chunks, and DRAMSys with `CCDS = 4`. The earlier synchronous
> arbiter capped large copies at about 57.9 GB/s, and the earlier iDMA added up
> to 14 ns per copy after the DRAM had finished
> ([Simulator fixes](#simulator-fixes)). The shared DRAMSys configuration's
> `CCDS = 2` lets bursts overlap on the data bus and go past 64 GB/s
> ([pipelined guide](../app_dram_bandwidth_pipelined/README.md#why-ccds--2-exceeded-64-gbs)).

## What is measured

A sample copies eight consecutive blocks starting at a fresh address `A`,
waiting for each copy before issuing the next:

```text
time A, A+N, A+2N, ..., A+7N   (one copy in flight)
```

**bandwidth = 8 × block size / elapsed time**, including DMA issue, waits and
clock reads; setup, checks and printing are untimed. Every sample reads its own
window, so no block is read twice (consecutive blocks can still hit open rows).

Before timing, untimed DMA writes fill the source with a pattern and tag each
block with its address, then a 100 µs wait lets them drain. After timing, the
buffer is checked against the last block. Up to 512 KiB, copies land at TCDM
offset `0x20000`; a 1 MiB copy uses the whole TCDM. Buffer layout:
[bandwidth.h](bandwidth.h).

## Build and run

From the SDK root, after the [SoftHier setup](../../../README.md); the
simulation takes about 15 minutes. For `CCDS = 4`, copy `dramsys_configs/` from
where `DRAMSYS_PATH` points, set `"CCDS": 4` in
`memspec/HBM4-emulation-4000.json`, and point `DRAMSYS_PATH` at the copy.

```bash
set -e
APP_DIR="$PWD/pulp/pulp/chips/softhier/common/sw/app_dram_bandwidth_sequential"
make sh-hw TOPOLOGY=2d_mesh
make sh-sw TOPOLOGY=2d_mesh app="$APP_DIR"
mkdir -p .local-artifacts/dram-bandwidth-sequential
RUN_DIR="$(mktemp -d "$PWD/.local-artifacts/dram-bandwidth-sequential/run.XXXXXX")"
cp sw_build/softhier.elf "$RUN_DIR/softhier.elf"
make sh-run TOPOLOGY=2d_mesh SH_TRACE_ARGS= RUN_ARGS="--work-dir=$RUN_DIR" \
    > "$RUN_DIR/simulation.log" 2>&1
python3 "$APP_DIR/summarize.py" "$RUN_DIR/simulation.log" \
    --report "$RUN_DIR/report.md" > "$RUN_DIR/bandwidth.csv"
python3 "$APP_DIR/query_dram.py" "$RUN_DIR/simulation.log" "$RUN_DIR" \
    > "$RUN_DIR/dram.csv"
```

A good run ends with `[dram-bandwidth] errors=0` and
`SoftHier DRAM bandwidth: OK`; the summary rejects incomplete runs.
`query_dram.py` reads the DRAMSys databases (`DatabaseRecording: true`, on by
default); they take 2–4 GB and can go once `dram.csv` exists.

The summary also reports the plateau: it exists when the last three medians
differ by at most 3%, and starts at the first size from which every median
stays within 5% of the best. The reference is **64 GB/s**, the bus bandwidth of
one HBM channel (two 32-bit pseudo-channels at 8 Gbit/s per pin);
`--peak-gbps` changes it.

## Results

One run on 2026-11-25; all 57 samples passed the data check. Every chart has
the block size on the horizontal axis, 4 B to 1 MiB. The numbers are in
[bandwidth.csv](assets/data/bandwidth.csv) and [dram.csv](assets/data/dram.csv).

### How fast is one copy at a time?

![Sequential DMA read bandwidth](assets/figures/bandwidth.png)

Blue is the bandwidth the core measures; the light band around it is the
min–max of the three samples. The dashed line is the channel's peak, 64 GB/s,
the grey area the plateau (every point within 5% of the best). The dotted
curve is the model explained [below](#how-far-the-curve-is-from-a-simple-model).

**Small copies are slow, because each one waits for its whole round trip to
the DRAM.** Bandwidth flattens from 64 KiB and reaches **63.86 GB/s at 1 MiB,
99.8% of the peak**.

| 1 KiB | 4 KiB | 16 KiB | 64 KiB | 1 MiB |
| ---: | ---: | ---: | ---: | ---: |
| 21.73 GB/s | 42.17 GB/s | 56.09 GB/s | 61.62 GB/s | 63.86 GB/s |

Below 8 KiB the three samples of a size differ by up to 20%; from 32 KiB up
they agree within 1%. The difference is DRAM refresh, explained
[below](#why-small-copies-are-noisy).

### How far the curve is from a simple model

We fit the latency-bandwidth (alpha-beta) model to the median time per copy:
every copy pays a fixed cost `alpha`, then moves its `N` bytes at bandwidth
`1/beta`.

$$
T(N)=\alpha+\beta N
$$

On the 64, 128 and 256 KiB points it gives **`alpha = 37.125 ns` and
`1/beta = 63.951 GB/s`** (64.05 ns per 4 KiB burst). **From 8 KiB up it is
within 1% of every point**, including 512 KiB and 1 MiB, which were not in the
fit. (With `CCDS = 2` the same fit gave 64.251 GB/s, above the peak.)

Below 8 KiB the model is pessimistic: the copies are faster than predicted.

| Block | Measured | Model |
| ---: | ---: | ---: |
| 4 B | 0.13 GB/s | 0.11 GB/s (+20%) |
| 64 B | 1.85 GB/s | 1.68 GB/s (+10%) |
| 512 B | 13.09 GB/s | 11.34 GB/s (+15%) |
| 1 KiB | 21.73 GB/s | 19.27 GB/s (+13%) |
| 4 KiB | 42.17 GB/s | 40.48 GB/s (+4%) |
| 8 KiB | 49.91 GB/s | 49.58 GB/s (+0.7%) |

The percentages are measured vs model. In time, a small copy costs 31–40 ns
where the model says 37 ns. Part of it is probably the DRAM rows: up to 4 KiB
a read can be served in 9.25 ns, from 8 KiB never under 12 ns, about one row
activation (`tRCD`, 2.5 ns) more. Below 8 KiB the noise is about as large as
the error, so a more detailed model would only fit the noise.

The on-chip part, instead, is the same at every size: the time of a sample
outside the DRAM's busy window (the first request travelling to the DRAM and
the last data travelling back) is 43.5–45 ns from 4 B to 1 MiB. Before the
[iDMA fix](#idma-read-data-in-chunks) it grew to 58.5 ns from 4 KiB.

### Why small copies are noisy

DRAMSys refreshes the whole channel every 1.25 µs on average (`tREFI`), and a
refresh blocks it for 65 ns (`tRFC`). The controller may postpone or pull in
refreshes, so where they land depends on everything that ran before. A sample
of eight small copies lasts 250–800 ns, so about one sample in two contains a
refresh.

We checked it in the DRAMSys database of a full run. Up to 4 KiB, every sample
more than 6 ns slower than the fastest of its size had a refresh inside its
window. Samples hit by a refresh were 21–29 ns slower on average, up to 108 ns
with two refreshes. Samples without a refresh were all within 6 ns of each
other.

So below 8 KiB, compare runs of the same firmware only: changing a `printf`
shifts every sample by a few ns and moves the refreshes to other samples.

### DRAM bandwidth vs what the cluster sees

![DRAM data bus vs end-to-end bandwidth](assets/figures/dram_vs_end_to_end.png)

Blue is what software gets: bytes copied over the time the core measures.
Green is how hard the DRAM works: bytes on its data bus over the time from its
first burst to its last. (Below 32 B the DRAM still moves a whole 32 B burst per
copy, so green counts more bytes than the copy.)

**The two curves meet at the top** (63.88 GB/s on the DRAM bus, 63.86 GB/s for
the cluster at 1 MiB), **but for mid-size copies even the DRAM is far from its
limit**: 44.7 GB/s at 4 KiB. With one copy at a time, the DRAM spends part of
its window waiting. In the
[pipelined sweep](../app_dram_bandwidth_pipelined/README.md#dram-bandwidth-vs-what-the-cluster-sees)
it does not.

![Where the time of a sample goes](assets/figures/time_breakdown.png)

Each sample's time split in three: green, the DRAM bus moving data; yellow, the
bus idle between the first and the last burst; purple, the time outside the
DRAM window.

**The big yellow area is the price of waiting for each copy.** Between two
copies the DRAM sits idle for 30–40 ns, the round trip of the next request:
200–285 ns per sample. At 4 KiB the DRAM is busy only 66% of the time, and up
to 512 B at most 20%. Yellow shrinks only because large copies make the gaps
negligible; the pipelined sweep removes it by keeping eight copies queued.

## Simulator fixes

Two parts of GVSoC made the curve look worse than hardware would. Both are
fixed in the runs above.

### Wide-port arbiter

Each cluster's iDMA reaches the NoC through `wide_arbiter_<cluster>`
([softhier_system_base.py](../../../softhier_system_base.py)), a router with
`bandwidth=noc_link_width` (512 B/cycle) that models the cluster's wide AXI port.

**The synchronous router (previous default) counted the queueing twice.** It
charged a read burst's data when the *request* passed
(`BandwidthLimiter::apply_bandwidth` in `core/models/interco/router/router.cpp`)
and stored the wait in the request's latency. The iDMA issues one 4 KiB burst
per cycle, each counted as 8 cycles, so burst *k* got 7·k cycles of latency.
The DRAM then served the bursts at its own pace, and the iDMA AXI backend
(`pulp/idma/be/idma_be_axi.cpp`) added that latency on top of the real
response. Each 4 KiB burst cost 63.75 ns of DRAM time plus 7 ns:
4096 / 70.75 ≈ 57.9 GB/s. In hardware the data comes back on the R channel
while the DRAM works, so nothing adds up.

**The asynchronous router (`synchronous=False`, current) counts it once.**
Requests queue and leave the port spaced by its bandwidth; responses pass
straight back. Against a DRAM that serves one burst every ~64 ns the spacing
costs nothing, while a fast target would still be limited to 512 B/cycle. It
is not a beat-accurate AXI model, since it throttles requests rather than the
R channel, but the throughput is right.

To see the difference in a trace, run with
`--trace=/system/cluster_0/idma/axi_read --trace-level=trace`: the lines
`Handling end of read request (... latency N)` grow by 7 per burst with the
synchronous router and stay at 0 with the asynchronous one.

### iDMA read data in chunks

The iDMA splits a copy into AXI bursts of up to 4 KiB. **Before the fix, a
read burst was answered in one piece:** the DRAMSys bridge
(`core/models/memory/dramsys.cpp`) replies only when all 128 of its 32 B
accesses are done, and only then did the iDMA write the burst into TCDM, one
512 B line every 2 cycles. So after the DRAM had sent its last byte, the copy
still waited up to 8 line writes, 16 ns for a 4 KiB burst.

In hardware the data comes back as R beats, and the iDMA writes them into TCDM
while the rest of the burst is still on its way: only the last line is left
at the end. **Now the iDMA reads each burst in 512 B chunks** (one TCDM line),
each answered and written on its own (`read_chunk_size` in
`pulp/idma/be/idma_be_axi.cpp`). The chunks of a burst still count as one
outstanding burst. To get the old behaviour back, run with
`--config-opt system/idma_read_chunk=0`.

![Sequential reads with whole bursts and with 512 B chunks](assets/figures/read_chunks.png)

The same firmware with the old iDMA (grey) and the new one (blue), from
[bandwidth_whole_burst.csv](assets/data/bandwidth_whole_burst.csv) and
[bandwidth.csv](assets/data/bandwidth.csv).

**The fix matters between 1 and 64 KiB.** Up to 512 B every sample is
identical, to the picosecond: a burst that fits in one line had nothing to
wait for. From 1 KiB each copy saves one line write per extra 512 B of its
last burst, up to 14 ns from 4 KiB (2 ns at 1 KiB, measured on samples without
a refresh). At 4 KiB that is 17% more bandwidth; at 1 MiB, 0.1%. The DRAM side
does not change.

| Block | Whole burst | 512 B chunks | Gain |
| ---: | ---: | ---: | ---: |
| 1 KiB | 18.75 GB/s | 21.73 GB/s | +16% |
| 4 KiB | 35.97 GB/s | 42.17 GB/s | +17% |
| 16 KiB | 52.87 GB/s | 56.09 GB/s | +6% |
| 64 KiB | 61.04 GB/s | 61.62 GB/s | +1% |
| 1 MiB | 63.80 GB/s | 63.86 GB/s | +0.1% |

(Below 16 KiB the medians also move with the refreshes, so the gain there is
± a few %.)

### All the fixes together

![Sequential reads before and after each simulator fix](assets/figures/simulator_fixes.png)

The same sweep in the four configurations we went through, from the original
(light grey) to the final one (blue).

**The router fix is the big one at the top**: the synchronous router flattens
at about 57.8 GB/s, 9.5% below the final curve at 1 MiB. **The iDMA fix is the
big one in the middle**, up to 17% around 4 KiB. The `CCDS` fix is small but
keeps the curve under the physical limit: with `CCDS = 2` it ends slightly
above 64 GB/s. Below 1 KiB the curves cross at random because of the
refreshes.

| Block | Synchronous router, `CCDS = 2` | Asynchronous router, `CCDS = 2` | `CCDS = 4` | 512 B chunks (final) |
| ---: | ---: | ---: | ---: | ---: |
| 4 KiB | 37.71 GB/s | 37.62 GB/s | 35.97 GB/s | 42.17 GB/s |
| 16 KiB | 51.42 GB/s | 54.50 GB/s | 52.87 GB/s | 56.09 GB/s |
| 64 KiB | 56.08 GB/s | 61.56 GB/s | 61.04 GB/s | 61.62 GB/s |
| 1 MiB | 57.78 GB/s | 64.08 GB/s | 63.80 GB/s | 63.86 GB/s |

(The two `CCDS = 2` runs are from 2026-11-20 and earlier, with an older
firmware.)

## Regenerate the figures

```bash
APP_DIR="$PWD/pulp/pulp/chips/softhier/common/sw/app_dram_bandwidth_sequential"
pulp/.venv/bin/python "$APP_DIR/plot.py"
```

`plot.py` reads `assets/data/` and writes every chart into `assets/figures/`.
`read_chunks.png` also needs `bandwidth_whole_burst.csv`, the same firmware run
with `--config-opt system/idma_read_chunk=0`; `simulator_fixes.png` also needs
`bandwidth_ccds2.csv` and `bandwidth_sync_router_ccds2.csv`, the 2026-11-20
and earlier runs. For a new run, `plot.py "$RUN_DIR"` draws the charts next to
its CSVs (`--output-dir` changes this) and skips those whose CSVs are missing;
the fit needs the 64/128/256 KiB points. All DRAM sweep charts share
[dram_common/plot_sweep.py](../dram_common/plot_sweep.py).
