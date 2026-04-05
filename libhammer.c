#include "libhammer.h"

#include <gem5/m5ops.h>
#include <m5_mmap.h>

#include <stdio.h>

// Translate a virtual address to a physical address.
uint64_t virt_to_phys(uint64_t vaddr) {
    uint64_t ret = m5_virttophys(vaddr);
    if (ret == 0) {
        fprintf(stderr, "Could not translate virtual address %lx. Is it correcly mapped? Mind lazy mapping.\n", vaddr);
    }
    return ret;
}

// Gem5 does not implement sleep, so make our own.
// Helpful to wait for the victim.
void sleep(int approx_seconds) {
    volatile int tmp = 0;
    for (int i = 0; i < 100000*approx_seconds; i++) {
      tmp += 1;
    }
}