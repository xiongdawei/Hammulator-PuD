# Lab Partner Hammulator Patch

This directory contains a small gem5 patch and a separate DRAMsim3
configuration profile for a second Hammulator setup.

## Files

- `0007-lab-partner-trace-window.patch`: applies on top of the existing
  Hammulator gem5 patch series. It adds trace-row registration through
  `m5_work_begin()` ids 4, 5, and 6, filters hammer accounting to registered
  trace rows, moves mitigation summaries onto the `PuDHammer` debug flag, and
  prints special trace rows with labels such as `C0`, `T0`, and `B_DCC0`.
- `DDR4_8Gb_x8_2400_lab_partner.ini`: a distinct DDR4 profile with different
  rowhammer thresholds, spatial variation seed/multipliers, disturbance
  weights, temperature, and bit-flip distribution.

## Apply

From a clean `gem5` checkout after applying `../gem5-patches/*.patch`:

```bash
git -C gem5 apply ../lab-partner/0007-lab-partner-trace-window.patch
cp lab-partner/DDR4_8Gb_x8_2400_lab_partner.ini \
   gem5/ext/dramsim3/DRAMsim3/configs/
```

Then point the run command at:

```text
gem5/ext/dramsim3/DRAMsim3/configs/DDR4_8Gb_x8_2400_lab_partner.ini
```

## Trace Registration

The patch reserves these `m5_work_begin(workid, threadid)` calls:

- `workid = 4`: set the trace base physical address to `threadid`
- `workid = 5`: clear all registered trace-row bases
- `workid = 6`: register one trace-row base physical address from `threadid`

Attack mode ids stay unchanged:

- `0`: `NORMAL`
- `1`: `SiMRA`
- `2`: `CoMRA`
- `3`: `RESET_BYPASS`
