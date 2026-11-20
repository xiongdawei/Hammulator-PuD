#ifndef RH_CORE_H
#define RH_CORE_H



#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>



void rh_init(void);
void rh_reset_stats(void);
// target_events <= 0 runs through the trace exactly once (original
// behavior). target_events > 0 replays the parsed event list, wrapping
// around from the start, until that many events have executed.
void rh_hammer(const char *trace, long target_events);
void rh_collect_bitflips(void);
void dump_final_info(const char *trace);
void get_row_name_str(uint8_t *ptr, char *buf);
void dump_row_addresses(void);
// void dump_restructured_trace(const char *original_filename, hammer_event_t *events, int event_count);
// void print_row_name(uint8_t *ptr);

#ifdef __cplusplus
}
#endif

#endif // RH_CORE_H