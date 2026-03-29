#!/bin/bash

# grab_trace.sh - Copy MIMDRAM trace files from Docker container to PIM_trace folder
# 
# CONFIGURATION: Edit these variables to specify which benchmarks to grab
# =====================================================================

# Define benchmarks to grab as pairs of (TYPE, NAME)
# Format: BENCHMARKS=("TYPE1" "NAME1" "TYPE2" "NAME2" ...)
# Supported types: polybench, spec, faiss

BENCHMARKS=(
    "polybench" "3mm"
    "polybench" "2mm"  
    "polybench" "gramschmidt"
    "polybench" "covariance"
    # "faiss" "your_benchmark"
)

# =====================================================================

set -u  # Exit on undefined variables, but not on command failures

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Check if benchmarks array is empty
if [ ${#BENCHMARKS[@]} -eq 0 ]; then
    echo "Error: BENCHMARKS array is empty. Please configure benchmarks in the script."
    exit 1
fi

# Check if Docker container is running
DOCKER_CONTAINER=$(docker ps --filter "name=MIMDRAM" --format "{{.ID}}" 2>/dev/null | head -1)

if [ -z "$DOCKER_CONTAINER" ]; then
    echo "Error: MIMDRAM Docker container not found or not running"
    echo "Make sure the MIMDRAM container is running with: docker start MIMDRAM"
    exit 1
fi

echo "Docker container ID: $DOCKER_CONTAINER"
echo "Found ${#BENCHMARKS[@]} benchmarks to process (${#BENCHMARKS[@]} / 2 pairs)"
echo ""

# Process benchmarks
TOTAL_BENCHMARKS=$((${#BENCHMARKS[@]} / 2))
SUCCESSFUL=0
FAILED=0

for ((i = 0; i < ${#BENCHMARKS[@]}; i += 2)); do
    BENCHMARK_TYPE="${BENCHMARKS[$i]}"
    BENCHMARK_NAME="${BENCHMARKS[$((i + 1))]}"
    BENCHMARK_NUM=$((i / 2 + 1))
    
    echo "[$BENCHMARK_NUM/$TOTAL_BENCHMARKS] Processing: $BENCHMARK_TYPE/$BENCHMARK_NAME"
    
    DEST_DIR="$SCRIPT_DIR/$BENCHMARK_TYPE"
    
    # Construct source paths based on benchmark type
    SOURCE_PATHS=()
    case "$BENCHMARK_TYPE" in
        polybench)
            # PolyBench benchmarks are organized across several top-level categories.
            SOURCE_PATHS=(
                "/root/benchmarks/PolyBenchC-4.2.1/linear-algebra/blas/$BENCHMARK_NAME/m5out/MIMDRAM_$BENCHMARK_NAME.log"
                "/root/benchmarks/PolyBenchC-4.2.1/linear-algebra/kernels/$BENCHMARK_NAME/m5out/MIMDRAM_$BENCHMARK_NAME.log"
                "/root/benchmarks/PolyBenchC-4.2.1/linear-algebra/solvers/$BENCHMARK_NAME/m5out/MIMDRAM_$BENCHMARK_NAME.log"
                "/root/benchmarks/PolyBenchC-4.2.1/medley/$BENCHMARK_NAME/m5out/MIMDRAM_$BENCHMARK_NAME.log"
                "/root/benchmarks/PolyBenchC-4.2.1/stencils/$BENCHMARK_NAME/m5out/MIMDRAM_$BENCHMARK_NAME.log"
                "/root/benchmarks/PolyBenchC-4.2.1/datamining/$BENCHMARK_NAME/m5out/MIMDRAM_$BENCHMARK_NAME.log"
            )
            ;;
        spec)
            SOURCE_PATHS=(
                "/root/benchmarks/SPEC/$BENCHMARK_NAME/m5out/MIMDRAM_$BENCHMARK_NAME.log"
            )
            ;;
        faiss)
            SOURCE_PATHS=(
                "/root/benchmarks/faiss/$BENCHMARK_NAME/m5out/MIMDRAM_$BENCHMARK_NAME.log"
            )
            ;;
        *)
            echo "✗ Error: Unknown benchmark type '$BENCHMARK_TYPE'"
            ((FAILED++))
            continue
            ;;
    esac
    
    # Try to find the file in Docker container
    SOURCE_FILE=""
    for path in "${SOURCE_PATHS[@]}"; do
        if docker exec "$DOCKER_CONTAINER" test -f "$path" 2>/dev/null; then
            SOURCE_FILE="$path"
            break
        fi
    done
    
    if [ -z "$SOURCE_FILE" ]; then
        echo "✗ Error: Could not find trace file for '$BENCHMARK_NAME'"
        ((FAILED++))
        continue
    fi
    
    # Create destination directory
    mkdir -p "$DEST_DIR"
    
    # Copy file from Docker container to host
    OUTPUT_FILE="$DEST_DIR/MIMDRAM_$BENCHMARK_NAME.log"
    
    if docker cp "$DOCKER_CONTAINER:$SOURCE_FILE" "$OUTPUT_FILE" 2>/dev/null; then
        if [ -f "$OUTPUT_FILE" ]; then
            FILE_SIZE=$(du -h "$OUTPUT_FILE" | cut -f1)
            echo "✓ Successfully copied ($FILE_SIZE)"
            ((SUCCESSFUL++))
        else
            echo "✗ Error: File was not copied correctly"
            ((FAILED++))
        fi
    else
        echo "✗ Error: Failed to copy file from Docker"
        ((FAILED++))
    fi
done

# Summary
echo ""
echo "=========================================="
echo "Summary:"
echo "  Successful: $SUCCESSFUL/$TOTAL_BENCHMARKS"
echo "  Failed: $FAILED/$TOTAL_BENCHMARKS"
echo "=========================================="

if [ $FAILED -gt 0 ]; then
    exit 1
fi
