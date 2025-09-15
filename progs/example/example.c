#include <assert.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#include "libhammer.h"
#include "libswapcpu.h"

// Colors
#define RESET   "\033[0m"
#define RED     "\033[31m"
#define GREEN   "\033[32m"
#define YELLOW  "\033[33m"
#define CYAN    "\033[36m"
#define BOLD    "\033[1m"

// The DRAM addressing configuration is:
//
// addressing_mapping = channel rank bank row bankgroup column (chrabarobgco)
// banks_per_group    = 4
// rows               = 65536
// bankgroups         = 4
// columns_per_row    = 1024
//
// column_size        = 8B
// (can be inspected in gem5/ext/dramsim3/DRAMsim3/configs/DDR4_8Gb_x8_2400.ini)

// Resulting row size:
#define ROW_SIZE 8192

int bankgroups = 4;
int rows = 65536;
int get_dram_set(uint64_t paddr) {
  return ((paddr>>(16+2+10+3)) << 2) | ((paddr>>(10+3)) & (bankgroups-1));
}
int get_dram_row(uint64_t paddr) {
  return (paddr>>(2+10+3)) & (rows-1);
}

#define PAGE_SIZE 4096

// log2(column_size) + log2(columns_per_row) + log2(bankgroups)
#define row_dist (uint64_t)(1 << (3+10+2))

static inline void flush(void *p) {
  asm volatile("clflush 0(%0)\n" : : "c"(p) : "rax");
}

static inline void flushaccess(void *p) {
  asm volatile("clflush 0(%0)\n"
               "movq (%0), %%rax\n" : : "c"(p) : "rax");
}

#define HAMMER_N 10000
#define MARKER 0xdeadbeefdeadbeef

int main()
{
    setvbuf(stdout, NULL, _IONBF, 0);

    libswapcpu_init();

    int mmap_size = row_dist*3*150;
    uint8_t* mapping_base = (uint8_t*)mmap(0, mmap_size, PROT_WRITE | PROT_READ, MAP_PRIVATE | MAP_ANONYMOUS, 0, 0);

    // Populate the mapping. Assert that physically coherent.
    uint64_t next = 0;
    for (int i=0; i<mmap_size; i+=PAGE_SIZE) {
      *((volatile uint64_t*)(mapping_base+i)) = 0;
      uint64_t res = virt_to_phys((uint64_t)mapping_base+i);
      if (next) {
        assert(next + (1 << 12) == res);
      }
      next = res;
    }

    // Shift mapping base to begin at start of physical row.
    while ((virt_to_phys((uint64_t)mapping_base) % row_dist) != 0) {
      mapping_base += PAGE_SIZE;
    }

    printf(CYAN "switching to timing cpu...\n" RESET);
    libswapcpu_swapcpu();

    // Now go through the mapped memory and try to find bit flips.
    int jj = 0;
    for (uint8_t* lower = mapping_base; mapping_base + 2*row_dist < mapping_base + mmap_size; lower += row_dist) {
      printf("[%03d] " "paddr=" CYAN "%lx" RESET "\n",
             jj, virt_to_phys((uint64_t)lower+row_dist));
      jj++;

      uint8_t* middle = lower+1*row_dist;
      uint8_t* upper = lower+2*row_dist;

      assert(get_dram_set(virt_to_phys((uint64_t)lower)) == get_dram_set(virt_to_phys((uint64_t)middle)));
      assert(get_dram_set(virt_to_phys((uint64_t)lower)) == get_dram_set(virt_to_phys((uint64_t)upper)));
      assert(get_dram_row(virt_to_phys((uint64_t)lower))+1 == get_dram_row(virt_to_phys((uint64_t)middle)));
      assert(get_dram_row(virt_to_phys((uint64_t)middle))+1 == get_dram_row(virt_to_phys((uint64_t)upper)));

      // Reset the row to marker.
      for (unsigned i = 0; i < ROW_SIZE/sizeof(uint64_t); i++) {
        ((uint64_t*)middle)[i] = MARKER;
        flush(&((uint64_t*)middle)[i]);
      }

      // Hammer the rows.
      for (unsigned i = 0; i < HAMMER_N; i++)
      {
          flushaccess(lower);
          flushaccess(upper);
      }

      // Search for bit flips.
      for (unsigned i = 0; i < ROW_SIZE; i+=sizeof(uint64_t)) {
        if (*(uint64_t*)(middle+i) != MARKER) {
          printf(RED BOLD "Found bit flips" RESET
                 " at " CYAN "%p" RESET " with pattern: " YELLOW "%016lx" RESET "\n",
                 middle+i, *(uint64_t*)(middle+i) ^ MARKER);
        }
      }
    }

    fprintf(stderr, RED "Couldn't find working bit flips in a lot of memory, so exit.\n" RESET);
    return 1;
}
