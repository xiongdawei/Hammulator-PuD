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
        # REGFILE section marker
        if "REG_FILE ROWS" in line:
            regfile_section = True
            special_section = False
            continue

        # SPECIAL rows section marker
        if "SPECIAL ROWS" in line:
            special_section = True
            regfile_section = False
            continue

        # REGFILE rows
        if regfile_section and line.startswith("Row"):
            m = re.match(r"Row\s+(\d+)\s+\|\s+Accesses:\s+(\d+)\s+\|\s+Bitflips:\s+(\d+)", line)
            if m:
                rows.append(int(m.group(1)))
                accesses.append(int(m.group(2)))
                bitflips.append(int(m.group(3)))

        # SPECIAL rows
        if special_section:
            m = re.match(r"(\S+)\s+\|\s+Accesses:\s+(\d+)\s+\|\s+Bitflips:\s+(\d+)", line)
            if m:
                special_names.append(m.group(1))
                special_access.append(int(m.group(2)))
                special_bitflip.append(int(m.group(3)))

    return rows, accesses, bitflips, special_names, special_access, special_bitflip


def find_intervals(values):
    """Given a list of integers, return consecutive ranges."""
    if not values:
        return []
    intervals = []
    start = prev = values[0]

    for v in values[1:]:
        if v == prev + 1:
            prev = v
        else:
            intervals.append((start, prev))
            start = prev = v
    intervals.append((start, prev))
    return intervals


def plot_double_bar(path):

    # Parse input file
    rows, accesses, bitflips, s_names, s_acc, s_bf = parse_report(path)

    # SPECIAL rows appended
    last_row_idx = rows[-1] if rows else -1
    s_indices = list(range(last_row_idx + 1, last_row_idx + 1 + len(s_names)))

    # Combine REGFILE + SPECIAL
    all_labels = rows + s_names
    all_indices = rows + s_indices
    all_access = accesses + s_acc
    all_bitflip = bitflips + s_bf

    # Filter (0,0) rows
    ignored = []
    kept_labels = []
    kept_indices = []
    kept_accesses = []
    kept_bitflips = []

    for lbl, idx, acc, bf in zip(all_labels, all_indices, all_access, all_bitflip):
        if acc == 0 and bf == 0:
            ignored.append(idx)
            continue
        kept_labels.append(lbl)
        kept_indices.append(idx)
        kept_accesses.append(acc)
        kept_bitflips.append(bf)

    # Print ignored intervals
    intervals = find_intervals(ignored)
    print("\nIgnored row intervals (Access=0 AND Bitflip=0):")
    if not intervals:
        print("  None")
    else:
        for a, b in intervals:
            if a == b:
                print(f"  {a}")
            else:
                print(f"  {a}–{b}")
    print()

    # ----------------- PLOTTING -----------------
    x = np.arange(len(kept_labels))
    width = 0.45

    fig, ax1 = plt.subplots(figsize=(16, 6))

    # Left Y-axis → Accesses
    bars_access = ax1.bar(
        x - width/2, kept_accesses, width,
        label="Accesses", color="tab:blue"
    )
    ax1.set_ylabel("Access Count", color="tab:blue")
    ax1.tick_params(axis='y', labelcolor='tab:blue')

    # Right Y-axis → Bitflips
    ax2 = ax1.twinx()
    bars_bitflip = ax2.bar(
        x + width/2, kept_bitflips, width,
        label="Bitflips", color="tab:red"
    )
    ax2.set_ylabel("Bitflips", color="tab:red")
    ax2.tick_params(axis='y', labelcolor='tab:red')

    # X-axis
    ax1.set_xlabel("Row Index")
    ax1.set_xticks(x)
    ax1.set_xticklabels(kept_labels, rotation=90, fontsize=7)

    # get ride of the -final-report.txt in the path
    plt.title(os.path.basename(path).replace("-final-report.txt", ""))

    # Add numeric labels above bars
    def add_labels(ax, bars, values, color):
        for bar, val in zip(bars, values):
            height = bar.get_height()
            ax.text(
                bar.get_x() + bar.get_width()/2, height,
                f"{val}",
                ha="center", va="bottom", fontsize=6, color=color
            )

    add_labels(ax1, bars_access, kept_accesses, "tab:blue")
    add_labels(ax2, bars_bitflip, kept_bitflips, "tab:red")

    fig.tight_layout()

    # Save
    base = os.path.basename(path).replace(".txt", "")
    out = os.path.join("graph", f"{base}_access_pattern_bitflips.png")
    plt.savefig(out, dpi=300, bbox_inches="tight")
    print(f"[Saved] {out}\n")

    plt.show()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python plot_double_bar_two_axes.py <final-report.txt>")
        sys.exit(1)

    plot_double_bar(sys.argv[1])
