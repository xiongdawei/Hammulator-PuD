import os
import re

# Define file paths
trace_dir = "trace"
trace_benchmark_dir = "trace_benchmarks"
multu_file = os.path.join(trace_dir, "11_multu-plus_console-hammulator_trace.txt")
add_file = os.path.join(trace_dir, "00_addition-plus_console-hammulator_trace.txt")
output_file = os.path.join(trace_benchmark_dir, "mixed_11multu_00add_10x-hammulator_trace.txt")

def read_trace_file(filepath):
    """Reads a trace file, separating the unmapped row header from the AP/AAP trace body."""
    if not os.path.exists(filepath):
        print(f"⚠️ Warning: File not found -> {filepath}")
        return [], []

    with open(filepath, 'r') as f:
        lines = [line.strip() for line in f.readlines() if line.strip()]
    
    if not lines:
        return [], []

    # In the new format, the first line is the unmapped rows. 
    # We check if it starts with an operation command to be safe.
    first_line = lines[0]
    if not first_line.startswith(("AP", "AAP")):
        # Extract all standalone numbers from the header line (ignoring spaces/commas safely)
        header = [int(x) for x in re.findall(r'\b\d+\b', first_line)]
        body = lines[1:]
    else:
        # If there is no header and it jumps straight to commands
        header = []
        body = lines
        
    return header, body

# 1. Ensure output directory exists
os.makedirs(trace_benchmark_dir, exist_ok=True)

# 2. Read both source files
print(f"Reading: {multu_file}")
multu_header, multu_body = read_trace_file(multu_file)

print(f"Reading: {add_file}")
add_header, add_body = read_trace_file(add_file)

# 3. Combine and sort the headers (keeping only unique unmapped row numbers)
combined_header = sorted(list(set(multu_header + add_header)))

# 4. Create the repeating sequence (11_multu followed by 00_addition, 10 times)
combined_body = []
for i in range(10):
    combined_body.extend(multu_body)
    combined_body.extend(add_body)

# 5. Count the operations for validation
ap_count = sum(1 for line in combined_body if line.startswith("AP "))
aap_count = sum(1 for line in combined_body if line.startswith("AAP "))

# 6. Write to the new output file (Using 'w' to overwrite instead of 'a' to append)
with open(output_file, 'w') as f:
    # Write the combined header at the very top
    if combined_header:
        f.write(" ".join(map(str, combined_header)) + "\n")
    
    # Write the combined trace body
    if combined_body:
        f.write("\n".join(combined_body) + "\n")

print("\n🎯 Mixed trace generation complete!")
print(f"✅ Saved to: {output_file}")
print(f"📊 Unique unmapped rows in header: {len(combined_header)}")
print(f"🔄 Total trace operations written: {len(combined_body)}")
print(f"   ↳ AP Operations:  {ap_count}")
print(f"   ↳ AAP Operations: {aap_count}")