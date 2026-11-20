#!/usr/bin/env bash
# One-command PuD-Hammulator test run: simulate -> collect -> plot -> report.
#
#   ./run_tests.sh                              # enabled experiments in configs/experiments.yaml
#   ./run_tests.sh -c configs/smoke.yaml        # another config file
#   ./run_tests.sh -e pareto_micro,weights      # only these experiments (also if disabled)
#   ./run_tests.sh -j 20                        # parallel gem5 processes (default: config)
#   ./run_tests.sh --rebuild                    # rebuild gem5/DRAMsim3/verify first
#   ./run_tests.sh --dry-run                    # print the run matrix only
#   ./run_tests.sh --post-only                  # skip simulation; re-collect, re-plot, re-report
#   ./run_tests.sh --background                 # detach (nohup); follow the log file
#
# Logs: <results>/logs/<timestamp>.log ; results index: <results>/INDEX.md
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$ROOT/configs/experiments.yaml"
EXPS=""
JOBS=""
REBUILD=0
DRY=0
POST=0
BG=0

usage() { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        -c|--config)      CONFIG="$2"; shift 2 ;;
        -e|--experiments) EXPS="$2"; shift 2 ;;
        -j|--jobs)        JOBS="$2"; shift 2 ;;
        --rebuild)        REBUILD=1; shift ;;
        --dry-run)        DRY=1; shift ;;
        --post-only)      POST=1; shift ;;
        --background)     BG=1; shift ;;
        -h|--help)        usage 0 ;;
        *) echo "unknown option: $1" >&2; usage 1 ;;
    esac
done

[[ -f "$CONFIG" ]] || { echo "config not found: $CONFIG" >&2; exit 1; }
CONFIG="$(cd "$(dirname "$CONFIG")" && pwd)/$(basename "$CONFIG")"

# results dir and hammulator path come from the config file
read -r RESULTS HAMM < <(python3 - "$CONFIG" "$ROOT" <<'EOF'
import os, sys, yaml
from pathlib import Path
cfg = yaml.safe_load(open(sys.argv[1]))
root = Path(sys.argv[2])
def p(x):
    q = Path(os.path.expanduser(str(x)))
    return q if q.is_absolute() else root / q
print(p(cfg["paths"]["results"]), p(cfg["paths"]["hammulator"]))
EOF
)

ARGS=(-c "$CONFIG")
[[ -n "$EXPS" ]] && ARGS+=(-e "$EXPS")
[[ -n "$JOBS" ]] && ARGS+=(-j "$JOBS")

if [[ $DRY -eq 1 ]]; then
    exec python3 "$ROOT/scripts/autotest.py" list "${ARGS[@]}"
fi

mkdir -p "$RESULTS/logs"
LOG="$RESULTS/logs/$(date +%Y%m%d-%H%M%S).log"

if [[ $BG -eq 1 ]]; then
    REST=()
    [[ $REBUILD -eq 1 ]] && REST+=(--rebuild)
    [[ $POST -eq 1 ]] && REST+=(--post-only)
    nohup "$0" "${ARGS[@]}" "${REST[@]}" > "$LOG" 2>&1 &
    echo "started in background (pid $!); log: $LOG"
    exit 0
fi

exec > >(tee -a "$LOG") 2>&1
echo "=== PuD-Hammulator tests  $(date '+%F %T')"
echo "config:  $CONFIG"
echo "results: $RESULTS"
echo "log:     $LOG"

if [[ $REBUILD -eq 1 ]]; then
    echo "=== rebuild"
    (cd "$HAMM" && make hammulator && make build/tmp_root/verify)
fi

for f in build/X86/gem5.opt build/tmp_root/verify gem5/configs/example/se.py; do
    [[ -e "$HAMM/$f" ]] || { echo "missing $HAMM/$f (use --rebuild)" >&2; exit 1; }
done

T0=$(date +%s)
if [[ $POST -eq 0 ]]; then
    echo "=== simulate"
    python3 "$ROOT/scripts/autotest.py" run "${ARGS[@]}"
fi
echo "=== collect"
python3 "$ROOT/scripts/autotest.py" collect "${ARGS[@]}"
echo "=== plot"
python3 "$ROOT/scripts/autotest.py" plot "${ARGS[@]}"
echo "=== report"
python3 "$ROOT/scripts/autotest.py" report "${ARGS[@]}"
echo "=== done in $(( $(date +%s) - T0 )) s; index: $RESULTS/INDEX.md"
