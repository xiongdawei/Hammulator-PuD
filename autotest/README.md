# autotest: config-driven RowHammer experiments

One YAML file holds every parameter; one command simulates, collects, plots and reports.
All paths in the configs are relative to this directory, so it works from a fresh clone
once the simulator is built.

```bash
cd autotest
./run_tests.sh --rebuild -c configs/smoke.yaml   # build gem5 + DRAMsim3 + verify, 2-min check
./run_tests.sh                                   # enabled experiments in configs/experiments.yaml
./run_tests.sh -e pareto_micro,apps              # selected experiments (also disabled ones)
./run_tests.sh --dry-run                         # print the run matrix, start nothing
./run_tests.sh --post-only                       # re-collect / re-plot / re-report only
./run_tests.sh --background                      # detach; log in results/auto/logs/
```

Requirements: the patched simulator (`gem5-patches/`, `DRAMsim3-patches/` applied, then
`make hammulator && make build/tmp_root/verify` in the repo root), Python 3.8+ with PyYAML,
matplotlib and numpy.

## Config (`configs/experiments.yaml`)

| Section | Contents |
|---|---|
| `paths` | simulator checkout (`..`), base DRAMsim3 ini, results directory |
| `run` | parallel jobs, PuD refresh window (ns), debug log |
| `timing` | ns per AP / AAP / ACT / row refresh / ABO, used for PuD time and overhead |
| `metric` | headline flip count: `sim` (simulator) or `harness` (replay-harness scan) |
| `models` | named sets of physics keys (`base:` inherits another model) |
| `mechanisms` | `set` (fixed keys), `sweep` (lists expanded as a grid), `derive` (computed keys), `label`, `color` |
| `workloads` | trace directory, `include`/`exclude` filters, optional `build` command for generated traces |
| `experiments` | workload x models x mechanisms x seeds x `params` grid, and the `plots` to draw |

A mechanism entry in an experiment can override its sweep, e.g. `{name: prac, prac_threshold: [20]}`.
Generated traces (`traces/apps`, `traces/rearranged`) are built on first use and not committed.

## Output (`results/auto/<experiment>/`)

- `runs/<config>/<trace>/`: dram.ini, env, gem5 log, `m5out/rh_mitigation.txt`, harness report
- `data.csv` (one row per run), `summary.csv` (one row per config)
- `figures/`: `pareto`, `flips_bar`, `overhead_bar`, `heatmap`, `totals_bar`
- `REPORT.md`, `manifest.json` (parameters, git commits, failed runs); `results/auto/INDEX.md`

Finished runs are skipped, so an interrupted batch resumes. A run whose parameters changed
is moved to `*.stale-<time>` and re-run.
