# Local and distant HBM accesses

This benchmark checks how HBM distance affects scalar-load latency and DMA
throughput on SoftHier v1. It runs on the last core of cluster 0; the other
cores stay idle.

With the default 4×4 mesh, it compares channel 0 (`local`, no inter-router
hops) with channel 15 (`far`, six hops). Local accesses still go through the
cluster interface, local router and DRAM interface.

## Run the benchmark

Start from the **GVSOC SDK root**, with the SDK environment activated and
submodules initialized. You need FlooGen, the RISC-V toolchain
(`riscv32-unknown-elf-g++` and `objdump`), and compatible SystemC/DRAMSys
libraries with `hbm4-emu-example.json`. See the
[SoftHier setup](../../../README.md) for the general setup.
`DRAMSYS_PATH` must point to the parent of `dramsys_configs/`.

The SDK Makefile must include this line (add it if missing):

```makefile
include pulp/pulp/chips/softhier/softhier.mk
```

Build the models and select the app:

```bash
set -e
APP_DIR="$PWD/pulp/pulp/chips/softhier/common/sw/app_dram"
make sh-hw TOPOLOGY=2d_mesh
make sh-sw TOPOLOGY=2d_mesh app="$APP_DIR"
```

Without `app=...`, `sh-sw` builds `app_example`. This benchmark creates its
own data, so there is no need to run `sh-data`.

Run in a fresh directory to keep the log and DRAMSys databases together:

```bash
mkdir -p "$PWD/.local-artifacts/dram-benchmark"
RUN_DIR="$(mktemp -d "$PWD/.local-artifacts/dram-benchmark/run.XXXXXX")"
cp sw_build/softhier.elf "$RUN_DIR/softhier.elf"
make sh-run TOPOLOGY=2d_mesh RUN_ARGS="--work-dir=$RUN_DIR" \
    > "$RUN_DIR/simulation.log" 2>&1
printf 'Results: %s\n' "$RUN_DIR"
```

Run these blocks in the same Bash session, using paths without spaces.
`sh-run` uses the ELF in `sw_build/`; the copy above keeps it with the results.
`sh-sw` recreates `sw_build/`, so save results elsewhere.
For another run, create a new `RUN_DIR` and repeat the run block.

## Read the results

A successful log ends with `[dram] errors=0` and `SoftHier DRAM: OK`.
Check these even if the simulator exits with status zero: the exit status
alone does not catch data errors.

```bash
python3 "$APP_DIR/summarize.py" "$RUN_DIR/simulation.log" > "$RUN_DIR/cluster.csv"
python3 "$APP_DIR/query_dram.py" "$RUN_DIR/simulation.log" "$RUN_DIR" > "$RUN_DIR/dram.csv"
```

Both scripts reject incomplete or failed runs. Database analysis needs
`DatabaseRecording: true` in the DRAMSys simulation configuration referenced
by `hbm4-emu-example.json`. If timestamps (`start_ps`/`end_ps`) are missing,
rebuild the hardware with `make sh-hw`. If databases are missing or ambiguous,
check recording is enabled and use a fresh run directory.

The app takes 14 measurements: seven for each destination.

| Operation | What it measures |
| --- | --- |
| `dependent_read` | Average time for 128 dependent 4-byte loads through the narrow path. Each result gives the next address (`index = ring[index]`). |
| `dma_read` | Time and payload GB/s for HBM-to-TCDM copies through the wide path: eight copies at each of 64 B, 4 KiB and 64 KiB. |
| `dma_write_readback` | Time and combined payload GB/s for eight write/readback pairs at each size. |

Compare local and far within each operation. The dependent loads use a fixed,
warmed pattern; DMA copies are issued one at a time. Initialization, warm-up
and full-buffer validation are outside the timers. Loop, timer and DMA setup
costs are included; write/readback also includes a one-word update and check.
DRAMSys acknowledges writes at acceptance, so the write/readback result covers
both directions rather than measuring write completion alone.

`cluster.csv` reports the time seen by the cluster and useful payload GB/s
(bytes/ns). `dram.csv` reports transaction counts, physical DRAM bytes and
latency from request generation to response, including controller queueing.
It selects requests generated during each timed interval, including responses
that finish later. These DRAM timings exclude the NoC trip; physical burst
sizes can also make DRAM bandwidth differ from useful payload bandwidth.

## Configuration and reference results

Defaults are a 1 GHz system clock, `link_latency=1` and `noc_link_width=512`.
Widths are in **bytes**, so the default wide link is 4096 bits. Runtime changes
go in `RUN_ARGS`, alongside `--work-dir=$RUN_DIR`; for example,
`--config-opt system/noc_link_width=64` selects 512-bit links.
For dimensions, core count or memory layout changes, keep the generated
software headers and runtime configuration consistent; see the
[SoftHier configuration guide](../../../README.md#topology-configuration).

An initial run on 2026-11-11 with these defaults and `hbm4-emu-example.json`
completed all 14 measurements with zero data errors:

| Metric | Local | Far |
| --- | ---: | ---: |
| Dependent read, ns/load | 13.508 | 36.430 |
| 64 KiB DMA read, payload GB/s | 56.164 | 55.032 |
| 64 KiB DMA write/readback, combined payload GB/s | 60.046 | 58.754 |

Here, distance mainly affects dependent loads; large DMA transfers reach
similar throughput. These numbers describe this workload and emulated HBM
configuration, with one active cluster.
