import os

# Define file paths
trace_dir = "trace"
trace_benchmark_dir = "trace_benchmarks"
multu_file = os.path.join(trace_dir, "11_multu-plus-extend_console-hammulator_trace.txt")
add_file = os.path.join(trace_dir, "00_addition-plus-extend_console-hammulator_trace.txt")
output_file = os.path.join(trace_benchmark_dir, "mixed_11multu_00add_10x-hammulator_trace.txt")

def read_trace_file(filepath):
    """Reads a trace file and separates the unmapped row header from the trace body."""
    with open(filepath, 'r') as f:
        lines = [line.strip() for line in f.readlines() if line.strip()]
    
    header = []
    body = []
    
    if not lines:
        return header, body

    # Check if the first line is the unmapped rows header (all pure digits separated by spaces)
    first_line_parts = lines[0].split()
    if all(part.isdigit() for part in first_line_parts):
        header = [int(p) for p in first_line_parts]
        body = lines[1:] # Everything else is the actual trace
    else:
        body = lines
        
    return header, body

# 1. Read both source files
print(f"Reading: {multu_file}")
multu_header, multu_body = read_trace_file(multu_file)

print(f"Reading: {add_file}")
add_header, add_body = read_trace_file(add_file)

# 2. Combine and sort the headers (keeping only unique unmapped row numbers)
combined_header = sorted(list(set(multu_header + add_header)))

# 3. Create the repeating sequence (11_multu followed by 00_addition, 10 times)
combined_body = []
for i in range(10):
    combined_body.extend(multu_body)
    combined_body.extend(add_body)

# 4. Write to the new output file
with open(output_file, 'a') as f:
    # Write the combined header at the very top (if it exists)
    if combined_header:
        f.write(" ".join(map(str, combined_header)) + "\n")
    
    # Write the combined trace body
    f.write("\n".join(combined_body) + "\n")

print("\n🎯 Mixed trace generation complete!")
print(f"✅ Saved to: {output_file}")
print(f"📊 Unique unmapped rows in header: {len(combined_header)}")
print(f"🔄 Total trace operations written: {len(combined_body)}")