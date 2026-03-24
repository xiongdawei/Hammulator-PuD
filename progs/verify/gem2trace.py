import os
import re

# Path to your dram_trace directory
folder_path = "/home/daweix3/dram_trace"
save_path = "/home/daweix3/hammulator/progs/verify/trace"

row_to_operand = {
    4: "T0",
    6: "T1",
    8: "T2",
    10: "T3",
    12: "B_DCC0",
    14: "B_DCC0N",
    16: "B_DCC1",
    18: "B_DCC1N",
    20: "B_DCC0N T0",
    22: "B_DCC1N T1",
    24: "T2 T3",
    26: "T0 T3",
    28: "T0 T1 T2",
    30: "T1 T2 T3",
    32: "B_DCC0 T1 T2",
    34: "B_DCC1 T0 T3",
    36: "C0",
    38: "C1",
}

# Regex to match "@PIM_AP <num>" OR "@PIM_AAP <num1> <num2>"
# Group 1 = "AP" or "AAP"
# Group 2 = first number
# Group 3 = second number (optional, only exists for AAP)
pattern = re.compile(r"@PIM_(AP|AAP)\s+(\d+)(?:\s+(\d+))?")

# Iterate over all files in the folder
for filename in os.listdir(folder_path):
    if filename.endswith("row_summary.txt"):
        input_path = os.path.join(folder_path, filename)
        row_numbers = []
        mapped_rows = []

        # Read and extract all row numbers
        with open(input_path, "r") as f:
            content = f.read()

        # Find all matches using finditer to easily grab regex groups
        for match in pattern.finditer(content):
            cmd_type = match.group(1) # "AP" or "AAP"
            row1 = int(match.group(2))
            
            # Map first operand
            mapped1 = row_to_operand.get(row1, str(row1))
            if row1 not in row_to_operand and row1 not in row_numbers:
                row_numbers.append(row1)

            # Handle based on command type
            if cmd_type == "AAP":
                row2 = int(match.group(3))
                mapped2 = row_to_operand.get(row2, str(row2))
                
                # Check for unmapped row2
                if row2 not in row_to_operand and row2 not in row_numbers:
                    row_numbers.append(row2)
                
                # Format: AAP <arg1>, <arg2>
                mapped_rows.append(f"AAP {mapped1}, {mapped2}")
            else:
                # Format: AP <arg1>
                mapped_rows.append(f"AP {mapped1}")

        # Output file name: same base + "-hammulator_trace.txt"
        output_filename = filename.replace("_row_summary.txt", "-hammulator_trace.txt")
        output_path = os.path.join(save_path, output_filename)

        print(f"✅ {filename}: extracted {len(mapped_rows)} rows → {output_filename}")
        print(f"Unmapped row numbers collected: {sorted(row_numbers)}")

        # Write the sorted row_numbers to the top, then the translated trace
        with open(output_path, "w") as out:
            out.write(" ".join(map(str, sorted(row_numbers))) + "\n")
            out.write("\n".join(mapped_rows) + "\n")        

print("\n🎯 All row_summary.txt files processed successfully!")