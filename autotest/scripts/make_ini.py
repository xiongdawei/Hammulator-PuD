#!/usr/bin/env python3
"""Generate a DRAMsim3 ini from the base profile plus key=value overrides.

Only the [rowhammer] section is touched. Keys missing from the base are
appended to that section, so new knobs never need a base-file edit.

    python3 make_ini.py --out /tmp/x.ini prac_abo_enabled=true prac_threshold=20
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE_INI = HERE.parent / "configs" / "base.ini"


def render(base_text: str, overrides: dict[str, str]) -> str:
    lines = base_text.splitlines()
    out: list[str] = []
    in_rh = False
    seen: set[str] = set()
    pending = dict(overrides)
    for ln in lines:
        stripped = ln.strip()
        if stripped.startswith("["):
            if in_rh:
                # leaving [rowhammer]: append keys not present in the base
                for k, v in pending.items():
                    if k not in seen:
                        out.append(f"{k} = {v}")
                        seen.add(k)
                out.append("")
            in_rh = stripped.lower() == "[rowhammer]"
            out.append(ln)
            continue
        if in_rh:
            m = re.match(r"^\s*([A-Za-z0-9_]+)\s*=", ln)
            if m and m.group(1) in pending:
                k = m.group(1)
                out.append(f"{k} = {pending[k]}")
                seen.add(k)
                continue
        out.append(ln)
    if in_rh:
        for k, v in pending.items():
            if k not in seen:
                out.append(f"{k} = {v}")
    return "\n".join(out) + "\n"


def make_ini(out_path: Path, overrides: dict[str, str], base: Path = BASE_INI) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render(base.read_text(), overrides))
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", type=Path, default=BASE_INI)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("overrides", nargs="*", help="key=value")
    args = ap.parse_args()
    ov = dict(kv.split("=", 1) for kv in args.overrides)
    make_ini(args.out, ov, args.base)
    print(args.out)


if __name__ == "__main__":
    main()
