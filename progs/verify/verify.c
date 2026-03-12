#include <stdio.h>
#include "rh_core.h"

int main(int argc, char **argv) {
    rh_init();

    if (argc < 2) {
        printf("Usage: %s <trace_file1> [trace_file2] ...\n", argv[0]);
        return 1;
    }

    for (int i = 1; i < argc; i++) {
        const char *trace_file = argv[i];

        printf("\n============================================\n");
        printf(" Processing trace file: %s\n", trace_file);
        printf("============================================\n\n");

        rh_reset_stats(); // Reset counters for the new trace file
        rh_hammer_from_file(1, trace_file);

        printf("Hammering complete for %s.\n", trace_file);
        printf("Scanning for bit flips...\n");

        rh_collect_bitflips();   
        dump_final_info(trace_file); 
    }

    return 0;
}