import os
import re

# Path to your dram_trace directory
folder_path = "/home/daweix3/dram_trace"
save_path = "/home/daweix3/hammulator/progs/verify/trace"

row_to_operand = {
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

# Regex to match "row <number>"
pattern = re.compile(r"@PIM_AP\s+(\d+)")


# Iterate over all files in the folder
for filename in os.listdir(folder_path):
    row_numbers = []
    if filename.endswith("row_summary.txt"):
        input_path = os.path.join(folder_path, filename)

        # Read and extract all row numbers
        with open(input_path, "r") as f:
            content = f.read()
            rows = pattern.findall(content)

        # for each row number, map to operand name if exists
        mapped_rows = []
        for row in rows:
            row_num = int(row)
            if row_num in row_to_operand:
                mapped_rows.append(row_to_operand[row_num])
            else:
                mapped_rows.append(f"{row_num}")
                #print(row_num)
                if row_num not in row_numbers:
                    row_numbers.append(row_num)
        
        rows = mapped_rows


        # Output file name: same base + "_hammulator_trace.txt"
        output_filename = filename.replace("_row_summary.txt", "-hammulator_trace.txt")
        output_path = os.path.join(save_path, output_filename)

        # Write extracted row numbers line by line


        print(f"✅ {filename}: extracted {len(rows)} rows → {output_filename}")
        print(sorted(row_numbers))
        # write the sorted row_numbers to the top of the file

        with open(output_path, "w") as out:
            out.write(" ".join(map(str, sorted(row_numbers))) + "\n")
            out.write("\n".join(rows))        

print("\n🎯 All row_summary.txt files processed successfully!")
