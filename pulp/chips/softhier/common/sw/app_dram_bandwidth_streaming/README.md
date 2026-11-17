# Local HBM bandwidth: streaming reads

This benchmark asks: **how fast can one cluster read successive HBM blocks as
the copy size grows?** The DM core of cluster 0 reads its local HBM 0 into TCDM.
Sizes double from **4 B to 1 MiB**, with three samples per size and eight copies
per sample: **19 sizes and 57 measurements**.

## How it differs from warm reads

There are **two differences** from the
[warm app](../app_dram_bandwidth_warm/README.md): the read warm-up and the source
addresses used during measurement. For a block size of `N` bytes:

```text
Streaming:  no read warm-up; time A, A+N, A+2N, ..., A+7N
Warm:       read A once outside the timer, then time A, A, A, ..., A
```

Each streaming sample uses a distinct source window, also separate across
sizes. Both apps overwrite the same TCDM buffer and **wait after each DMA copy**.
The DMA may have several internal requests in flight; the software starts its
eight copies one at a time. The warm sweep stops at 512 KiB.

The warm app reads the block once **before every sample**, outside the timer.
This can leave useful rows open and speed up the first timed read. A large
block visits many rows, however, so its beginning may need to be opened again.
**No read warm-up does not mean closed rows:** setup and previous accesses
affect row state, and consecutive streaming blocks can reuse open rows.

**Why are the results close?** For large blocks, moving the bytes dominates the
total time in both apps. Streaming can also exploit row locality and different
banks. At 64 KiB, streaming measured **56.080 GB/s** and warm **56.140 GB/s**,
a difference of about **0.11%**. Smaller blocks are more sensitive to initial
delays. These are plausible explanations; changing both warm-up and addresses
does not isolate row effects.

Use streaming to study successive data and warm reads to study repeated data.

## Setup and measurement

Untimed DMA writes prepare a known pattern and an address tag for each block.
A **100 µs** wait lets setup writes drain; the saved run's DRAM records confirm
they finished before measurement. Below 32 B, distinct logical blocks can share
a physical DRAM burst.

**Bandwidth = 8 × bytes per copy / elapsed time**, including DMA issue, waits and
clock-read overhead. Setup, checks and printing are untimed. The final buffer is
checked against the last source block; DRAM records check traffic for all copies.

Up to 512 KiB, copies use TCDM offset `0x20000`. A 1 MiB copy uses all TCDM after
setup: staging data is no longer needed, stacks and instructions are elsewhere,
and the app uses no barrier words. A runtime check rejects persistent `.l1`
data. All source windows are disjoint. See the shared
[firmware](../dram_common/bandwidth.h) for the buffer layout.

## Build and run

Run from the **SDK root**, after the [SoftHier environment setup](../../../README.md).
Build hardware once, or again after a model change. No `sh-data` is needed.
The full simulation can take several minutes.

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

A complete run ends with `[dram-bandwidth] errors=0` and
`SoftHier DRAM bandwidth: OK`; the summary rejects missing or failed samples.
For physical DRAM statistics, with recording enabled:

```bash
python3 "$ANALYSIS_DIR/query_dram.py" "$RUN_DIR/simulation.log" "$RUN_DIR" \
    > "$RUN_DIR/dram.csv"
```

Both apps share firmware and analysis scripts and keep the same hardware and
memory timings. See the [warm guide](../app_dram_bandwidth_warm/README.md#reading-the-results)
for the plateau criterion and the nominal 64 GB/s reference.

## Results and plot

![Streaming DMA read bandwidth](assets/bandwidth.png)

All 57 saved samples passed data and DRAM traffic checks. The plateau starts
at **64 KiB** under our 95% criterion. At **1 MiB**, bandwidth is **57.777 GB/s**
(**90.28%** of nominal), only about **0.20%** above the 512 KiB result.

The figure shows **1 to 128 KiB** on linear axes; the complete data is in
[assets/bandwidth.csv](assets/bandwidth.csv). The dashed line is the **64 GB/s
theoretical bandwidth** from bus width and transfer rate. The dotted line
estimates the large-copy limit at **57.894 GB/s**.

### How the asymptote is calculated

We fit the latency-bandwidth (alpha-beta/Hockney) model to time per copy:

$$
T(N)=\alpha+\beta N,\qquad B_{\infty}=1/\beta.
$$

`N` is bytes per copy, `T` is its median time in ns, `alpha` is an effective
fixed cost, and `beta` is ns per byte. Thus `1 / beta` gives bytes/ns, equivalent
to GB/s. See the [model reference](https://www.cs.cornell.edu/courses/cs5220/2024fa/slides/08-mpi.pdf).

`plot.py` fits the complete CSV's **64, 128 and 256 KiB** points by ordinary
least squares, even though the figure stops at 128 KiB. This gives
`alpha = 36.625 ns` and `beta = 0.01727294921875 ns/B`. For example, the first
two points give the same limit:

$$
B_{\infty}=\frac{131072-65536}{2300.625-1168.625}
=\frac{65536}{1132}\approx57.894\ \text{GB/s}.
$$

Larger copies approach this limit as the fixed cost is spread over more bytes.
The excluded larger points provide a check:

| Size | Predicted ns per copy | Measured ns per copy |
| --- | ---: | ---: |
| 512 KiB | 9092.625 | 9092.625 |
| 1 MiB | 18148.625 | 18148.625 |

Agreement is at CSV precision in this deterministic simulation. This checks
existing results; it does not establish a physical DRAM limit. Other addresses,
contention or hardware may differ. The fitted fixed cost is not a separate
measurement of DMA startup.

### Regenerate the figure

From the SDK root, using the existing local Python environment with Matplotlib:

```bash
APP_DIR="$PWD/pulp/pulp/chips/softhier/common/sw/app_dram_bandwidth_streaming"
pulp/.venv/bin/python "$APP_DIR/plot.py" "$APP_DIR/assets/bandwidth.csv"
```

For a new run, replace the last argument with `"$RUN_DIR/bandwidth.csv"`.
The script writes PNG and SVG into `assets/`. Use `--output /path/to/figure.png`
to save elsewhere. It needs the 64/128/256 KiB measurements for the fit.

The warm and streaming entry scripts both use `dram_common/plot_bandwidth.py`;
each supplies its own title, CSV and output directory.
