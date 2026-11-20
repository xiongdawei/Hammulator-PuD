#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPORT_DIR="$SCRIPT_DIR/report"
GRAPH_DIR="$SCRIPT_DIR/graph"
PLOT_SCRIPT="$SCRIPT_DIR/plot_reduce_bar.py"

usage() {
    cat <<EOF
Usage: $(basename "$0") [workload ...]

Generate report graphs from files under:
  $REPORT_DIR

Examples:
  $(basename "$0")
  $(basename "$0") addition multu
  $(basename "$0") addition,multu

If no workload is provided, graphs are generated for every report file.
Each workload is treated as a case-insensitive substring match against the
report filename.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

if [[ ! -d "$REPORT_DIR" ]]; then
    echo "Report directory not found: $REPORT_DIR" >&2
    exit 1
fi

mkdir -p "$GRAPH_DIR"

declare -a filters=()
declare -a matched_files=()
declare -A seen=()

if [[ "$#" -eq 0 ]]; then
    while IFS= read -r -d '' file; do
        matched_files+=("$file")
    done < <(find "$REPORT_DIR" -maxdepth 1 -type f -name '*-final-report.txt' -print0 | sort -z)
else
    for arg in "$@"; do
        IFS=',' read -r -a split_filters <<< "$arg"
        for filter in "${split_filters[@]}"; do
            [[ -n "$filter" ]] && filters+=("$filter")
        done
    done

    shopt -s nullglob nocasematch
    for filter in "${filters[@]}"; do
        current_matches=("$REPORT_DIR"/*"$filter"*"-final-report.txt")
        if [[ "${#current_matches[@]}" -eq 0 ]]; then
            echo "No report matched workload filter: $filter" >&2
            continue
        fi

        for file in "${current_matches[@]}"; do
            if [[ -f "$file" && -z "${seen["$file"]+x}" ]]; then
                matched_files+=("$file")
                seen["$file"]=1
            fi
        done
    done
    shopt -u nullglob nocasematch
fi

if [[ "${#matched_files[@]}" -eq 0 ]]; then
    echo "No report files selected from $REPORT_DIR" >&2
    echo "Available reports:" >&2
    find "$REPORT_DIR" -maxdepth 1 -type f -name '*-final-report.txt' -printf '  %f\n' | sort >&2
    exit 1
fi

echo "Generating graphs in: $GRAPH_DIR"
for file in "${matched_files[@]}"; do
    echo "Running python3 $(basename "$PLOT_SCRIPT") on: $(basename "$file")"
    (
        cd "$SCRIPT_DIR"
        MPLBACKEND="${MPLBACKEND:-Agg}" python3 "$PLOT_SCRIPT" "$file"
    )
done
