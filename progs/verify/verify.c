#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <gem5/m5ops.h>
#include "rh_core.h"

int main(int argc, char **argv) {
    rh_init();

    if (argc < 2) {
        printf("Usage: %s [--target-events=N] <trace_file1> [trace_file2] ...\n",
               argv[0]);
        return 1;
    }

    long target_events = 0;
    int arg_start = 1;
    if (strncmp(argv[1], "--target-events=", 16) == 0) {
        target_events = strtol(argv[1] + 16, NULL, 10);
        arg_start = 2;
    }

    if (arg_start >= argc) {
        printf("Usage: %s [--target-events=N] <trace_file1> [trace_file2] ...\n",
               argv[0]);
        return 1;
    }

    for (int i = arg_start; i < argc; i++) {
        const char *trace_file = argv[i];

        printf("\n============================================\n");
        printf(" Processing trace file: %s\n", trace_file);
        printf("============================================\n\n");

        rh_reset_stats();
        // prac_abo is not present in the current config files, so the
        // verify flow uses the trace filename as the hammer/skip signal.
        // TMR traces are replayed only when RH_RUN_TMR is set (PuD-paper).
        const char *run_tmr = getenv("RH_RUN_TMR");
        if (strstr(trace_file, "tmr") != NULL &&
            !(run_tmr && run_tmr[0] != '\0' && run_tmr[0] != '0')) {
            continue;
        }
        rh_hammer(trace_file, target_events);

        printf("Scanning for bit flips...\n");

        rh_collect_bitflips();

        dump_final_info(trace_file);
    }

    return 0;
}
