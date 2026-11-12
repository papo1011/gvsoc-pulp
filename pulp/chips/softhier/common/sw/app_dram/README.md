# Local and distant HBM accesses

This single-cluster benchmark compares two ways of accessing HBM: dependent
scalar reads executed by a core, and block copies executed by its iDMA. It
measures both against a nearby and a distant HBM channel on SoftHier v1.

## What is being compared

The default topology is a 4×4 mesh. Only cluster 0's last core runs the test
(core 1 with the default two cores per cluster). This is the core connected
to the iDMA command interface. Other cores return to the startup WFI loop.

| Destination | Channel | Coordinates | Inter-router hops, one way |
| --- | ---: | --- | ---: |
| `local` | 0 | (0, 0) | 0 |
| `far` | 15 | (3, 3) | 6 |

A channel here is one DRAMSys memory/controller instance, with its own mapped
address window. Narrow and wide are NoC paths that can reach the same channel.
Even local HBM access traverses the cluster NI, local router and DRAM NI.
The logical connections do not establish a physical stacking/packaging layout.

### Dependent scalar reads: latency

In [main.cpp](main.cpp), the timed loop is:

```cpp
for (uint32_t i = 0; i < READS; ++i)
    index = ring[index];
```

The value returned by a load is the index of the next load. For example,
`ring[0]` contains 272, so the core must obtain that value before it can read
`ring[272]`. These are ordinary 4-byte scalar loads through the narrow path.
The string `dependent_read` is a log label; the assignment above enforces the
address dependency.

The ring is a data structure in HBM, independent of the mesh topology. Its 64
nodes are spaced 64 bytes apart within a 4 KiB region. Initialization uses
`((i + 17) % 64) * STRIDE_WORDS`: 17 and 64 are coprime, so all nodes are
visited before returning to index zero. The ring permits repeated traversal
without a separate end-of-list/restart operation. One full traversal warms the
pattern; 128 timed reads make two further traversals.

This measures average cluster-observed dependent-load latency, including loop
and timer overhead. It is a fixed, warmed access pattern, not a distribution
of random or cold-row accesses. A real application could instead DMA this
small region into TCDM and traverse it locally; that optimization is not part
of the current benchmark.

### DMA block copies: transfer time and throughput

The timed read loop calls:

```cpp
for (uint32_t i = 0; i < REPEATS; ++i)
    copy(DST, data_address, bytes);
```

`data_address` is in the selected HBM channel and `DST` is in cluster 0's TCDM.
`copy()` starts `flex_dma_async_1d()` and immediately waits with
`flex_dma_async_wait_all()`, with memory/compiler fences around the operation.
Source, destination and size are known before the copy; they do not depend on
the values being copied. The iDMA uses the wide path and can have multiple
internal requests in flight. The eight complete copies are issued one at a
time, rather than eight simultaneously outstanding DMA transfers.

| Log operation | Work per destination | Reported quantity |
| --- | --- | --- |
| `dependent_read` | 128 dependent 4-byte loads | ns per scalar load |
| `dma_read` | 8 copies at each of 64 B, 4 KiB and 64 KiB | ns per copy and useful payload GB/s |
| `dma_write_readback` | 8 write/readback pairs at each size | ns per pair and combined write + read payload GB/s |

These are seven measurements per destination, fourteen overall. Compare local
and far within each operation: scalar reads and DMA use different initiators,
NoC paths and request patterns.

For write/readback, each repetition changes the first source word, writes the
block to HBM, reads it back and checks that word. The v1 DRAMSys wrapper
acknowledges posted writes at acceptance. A write-only DMA timer would
therefore describe submission/acceptance rather than physical DRAM completion.
The reported pair time/bandwidth includes both directions and is not an
isolated write-latency or write-bandwidth measurement.

Initialization, warm-up, full-buffer validation and logging are outside the
timed regions. Write/readback includes the one-word update/check. The same
channel-relative addresses and sizes are used for both destinations. The test
uses two 64 KiB TCDM buffers from offset `0x1000` and the final 68 KiB of each
selected HBM channel for its ring and transfer buffer.

## Prerequisites and supported layout

The commands below use Bash on Linux and a complete, compatible GVSOC SDK.
This repository contains models, not a standalone SDK. You need:

- SDK submodules initialized, its host build dependencies, GNU Make and CMake.
- An activated Python environment with the SDK dependencies and this checkout's
  [requirements.txt](../../../../../../requirements.txt), including FlooGen.
  The two CSV analysis scripts themselves use only the Python standard library.
- `riscv32-unknown-elf-g++` and `riscv32-unknown-elf-objdump` in `PATH`, with
  support for `rv32imafdv_zfh_zvfh` and the custom instructions used by the runtime.
  The compiler names are currently fixed in the software CMake file.
- SystemC headers/libraries and a compatible patched `libDRAMSys_Simulator.so`.
  Build the library against the same SystemC installation/ABI as the GVSOC
  launcher. A prebuilt library is not necessarily compatible with another host.
- The DRAMSys resource tree supplied by the matching SDK, including
  `dramsys_configs/hbm4-emu-example.json` and its referenced files. The patched
  library must understand this configuration and support database recording.

Follow the SDK's build and `DRAMSys.md` instructions to provision those
prerequisites. Do not rely on an ETH-specific compiler executable or private
shell helper. The explicit roots below support either the SDK's `pulp/`
submodule or a separate checkout of these models.

### 1. Select paths and check the environment

Activate your Python environment first. Set these roots for your installation:

```bash
export SDK_ROOT=/path/to/gvsoc-sdk
export PULP_ROOT="$SDK_ROOT/pulp"  # or /path/to/this/gvsoc-pulp checkout
export SYSTEMC_HOME="${SYSTEMC_HOME:-$SDK_ROOT/third_party/systemc_install}"
export DRAMSYS_LIB_DIR="${DRAMSYS_LIB_DIR:-$SDK_ROOT/third_party/DRAMSys}"
export DRAMSYS_PATH="${DRAMSYS_PATH:-$SDK_ROOT/add_dramsyslib_patches}"
export PATH="$SDK_ROOT/third_party/toolchain/install/bin:$SDK_ROOT/gapy/bin:$PATH"
export LD_LIBRARY_PATH="$DRAMSYS_LIB_DIR:$SYSTEMC_HOME/lib64:$SYSTEMC_HOME/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$PULP_ROOT:$PULP_ROOT/models:$PULP_ROOT/targets:$SDK_ROOT/engine/python:$SDK_ROOT/core/models:$SDK_ROOT/gvrun/python:$SDK_ROOT/config_tree:$SDK_ROOT/gapy/bin${PYTHONPATH:+:$PYTHONPATH}"
SH_ROOT="$PULP_ROOT/pulp/chips/softhier"
APP_DIR="$SH_ROOT/common/sw/app_dram"
```

`DRAMSYS_PATH` is the **parent** of `dramsys_configs/`, not that directory
itself. If dependencies live elsewhere, set the three library/resource
variables above to those installations. The SDK build system must also support
these models; selecting a different Python path cannot fix incompatible APIs.

Run the following blocks in the same Bash session. Stop on a failed command:

```bash
set -e
for tool in make cmake python3 riscv32-unknown-elf-g++ riscv32-unknown-elf-objdump; do
    command -v "$tool" >/dev/null
done
test -f "$SDK_ROOT/Makefile"
test -f "$APP_DIR/main.cpp"
test -f "$SYSTEMC_HOME/include/systemc"
test -f "$DRAMSYS_PATH/dramsys_configs/hbm4-emu-example.json"
python3 - <<'PYTHON'
import ctypes
import os
import elftools
import floogen
import yaml
# SystemC resolves sc_main from the simulator executable, not from Python.
library = ctypes.CDLL(
    os.path.join(os.environ['DRAMSYS_LIB_DIR'], 'libDRAMSys_Simulator.so'),
    mode=os.RTLD_LAZY,
)
for symbol in ('add_dram', 'dram_send_req', 'dram_register_async_callback'):
    getattr(library, symbol)
print('Python dependencies and DRAMSys library loading: OK')
PYTHON
```

### 2. Build in a fresh output directory

This workflow builds the selected checkout explicitly, without adding a line
to the SDK Makefile or loading a local Makefile adapter. It copies the software
sources before generating architecture headers, preserving the source checkout.
SDK build paths should not contain spaces, as required by the underlying SDK
Makefile commands.

```bash
mkdir -p "$SDK_ROOT/.local-artifacts/dram-benchmark"
BENCH_ROOT="$(mktemp -d "$SDK_ROOT/.local-artifacts/dram-benchmark/case.XXXXXX")"
INSTALL_ROOT="$BENCH_ROOT/install"
RUN_DIR="$BENCH_ROOT/run"
mkdir -p "$RUN_DIR"
cp -R "$SH_ROOT/common/sw" "$BENCH_ROOT/sw-src"
python3 "$SH_ROOT/common/utils/config.py" 2d_mesh \
    "$BENCH_ROOT/sw-src/runtime/include"

MODULE_ROOTS="$SDK_ROOT/engine/python;$SDK_ROOT/core/models;$PULP_ROOT;$PULP_ROOT/models;$PULP_ROOT/targets;$SDK_ROOT/gvrun/python;$SDK_ROOT/config_tree"
env -u MAKEFILES make -C "$SDK_ROOT" build \
    TARGETS=pulp.chips.softhier.topologies.softhier_2d_mesh_target \
    "GVSOC_MODULES=$MODULE_ROOTS" \
    "BUILDDIR=$BENCH_ROOT/hw-build" "INSTALLDIR=$INSTALL_ROOT"

cmake -S "$BENCH_ROOT/sw-src" -B "$BENCH_ROOT/sw-build" \
    "-DSRC_DIR=$BENCH_ROOT/sw-src/app_dram" \
    -DRISCV_ARCH=rv32imafdv_zfh_zvfh
env -u MAKEFILES cmake --build "$BENCH_ROOT/sw-build"
cp "$BENCH_ROOT/sw-build/softhier.elf" "$RUN_DIR/softhier.elf"
```

The hardware rebuild includes absolute performance-counter timestamps needed
by the database query. `MAKEFILES` is cleared for this build so a private shell
adapter cannot redirect the selected sources, build or installation directory;
it is also cleared when building the firmware.
The app generates its own input data; matrix generation from `app_example`
is unnecessary.

### 3. Run and analyze

```bash
export PATH="$INSTALL_ROOT/bin:$PATH"
export PYTHONPATH="$INSTALL_ROOT/python:$PYTHONPATH"
(
    cd "$RUN_DIR"
    "$INSTALL_ROOT/bin/gvsoc" \
        --target=pulp.chips.softhier.topologies.softhier_2d_mesh_target \
        --binary "$RUN_DIR/softhier.elf" run > simulation.log 2>&1
)
python3 "$APP_DIR/summarize.py" "$RUN_DIR/simulation.log" > "$RUN_DIR/cluster.csv"
python3 "$APP_DIR/query_dram.py" "$RUN_DIR/simulation.log" "$RUN_DIR" > "$RUN_DIR/dram.csv"
printf 'Results: %s\n' "$RUN_DIR"
```

DRAMSys writes databases in the working directory. Each fresh `BENCH_ROOT`
keeps its ELF, log, configuration, CSVs and `.tdb` files together without
replacing earlier results. The expected final log lines are `[dram] errors=0`
and `SoftHier DRAM: OK`. SoftHier v1's exit register returns host status zero
even on a failed data check, so use the analysis scripts to validate success;
both reject a failed/incomplete log and require all 14 measurements.

For a repeat run, create a fresh `RUN_DIR`, copy the ELF into it and run/analyze
there. The existing `make sh-sw` shortcut recreates `sw_build`; preserve any
results stored there before using that shortcut.

### Configuration changes and interpretation

The commands above retain the current defaults. In this model, widths are in
**bytes**: `narrow_width=8` means 64 bits and `noc_link_width=512` means 4096
bits. To investigate 512-bit wide links, add
`--config-opt system/noc_link_width=64` before `run` and save that experiment in
a new run directory. That is a different configuration from the initial results.

The default system clock is 1 GHz. `link_latency=1` is the configured delay;
the current engine queue uses `current_cycle + delay + 1` when enqueuing an
incoming router request. It is not a guarantee of one complete hop per cycle.
`--config-opt system/link_latency=2` changes the delay assigned to both
inter-router links and NI/router links. Queueing and arbitration add further
waiting under load. Runtime configuration regenerates the topology under
`RUN_DIR/softhier_topologies/` using the actual overrides.

Changes to dimensions, core count or memory layout require matching generated
software headers and simulator configuration; keep the defaults for this guide.
HBM4 is emulated by the installed DRAMSys configuration; this benchmark is not
calibrated against a production chip. A single active cluster measures distance
at low load, without competing application traffic from other clusters.

## Reading the results

`summarize.py` pairs each `[dram]` description with the next performance-counter
line. `cluster.csv` reports total ns, ns per operation and decimal useful payload
GB/s. For eight 64 KiB reads taking 9,335 ns:

```text
ns per copy = 9,335 / 8 = 1,166.875
payload GB/s = (8 × 65,536) / 9,335 = 56.163685
```

Bytes/ns equals decimal GB/s. For write/readback, payload is doubled because
both directions are counted, and one operation is one complete pair. Timer,
loop, DMA setup and wait costs are included; no overhead is subtracted.

## Querying DRAMSys

Enable `DatabaseRecording: true` in the simulation configuration referenced by
`hbm4-emu-example.json`. Keep that resource tree and the generated
`gvsoc_config.json` with the results when comparing installations; `GeneralInfo`
in each database records the memory specification and controller configuration.

`query_dram.py` opens `DRAMSysRecordable<channel>_*.tdb` read-only using Python's
standard `sqlite3` module. It joins `Transactions.ID` to `Phases.Transact` and
selects `RESP` phases for read/write requests generated in the half-open
`[start_ps, end_ps)` measurement interval. Their responses are included even
when they finish after the interval. Maintenance transactions (`Command = 'X'`)
are excluded from the selected requests, although their effects on timing remain.

`dram.csv` contains a row per measurement and recorded direction (`R` or `W`):

- Transaction count and DRAM bytes, including physical burst expansion.
- Mean/minimum/maximum time from `TimeOfGeneration` to `RESP.PhaseEnd`, converted
  from picoseconds to nanoseconds after checking `GeneralInfo.UnitOfTime`.
- Selected requests' DRAM bytes divided by the cluster measurement interval:
  a request-cohort throughput, not instantaneous bus utilization.

These latencies include controller queueing and exclude the NoC path before
DRAMSys receives a request and after its response. Compare them with cluster
timings, but subtracting separate averages does not give an exact NoC latency.
A 4-byte load can generate a larger physical DRAM transaction, so DRAM bandwidth
can differ from useful payload bandwidth. Optional/windowed `Bandwidth`,
`BufferDepth` and `Power` tables are not used for these short measurements.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Missing `floogen`, `yaml` or `elftools` | Activate the intended Python environment and install its SDK/model requirements. |
| Missing RISC-V compiler or unsupported `-march` | Put a compatible RISC-V toolchain in `PATH`; host `CC`/`CXX` do not select the firmware compiler. |
| `libDRAMSys_Simulator.so` or `libsystemc.so` loading error | Check library directories and rebuild DRAMSys against the launcher's SystemC version/ABI. |
| DRAMSys config/JSON parsing error | Check `DRAMSYS_PATH`, all referenced files and the patched library/configuration compatibility. |
| Missing `start_ps`/`end_ps` | Rebuild the hardware from this checkout and use that installation's launcher/models. |
| Zero or multiple matching channel databases | Use recording-enabled DRAMSys and a fresh run directory. |
| Data errors or fewer than 14 measurements | Inspect `simulation.log`; the process exit status alone is insufficient. |

## Command validation

On 2026-11-12, this workflow was checked on Linux from an environment cleared
with `env -i`, with an activated Python environment and compatible, previously
installed SystemC/DRAMSys dependencies. The model checkout was outside the SDK;
hardware, installation, firmware and run directories were newly created. No
private environment script, Makefile adapter or modified SDK Makefile was used.
The build, simulation and both analysis commands completed: zero data errors,
14 cluster measurements and 20 DRAM direction rows. Both CSVs matched the
initial run. This checks the documented workflow, not installation of the
prerequisites or compatibility with every host/SDK revision.

## Initial run

Measured on 2026-11-11 with the default 4×4 mesh, 1 GHz system clock,
`link_latency=1`, `noc_link_width=512` **bytes**, and the installed `hbm4-emu-example.json` configuration
(`GeneralInfo`: 250 ps DRAM clock, 32-byte transactions, FR-FCFS/open-page
controller). All 14 measurements completed with zero data errors.

| Metric | Local channel 0 | Distant channel 15 |
| --- | ---: | ---: |
| Cluster dependent read, ns/load | 13.508 | 36.430 |
| DRAMSys dependent-read cohort, mean ns/transaction | 11.156 | 11.422 |
| DRAMSys dependent-read cohort, min–max ns/transaction | 9.250–74.000 | 9.250–50.000 |
| Cluster 64 KiB DMA read, payload GB/s | 56.164 | 55.032 |
| Cluster 64 KiB DMA write/readback, combined payload GB/s | 60.046 | 58.754 |
| DRAMSys 64 KiB DMA read cohort, mean ns/transaction | 41.704 | 41.968 |
| DRAMSys 64 KiB write/readback writes, mean ns/transaction | 7.694 | 7.996 |

SQL validation found exactly 128 reads in each dependent-read window, and
16, 1,024 and 16,384 transactions per direction in the respective DMA windows.
Only channels 0 and 15 recorded program reads/writes. Warm-up and validation
traffic were excluded by the timestamp filters. The distance penalty is most
visible in small/dependent accesses; the large-transfer throughput is similar
in this experiment. These are results for this fixed workload, including its
refresh timing, rather than hardware-independent latency guarantees.
