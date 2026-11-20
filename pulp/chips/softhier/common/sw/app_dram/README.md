# Local and distant HBM accesses

How does HBM distance affect scalar-load latency and DMA throughput on
SoftHier v1? Only the DM core (last core) of cluster 0 runs. On the default 4×4
mesh it compares channel 0 (`local`, 0 hops) with channel 15 (`far`, 6 hops).
For the multi-cluster sweep, see [app_dram_multi](../app_dram_multi/README.md).

> **Wide-port model.** The reference results use the asynchronous wide-port
> arbiter. The earlier synchronous arbiter added about 104 ns to every 64 KiB
> read through a modeling artifact; see the [explanation](../app_dram_bandwidth_streaming/README.md#wide-port-arbiter-model).

## What it measures

14 measurements, seven per destination:

| Operation | What it measures |
| --- | --- |
| `dependent_read` | Average time of 128 dependent 4 B loads on the narrow path (`index = ring[index]`). |
| `dma_read` | HBM → TCDM copies on the wide path: eight copies each of 64 B, 4 KiB and 64 KiB. |
| `dma_write_readback` | Eight write + readback pairs per size; payload counts both directions. |

Copies run one at a time. Initialization, warm-up and full-buffer checks are
untimed; loop, timer and DMA setup costs are included. DRAMSys acknowledges
writes on acceptance, so write/readback measures the pair, not write completion.

## Build and run

From the **SDK root**, with submodules initialized and the
[SoftHier setup](../../../README.md) done: you need FlooGen, the RISC-V toolchain
and SystemC/DRAMSys with `hbm4-emu-example.json`; `DRAMSYS_PATH` must point to
the folder containing `dramsys_configs/`. The SDK `Makefile` must include
`include pulp/pulp/chips/softhier/softhier.mk`.

```bash
set -e
APP_DIR="$PWD/pulp/pulp/chips/softhier/common/sw/app_dram"
make sh-hw TOPOLOGY=2d_mesh
make sh-sw TOPOLOGY=2d_mesh app="$APP_DIR"
mkdir -p "$PWD/.local-artifacts/dram-benchmark"
RUN_DIR="$(mktemp -d "$PWD/.local-artifacts/dram-benchmark/run.XXXXXX")"
cp sw_build/softhier.elf "$RUN_DIR/softhier.elf"
make sh-run TOPOLOGY=2d_mesh SH_TRACE_ARGS= RUN_ARGS="--work-dir=$RUN_DIR" \
    > "$RUN_DIR/simulation.log" 2>&1
```

The app creates its own data, so `sh-data` is not needed. `sh-sw` recreates
`sw_build/`, which is why the ELF is copied into the run directory.

## Results

A valid log ends with `[dram] errors=0` and `SoftHier DRAM: OK`; check them,
because the exit status does not catch data errors.

```bash
python3 "$APP_DIR/summarize.py" "$RUN_DIR/simulation.log" > "$RUN_DIR/cluster.csv"
python3 "$APP_DIR/query_dram.py" "$RUN_DIR/simulation.log" "$RUN_DIR" > "$RUN_DIR/dram.csv"
```

- `cluster.csv`: time seen by the cluster and useful payload GB/s.
- `dram.csv`: transactions, physical DRAM bytes and DRAM latency (including
  controller queueing, excluding the NoC). Needs `DatabaseRecording: true`.

Missing `start_ps`/`end_ps` means the hardware must be rebuilt (`make sh-hw`).

Reference run (2026-11-20, asynchronous wide-port arbiter), defaults: 1 GHz,
`link_latency=1`, `noc_link_width=512` bytes. All 14 measurements without errors:

| Metric | Local | Far |
| --- | ---: | ---: |
| Dependent read, ns/load | 13.766 | 36.969 |
| 64 KiB DMA read, payload GB/s | 61.659 | 60.298 |
| 64 KiB DMA write/readback, combined payload GB/s | 62.985 | 61.565 |

Compared with the synchronous arbiter, each 64 KiB copy is about 104 ns faster
(read) and 102 ns faster (write/readback); single-burst write/readback copies
are 10–14 ns slower, because the asynchronous arbiter adds one cycle to each
512 B write chunk and the iDMA sends them one at a time.

Distance mostly affects dependent loads; large DMA copies reach similar
throughput. Runtime options go in `RUN_ARGS` (e.g.
`--config-opt system/noc_link_width=64`); for other changes see the
[configuration guide](../../../README.md#topology-configuration).
