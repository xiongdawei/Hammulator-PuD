import re
import sys
import os
import matplotlib.pyplot as plt
import numpy as np

def parse_report(path):
    rows = []
    accesses = []
    bitflips = []

    special_names = []
    special_access = []
    special_bitflip = []

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

        # Detect SPECIAL section
        if "SPECIAL ROWS" in line:
            special_section = True
            regfile_section = False
            continue

        # REGFILE parsing
        if regfile_section and line.startswith("Row"):
            m = re.match(
                r"Row\s+(\d+)\s+\|\s+Accesses:\s+(\d+)\s+\|\s+Bitflips:\s+(\d+)",
                line
            )
            if m:
                rows.append(int(m.group(1)))
                accesses.append(int(m.group(2)))
                bitflips.append(int(m.group(3)))

        # SPECIAL rows parsing
        if special_section:
            m = re.match(
                r"(\S+)\s+\|\s+Accesses:\s+(\d+)\s+\|\s+Bitflips:\s+(\d+)",
                line
            )
            if m:
                special_names.append(m.group(1))
                special_access.append(int(m.group(2)))
                special_bitflip.append(int(m.group(3)))

    return rows, accesses, bitflips, special_names, special_access, special_bitflip


def plot_double_bar(path):
    rows, accesses, bitflips, special_names, special_access, special_bitflip = parse_report(path)

    # Compute new indices for special rows:
    max_reg_index = rows[-1]
    special_indices = list(range(max_reg_index + 1, max_reg_index + 1 + len(special_names)))

    # Append special rows to the end
    all_indices = rows + special_indices
    all_access = accesses + special_access
    all_bitflips = bitflips + special_bitflip

    # Auto-scale bitflips to be visible
    max_access = max(all_access)
    max_bf = max(all_bitflips)
    scale_factor = (0.4 * max_access / max_bf) if max_bf > 0 else 1.0

    print(f"[info] bitflip scale factor = {scale_factor:.2f}")

    scaled_bitflips = [b * scale_factor for b in all_bitflips]

    x = np.arange(len(all_indices))
    width = 0.45

    fig, ax = plt.subplots(figsize=(16, 6))

    # Access bars
    bars_access = ax.bar(x - width/2, all_access, width, label="Accesses", color='tab:blue')

    # Bitflip bars (scaled)
    bars_bitflip = ax.bar(
        x + width/2,
        scaled_bitflips,
        width,
        label=f"Bitflips (×{scale_factor:.1f})",
        color='tab:red'
    )

    # Labels and ticks
    ax.set_xlabel("Row Index (REGFILE + SPECIAL appended)")
    ax.set_ylabel("Value (bitflips scaled)")
    ax.set_title(f"Double Bar Chart: Accesses & Bitflips\n{os.path.basename(path)}")
    ax.set_xticks(x)

    # X tick labels
    tick_labels = rows + special_names
    ax.set_xticklabels(tick_labels, rotation=90, fontsize=7)

    ax.legend()

    # Add value labels above bars
    def add_labels(bars, values):
        for bar, val in zip(bars, values):
            height = bar.get_height()
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                height,
                f"{val}",
                ha='center',
                va='bottom',
                fontsize=6
            )

    add_labels(bars_access, all_access)
    add_labels(bars_bitflip, all_bitflips)

    plt.tight_layout()

    # Save output
    base = os.path.basename(path).replace(".txt", "")
    out = f"{base}_double_bar_full.png"
    plt.savefig(out, dpi=300, bbox_inches='tight')
    print(f"[Saved] {out}")

    plt.show()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python plot_double_bar_full.py <final-report.txt>")
        sys.exit(1)

    plot_double_bar(sys.argv[1])
