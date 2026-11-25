# Local HBM bandwidth: pipelined contiguous reads

How fast can one cluster read from its local HBM channel when it keeps the DMA
busy? The DM core of cluster 0 copies contiguous blocks from HBM 0 into TCDM,
from **4 B to 1 MiB** (doubling each time: 19 sizes, three samples each). It is
the [sequential sweep](../app_dram_bandwidth_sequential/README.md) with eight
copies in flight instead of one.

> **Simulator setup.** Asynchronous wide-port arbiter and DRAMSys with
> `CCDS = 4`. The shared DRAMSys configuration has `CCDS = 2`, which lets bursts
> overlap on the data bus and go past 64 GB/s
> ([why](#why-ccds--2-exceeded-64-gbs)).

## What is measured

Each sample issues eight consecutive copies back to back and waits once, so the
DMA always has the next one queued; the sequential app waits after every copy.
The core times from the first request to the end of the last copy:

**bandwidth = 8 × block size / elapsed time**

Every sample reads its own window, starting at 4 MiB in channel 0 and aligned
to a 32 B burst, so nothing is read twice. After timing, the app checks that
TCDM holds the last block (an address tag in its first word), and
`summarize.py` rejects partial runs, errors, wrong offsets and overlapping
windows. Blocks up to 512 KiB land at TCDM offset `0x40000`; a 1 MiB block
fills the whole TCDM.

## Build and run

From the SDK root, after the [SoftHier setup](../../../README.md); the
simulation takes about 15 minutes. For `CCDS = 4`, copy `dramsys_configs/` from
where `DRAMSYS_PATH` points, set `"CCDS": 4` in
`memspec/HBM4-emulation-4000.json`, and point `DRAMSYS_PATH` at the copy.

```bash
set -e
APP_DIR="$PWD/pulp/pulp/chips/softhier/common/sw/app_dram_bandwidth_pipelined"
make sh-hw TOPOLOGY=2d_mesh
make sh-sw TOPOLOGY=2d_mesh app="$APP_DIR"
mkdir -p .local-artifacts/dram-bandwidth-pipelined
RUN_DIR="$(mktemp -d "$PWD/.local-artifacts/dram-bandwidth-pipelined/run.XXXXXX")"
cp sw_build/softhier.elf "$RUN_DIR/softhier.elf"
make sh-run TOPOLOGY=2d_mesh SH_TRACE_ARGS= RUN_ARGS="--work-dir=$RUN_DIR" \
    > "$RUN_DIR/simulation.log" 2>&1
python3 "$APP_DIR/summarize.py" "$RUN_DIR/simulation.log" \
    --report "$RUN_DIR/report.md" > "$RUN_DIR/bandwidth.csv"
python3 "$APP_DIR/query_dram.py" "$RUN_DIR/simulation.log" "$RUN_DIR" \
    > "$RUN_DIR/dram.csv"
rm -f "$RUN_DIR"/*.tdb
pulp/.venv/bin/python "$APP_DIR/plot.py" "$RUN_DIR"
```

A good run ends with `[dram-pipelined] errors=0` and
`SoftHier pipelined DRAM bandwidth: OK`. `query_dram.py` reads the DRAMSys
databases (`DatabaseRecording: true`, on by default); they take several GB and
can go once `dram.csv` exists. Don't start two simulations at the same moment
from one SDK: both rewrite the NoC topology file.

To keep single bursts, extract them from the channel 0 database before
deleting it:

```bash
python3 "$APP_DIR/query_trace.py" "$RUN_DIR/simulation.log" "$RUN_DIR" \
    --size 512 --tdb "$RUN_DIR/trace.tdb"
python3 "$APP_DIR/query_trace.py" "$RUN_DIR/simulation.log" "$RUN_DIR" \
    --spacing > "$RUN_DIR/spacing.csv"
```

The first cuts the 512 B sample out of the DRAMSys trace, in its own format
(without `--tdb` it prints one CSV row per request instead). The second
counts, for every sample, the gaps between consecutive bursts and whether they
came from the same bank group (`tCCD_L`) or not (`tCCD_S`).

## Results

One run on 2026-11-23; all 57 samples passed the data check. Every chart has
the block size on the horizontal axis, 4 B to 1 MiB. The numbers are in
[bandwidth.csv](assets/data/bandwidth.csv) and [dram.csv](assets/data/dram.csv).

### How fast can one cluster read?

![Pipelined contiguous read bandwidth](assets/figures/bandwidth.png)

Orange is the bandwidth the core measures; the light band around it is the
min–max of the three samples (under 0.7% wide from 1 KiB up). The dashed line
is the channel's peak, 64 GB/s, and the grey area is the plateau, where every
point is within 5% of the best. The two black curves are the models explained
[below](#how-far-the-curve-is-from-a-simple-model).

**One cluster saturates its HBM channel.** Bandwidth climbs fast, flattens from
16 KiB and reaches **63.97 GB/s at 1 MiB, 99.96% of the peak**.

| 1 KiB | 4 KiB | 16 KiB | 64 KiB | 1 MiB |
| ---: | ---: | ---: | ---: | ---: |
| 47.35 GB/s | 57.39 GB/s | 62.21 GB/s | 63.54 GB/s | 63.97 GB/s |

### How far the curve is from a simple model

We start from the latency-bandwidth (alpha-beta) model of the
[sequential sweep](../app_dram_bandwidth_sequential/README.md#how-the-asymptote-is-calculated):
every copy pays a fixed cost `alpha`, then moves its `N` bytes at bandwidth
`1/beta`.

$$
T(N)=\alpha+\beta N
$$

Fitted on the 64–256 KiB points (time per copy = sample time / 8), it gives
**`alpha = 7.375 ns` per copy and `1/beta = 64.000 GB/s`**, the channel's peak.
From 4 KiB to 1 MiB it predicts every time to the picosecond, even at 512 KiB
and 1 MiB, which were not in the fit. Below 4 KiB it is off by up to 20%. The
next chart shows why.

![Time outside the DRAM bus window](assets/figures/fixed_cost.png)

This is the part of each sample when the DRAM is *not* busy: the core's time
minus the window from the start of the first burst on the DRAM data bus to the
end of the last one (DRAMSys records both). It is the request travelling to the
DRAM and the data travelling back.

**From 4 KiB up it is always 56.5 ns; below, it is shorter and grows with the
block.** About 42 ns up to 512 B, then roughly 2 ns more every 512 B until
4 KiB, the largest burst the iDMA sends. We have not traced why yet; it looks
like the last burst's data crossing the interconnect after the DRAM is done.
Either way, a single `alpha` cannot fit every block size.

The large-block `alpha` is these 56.5 ns plus 2.5 ns by which the bus window is
longer than the bytes alone: 59 ns per sample, 7.375 ns per copy, since the
eight copies share it. The sequential sweep pays 50.125 ns per copy, 6.8× more,
and that is where pipelining wins on small and medium blocks.

**So below 4 KiB we add a floor and a growing fixed cost:**

$$
T(N)=\max\left(T_{\min},\ \alpha(N)+\beta N\right)
$$

- **`T_min = 9.875 ns` per copy (79 ns per sample).** Up to 256 B a sample
  takes 73–79 ns whatever the size: the eight requests reach the DRAM about
  4.5 ns apart, so the time depends on how fast they arrive, not on the bytes.
  `T_min` is the slowest of these samples.
- **`alpha(N)` rises in a straight line** from 5.375 ns per copy at 512 B (the
  measured `T − βN` there) to the large-block 7.375 ns at 4 KiB, and stays flat
  outside that range. Only these two ends are fitted; 1 and 2 KiB are a check.

From 4 KiB up this is the alpha-beta model again, and the curve has no jump.

| Block | Measured | Alpha-beta model | New model |
| ---: | ---: | ---: | ---: |
| 4 B | 0.44 GB/s | 0.54 GB/s (−18%) | 0.41 GB/s (+8%) |
| 32 B | 3.24 GB/s | 4.06 GB/s (−20%) | 3.24 GB/s (0%) |
| 64 B | 6.48 GB/s | 7.64 GB/s (−15%) | 6.48 GB/s (0%) |
| 256 B | 25.92 GB/s | 22.51 GB/s (+15%) | 25.92 GB/s (0%) |
| 512 B | 38.28 GB/s | 33.30 GB/s (+15%) | 38.28 GB/s (0%) |
| 1 KiB | 47.35 GB/s | 43.81 GB/s (+8%) | 47.27 GB/s (+0.2%) |
| 2 KiB | 53.72 GB/s | 52.01 GB/s (+3%) | 53.57 GB/s (+0.3%) |
| 4 KiB | 57.39 GB/s | 57.39 GB/s (0%) | 57.39 GB/s (0%) |

The percentages are measured vs model. **The new model is within 0.3% from
32 B to 1 MiB.** Below 32 B it is up to 8% pessimistic, because smaller
requests reach the DRAM slightly closer together. Keep in mind that the ramp is
empirical and that the sweep has no point between 2 and 4 KiB. The sequential
sweep keeps the plain alpha-beta model.

### One copy at a time vs eight in flight

![Sequential vs pipelined reads](assets/figures/sequential_vs_pipelined.png)

Blue is the [sequential sweep](../app_dram_bandwidth_sequential/README.md),
which waits for each copy before starting the next; orange is this one, with
eight in flight.

**Both reach 64 GB/s, but pipelining gets there much sooner.** The gain comes
from waiting less, not from more bandwidth: 3.6× at 256 B, 1.6× at 4 KiB, and
under 4% from 64 KiB, where a single big copy already keeps the DRAM busy.

| Block | Sequential | Pipelined | Gain |
| ---: | ---: | ---: | ---: |
| 256 B | 7.19 GB/s | 25.92 GB/s | 3.6× |
| 4 KiB | 35.05 GB/s | 57.39 GB/s | 1.6× |
| 64 KiB | 61.04 GB/s | 63.54 GB/s | 1.04× |
| 1 MiB | 63.79 GB/s | 63.97 GB/s | 1.003× |

**For the compiler:** tiles below about 64 KiB need several DMA transfers in
flight (double buffering or more) to get close to the channel bandwidth.

### DRAM bandwidth vs what the cluster sees

![DRAM data bus vs end-to-end bandwidth](assets/figures/dram_vs_end_to_end.png)

Orange is what software gets: bytes copied over the time the core measures.
Green is how hard the DRAM works: bytes on its data bus over the time from its
first burst to its last. (Below 32 B the DRAM still moves whole 32 B bursts, so
green counts more bytes than the copy.)

**The DRAM is flat out already at 1 KiB** (63.4 GB/s) and reaches **64.00 GB/s
at 1 MiB, its physical limit**: one 32 B burst per nanosecond on each
pseudo-channel. The cluster catches up more slowly because of the fixed latency
[above](#how-far-the-curve-is-from-a-simple-model).

![Where the time of a sample goes](assets/figures/time_breakdown.png)

Each sample's time split in three: green, the DRAM bus moving data; yellow, the
bus idle between the first and the last burst; purple, the time outside the
DRAM window.

**There is almost no yellow.** With eight copies queued, the DRAM never waits
for the next request (at most 3.3 ns per sample). Large blocks are nearly all
green; up to 256 B, purple is more than half. Compare it with the
[sequential breakdown](../app_dram_bandwidth_sequential/README.md#dram-bandwidth-vs-what-the-cluster-sees),
where a big yellow area shows the DRAM waiting between copies.

## Why `CCDS = 2` exceeded 64 GB/s

**No physical limit was broken: the simulator moved bytes that real wires
could not.** Each pseudo-channel has 32 data wires at 8 Gb/s, which carry one
32 B burst per nanosecond: 32 GB/s, 64 GB/s for the two. Going faster would
need two bursts on the same wires at once.

DRAMSys works like a timetable, not like a model of the wires. Before each read
it checks only the minimum gaps from earlier commands (`tCCD_L` within a bank
group, `tCCD_S` between bank groups, `tRCD` after opening a row), then
computes when the data *would* be on the bus. **By default DRAMSys does no
check at all to stop two bursts from sharing the data bus, something
impossible in hardware: it assumes the timing parameters are correct.**

Here one was not. `CCDS` (`tCCD_S`) was 2 cycles, 0.5 ns, for a burst that
lasts 4 cycles, 1 ns. That is the HBM2 value for 4-beat bursts, kept when the
emulation moved to 8-beat bursts. DRAMSys's HBM2 checker even has an `assert`
for 4-beat bursts, but the library is built in Release (`-DNDEBUG`), which
removes it. So a burst could start 0.5 ns after another one on the same wires.
The data still arrived intact (`errors=0`), because the simulator copies it
instead of sending it over wires.

With `CCDS = 4`, as long as one burst, nothing overlaps and the bus tops at
64.00 GB/s. **All results in this README use `CCDS = 4`.** (Refreshes are
postponed out of these short samples; a long stream would pay up to about 5%
for them, `tRFC / tREFI` = 65 ns / 1.25 µs.)

![tCCD_S = 2 vs 4, DRAM data bus](assets/figures/ccds_bus.png)

DRAM bus bandwidth with the shared configuration (`CCDS = 2`, grey) and the
corrected one (`CCDS = 4`, green). **With `CCDS = 2` the bus goes past the
physical limit:** 67.4 GB/s at 512 B, 64.25 GB/s at 1 MiB. End to end the gap
is smaller (64.22 vs 63.97 GB/s at 1 MiB, [ccds.png](assets/figures/ccds.png)),
but enough to report a result no hardware could reach.

### What the overlap looks like

Two screenshots from DRAMSys's own viewer, the Trace Analyzer (v5.6.0), opened
on the 512 B samples kept in [assets/traces/](#where-the-data-comes-from). Both
show about 10 ns in the middle of a copy, when the requests are streaming in.

**How to read them.** Time goes left to right, and each vertical grid line is
one DRAM clock, 0.25 ns. Every row is something that can be busy, and every
hexagon is one thing keeping it busy:

- **REQ** is a 32 B read reaching the DRAM controller; **RESP** is its data
  going back (a thin mark, it takes no time).
- **Command Bus [C]** carries the read commands, one clock each. Command Bus [R]
  would carry row commands like ACT, but there are none in this window.
- **Data Bus [PC0]** and **[PC1]** are the wires of the two pseudo-channels.
  Each hexagon is one 32 B burst, 4 clocks (1 ns) long.

Watch the two data buses: on real wires, hexagons in the same row can touch
but never overlap.

![Trace Analyzer, CCDS = 4](assets/figures/trace_analyzer_ccds4.png)

**With `CCDS = 4`, that is exactly what happens.** The bursts sit end to end,
never on top of each other. Two read commands arrive every nanosecond, one per
pseudo-channel: 2 × 32 B per ns, 64 GB/s.

![Trace Analyzer, CCDS = 2](assets/figures/trace_analyzer_ccds2.png)

**With `CCDS = 2`, they pile up.** The bright magenta hexagons are bursts that
start half a nanosecond before the previous one has left the same wires: for
that half nanosecond, two bursts share 32 wires. It only happens between
different bank groups, where the gap is `tCCD_S`; inside one bank group
`tCCD_L` still keeps reads 1 ns apart.

### Why the `CCDS = 2` curve settles at 64.25 GB/s

In both runs the requests reach the DRAM at the same times. Their rate is set
by the cluster and the network, and it is above 64 GB/s:

| | 512 B copies | 4 KiB to 1 MiB copies |
| --- | ---: | ---: |
| Requests arrive | one copy every 7.75 ns: 66.07 GB/s | 4 KiB every 63.75 ns: 64.25 GB/s |
| DRAM bus, `CCDS = 4` | 62.77 GB/s | 64.00 GB/s |
| DRAM bus, `CCDS = 2` | 67.42 GB/s | 64.25 GB/s |

With `CCDS = 4` the DRAM is the bottleneck: it stops at 64.00 GB/s and the
extra requests wait in the controller. With `CCDS = 2` it can go faster than
the requests, so the queue empties and the bus just follows the request rate.
At 512 B it also catches up, with overlaps, on the requests that queued while
the first rows were opening, which gives the 67.4 GB/s. In a 1 MiB sample 8.2%
of the gaps overlap, but the pauses to open new rows (every 8 KiB) give almost
all of it back. Counts per sample: [spacing_ccds4.csv](assets/data/spacing_ccds4.csv),
[spacing_ccds2.csv](assets/data/spacing_ccds2.csv).

### Where the data comes from

DRAMSys ([tukl-msd/DRAMSys](https://github.com/tukl-msd/DRAMSys), commit
`8e021ea`) writes every request and every DRAM command into one SQLite file per
channel, `DRAMSysRecordable0_hbm4-emu-example_example_ch0.tdb` for channel 0:
about 1 GB per run, too much for the repository. So `assets/traces/` keeps only
the 512 B sample of the screenshots, from both runs:
[trace_ccds4.tdb](assets/traces/trace_ccds4.tdb) and
[trace_ccds2.tdb](assets/traces/trace_ccds2.tdb), 128 requests with all their
phases and the configuration they were made with (`GeneralInfo.Memspec`).
The CSVs in `assets/data/` come from the full runs through the commands in
[Build and run](#build-and-run), with the firmware at pulp commit `0541a8a`;
rerunning them gives the same files.

To print the first bursts yourself:

```bash
python3 - <<'EOF'
import sqlite3
db = sqlite3.connect("assets/traces/trace_ccds2.tdb")
for begin, end, group in db.execute(
        "SELECT DataStrobeBegin, DataStrobeEnd, BankGroup FROM Phases "
        "WHERE PhaseName = 'RD' AND Rank = 0 ORDER BY DataStrobeBegin LIMIT 10"):
    print(begin, end, group)
EOF
```

Times are in picoseconds and `Rank` is the pseudo-channel. Every burst lasts
1000 ps; in `trace_ccds4.tdb` they start 1000 ps apart, in `trace_ccds2.tdb`
some only 500 ps. To open the files in the Trace Analyzer, build it from
DRAMSys `v5.6.0` (CMake option `DRAMSYS_BUILD_TRACE_ANALYZER`); later versions
use a newer format and cannot read them.

## Regenerate the figures

```bash
APP_DIR="$PWD/pulp/pulp/chips/softhier/common/sw/app_dram_bandwidth_pipelined"
pulp/.venv/bin/python "$APP_DIR/plot.py"
```

`plot.py` reads `assets/data/` and writes every chart into `assets/figures/`;
the sequential comparison also reads the sequential app's
`assets/data/bandwidth.csv` (`--sequential DIR` changes it). The two
`trace_analyzer_ccds*.png` are screenshots. For a new run, `plot.py "$RUN_DIR"`
draws the charts next to its CSVs (`--output-dir` changes this) and skips those
whose CSVs are missing. All DRAM sweep charts share
[dram_common/plot_sweep.py](../dram_common/plot_sweep.py).
