# autotest: config-driven RowHammer experiments

One YAML file describes every run. `./run_tests.sh` reads it, runs the gem5 + DRAMsim3 simulations
in parallel, then collects the numbers, draws the figures and writes a report per experiment.
All paths in the configs are relative to this directory, so it works from a fresh clone once the
simulator is built.

## Quick start

```bash
cd autotest
./run_tests.sh --rebuild -c configs/smoke.yaml   # build the simulator, then a 30-run (~2 min) check
./run_tests.sh --dry-run                         # list what the enabled experiments would run
./run_tests.sh --background                      # run the enabled experiments, detached
tail -f results/auto/logs/<timestamp>.log
```

Results are listed in `results/auto/INDEX.md`; each experiment has its own `REPORT.md`.

Requirements:
- The patched simulator: apply `gem5-patches/` and `DRAMsim3-patches/` (see the top-level README),
  then build with `make hammulator && make build/tmp_root/verify` in the repo root, or pass `--rebuild`.
- Python 3.8+ with PyYAML, matplotlib and numpy.

Do not rebuild while runs are in progress.

## Command-line options

| Option | Effect |
|---|---|
| `-c FILE` | config file (default `configs/experiments.yaml`) |
| `-e a,b` | run only these experiments, including ones with `enabled: false` |
| `-j N` | parallel gem5 processes (default `run.jobs`) |
| `--dry-run` | print the run matrix; nothing is started |
| `--post-only` | skip simulation; re-collect, re-plot and re-report from finished runs |
| `--rebuild` | rebuild gem5, DRAMsim3 and the `verify` replay program first |
| `--background` | detach with `nohup`; the log goes to `<results>/logs/<timestamp>.log` |

Single steps: `python3 scripts/autotest.py {run,collect,plot,report,list,all} [-c FILE] [-e a,b] [-j N]`.

## The config file

`configs/experiments.yaml` is the reference; it is commented. `configs/smoke.yaml` is a minimal,
self-contained example. The sections:

| Section | Contents |
|---|---|
| `paths` | `hammulator` (the simulator checkout, `..`), `base_ini` (the DRAMsim3 profile), `results` folder. Relative paths are relative to `autotest/`. |
| `run` | `jobs`, `pud_window_ns` (PuD refresh window, 64 ms), `debug` (PuDHammer debug log; large) |
| `timing` | ns per AP / AAP / ACT, per victim refresh (`t_rc`) and per ABO. Used only to compute PuD time and overhead when collecting. |
| `metric` | `sim` (simulator `bits_flipped`, default) or `harness` (replay-harness scan; misses rows below row 0) |
| `models` | named sets of physics `[rowhammer]` keys; `base: <model>` inherits another model and overrides it |
| `mechanisms` | mitigation definitions, below |
| `workloads` | trace folders, below |
| `experiments` | what to run, below |

**A mechanism** is a set of `[rowhammer]` keys, optionally swept:

```yaml
salt:
  label: SALT                               # legend name
  color: "#eda100"                          # plot colour
  set: {salt_enabled: true}                 # fixed keys
  sweep: {salt_ath: [10, 40, 78, 300]}      # one config per value; several keys = full grid
  derive: {salt_apm: "max(1, salt_ath // 2)"}   # computed from the swept keys
  short: {salt_ath: ath}                    # name in config labels -> salt_ath78
```

**A workload** is a folder of replay traces; every `*.txt` in it is one trace:

```yaml
apps:
  trace_dir: traces/apps
  include: ["gmm", "km"]                    # optional: keep files whose name contains any of these
  exclude: ["99_"]                          # optional: drop files whose name contains any of these
  build: python3 scripts/build_app_traces.py    # optional: runs only if trace_dir is missing or empty
```

The microworkloads come from `../progs/verify/microworkloads`. The generated traces are committed
under `traces/`, so a fresh clone needs no build step:
- `traces/apps`: 12 MIMDRAM applications plus `apps_info.json` (`scripts/build_app_traces.py`)
- `traces/rearranged/{orig,thesis,sa}`: row-layout variants of the 32 microworkloads
  (`scripts/rearrange_traces.py`)
- `traces/tmr`: the thesis's 32 TMR microworkload traces (only 11 are really triplicated)

A config's `build:` command runs only if its `trace_dir` is missing or empty.

**An experiment** combines one workload with models, mechanisms, seeds and extra parameters:

```yaml
- name: weights                    # output goes to results/auto/weights/
  enabled: true                    # run by default; -e weights runs it even if false
  description: Sensitivity to the SiMRA disturbance weight.
  workload: micro
  model: fixed                     # or  models: [orig, fixed]  to compare several
  seeds: [1337, 7, 42]             # default [0]
  mechanisms:
    - none                         # needed for normalized flips and for the Pareto plot
    - prac                         # the mechanism's full sweep
    - {name: pu_ds, pu_threshold: [4000]}   # replace the sweep for this experiment only
  params: {SiMRA_weight: [50, 100, 200]}    # extra [rowhammer] keys; a list is swept
  pud_clock: true                  # default; false = the old time-based refresh resets
  plots: [pareto, totals_bar]
```

The number of runs is models × params grid × mechanism configs × seeds × traces; check it with
`--dry-run` before a long run.

Each run's ini is `base_ini` with these `[rowhammer]` keys applied in order, later ones winning:
all mitigations off → `time_refresh_resets = false` (PuD clock) → model → `params` → mechanism → `rowhammer_seed`.
Keys missing from `base.ini` are appended, so a new simulator knob needs no ini edit.

Config names are `[<model>-][<param><value>-]<mechanism>[_<short><value>]_s<seed>`, for example
`prac_t20_s1337` or `SiMRA_weight200-pu_ds_t4000_s1337`. The model prefix appears only when an
experiment has several models.

Plot types:

| Plot | Shows |
|---|---|
| `pareto` | residual flips (fraction of `none`) vs. mitigation time, one line per mechanism, error bars over seeds |
| `flips_bar` | flips per trace and config (drawn as a heatmap above 9 configs) |
| `overhead_bar` | mitigation time per trace and config (same rule) |
| `heatmap` | flips, trace × config |
| `totals_bar` | total flips per config; the default when `plots` is missing |

## Output

`results/` is ignored by git.

```
results/auto/
  INDEX.md                       all experiments, with links to the reports
  logs/<timestamp>.log           console log of each run_tests.sh call
  <experiment>/
    runs/<config>/<trace>/       dram.ini, env.txt, params.json, params.sig, gem5.log,
                                 m5out/rh_mitigation.txt, report/
    data.csv                     one row per run
    summary.csv                  one row per config (summed over traces)
    figures/*.png
    REPORT.md                    tables + figures
    manifest.json                experiment snapshot, models, mechanisms, git commits of
                                 hammulator / gem5 / DRAMsim3, ok / skipped / failed runs
```

- `data.csv` has flips, activations, AP/AAP/ACT counts, PuD time, mitigation events, mitigation
  time, overhead %, window resets and double-sided hits for every run.
- `summary.csv` adds `norm_flips` (flips / flips of `none` with the same model, params and seed),
  `zero_flip_traces` and `complete` (whether every trace finished).
- Overhead = mitigation time / PuD time, with
  PuD time = `t_ap`·AP + `t_aap`·AAP + `t_act`·ACT rows and
  mitigation time = PRAC ABOs × (2·blast radius·`t_rc` + `t_abo`) + (SALT rows + PARA refreshes + PU mitigations) × `t_rc`.

## Resuming and re-running

- Finished runs are skipped, so an interrupted or extended experiment just needs the same command again.
  Adding a value to a sweep runs only the new configs.
- Each run stores a signature of its ini keys, trace and PuD window. If the config changes it, the old
  run folder is renamed `<trace>.stale-<unix time>` and the run starts again. `collect` ignores stale
  folders; delete them when they are no longer needed.
- `timing`, `metric`, `plots`, labels and colours are not part of the signature. After changing them,
  use `--post-only`.
- **Rebuilding the simulator is not detected.** After a code change, delete
  `results/auto/<experiment>/runs/` or point `paths.results` to a new folder. `manifest.json`
  records which commits produced the runs.
- Failed runs are listed at the end of the log and in `manifest.json`; the cause is in
  `runs/<config>/<trace>/gem5.log`. Re-running the command retries only the failed runs.

## Common changes

- **New experiment:** append it under `experiments`, check it with `./run_tests.sh -e <name> --dry-run`,
  then run it.
- **New workload:** put the traces in a folder and add an entry under `workloads`.
- **New mechanism:** add an entry under `mechanisms` with its `[rowhammer]` keys. Its enable key must
  default to off in `configs/base.ini`: the workflow switches off only SALT, PRAC, PARA, TRR and PU.
  Its overhead counts only once its refresh counter from `rh_mitigation.txt` is added to `costs()` in
  `scripts/autotest.py`.
- **Separate result tree:** copy the config and change `paths.results`, as `configs/smoke.yaml` does.
