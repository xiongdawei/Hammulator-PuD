import re
import sys
import os
import matplotlib.pyplot as plt

def parse_report(path):
    rows = []
    accesses = []
    bitflips = []

    special_names = []
    special_accesses = []
    special_bitflips = []

    with open(path, "r") as f:
        lines = f.readlines()

    regfile_section = False
    special_section = False

    for line in lines:

        # Detect REGFILE section
        if "REG_FILE ROWS" in line:
            regfile_section = True
            special_section = False
            continue

        # Detect SPECIAL rows section
        if "SPECIAL ROWS" in line:
            special_section = True
            regfile_section = False
            continue

        # Parse REGFILE rows
        if regfile_section and line.startswith("Row"):
            m = re.match(
                r"Row\s+(\d+)\s+\|\s+Accesses:\s+(\d+)\s+\|\s+Bitflips:\s+(\d+)",
                line
            )
            if m:
                rows.append(int(m.group(1)))
                accesses.append(int(m.group(2)))
                bitflips.append(int(m.group(3)))

        # Parse SPECIAL rows
        if special_section:
            m = re.match(
                r"(\S+)\s+\|\s+Accesses:\s+(\d+)\s+\|\s+Bitflips:\s+(\d+)",
                line
            )
            if m:
                special_names.append(m.group(1))
                special_accesses.append(int(m.group(2)))
                special_bitflips.append(int(m.group(3)))

    return rows, accesses, bitflips, special_names, special_accesses, special_bitflips


def plot_report(path):
    rows, accesses, bitflips, special_names, special_accesses, special_bitflips = parse_report(path)

    # Base name for saving files
    base = os.path.basename(path).replace(".txt", "")

    # -----------------------------
    # Plot REGFILE Rows
    # -----------------------------
    fig, ax1 = plt.subplots(figsize=(12, 6))

    ax1.set_xlabel("Row index")
    ax1.set_ylabel("Access count", color="tab:blue")
    ax1.plot(rows, accesses, color="tab:blue", label="Access count")
    ax1.tick_params(axis="y", labelcolor="tab:blue")

    ax2 = ax1.twinx()
    ax2.set_ylabel("Bitflips", color="tab:red")
    ax2.scatter(rows, bitflips, color="tab:red", label="Bitflips", s=20)
    ax2.tick_params(axis="y", labelcolor="tab:red")

    plt.title(f"REGFILE Row Accesses vs Bitflips\n{os.path.basename(path)}")
    fig.tight_layout()

    # Save REGFILE plot
    out1 = f"{base}_regfile_plot.png"
    fig.savefig(out1, dpi=300, bbox_inches='tight')
    print(f"[Saved] {out1}")

    plt.show()

    # -----------------------------
    # Plot SPECIAL Rows
    # -----------------------------
    fig2, ax3 = plt.subplots(figsize=(10, 4))
    ax3.bar(special_names, special_accesses, color="tab:blue", alpha=0.7, label="Accesses")
    ax3.scatter(special_names, special_bitflips, color="tab:red", label="Bitflips", zorder=5)

    plt.title(f"Special Rows Accesses & Bitflips\n{os.path.basename(path)}")
    plt.xticks(rotation=45)
    plt.legend()
    plt.tight_layout()

    # Save SPECIAL plot
    out2 = f"{base}_special_rows_plot.png"
    fig2.savefig(out2, dpi=300, bbox_inches='tight')
    print(f"[Saved] {out2}")

    plt.show()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python plot_rh_report.py <final-report.txt>")
        sys.exit(1)

    plot_report(sys.argv[1])
