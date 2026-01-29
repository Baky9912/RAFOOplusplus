from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from lang_types import ClassDef, Instance, is_int

import time

@dataclass
class Config:
    enable_metrics: bool = False
    enable_gc: bool = False
    enable_vtable: bool = False
    enable_cache: bool = True

@dataclass
class VarBinding:
    inst: Instance
    view_cls: ClassDef


class Metrics:
    def __init__(self) -> None:
        self.resolve_calls = 0
        self.resolve_time_ns = 0
        self.resolve_steps = 0

        self.cache_hits = 0
        self.cache_misses = 0

        self.vtable_uses = 0
        self.itable_uses = 0

        self.class_jumps = 0
        self.class_probes = 0
        self.resolve_fails = 0
        
        self.gc_runs = 0
        self.gc_collected = 0


    def print(self) -> None:
        print("\n=== Metrics ===")
        print(f"resolve_calls   : {self.resolve_calls}")
        print(f"cache_hits      : {self.cache_hits}")
        print(f"cache_misses    : {self.cache_misses}")
        print(f"vtable_uses     : {self.vtable_uses}")
        print(f"itable_uses     : {self.itable_uses}")
        print(f"resolve_steps   : {self.resolve_steps}")
        if self.resolve_calls:
            avg = self.resolve_time_ns / self.resolve_calls
            print(f"resolve_time_ns : {self.resolve_time_ns} (avg {avg:.1f})")
        else:
            print(f"resolve_time_ns : {self.resolve_time_ns}")
        print(f"gc_runs         : {self.gc_runs}")
        print(f"gc_collected    : {self.gc_collected}")


# MethodPlan: list of operations (is_field, value)
MethodPlan = List[Tuple[bool, int]]


class Interpreter:
    def __init__(self, classes: Dict[str, ClassDef], statements: List[str], config: Optional[Config] = None):
        self.classes = classes
        self.statements = statements
        self.env: Dict[str, VarBinding] = {}

        self.cfg = config or Config()
        self.metrics = Metrics()
        # global plan cache (for lookup fallback paths)
        self._plan_cache: Dict[Tuple[str, str, str, str], MethodPlan] = {}

        # heap for GC (instances allocated by new/clone)
        self.heap: List[Instance] = []

    def run(self) -> None:
        for stmt in self.statements:
            self._exec_statement(stmt)

        if self.cfg.enable_metrics:
            self.metrics.print()

    def _exec_statement(self, stmt: str) -> None:
        stmt = stmt.split(";", 1)[0].strip()
        if not stmt:
            return

        if stmt.startswith("let "):
            self._exec_let(stmt)
            return

        if stmt.startswith("call "):
            self._exec_call(stmt, dynamic=False)
            return

        if stmt.startswith("vcall "):
            self._exec_call(stmt, dynamic=True)
            return

        if "." in stmt and "=" in stmt and not stmt.startswith("call ") and not stmt.startswith("vcall "):
            self._exec_field_assign(stmt)
            return

        if " is " in stmt:
            self._exec_is(stmt)
            return

        # reflection (minimal)
        if stmt.startswith("typeof "):
            self._exec_typeof(stmt)
            return
        if stmt.startswith("fieldsof "):
            self._exec_fieldsof(stmt)
            return
        if stmt.startswith("methodsof "):
            self._exec_methodsof(stmt)
            return
        if stmt.startswith("interfacesof "):
            self._exec_interfacesof(stmt)
            return

        # GC manual trigger
        if stmt == "gc":
            if self.cfg.enable_gc:
                self._gc_collect()
            return

        # manual reference drop (makes objects collectible by GC)
        if stmt.startswith("free "):
            self._exec_free(stmt)
            return

        raise ValueError(f"Unknown statement: {stmt}")

    def _exec_free(self, stmt: str) -> None:
        """free x  -> removes variable binding from env.

        This does NOT necessarily destroy the object immediately:
        if other variables alias the same instance, it stays reachable.
        When GC is enabled, a later 'gc' will collect unreachable instances.
        """
        _, name = stmt.split("free", 1)
        name = name.strip()
        if name not in self.env:
            raise ValueError(f"Unknown variable {name}")
        del self.env[name]

    # ------------- let -------------

    def _exec_let(self, stmt: str) -> None:
        _, rest = stmt.split("let", 1)
        name_part, expr_part = rest.split("=", 1)
        var_name = name_part.strip()
        expr = expr_part.strip()

        if expr.startswith("new "):
            expr2 = expr[4:].strip()
            cls_name, args_str = expr2.split("(", 1)
            cls_name = cls_name.strip()
            args_str = args_str.rsplit(")", 1)[0]
            args: List[int] = []
            if args_str.strip():
                for tok in args_str.split(","):
                    tok = tok.strip()
                    if not is_int(tok):
                        raise ValueError("Only int literals allowed as constructor args")
                    args.append(int(tok))
            inst = self._instantiate(cls_name, args)
            self.env[var_name] = VarBinding(inst=inst, view_cls=inst.cls)
            return

        if expr.startswith("clone "):
            src_name = expr[6:].strip()
            src_binding = self._get_binding(src_name)
            new_inst = self._clone(src_binding.inst)
            self.env[var_name] = VarBinding(inst=new_inst, view_cls=src_binding.view_cls)
            return

        if expr.startswith("cast<"):
            after_cast = expr[len("cast<"):]
            type_part, rest2 = after_cast.split(">", 1)
            target_cls_name = type_part.strip()
            src_name = rest2.strip()
            src_binding = self._get_binding(src_name)
            new_binding = self._cast_binding(src_binding, target_cls_name)
            self.env[var_name] = new_binding
            return

        # alias
        src_name = expr
        src_binding = self._get_binding(src_name)
        self.env[var_name] = VarBinding(inst=src_binding.inst, view_cls=src_binding.view_cls)

    def _exec_field_assign(self, stmt: str) -> None:
        left, right = stmt.split("=", 1)
        right = right.strip()
        if not is_int(right):
            raise ValueError("Only int literals allowed in field assignment")
        value = int(right)

        obj_part = left.strip()
        var_name, field_name = [p.strip() for p in obj_part.split(".", 1)]
        binding = self._get_binding(var_name)
        inst = binding.inst

        try:
            idx = inst.cls.field_idx(field_name)
        except KeyError:
            raise ValueError(f"Unknown field {field_name} for instance of {inst.cls.name}")
        inst.values[idx] = value

    # ------------- calls -------------

    def _exec_call(self, stmt: str, dynamic: bool) -> None:
        kw = "vcall" if dynamic else "call"
        _, rest = stmt.split(kw, 1)
        rest = rest.strip()
        var_part, method_name = [p.strip() for p in rest.split(".", 1)]
        binding = self._get_binding(var_part)

        plan = self._resolve_call_plan(binding, method_name, dynamic=dynamic)

        values: List[int] = []
        inst = binding.inst
        for is_field, v in plan:
            if is_field:
                values.append(inst.values[v])
            else:
                values.append(v)
        print(" ".join(str(x) for x in values))

    def _resolve_call_plan(self, binding: VarBinding, method_name: str, dynamic: bool) -> MethodPlan:
        inst = binding.inst
        view = binding.view_cls
        runtime = inst.cls

        # type-check: method must exist in view type
        if view.lookup_method(method_name) is None:
            raise ValueError(f"Method {method_name} not found in view type {view.name} or its bases")

        # key for cache (only for non-vtable paths)
        mode = "dyn" if dynamic else "static"
        cache_key = (mode, view.name, runtime.name, method_name)

        # fast paths
        if dynamic and self.cfg.enable_vtable:
            if view.is_interface:
                # interface dispatch
                iface = view
                if not runtime.implements_interface(iface):
                    raise ValueError(f"Runtime type {runtime.name} does not implement interface {iface.name}")
                plan = self._itable_plan(runtime, iface, method_name)
                if self.cfg.enable_metrics:
                    self.metrics.itable_uses += 1
                return plan
            else:
                # vtable dispatch
                if not runtime.is_subclass_of(view) and runtime is not view:
                    raise ValueError(f"Runtime type {runtime.name} is not a subclass of view type {view.name}")

                slot = view.method_slot.get(method_name)
                if slot is None:
                    # should not happen because lookup_method above succeeded
                    raise ValueError(f"Internal: no slot for {method_name} in {view.name}")
                plan = self._vtable_plan(runtime, slot)
                if self.cfg.enable_metrics:
                    self.metrics.vtable_uses += 1
                return plan

        # cached fallback
        if self.cfg.enable_cache and cache_key in self._plan_cache:
            if self.cfg.enable_metrics:
                self.metrics.cache_hits += 1
            return self._plan_cache[cache_key]

        if self.cfg.enable_metrics:
            if self.cfg.enable_cache:
                self.metrics.cache_misses += 1

        start_ns = time.perf_counter_ns() if self.cfg.enable_metrics else 0

        steps = 0
        tokens: Optional[List[str]] = None

        if not dynamic: # static dispatch:
            if view.is_interface:
                if not runtime.implements_interface(view):
                    raise ValueError(f"Runtime type {runtime.name} does not implement interface {view.name}")
                tokens, steps = self._lookup_with_steps(view, method_name)
                if tokens is None or len(tokens) == 0:
                    tokens, steps = self._lookup_with_steps(runtime, method_name)
            else:
                tokens, steps = self._lookup_with_steps(view, method_name)
        else: # dynamic dispatch without vtable: method body from runtime (virtual)
            if view.is_interface:
                if not runtime.implements_interface(view):
                    raise ValueError(f"Runtime type {runtime.name} does not implement interface {view.name}")
            tokens, steps = self._lookup_with_steps(runtime, method_name)

        if tokens is None:
            raise ValueError(f"Method {method_name} not found (resolved)")

        plan = self._compile_plan(runtime, method_name, tokens)

        if self.cfg.enable_metrics:
            elapsed = time.perf_counter_ns() - start_ns
            self.metrics.resolve_calls += 1
            self.metrics.resolve_time_ns += elapsed
            self.metrics.resolve_steps += steps

        if self.cfg.enable_cache:   
            self._plan_cache[cache_key] = plan
        return plan

    def _lookup_with_steps(self, start: ClassDef, method_name: str) -> Tuple[Optional[List[str]], int]:
        steps = 0
        c: Optional[ClassDef] = start
        while c is not None:
            self.metrics.class_jumps += 1

            for c_method_name in c.methods:
                self.metrics.class_probes += 1
                if c_method_name == method_name:
                    return c.methods[method_name], steps
                self.metrics.resolve_fails += 1
            
            # if method_name in c.methods:
            c = c.base
            steps += 1
        return None, steps

    def _compile_plan(self, runtime: ClassDef, method_name: str, tokens: List[str]) -> MethodPlan:
        plan: MethodPlan = []
        for tok in tokens:
            if is_int(tok):
                plan.append((False, int(tok)))
            else:
                try:
                    idx = runtime.field_idx(tok)
                except KeyError:
                    raise ValueError(f"Unknown field {tok} in method {method_name}")
                plan.append((True, idx))
        return plan

    # ---- vtable/itable plans (lazy compiled) ----

    def _vtable_plan(self, runtime: ClassDef, slot: int) -> MethodPlan:
        # runtime._vtable_plans is per class and aligned to runtime.vtable_tokens
        cached = runtime._vtable_plans[slot]
        if cached is not None:
            if self.cfg.enable_metrics:
                self.metrics.cache_hits += 1
            return cached  # type: ignore[return-value]

        if self.cfg.enable_metrics:
            self.metrics.cache_misses += 1

        start_ns = time.perf_counter_ns() if self.cfg.enable_metrics else 0
        tokens = runtime.vtable_tokens[slot]
        plan = self._compile_plan(runtime, f"<slot {slot}>", tokens)
        runtime._vtable_plans[slot] = plan
        if self.cfg.enable_metrics:
            elapsed = time.perf_counter_ns() - start_ns
            self.metrics.resolve_calls += 1
            self.metrics.resolve_time_ns += elapsed
            # vtable dispatch: 0 "steps"
        return plan

    def _itable_plan(self, runtime: ClassDef, iface: ClassDef, method_name: str) -> MethodPlan:
        # build per (runtime, iface) list aligned with iface slots
        cache = runtime._itable_plans.get(iface.name)
        if cache is None:
            if self.cfg.enable_metrics:
                self.metrics.cache_misses += 1
            start_ns = time.perf_counter_ns() if self.cfg.enable_metrics else 0

            plans: List[MethodPlan] = [None] * len(iface.method_slot)
            # build by iface slot order: methods keys insertion order
            for m, slot in iface.method_slot.items():
                tokens, steps = self._lookup_with_steps(runtime, m)
                if tokens is None:
                    raise ValueError(f"Class {runtime.name} does not implement {iface.name}.{m}")
                plans[slot] = self._compile_plan(runtime, m, tokens)
                if self.cfg.enable_metrics:
                    self.metrics.resolve_steps += steps

            runtime._itable_plans[iface.name] = plans
            if self.cfg.enable_metrics:
                elapsed = time.perf_counter_ns() - start_ns
                self.metrics.resolve_calls += 1
                self.metrics.resolve_time_ns += elapsed
            cache = plans
        else:
            if self.cfg.enable_metrics:
                self.metrics.cache_hits += 1

        slot = iface.method_slot[method_name]
        return cache[slot]

    # ------------- is (instanceof) -------------

    def _exec_is(self, stmt: str) -> None:
        var_name, type_name = [p.strip() for p in stmt.split(" is ", 1)]
        binding = self.env.get(var_name)
        t = self.classes.get(type_name)
        if binding is None or t is None:
            print("ISN'T")
            return

        inst = binding.inst
        if t.is_interface:
            print("IS" if inst.cls.implements_interface(t) else "ISN'T")
        else:
            print("IS" if inst.cls.is_subclass_of(t) else "ISN'T")

    # ------------- reflection (minimal) -------------

    def _exec_typeof(self, stmt: str) -> None:
        _, name = stmt.split("typeof", 1)
        name = name.strip()
        b = self._get_binding(name)
        print(f"VIEW {b.view_cls.name} RUNTIME {b.inst.cls.name}")

    def _exec_fieldsof(self, stmt: str) -> None:
        _, name = stmt.split("fieldsof", 1)
        name = name.strip()
        b = self._get_binding(name)
        # field layout is from runtime
        print(" ".join(b.inst.cls.all_fields()))

    def _exec_methodsof(self, stmt: str) -> None:
        _, name = stmt.split("methodsof", 1)
        name = name.strip()
        b = self._get_binding(name)
        names = sorted(b.view_cls.visible_method_names())
        print(" ".join(names))

    def _exec_interfacesof(self, stmt: str) -> None:
        _, name = stmt.split("interfacesof", 1)
        name = name.strip()
        b = self._get_binding(name)
        ifaces = sorted([i.name for i in b.inst.cls.all_interfaces()])
        print(" ".join(ifaces))

    def _get_binding(self, name: str) -> VarBinding:
        if name not in self.env:
            raise ValueError(f"Unknown variable {name}")
        return self.env[name]

    def _instantiate(self, class_name: str, args: List[int]) -> Instance:
        if class_name not in self.classes:
            raise ValueError(f"Unknown class {class_name}")
        cls = self.classes[class_name]
        if cls.is_interface:
            raise ValueError("Cannot instantiate an interface")
        all_fields = cls.all_fields()
        if len(all_fields) != len(args):
            raise ValueError(
                f"Class {class_name} expects {len(all_fields)} args, got {len(args)}"
            )
        inst = Instance(cls=cls, values=list(args))
        self.heap.append(inst)
        return inst

    def _clone(self, inst: Instance) -> Instance:
        new_inst = Instance(cls=inst.cls, values=list(inst.values))
        self.heap.append(new_inst)
        return new_inst

    def _cast_binding(self, binding: VarBinding, target_name: str) -> VarBinding:
        if target_name not in self.classes:
            raise ValueError(f"Unknown type {target_name}")
        target = self.classes[target_name]
        runtime = binding.inst.cls

        if target.is_interface:
            if not runtime.implements_interface(target):
                raise ValueError(f"Cannot cast {runtime.name} to interface {target.name}")
            return VarBinding(inst=binding.inst, view_cls=target)

        # class upcast
        if not runtime.is_subclass_of(target):
            raise ValueError(f"Cannot cast {runtime.name} to {target.name}")
        return VarBinding(inst=binding.inst, view_cls=target)

    def print_classes(self) -> None:
        print("\n=== Type Structure ===")
        for name in sorted(self.classes.keys()):
            t = self.classes[name]
            if t.is_interface:
                print(f"Interface {t.name}:")
                print(f"  methods: {', '.join(t.methods.keys()) if t.methods else '(none)'}")
                print()
            else:
                base_name = t.base.name if t.base else "None"
                ifaces = ", ".join(x.name for x in t.interfaces) if t.interfaces else "(none)"
                print(f"Class {t.name}:")
                print(f"  base       : {base_name}")
                print(f"  interfaces : {ifaces}")
                print(f"  fields     : {', '.join(t.fields) if t.fields else '(none)'}")
                if t.methods:
                    print("  methods:")
                    for m, toks in t.methods.items():
                        print(f"    {m} -> [{', '.join(toks)}]")
                else:
                    print("  methods: (none)")
                print(f"  vtable_size: {len(t.vtable_tokens)}")
                print()

    def print_instances(self) -> None:
        print("=== Instances ===")
        if not self.env:
            print("(no instances)")
            return
        for var_name in sorted(self.env.keys()):
            b = self.env[var_name]
            inst = b.inst
            view = b.view_cls
            runtime = inst.cls
            pairs = [f"{n}={v}" for n, v in zip(runtime.all_fields(), inst.values)]
            print(f"Instance {var_name}:")
            print(f"  view type    : {view.name}")
            print(f"  runtime type : {runtime.name}")
            print(f"  fields       : {', '.join(pairs) if pairs else '(none)'}")
            print()

    def _gc_collect(self) -> None:
        # roots: instances reachable from env
        roots = {id(b.inst) for b in self.env.values()}

        before = len(self.heap)
        self.heap = [x for x in self.heap if id(x) in roots] # new
        collected = before - len(self.heap)
        # one level

        if self.cfg.enable_metrics:
            self.metrics.gc_runs += 1
            self.metrics.gc_collected += collected
