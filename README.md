# PuD-Hammulator

PuD-Hammulator is a `gem5` + `DRAMsim3` based rowhammer simulation framework for studying Processing-using-DRAM(PuD) style rowhammer behavior, attack-mode switching, and mitigation mechanisms.

This repository keeps the Hammulator-specific code, helper libraries, workloads, and patch sets used to modify upstream `gem5` and `DRAMsim3`.

## What Is In This Repo

- `gem5-patches/`: patch series for upstream `gem5`
- `DRAMsim3-patches/`: patch series for upstream `DRAMsim3`
- `Makefile`: build and run entry points
- `libhammer.*`: helper library for virtual-to-physical translation and simple timing helpers
- `libswapcpu.*`: helper library for CPU switching in `gem5`
- `progs/`: example, verification, and exploit workloads

## Tested Base Versions

- `gem5`: `v22.1.0.0`
- `DRAMsim3`: `1.0.0`

## Installation

### 1. Clone this repository

```bash
git clone <your-repo-url> PuD-Hammulator
cd PuD-Hammulator
```

### 2. Clone the upstream dependencies

```bash
git clone -b v22.1.0.0 https://github.com/gem5/gem5 gem5
git clone -b 1.0.0 https://github.com/umd-memsys/DRAMSim3 \
    gem5/ext/dramsim3/DRAMsim3
```

### 3. Install build dependencies

For Ubuntu 22.04/24.04, the following packages are enough for the current tree:

```bash
sudo apt update
sudo apt install -y \
    build-essential git m4 scons zlib1g zlib1g-dev \
    libprotobuf-dev protobuf-compiler libprotoc-dev \
    libgoogle-perftools-dev python3-dev libboost-all-dev pkg-config \
    cmake libinih-dev genext2fs
```

## Applying The Patches

The modified implementation lives in the local `gem5` and `DRAMsim3` trees, but the repository also ships patch bundles so the setup can be recreated from clean upstream checkouts.

### Patch `gem5`

```bash
git -C gem5 checkout v22.1.0.0
git -C gem5 am ../gem5-patches/*.patch
```

### Patch `DRAMsim3`

```bash
git -C gem5/ext/dramsim3/DRAMsim3 checkout 1.0.0
git -C gem5/ext/dramsim3/DRAMsim3 am ../../../../DRAMsim3-patches/*.patch
```

If you already have local edits in either tree, commit or stash them before running `git am`.

## Building

Build DRAMsim3, the `m5` helper binary, and `gem5`:

```bash
make hammulator
```

Useful partial targets:

```bash
make dramsim3
make m5
make compile_commands
```

## PuD-Hammulator Features

### Attack Modes

PuD-Hammulator extends `gem5` so workloads can switch rowhammer behavior at runtime using:

```c
#include <gem5/m5ops.h>
m5_work_begin(mode_id, 0);
```

The implementation currently interprets `mode_id` as:

- `0`: `NORMAL`
- `1`: `SiMRA`
- `2`: `CoMRA`
- `3`: `RESET_BYPASS`

When writing workload-side helper macros, treat this mapping as the source of truth.

`m5_work_begin()` is handled in `gem5/src/sim/pseudo_inst.cc`, and the selected mode is consumed by the DRAMsim3 model in `gem5/src/mem/dramsim3.cc`.

### New Modeling Support

Compared with the original Hammulator setup, this tree adds or extends:

- Runtime attack-mode switching through `m5_work_begin()`
- `NORMAL`, `SiMRA`, and `CoMRA` attack behaviors
- `RESET_BYPASS` mode for reset/initialization phases
- Temperature-aware hammer amplification
- `tAggOn`-related attack tuning knobs
- Spatial row-vulnerability variation
- Non-linear bit-flip growth near `HC_last`
- Mitigation statistics printed at simulation exit
- Additional mitigation models:
  - `PARA`
  - `TRR`
  - `PRAC+ABO`
  - `SALT`
  - `SALT-C`

### Output Handling

`configs/common/MemConfig.py` has been updated so DRAMsim3 outputs are written into the active `gem5` output directory, which keeps per-run stats and traces together under `m5out-*`.

## DRAM Configuration Parameters

The main DRAM/rowhammer configuration file is:

`gem5/ext/dramsim3/DRAMsim3/configs/DDR4_8Gb_x8_2400.ini`

The most important PuD-Hammulator parameters are below.

### Core Rowhammer Parameters

- `HC_first`: hammer count where flips begin
- `HC_last`: hammer count where additional flips saturate
- `HC_last_bitflip_rate`: quadword flip probability near `HC_last`
- `non_linear_degree`: controls the non-linear flip-rate ramp
- `inc_dist_1` ... `inc_dist_5`: distance-dependent disturbance weights
- `proba_1_bit_flipped` ... `proba_4_bit_flipped`: per-quadword flip multiplicity
- `flip_mask`: optional fixed mask instead of probabilistic bit selection

### PuD Attack Parameters

- `SiMRA_weight`
- `CoMRA_weight`
- `temperature`
- `baseline_temperature`
- `temperature_scale_factor`
- `tRAS_baseline_ticks`
- `tAggOn`
- `tAggOn_max_ticks`
- `simra_taggon_max_penalty`
- `normal_taggon_max_penalty`

### Spatial Variation Parameters

- `spatial_variation_enabled`
- `spatial_variation_seed`
- `spatial_q01_multiplier`
- `spatial_q05_multiplier`
- `spatial_q10_multiplier`
- `spatial_q100_multiplier`

These parameters model the fact that not all rows are equally vulnerable.

### Mitigation Parameters

- `para_enabled`
- `para_proba`
- `trr_enabled`
- `trr_threshold`
- `prac_abo_enabled`
- `prac_threshold`
- `prac_blast_radius`
- `salt_enabled`
- `salt_coord_refresh`
- `salt_row_striping`
- `salt_bundle_rows`
- `salt_rows_per_subarray`
- `salt_subarrays`
- `salt_target_max_activations`
- `salt_apm`
- `salt_ath`

## Running Simulations

### Syscall Emulation

Syscall emulation is the fastest way to test the memory model and workloads.

Run the bundled example:

```bash
make example
```

Run the verification workload:

```bash
make verify
```

Run your own binary:

```bash
make se CMD=/absolute/path/to/your_binary
```

The default syscall-emulation configuration uses:

- `--cpu-type=X86AtomicSimpleCPU`
- `--repeat-switch 1`
- `--mem-type=DRAMsim3`

## Switching Modes Inside A Workload

The current implementation uses `workid` from `m5_work_begin(workid, threadid)` as the rowhammer mode selector.

Example:

```c
#include <gem5/m5ops.h>

#define SET_MODE_NORMAL m5_work_begin(0, 0)
#define SET_MODE_SIMRA  m5_work_begin(1, 0)
#define SET_MODE_COMRA  m5_work_begin(2, 0)
#define SET_MODE_RESET  m5_work_begin(3, 0)
```

A typical usage pattern is:

1. Enter reset mode while initializing or rewriting memory.
2. Switch back to `NORMAL` before the actual experiment.
3. Enter `SiMRA` or `CoMRA` during the attack phase.

## Debugging

The Makefile already enables `DRAMsim3,PuDHammer` debug flags by default. You can override them per run:

```bash
make verify DEBUG=DRAMsim3,PuDHammer
make fs-restore DEBUG=DRAMsim3,PuDHammer
```

To see the full list of `gem5` debug flags:

```bash
build/X86/gem5.opt --debug-help
```

## Notes

- The top-level repository is meant to track Hammulator code and patch bundles; `gem5` itself is ignored by `.gitignore`.
- `DRAMsim3` stats and traces are written next to each run's `gem5` output directory.
- If you change `memsize` in the Makefile, recreate full-system checkpoints.
