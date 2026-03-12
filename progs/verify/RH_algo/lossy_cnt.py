import collections

def process_rowhammer_trace(trace_data, threshold, error_tolerance):
    """
    trace_data: List of row IDs from the DRAM access pattern.
    threshold: The support threshold (e.g., 5 activations).
    error_tolerance: Maximum error (e.g., 1).
    """
    # Width of the bucket (window)
    w = int(1 / error_tolerance)
    
    # Storage for (row_id: [count, delta])
    # delta is the maximum possible error for this entry
    counters = {}
    current_bucket = 1
    processed_count = 0
    
    output_trace = []

    for row in trace_data:
        processed_count += 1
        output_trace.append(f"ACT {row}")
        
        # 1. Update/Add Row to Counters
        if row in counters:
            counters[row][0] += 1
        else:
            counters[row] = [1, current_bucket - 1]
            
        # 2. Check for Rowhammer Threshold
        # If the count + delta exceeds threshold, trigger mitigation
        if counters[row][0] >= threshold:
            # Targeted Refresh to adjacent rows
            output_trace.append(f"--- REFRESH Neighbor {row-1} ---")
            output_trace.append(f"--- REFRESH Neighbor {row+1} ---")
            # Reset counter after mitigation to prevent infinite looping
            counters[row][0] = 0 

        # 3. Bucket Cleanup (End of Window)
        if processed_count % w == 0:
            keys_to_delete = []
            for r_id, (count, delta) in counters.items():
                if count + delta <= current_bucket:
                    keys_to_delete.append(r_id)
            
            for k in keys_to_delete:
                del counters[k]
                
            current_bucket += 1

    return output_trace

# --- Data Preparation from your Trace ---
# Extracting the raw row numbers from your provided trace snippet
raw_trace = [
    6, 40, 57, 73, 6, 41, 58, 74, 6, 42, 59, 75, 
    6, 44, 60, 76, 6, 45, 61, 77, 6, 46, 62, 78,
    6, 47, 63, 79, 6, 48, 64, 80, 6, 49, 65, 81,
    6, 50, 66, 82, 6, 51, 67, 83, 6, 52, 68, 84,
    6, 53, 69, 85, 6, 54, 70, 86, 6, 55, 71, 87,
    6, 56, 72, 88
]

# Set parameters: Trigger refresh if a row appears ~5 times
# with a bucket size of 10 accesses.
mitigated_trace = process_rowhammer_trace(raw_trace, threshold=500, error_tolerance=0.1)

# Display first few lines of the result
for line in mitigated_trace[:20]:
    print(line)