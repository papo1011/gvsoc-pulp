# Multi-cluster HBM DMA

This app measures DMA throughput and contention on SoftHier v1's default
**4×4 mesh**, with **2, 4, 8 and 16 active clusters** and **4 KiB and 64 KiB**
transfers. The single-cluster latency benchmark lives in
[app_dram](../app_dram/README.md).

Only the last core of each cluster drives its iDMA. Compute cores stay idle;
clusters outside the active set wait without issuing DMA traffic.

## Build and run

Run from the **GVSOC SDK root**, with its environment activated and submodules
initialized. You need the RISC-V toolchain, FlooGen, compatible SystemC/DRAMSys
libraries and `hbm4-emu-example.json`. See the [SoftHier setup](../../../README.md).
`DRAMSYS_PATH` must point to the parent of `dramsys_configs/`.

The SDK Makefile must include this line; add it if missing:

```makefile
include pulp/pulp/chips/softhier/softhier.mk
```

```bash
set -e
APP_DIR="$PWD/pulp/pulp/chips/softhier/common/sw/app_dram_multi"
make sh-hw TOPOLOGY=2d_mesh
make sh-sw TOPOLOGY=2d_mesh app="$APP_DIR"

mkdir -p "$PWD/.local-artifacts/dram-multi-benchmark"
RUN_DIR="$(mktemp -d "$PWD/.local-artifacts/dram-multi-benchmark/run.XXXXXX")"
cp sw_build/softhier.elf "$RUN_DIR/softhier.elf"
make sh-run TOPOLOGY=2d_mesh SH_TRACE_ARGS= RUN_ARGS="--work-dir=$RUN_DIR" \
    > "$RUN_DIR/simulation.log" 2>&1
printf 'Results: %s\n' "$RUN_DIR"
```

Run the blocks in the same Bash session, using paths without spaces. Rebuild
hardware too: concurrent timing uses a new read-only simulator clock register.
Without `app=...`, `sh-sw` builds `app_example`. This app creates its own data
and needs no `sh-data`. `SH_TRACE_ARGS=` disables verbose model traces.
`sh-run` uses `sw_build/softhier.elf`; its saved copy
keeps the binary with the results. `sh-sw` recreates `sw_build/`.

## What the sweep measures

For each active count, clusters **0 through N−1** run these scenarios:

| Scenario | HBM destination | Purpose |
| --- | --- | --- |
| `local` | Each cluster's own channel | Throughput with distributed local traffic |
| `remote` | A distinct remote channel per cluster | Throughput across the NoC with distributed destinations |
| `shared` | Channel 15 for every cluster | Contention on routes and a single HBM controller |

The remote mapping shifts both coordinates by two, modulo four:
`(x, y) → ((x + 2) % 4, (y + 2) % 4)`. All destinations are distinct and
four Manhattan hops away. For `shared`, channel 15 is local to cluster 15
when all 16 clusters participate. Each result records its channel and distance.
The active placement is fixed, so comparisons also reflect which routes overlap.

Every scenario and size runs two operations, with eight repetitions per cluster:

- `dma_read`: HBM → local TCDM.
- `dma_write_readback`: local TCDM → HBM → local TCDM, checking a changing word.
  Reported payload includes both directions. DRAMSys acknowledges posted writes
  at acceptance, so this measures a complete pair rather than write completion alone.

That gives **48 timed cases and 360 per-cluster measurements**. Each worker
has a private 64 KiB HBM slot, including on the shared channel, and
a cluster-specific data pattern. Clusters contend without overwriting each
other's buffers. Transfers within a worker run one at a time.

Workers finish initialization and warm-up before receiving a common future
start deadline. Absolute timestamps record actual starts and finishes; the
summary reports remaining start skew. Timing includes DMA setup/waits, timer
reads and the write/readback word check. Full-buffer validation and printing
happen outside the timed interval. The next case waits for all workers.

## Analyze the results

A successful log ends with `[dram-multi] errors=0` and `SoftHier DRAM multi: OK`.
The simulator exit status alone does not catch data errors.

```bash
python3 "$APP_DIR/summarize.py" "$RUN_DIR/simulation.log" > "$RUN_DIR/cluster.csv"
python3 "$APP_DIR/summarize.py" "$RUN_DIR/simulation.log" --aggregate > "$RUN_DIR/aggregate.csv"
python3 "$APP_DIR/query_dram.py" "$RUN_DIR/simulation.log" "$RUN_DIR" > "$RUN_DIR/dram.csv"
```

`cluster.csv` reports each worker's time and useful payload GB/s. `aggregate.csv`
uses **total payload / (last finish − first start)**, and reports start skew
and the slowest worker's duration. It does not add individual bandwidths.

Database analysis needs `DatabaseRecording: true` in the simulation configuration
referenced by `hbm4-emu-example.json`. `query_dram.py` opens databases read-only,
selecting requests generated during each worker's interval **and within its
private HBM slot**. This prevents overlapping shared-channel windows from
counting the same requests. Responses finishing later are included.
DRAM latency includes controller queueing but excludes the NoC trip; physical
burst sizes can make DRAM bytes differ from useful payload bytes.

Both scripts reject failed or incomplete data. Missing timestamps require a
hardware rebuild; missing or ambiguous databases require recording and a fresh
run directory. The multi-cluster parser expects the full 360-row sweep.

Defaults are a 1 GHz system clock, `link_latency=1` and `noc_link_width=512`
**bytes** (4096 bits). Runtime overrides go in `RUN_ARGS`, alongside
`--work-dir=$RUN_DIR`; keep the default 4×4 dimensions for this sweep.
These are measurements of this workload and emulated HBM configuration.

DMA helpers and CSV/database utilities are shared through `../dram_common/`.
