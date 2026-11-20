#!/usr/bin/env python3
"""Config-driven test runner for PuD-Hammulator.

    python3 scripts/autotest.py all     -c configs/experiments.yaml [-e exp1,exp2] [-j N]
    python3 scripts/autotest.py run     ...   # simulate only (resumes; skips finished runs)
    python3 scripts/autotest.py collect ...   # data.csv + summary.csv from finished runs
    python3 scripts/autotest.py plot    ...   # figures from summary/data
    python3 scripts/autotest.py report  ...   # REPORT.md per experiment + INDEX.md
    python3 scripts/autotest.py list    ...   # print the expanded run matrix

The config format is documented in configs/experiments.yaml.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import itertools
import json
import math
import os
import re
import subprocess
import sys
import time
from collections import OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from make_ini import render  # noqa: E402

GEM5_ARGS = [
    "--mem-size", "2GB", "--cpu-clock", "2GHz", "--caches", "--num-cpus", "4",
    "--mem-type=DRAMsim3", "--cpu-type=X86AtomicSimpleCPU", "--repeat-switch", "1",
]
# gem5-x86 lacks palignr: keep glibc on its SSE2 string routines (see README).
GLIBC_TUNABLES = ("glibc.cpu.hwcaps=-SSSE3,-SSE4_1,-SSE4_2,-AVX,-AVX2,"
                  "-AVX512F,-AVX512BW,-AVX512VL,-ERMS,-FSRM,-Fast_Unaligned_Load")
NO_MIT = {"salt_enabled": "false", "prac_abo_enabled": "false",
          "para_enabled": "false", "trr_enabled": "false", "pu_enabled": "false"}

RE_FLIPS = re.compile(r"Total Bitflip Errors \(All Rows\):\s*(\d+)")
RE_AP = re.compile(r"Total AP Operations:\s*(\d+)")
RE_AAP = re.compile(r"Total AAP Operations:\s*(\d+)")
RE_ACT = re.compile(r"Total ACT (?:Operations|Rows):\s+(\d+)")

INK, INK2, GRID = "#2b2b29", "#5f5e58", "#e4e3de"
FALLBACK_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6"]


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
def path_of(p: str) -> Path:
    q = Path(os.path.expanduser(str(p)))
    return q if q.is_absolute() else (ROOT / q)


def ini_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def load_config(path: Path) -> dict:
    cfg = yaml.safe_load(path.read_text())
    for key in ("paths", "models", "mechanisms", "workloads", "experiments"):
        if key not in cfg:
            sys.exit(f"{path}: missing section '{key}'")
    cfg.setdefault("run", {})
    cfg.setdefault("timing", {})
    cfg.setdefault("metric", "sim")
    names = [e["name"] for e in cfg["experiments"]]
    if len(names) != len(set(names)):
        sys.exit(f"{path}: duplicate experiment names")
    return cfg


def resolve_model(cfg: dict, name: str, depth: int = 0) -> dict:
    if name not in cfg["models"]:
        sys.exit(f"unknown model '{name}'")
    if depth > 8:
        sys.exit(f"model inheritance loop at '{name}'")
    m = dict(cfg["models"][name] or {})
    out = resolve_model(cfg, m.pop("base"), depth + 1) if "base" in m else {}
    out.update(m)
    return out


def fmt_val(v) -> str:
    s = ini_value(v)
    return s.rstrip("0").rstrip(".") if re.fullmatch(r"\d+\.\d+", s) else s


def grid(d: dict) -> list[OrderedDict]:
    """{'a': [1, 2], 'b': 3} -> [{'a':1,'b':3}, {'a':2,'b':3}] (order kept)."""
    if not d:
        return [OrderedDict()]
    keys = list(d)
    vals = [v if isinstance(v, list) else [v] for v in d.values()]
    return [OrderedDict(zip(keys, combo)) for combo in itertools.product(*vals)]


def mech_variants(cfg: dict, entry) -> list[dict]:
    """One mechanism entry of an experiment -> list of concrete variants."""
    if isinstance(entry, str):
        name, override = entry, {}
    else:
        entry = dict(entry)
        name, override = entry.pop("name"), entry
    if name not in cfg["mechanisms"]:
        sys.exit(f"unknown mechanism '{name}'")
    m = cfg["mechanisms"][name]
    sweep = dict(m.get("sweep") or {})
    for k, v in override.items():
        sweep[k] = v
    short = m.get("short") or {}
    out = []
    for point in grid(sweep):
        keys = dict(m.get("set") or {})
        keys.update(point)
        for k, expr in (m.get("derive") or {}).items():
            env = {"min": min, "max": max, "int": int, "round": round}
            keys[k] = eval(str(expr), {"__builtins__": {}}, {**env, **point})
        tag = name + "".join(f"_{short.get(k, k)}{fmt_val(v)}" for k, v in point.items())
        out.append({"mech": name, "label": m.get("label", name),
                    "color": m.get("color"), "tag": tag, "keys": keys,
                    "point": dict(point)})
    return out


def expand(cfg: dict, exp: dict) -> list[dict]:
    """Experiment -> list of configs (one per model x mechanism variant x params x seed)."""
    models = exp.get("models") or [exp.get("model", "fixed")]
    seeds = exp.get("seeds") or [0]
    pud_clock = exp.get("pud_clock", True)
    params = exp.get("params") or {}
    swept_params = [k for k, v in params.items() if isinstance(v, list)]
    out = []
    for model in models:
        mkeys = resolve_model(cfg, model)
        for p in grid(params):
            for entry in exp["mechanisms"]:
                for mv in mech_variants(cfg, entry):
                    for seed in seeds:
                        ov = dict(NO_MIT)
                        if pud_clock:
                            ov["time_refresh_resets"] = "false"
                        ov.update({k: ini_value(v) for k, v in mkeys.items()})
                        ov.update({k: ini_value(v) for k, v in p.items()})
                        ov.update({k: ini_value(v) for k, v in mv["keys"].items()})
                        ov["rowhammer_seed"] = str(seed)
                        parts = []
                        if len(models) > 1:
                            parts.append(model)
                        parts += [f"{k}{fmt_val(p[k])}" for k in swept_params]
                        parts.append(mv["tag"])
                        group = "-".join(parts)          # identity without the seed
                        out.append({
                            "name": f"{group}_s{seed}", "group": group, "model": model,
                            "mech": mv["mech"], "mech_label": mv["label"],
                            "color": mv["color"], "mech_point": mv["point"],
                            "params": {k: p[k] for k in swept_params}, "seed": seed,
                            "overrides": ov, "pud_clock": pud_clock,
                        })
    names = [c["name"] for c in out]
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        sys.exit(f"experiment {exp['name']}: duplicate config names {sorted(dup)}")
    return out


def workload_traces(cfg: dict, wname: str) -> list[Path]:
    if wname not in cfg["workloads"]:
        sys.exit(f"unknown workload '{wname}'")
    w = cfg["workloads"][wname]
    d = path_of(w["trace_dir"])
    if not d.is_dir() or not any(d.glob("*.txt")):
        if w.get("build"):
            print(f"[workload {wname}] {d} missing -> {w['build']}", flush=True)
            subprocess.check_call(w["build"], shell=True, cwd=str(ROOT))
        if not d.is_dir():
            sys.exit(f"workload '{wname}': trace dir {d} does not exist")
    traces = sorted(p for p in d.glob("*.txt") if p.name != "apps_info.json")
    inc, exc = w.get("include") or [], w.get("exclude") or []
    if inc:
        traces = [t for t in traces if any(s in t.name for s in inc)]
    traces = [t for t in traces if not any(s in t.name for s in exc)]
    if not traces:
        sys.exit(f"workload '{wname}': no traces after filters in {d}")
    return traces


def selected(cfg: dict, only: list[str] | None) -> list[dict]:
    if only:
        known = {e["name"] for e in cfg["experiments"]}
        bad = [o for o in only if o not in known]
        if bad:
            sys.exit(f"unknown experiment(s): {bad}; known: {sorted(known)}")
        return [e for e in cfg["experiments"] if e["name"] in only]
    return [e for e in cfg["experiments"] if e.get("enabled", True)]


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
def git_state(repo: Path) -> str:
    try:
        h = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                                    text=True, stderr=subprocess.DEVNULL).strip()
        dirty = subprocess.call(["git", "-C", str(repo), "diff", "--quiet", "HEAD"],
                                stderr=subprocess.DEVNULL) != 0
        return h + ("-dirty" if dirty else "")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def one_run(cfg: dict, exp_dir: Path, c: dict, trace: Path, debug: bool) -> tuple[str, float, int, str]:
    hamm = path_of(cfg["paths"]["hammulator"])
    run_dir = exp_dir / "runs" / c["name"] / trace.stem
    tag = f"{c['name']}/{trace.stem}"
    sig = hashlib.sha1(json.dumps({"ov": c["overrides"], "trace": str(trace),
                                   "window": cfg["run"].get("pud_window_ns", 64_000_000)},
                                  sort_keys=True).encode()).hexdigest()[:12]
    done = run_dir / "m5out" / "rh_mitigation.txt"
    sig_file = run_dir / "params.sig"
    if done.exists():
        if sig_file.exists() and sig_file.read_text().strip() == sig:
            return tag, 0.0, 0, "skipped"
        stale = run_dir.with_name(f"{trace.stem}.stale-{int(time.time())}")
        run_dir.rename(stale)                     # parameters changed: keep old, re-run
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "m5out").mkdir(exist_ok=True)
    rep_dir = run_dir / "report"
    rep_dir.mkdir(exist_ok=True)
    ini = run_dir / "dram.ini"
    ini.write_text(render(path_of(cfg["paths"]["base_ini"]).read_text(), c["overrides"]))
    (run_dir / "params.json").write_text(json.dumps(
        {k: c[k] for k in ("name", "model", "mech", "mech_point", "params", "seed", "overrides")},
        indent=1, default=str))
    window = cfg["run"].get("pud_window_ns", 64_000_000) if c["pud_clock"] else 0
    ov = c["overrides"]
    env_file = run_dir / "env.txt"
    env_file.write_text(
        f"GLIBC_TUNABLES={GLIBC_TUNABLES}\nRH_REPORT_DIR={rep_dir}\nRH_TAG={c['name']}\n"
        f"RH_RUN_TMR=1\nRH_PUD_WINDOW_NS={window}\n"
        + "".join(f"RH_{k.upper()}={1 if ov.get(k) == 'true' else 0}\n"
                  for k in ("salt_enabled", "prac_abo_enabled", "para_enabled", "trr_enabled")))
    cmd = [str(hamm / "build/X86/gem5.opt"), f"--outdir={run_dir / 'm5out'}"]
    if debug:
        cmd += ["--debug-file=debug", "--debug-flags=PuDHammer"]
    cmd += [str(hamm / "gem5/configs/example/se.py"), *GEM5_ARGS, f"--env={env_file}",
            f"--cmd={hamm / 'build/tmp_root/verify'}", f"--options={trace}"]
    env = dict(os.environ, PUD_DRAMSIM3_INI=str(ini))
    t0 = time.time()
    with open(run_dir / "gem5.log", "w") as log:
        rc = subprocess.call(cmd, cwd=str(hamm), stdout=log, stderr=subprocess.STDOUT, env=env)
    if rc == 0 and done.exists():
        sig_file.write_text(sig)
        status = "ok"
    else:
        status = "failed"
        rc = rc or 1
    return tag, time.time() - t0, rc, status


def cmd_run(cfg: dict, exps: list[dict], jobs: int, dry: bool) -> None:
    hamm = path_of(cfg["paths"]["hammulator"])
    for p in ("build/X86/gem5.opt", "gem5/configs/example/se.py", "build/tmp_root/verify"):
        if not (hamm / p).exists():
            sys.exit(f"missing {hamm / p}; run sim/rebuild.sh first")
    results = path_of(cfg["paths"]["results"])
    work = []
    for exp in exps:
        configs = expand(cfg, exp)
        traces = workload_traces(cfg, exp["workload"])
        exp_dir = results / exp["name"]
        exp_dir.mkdir(parents=True, exist_ok=True)
        print(f"[{exp['name']}] {len(configs)} configs x {len(traces)} traces = "
              f"{len(configs) * len(traces)} runs")
        for c in configs:
            for t in traces:
                work.append((exp, exp_dir, c, t))
    if dry:
        for exp, _, c, t in work:
            print(f"  {exp['name']:16s} {c['name']:34s} {t.name}")
        return
    print(f"total {len(work)} runs, -j{jobs}", flush=True)
    t0 = time.time()
    status: dict[str, dict] = defaultdict(lambda: {"ok": 0, "skipped": 0, "failed": []})
    debug = bool(cfg["run"].get("debug", False))
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        futs = {ex.submit(one_run, cfg, ed, c, t, debug): e["name"] for e, ed, c, t in work}
        for i, f in enumerate(as_completed(futs), 1):
            tag, dt_s, rc, st = f.result()
            s = status[futs[f]]
            if st == "failed":
                s["failed"].append(tag)
            else:
                s[st] += 1
            if st == "failed" or i % 25 == 0 or i == len(work):
                print(f"[{i}/{len(work)}] {futs[f]}:{tag} {st} {dt_s:.0f}s "
                      f"(elapsed {time.time() - t0:.0f}s)", flush=True)
    for exp in exps:
        s = status[exp["name"]]
        write_manifest(cfg, exp, results / exp["name"], s)
        print(f"[{exp['name']}] ok={s['ok']} skipped={s['skipped']} failed={len(s['failed'])}")
        for t in s["failed"]:
            print(f"    FAILED {t}  (see runs/{t}/gem5.log)")


def write_manifest(cfg: dict, exp: dict, exp_dir: Path, status: dict) -> None:
    hamm = path_of(cfg["paths"]["hammulator"])
    man = {
        "experiment": exp, "finished": dt.datetime.now().isoformat(timespec="seconds"),
        "git": {"hammulator": git_state(hamm), "gem5": git_state(hamm / "gem5"),
                "DRAMsim3": git_state(hamm / "gem5/ext/dramsim3/DRAMsim3"),
                "workflow": git_state(ROOT)},
        "models": {m: resolve_model(cfg, m) for m in (exp.get("models") or [exp.get("model", "fixed")])},
        "mechanisms": {e if isinstance(e, str) else e["name"]:
                       cfg["mechanisms"][e if isinstance(e, str) else e["name"]]
                       for e in exp["mechanisms"]},
        "workload": cfg["workloads"][exp["workload"]], "timing": cfg["timing"],
        "run": cfg["run"], "status": status,
    }
    (exp_dir / "manifest.json").write_text(json.dumps(man, indent=1, default=str))


# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------
def parse_kv(path: Path) -> dict[str, str]:
    d: dict[str, str] = {}
    if path.exists():
        for ln in path.read_text().splitlines():
            if "=" in ln:
                k, v = ln.split("=", 1)
                d[k.strip()] = v.strip()
    return d


def parse_report(rep_dir: Path) -> dict[str, int] | None:
    reps = sorted(rep_dir.glob("*-report.txt"))
    if not reps:
        return None
    txt = reps[-1].read_text(errors="replace")
    g = lambda rx: int(rx.search(txt).group(1)) if rx.search(txt) else 0
    return {"flips_harness": g(RE_FLIPS), "ap": g(RE_AP), "aap": g(RE_AAP), "act_rows": g(RE_ACT)}


def costs(cfg: dict, mit: dict[str, str], rep: dict) -> tuple[float, float, float]:
    t = {"t_ap": 46, "t_aap": 60, "t_act": 50, "t_rc": 46, "t_abo": 180, **cfg["timing"]}
    f = lambda k: float(mit.get(k, 0) or 0)
    pud = t["t_ap"] * rep["ap"] + t["t_aap"] * rep["aap"] + t["t_act"] * rep["act_rows"]
    mit_ns = (f("prac_abos") * (2 * f("prac_blast_radius") * t["t_rc"] + t["t_abo"])
              + f("salt_rows_refreshed") * t["t_rc"] + f("para_refreshes") * t["t_rc"]
              + f("pu_mitigations") * t["t_rc"])
    events = (f("prac_abos") + f("salt_rows_refreshed") + f("para_refreshes")
              + f("pu_mitigations"))
    return pud, mit_ns, events


def cmd_collect(cfg: dict, exps: list[dict]) -> None:
    results = path_of(cfg["paths"]["results"])
    for exp in exps:
        exp_dir = results / exp["name"]
        configs = {c["name"]: c for c in expand(cfg, exp)}
        rows = []
        for cname, c in configs.items():
            cdir = exp_dir / "runs" / cname
            if not cdir.is_dir():
                continue
            for run in sorted(p for p in cdir.iterdir() if p.is_dir() and ".stale-" not in p.name):
                mit = parse_kv(run / "m5out" / "rh_mitigation.txt")
                rep = parse_report(run / "report")
                if not mit or rep is None:
                    continue
                pud, mit_ns, events = costs(cfg, mit, rep)
                sim = int(mit.get("bits_flipped", 0))
                rows.append({
                    "experiment": exp["name"], "config": cname, "group": c["group"],
                    "model": c["model"], "mechanism": c["mech"], "mech_label": c["mech_label"],
                    **{f"m_{k}": v for k, v in c["mech_point"].items()},
                    **{f"p_{k}": v for k, v in c["params"].items()},
                    "seed": c["seed"], "trace": run.name,
                    "flips": sim if cfg["metric"] == "sim" else rep["flips_harness"],
                    "flips_sim": sim, "flips_harness": rep["flips_harness"],
                    "quadwords_flipped": int(mit.get("quadwords_flipped", 0)),
                    "activations": int(mit.get("trace_activations", 0)),
                    "ap": rep["ap"], "aap": rep["aap"], "act_rows": rep["act_rows"],
                    "pud_ms": pud / 1e6, "mitigation_events": int(events),
                    "mitigation_ms": mit_ns / 1e6,
                    "overhead_pct": 100.0 * mit_ns / pud if pud else 0.0,
                    "window_resets": int(mit.get("pud_window_resets", 0)),
                    "double_sided_hits": int(mit.get("double_sided_hits", 0)),
                })
        if not rows:
            print(f"[{exp['name']}] no finished runs")
            continue
        write_csv(exp_dir / "data.csv", rows)
        summ = summarize(exp, list(configs.values()), rows)
        write_csv(exp_dir / "summary.csv", summ)
        print(f"[{exp['name']}] {len(rows)} runs -> data.csv, {len(summ)} configs -> summary.csv")


def summarize(exp: dict, configs: list[dict], rows: list[dict]) -> list[dict]:
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by[r["config"]].append(r)
    n_traces = max(len(v) for v in by.values())
    # baseline = mechanism "none" with the same model, params and seed
    base = {}
    for c in configs:
        if c["mech"] == "none" and c["name"] in by:
            base[(c["model"], json.dumps(c["params"], sort_keys=True), c["seed"])] = \
                sum(r["flips"] for r in by[c["name"]])
    out = []
    for c in configs:
        rs = by.get(c["name"])
        if not rs:
            continue
        flips = sum(r["flips"] for r in rs)
        pud = sum(r["pud_ms"] for r in rs)
        mit = sum(r["mitigation_ms"] for r in rs)
        b = base.get((c["model"], json.dumps(c["params"], sort_keys=True), c["seed"]))
        out.append({
            "config": c["name"], "group": c["group"], "model": c["model"],
            "mechanism": c["mech"], "mech_label": c["mech_label"],
            **{f"m_{k}": v for k, v in c["mech_point"].items()},
            **{f"p_{k}": v for k, v in c["params"].items()},
            "seed": c["seed"], "n_traces": len(rs), "complete": len(rs) == n_traces,
            "flips": flips, "norm_flips": flips / b if b else "",
            "zero_flip_traces": sum(1 for r in rs if r["flips"] == 0),
            "mitigation_events": sum(r["mitigation_events"] for r in rs),
            "overhead_pct": 100.0 * mit / pud if pud else 0.0,
            "overhead_pct_mean": sum(r["overhead_pct"] for r in rs) / len(rs),
            "pud_ms": pud,
        })
    return out


def write_csv(path: Path, rows: list[dict]) -> None:
    keys: list[str] = []
    for r in rows:
        keys += [k for k in r if k not in keys]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path) as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# plot
# ---------------------------------------------------------------------------
def seed_mean(rows: list[dict], key: str, group_key: str = "group") -> dict[str, tuple[float, float, dict]]:
    """group -> (mean, std over seeds, first row)."""
    acc: dict[str, list] = defaultdict(list)
    first: dict[str, dict] = {}
    for r in rows:
        if r.get(key, "") == "":
            continue
        acc[r[group_key]].append(float(r[key]))
        first.setdefault(r[group_key], r)
    out = {}
    for g, v in acc.items():
        m = sum(v) / len(v)
        sd = math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1)) if len(v) > 1 else 0.0
        out[g] = (m, sd, first[g])
    return out


def style(ax, xgrid: bool = False) -> None:
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(INK2)
    ax.tick_params(colors=INK2, labelsize=8)
    ax.yaxis.grid(True, color=GRID, lw=0.8)
    if xgrid:
        ax.xaxis.grid(True, color=GRID, lw=0.8)
    ax.set_axisbelow(True)


def colour_map(cfg: dict, mechs: list[str]) -> dict[str, str]:
    out, k = {}, 0
    for m in mechs:
        col = (cfg["mechanisms"].get(m) or {}).get("color")
        if not col:
            col = FALLBACK_COLORS[k % len(FALLBACK_COLORS)]
            k += 1
        out[m] = col
    return out


def plot_pareto(cfg, exp, summ, fig_dir, plt) -> Path | None:
    pts = seed_mean(summ, "norm_flips")
    ovh = seed_mean(summ, "overhead_pct")
    by_mech: dict[str, list] = defaultdict(list)
    for g, (y, sd, r) in pts.items():
        if r["mechanism"] == "none" or g not in ovh:
            continue
        by_mech[r["mechanism"]].append((ovh[g][0], y, sd, r))
    if not by_mech:
        return None
    cols = colour_map(cfg, list(by_mech))
    fig, ax = plt.subplots(figsize=(6.8, 4.2))
    xs_all = []
    for m, p in by_mech.items():
        p.sort(key=lambda t: t[0])
        xs = [max(t[0], 0.05) for t in p]
        xs_all += xs
        ys = [t[1] for t in p]
        ax.errorbar(xs, ys, yerr=[t[2] for t in p], color=cols[m], lw=2, marker="o", ms=6,
                    mec="white", mew=1.5, capsize=2, label=p[0][3]["mech_label"], zorder=3)
        ax.annotate(p[-1][3]["mech_label"], (xs[-1], ys[-1]), textcoords="offset points",
                    xytext=(6, 2), fontsize=7.5, color=INK)
    style(ax, xgrid=True)
    if xs_all and max(xs_all) / max(min(xs_all), 1e-9) > 30:
        ax.set_xscale("log")
    ax.set_ylim(-0.05, 1.12)
    ax.axhline(1.0, color=INK2, lw=0.6, ls=":")
    ax.set_xlabel("Mitigation time (% of PuD time)", color=INK, fontsize=9)
    ax.set_ylabel("Residual bit flips (fraction of no mitigation)", color=INK, fontsize=9)
    ax.legend(fontsize=7.5, frameon=False, loc="upper right")
    ax.set_title(f"{exp['name']}: flips vs. overhead", fontsize=10, color=INK, loc="left")
    fig.tight_layout()
    out = fig_dir / "pareto.png"
    fig.savefig(out, dpi=200)
    plt.close(fig)
    return out


def plot_bars(cfg, exp, data, fig_dir, plt, key: str, fname: str, ylabel: str, log: bool) -> Path | None:
    rows = [r for r in data if not (key == "overhead_pct" and r["mechanism"] == "none")]
    if not rows:
        return None
    # seed-mean per (group, trace)
    acc: dict[tuple, list] = defaultdict(list)
    meta: dict[str, dict] = {}
    for r in rows:
        acc[(r["group"], r["trace"])].append(float(r[key]))
        meta.setdefault(r["group"], r)
    groups = list(dict.fromkeys(r["group"] for r in rows))
    traces = list(dict.fromkeys(r["trace"] for r in rows))
    if len(groups) > 9:
        return plot_heatmap(cfg, exp, data, fig_dir, plt, key, fname, ylabel)
    mechs = list(dict.fromkeys(meta[g]["mechanism"] for g in groups))
    cols = colour_map(cfg, mechs)
    hatches = ["", "//", "..", "xx", "\\\\", "++"]
    hidx: dict[str, int] = defaultdict(int)
    hatch_of = {}
    for g in groups:
        m = meta[g]["mechanism"]
        hatch_of[g] = hatches[hidx[m] % len(hatches)]
        hidx[m] += 1
    w = 0.8 / len(groups)
    fig, ax = plt.subplots(figsize=(max(7, 0.9 * len(traces) + 2), 3.8))
    for j, g in enumerate(groups):
        xs = [i - 0.4 + w * (j + 0.5) for i in range(len(traces))]
        ys = []
        for t in traces:
            v = acc.get((g, t))
            y = sum(v) / len(v) if v else 0.0
            ys.append(max(y, 0.5) if log else y)
        ax.bar(xs, ys, width=w * 0.86, color=cols[meta[g]["mechanism"]], hatch=hatch_of[g],
               edgecolor="white", lw=0.6, label=g)
    style(ax)
    ax.set_xticks(range(len(traces)))
    ax.set_xticklabels([t.replace("-plus", "") for t in traces], rotation=45 if len(traces) > 8 else 0,
                       ha="right" if len(traces) > 8 else "center", fontsize=7.5, color=INK)
    if log:
        ax.set_yscale("log")
        ax.set_ylim(0.4, None)
        ax.axhline(0.5, color=INK2, lw=0.6, ls=":")
    ax.set_ylabel(ylabel, color=INK, fontsize=9)
    ax.legend(ncol=min(5, len(groups)), fontsize=7, frameon=False, loc="lower center",
              bbox_to_anchor=(0.5, 1.0))
    if log:
        ax.text(-0.45, 0.55, "0 flips", fontsize=6.5, color=INK2, ha="left", va="bottom")
    fig.tight_layout()
    out = fig_dir / fname
    fig.savefig(out, dpi=200)
    plt.close(fig)
    return out


def plot_heatmap(cfg, exp, data, fig_dir, plt, key="flips", fname="heatmap.png",
                 label="Bit flips (seed mean)") -> Path | None:
    import matplotlib.colors as mcolors
    import numpy as np
    acc: dict[tuple, list] = defaultdict(list)
    for r in data:
        acc[(r["group"], r["trace"])].append(float(r[key]))
    groups = list(dict.fromkeys(r["group"] for r in data))
    traces = list(dict.fromkeys(r["trace"] for r in data))
    if not groups:
        return None
    M = np.array([[sum(acc[(g, t)]) / len(acc[(g, t)]) if acc.get((g, t)) else np.nan
                   for t in traces] for g in groups])
    fig, ax = plt.subplots(figsize=(max(6, 0.32 * len(traces) + 3), max(3, 0.24 * len(groups) + 1.5)))
    vmax = np.nanmax(M) if np.isfinite(np.nanmax(M)) else 1
    norm = mcolors.SymLogNorm(linthresh=1, vmin=0, vmax=max(vmax, 1)) if key == "flips" else None
    im = ax.imshow(M, aspect="auto", cmap="Blues", norm=norm)
    ax.set_xticks(range(len(traces)))
    ax.set_xticklabels([t.replace("-plus", "") for t in traces], rotation=60, ha="right",
                       fontsize=6.5, color=INK)
    ax.set_yticks(range(len(groups)))
    ax.set_yticklabels(groups, fontsize=6.5, color=INK)
    cb = fig.colorbar(im, ax=ax, fraction=0.03)
    cb.set_label(label, fontsize=8, color=INK)
    cb.ax.tick_params(labelsize=7, colors=INK2)
    ax.set_title(f"{exp['name']}: {label}", fontsize=10, color=INK, loc="left")
    fig.tight_layout()
    out = fig_dir / fname
    fig.savefig(out, dpi=200)
    plt.close(fig)
    return out


def plot_totals(cfg, exp, summ, fig_dir, plt) -> Path | None:
    f = seed_mean(summ, "flips")
    if not f:
        return None
    groups = list(f)
    mechs = list(dict.fromkeys(f[g][2]["mechanism"] for g in groups))
    cols = colour_map(cfg, mechs)
    fig, ax = plt.subplots(figsize=(max(5.5, 0.55 * len(groups) + 2), 3.8))
    ys = [f[g][0] for g in groups]
    sds = [f[g][1] for g in groups]
    ax.bar(range(len(groups)), ys, yerr=sds if any(sds) else None, width=0.7,
           color=[cols[f[g][2]["mechanism"]] for g in groups], edgecolor="white", capsize=2,
           error_kw={"ecolor": INK2, "lw": 0.8})
    for i, (y, sd) in enumerate(zip(ys, sds)):
        ax.annotate(f"{y:.0f}", (i, y + sd), textcoords="offset points", xytext=(0, 3),
                    ha="center", va="bottom", fontsize=7, color=INK)
    ax.set_ylim(0, max(ys + [1]) * 1.12)
    style(ax)
    ax.set_xticks(range(len(groups)))
    ax.set_xticklabels(groups, rotation=35, ha="right", fontsize=7.5, color=INK)
    ax.set_ylabel("Total bit flips over all traces", color=INK, fontsize=9)
    ax.set_title(f"{exp['name']}", fontsize=10, color=INK, loc="left")
    fig.tight_layout()
    out = fig_dir / "totals_bar.png"
    fig.savefig(out, dpi=200)
    plt.close(fig)
    return out


def cmd_plot(cfg: dict, exps: list[dict]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    results = path_of(cfg["paths"]["results"])
    for exp in exps:
        exp_dir = results / exp["name"]
        data, summ = read_csv(exp_dir / "data.csv"), read_csv(exp_dir / "summary.csv")
        if not data:
            print(f"[{exp['name']}] nothing to plot (run collect first)")
            continue
        fig_dir = exp_dir / "figures"
        fig_dir.mkdir(exist_ok=True)
        made = []
        for p in exp.get("plots") or ["totals_bar"]:
            if p == "pareto":
                made.append(plot_pareto(cfg, exp, summ, fig_dir, plt))
            elif p == "flips_bar":
                made.append(plot_bars(cfg, exp, data, fig_dir, plt, "flips", "flips_bar.png",
                                      "Bit flips (log, seed mean)", True))
            elif p == "overhead_bar":
                made.append(plot_bars(cfg, exp, data, fig_dir, plt, "overhead_pct",
                                      "overhead_bar.png", "Mitigation time (% of PuD time)", False))
            elif p == "heatmap":
                made.append(plot_heatmap(cfg, exp, data, fig_dir, plt))
            elif p == "totals_bar":
                made.append(plot_totals(cfg, exp, summ, fig_dir, plt))
            else:
                print(f"[{exp['name']}] unknown plot type '{p}'")
        made = [m for m in made if m]
        print(f"[{exp['name']}] figures: {', '.join(m.name for m in made) or 'none'}")


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def cmd_report(cfg: dict, exps: list[dict]) -> None:
    results = path_of(cfg["paths"]["results"])
    for exp in exps:
        exp_dir = results / exp["name"]
        summ = read_csv(exp_dir / "summary.csv")
        if not summ:
            continue
        man = json.loads((exp_dir / "manifest.json").read_text()) if (exp_dir / "manifest.json").exists() else {}
        flips = seed_mean(summ, "flips")
        norm = seed_mean(summ, "norm_flips")
        ovh = seed_mean(summ, "overhead_pct")
        nt = {r["group"]: r["n_traces"] for r in summ}
        zero = seed_mean(summ, "zero_flip_traces")
        seeds = sorted({r["seed"] for r in summ})
        L = [f"# {exp['name']}", "", exp.get("description", ""), "",
             f"- Workload: `{exp['workload']}`; seeds: {', '.join(seeds)}; "
             f"PuD clock: {exp.get('pud_clock', True)}; metric: {cfg['metric']} flips",
             f"- Generated: {dt.datetime.now().isoformat(timespec='seconds')}; "
             f"git: {json.dumps(man.get('git', {}))}"]
        st = man.get("status")
        if st:
            L.append(f"- Last run: ok={st.get('ok')} skipped={st.get('skipped')} "
                     f"failed={len(st.get('failed', []))}")
        L += ["", "| config | mechanism | traces | flips (mean ± sd) | norm. flips | traces with 0 flips | overhead |",
              "|---|---|---|---|---|---|---|"]
        for g, (m, sd, r) in flips.items():
            fl = f"{m:.0f}" + (f" ± {sd:.0f}" if len(seeds) > 1 else "")
            nf = f"{norm[g][0]:.3f}" if g in norm else "–"
            zf = f"{zero[g][0]:.0f}" if g in zero else ""
            ov = f"{ovh[g][0]:.1f}%" if g in ovh else ""
            L.append(f"| {g} | {r['mech_label']} | {nt.get(g, '')} | {fl} | {nf} | {zf} | {ov} |")
        figs = sorted((exp_dir / "figures").glob("*.png")) if (exp_dir / "figures").exists() else []
        if figs:
            L += [""] + [f"![{p.stem}](figures/{p.name})" for p in figs]
        L += ["", "Raw data: `data.csv` (one row per run), `summary.csv` (one row per config), "
              "`manifest.json` (exact parameters and git commits)."]
        (exp_dir / "REPORT.md").write_text("\n".join(L) + "\n")
    # index over everything present in the results dir
    rows = ["# Automated test results", "",
            "| experiment | description | configs | runs | report |", "|---|---|---|---|---|"]
    known = {e["name"]: e for e in cfg["experiments"]}
    for d in sorted(p for p in results.iterdir() if p.is_dir()):
        if not (d / "summary.csv").exists():
            continue
        s, data = read_csv(d / "summary.csv"), read_csv(d / "data.csv")
        desc = known.get(d.name, {}).get("description", "")
        rows.append(f"| {d.name} | {desc} | {len(s)} | {len(data)} | [REPORT]({d.name}/REPORT.md) |")
    (results / "INDEX.md").write_text("\n".join(rows) + "\n")
    print(f"reports written; index: {results / 'INDEX.md'}")


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["all", "run", "collect", "plot", "report", "list"])
    ap.add_argument("-c", "--config", type=Path, default=ROOT / "configs/experiments.yaml")
    ap.add_argument("-e", "--experiments", default="", help="comma-separated names (default: enabled)")
    ap.add_argument("-j", "--jobs", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cfg = load_config(args.config)
    exps = selected(cfg, [e for e in args.experiments.split(",") if e])
    if not exps:
        sys.exit("no experiments selected (all disabled? use -e NAME)")
    jobs = args.jobs or int(cfg["run"].get("jobs", os.cpu_count() or 4))
    if args.command == "list":
        cmd_run(cfg, exps, jobs, dry=True)
        return
    if args.command in ("all", "run"):
        cmd_run(cfg, exps, jobs, args.dry_run)
        if args.dry_run:
            return
    if args.command in ("all", "collect"):
        cmd_collect(cfg, exps)
    if args.command in ("all", "plot"):
        cmd_plot(cfg, exps)
    if args.command in ("all", "report"):
        cmd_report(cfg, exps)


if __name__ == "__main__":
    main()
