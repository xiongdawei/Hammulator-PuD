#!/usr/bin/env python3
"""Build row-level replay traces for the 12 MIMDRAM applications.

Why synthesize: in the MIMDRAM gem5 build, the bbops the LLVM PIM pass emits for
ADD/SUB/MUL/DIV (opcodes 0x62-0x65) only print "@PIM_ADD" etc. and issue no row
commands (gem5/src/mem/dram_ctrl.cc), so application binaries yield no row trace.
MIMDRAM's control unit executes each bbop as a fixed SIMDRAM uProgram, so an
application's per-subarray activation stream is its sequence of bbops, each
expanded into its uProgram. We take the uPrograms from the validated
microworkload traces (one of the two identical iterations they contain) and
relocate their operand rows onto the application's arrays.

Per-application loop bodies follow MIMDRAM Table 3 (PUD ops, max VF) and the
order of operations in each benchmark's hot loop (sources in the MIMDRAM docker:
/root/benchmarks/{PolyBenchC-4.2.1,gpu-rodinia/openmp,phoenix,SPEC}); fdtd-apml
follows PolyBench 3.2 (not shipped with 4.2.1). Data are 32-bit fixed point as in
MIMDRAM (Rodinia converted to fixed point; x264 uses uint8 -> 8-bit uPrograms).

Subarray layout used by every application (D rows of the replay harness):
    TEMP  rows   0..101  scratch rows of multu/divu (uProgram labels 149..250)
    S0-S5 rows 102..299  six 33-row operand slots (32 bit-rows + 1 spare)
    special rows 300..312 (C0 C1 T0..T3 DCC R0..R2), unchanged harness order

Reduction (R) follows MIMDRAM Sec. 4.1.1: ceil(log2(#mats)) inter-mat GB-MOV
steps (the receiving mat activates the tmp bit-row once per 4-bit column move)
and log2(512/4)=7 intra-mat LC-MOV steps (ACT src + ACT tmp per move), each step
followed by one PUD addition. Plain activations are emitted as "ACT r1 [r2]".

The generated trace fills exactly one refresh window of PuD time
(46 ns/AP, 60 ns/AAP, 50 ns/ACT; default 64 ms) so that one replay = one tREFW.

    python3 build_app_traces.py                # all apps -> traces/apps/*.txt
    python3 build_app_traces.py --window-ms 8  # shorter, for smoke tests
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
# microworkload traces: $HAMMULATOR, else the enclosing hammulator checkout
# (when this lives in hammulator/autotest), else ~/hammulator
_HAMM = Path(os.environ["HAMMULATOR"]) if "HAMMULATOR" in os.environ else (
    ROOT.parent if (ROOT.parent / "progs/verify/microworkloads").is_dir()
    else Path.home() / "hammulator")
MW = _HAMM / "progs/verify/microworkloads"
OUT = ROOT / "traces/apps"

T_AP, T_AAP, T_ACT = 46, 60, 50
MAT_COLS = 512
MATS_PER_SUBARRAY = 128          # 16 mats/chip x 8 chips (MIMDRAM Table 2)
TEMP_BASE = 0
SLOT_BASE = 102
SLOT_ROWS = 33
N_SLOTS = 6
BITS = 32


def slot(s: int) -> int:
    assert 0 <= s < N_SLOTS
    return SLOT_BASE + SLOT_ROWS * s


# ---------------------------------------------------------------------------
# uPrograms
# ---------------------------------------------------------------------------
def load_uprog(stem: str, n_ops: int | None = None) -> list[tuple[str, list[str]]]:
    lines = (MW / f"{stem}-plus.txt").read_text().splitlines()[1:]
    one = lines[: len(lines) // 2]          # traces hold two identical iterations
    if n_ops is not None:
        one = one[:n_ops]
    ops = []
    for ln in one:
        t = ln.replace(",", " ").split()
        ops.append((t[0], t[1:]))
    return ops


UPROG = {
    "add": load_uprog("00_addition"),
    "sub": load_uprog("01_subtraction"),
    "mul": load_uprog("11_multu"),
    "div": load_uprog("16_divu"),
    # 8-bit add for x264 (uint8): prologue + 8 of the 32 bit-serial iterations
    "add8": load_uprog("00_addition", 1 + 8 * 8),
}

# bit-row offsets inside a slot, per role (the source allocation had one gap row)
IN1_OFFS = sorted({int(x) - 49 for _, r in UPROG["add"] for x in r if x.isdigit() and 49 <= int(x) <= 81})
OUT_OFFS = sorted({int(x) - 116 for _, r in UPROG["add"] for x in r if x.isdigit() and 116 <= int(x) <= 148})
assert len(IN1_OFFS) == BITS and len(OUT_OFFS) == BITS


def relocate(label: str, a: int, b: int, o: int) -> str:
    if not label.isdigit():
        return label
    v = int(label)
    if 49 <= v <= 81:
        return str(slot(a) + v - 49)
    if 82 <= v <= 114:
        return str(slot(b) + v - 82)
    if 116 <= v <= 148:
        return str(slot(o) + v - 116)
    if 149 <= v <= 250:
        return str(TEMP_BASE + v - 149)
    raise ValueError(f"unexpected uProgram row label {label}")


class Trace:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.ns = 0
        self.n_ap = self.n_aap = self.n_act = 0
        self.bbops: dict[str, int] = {}

    def _count(self, kind: str) -> None:
        self.bbops[kind] = self.bbops.get(kind, 0) + 1

    def bbop(self, kind: str, o: int, a: int, b: int | None = None) -> None:
        b = a if b is None else b
        self._count(kind)
        for cmd, rows in UPROG[kind]:
            rr = [relocate(x, a, b, o) for x in rows]
            if cmd == "AAP":
                self.lines.append(f"AAP {' '.join(rr[:-1])}, {rr[-1]}")
                self.ns += T_AAP
                self.n_aap += 1
            else:
                self.lines.append(f"AP {' '.join(rr)}")
                self.ns += T_AP
                self.n_ap += 1

    def act(self, *rows: int) -> None:
        self.lines.append("ACT " + " ".join(str(r) for r in rows))
        self.ns += T_ACT * len(rows)
        self.n_act += len(rows)

    def copy(self, dst: int, src: int) -> None:       # bbop_mov inside the mat: RowClone
        self._count("copy")
        for d, s in zip(OUT_OFFS, OUT_OFFS):
            self.lines.append(f"AAP {slot(dst) + d}, {slot(src) + s}")
            self.ns += T_AAP
            self.n_aap += 1

    def load(self, dst: int) -> None:
        """New operand slice arrives in the slot (inter-subarray copy / host
        write in vertical layout): one activation per bit-row."""
        self._count("load")
        for d in OUT_OFFS:
            self.act(slot(dst) + d)

    def reduce(self, p: int, q: int, vf: int) -> None:
        """MIMDRAM vector reduction of slot p using tmp slot q."""
        self._count("reduce")
        mats = max(1, math.ceil(vf / MAT_COLS))
        gb_steps = math.ceil(math.log2(min(mats, MATS_PER_SUBARRAY))) if mats > 1 else 0
        for _ in range(gb_steps):                  # GB-MOV: whole mat -> tmp
            for d in OUT_OFFS:
                for _m in range(MAT_COLS // 4):
                    self.act(slot(q) + d)
            self.bbop("add", o=p, a=p, b=q)
        elems = MAT_COLS
        while elems > 4:                           # LC-MOV adder tree in the mat
            half = elems // 2
            for d in OUT_OFFS:
                for _m in range(half // 4):
                    self.act(slot(p) + d, slot(q) + d)
            self.bbop("add", o=p, a=p, b=q)
            elems = half


# ---------------------------------------------------------------------------
# Applications: (Table 3 PUD ops, VF used for #mats, loop body, groups per app)
# A body = the bbops one mat range executes per output element / inner vector
# iteration; it is repeated (rotating streamed operands over resident slots).
# ---------------------------------------------------------------------------
def body_dot(t: Trace, it: int, vf: int, prep_every: int = 4, scale: bool = False) -> None:
    """y = sum(x * w): x resident in S0 (reloaded every `prep_every` dot
    products: next row of A), w streams through S1..S3; product S4, tmp S5."""
    if it % prep_every == 0:
        t.load(0)
        if scale:                                  # alpha * A[i][:] (2mm/gemm)
            t.load(5)                              # broadcast scalar
            t.bbop("mul", o=0, a=0, b=5)
    w = 1 + it % 3
    if it % 3 == 0:
        t.load(w)
    t.bbop("mul", o=4, a=0, b=w)
    t.reduce(4, 5, vf)


def app_gemm(t, it):  body_dot(t, it, 4000, scale=True)
def app_2mm(t, it):   body_dot(t, it, 4000, scale=(it // 4) % 2 == 0)
def app_3mm(t, it):   body_dot(t, it, 4000)
def app_hw(t, it):    body_dot(t, it, 2601)
def app_bp(t, it):    body_dot(t, it, 134217729, prep_every=16)


def app_dg(t, it):                                 # M, C, R (VF 1000)
    body_dot(t, it, 1000, prep_every=8)
    if it % 8 == 7:                                # A[r][q][:] = sum[:]
        t.copy(0, 4)


def app_cov(t, it):                                # D, S, R (+M for the products)
    if it % 4 == 0:                                # center next column: S
        t.load(1)
        t.load(5)
        t.bbop("sub", o=1, a=1, b=5)
    w = 2 + it % 2
    t.load(w)
    t.bbop("mul", o=4, a=1, b=w)
    t.reduce(4, 5, 4000)
    if it % 4 == 3:                                # cov[i][:] /= (n-1): D
        t.load(5)
        t.bbop("div", o=4, a=4, b=5)


def app_pca(t, it):                                # D, S, M, R (Phoenix pca)
    if it % 4 == 0:                                # mean of a new row: R + D, center: S
        t.load(0)
        t.copy(4, 0)
        t.reduce(4, 5, 4000)
        t.load(5)
        t.bbop("div", o=3, a=4, b=5)
        t.bbop("sub", o=0, a=0, b=3)
    t.load(1)
    t.bbop("sub", o=1, a=1, b=3)
    t.bbop("mul", o=4, a=0, b=1)
    t.reduce(4, 5, 4000)


def app_gs(t, it):                                 # M, D, R (+S in the update)
    if it % 4 == 0:                                # new column k: norm, Q = A/R
        t.load(0)
        t.bbop("mul", o=4, a=0, b=0)
        t.reduce(4, 5, 4000)
        t.load(5)
        t.bbop("div", o=0, a=0, b=5)
    w = 1 + it % 2
    t.load(w)
    t.bbop("mul", o=4, a=0, b=w)                   # R[k][j] = Q.k . A.j
    t.reduce(4, 5, 4000)
    t.load(5)
    t.bbop("mul", o=4, a=0, b=5)                   # A.j -= Q.k * R[k][j]
    t.bbop("sub", o=w, a=w, b=4)


def app_km(t, it):                                 # S, M, R (VF 16384)
    t.load(1)                                      # broadcast centroid coordinate
    t.bbop("sub", o=2, a=0, b=1)
    t.bbop("mul", o=4, a=2, b=2)
    t.reduce(4, 5, 16384)
    if it % 8 == 7:
        t.load(0)                                  # next feature column


def app_fdtd(t, it):                               # D, M, S, A (fdtd-apml, VF 1000)
    ex0, ex1 = 0, 1
    t.load(ex0)
    t.load(ex1)
    t.bbop("sub", o=5, a=ex0, b=ex1)               # clf = Ex - Ex'
    t.load(ex1)
    t.bbop("add", o=5, a=5, b=ex1)                 #     + Ey'
    t.load(ex1)
    t.bbop("sub", o=5, a=5, b=ex1)                 #     - Ey
    t.load(ex0)                                    # cymh/cyph coefficient row
    t.bbop("mul", o=4, a=2, b=ex0)                 # c1 * Bza
    t.bbop("mul", o=ex1, a=5, b=ex0)               # c2 * clf
    t.bbop("sub", o=4, a=4, b=ex1)                 # tmp
    t.load(ex0)
    t.load(ex1)
    t.bbop("div", o=5, a=ex0, b=ex1)               # cxmh / cxph
    t.bbop("mul", o=ex0, a=5, b=3)                 # * Hz
    t.bbop("mul", o=ex1, a=4, b=5)                 # * tmp
    t.bbop("add", o=3, a=ex0, b=ex1)               # Hz = ...
    t.bbop("mul", o=ex1, a=2, b=5)
    t.bbop("sub", o=3, a=3, b=ex1)
    t.copy(2, 4)                                   # Bza = tmp


def app_x264(t, it):                               # A, uint8, VF 64..320
    t.load(it % 2)
    t.bbop("add8", o=2 + it % 3, a=it % 2, b=1 - it % 2)


APPS = {
    # name: (suite, body, Table-3 ops, max VF, groups per app (bodies), note)
    "pca":  ("Phoenix",   app_pca,  "D,S,M,R", 4000, 4000 * 4001 // 2, "cov pairs, 4000x4000"),
    "2mm":  ("PolyBench", app_2mm,  "M,R",     4000, 2 * 4000 * 4000, "NI=NJ=NK=NL=4000"),
    "3mm":  ("PolyBench", app_3mm,  "M,R",     4000, 3 * 4000 * 4000, "NI..NM=4000"),
    "cov":  ("PolyBench", app_cov,  "D,S,R",   4000, 4000 * 4001 // 2, "N=M=4000"),
    "dg":   ("PolyBench", app_dg,   "M,C,R",   1000, 1000 ** 3, "NQ=NR=NP=1000"),
    "fdtd": ("PolyBench", app_fdtd, "D,M,S,A", 1000, 1000 * 1000, "CZ=CYM=CXM=1000 (fdtd-apml, PB 3.2)"),
    "gmm":  ("PolyBench", app_gemm, "M,R",     4000, 4000 * 4000, "NI=NJ=NK=4000"),
    "gs":   ("PolyBench", app_gs,   "M,D,R",   4000, 4000 * 3999 // 2, "NI=NJ=4000"),
    "bp":   ("Rodinia",   app_bp,   "M,R",     134217729, 16 * 2048, "2^27+1 inputs x 16 hidden; 2048 subarray slices"),
    "hw":   ("Rodinia",   app_hw,   "M,R",     2601, None, "groups not derived (input-dependent)"),
    "km":   ("Rodinia",   app_km,   "S,M,R",   16384, 20 * 5 * 34, "20 iters x 5 clusters x 34 features"),
    "x264": ("SPEC2017",  app_x264, "A",       320, None, "groups not derived (input-dependent)"),
}


def build(name: str, window_ns: int) -> dict:
    suite, body, ops, vf, groups, note = APPS[name]
    t = Trace()
    it = 0
    while True:
        snap = (len(t.lines), t.ns, t.n_ap, t.n_aap, t.n_act, dict(t.bbops))
        body(t, it)
        if t.ns >= window_ns:                      # never cross the window edge
            n, t.ns, t.n_ap, t.n_aap, t.n_act, t.bbops = snap
            del t.lines[n:]
            break
        it += 1
    # one-body cost, measured on a fresh trace (for runtime extrapolation)
    probe = Trace()
    for k in range(12):
        body(probe, k)
    body_ns = probe.ns / 12
    mats = max(1, math.ceil(vf / MAT_COLS))
    conc = max(1, MATS_PER_SUBARRAY // min(mats, MATS_PER_SUBARRAY))
    OUT.mkdir(parents=True, exist_ok=True)
    rows_used = sorted({int(x) for ln in t.lines for x in ln.replace(",", " ").split()[1:] if x.isdigit()})
    header = " ".join(str(r) for r in rows_used)
    (OUT / f"{name}.txt").write_text(header + "\n" + "\n".join(t.lines) + "\n")
    info = {"app": name, "suite": suite, "ops": ops, "vf": vf, "mats_per_op": min(mats, MATS_PER_SUBARRAY),
            "concurrent_ops_per_subarray": conc, "bodies_in_window": it, "lines": len(t.lines),
            "ap": t.n_ap, "aap": t.n_aap, "act": t.n_act, "pud_ns": t.ns,
            "bbops": t.bbops, "body_ns": body_ns, "groups": groups, "groups_note": note}
    if groups:
        # one subarray (MIMDRAM's default evaluation) with MIMD across mats
        info["app_pud_s_1subarray"] = groups * body_ns / conc / 1e9
        info["app_windows_1subarray"] = info["app_pud_s_1subarray"] / (window_ns / 1e9)
    return info


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-ms", type=float, default=64.0)
    ap.add_argument("--apps", nargs="*", default=list(APPS))
    args = ap.parse_args()
    window_ns = int(args.window_ms * 1e6)
    infos = []
    for a in args.apps:
        info = build(a, window_ns)
        infos.append(info)
        print(f"{a:5s} bodies={info['bodies_in_window']:6d} lines={info['lines']:8d} "
              f"AP={info['ap']:7d} AAP={info['aap']:7d} ACT={info['act']:8d} "
              f"pud={info['pud_ns']/1e6:6.2f}ms bbops={info['bbops']}")
    (OUT / "apps_info.json").write_text(json.dumps(infos, indent=1))
    print(f"wrote {OUT}/*.txt and apps_info.json")


if __name__ == "__main__":
    main()
