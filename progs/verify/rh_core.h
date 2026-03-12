#ifndef RH_CORE_H
#define RH_CORE_H

#ifdef __cplusplus
extern "C" {
#endif

void rh_init(void);
void rh_reset_stats(void);
void rh_hammer_from_file(int time, const char *filename);
void rh_collect_bitflips(void);
void dump_final_info(const char *trace_file);

#ifdef __cplusplus
}
#endif

#endif // RH_CORE_H