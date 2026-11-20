#!/usr/bin/env python3
"""Row-layout optimisation for the compute region (Phase 4).

The replay harness (rh_core.c) pins 13 special rows in this physical order:
    C0 C1 T0 T1 T2 T3 B_DCC1 B_DCC1N B_DCC0 B_DCC0N R0 R1 R2
A "rearranged" trace renames every logical row to the slot it should occupy,
so the harness hammers the new physical neighbours. This script produces
such traces for several layout policies and reports a layout cost that
mirrors the simulator's physics:

    D(v)    = h[v-1] + h[v+1] + (ds-1) * min(h[v-1], h[v+1])   disturbance on v
    cost    = sum_v D(v) / (1 + h[v])                            hot rows self-refresh

Hardware constraint: dual-contact pairs (B_DCC0/B_DCC0N, B_DCC1/B_DCC1N) must stay
adjacent and in order. Everything else may move.

Policies:
    orig    the harness order (what the thesis called "linear allocation")
    thesis  the Fig. 9 order from the thesis
    sa      simulated annealing on the cost above (near-optimal)

    python3 rearrange_traces.py --src <hammulator>/progs/verify/microworkloads \
                                --dst traces/rearranged
"""
from __future__ import annotations

import argparse
import csv
import math
import random
from collections import Counter
from pathlib import Path

SLOTS = ["C0", "C1", "T0", "T1", "T2", "T3", "B_DCC1", "B_DCC1N",
         "B_DCC0", "B_DCC0N", "R0", "R1", "R2"]
PAIRS = [("B_DCC0", "B_DCC0N"), ("B_DCC1", "B_DCC1N")]
THESIS_ORDER = ["T1", "T2", "T3", "B_DCC1", "B_DCC1N", "T0", "C0",
                "B_DCC0", "B_DCC0N", "C1", "R0", "R1", "R2"]
DS_MULT = 3.0


def parse_trace(path: Path) -> tuple[str, list[tuple[str, list[str]]]]:
    lines = path.read_text().splitlines()
    header, body = lines[0], lines[1:]
    ops = []
    for ln in body:
        toks = ln.replace(",", " ").split()
        if not toks:
            continue
        ops.append((toks[0], toks[1:]))
    return header, ops


def heat_of(ops: list[tuple[str, list[str]]]) -> Counter:
    h: Counter = Counter()
    for _, rows in ops:
        for r in rows:
            if r in SLOTS:
                h[r] += 1
    return h


def layout_cost(order: list[str], heat: Counter) -> float:
    n = len(order)
    h = [heat.get(r, 0) for r in order]
    cost = 0.0
    for v in range(n):
        left = h[v - 1] if v > 0 else 0
        right = h[v + 1] if v + 1 < n else 0
        d = left + right + (DS_MULT - 1.0) * min(left, right)
        cost += d / (1.0 + h[v])
    return cost


def valid(order: list[str]) -> bool:
    for a, b in PAIRS:
        ia, ib = order.index(a), order.index(b)
        if ib != ia + 1:
            return False
    return True


def anneal(heat: Counter, seed: int = 1, iters: int = 20000) -> list[str]:
    rng = random.Random(seed)
    # work on "units": DCC pairs move as a block
    units = [["C0"], ["C1"], ["T0"], ["T1"], ["T2"], ["T3"],
             ["B_DCC1", "B_DCC1N"], ["B_DCC0", "B_DCC0N"], ["R0"], ["R1"], ["R2"]]
    flat = lambda us: [r for u in us for r in u]
    cur = units[:]
    cur_c = layout_cost(flat(cur), heat)
    best, best_c = cur[:], cur_c
    T0 = max(1.0, cur_c * 0.1)
    for i in range(iters):
        T = T0 * (1.0 - i / iters) + 1e-3
        a, b = rng.sample(range(len(cur)), 2)
        nxt = cur[:]
        nxt[a], nxt[b] = nxt[b], nxt[a]
        c = layout_cost(flat(nxt), heat)
        if c < cur_c or rng.random() < math.exp((cur_c - c) / T):
            cur, cur_c = nxt, c
            if c < best_c:
                best, best_c = nxt[:], c
    return flat(best)


def rewrite(ops, mapping: dict[str, str]) -> list[str]:
    out = []
    for cmd, rows in ops:
        new = [mapping.get(r, r) for r in rows]
        if cmd == "AAP":
            # keep "AAP dst.., src" shape: last token is the source
            out.append(f"AAP {' '.join(new[:-1])}, {new[-1]}")
        else:
            out.append(f"{cmd} {' '.join(new)}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--dst", type=Path, required=True)
    ap.add_argument("--policies", nargs="*", default=["orig", "thesis", "sa"])
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    summary = []
    for trace in sorted(args.src.glob("*.txt")):
        if trace.name.startswith("99_"):
            continue
        header, ops = parse_trace(trace)
        heat = heat_of(ops)
        layouts = {"orig": SLOTS[:], "thesis": THESIS_ORDER[:]}
        if "sa" in args.policies:
            # several restarts; keep the best (the landscape is small but bumpy)
            cands = [anneal(heat, args.seed + k) for k in range(8)]
            layouts["sa"] = min(cands, key=lambda o: layout_cost(o, heat))
        row = {"trace": trace.stem}
        for pol in args.policies:
            order = layouts[pol]
            assert valid(order), (pol, order)
            # logical row r goes to physical slot SLOTS[order.index(r)]
            mapping = {r: SLOTS[order.index(r)] for r in SLOTS}
            out_dir = args.dst / pol
            out_dir.mkdir(parents=True, exist_ok=True)
            with open(out_dir / trace.name, "w") as f:
                f.write(header + "\n")
                f.write("\n".join(rewrite(ops, mapping)) + "\n")
            row[f"cost_{pol}"] = round(layout_cost(order, heat), 2)
            row[f"order_{pol}"] = " ".join(order)
        summary.append(row)
        print(f"{trace.stem:32s} " + "  ".join(
            f"{p}={row[f'cost_{p}']:.1f}" for p in args.policies))

    args.dst.mkdir(parents=True, exist_ok=True)
    with open(args.dst / "layout_summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
        w.writeheader()
        w.writerows(summary)
    print(f"wrote {args.dst / 'layout_summary.csv'}")


if __name__ == "__main__":
    main()
