from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
import re

_INT_RE = re.compile(r"-?\d+$")


def is_int(tok: str) -> bool:
    """
    Proverava da li je token ceo broj (npr. '5', '-3').
    """
    return bool(_INT_RE.match(tok))


@dataclass(eq=False)
class ClassDef:
    """
    Opis jednog tipa u jeziku: CLASS ili INTERFACE.

    name            - ime (npr. 'A' ili 'IPrintable')
    is_interface    - True ako je INTERFACE
    base_name       - ime bazne klase (samo za CLASS)
    interfaces_names- imena interfejsa koje CLASS implementira
    fields          - polja koja OVA klasa uvodi
    methods         - mapa: ime metode -> lista tokena koje ispisuje (implementacija)
                      (za INTERFACE se tokeni ne koriste za izvršavanje, služe samo kao deklaracija)
    base            - referenca na baznu klasu (CLASS) ili None
    interfaces      - razrešene reference na interfejse (samo za CLASS)
    """
    name: str
    is_interface: bool
    base_name: Optional[str]
    interfaces_names: List[str]
    fields: List[str]
    methods: Dict[str, List[str]]

    base: Optional["ClassDef"] = None
    interfaces: List["ClassDef"] = field(default_factory=list)

    # ---- Field layout (base fields first) ----
    _all_fields_cache: List[str] = field(default_factory=list, init=False, repr=False)
    _field_index: Dict[str, int] = field(default_factory=dict, init=False, repr=False)

    # ---- Fast dispatch tables ----
    # For CLASS: method_slot maps method_name -> slot in vtable_tokens (virtual dispatch)
    # For INTERFACE: method_slot maps method_name -> slot within interface dispatch table (itable)
    method_slot: Dict[str, int] = field(default_factory=dict, init=False)
    vtable_tokens: List[List[str]] = field(default_factory=list, init=False, repr=False)

    # Lazy caches built at runtime (Interpreter)
    _vtable_plans: List[object] = field(default_factory=list, init=False, repr=False)  # List[Optional[MethodPlan]]
    _itable_plans: Dict[str, List[object]] = field(default_factory=dict, init=False, repr=False)  # iface -> plans

    def compute_layout(self) -> None:
        """
        Računa pun raspored polja (baza pa izvedena) i mapu name->index.
        """
        if self.is_interface:
            allf: List[str] = []
        else:
            if self.base is None:
                allf = list(self.fields)
            else:
                allf = self.base.all_fields() + self.fields
        self._all_fields_cache = allf
        self._field_index = {n: i for i, n in enumerate(allf)}

    def all_fields(self) -> List[str]:
        return self._all_fields_cache

    def field_idx(self, name: str) -> int:
        return self._field_index[name]  # KeyError if missing

    def is_subclass_of(self, other: "ClassDef") -> bool:
        """
        Proverava da li je ova klasa jednaka ili potklasa zadate (samo CLASS hijerarhija).
        """
        if self.is_interface or other.is_interface:
            return False
        c: Optional[ClassDef] = self
        while c is not None:
            if c is other:
                return True
            c = c.base
        return False

    def all_interfaces(self) -> Set["ClassDef"]:
        """
        Skup svih interfejsa koje ova klasa implementira (uključujući nasledno kroz bazu).
        """
        if self.is_interface:
            return {self}
        out: Set[ClassDef] = set()
        c: Optional[ClassDef] = self
        while c is not None:
            for it in c.interfaces:
                out.add(it)
            c = c.base
        return out

    def implements_interface(self, iface: "ClassDef") -> bool:
        if not iface.is_interface:
            return False
        return iface in self.all_interfaces()

    def lookup_method(self, name: str) -> Optional[List[str]]:
        """
        Traži metodu u ovoj klasi i bazama (prema hijerarhiji). (Za CLASS)
        Za INTERFACE: traži samo u methods (deklaracija).
        """
        if self.is_interface:
            return self.methods.get(name)

        c: Optional[ClassDef] = self
        while c is not None:
            if name in c.methods:
                return c.methods[name]
            c = c.base
        return None

    def visible_method_names(self) -> Set[str]:
        """
        Skup metoda koje su vidljive kroz ovaj tip.
        - CLASS: sve metode u lancu baza (virtual skup).
        - INTERFACE: metode interfejsa.
        """
        if self.is_interface:
            return set(self.methods.keys())
        out: Set[str] = set()
        c: Optional[ClassDef] = self
        while c is not None:
            out.update(c.methods.keys())
            c = c.base
        return out


@dataclass
class Instance:
    """
    Konkretna instanca objekta.
    values su poravnate sa cls.all_fields()
    """
    cls: ClassDef
    values: List[int]
