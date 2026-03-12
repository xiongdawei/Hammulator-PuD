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

#define PAGE_SIZE 4096
#define D_ROW_SIZE 170
#define C_ROW_SIZE 2
#define B_ROW_SIZE 8
#define DIST (1 << (6 + 7 + 2))  // 32KB row size

const int rowsize = 2 * PAGE_SIZE;
const int page_size = PAGE_SIZE;

extern int syscall_emulation;

const char* special_row_sequence[10] = {
    "T0", "T1", "T2", "T3",
    "C0", "C1",
    "B_DCC0", "B_DCC0N", "B_DCC1", "B_DCC1N"
};

static uint8_t *dram;
static uint8_t *reg_file[D_ROW_SIZE];
static uint8_t *C0, *C1, *T0, *T1, *T2, *T3, *B_DCC0, *B_DCC0N, *B_DCC1, *B_DCC1N;

static uint64_t regfile_access_count[D_ROW_SIZE];
static uint64_t special_access_count[10];  
static int regfile_bitflips[D_ROW_SIZE];
static int special_bitflips[10];  
static int refresh_count[D_ROW_SIZE + 10]; 

uint64_t co = 0; 

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

static uint64_t get_physical_addr(uintptr_t virtual_addr) {
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

static void print_address(uint8_t *a) {
    printf("0x%016lx", (uint64_t)a);
    if (!syscall_emulation) {
        printf(", physical: 0x%016lx", get_physical_addr((intptr_t)a));
    }
    printf("\n");
}

void rh_init(void) {
    libswapcpu_init();
    setvbuf(stdout, NULL, _IONBF, 0);
    printf("starting...\n");

    int mmap_size = DIST * (D_ROW_SIZE + C_ROW_SIZE + B_ROW_SIZE);
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

    for (int i = 0; i < 10; i++) {
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
    }
    print_address(T0);
    print_address(T1);
    print_address(T2);
}

void rh_reset_stats(void) {
    memset(regfile_access_count, 0, sizeof(regfile_access_count));
    memset(special_access_count, 0, sizeof(special_access_count));
    memset(regfile_bitflips, 0, sizeof(regfile_bitflips));
    memset(special_bitflips, 0, sizeof(special_bitflips));
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
    
    return NULL;
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
}

void rh_hammer_from_file(int time, const char *filename) {
    printf("switching to timing cpu...\n");
    libswapcpu_swapcpu();
    printf("now hammering from file %s...\n", filename);
    
    for (int i = 0; i < time; i++) {
        int max_lines = 10000 * 8;
        int total_lines = 0;
        char line[4096];

        while (total_lines < max_lines) {
            FILE *fp = fopen(filename, "r");
            if (!fp) { perror("fopen"); exit(EXIT_FAILURE); }
            fgets(line, sizeof(line), fp); // skip header

            while (fgets(line, sizeof(line), fp) && total_lines < max_lines) {
                size_t len = strlen(line);
                if (len > 0 && line[len - 1] == '\n') line[len - 1] = '\0';
                total_lines++;
                char *tok = strtok(line, " \t\r\n");
                while (tok) {
                    uint8_t *ptr = get_ptr_from_label(tok);
                    if (!ptr && isdigit(tok[0])) {
                        int row = atoi(tok);
                        if (row >= 0 && row < D_ROW_SIZE) ptr = reg_file[row];
                    }
                    if (ptr) {
                        flushaccess(ptr);
                        increment_access_count(ptr);
                    }
                    tok = strtok(NULL, " \t\r\n");
                }
            }
            fclose(fp);
        }
    }
    printf("swapping back to atomic...\n");
    libswapcpu_swapcpu();  
}

static int scan(const char *msg, uint8_t *base) {
    uint64_t *b = (uint64_t *)base;
    int flips = 0;
    for (int i = 0; i < rowsize / sizeof(uint64_t); i++) {
        if (b[i] != co) {
            flips += popcount(b[i] ^ co);
        }
    }
    return flips;
}

void rh_collect_bitflips(void) {
    for (int i = 0; i < D_ROW_SIZE; i++) {
        regfile_bitflips[i] = scan("", reg_file[i]);
    }
    struct { const char *name; uint8_t *ptr; } extras[] = {
        {"C0", C0}, {"C1", C1}, {"T0", T0}, {"T1", T1}, {"T2", T2}, {"T3", T3},
        {"B_DCC0", B_DCC0}, {"B_DCC0N", B_DCC0N}, {"B_DCC1", B_DCC1}, {"B_DCC1N", B_DCC1N}
    };
    for (int i = 0; i < 10; i++) {
        special_bitflips[i] = scan(extras[i].name, extras[i].ptr);
    }
}

void dump_final_info(const char *trace_file) {
    const char *base = strrchr(trace_file, '/');
    base = base ? base + 1 : trace_file;

    char tmp[256];
    strncpy(tmp, base, sizeof(tmp) - 1);
    tmp[sizeof(tmp) - 1] = '\0';

    char *ext = strrchr(tmp, '.');
    if (ext) *ext = '\0'; 

    char outname[300];
    snprintf(outname, sizeof(outname), "/home/daweix3/hammulator/progs/verify/report/%s-final-report.txt", tmp);

    FILE *fp = fopen(outname, "w");
    if (!fp) { perror("fopen final_report"); return; }

    fprintf(fp, "================== FINAL RH REPORT ==================\n");
    fprintf(fp, "Source trace: %s\n\n", trace_file);
    fprintf(fp, "--------------- REG_FILE ROWS (%d rows) ---------------\n", D_ROW_SIZE);
    
    for (int i = 0; i < D_ROW_SIZE; i++) {
        fprintf(fp, "Row %4d | Accesses: %10lu | Bitflips: %d | Refresh: %d\n",
                i, regfile_access_count[i], regfile_bitflips[i], refresh_count[i]);
    }

    fprintf(fp, "\n--------------- SPECIAL ROWS ---------------\n");
    const char *names[10] = {"C0", "C1", "T0", "T1", "T2", "T3", "B_DCC0", "B_DCC0N", "B_DCC1", "B_DCC1N"};
    for (int i = 0; i < 10; i++) {
        fprintf(fp, "%-8s | Accesses: %10lu | Bitflips: %d | Refresh: %d\n",
                names[i], special_access_count[i], special_bitflips[i], refresh_count[D_ROW_SIZE + i]);
    }
    fprintf(fp, "======================================================\n");
    fclose(fp);
    printf("Final report saved to: %s\n", outname);
}