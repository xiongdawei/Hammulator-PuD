#!/bin/bash

REPORT_DIR="report"

for file in "$REPORT_DIR"/*; do
    if [ -f "$file" ]; then
        echo "Running python3 draw.py on: $file"
        python3 plot_reduce_bar.py "$file"
    fi
done
