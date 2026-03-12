import os
import numpy as np
import matplotlib.pyplot as plt

def get_adjacent_rows(row_id):
    """Get adjacent row IDs"""
    try:
        r_int = int(row_id)
        return str(r_int - 1), str(r_int + 1)
    except ValueError:
        return f"{row_id}_prev", f"{row_id}_next"

def count_total_accesses(input_file):
    """Counts the total number of individual row accesses in the trace file."""
    total_count = 0
    with open(input_file, 'r') as f:
        for line in f:
            tokens = line.strip().split()
            total_count += len(tokens)
    return total_count

def streaming_space_saving_mitigation(input_file, output_file, threshold=100, max_table_size=16):
    """RowHammer defense based on the Space-Saving streaming algorithm."""
    tracker_table = {}
    new_refresh_count = 0
    
    with open(input_file, 'r') as fin, open(output_file, 'w') as fout:
        for line in fin:
            tokens = line.strip().split()
            for row_id in tokens:
                fout.write(f"{row_id}\n") 
                
                if row_id in tracker_table:
                    tracker_table[row_id] += 1
                else:
                    if len(tracker_table) < max_table_size:
                        tracker_table[row_id] = 1
                    else:
                        min_row = min(tracker_table, key=tracker_table.get)
                        min_count = tracker_table[min_row]
                        del tracker_table[min_row]
                        tracker_table[row_id] = min_count + 1
                
                if tracker_table[row_id] >= threshold:
                    adj_1, adj_2 = get_adjacent_rows(row_id)
                    fout.write(f"REFRESH {adj_1}\n")
                    fout.write(f"REFRESH {adj_2}\n")
                    new_refresh_count += 2
                    del tracker_table[row_id]
                    
    return new_refresh_count

# ================= Research Study Automation (Heatmap with Overhead %) =================
if __name__ == "__main__":
    # Your trace file path
    input_trace = "/home/daweix3/hammulator/progs/verify/trace_benchmarks/mixed_11multu_00add_10x-hammulator_trace.txt"
    temp_output_trace = "temp_trace.txt"
    
    # Parameters to sweep
    thresholds = [1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000]
    table_sizes = [16, 32, 64, 128]
    
    if os.path.exists(input_trace):
        print("🚀 Starting RowHammer Parameter Sweep (Overhead Percentage Mode)...\n")
        
        # Step 1: Count total baseline accesses
        total_accesses = count_total_accesses(input_trace)
        print(f"Total baseline accesses in trace: {total_accesses:,}")
        print("-" * 50)
        
        # Create a 2D numpy array to store the heatmap percentage data
        results_grid = np.zeros((len(table_sizes), len(thresholds)))
        
        # Step 2: Run the simulations
        for i, ts in enumerate(table_sizes):
            for j, th in enumerate(thresholds):
                print(f"Running -> Table Size: {ts:3} | Threshold: {th} ... ", end="")
                
                refresh_count = streaming_space_saving_mitigation(
                    input_trace, 
                    temp_output_trace, 
                    threshold=th, 
                    max_table_size=ts
                )
                
                # Calculate Overhead Percentage
                overhead_pct = (refresh_count / total_accesses) * 100
                results_grid[i, j] = overhead_pct
                
                print(f"Added REFRESH: {refresh_count} ({overhead_pct:.2f}%)")
        
        # Clean up the temporary trace file
        if os.path.exists(temp_output_trace):
            os.remove(temp_output_trace)
            
        print("\n📊 Simulation complete! Drawing the Heatmap...")
        
        # ================= Step 3: Draw the Heatmap =================
        fig, ax = plt.subplots(figsize=(9, 7))
        
        # Use a colormap ('Reds') where darker red means higher percentage overhead
        cax = ax.imshow(results_grid, cmap='Reds', aspect='auto')
        
        # Set the X and Y axis ticks and labels
        ax.set_xticks(np.arange(len(thresholds)))
        ax.set_yticks(np.arange(len(table_sizes)))
        ax.set_xticklabels(thresholds)
        ax.set_yticklabels(table_sizes)
        
        ax.set_xlabel('RowHammer Threshold (Activation Count)', fontsize=12, fontweight='bold')
        ax.set_ylabel('Hardware Table Size (Entries)', fontsize=12, fontweight='bold')
        ax.set_title('RowHammer Defense Overhead Percentage (%)', fontsize=14, pad=15)
        
        # Loop over data dimensions and create text annotations inside the grid cells
        max_val = np.max(results_grid)
        for i in range(len(table_sizes)):
            for j in range(len(thresholds)):
                value = results_grid[i, j]
                # Determine text color based on background darkness for readability
                text_color = "white" if value > (max_val / 2) else "black"
                # Display the percentage with 2 decimal places
                text = ax.text(j, i, f"{value:.2f}%",
                               ha="center", va="center", color=text_color, fontsize=11, fontweight='bold')
        
        # Add a color bar legend on the side
        cbar = fig.colorbar(cax)
        cbar.set_label('Overhead Percentage (%)', rotation=270, labelpad=20)
        
        plt.tight_layout()
        
        # Save and display
        graph_filename = "rh_overhead_percentage_heatmap.png"
        plt.savefig(graph_filename, dpi=300)
        print(f"✅ Heatmap successfully saved as: {graph_filename}")
        
    else:
        print(f"❌ Error: Cannot find the trace file at {input_trace}")