#include "rh_core.h"
#include "libswapcpu.h"

#include <assert.h>
#include <ctype.h>
#include <fcntl.h>
#include <libgen.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <gem5/m5ops.h>

#define SET_MODE_NORMAL    m5_work_begin(0, 0)
#define SET_MODE_COMRA     m5_work_begin(1, 0)
#define SET_MODE_SIMRA     m5_work_begin(2, 0)
#define SET_MODE_RESET     m5_work_begin(3, 0)
#define SET_TRACE_BASE_ADDR(addr) m5_work_begin(4, (uint64_t)(addr))
#define CLEAR_TRACE_ROWS() m5_work_begin(5, 0)
#define ADD_TRACE_ROW_BASE(addr) m5_work_begin(6, (uint64_t)(addr))

#define PAGE_SIZE 4096
#define D_ROW_SIZE 300
#define C_ROW_SIZE 2
#define B_ROW_SIZE 8
#define R_ROW_SIZE 3
#define SPECIAL_ROW_COUNT (C_ROW_SIZE + B_ROW_SIZE + R_ROW_SIZE)
#define DIST (1 << (6 + 7 + 2))  // 32KB row size
#define MAX_ROWS_PER_LINE 6
#define tAP 46
#define tAAP 60
#define tREF 46

typedef struct {
    uint32_t offsets[MAX_ROWS_PER_LINE];
    int num_rows;
    int command_type; 
} hammer_event_t;

const int rowsize = 2 * PAGE_SIZE;
const int page_size = PAGE_SIZE;

extern int syscall_emulation;

const char* special_row_sequence[SPECIAL_ROW_COUNT] = {
    "C0", "C1",
    "T0", "T1", "T2", "T3",
    "B_DCC0", "B_DCC0N", "B_DCC1", "B_DCC1N",
    "R0", "R1", "R2"
};

static uint8_t *dram;
static uint8_t *reg_file[D_ROW_SIZE];
static uint8_t *C0, *C1, *T0, *T1, *T2, *T3, *B_DCC0, *B_DCC0N, *B_DCC1, *B_DCC1N;
static uint8_t *R0, *R1, *R2;

static uint64_t regfile_access_count[D_ROW_SIZE];
static uint64_t special_access_count[SPECIAL_ROW_COUNT];
static int regfile_bitflips[D_ROW_SIZE];
static int special_bitflips[SPECIAL_ROW_COUNT];
static int refresh_count[D_ROW_SIZE + SPECIAL_ROW_COUNT];

static uint64_t total_ap_count = 0;
static uint64_t total_aap_count = 0;
static uint64_t total_refresh_count = 0;

uint64_t co = 0; 

static void reset_dram_contents(void) {
    if (!dram) {
        return;
    }

    SET_MODE_RESET;
    size_t word_count = (size_t)(DIST * (D_ROW_SIZE + SPECIAL_ROW_COUNT)) / sizeof(uint64_t);
    uint64_t *words = (uint64_t *)dram;
    for (size_t i = 0; i < word_count; i++) {
        words[i] = co;
    }
    SET_MODE_NORMAL;
}

static inline void flushaccess(void *p) {
    asm volatile("clflush 0(%0)\n"
                 "movq (%0), %%rax\n" : : "c"(p) : "rax");
}

static inline int popcount(uint64_t p) {
    int cnt;
    asm volatile("popcnt %%rcx, %%rax\n" : "=a"(cnt) : "c"(p) :);
    return cnt;
}

static uint64_t frame_number_from_pagemap(uint64_t value) {
    return value & ((1ULL << 54) - 1);
}

static uint64_t get_host_physical_addr(uintptr_t virtual_addr) {
    int fd = open("/proc/self/pagemap", O_RDONLY);
    assert(fd >= 0);
    off_t pos = lseek(fd, (virtual_addr / page_size) * 8, SEEK_SET);
    assert(pos >= 0);
    uint64_t value;
    int got = read(fd, &value, 8);
    assert(got == 8);
    close(fd);
    assert(value & (1ULL << 63));
    return (frame_number_from_pagemap(value) * page_size) | (virtual_addr & (page_size - 1));
}

static uint64_t get_actual_physical_addr(uintptr_t virtual_addr) {
    uint64_t translated = m5_virttophys((uint64_t)virtual_addr);
    if (translated != 0) {
        return translated;
    }

    if (!syscall_emulation) {
        return get_host_physical_addr(virtual_addr);
    }

    return 0;
}

static uint64_t get_row_base_8k(uint64_t physical_addr) {
    return physical_addr & ~((uint64_t)rowsize - 1);
}

static void register_trace_row(uint8_t *ptr) {
    uint64_t physical_addr = get_actual_physical_addr((uintptr_t)ptr);
    if (physical_addr != 0) {
        ADD_TRACE_ROW_BASE(get_row_base_8k(physical_addr));
    }
}

static void print_address(uint8_t *a) {
    uint64_t physical_addr = get_actual_physical_addr((uintptr_t)a);

    printf("virtual: 0x%016lx", (uint64_t)a);
    if (physical_addr != 0) {
        printf(", physical: 0x%016lx, row_base_8k: 0x%016lx",
               physical_addr, get_row_base_8k(physical_addr));
    } else {
        printf(", physical: unavailable, row_base_8k: unavailable");
    }
    printf("\n");
}

void rh_init(void) {
    libswapcpu_init();
    setvbuf(stdout, NULL, _IONBF, 0);
    printf("starting...\n");

    int mmap_size = DIST * (D_ROW_SIZE + SPECIAL_ROW_COUNT);
    dram = (uint8_t*)mmap(0, mmap_size, PROT_WRITE | PROT_READ, 
                          MAP_PRIVATE | MAP_ANONYMOUS | MAP_POPULATE, 0, 0);
    
    if (syscall_emulation) {
        for (int i = 0; i < mmap_size; i += PAGE_SIZE) {
            *(dram + i) = 0;
        }
    }
    assert(dram != MAP_FAILED);

    for (int i = 0; i < D_ROW_SIZE; i++) {
        reg_file[i] = dram + i * DIST;
    }

    for (int i = 0; i < SPECIAL_ROW_COUNT; i++) {
        uint8_t* ptr = dram + (D_ROW_SIZE + i) * DIST;
        if (strcmp(special_row_sequence[i], "C0") == 0) C0 = ptr;
        else if (strcmp(special_row_sequence[i], "C1") == 0) C1 = ptr;
        else if (strcmp(special_row_sequence[i], "T0") == 0) T0 = ptr;
        else if (strcmp(special_row_sequence[i], "T1") == 0) T1 = ptr;
        else if (strcmp(special_row_sequence[i], "T2") == 0) T2 = ptr;
        else if (strcmp(special_row_sequence[i], "T3") == 0) T3 = ptr;
        else if (strcmp(special_row_sequence[i], "B_DCC0") == 0) B_DCC0 = ptr;
        else if (strcmp(special_row_sequence[i], "B_DCC0N") == 0) B_DCC0N = ptr;
        else if (strcmp(special_row_sequence[i], "B_DCC1") == 0) B_DCC1 = ptr;
        else if (strcmp(special_row_sequence[i], "B_DCC1N") == 0) B_DCC1N = ptr;
        else if (strcmp(special_row_sequence[i], "R0") == 0) R0 = ptr;
        else if (strcmp(special_row_sequence[i], "R1") == 0) R1 = ptr;
        else if (strcmp(special_row_sequence[i], "R2") == 0) R2 = ptr;
    }

    uint64_t trace_base_addr = get_actual_physical_addr((uintptr_t)reg_file[0]);
    if (trace_base_addr != 0) {
        CLEAR_TRACE_ROWS();
        SET_TRACE_BASE_ADDR(get_row_base_8k(trace_base_addr));

        for (int i = 0; i < D_ROW_SIZE; i++) {
            register_trace_row(reg_file[i]);
        }

        uint8_t *special_rows[] = {
            C0, C1, T0, T1, T2, T3,
            B_DCC0, B_DCC0N, B_DCC1, B_DCC1N,
            R0, R1, R2
        };
        for (size_t i = 0; i < sizeof(special_rows) / sizeof(special_rows[0]); i++) {
            register_trace_row(special_rows[i]);
        }
    }

    print_address(reg_file[0]);
    print_address(reg_file[1]);
    print_address(reg_file[10]);
    print_address(reg_file[12]);
    print_address(C0);
    print_address(C1);
    print_address(T0);
    print_address(T1);
    print_address(T2);
    print_address(T3);
    print_address(B_DCC0);
    print_address(B_DCC0N);
    print_address(B_DCC1);
    print_address(B_DCC1N);
    print_address(R0);
    print_address(R1);
    print_address(R2);
}

void rh_reset_stats(void) {
    reset_dram_contents();
    memset(regfile_access_count, 0, sizeof(regfile_access_count));
    memset(special_access_count, 0, sizeof(special_access_count));
    memset(regfile_bitflips, 0, sizeof(regfile_bitflips));
    memset(special_bitflips, 0, sizeof(special_bitflips));
    memset(refresh_count, 0, sizeof(refresh_count));
    total_ap_count = 0;
    total_aap_count = 0;
    total_refresh_count = 0;
}

static uint8_t* get_ptr_from_label(const char* label_raw) {
    char label[128];
    strncpy(label, label_raw, sizeof(label) - 1);
    label[sizeof(label) - 1] = '\0';
    label[strcspn(label, "\r\n ")] = '\0';

    if (strcmp(label, "C0") == 0) return C0;
    if (strcmp(label, "C1") == 0) return C1;
    if (strcmp(label, "T0") == 0) return T0;
    if (strcmp(label, "T1") == 0) return T1;
    if (strcmp(label, "T2") == 0) return T2;
    if (strcmp(label, "T3") == 0) return T3;
    if (strcmp(label, "B_DCC0") == 0) return B_DCC0;
    if (strcmp(label, "B_DCC0N") == 0) return B_DCC0N;
    if (strcmp(label, "B_DCC1") == 0) return B_DCC1;
    if (strcmp(label, "B_DCC1N") == 0) return B_DCC1N;
    if (strcmp(label, "R0") == 0) return R0;
    if (strcmp(label, "R1") == 0) return R1;
    if (strcmp(label, "R2") == 0) return R2;
    
    return NULL;
}

static int get_refresh_count_index(const char* label_raw) {
    char label[128];
    strncpy(label, label_raw, sizeof(label) - 1);
    label[sizeof(label) - 1] = '\0';
    label[strcspn(label, "\r\n ")] = '\0';

    // Check if it's a numeric row (regfile)
    if (isdigit((unsigned char)label[0])) {
        int row_idx = atoi(label);
        if (row_idx >= 0 && row_idx < D_ROW_SIZE) {
            return row_idx;
        }
        return -1;
    }

    // Check special rows
    if (strcmp(label, "C0") == 0) return D_ROW_SIZE + 0;
    if (strcmp(label, "C1") == 0) return D_ROW_SIZE + 1;
    if (strcmp(label, "T0") == 0) return D_ROW_SIZE + 2;
    if (strcmp(label, "T1") == 0) return D_ROW_SIZE + 3;
    if (strcmp(label, "T2") == 0) return D_ROW_SIZE + 4;
    if (strcmp(label, "T3") == 0) return D_ROW_SIZE + 5;
    if (strcmp(label, "B_DCC0") == 0) return D_ROW_SIZE + 6;
    if (strcmp(label, "B_DCC0N") == 0) return D_ROW_SIZE + 7;
    if (strcmp(label, "B_DCC1") == 0) return D_ROW_SIZE + 8;
    if (strcmp(label, "B_DCC1N") == 0) return D_ROW_SIZE + 9;
    if (strcmp(label, "R0") == 0) return D_ROW_SIZE + 10;
    if (strcmp(label, "R1") == 0) return D_ROW_SIZE + 11;
    if (strcmp(label, "R2") == 0) return D_ROW_SIZE + 12;
    
    return -1;
}

static void print_row_name(uint8_t *ptr) {
    for (int i = 0; i < D_ROW_SIZE; i++) {
        if (ptr == reg_file[i]) { 
            printf("%d ", i); 
            return; 
        }
    }
    if (ptr == C0) { printf("C0 "); return; }
    if (ptr == C1) { printf("C1 "); return; }
    if (ptr == T0) { printf("T0 "); return; }
    if (ptr == T1) { printf("T1 "); return; }
    if (ptr == T2) { printf("T2 "); return; }
    if (ptr == T3) { printf("T3 "); return; }
    if (ptr == B_DCC0) { printf("B_DCC0 "); return; }
    if (ptr == B_DCC0N) { printf("B_DCC0N "); return; }
    if (ptr == B_DCC1) { printf("B_DCC1 "); return; }
    if (ptr == B_DCC1N) { printf("B_DCC1N "); return; }
    if (ptr == R0) { printf("R0 "); return; }
    if (ptr == R1) { printf("R1 "); return; }
    if (ptr == R2) { printf("R2 "); return; }
    
    printf("UNKNOWN ");
}

static void increment_access_count(uint8_t *ptr) {
    for (int i = 0; i < D_ROW_SIZE; i++) {
        if (ptr == reg_file[i]) { regfile_access_count[i]++; return; }
    }
    if (ptr == C0) { special_access_count[0]++; return; }
    if (ptr == C1) { special_access_count[1]++; return; }
    if (ptr == T0) { special_access_count[2]++; return; }
    if (ptr == T1) { special_access_count[3]++; return; }
    if (ptr == T2) { special_access_count[4]++; return; }
    if (ptr == T3) { special_access_count[5]++; return; }
    if (ptr == B_DCC0) { special_access_count[6]++; return; }
    if (ptr == B_DCC0N) { special_access_count[7]++; return; }
    if (ptr == B_DCC1) { special_access_count[8]++; return; }
    if (ptr == B_DCC1N) { special_access_count[9]++; return; }
    if (ptr == R0) { special_access_count[10]++; return; }
    if (ptr == R1) { special_access_count[11]++; return; }
    if (ptr == R2) { special_access_count[12]++; return; }
}

void rh_hammer(const char *filename) {
    FILE *fp = fopen(filename, "r");
    if (!fp) {
        perror("fopen");
        return;
    }

    int capacity = 1000;
    hammer_event_t *events = (hammer_event_t *)malloc(capacity * sizeof(hammer_event_t));
    int event_count = 0;
    char line[4096*4];

    if (fgets(line, sizeof(line), fp) == NULL) {
        fclose(fp);
        free(events);
        return;
    }

    int line_number = 1;

    while (fgets(line, sizeof(line), fp)) {
        line_number++;

        hammer_event_t current_event;
        memset(&current_event, 0, sizeof(hammer_event_t));
        current_event.num_rows = 0;
        current_event.command_type = -1;  // -1 = unknown

        char *token = strtok(line, " ,\n\r\t");
        if (!token) continue; 

        if (strcmp(token, "AP") == 0) {
            total_ap_count++;
            current_event.command_type = 0;  // AP
            token = strtok(NULL, " ,\n\r\t"); 
        } else if (strcmp(token, "AAP") == 0) {
            total_aap_count++;
            current_event.command_type = 1;  // AAP
            token = strtok(NULL, " ,\n\r\t"); 
        } else if (strcmp(token, "REF") == 0) {
            current_event.command_type = 2;  // REF
            // REF command: REF row1 row2 row3... => refresh multiple rows
            token = strtok(NULL, " ,\n\r\t");
            while (token) {
                int idx = get_refresh_count_index(token);
                if (idx >= 0 && idx < D_ROW_SIZE + SPECIAL_ROW_COUNT) {
                    total_refresh_count++;
                    refresh_count[idx]++;
                } else {
                    // Invalid row label, stop processing this REF line
                    break;
                }
                token = strtok(NULL, " ,\n\r\t");
            }
            continue;
        }

        while (token != NULL && current_event.num_rows < MAX_ROWS_PER_LINE) {
            uint8_t *ptr = get_ptr_from_label(token);
            if (ptr) {
                current_event.offsets[current_event.num_rows] = (uint32_t)(ptr - dram);
                current_event.num_rows++;
            } else if (isdigit((unsigned char)token[0])) { 
                int row_idx = atoi(token);
                if (row_idx >= 0 && row_idx < D_ROW_SIZE) {
                    current_event.offsets[current_event.num_rows] = (uint32_t)(reg_file[row_idx] - dram);
                    current_event.num_rows++;
                } else {
                    fprintf(stderr, "WARNING: Row index %d is OUT OF BOUNDS (Allowed: 0 to %d) at line %d! Skipping token.\n", 
                                row_idx, D_ROW_SIZE - 1, line_number);
                }
            }

            token = strtok(NULL, " ,\n\r\t");
        }

        if (current_event.num_rows > 0 && current_event.command_type >= 0) {
            if (event_count >= capacity) {
                capacity *= 2; 
                events = (hammer_event_t *)realloc(events, capacity * sizeof(hammer_event_t));
            }
            events[event_count] = current_event;
            event_count++;
        }
    }
    fclose(fp);

    fflush(stdout);
    libswapcpu_swapcpu();

    int total_mmap_size = DIST * (D_ROW_SIZE + SPECIAL_ROW_COUNT);

    for (int i = 0; i < event_count; i++) {
        int safe_num_rows = events[i].num_rows;
        
        if (safe_num_rows > MAX_ROWS_PER_LINE || safe_num_rows < 0) {
            safe_num_rows = safe_num_rows % (MAX_ROWS_PER_LINE + 1);
            if (safe_num_rows < 0) safe_num_rows = -safe_num_rows;
        }

        // Set mode based on command type
        if (events[i].command_type == 0) {
            // AP trace
            SET_MODE_SIMRA;
        } else if (events[i].command_type == 1) {
            // AAP trace
            SET_MODE_COMRA;
        } else if (events[i].command_type == 2) {
            // REF trace
            SET_MODE_NORMAL;
        }

        for (int j = 0; j < safe_num_rows; j++) {
            uint32_t safe_offset = events[i].offsets[j] % total_mmap_size;
            uint8_t *target_ptr = dram + safe_offset;

            flushaccess(target_ptr);
            increment_access_count(target_ptr);
        }
    }

    SET_MODE_NORMAL;
    fflush(stdout);
    libswapcpu_swapcpu();

    if (events) free(events);
}

static int scan_int(const char *msg, uint8_t *base) {
    uint64_t *b = (uint64_t *)base;
    int flips = 0;
    for (size_t i = 0; i < rowsize / sizeof(uint64_t); i++) {
        if (b[i] != co) {
            flips += popcount(b[i] ^ co);
        }
    }
    return flips;
}

void scan(uint8_t* base) {
    uint64_t* b = (uint64_t*) base;
    int flips[5] = {0};
    for (unsigned i=0; i < rowsize / sizeof(uint64_t); i++) {
        if (b[i] != co) {
            uintptr_t virtual_addr = (uintptr_t)&b[i];
            uint64_t physical_addr = get_actual_physical_addr(virtual_addr);

            printf("0x%016lx: %d flips, mask: 0x%016lx",
                   (uint64_t)virtual_addr, popcount(b[i] ^ co), b[i] ^ co);
            if (physical_addr != 0) {
                printf(", row_base_8k: 0x%016lx",
                       get_row_base_8k(physical_addr));
            } else {
                printf(", physical: unavailable, row_base_8k: unavailable");
            }
            printf("\n");
            flips[popcount(b[i] ^ co)]++;
        }
    }
    printf("flips: %d %d %d %d\n", flips[1], flips[2], flips[3], flips[4]);
}

void rh_collect_bitflips(void) {
    for (int i = 0; i < D_ROW_SIZE; i++) {
        // printf("scanning at row %d\n", i);
        regfile_bitflips[i] = scan_int("", reg_file[i]);
    }
    struct { const char *name; uint8_t *ptr; } extras[] = {
        {"C0", C0}, {"C1", C1}, {"T0", T0}, {"T1", T1}, {"T2", T2}, {"T3", T3},
        {"B_DCC0", B_DCC0}, {"B_DCC0N", B_DCC0N}, {"B_DCC1", B_DCC1}, {"B_DCC1N", B_DCC1N},
        {"R0", R0}, {"R1", R1}, {"R2", R2}
    };
    for (int i = 0; i < SPECIAL_ROW_COUNT; i++) {
        special_bitflips[i] = scan_int(extras[i].name, extras[i].ptr);
    }
}

void dump_final_info(const char *trace) {
    char source_trace[1024];
    strncpy(source_trace, trace, sizeof(source_trace) - 1);
    source_trace[sizeof(source_trace) - 1] = '\0';

    char *source_ext = strrchr(source_trace, '.');
    if (source_ext) {
        char *trr_suffix = source_ext - 4;
        if (trr_suffix >= source_trace && strcmp(trr_suffix, "-trr") == 0) {
            memmove(trr_suffix, source_ext, strlen(source_ext) + 1);
        }
    }

    const char *base = strrchr(trace, '/');
    base = base ? base + 1 : trace;
    const char *verify_marker = "/progs/verify/";
    const char *verify_dir = strstr(trace, verify_marker);

    char tmp[256];
    strncpy(tmp, base, sizeof(tmp) - 1);
    tmp[sizeof(tmp) - 1] = '\0';

    char *ext = strrchr(tmp, '.');
    if (ext) *ext = '\0'; 

    char report_dir[512];
    if (verify_dir) {
        size_t prefix_len = (size_t)(verify_dir - trace) + strlen(verify_marker);
        if (prefix_len >= sizeof(report_dir)) {
            fprintf(stderr, "report directory path is too long: %s\n", trace);
            return;
        }

        memcpy(report_dir, trace, prefix_len);
        report_dir[prefix_len] = '\0';
        strncat(report_dir, "report", sizeof(report_dir) - strlen(report_dir) - 1);
    } else {
        snprintf(report_dir, sizeof(report_dir), "report");
    }

    char outname[768];
    if (snprintf(outname, sizeof(outname), "%s/%s-final-report.txt", report_dir, tmp) >= (int)sizeof(outname)) {
        fprintf(stderr, "report file path is too long: %s/%s-final-report.txt\n", report_dir, tmp);
        return;
    }

    FILE *fp = fopen(outname, "w");
    if (!fp) { perror("fopen final_report"); return; }

    uint64_t total_access_count = 0;
    uint64_t total_bitflip_errors = 0;

    for (int i = 0; i < D_ROW_SIZE; i++) {
        total_access_count += regfile_access_count[i];
        total_bitflip_errors += regfile_bitflips[i];
    }

    for (int i = 0; i < SPECIAL_ROW_COUNT; i++) {
        total_access_count += special_access_count[i];
        total_bitflip_errors += special_bitflips[i];
    }

    uint64_t total_ap_time_ns = total_ap_count * tAP;   
    uint64_t total_aap_time_ns = total_aap_count * tAAP; 
    uint64_t total_refresh_time_ns = total_refresh_count * tREF;
    uint64_t total_pim_time_ns = total_ap_time_ns + total_aap_time_ns;

    fprintf(fp, "================== FINAL RH REPORT ==================\n");
    fprintf(fp, "Source trace: %s\n", source_trace);
    fprintf(fp, "Total Access Count (All Rows): %lu\n", total_access_count); 
    fprintf(fp, "Total Bitflip Errors (All Rows): %lu\n", total_bitflip_errors);
    fprintf(fp, "Total Refresh Count: %lu\n\n", total_refresh_count); 

    fprintf(fp, "--------------- PIM OPERATIONS & TIME ---------------\n");
    fprintf(fp, "Total AP Operations:      %10lu | Est. Time: %lu ns\n", total_ap_count, total_ap_time_ns);
    fprintf(fp, "Total AAP Operations:     %10lu | Est. Time: %lu ns\n", total_aap_count, total_aap_time_ns);
    fprintf(fp, "Total Refresh Operations: %10lu | Est. Time: %lu ns\n", total_refresh_count, total_refresh_time_ns);   
    fprintf(fp, "-----------------------------------------------------\n");
    fprintf(fp, "Total Est. PIM Execution Time: %lu ns (%.6f ms)\n", 
            total_pim_time_ns, (double)total_pim_time_ns / 1000000.0);
    fprintf(fp, "REF Est. Overhead Percentage: %f \n\n", (double)total_refresh_time_ns / total_pim_time_ns);

    fprintf(fp, "--------------- REG_FILE ROWS (%d rows) ---------------\n", D_ROW_SIZE);
    
    for (int i = 0; i < D_ROW_SIZE; i++) {
        fprintf(fp, "Row %4d | Accesses: %10lu | Bitflips: %d | Refresh: %d\n",
                i, regfile_access_count[i], regfile_bitflips[i], refresh_count[i]);
    }

    fprintf(fp, "\n--------------- SPECIAL ROWS ---------------\n");
    const char *names[SPECIAL_ROW_COUNT] = {
        "C0", "C1", "T0", "T1", "T2", "T3",
        "B_DCC0", "B_DCC0N", "B_DCC1", "B_DCC1N",
        "R0", "R1", "R2"
    };
    for (int i = 0; i < SPECIAL_ROW_COUNT; i++) {
        fprintf(fp, "%-8s | Accesses: %10lu | Bitflips: %d | Refresh: %d\n",
                names[i], special_access_count[i], special_bitflips[i], refresh_count[D_ROW_SIZE + i]);
    }
    fprintf(fp, "======================================================\n");
    fclose(fp);
    printf("Final report saved to: %s\n", outname);
}
