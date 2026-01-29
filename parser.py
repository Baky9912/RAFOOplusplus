
from __future__ import annotations
from typing import Dict, List, Tuple, Optional
from lang_types import ClassDef


class Parser:
    def __init__(self, src: str):
        self.src = src
        self.types: Dict[str, ClassDef] = {}
        self.statements: List[str] = []

    def parse(self) -> Tuple[Dict[str, ClassDef], List[str]]:
        lines = [l.rstrip() for l in self.src.splitlines()]
        i = 0

        # Prvo parsiramo blokove tipova.
        while i < len(lines):
            line = lines[i].strip()
            if not line or line.startswith("//") or line.startswith(";"):
                i += 1
                continue
            if not (line.startswith("CLASS ") or line.startswith("INTERFACE ")):
                break
            i = self._parse_type_block(lines, i)

        # Ostatak su naredbe.
        while i < len(lines):
            line = lines[i].strip()
            if line and not line.startswith("//") and not line.startswith(";"):
                self.statements.append(line)
            i += 1

        self._resolve_and_link()
        return self.types, self.statements

    def _parse_type_block(self, lines: List[str], i: int) -> int:
        header = lines[i].strip()  # "CLASS A" ili "INTERFACE I"
        kind, name = header.split(None, 1)
        name = name.strip()
        is_interface = (kind == "INTERFACE")

        base_name: Optional[str] = None
        interfaces_names: List[str] = []
        fields: List[str] = []
        methods: Dict[str, List[str]] = {}

        i += 1
        while i < len(lines):
            line_raw = lines[i]
            line = line_raw.strip()

            # Prazna linija označava kraj bloka.
            if not line:
                i += 1
                break
            # Ako naiđemo na novi blok, prethodni se završio.
            if line.startswith("CLASS ") or line.startswith("INTERFACE "):
                break

            if line.startswith("base"):
                # base = B
                part = line.split("=", 1)[1].strip()
                base_name = None if part == "None" else part

            elif line.startswith("interfaces"):
                # interfaces = [I1, I2]
                bracket_part = line.split("=", 1)[1]
                lb = bracket_part.find("[")
                rb = bracket_part.find("]")
                if lb >= 0 and rb >= 0:
                    inner = bracket_part[lb + 1:rb]
                    interfaces_names = [
                        x.strip() for x in inner.split(",") if x.strip()
                    ]
                else:
                    interfaces_names = []

            elif line.startswith("fields"):
                # fields = [a, b]
                bracket_part = line.split("=", 1)[1]
                lb = bracket_part.find("[")
                rb = bracket_part.find("]")
                if lb >= 0 and rb >= 0:
                    inner = bracket_part[lb + 1:rb]
                    fields = [f.strip() for f in inner.split(",") if f.strip()]
                else:
                    fields = []

            elif line.startswith("methods"):
                # methods can be written either as a block:
                #   methods = {
                #     m -> [a, 1]
                #   }
                # or as an empty inline form:
                #   methods = { }
                #   methods = {}

                # Inline empty form: just skip.
                if "{" in line and "}" in line and line.find("{") < line.find("}"):
                    inner = line[line.find("{") + 1: line.rfind("}")].strip()
                    if inner:
                        raise ValueError(
                            f"Inline methods form must be empty (use a block). Got: {inner!r}"
                        )
                    i += 1
                    continue

                # Block form: consume following lines until a standalone '}'
                i += 1
                while i < len(lines):
                    mline = lines[i].strip()
                    if mline.startswith("}"):
                        break
                    if not mline:
                        i += 1
                        continue
                    if "->" in mline:
                        left, right = mline.split("->", 1)
                        mname = left.strip()
                        rb_part = right.strip()
                        lb = rb_part.find("[")
                        rb = rb_part.find("]")
                        if lb >= 0 and rb >= 0:
                            inner = rb_part[lb + 1:rb]
                            tokens = [
                                t.strip()
                                for t in inner.replace(",", " ").split()
                                if t.strip()
                            ]
                        else:
                            tokens = []
                        methods[mname] = tokens
                    i += 1
            i += 1

        if name in self.types:
            raise ValueError(f"Type {name} defined multiple times")

        # INTERFACE ignorišemo base/fields/interfaces
        if is_interface:
            base_name = None
            interfaces_names = []
            fields = []

        tdef = ClassDef(
            name=name,
            is_interface=is_interface,
            base_name=base_name,
            interfaces_names=interfaces_names,
            fields=fields,
            methods=methods,
        )
        self.types[name] = tdef
        return i

    def _resolve_and_link(self) -> None:
        # 1) poveži baze za klase
        for t in self.types.values():
            if t.is_interface:
                continue
            if t.base_name is not None:
                if t.base_name not in self.types:
                    raise ValueError(f"Unknown base class {t.base_name} for {t.name}")
                base = self.types[t.base_name]
                if base.is_interface:
                    raise ValueError(f"Base of class {t.name} cannot be an interface")
                t.base = base

        # 2) poveži interfejse za klase
        for t in self.types.values():
            if t.is_interface:
                continue
            resolved: List[ClassDef] = []
            for iname in t.interfaces_names:
                if iname not in self.types:
                    raise ValueError(f"Unknown interface {iname} for class {t.name}")
                iface = self.types[iname]
                if not iface.is_interface:
                    raise ValueError(f"{iname} is not an interface (used in {t.name})")
                resolved.append(iface)
            t.interfaces = resolved

        # 3) detekcija ciklusa u base lancu (klase)
        def has_cycle(start: ClassDef) -> bool:
            slow = start
            fast = start
            while fast is not None and fast.base is not None:
                slow = slow.base
                fast = fast.base.base
                if slow is fast:
                    return True
            return False

        for t in self.types.values():
            if not t.is_interface and has_cycle(t):
                raise ValueError("Cyclic class inheritance detected")

        # 4) field layout u topološkom redosledu
        remaining = {name for name, t in self.types.items() if not t.is_interface}
        # prvo interace
        for t in self.types.values():
            if t.is_interface:
                t.compute_layout()

        done = set()
        while remaining:
            progressed = False
            for name in list(remaining):
                t = self.types[name]
                if t.base is None or t.base.name in done:
                    t.compute_layout()
                    done.add(name)
                    remaining.remove(name)
                    progressed = True
            if not progressed:
                raise ValueError("Could not compute field layout (unexpected)")

        # 5) zabrani redefinisanje polja iz baze
        for t in self.types.values():
            if t.is_interface or t.base is None:
                continue
            inherited = set(t.base.all_fields())
            for f in t.fields:
                if f in inherited:
                    raise ValueError(
                        f"Field {f} in class {t.name} already defined in base class"
                    )

        # 6) method slots za interface
        for t in self.types.values():
            if not t.is_interface:
                continue
            t.method_slot = {m: i for i, m in enumerate(t.methods.keys())}

        # 7) vtable za klase (prefiks, override replace, new append)
        remaining = {name for name, t in self.types.items() if not t.is_interface}
        done = set()
        while remaining:
            progressed = False
            for name in list(remaining):
                t = self.types[name]
                if t.base is None or t.base.name in done:
                    if t.base is None:
                        t.method_slot = {}
                        t.vtable_tokens = []
                    else:
                        t.method_slot = dict(t.base.method_slot)
                        t.vtable_tokens = [list(x) for x in t.base.vtable_tokens]

                    # u redosledu definicije u klasi
                    for mname, toks in t.methods.items():
                        if mname in t.method_slot:
                            slot = t.method_slot[mname]
                            t.vtable_tokens[slot] = list(toks)
                        else:
                            slot = len(t.vtable_tokens)
                            t.method_slot[mname] = slot
                            t.vtable_tokens.append(list(toks))

                    # pripremi cache listu za planove
                    t._vtable_plans = [None] * len(t.vtable_tokens)

                    done.add(name)
                    remaining.remove(name)
                    progressed = True
            if not progressed:
                raise ValueError("Could not build vtables")
