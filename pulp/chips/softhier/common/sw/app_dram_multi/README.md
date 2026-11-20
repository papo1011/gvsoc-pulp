# Multi-cluster HBM DMA

DMA throughput and contention on SoftHier v1's default **4×4 mesh**, with
**2, 4, 8 and 16 active clusters** and **4 KiB and 64 KiB** transfers. Only the
DM core of each active cluster drives its iDMA; the rest stays idle. The
single-cluster benchmark is [app_dram](../app_dram/README.md).

> **Model caveat.** Each cluster has its own wide-port router with a modeling
> artifact that adds about 105 ns to every 64 KiB read (16 bursts of 4 KiB,
> 7 ns each). See the [explanation](../app_dram_bandwidth_streaming/README.md#model-artifact-wide-port-limiter).
> 4 KiB transfers (one burst) are not affected; write/readback is affected only
> through its readback. The cost is per copy, so it should weigh less when DRAM
> contention already slows transfers.

## Scenarios

For each active count, clusters 0 to N−1 run:

| Scenario | HBM destination | Purpose |
| --- | --- | --- |
| `local` | Each cluster's own channel | Distributed local traffic |
| `remote` | A distinct channel 4 hops away: `(x, y) → ((x+2)%4, (y+2)%4)` | Traffic across the NoC |
| `shared` | Channel 15 for every cluster | Contention on routes and one HBM controller |

Each scenario and size runs `dma_read` (HBM → TCDM) and `dma_write_readback`
(TCDM → HBM → TCDM, payload counts both directions), eight repetitions per
cluster: **48 timed cases, 360 measurements**. Every worker has a private 64 KiB
HBM slot and its own data pattern, so clusters contend without overwriting each
other.

Workers finish setup, then all receive the same future start deadline; actual
start and end times are logged, and the next case waits for everyone. This
needs the simulator clock register added for this app: rebuild the hardware
(`make sh-hw`).

## Build and run

From the **SDK root**, after the [SoftHier setup](../../../README.md) (FlooGen,
RISC-V toolchain, SystemC/DRAMSys with `hbm4-emu-example.json`, `DRAMSYS_PATH`
pointing to the folder containing `dramsys_configs/`, and
`include pulp/pulp/chips/softhier/softhier.mk` in the SDK `Makefile`).

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
```

## Analyze the results

A valid log ends with `[dram-multi] errors=0` and `SoftHier DRAM multi: OK`.

```bash
python3 "$APP_DIR/summarize.py" "$RUN_DIR/simulation.log" > "$RUN_DIR/cluster.csv"
python3 "$APP_DIR/summarize.py" "$RUN_DIR/simulation.log" --aggregate > "$RUN_DIR/aggregate.csv"
python3 "$APP_DIR/query_dram.py" "$RUN_DIR/simulation.log" "$RUN_DIR" > "$RUN_DIR/dram.csv"
```

- `cluster.csv`: time and useful payload GB/s for each worker.
- `aggregate.csv`: **total payload / (last end − first start)**, plus start
  skew and slowest worker. Individual bandwidths are not summed.
- `dram.csv`: DRAM traffic per worker, selected by time window **and** private
  slot so shared-channel windows are not double counted. Needs
  `DatabaseRecording: true`.

The scripts reject incomplete runs and expect all 360 rows. Defaults: 1 GHz,
`link_latency=1`, `noc_link_width=512` bytes; keep the 4×4 dimensions.
