#ifndef LIBHAMMER_H_
#define LIBHAMMER_H_

#include <stdint.h>

uint64_t virt_to_phys(uint64_t vaddr);
#undef sleep
void sleep(int approx_seconds);

#endif // LIBHAMMER_H_