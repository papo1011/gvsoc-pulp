## Getting Started

1. Use a complete GVSOC SDK with its submodules initialized. The commands
   below run from the **SDK root**, with this models checkout at `pulp/`,
   rather than from the root of the standalone `gvsoc-pulp` repository.
   Add the following line to the SDK's `Makefile`:

   ```makefile
   include pulp/pulp/chips/softhier/softhier.mk
   ```

2. From the root directory, run the following command to install the required toolchains:

   ```bash
   source pulp/pulp/chips/softhier/softhier_init.sh
   ```

3. Ensure that the host compilers (`CC`, `CXX`) and `CMAKE` are correctly set,
   and that `riscv32-unknown-elf-g++` and `riscv32-unknown-elf-objdump` are in
   `PATH` for the firmware. The 2D mesh also needs the SDK's compatible
   SystemC/DRAMSys installation and its HBM resource files; see the explicit
   prerequisites and environment checks in the experiment guide below.

4. Compile the SoftHier hardware:

   ```bash
   make sh-hw
   ```

   `sh-hw` builds the models currently checked out in `pulp/`. It does not
   update submodules: initialize the SDK dependencies before building, and
   keep `pulp/` on the branch you intend to simulate. The SDK's `make all`
   includes a submodule checkout step and can restore its pinned revision.

5. Compile the SoftHier software:

   ```bash
   make sh-sw
   ```

   The default application is located at:

   ```
   pulp/pulp/chips/softhier/common/sw/app_example
   ```

   [`common/sw/app_dram`](common/sw/app_dram/README.md) compares dependent
   scalar reads and DMA block copies to local and distant HBM. Its guide
   uses these Make targets for build and simulation, and documents measurement
   definitions and DRAMSys database analysis. To select the DRAM app explicitly:

   ```bash
   make sh-sw TOPOLOGY=2d_mesh app=pulp/pulp/chips/softhier/common/sw/app_dram
   ```

   Without `app=...`, `sh-sw` builds `app_example`. The DRAM app initializes
   its own data and does not need `sh-data`.

6. Run the simulation:

   ```bash
   make sh-run
   ```

   To collect simulation outputs in a separate directory, use
   `make sh-run RUN_ARGS="--work-dir=/path/to/run-directory"`. The firmware
   remains in the SDK's `sw_build/` directory.

7. Chips with specific topologies can be called as follows:

   ```bash
      make sh-hw TOPOLOGY=[topology]
      make sh-sw TOPOLOGY=[topology]
      make sh-run TOPOLOGY=[topology]
   ```

   Where `[topology]` can be `2d_mesh` (the default, used when `TOPOLOGY=` is
   omitted), `2d_torus`, `3d_mesh`, `3d_torus`, `ring`, `hierarchical_ring`,
   `hexamesh` or `folded_hexatorus`.

## Topology configuration

Every topology's parameters (cluster count, dimensions, link latencies, ...)
are defined as a class in
[`softhier_arch_base.py`](softhier_arch_base.py), keyed by topology name in
its `TOPOLOGIES` dict. At `make sh-config` time,
[`topologies/gen_floogen_topology.py`](topologies/gen_floogen_topology.py)
generates the FlooGen topology (`.floogen.yml`), routing table
(`.routing.yml`) and per-link latencies (`.link_latencies.yml`) for the
selected topology straight from its `TOPOLOGIES` entry, and writes them to
`topologies/generated/`. The model itself also regenerates them at
simulation-configuration time, from the actual arch including any
`--config-opt` override, into `softhier_topologies/` under the working
directory, so the target runs from any directory (this needs the `floogen`
Python package, see [`requirements.txt`](../../../requirements.txt)).
The same command also regenerates the C headers consumed by the SoftHier
runtime (`common/sw/runtime/include/softhier_arch.h`/`.inc`).

Three ways to change a topology's parameters:

- **`PARAMS=key=value,key2=value2`** (at `make sh-config`/`sh-sw` time). Example:

  ```bash
  make sh-config TOPOLOGY=2d_torus PARAMS=num_cluster=100,link_latency=2
  ```

- **`--config-opt <component-path>/<property>=<value>`** (at `make sh-run`
  time, via `RUN_ARGS`). Example:

  ```bash
  make sh-run TOPOLOGY=2d_torus RUN_ARGS="--config-opt system/num_cluster=48"
  ```

- **`cfg=<path-to-file>`** (at `make sh-config` time): bypasses the
  `TOPOLOGIES` registry entirely and scrapes arch parameters from a custom
  Python file instead, for a full replacement rather than a few field
  overrides.

Build-time `PARAMS` do not automatically persist into the simulator's
runtime architecture. Pass matching `RUN_ARGS` overrides when running a
changed configuration, particularly for dimensions and memory layout: the
generated software headers and simulated architecture must agree.
