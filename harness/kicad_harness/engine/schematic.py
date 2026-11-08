"""Schematic documents as KiCad S-expressions. See DESIGN.md, "Schematic".

A design in the schematic phase has ``schematic.kicad_sch`` instead of a board. This module is
the pure-Python model behind the agent's schematic tools: it reads and writes the file, places
symbols from a ``.kicad_sym`` library, and connects them with wires, junctions and labels.

It deliberately knows nothing about KiCad's Python bindings, so it runs in the harness venv and
not only under KiCad's interpreter. ``kicad-cli`` remains the authority: every write is checked by
rendering (``sch export svg``) and by ERC (``sch erc``) in ``engine/kicad_cli.py``.

Coordinates are millimetres in KiCad schematic space: x right, **y down**.
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMATIC_VERSION = "20250114"
GENERATOR = "kicad_harness"

# KiCad's default paper sizes, in millimetres.
PAPER: dict[str, tuple[float, float]] = {
    "A4": (297.0, 210.0),
    "A3": (420.0, 297.0),
    "A2": (594.0, 420.0),
    "A1": (841.0, 594.0),
    "A0": (1189.0, 841.0),
}

class Q(str):
    """A string KiCad always writes quoted (uuids, generator ids, lib ids, pages).

    The writer quotes anything that needs it; subclassing ``str`` lets a node carry the
    "always quote this" hint without changing the shape of the S-expression tree.
    """


# The grid KiCad snaps to; keeping every endpoint on it is what makes wires connect.
GRID_MM = 1.27

POWER_NETS = {"GND", "GNDA", "GNDD", "VCC", "VDD", "VSS", "+3V3", "+5V", "+12V", "-12V", "VBUS"}


# ---------------------------------------------------------------------------------------------
# S-expressions


def parse(text: str) -> list:
    """Parse KiCad S-expression ``text`` into nested lists of strings.

    Returns the single root node, e.g. ``["kicad_sch", ["version", "20250114"], ...]``.
    Quoted strings come back unescaped; every other token stays text, so numbers keep the
    form KiCad wrote (callers convert what they need).
    """
    pos = _skip_ws(text, 0)
    if pos >= len(text) or text[pos] != "(":
        raise ValueError("schematic must start with '('")
    node, pos = _parse_node(text, pos)
    if _skip_ws(text, pos) < len(text):
        raise ValueError("trailing data after the schematic expression")
    return node


def _skip_ws(text: str, pos: int) -> int:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _parse_node(text: str, pos: int) -> tuple[list, int]:
    """Parse one ``( ... )`` starting at ``pos``; returns the node and the position after it."""
    assert text[pos] == "("
    pos += 1
    out: list = []
    n = len(text)
    while True:
        pos = _skip_ws(text, pos)
        if pos >= n:
            raise ValueError("unbalanced '(' in schematic")
        ch = text[pos]
        if ch == ")":
            return out, pos + 1
        if ch == "(":
            child, pos = _parse_node(text, pos)
            out.append(child)
        elif ch == '"':
            value, pos = _parse_string(text, pos)
            out.append(Q(value))
        else:
            start = pos
            while pos < n and not text[pos].isspace() and text[pos] not in '()"':
                pos += 1
            out.append(text[start:pos])


def _parse_string(text: str, pos: int) -> tuple[str, int]:
    """Parse a quoted string starting at ``pos``, honouring backslash escapes."""
    assert text[pos] == '"'
    pos += 1
    buf = []
    n = len(text)
    while pos < n:
        ch = text[pos]
        if ch == "\\" and pos + 1 < n:
            buf.append(text[pos + 1])
            pos += 2
            continue
        if ch == '"':
            return "".join(buf), pos + 1
        buf.append(ch)
        pos += 1
    raise ValueError("unterminated string in schematic")


def dump(node: Any, indent: int = 0) -> str:
    """Write a parsed S-expression back out, one child per line."""
    pad = "  " * indent
    if not isinstance(node, list):
        return f"{pad}{quote(node)}"
    if not node:
        return f"{pad}()"
    # Keep short, atomic lists on one line: (at 10 20 0), (size 1.27 1.27), (uuid "...").
    simple = all(not isinstance(child, list) for child in node)
    if simple:
        return f"{pad}({' '.join(quote(c) for c in node)})"
    head = node[0]
    inner = "\n".join(dump(child, indent + 1) for child in node[1:])
    return f"{pad}({quote(head)}\n{inner}\n{pad})"



def write_top(node: list) -> str:
    """Write a whole document: the root list keeps its own parentheses."""
    return dump(node)



_NEEDS_QUOTES = re.compile(r'[\s()"]')


def quote(token: Any) -> str:
    """Quote a token if KiCad requires it."""
    if isinstance(token, Q):
        return '"' + str(token).replace("\\", "\\\\").replace('"', '\\"') + '"'
    if not isinstance(token, str):
        # Generated libraries contain "-0.0", which KiCad's tokenizer rejects.
        if isinstance(token, float) and token == 0:
            return "0"
        token = str(token)
    text = token
    if token in ("-0.0", "-0"):
        return "0"
    if text and not _NEEDS_QUOTES.search(text):
        return text
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def find_all(node: list, head: str) -> list[list]:
    """Every direct child of ``node`` whose head is ``head``."""
    return [c for c in node if isinstance(c, list) and c and _head(c[0]) == head]


def find_one(node: list, head: str) -> list | None:
    """The first direct child of ``node`` whose head is ``head``."""
    for child in node:
        if isinstance(child, list) and child and _head(child[0]) == head:
            return child
    return None


def _head(token: Any) -> str:
    """A node head, whitespace-normalised (generated libraries leave trailing spaces)."""
    return "" if isinstance(token, list) else str(token).strip()


def _clean(token: Any) -> str:
    """A scalar node value, whitespace-normalised; ``""`` for a nested list."""
    return "" if isinstance(token, list) else str(token).strip()


def new_uuid() -> str:
    return str(uuid.uuid4())


def snap(value: float, grid: float = GRID_MM) -> float:
    """Snap a coordinate to the schematic grid, rounded to avoid float noise."""
    if grid <= 0:
        return round(value, 6)
    return round(round(value / grid) * grid, 6)


def num(value: float) -> str:
    """Format a number the way KiCad does: no trailing zeros, no exponent."""
    text = f"{round(float(value), 6):.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


# ---------------------------------------------------------------------------------------------
# Symbol libraries


@dataclass
class LibraryPin:
    """One pin of a library symbol, in symbol-local coordinates."""

    number: str
    name: str
    x: float
    y: float
    angle: float = 0.0
    length: float = 2.54
    electrical: str = "unspecified"


@dataclass
class LibrarySymbol:
    """A symbol from a ``.kicad_sym`` library, flattened out of its unit sub-symbols."""

    name: str
    lib_id: str
    pins: dict[str, LibraryPin]
    reference: str = "U"
    value: str = ""
    footprint: str = ""
    power: bool = False
    body: list = field(default_factory=list)

    @property
    def pin_numbers(self) -> list[str]:
        return list(self.pins)


def read_library(path: str | Path) -> dict[str, LibrarySymbol]:
    """Every symbol in a ``.kicad_sym`` file, keyed by name.

    ``pins`` merges all unit sub-symbols (``NAME_0_1``, ``NAME_1_1``); symbols with several
    units are not supported and report the union of their pins.
    """
    text = Path(path).read_text(encoding="utf-8")
    tree = parse(text)
    lib_name = Path(path).stem
    out: dict[str, LibrarySymbol] = {}
    for sym in find_all(tree, "symbol"):
        if len(sym) < 2 or isinstance(sym[1], list):
            continue
        name = _clean(sym[1])
        if not name:
            continue
        pins: dict[str, LibraryPin] = {}
        reference, value, footprint = "U", name, ""
        for prop in find_all(sym, "property"):
            key = _clean(prop[1]) if len(prop) > 1 else ""
            val = _clean(prop[2]) if len(prop) > 2 else ""
            if key == "Reference":
                reference = val or "U"
            elif key == "Value":
                value = val or name
            elif key == "Footprint":
                footprint = val
        for unit in find_all(sym, "symbol"):
            for pin in find_all(unit, "pin"):
                at = find_one(pin, "at") or ["at", "0", "0", "0"]
                number_node = find_one(pin, "number")
                name_node = find_one(pin, "name")
                length_node = find_one(pin, "length")
                number = _clean(number_node[1]) if number_node and len(number_node) > 1 else ""
                if not number:
                    continue
                pins[number] = LibraryPin(
                    number=number,
                    name=_clean(name_node[1]) if name_node and len(name_node) > 1 else number,
                    x=float(at[1]) if len(at) > 1 else 0.0,
                    y=float(at[2]) if len(at) > 2 else 0.0,
                    angle=float(at[3]) if len(at) > 3 else 0.0,
                    length=float(length_node[1]) if length_node and len(length_node) > 1 else 2.54,
                    electrical=_clean(pin[1]) if len(pin) > 1 else "unspecified",
                )
        out[name] = LibrarySymbol(
            name=name,
            lib_id=f"{lib_name}:{name}",
            pins=pins,
            reference=reference,
            value=value,
            footprint=footprint,
            power=find_one(sym, "power") is not None,
            body=sym,
        )
    return out


# ---------------------------------------------------------------------------------------------
# Schematic document


@dataclass
class SymbolPlacement:
    """One symbol instance on the sheet."""

    lib_id: str
    reference: str
    value: str
    x: float
    y: float
    rotation: float = 0.0
    footprint: str = ""
    uuid: str = field(default_factory=new_uuid)
    properties: dict[str, str] = field(default_factory=dict)


@dataclass
class NetNode:
    """A point where a pin, wire end, label or junction can connect."""

    x: float
    y: float

    def key(self) -> tuple[float, float]:
        return (snap(self.x), snap(self.y))


@dataclass
class Schematic:
    """A ``.kicad_sch`` document that can be edited and written back.

    Wires and labels are stored as their S-expression nodes; symbols and pins are resolved
    through ``libraries``, which the caller loads once per edit session.
    """

    path: Path
    tree: list
    libraries: dict[str, dict[str, LibrarySymbol]] = field(default_factory=dict)

    # -- lifecycle -------------------------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path, libraries: dict[str, dict[str, LibrarySymbol]] | None = None) -> "Schematic":
        """Read ``path``; raises ``FileNotFoundError`` if there is no schematic.

        With no ``libraries``, symbols resolve out of the file's own ``lib_symbols`` block, which
        is what a schematic written by skidl or eeschema carries. Extra libraries passed in are
        searched first.
        """
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"no schematic at {path}")
        tree = parse(path.read_text(encoding="utf-8"))
        doc = cls(path=path, tree=tree, libraries=dict(libraries or {}))
        doc._load_embedded()
        return doc

    def _load_embedded(self) -> None:
        """Register the file's ``lib_symbols`` under the library name they are addressed by."""
        block = find_one(self.tree, "lib_symbols")
        if block is None:
            return
        for node in find_all(block, "symbol"):
            if len(node) < 2:
                continue
            full = _clean(node[1])
            if ":" not in full:
                continue
            lib, name = full.split(":", 1)
            table = self.libraries.setdefault(lib, {})
            if name in table:
                continue
            table[name] = _embedded_symbol(full, node)

    @classmethod
    def create(
        cls, path: str | Path, paper: str = "A4", libraries: dict[str, dict[str, LibrarySymbol]] | None = None
    ) -> "Schematic":
        """Start an empty schematic and write it (see ``empty()`` for the in-memory form)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(empty_schematic(paper), encoding="utf-8")
        return cls.load(path, libraries)

    def save(self) -> None:
        """Write the document back, keeping a trailing newline."""
        self.path.write_text(write_top(self.tree) + "\n", encoding="utf-8")

    # -- queries ---------------------------------------------------------------------------

    @property
    def symbols(self) -> list[list]:
        return find_all(self.tree, "symbol")

    @property
    def wires(self) -> list[list]:
        return find_all(self.tree, "wire")

    def placements(self) -> list[SymbolPlacement]:
        """Every placed symbol, in sheet order."""
        out = []
        for sym in self.symbols:
            lib_node = find_one(sym, "lib_id")
            lib_id = _clean(lib_node[1]) if lib_node and len(lib_node) > 1 else ""
            at = find_one(sym, "at") or ["at", "0", "0", "0"]
            props: dict[str, str] = {}
            for prop in find_all(sym, "property"):
                if len(prop) > 2:
                    props[_clean(prop[1])] = _clean(prop[2])
            uw = find_one(sym, "uuid")
            out.append(
                SymbolPlacement(
                    lib_id=lib_id,
                    reference=props.get("Reference", "?"),
                    value=props.get("Value", ""),
                    x=float(at[1]),
                    y=float(at[2]),
                    rotation=float(at[3]) if len(at) > 3 else 0.0,
                    footprint=props.get("Footprint", ""),
                    uuid=str(uw[1]) if uw and len(uw) > 1 else "",
                    properties=props,
                )
            )
        return out

    def lookup(self, lib_id: str) -> LibrarySymbol | None:
        """The library symbol for ``lib_id`` (``lib:Name`` or a bare ``Name``)."""
        if ":" in lib_id:
            lib, name = lib_id.split(":", 1)
            table = self.libraries.get(lib)
            return table.get(name) if table else None
        for table in self.libraries.values():
            if lib_id in table:
                return table[lib_id]
        return None

    def pin_position(self, placement: SymbolPlacement, number: str) -> tuple[float, float] | None:
        """Absolute sheet position of a pin, using the placement's rotation.

        KiCad rotates a symbol's pins about its origin; a pin at local ``(px, py)`` placed at
        ``(x, y)`` with rotation ``r`` lands at ``(x + px*cos - py*sin, y + px*sin + py*cos)``
        with screen-positive angles, which is what ``at`` stores.
        """
        symbol = self.lookup(placement.lib_id)
        if symbol is None:
            return None
        pin = symbol.pins.get(_clean(number))
        if pin is None:
            return None
        angle = math.radians(placement.rotation)
        cos, sin = math.cos(angle), math.sin(angle)
        # Library symbol space has +y UP, but sheet coordinates have +y DOWN, so a pin's local
        # y is negated before the placement offset is added. Getting this sign wrong puts the
        # label on the opposite pin: verified against kicad-cli, where a label above a
        # Device:R lands on pin 1 (library y = +3.81, sheet y = placement.y - 3.81).
        px, py = pin.x, -pin.y
        return (
            round(placement.x + px * cos - py * sin, 6),
            round(placement.y + px * sin + py * cos, 6),
        )

    def pin_endpoint(self, placement: SymbolPlacement, number: str) -> tuple[float, float] | None:
        """Absolute position of a pin's *outer* end (anchor plus the pin's own length).

        KiCad connects at the anchor, but generators such as skidl draw wires to this point, so
        connectivity considers both.
        """
        symbol = self.lookup(placement.lib_id)
        if symbol is None:
            return None
        pin = symbol.pins.get(_clean(number))
        anchor = self.pin_position(placement, number)
        if pin is None or anchor is None:
            return None
        angle = math.radians(placement.rotation + pin.angle)
        return (
            round(anchor[0] + pin.length * math.cos(angle), 6),
            round(anchor[1] - pin.length * math.sin(angle), 6),
        )

    def pin_points(self, placement: SymbolPlacement, number: str) -> list[tuple[float, float]]:
        """Where a pin can connect: its anchor, and the outer end generators draw wires to."""
        points = []
        anchor = self.pin_position(placement, number)
        if anchor is not None:
            points.append(anchor)
        end = self.pin_endpoint(placement, number)
        if end is not None and end != anchor:
            points.append(end)
        return points

    def pin_net(self, placement: SymbolPlacement, number: str) -> str | None:
        """Net attached to a pin, following wires transitively.

        A pin is often joined to its net by a *chain* of wire segments (skidl draws one segment
        per leg), with the label somewhere else on the chain. Looking only at wires that touch
        the pin directly made every chained connection read as unconnected.
        """
        keys = {NetNode(*p).key() for p in self.pin_points(placement, number)}
        if not keys:
            return None

        # Wire adjacency: point -> neighbouring points on the same segment.
        adjacency: dict[tuple[float, float], set[tuple[float, float]]] = {}
        for wire in self.wires:
            pts = find_one(wire, "pts")
            if not pts:
                continue
            ends = [NetNode(float(p[1]), float(p[2])).key() for p in find_all(pts, "xy") if len(p) > 2]
            for a, b in zip(ends, ends[1:]):
                adjacency.setdefault(a, set()).add(b)
                adjacency.setdefault(b, set()).add(a)

        labels: dict[tuple[float, float], str] = {}
        for label in find_all(self.tree, "label") + find_all(self.tree, "global_label"):
            at = find_one(label, "at")
            if at and len(at) > 2:
                labels[NetNode(float(at[1]), float(at[2])).key()] = _clean(label[1])

        # Breadth-first over wire segments from every point of the pin.
        seen: set[tuple[float, float]] = set()
        queue = [k for k in keys]
        while queue:
            point = queue.pop()
            if point in seen:
                continue
            seen.add(point)
            if point in labels:
                return labels[point]
            queue.extend(p for p in adjacency.get(point, ()) if p not in seen)
        return None

    def wire_net(self, wire: list) -> str | None:
        """The label attached to either end of ``wire``, if any."""
        pts = find_one(wire, "pts")
        if not pts:
            return None
        ends = {NetNode(float(p[1]), float(p[2])).key() for p in find_all(pts, "xy") if len(p) > 2}
        for label in find_all(self.tree, "label") + find_all(self.tree, "global_label"):
            at = find_one(label, "at")
            if at and len(at) > 2 and NetNode(float(at[1]), float(at[2])).key() in ends:
                return _clean(label[1]) if len(label) > 1 else None
        return None

    def build_connectivity(self) -> dict[str, list[dict[str, Any]]]:
        """Nets found by *geometry*: pins, wire endpoints and wire-junction points that meet.

        This is the general case, and what a schematic written by skidl or by hand needs:
        pins that sit on a wire endpoint join that wire's net, wires that share a point join
        each other, and a label anywhere on a point names the net. The net's name comes from
        the labels on its points, or ``Net-<n>`` when it has none.
        """
        parent: dict[tuple[float, float], tuple[float, float]] = {}

        def find(point: tuple[float, float]) -> tuple[float, float]:
            parent.setdefault(point, point)
            while parent[point] != point:
                parent[point] = parent[parent[point]]
                point = parent[point]
            return point

        def union(a: tuple[float, float], b: tuple[float, float]) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        for wire in self.wires:
            pts = find_one(wire, "pts")
            if not pts:
                continue
            ends = [NetNode(float(p[1]), float(p[2])).key() for p in find_all(pts, "xy") if len(p) > 2]
            for point in ends:
                find(point)
            for a, b in zip(ends, ends[1:]):
                union(a, b)

        labels: dict[tuple[float, float], str] = {}
        for label in find_all(self.tree, "label") + find_all(self.tree, "global_label"):
            at = find_one(label, "at")
            if not at or len(at) < 3:
                continue
            point = NetNode(float(at[1]), float(at[2])).key()
            labels[point] = _clean(label[1])
            find(point)

        pin_nodes: list[tuple[tuple[float, float], str, str, str]] = []
        power_points: dict[str, list[tuple[float, float]]] = {}
        for placement in self.placements():
            symbol = self.lookup(placement.lib_id)
            if symbol is None:
                continue
            if placement.reference.startswith("#"):
                # Power symbols (#PWR..) name a global net through their value and connect at
                # their single pin; they are not parts on the board.
                if placement.lib_id.startswith("power:") or symbol.power:
                    for number in symbol.pins:
                        points = self.pin_points(placement, number)
                        if points:
                            power_points.setdefault(placement.value or symbol.value, []).extend(points)
                continue
            for number, pin in symbol.pins.items():
                points = self.pin_points(placement, number)
                if not points:
                    continue
                entry = (placement.reference, number, pin.name)
                for position in points:
                    point = NetNode(*position).key()
                    pin_nodes.append((point, *entry))
                    find(point)
                # A pin's own points are always connected to each other.
                for a, b in zip(points, points[1:]):
                    union(NetNode(*a).key(), NetNode(*b).key())

        groups: dict[tuple[float, float], list[dict[str, Any]]] = {}
        for point, ref, number, name in pin_nodes:
            members = groups.setdefault(find(point), [])
            entry = {"ref": ref, "pin": number, "name": name}
            if entry not in members:
                members.append(entry)

        names: dict[tuple[float, float], str] = {}
        # Explicit labels win over power-symbol names, and both are deterministic: with two
        # names at one point, sorting decides rather than dict insertion order.
        for point, name in sorted(labels.items()):
            names.setdefault(find(point), name)
        for net_name, points in sorted(power_points.items()):
            for point in points:
                mapped = NetNode(*point).key()
                if mapped in parent:
                    names.setdefault(find(mapped), net_name)

        out: dict[str, list[dict[str, Any]]] = {}
        unnamed = 0
        for root, members in groups.items():
            if len(members) < 2 and root not in names:
                continue
            name = names.get(root)
            if not name:
                unnamed += 1
                name = f"Net-({root[0]:g},{root[1]:g})"
            out.setdefault(name, []).extend(members)
        return dict(sorted(out.items()))

    def nets(self) -> dict[str, list[dict[str, Any]]]:
        """``{net: [{ref, pin, name}]}`` for every connection on the sheet.

        Uses geometry (``build_connectivity``), so it sees skidl's drawn wires as well as
        labels.
        """
        return self.build_connectivity()

    def index(self) -> dict[str, Any]:
        """A compact description of the sheet, for the agent and the UI."""
        placements = self.placements()
        nets = self.nets()
        size = find_one(self.tree, "paper")
        paper = _clean(size[1]) if size and len(size) > 1 else "A4"
        return {
            "paper": paper,
            "sheet": list(PAPER.get(paper, PAPER["A4"])),
            "symbols": [
                {
                    "reference": p.reference,
                    "value": p.value,
                    "lib_id": p.lib_id,
                    "x": p.x,
                    "y": p.y,
                    "rotation": p.rotation,
                    "footprint": p.footprint,
                    "pins": list(self.lookup(p.lib_id).pins) if self.lookup(p.lib_id) else [],
                }
                for p in placements
            ],
            "wires": len(self.wires),
            "labels": len(find_all(self.tree, "label")),
            "global_labels": len(find_all(self.tree, "global_label")),
            "nets": {name: members for name, members in sorted(nets.items())},
            "unconnected_pins": self.unconnected_pins(),
        }

    def unconnected_pins(self) -> list[dict[str, str]]:
        """Pins that reach no net — the ones ERC complains about.

        Uses the full connectivity graph (wires, labels *and* pins that meet at a point, which
        is how a pin joins a power symbol), because a pin can be connected with no label and no
        wire of its own.
        """
        connected = {
            (member["ref"], str(member["pin"]))
            for members in self.build_connectivity().values()
            for member in members
        }
        out = []
        for placement in self.placements():
            symbol = self.lookup(placement.lib_id)
            if symbol is None or placement.reference.startswith("#"):
                continue
            for number in symbol.pins:
                if (placement.reference, str(number)) not in connected:
                    out.append({"ref": placement.reference, "pin": number})
        return out

    # -- edits -----------------------------------------------------------------------------

    def add_symbol(
        self,
        lib_id: str,
        reference: str,
        x: float,
        y: float,
        rotation: float = 0.0,
        value: str | None = None,
        footprint: str | None = None,
        properties: dict[str, str] | None = None,
    ) -> SymbolPlacement:
        """Place a library symbol. Raises ``ValueError`` if it isn't in a loaded library."""
        symbol = self.lookup(lib_id)
        if symbol is None:
            known = sorted(name for table in self.libraries.values() for name in table)[:40]
            raise ValueError(f"no symbol {lib_id!r} in the loaded libraries; available: {', '.join(known)}")
        if any(p.reference == reference for p in self.placements()):
            raise ValueError(f"reference {reference!r} is already placed")
        placement = SymbolPlacement(
            lib_id=symbol.lib_id,
            reference=reference,
            value=value if value is not None else symbol.value,
            x=snap(x),
            y=snap(y),
            rotation=rotation,
            footprint=footprint if footprint is not None else symbol.footprint,
            properties=dict(properties or {}),
        )
        self._ensure_lib_symbol(symbol)
        self._remove_top("symbol_instances")
        node = [
            "symbol",
            ["lib_id", Q(placement.lib_id)],
            ["at", num(placement.x), num(placement.y), num(placement.rotation)],
            ["unit", "1"],
            ["exclude_from_sim", "no"],
            ["in_bom", "yes"],
            ["on_board", "yes"],
            ["dnp", "no"],
            ["uuid", Q(placement.uuid)],
            _instance_property(
                "Reference", placement.reference, placement.x + 2.54, placement.y - 2.54
            ),
            _instance_property("Value", placement.value, placement.x + 2.54, placement.y),
            _instance_property(
                "Footprint", placement.footprint, placement.x, placement.y, hidden=True
            ),
            _instance_property("Datasheet", "", placement.x, placement.y, hidden=True),
        ]
        for key, val in placement.properties.items():
            node.append(_instance_property(key, str(val), placement.x, placement.y, hidden=True))
        # No per-pin (pin N (uuid ...)) entries: kicad-cli rejects them in this project's files,
        # and they only assign UUIDs to pin instances, which nothing downstream needs.
        self._insert_after_uuid(node)
        return placement

    def remove_symbol(self, reference: str) -> bool:
        """Delete a placed symbol and any wires that end on its pins."""
        target = next((p for p in self.placements() if p.reference == reference), None)
        if target is None:
            return False
        ends = set()
        for number in (self.lookup(target.lib_id).pins if self.lookup(target.lib_id) else {}):
            position = self.pin_position(target, number)
            if position:
                ends.add(NetNode(*position).key())
        self.tree[:] = [
            child
            for child in self.tree
            if not (
                isinstance(child, list)
                and child
                and child[0] == "symbol"
                and find_one(child, "uuid")
                and find_one(child, "uuid")[1] == target.uuid
            )
        ]
        self.tree[:] = [c for c in self.tree if not self._wire_touches(c, ends)]
        return True

    def move_symbol(self, reference: str, x: float, y: float,
                    rotation: float | None = None) -> bool:
        """Move (and optionally rotate) a placed symbol, carrying its reference/value text.

        Used by the tidy layout, which re-places every symbol on a grid: the text has to travel
        with the body, or the sheet ends up with references floating where the symbols used to be.
        """
        node = self._symbol_node(reference)
        if node is None:
            return False
        at = find_one(node, "at")
        if at is None or len(at) < 3:
            return False
        old_x, old_y = float(at[1]), float(at[2])
        new_x, new_y = snap(x), snap(y)
        at[1] = num(new_x)
        at[2] = num(new_y)
        if rotation is not None and len(at) > 3:
            # KiCad stores a positive angle; a negative one makes the whole file fail to load.
            at[3] = num(float(rotation) % 360.0)
        dx, dy = new_x - old_x, new_y - old_y
        if dx or dy:
            for prop in find_all(node, "property"):
                prop_at = find_one(prop, "at")
                if prop_at and len(prop_at) > 2:
                    prop_at[1] = num(float(prop_at[1]) + dx)
                    prop_at[2] = num(float(prop_at[2]) + dy)
        return True

    def set_symbol_value(self, reference: str, value: str) -> bool:
        """Change a symbol's Value (for a power symbol, that is its net name)."""
        node = self._symbol_node(reference)
        if node is None:
            return False
        for prop in find_all(node, "property"):
            if len(prop) > 2 and str(prop[1]) == "Value":
                prop[2] = value
                return True
        return False

    def remove_wires(self, *, net: str | None = None, reference: str | None = None) -> int:
        """Delete the wires of ``net`` (with its labels), or every wire touching a symbol's pins."""
        removed = 0
        pins: set[tuple[float, float]] = set()
        if reference:
            placement = self._placement(reference)
            symbol = self.lookup(placement.lib_id) if placement else None
            if placement and symbol:
                for number in symbol.pins:
                    point = self.pin_position(placement, number)
                    if point:
                        pins.add(NetNode(*point).key())
        keep = []
        for child in self.tree:
            if isinstance(child, list) and child and child[0] == "wire":
                pts = find_one(child, "pts")
                ends = {NetNode(float(q[1]), float(q[2])).key()
                        for q in find_all(pts, "xy")} if pts else set()
                if (net is not None and self.wire_net(child) == net) or (pins and ends & pins):
                    removed += 1
                    continue
            if (net is not None and isinstance(child, list) and child
                    and child[0] in ("label", "global_label") and str(child[1]) == net):
                removed += 1
                continue
            keep.append(child)
        self.tree[:] = keep
        return removed

    def add_no_connect(self, x: float, y: float) -> list:
        """Mark a pin as deliberately unconnected (KiCad's no-connect cross)."""
        node = ["no_connect", ["at", num(snap(x)), num(snap(y))], ["uuid", Q(new_uuid())]]
        self._insert_after_uuid(node)
        return node

    def _symbol_node(self, reference: str) -> list | None:
        """The top-level ``symbol`` node whose Reference property is ``reference``."""
        for symbol in self.symbols:
            for prop in find_all(symbol, "property"):
                if len(prop) > 2 and str(prop[1]) == "Reference" and str(prop[2]) == reference:
                    return symbol
        return None

    def add_wire(self, points: Sequence[Sequence[float]], net: str | None = None) -> list[list]:
        """Add a wire through ``points`` and, optionally, a local label on its first point.

        KiCad stores one ``wire`` per straight segment, so a bent path becomes several wires
        sharing endpoints. Returns the wire nodes.
        """
        if len(points) < 2:
            raise ValueError("a wire needs at least two points")
        coords = [(snap(x), snap(y)) for x, y in points]
        nodes = []
        for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
            if (x1, y1) == (x2, y2):
                continue
            node = [
                "wire",
                ["pts", ["xy", num(x1), num(y1)], ["xy", num(x2), num(y2)]],
                ["stroke", ["width", "0"], ["type", "default"]],
                ["uuid", Q(new_uuid())],
            ]
            self._insert_after_uuid(node)
            nodes.append(node)
        if not nodes:
            raise ValueError("a wire needs two distinct points")
        if net:
            self.add_label(net, *coords[0], anchor="left")
        return nodes

    def add_label(self, name: str, x: float, y: float, anchor: str = "left", global_: bool = False) -> list:
        """Add a local (or global) label at ``(x, y)``."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("a label needs a name")
        node = [
            "global_label" if global_ else "label",
            name.strip(),
            ["at", num(snap(x)), num(snap(y)), "0"],
            ["fields_autoplaced", "yes"],
            ["effects", ["font", ["size", "1.27", "1.27"]], ["justify", anchor]],
            ["uuid", Q(new_uuid())],
        ]
        self._insert_after_uuid(node)
        return node

    def draw_pin_stubs(self, length: float = 2.54) -> dict[str, int]:
        """Give every labelled pin a visible stub wire from the pin out to its net label.

        Generators (and ``generate_schematic_skidl`` in particular) place a net label exactly on
        each pin anchor. That is electrically correct — KiCad joins them — but nothing is drawn,
        so the sheet looks like a floor plan with no wires and reads as unfinished.

        For each label sitting on a pin, this moves the label ``length`` outward along the pin's
        own direction and wires pin → label. Real schematics look like this (pin, stub, net
        name), and it cannot overlap another pin because the stub only extends along a direction
        that is checked first.

        Returns ``{"stubbed": n, "skipped_occupied": n}``.
        """
        # Every pin point, so a stub never lands on a foreign pin (which would join it).
        pin_points: dict[tuple[float, float], list[tuple[str, str]]] = {}
        outward: dict[tuple[float, float], tuple[float, float]] = {}
        for placement in self.placements():
            symbol = self.lookup(placement.lib_id)
            if symbol is None:
                continue
            for number in symbol.pins:
                anchor = self.pin_position(placement, number)
                if anchor is None:
                    continue
                key = NetNode(*anchor).key()
                pin_points.setdefault(key, []).append((placement.reference, number))
                end = self.pin_endpoint(placement, number)
                if end and end != anchor:
                    outward.setdefault(key, (end[0] - anchor[0], end[1] - anchor[1]))

        labels = find_all(self.tree, "label") + find_all(self.tree, "global_label")
        stubbed = skipped = 0
        for label in labels:
            at = find_one(label, "at")
            if not at or len(at) < 3:
                continue
            point = NetNode(float(at[1]), float(at[2])).key()
            direction = outward.get(point)
            if direction is None:
                continue  # a label on a wire or free space, not on a pin
            norm = math.hypot(*direction)
            if norm == 0:
                continue
            target = (
                round(point[0] + direction[0] / norm * length, 6),
                round(point[1] + direction[1] / norm * length, 6),
            )
            target_key = NetNode(*target).key()
            if target_key in pin_points:
                skipped += 1
                continue
            self.add_wire([point, target])
            at[1] = num(target[0])
            at[2] = num(target[1])
            stubbed += 1

        return {"stubbed": stubbed, "skipped_occupied": skipped}

    def set_symbol_property(self, reference: str, name: str, value: str) -> bool:
        """Set (or add) a property on a placed symbol, e.g. the LCSC part number for the BOM."""
        for sym in find_all(self.tree, "symbol"):
            lib_node = find_one(sym, "lib_id")
            if lib_node is None:
                continue  # a library definition, not a placement
            props = {
                _clean(p[1]) if len(p) > 1 else "": p
                for p in find_all(sym, "property")
            }
            ref_prop = props.get("Reference")
            if ref_prop is None or len(ref_prop) < 3 or _clean(ref_prop[2]) != reference:
                continue
            existing = props.get(name)
            if existing is not None:
                if len(existing) > 2:
                    existing[2] = Q(str(value))
                return True
            at = find_one(sym, "at") or ["at", "0", "0", "0"]
            sym.append(
                [
                    "property", Q(name), Q(str(value)),
                    ["at", at[1], at[2], "0"],
                    ["effects", ["font", ["size", "1.27", "1.27"]], "hide"],
                ]
            )
            return True
        return False

    def add_junction(self, x: float, y: float) -> list:
        """Mark a T-connection where three or more wires meet."""
        node = ["junction", ["at", num(snap(x)), num(snap(y))], ["diameter", "0"], ["color", "0", "0", "0", "0"], ["uuid", Q(new_uuid())]]
        self._insert_after_uuid(node)
        return node

    def add_text(self, text: str, x: float, y: float, size: float = 1.27) -> list:
        """Free text on the sheet (notes, not connections)."""
        node = [
            "text",
            text,
            ["at", num(snap(x)), num(snap(y)), "0"],
            ["effects", ["font", ["size", num(size), num(size)]]],
            ["uuid", Q(new_uuid())],
        ]
        self._insert_after_uuid(node)
        return node

    def connect_pins(
        self, a: tuple[str, str | int], b: tuple[str, str | int], net: str | None = None
    ) -> str:
        """Wire pin ``a`` to pin ``b``, giving the connection a label.

        When ``net`` is omitted the net is named after the pins (``U1.3-R1.1``). Both pins get
        a label at their own position, which is what KiCad needs to see them joined even if the
        drawn wire is later moved.
        """
        ref_a, pin_a = a
        ref_b, pin_b = b
        pa = self._placement(ref_a)
        pb = self._placement(ref_b)
        if pa is None or pb is None:
            raise ValueError(f"no such symbol: {a if pa is None else b}")
        p1 = self.pin_position(pa, str(pin_a))
        p2 = self.pin_position(pb, str(pin_b))
        if p1 is None or p2 is None:
            raise ValueError(f"unknown pin: {a if p1 is None else b}")
        name = net or f"{ref_a}.{pin_a}-{ref_b}.{pin_b}"
        self._route(p1, p2)
        self.add_label(name, p1[0], p1[1])
        self.add_label(name, p2[0], p2[1])
        return name

    def _all_pin_points(self, exclude: set[tuple[float, float]]) -> set[tuple[float, float]]:
        """Every pin anchor/end on the sheet except the two being connected.

        A wire that passes through one of these would silently join that pin to the net —
        KiCad connects at any shared point, including mid-wire — so routes must avoid them.
        """
        occupied: set[tuple[float, float]] = set()
        for placement in self.placements():
            symbol = self.lookup(placement.lib_id)
            if symbol is None:
                continue
            for number in symbol.pins:
                for point in self.pin_points(placement, number):
                    key = NetNode(*point).key()
                    if key not in exclude:
                        occupied.add(key)
        return occupied

    def _segment_hits(self, a: tuple[float, float], b: tuple[float, float],
                      occupied: set[tuple[float, float]]) -> bool:
        """Whether the straight segment ``a``-``b`` passes through an occupied point."""
        (x1, y1), (x2, y2) = a, b
        for (px, py) in occupied:
            within_x = min(x1, x2) - 1e-6 <= px <= max(x1, x2) + 1e-6
            within_y = min(y1, y2) - 1e-6 <= py <= max(y1, y2) + 1e-6
            if within_x and within_y:
                return True
        return False

    def _route(self, p1: tuple[float, float], p2: tuple[float, float]) -> None:
        """Draw a wire from ``p1`` to ``p2`` that does not run over any other pin.

        Tries the direct line, then an L bend at either corner, then a Z jog in a free channel.
        Every candidate is checked against all other pins first, because a wire crossing a pin
        silently connects to it.
        """
        exclude = {NetNode(*p1).key(), NetNode(*p2).key()}
        occupied = self._all_pin_points(exclude)

        # Candidates in order of preference; the first clear one wins.
        x1, y1 = p1
        x2, y2 = p2
        mid_y = snap((y1 + y2) / 2)
        mid_x = snap((x1 + x2) / 2)
        candidates = [
            [(p1, p2)],
            [(p1, (x2, y1)), ((x2, y1), p2)],
            [(p1, (x1, y2)), ((x1, y2), p2)],
            [(p1, (p1[0], mid_y)), ((p1[0], mid_y), (p2[0], mid_y)), ((p2[0], mid_y), p2)],
            [(p1, (mid_x, p1[1])), ((mid_x, p1[1]), (mid_x, p2[1])), ((mid_x, p2[1]), p2)],
        ]
        chosen = candidates[0]
        for option in candidates:
            if not any(self._segment_hits(a, b, occupied) for a, b in option):
                chosen = option
                break

        for a, b in chosen:
            if NetNode(*a).key() == NetNode(*b).key():
                continue
            self.add_wire([a, b])
        # Junctions only where the path genuinely turns.
        for i in range(1, len(chosen)):
            corner = chosen[i][0]
            if NetNode(*corner).key() not in exclude:
                self.add_junction(*corner)

    # -- internals -------------------------------------------------------------------------

    def _placement(self, reference: str) -> SymbolPlacement | None:
        return next((p for p in self.placements() if p.reference == reference), None)

    def _wire_touches(self, child: Any, ends: set[tuple[float, float]]) -> bool:
        if not (isinstance(child, list) and child and child[0] == "wire"):
            return False
        pts = find_one(child, "pts")
        if not pts:
            return False
        return any(NetNode(float(p[1]), float(p[2])).key() in ends for p in find_all(pts, "xy") if len(p) > 2)

    def _remove_top(self, head: str) -> None:
        self.tree[:] = [c for c in self.tree if not (isinstance(c, list) and c and c[0] == head)]

    def _insert_after_uuid(self, node: list) -> None:
        """Insert a new top-level node after the header, before ``sheet_instances``."""
        for i, child in enumerate(self.tree):
            if isinstance(child, list) and child and child[0] == "sheet_instances":
                self.tree.insert(i, node)
                return
        self.tree.append(node)

    def _ensure_lib_symbol(self, symbol: LibrarySymbol) -> None:
        """Copy a library symbol into ``lib_symbols`` if it isn't cached there yet."""
        lib_block = find_one(self.tree, "lib_symbols")
        if lib_block is None:
            lib_block = ["lib_symbols"]
            self._insert_after_uuid(lib_block)
        full_name = symbol.lib_id
        for existing in find_all(lib_block, "symbol"):
            if len(existing) > 1 and existing[1] == full_name:
                return
        body = _rename_symbol(symbol.body, full_name)
        lib_block.append(body)


def _embedded_symbol(full_name: str, node: list) -> LibrarySymbol:
    """A ``LibrarySymbol`` built from a ``lib_symbols`` entry inside a schematic file."""
    pins: dict[str, LibraryPin] = {}
    reference, value, footprint = "U", full_name.split(":", 1)[-1], ""
    for prop in find_all(node, "property"):
        key = _clean(prop[1]) if len(prop) > 1 else ""
        val = _clean(prop[2]) if len(prop) > 2 else ""
        if key == "Reference":
            reference = val or "U"
        elif key == "Value":
            value = val or value
        elif key == "Footprint":
            footprint = val
    for unit in find_all(node, "symbol"):
        for pin in find_all(unit, "pin"):
            at = find_one(pin, "at") or ["at", "0", "0", "0"]
            number_node = find_one(pin, "number")
            name_node = find_one(pin, "name")
            length_node = find_one(pin, "length")
            number = _clean(number_node[1]) if number_node and len(number_node) > 1 else ""
            if not number:
                continue
            pins[number] = LibraryPin(
                number=number,
                name=_clean(name_node[1]) if name_node and len(name_node) > 1 else number,
                x=float(at[1]) if len(at) > 1 else 0.0,
                y=float(at[2]) if len(at) > 2 else 0.0,
                angle=float(at[3]) if len(at) > 3 else 0.0,
                length=float(length_node[1]) if length_node and len(length_node) > 1 else 2.54,
                electrical=_clean(pin[1]) if len(pin) > 1 else "unspecified",
            )
    return LibrarySymbol(
        name=full_name.split(":", 1)[-1],
        lib_id=full_name,
        pins=pins,
        reference=reference,
        value=value,
        footprint=footprint,
        power=find_one(node, "power") is not None,
        body=node,
    )


def _instance_property(name: str, value: str, x: float, y: float, hidden: bool = False) -> list:
    """A placed symbol's property in the shape KiCad 10 writes it."""
    effects = ["effects", ["font", ["size", "1.27", "1.27"]]]
    if hidden:
        effects.append("hide")
    return ["property", Q(name), Q(str(value)), ["at", num(x), num(y), "0"], effects]


def _rename_symbol(body: list, full_name: str) -> list:
    """Give a library symbol its ``lib:Name`` id, keeping unit sub-symbols named ``Name_0_1``.

    KiCad resolves a unit by stripping the ``lib:`` prefix off the parent id and looking for
    ``Name_<unit>_<style>``, so the sub-symbols keep the short name.
    """
    short = full_name.split(":", 1)[-1]
    out = list(body)
    out[1] = Q(full_name)
    for i, child in enumerate(out):
        if isinstance(child, list) and child and child[0] == "symbol" and len(child) > 1:
            renamed = list(child)
            renamed[1] = Q(str(child[1]).split(":", 1)[-1])
            out[i] = renamed
    # KiCad 10 dropped numeric property ids; a stale ``(id N)`` makes eeschema refuse the file.
    cleaned = []
    for child in out:
        if isinstance(child, list) and child:
            if child[0] == "property":
                child = [c for c in child if not (isinstance(c, list) and c and c[0] == "id")]
                if len(child) > 1:
                    child[1] = Q(str(child[1]))
                if len(child) > 2:
                    child[2] = Q(str(child[2]))
            elif child[0] == "symbol":
                child = _strip_property_ids(child)
        cleaned.append(child)
    return cleaned


def _strip_property_ids(node: list) -> list:
    out = []
    for child in node:
        if isinstance(child, list) and child:
            if child[0] == "property":
                child = [c for c in child if not (isinstance(c, list) and c and c[0] == "id")]
                if len(child) > 1:
                    child[1] = Q(str(child[1]))
                if len(child) > 2:
                    child[2] = Q(str(child[2]))
            elif child[0] == "symbol":
                child = _strip_property_ids(child)
        out.append(child)
    return out


def empty_schematic(paper: str = "A4") -> str:
    """The text of a new, empty schematic."""
    if paper not in PAPER:
        raise ValueError(f"unknown paper size {paper!r}: use one of {', '.join(PAPER)}")
    tree = [
        "kicad_sch",
        ["version", SCHEMATIC_VERSION],
        ["generator", Q(GENERATOR)],
        ["generator_version", Q("10.0")],
        ["uuid", Q(new_uuid())],
        ["paper", Q(paper)],
        ["lib_symbols"],
        ["sheet_instances", ["path", Q("/"), ["page", Q("1")]]],
    ]
    return write_top(tree) + "\n"


def board_netlist(schematic: Schematic) -> dict[str, Any]:
    """Footprints and pad-to-net assignments implied by the schematic, for board seeding.

    Returns ``{"components": [{ref, value, footprint, x, y}], "nets": {net: [{ref, pin}]}}``.
    Symbols without a footprint are left out of ``components`` so layout can still start.
    """
    components = []
    for placement in schematic.placements():
        components.append(
            {
                "ref": placement.reference,
                "value": placement.value,
                "footprint": placement.footprint,
                "x": placement.x,
                "y": placement.y,
            }
        )
    nets = {name: members for name, members in schematic.nets().items()}
    return {"components": components, "nets": nets}


def iter_pins(schematic: Schematic) -> Iterable[tuple[SymbolPlacement, str]]:
    """Every (placement, pin number) pair on the sheet."""
    for placement in schematic.placements():
        symbol = schematic.lookup(placement.lib_id)
        if symbol is None:
            continue
        for number in symbol.pins:
            yield placement, number
