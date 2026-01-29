"""
Build "fake classes at runtime" (no Parser) and optionally run Interpreter.

This is useful for benchmarks because it avoids parsing time and lets you generate
types programmatically.

You can import:
  from gen_runtime_cases import build_chain_case, build_many_methods_case
"""
from __future__ import annotations

from typing import Dict, List, Tuple, Optional
import random

from lang_types import ClassDef


def _link_and_build(types: Dict[str, ClassDef]) -> None:
    """Link base/interfaces, compute field layouts, build interface slots and class vtables."""
    # connect bases + interfaces
    for t in types.values():
        if t.is_interface:
            continue
        if t.base_name is not None:
            base = types[t.base_name]
            if base.is_interface:
                raise ValueError("Base cannot be an interface")
            t.base = base
        t.interfaces = []
        for iname in t.interfaces_names:
            iface = types[iname]
            if not iface.is_interface:
                raise ValueError(f"{iname} is not an interface")
            t.interfaces.append(iface)

    # layouts
    for t in types.values():
        if t.is_interface:
            t.compute_layout()

    # topo compute layouts for classes
    remaining = {n for n, t in types.items() if not t.is_interface}
    done = set()
    while remaining:
        progressed = False
        for name in list(remaining):
            t = types[name]
            if t.base is None or t.base.name in done:
                t.compute_layout()
                done.add(name)
                remaining.remove(name)
                progressed = True
        if not progressed:
            raise ValueError("Unexpected cycle while computing layouts")

    # forbid field redeclare
    for t in types.values():
        if t.is_interface or t.base is None:
            continue
        inherited = set(t.base.all_fields())
        for f in t.fields:
            if f in inherited:
                raise ValueError(f"Field {f} in {t.name} redeclared")

    # interface method slots
    for t in types.values():
        if t.is_interface:
            t.method_slot = {m: i for i, m in enumerate(t.methods.keys())}

    # build vtables
    remaining = {n for n, t in types.items() if not t.is_interface}
    done = set()
    while remaining:
        progressed = False
        for name in list(remaining):
            t = types[name]
            if t.base is None or t.base.name in done:
                if t.base is None:
                    t.method_slot = {}
                    t.vtable_tokens = []
                else:
                    t.method_slot = dict(t.base.method_slot)
                    t.vtable_tokens = [list(x) for x in t.base.vtable_tokens]

                for mname, toks in t.methods.items():
                    if mname in t.method_slot:
                        slot = t.method_slot[mname]
                        t.vtable_tokens[slot] = list(toks)
                    else:
                        slot = len(t.vtable_tokens)
                        t.method_slot[mname] = slot
                        t.vtable_tokens.append(list(toks))

                t._vtable_plans = [None] * len(t.vtable_tokens)

                done.add(name)
                remaining.remove(name)
                progressed = True
        if not progressed:
            raise ValueError("Unexpected cycle while building vtables")


def build_chain_case(
    n_classes: int = 250,
    calls: int = 20000,
    use_interface: bool = True,
    seed: int = 1,
    temp_every: int = 200,
    gc_every: int = 2000,
) -> Tuple[Dict[str, ClassDef], List[str]]:
    """
    Same as chain_base scenario but without text parsing.
    """
    rng = random.Random(seed)
    types: Dict[str, ClassDef] = {}
    iface = "I0"
    m = "m"

    if use_interface:
        types[iface] = ClassDef(
            name=iface, is_interface=True, base_name=None,
            interfaces_names=[], fields=[],
            methods={m: []},
        )

    # base class
    types["C0"] = ClassDef(
        name="C0", is_interface=False, base_name=None,
        interfaces_names=[iface] if use_interface else [],
        fields=["f0"],
        methods={m: ["f0"]},
    )

    for i in range(1, n_classes):
        types[f"C{i}"] = ClassDef(
            name=f"C{i}", is_interface=False, base_name=f"C{i-1}",
            interfaces_names=[],
            fields=[f"f{i}"],
            methods={},
        )

    _link_and_build(types)

    # statements
    args = ", ".join(str(i) for i in range(n_classes))
    stmts: List[str] = [
        f"let x = new C{n_classes-1}({args})",
        "let xb = cast<C0> x",
    ]
    if use_interface:
        stmts.append(f"let xi = cast<{iface}> x")

    for i in range(calls):
        r = rng.random()
        if r < 0.34:
            stmts.append("call x.m")
        elif r < 0.67:
            stmts.append("vcall xb.m")
        else:
            stmts.append("vcall xi.m" if use_interface else "vcall xb.m")

        if temp_every > 0 and (i % temp_every == 0):
            tname = f"t{i}"
            stmts.append(f"let {tname} = clone x")
            stmts.append(f"free {tname}")

        if gc_every > 0 and (i % gc_every == 0):
            stmts.append("gc")

    stmts += ["gc"]
    return types, stmts


def build_many_methods_case(
    n_classes: int = 250,
    methods_per_class: int = 3,
    calls: int = 20000,
    seed: int = 1,
    temp_every: int = 200,
    gc_every: int = 2000,
) -> Tuple[Dict[str, ClassDef], List[str]]:
    rng = random.Random(seed)
    types: Dict[str, ClassDef] = {}

    types["C0"] = ClassDef(
        name="C0", is_interface=False, base_name=None,
        interfaces_names=[], fields=["f0"],
        methods={f"m0_{j}": ["f0", str(j)] for j in range(methods_per_class)},
    )

    for i in range(1, n_classes):
        types[f"C{i}"] = ClassDef(
            name=f"C{i}", is_interface=False, base_name=f"C{i-1}",
            interfaces_names=[], fields=[f"f{i}"],
            methods={f"m{i}_{j}": ["f0", f"f{i}", str(j)] for j in range(methods_per_class)},
        )

    _link_and_build(types)

    args = ", ".join(str(i) for i in range(n_classes))
    stmts: List[str] = [f"let x = new C{n_classes-1}({args})"]

    all_methods: List[str] = []
    for i in range(n_classes):
        for j in range(methods_per_class):
            all_methods.append(f"m0_{j}" if i == 0 else f"m{i}_{j}")

    for i in range(calls):
        m = rng.choice(all_methods)
        if rng.random() < 0.5:
            stmts.append(f"call x.{m}")
        else:
            stmts.append(f"vcall x.{m}")

        if temp_every > 0 and (i % temp_every == 0):
            tname = f"t{i}"
            stmts.append(f"let {tname} = clone x")
            stmts.append(f"free {tname}")

        if gc_every > 0 and (i % gc_every == 0):
            stmts.append("gc")
    stmts.append("gc")
    return types, stmts
