"""
Generate stress-test programs for raf_oop language (TEXT form).

Usage examples:
  python gen_text_programs.py --outdir tests --n-classes 250 --calls 20000
  python gen_text_programs.py --outdir tests --n-classes 300 --calls 50000 --seed 7 --scenarios chain_base iface_chain many_methods

Outputs multiple .oop programs in the chosen outdir.
"""
from __future__ import annotations

import argparse
import os
import random
from typing import List, Dict, Tuple


def _methods_block(methods: Dict[str, List[str]]) -> List[str]:
    out = ["methods = {"]
    if methods:
        for m, toks in methods.items():
            inside = " ".join(toks)
            out.append(f"  {m} -> [{inside}]")
    out.append("}")
    return out


def gen_chain_base(n: int, calls: int, use_interface: bool, seed: int, temp_every: int, gc_every: int) -> str:
    """
    Deep chain: C0 <- C1 <- ... <- C(n-1)
    Only base C0 defines method m; others do not override.
    Calls are written to stress:
      - static call from derived view (call x.m) -> long base walk on first resolve
      - dynamic call from base view (vcall xb.m) -> long walk when vtable OFF, O(1) when ON
      - interface call (vcall xi.m) -> itable path when vtable ON
    """
    rng = random.Random(seed)
    lines: List[str] = []

    iface_name = "I0"
    method_name = "m"

    if use_interface:
        lines += [
            f"INTERFACE {iface_name}",
            *_methods_block({method_name: []}),
            "",
        ]

    # Base class
    lines += [
        "CLASS C0",
        "base = None",
        f"interfaces = [{iface_name}]" if use_interface else "interfaces = []",
        "fields = [f0]",
        *_methods_block({method_name: ["f0"]}),
        "",
    ]

    # Derived classes
    for i in range(1, n):
        lines += [
            f"CLASS C{i}",
            f"base = C{i-1}",
            "interfaces = []",
            f"fields = [f{i}]",
            *_methods_block({}),  # no methods
            "",
        ]

    # Statements
    args = ", ".join(str(i) for i in range(n))
    lines.append(f"let x = new C{n-1}({args})")
    lines.append(f"let xb = cast<C0> x")
    if use_interface:
        lines.append(f"let xi = cast<{iface_name}> x")
    lines.append("")

    # Mix of calls to exercise caches and dispatch
    # Repeat some patterns to create cache hits.
    for i in range(calls):
        r = rng.random()
        if r < 0.34:
            # static: view=runtime type => long walk to base method (first time), then cache
            lines.append("call x.m")
        elif r < 0.67:
            # virtual call via base view
            lines.append("vcall xb.m")
        else:
            if use_interface:
                lines.append("vcall xi.m")
            else:
                lines.append("vcall xb.m")

        # allocate short-lived objects + drop refs so GC can collect them
        if temp_every > 0 and (i % temp_every == 0):
            tname = f"t{i}"
            lines.append(f"let {tname} = clone x")
            lines.append(f"free {tname}")

        # periodic GC run (toggle-controlled at runtime)
        if gc_every > 0 and (i % gc_every == 0):
            lines.append("gc")

    # optional reflection and gc (safe even if toggles off)
    lines += [
        "typeof xb",
        "fieldsof xb",
        "methodsof xb",
    ]
    if use_interface:
        lines.append("interfacesof xb")
    lines.append("gc")
    lines.append("")
    return "\n".join(lines)


def gen_iface_chain(n: int, calls: int, seed: int, temp_every: int, gc_every: int) -> str:
    """
    Deep chain stressing INTERFACE dispatch.
    Interface I0 has method m.
    C0 implements I0 and defines m; derived classes add fields but do not override.
    Most calls go through interface view (xi) using vcall.
    """
    rng = random.Random(seed)
    lines: List[str] = []
    iface_name = "I0"
    method_name = "m"

    lines += [
        f"INTERFACE {iface_name}",
        *_methods_block({method_name: []}),
        "",
        "CLASS C0",
        "base = None",
        f"interfaces = [{iface_name}]",
        "fields = [f0]",
        *_methods_block({method_name: ["f0"]}),
        "",
    ]

    for i in range(1, n):
        lines += [
            f"CLASS C{i}",
            f"base = C{i-1}",
            "interfaces = []",
            f"fields = [f{i}]",
            *_methods_block({}),
            "",
        ]

    args = ", ".join(str(i) for i in range(n))
    lines.append(f"let x = new C{n-1}({args})")
    lines.append(f"let xi = cast<{iface_name}> x")
    lines.append("")

    for i in range(calls):
        # mostly interface virtual calls
        if rng.random() < 0.9:
            lines.append("vcall xi.m")
        else:
            lines.append("call xi.m")  # static through interface -> still resolves impl in runtime

        if temp_every > 0 and (i % temp_every == 0):
            tname = f"t{i}"
            lines.append(f"let {tname} = clone x")
            lines.append(f"free {tname}")

        if gc_every > 0 and (i % gc_every == 0):
            lines.append("gc")
    lines += ["gc", ""]
    return "\n".join(lines)


def gen_many_methods(n: int, methods_per_class: int, calls: int, seed: int, temp_every: int, gc_every: int) -> str:
    """
    Many methods: every class adds K new virtual methods.
    Calls randomly pick methods from current runtime type, causing large vtable and more unique cache keys.
    """
    rng = random.Random(seed)
    lines: List[str] = []
    lines += [
        "CLASS C0",
        "base = None",
        "interfaces = []",
        "fields = [f0]",
    ]
    m0 = {f"m0_{j}": ["f0", str(j)] for j in range(methods_per_class)}
    lines += _methods_block(m0)
    lines.append("")

    for i in range(1, n):
        lines += [
            f"CLASS C{i}",
            f"base = C{i-1}",
            "interfaces = []",
            f"fields = [f{i}]",
        ]
        mi = {f"m{i}_{j}": ["f0", f"f{i}", str(j)] for j in range(methods_per_class)}
        lines += _methods_block(mi)
        lines.append("")

    args = ", ".join(str(i) for i in range(n))
    lines.append(f"let x = new C{n-1}({args})")
    lines.append("")

    # choose method names from whole hierarchy
    all_methods: List[str] = []
    for i in range(n):
        for j in range(methods_per_class):
            all_methods.append(f"m{i}_{j}" if i else f"m0_{j}")

    for i in range(calls):
        m = rng.choice(all_methods)
        # static call through runtime view: forces lookup in view chain for methods not in leaf
        if rng.random() < 0.5:
            lines.append(f"call x.{m}")
        else:
            lines.append(f"vcall x.{m}")

        if temp_every > 0 and (i % temp_every == 0):
            tname = f"t{i}"
            lines.append(f"let {tname} = clone x")
            lines.append(f"free {tname}")

        if gc_every > 0 and (i % gc_every == 0):
            lines.append("gc")
    lines += ["gc", ""]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="tests", help="Output directory for .oop programs")
    ap.add_argument("--n-classes", type=int, default=250, help="Number of classes")
    ap.add_argument("--calls", type=int, default=20000, help="Number of call/vcall statements")
    ap.add_argument("--seed", type=int, default=1, help="Random seed")
    ap.add_argument("--scenarios", nargs="+", default=["chain_base", "iface_chain", "many_methods"],
                    choices=["chain_base", "iface_chain", "many_methods"])
    ap.add_argument("--no-interface", action="store_true", help="Disable interface use in chain_base scenario")
    ap.add_argument("--methods-per-class", type=int, default=3, help="For many_methods scenario")
    ap.add_argument("--temp-every", type=int, default=200,
                    help="Every N calls, allocate a clone and free it (so GC can collect). 0 disables.")
    ap.add_argument("--gc-every", type=int, default=2000,
                    help="Every N calls, emit a 'gc' statement. 0 disables.")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)

    n = args.n_classes
    calls = args.calls
    seed = args.seed

    if "chain_base" in args.scenarios:
        txt = gen_chain_base(n=n, calls=calls, use_interface=(not args.no_interface), seed=seed,
                             temp_every=args.temp_every, gc_every=args.gc_every)
        path = os.path.join(args.outdir, f"chain_base_N{n}_C{calls}.oop")
        with open(path, "w", encoding="utf-8") as f:
            f.write(txt)

    if "iface_chain" in args.scenarios:
        txt = gen_iface_chain(n=n, calls=calls, seed=seed + 101,
                              temp_every=args.temp_every, gc_every=args.gc_every)
        path = os.path.join(args.outdir, f"iface_chain_N{n}_C{calls}.oop")
        with open(path, "w", encoding="utf-8") as f:
            f.write(txt)

    if "many_methods" in args.scenarios:
        txt = gen_many_methods(n=n, methods_per_class=args.methods_per_class, calls=calls, seed=seed + 202,
                               temp_every=args.temp_every, gc_every=args.gc_every)
        path = os.path.join(args.outdir, f"many_methods_N{n}_K{args.methods_per_class}_C{calls}.oop")
        with open(path, "w", encoding="utf-8") as f:
            f.write(txt)

    print(f"Generated {len(args.scenarios)} file(s) in: {args.outdir}")


if __name__ == "__main__":
    main()
