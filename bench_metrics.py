"""
Benchmark runner that:
- generates a case (runtime or text)
- runs Interpreter with toggles (vtable on/off, gc on/off, cache on/off)
- collects metrics into CSV + Markdown table.

Usage (runtime generator):
  python bench_metrics.py --mode runtime --case chain --n-classes 300 --calls 50000 --out metrics

Usage (text program file):
  python bench_metrics.py --mode text --program tests/chain_base_N250_C20000.oop --out metrics
"""

from __future__ import annotations
import argparse
import csv
import io
import os
import time
from typing import Dict, List, Tuple, Any
import contextlib

from interpreter import Interpreter, Config
from parser import Parser

# runtime generators
try:
    from gen_runtime_cases import build_chain_case, build_many_methods_case
except Exception:
    build_chain_case = None
    build_many_methods_case = None


def parse_metrics_from_object(metrics) -> Dict[str, Any]:
    return {
        "resolve_calls": metrics.resolve_calls,
        "resolve_time_ns": metrics.resolve_time_ns,
        "resolve_steps": metrics.resolve_steps,
        "class_jumps": metrics.class_jumps,
        "class_probes": metrics.class_probes,
        "resolve_fails": metrics.resolve_fails,
        "cache_hits": metrics.cache_hits,
        "cache_misses": metrics.cache_misses,
        "vtable_uses": metrics.vtable_uses,
        "itable_uses": metrics.itable_uses,
        "gc_runs": metrics.gc_runs,
        "gc_collected": metrics.gc_collected,
    }


def run_one(classes: Dict, statements: List[str], *, vtable: bool, gc: bool, cache: bool, label: str) -> Dict[str, Any]:
    cfg = Config(enable_metrics=True, enable_gc=gc, enable_vtable=vtable, enable_cache=cache)
    interp = Interpreter(classes, statements, config=cfg)

    buf = io.StringIO()
    start = time.perf_counter()
    # suppress program prints (calls print)
    with contextlib.redirect_stdout(buf):
        interp.run()
    wall_ms = (time.perf_counter() - start) * 1000.0

    row = {
        "label": label,
        "vtable": int(vtable),
        "gc": int(gc),
        "cache": int(cache),
        "wall_ms": round(wall_ms, 3),
    }
    row.update(parse_metrics_from_object(interp.metrics))
    return row


def write_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)


def write_md(path: str, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    cols = list(rows[0].keys())
    with open(path, "w", encoding="utf-8") as f:
        f.write("| " + " | ".join(cols) + " |\n")
        f.write("| " + " | ".join(["---"] * len(cols)) + " |\n")
        for r in rows:
            f.write("| " + " | ".join(str(r[c]) for c in cols) + " |\n")


def load_text_program(program_path: str) -> Tuple[Dict, List[str]]:
    with open(program_path, "r", encoding="utf-8") as f:
        src = f.read()
    p = Parser(src)
    return p.parse()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["runtime", "text"], default="runtime")
    ap.add_argument("--case", choices=["chain", "many_methods"], default="chain")
    ap.add_argument("--program", help="Path to .oop program when mode=text")
    ap.add_argument("--n-classes", type=int, default=250)
    ap.add_argument("--calls", type=int, default=20000)
    ap.add_argument("--methods-per-class", type=int, default=3)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default="metrics", help="Output directory for metrics.csv and metrics.md")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)

    if args.mode == "text":
        if not args.program:
            raise SystemExit("--program is required for mode=text")
        classes, statements = load_text_program(args.program)
        label_base = os.path.basename(args.program)
    else:
        if args.case == "chain":
            classes, statements = build_chain_case(
                n_classes=args.n_classes, calls=args.calls, use_interface=True, seed=args.seed
            )
        else:
            classes, statements = build_many_methods_case(
                n_classes=args.n_classes, methods_per_class=args.methods_per_class, calls=args.calls, seed=args.seed
            )
        label_base = f"{args.case}_N{args.n_classes}_C{args.calls}"

    rows: List[Dict[str, Any]] = []
    for vtable in (False, True):
        for gc in (False, True):
            for cache in (False, True):
                label = f"{label_base}"
                rows.append(run_one(classes, statements, vtable=vtable, gc=gc, cache=cache, label=label))

    csv_path = os.path.join(args.out, "metrics.csv")
    md_path = os.path.join(args.out, "metrics.md")
    write_csv(csv_path, rows)
    write_md(md_path, rows)

    print(f"Wrote: {csv_path}")
    print(f"Wrote: {md_path}")


if __name__ == "__main__":
    main()
