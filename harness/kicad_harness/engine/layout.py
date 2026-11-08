"""Schematic legibility: measure the drawing, and tidy it into a labelled layout.

ERC says whether a schematic is *correct*; it says nothing about whether a human can read it.
Two symbols drawn on top of each other, a net label buried under a part, or a wire running
straight through an unrelated symbol are all ERC-clean and all unreadable.

This module turns "legible" into numbers taken from the drawing itself:

* ``symbol_overlaps`` — two symbol bodies share more than a token area.
* ``label_overlaps`` — a net label (or a symbol's reference/value text) sits on another one, or
  on a symbol body it does not belong to.
* ``wires_through_symbols`` — a wire crosses a symbol body that is not one of its own endpoints.
* ``dangling_wires`` — a wire end touches neither a pin, a label, a junction nor another wire.
* ``off_sheet`` — an object lies outside the page.
* ``wire_crossings`` — two wires cross in mid-air. Legal in KiCad, but reported because a
  crossing is exactly where a reader loses track of which net is which.

The *score* counts the first five (the ones that make a sheet unreadable); crossings are
reported separately and are not part of the pass/fail score.

``tidy`` rewrites the sheet as a grid of symbols with one short stub and one net label per pin.
That style is legible by construction — no long wires, no crossings, no labels under parts — and
because KiCad merges labels by name it preserves the connectivity the sheet already had.
"""

from __future__ import annotations

import math
import re
from collections import deque
from typing import Any, Iterable

from .schematic import PAPER, POWER_NETS, Schematic, find_all, find_one, num

MARGIN = 10.0          # sheet border left free around the grid
GAP = 4.0              # clear space between neighbouring symbol boxes
STUB = 3.81            # stub length from a pin to its label
TEXT_H = 2.4           # a 1.27 mm label's rendered height, with a little air
CHAR_W = 1.15          # rough advance of a 1.27 mm KiCad character
MIN_LABEL_W = 9.0
MAX_LABEL_W = 30.0
PAPERS = ["A4", "A3", "A2", "A1", "A0"]
TITLE_BAND = 65.0      # room at the foot of a cropped sheet for KiCad's title block


# ---------------------------------------------------------------------------------------------
# Geometry


def _text(node: Any) -> str:
    return str(node[1]) if len(node) > 1 else ""


def _justify(node: Any) -> str:
    effects = find_one(node, "effects")
    if effects is None:
        return "left"
    justify = find_one(effects, "justify")
    if justify is None:
        return "left"
    return "right" if any(str(token) == "right" for token in justify[1:]) else "left"


def _label_box(node: Any) -> tuple[float, float, float, float] | None:
    at = find_one(node, "at")
    if at is None or len(at) < 3:
        return None
    x, y = float(at[1]), float(at[2])
    width = min(MAX_LABEL_W, max(MIN_LABEL_W, CHAR_W * len(_text(node))))
    if _justify(node) == "right":
        return (x - width, y - TEXT_H / 2, x, y + TEXT_H / 2)
    return (x, y - TEXT_H / 2, x + width, y + TEXT_H / 2)


def _pin_points(doc: Schematic, placement) -> list[tuple[str, tuple[float, float]]]:
    symbol = doc.lookup(placement.lib_id)
    if symbol is None:
        return []
    out = []
    for number in symbol.pins:
        position = doc.pin_position(placement, number)
        if position is not None:
            out.append((number, position))
    return out


def symbol_boxes(doc: Schematic) -> dict[str, tuple[float, float, float, float]]:
    """Bounding box of every placed symbol, in sheet mm.

    The box is taken from the pin tips (the outermost geometry the drawing shows) with one grid
    step of margin for the reference and value text.
    """
    boxes: dict[str, tuple[float, float, float, float]] = {}
    for placement in doc.placements():
        points = [p for _, p in _pin_points(doc, placement)]
        if not points:
            points = [(placement.x, placement.y)]
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        boxes[placement.reference] = (
            min(xs) - 1.27, min(ys) - 1.27, max(xs) + 1.27, max(ys) + 1.27
        )
    return boxes


def _segments(doc: Schematic) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    out = []
    for wire in doc.wires:
        pts = find_one(wire, "pts")
        if pts is None:
            continue
        points = [(float(p[1]), float(p[2])) for p in find_all(pts, "xy") if len(p) > 2]
        out.extend(zip(points, points[1:]))
    return out


def _overlap_area(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 1e-9 and h > 1e-9 else 0.0


def _inside(point: tuple[float, float], box) -> bool:
    return box[0] < point[0] < box[2] and box[1] < point[1] < box[3]


def _segments_cross(a, b, c, d) -> bool:
    """True when the two segments cross strictly inside both (shared endpoints do not count)."""

    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    o1, o2 = orient(a, b, c), orient(a, b, d)
    o3, o4 = orient(c, d, a), orient(c, d, b)
    return (
        (o1 > 1e-9 and o2 < -1e-9 or o1 < -1e-9 and o2 > 1e-9)
        and (o3 > 1e-9 and o4 < -1e-9 or o3 < -1e-9 and o4 > 1e-9)
    )


def _segment_hits_box(a, b, box, *, skip: Iterable[tuple[float, float]] = ()) -> bool:
    """Whether segment a-b passes through the *interior* of ``box``, ignoring its own ends."""
    skip = list(skip)
    # Sample the segment at 0.25 mm. Schematics are small sheets of axis-aligned wires, so a
    # dense sample is both exact enough and far simpler than clipping against every edge.
    length = math.dist(a, b)
    steps = max(1, int(length / 0.25))
    for i in range(1, steps):
        t = i / steps
        p = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        if any(math.dist(p, q) < 0.4 for q in skip):
            continue
        if _inside(p, box):
            return True
    return False


def _paper(doc: Schematic) -> tuple[str, tuple[float, float]]:
    node = find_one(doc.tree, "paper")
    name = str(node[1]) if node and len(node) > 1 else "A4"
    if name == "User" and node and len(node) > 3:
        return "User", (float(node[2]), float(node[3]))
    return name, PAPER.get(name, PAPER["A4"])


# ---------------------------------------------------------------------------------------------
# Measurement


def analyze(doc: Schematic) -> dict[str, Any]:
    """Measure how legible the sheet is. ``score`` counts the unreadable-drawing faults."""
    boxes = symbol_boxes(doc)
    refs = list(boxes)
    violations: list[dict[str, Any]] = []

    # Symbols drawn on top of each other.
    overlaps = 0
    for i, a in enumerate(refs):
        for b in refs[i + 1:]:
            area = _overlap_area(boxes[a], boxes[b])
            if area > 0.5:
                overlaps += 1
                violations.append({"kind": "symbol_overlap", "refs": [a, b],
                                   "detail": f"{a} and {b} overlap by {area:.1f} mm^2"})

    # Net labels and symbol text on top of each other or over a foreign symbol body.
    texts: list[tuple[str, tuple[float, float, float, float]]] = []
    for kind in ("label", "global_label"):
        for node in find_all(doc.tree, kind):
            box = _label_box(node)
            if box:
                texts.append((_text(node), box))

    label_overlaps = 0
    for i, (text_a, box_a) in enumerate(texts):
        for text_b, box_b in texts[i + 1:]:
            if _overlap_area(box_a, box_b) > 0.02:
                label_overlaps += 1
                violations.append({"kind": "label_overlap", "texts": [text_a, text_b],
                                   "detail": f"{text_a!r} overlaps {text_b!r}"})
        for ref, box in boxes.items():
            if text_a == ref:
                continue  # its own symbol's text sits beside its own body
            if _overlap_area(box_a, box) > 0.5:
                label_overlaps += 1
                violations.append({"kind": "label_over_symbol", "texts": [text_a], "refs": [ref],
                                   "detail": f"{text_a!r} covers {ref}"})

    # Wires: crossings, and wires through unrelated symbol bodies.
    segments = _segments(doc)
    crossings = 0
    for i, (a, b) in enumerate(segments):
        for c, d in segments[i + 1:]:
            if _segments_cross(a, b, c, d):
                crossings += 1

    pin_points = {ref: [p for _, p in _pin_points(doc, placement)]
                  for placement in doc.placements()
                  for ref in [placement.reference]}
    through = 0
    for a, b in segments:
        for ref, box in boxes.items():
            own = pin_points.get(ref, [])
            # A segment that begins/ends on one of this symbol's pins is its own connection.
            if any(math.dist(a, p) < 0.05 or math.dist(b, p) < 0.05 for p in own):
                continue
            if _segment_hits_box(a, b, box):
                through += 1
                violations.append({"kind": "wire_through_symbol", "refs": [ref],
                                   "detail": f"a wire crosses {ref}"})
                break

    # Dangling wire ends: touching nothing at all.
    ends = [end for seg in segments for end in seg]
    contacts: set[tuple[float, float]] = set()
    for placement in doc.placements():
        contacts.update(p for _, p in _pin_points(doc, placement))
    for kind in ("label", "global_label"):
        for node in find_all(doc.tree, kind):
            at = find_one(node, "at")
            if at and len(at) > 2:
                contacts.add((float(at[1]), float(at[2])))
    for junction in find_all(doc.tree, "junction"):
        at = find_one(junction, "at")
        if at and len(at) > 2:
            contacts.add((float(at[1]), float(at[2])))

    def touches(point: tuple[float, float], others: list[tuple[float, float]]) -> bool:
        return any(math.dist(point, other) < 0.05 for other in others)

    dangling = 0
    for index, end in enumerate(ends):
        others = ends[:index] + ends[index + 1:]
        if not touches(end, list(contacts)) and not touches(end, others):
            dangling += 1
            violations.append({"kind": "dangling_wire", "at": [round(end[0], 2), round(end[1], 2)],
                               "detail": f"wire end at ({end[0]:.1f}, {end[1]:.1f}) touches nothing"})

    # Anything outside the page.
    _, (page_w, page_h) = _paper(doc)
    off_sheet = 0
    for ref, box in boxes.items():
        if box[0] < 0 or box[1] < 0 or box[2] > page_w or box[3] > page_h:
            off_sheet += 1
            violations.append({"kind": "off_sheet", "refs": [ref], "detail": f"{ref} is off the page"})
    for text, box in texts:
        if box[0] < 0 or box[1] < 0 or box[2] > page_w or box[3] > page_h:
            off_sheet += 1
            violations.append({"kind": "off_sheet", "texts": [text], "detail": f"{text!r} is off the page"})

    counts = {
        "symbol_overlaps": overlaps,
        "label_overlaps": label_overlaps,
        "wires_through_symbols": through,
        "dangling_wires": dangling,
        "off_sheet": off_sheet,
        "wire_crossings": crossings,
    }
    score = overlaps + label_overlaps + through + dangling + off_sheet
    return {
        "ok": score == 0,
        "score": score,
        "counts": counts,
        "paper": _paper(doc)[0],
        "symbols": len(boxes),
        "wires": len(segments),
        "labels": len(find_all(doc.tree, "label")) + len(find_all(doc.tree, "global_label")),
        "violations": violations[:40],
        "violation_count": len(violations),
    }


# ---------------------------------------------------------------------------------------------
# Tidy layout


def _natural_key(reference: str) -> tuple[str, int, str]:
    import re

    match = re.match(r"^(\D*?)(\d+)(.*)$", reference)
    if not match:
        return (reference, 0, "")
    return (match.group(1), int(match.group(2)), match.group(3))


def _alias_nets(doc: Schematic) -> dict[tuple[str, str], str]:
    """``(ref, pin) -> net name``, with coordinate-derived names turned into stable N$k."""
    nets = doc.nets()
    names = sorted(nets)
    alias: dict[str, str] = {}
    counter = 0
    for name in names:
        if name.startswith("Net-("):
            counter += 1
            alias[name] = f"N${counter}"
    pin_net: dict[tuple[str, str], str] = {}
    for name, members in nets.items():
        target = alias.get(name, name)
        for member in members:
            pin_net[(member["ref"], member["pin"])] = target
    return pin_net


def _label_width(text: str) -> float:
    return min(MAX_LABEL_W, max(MIN_LABEL_W, CHAR_W * len(text)))


def _box_at(text: str, x: float, y: float, anchor: str) -> tuple[float, float, float, float]:
    width = _label_width(text)
    if anchor == "right":
        return (x - width, y - TEXT_H / 2, x, y + TEXT_H / 2)
    return (x, y - TEXT_H / 2, x + width, y + TEXT_H / 2)


def _free(box, occupied: list, boxes: dict[str, tuple], page: tuple[float, float]) -> bool:
    """A candidate label box: on the page, clear of other labels and of every symbol body."""
    if box[0] < 1 or box[1] < 1 or box[2] > page[0] - 1 or box[3] > page[1] - 1:
        return False
    if any(_overlap_area(box, other) > 0.02 for other in occupied):
        return False
    if any(_overlap_area(box, body) > 0.5 for body in boxes.values()):
        return False
    return True


def tidy(doc: Schematic, *, paper: str | None = None) -> dict[str, Any]:
    """Relayout the sheet as a packed flow of symbols with a labelled stub on every connected pin.

    Symbols are placed left to right, each one advancing the row by *its own* width plus the room
    its labels need, and wrapping to a new row whose height is the tallest symbol in it. A single
    global cell (the first version) gave every 0805 resistor the footprint of the 40-pin header,
    which is why the sheet came out mostly empty.

    The spacing is chosen by measurement, not assumption: the layout is rebuilt with growing
    label allowances until ``analyze`` reports 0, so the result is as tight as it can be while
    still being legible.

    Returns before/after measurements. Connectivity is preserved: each net keeps its name and
    every pin that belonged to it gets a label with that name, which KiCad merges sheet-wide.
    """
    import copy

    before = analyze(doc)
    pin_net = _alias_nets(doc)
    # Power symbols (GND/+5V/PWR_FLAG) name nets through their value, but a PWR_FLAG's value is
    # "PWR_FLAG" — its real net is the one it was wired to. Record that before the wires go.
    power: dict[tuple[str, str], str] = {}
    for placement in doc.placements():
        if not placement.reference.startswith("#"):
            continue
        symbol = doc.lookup(placement.lib_id)
        if symbol is None:
            continue
        for number in symbol.pins:
            net = doc.pin_net(placement, number)
            power[(placement.reference, number)] = net or placement.value

    all_names = list(pin_net.values()) + list(power.values())
    label_w = max((_label_width(name) for name in all_names), default=MIN_LABEL_W)
    snapshot = copy.deepcopy(doc.tree)

    chosen_report: dict[str, Any] | None = None
    # Start tight and open up only as far as the measurement requires.
    for scale in (1.0, 1.25, 1.6, 2.1):
        doc.tree = copy.deepcopy(snapshot)
        result = _lay_out(doc, pin_net, power, label_w, scale=scale, paper=paper)
        after = analyze(doc)
        result.update(before=before, after=after)
        chosen_report = result
        if after["score"] == 0:
            break

    assert chosen_report is not None
    return chosen_report


def _pin_sides(doc: Schematic, placement, symbol) -> dict[str, bool]:
    """Which sides of a symbol a pin leaves from, so only those sides reserve label room."""
    need = {"left": False, "right": False, "top": False, "bottom": False}
    theta = math.radians(placement.rotation)
    for pin in symbol.pins.values():
        angle = math.radians(pin.angle)
        local_x, local_y = -math.cos(angle), math.sin(angle)
        ox = local_x * math.cos(theta) - local_y * math.sin(theta)
        oy = local_x * math.sin(theta) + local_y * math.cos(theta)
        if abs(ox) >= abs(oy):
            need["right" if ox >= 0 else "left"] = True
        else:
            need["bottom" if oy >= 0 else "top"] = True
    return need


_RAIL_NAME = re.compile(r"^(P\d+V\d*|\d+V\d*|VIN.*|VOUT.*|VBAT.*|VREF.*)$", re.IGNORECASE)


def _is_rail(name: str, fanout: int, total: int) -> bool:
    """A supply rail is not a signal: it must not pull the whole sheet into one layer.

    Named rails (+5V, GND, VBUS, VINBUCK, P20V, 3V3) and any net that fans out to a third of the
    design are treated as rails; everything else is signal and drives the left-to-right flow.
    """
    upper = (name or "").strip().upper()
    if not upper:
        return True
    if upper in POWER_NETS or upper.startswith(("+", "-", "GND", "VCC", "VDD", "VSS", "VBUS")):
        return True
    if _RAIL_NAME.match(upper):
        return True
    return total > 0 and fanout >= max(8, 0.35 * total)


def _graph(doc: Schematic) -> tuple[list[str], dict[str, set[str]], dict[str, set[str]]]:
    """``(components, signal adjacency, rail adjacency)`` from the schematic's own nets."""
    comps = [p.reference for p in doc.placements() if not p.reference.startswith("#")]
    nets = doc.nets()
    signal = {ref: set() for ref in comps}
    rails = {ref: set() for ref in comps}
    for name, members in nets.items():
        refs = {m["ref"] for m in members if not m["ref"].startswith("#") and m["ref"] in signal}
        if len(refs) < 2:
            continue
        target = rails if _is_rail(name, len(refs), len(comps)) else signal
        for ref in refs:
            target[ref].update(refs - {ref})
    return comps, signal, rails


def _islands(comps: list[str], signal: dict[str, set[str]]) -> tuple[list[list[str]], list[str]]:
    """Connected groups of parts that share signals, plus the parts that share no signal at all."""
    seen: set[str] = set()
    islands: list[list[str]] = []
    for ref in sorted(comps, key=_natural_key):
        if ref in seen or not signal[ref]:
            continue
        group: list[str] = []
        stack = [ref]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            group.append(current)
            stack.extend(sorted(signal[current] - seen, key=_natural_key))
        islands.append(sorted(group, key=_natural_key))
    loose = [ref for ref in comps if ref not in seen]
    return islands, loose


def _bfs_depth(start: str, adj: dict[str, set[str]]) -> dict[str, int]:
    depth = {start: 0}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for neighbour in sorted(adj.get(current, ()) , key=_natural_key):
            if neighbour not in depth:
                depth[neighbour] = depth[current] + 1
                queue.append(neighbour)
    return depth


def _order_layers(depth: dict[str, int], adj: dict[str, set[str]]) -> list[list[str]]:
    """Layers left-to-right, ordered by the barycentre of each part's neighbours.

    A part sits opposite the parts it connects to, so a relay lines up with the driver output
    that feeds it and with the terminal block on its contacts — the channel reads across the
    sheet instead of zig-zagging.
    """
    layers: dict[int, list[str]] = {}
    for ref, level in depth.items():
        layers.setdefault(level, []).append(ref)
    ordered = [sorted(layers[level], key=_natural_key) for level in sorted(layers)]

    def sweep(forward: bool) -> None:
        indices = [{ref: i for i, ref in enumerate(layer)} for layer in ordered]
        order = range(1, len(ordered)) if forward else range(len(ordered) - 2, -1, -1)
        for level in order:
            reference = indices[level - 1 if forward else level + 1]

            def barycentre(ref: str) -> tuple[float, tuple]:
                seen = [reference[other] for other in adj.get(ref, ()) if other in reference]
                middle = sum(seen) / len(seen) if seen else float("inf")
                return middle, _natural_key(ref)

            ordered[level] = sorted(ordered[level], key=barycentre)
            indices[level] = {ref: i for i, ref in enumerate(ordered[level])}

    sweep(True)
    sweep(False)
    return ordered


def _pin_sets(doc: Schematic, pin_net: dict[tuple[str, str], str]) -> dict[str, list[tuple[str, str | None]]]:
    """``{ref: [(pin, net)]}`` for every non-power symbol on the sheet."""
    out: dict[str, list[tuple[str, str | None]]] = {}
    for placement in doc.placements():
        if placement.reference.startswith("#"):
            continue
        symbol = doc.lookup(placement.lib_id)
        if symbol is None:
            continue
        out[placement.reference] = [
            (number, pin_net.get((placement.reference, number))) for number in symbol.pins
        ]
    return out


def _owners(doc: Schematic, pin_net: dict[tuple[str, str], str]) -> tuple[dict[str, str], set[str]]:
    """``{2-pin passive: owning anchor}`` plus the set of rail nets.

    A pull-up or a decoupling capacitor belongs beside the part it serves, not in a column of its
    own. The owner is the part the passive shares a signal with (preferring an IC to a
    connector); a passive that only touches rails goes with the smallest IC on those rails.
    """
    pin_sets = _pin_sets(doc, pin_net)
    nets = doc.nets()
    net_refs = {name: {m["ref"] for m in members if not m["ref"].startswith("#")}
                for name, members in nets.items()}
    rails = {name for name, refs in net_refs.items()
             if _is_rail(name, len(refs), len(pin_sets))}
    nets_of: dict[str, set[str]] = {ref: set() for ref in pin_sets}
    for name, refs in net_refs.items():
        for ref in refs:
            nets_of.setdefault(ref, set()).add(name)

    anchors = [ref for ref in pin_sets if len(pin_sets[ref]) != 2]
    owner_of: dict[str, str] = {}
    for passive, pins in pin_sets.items():
        if len(pins) != 2:
            continue
        own_nets = [name for _, name in pins if name]
        signal_nets = [name for name in own_nets if name not in rails]
        if signal_nets:
            candidates = [ref for ref in anchors
                          if any(name in nets_of.get(ref, ()) for name in signal_nets)]
        else:
            candidates = [ref for ref in anchors
                          if all(name in nets_of.get(ref, ()) for name in own_nets)]
            if not candidates:
                candidates = [ref for ref in anchors
                              if any(name in nets_of.get(ref, ()) for name in own_nets)]
        if not candidates:
            continue
        owner_of[passive] = min(
            candidates,
            key=lambda ref: ({"U": 0, "Q": 1, "K": 2, "S": 3, "D": 4, "L": 5, "J": 6}
                             .get(ref[:1], 7), len(pin_sets[ref]), _natural_key(ref)),
        )
    return owner_of, rails


def _pin_outward(doc: Schematic, ref: str, number: str) -> tuple[float, float]:
    """Sheet direction in which a pin leaves its symbol (where a wire should leave it)."""
    placement = doc._placement(ref)
    symbol = doc.lookup(placement.lib_id) if placement else None
    pin = symbol.pins.get(number) if symbol else None
    if pin is None or placement is None:
        return (1.0, 0.0)
    angle = math.radians(pin.angle)
    local_x, local_y = -math.cos(angle), math.sin(angle)
    theta = math.radians(placement.rotation)
    return (local_x * math.cos(theta) - local_y * math.sin(theta),
            local_x * math.sin(theta) + local_y * math.cos(theta))


def _route_between(a: tuple[float, float], a_out: tuple[float, float],
                   b: tuple[float, float], b_out: tuple[float, float],
                   step: float) -> list[tuple[float, float]]:
    """Route two pins that leave their symbols along ``a_out``/``b_out``.

    The bend happens in the free channel between the symbols, so a vertical leg cannot run along
    the target's other pins (which would join their nets).
    """
    a1 = (a[0] + a_out[0] * step, a[1] + a_out[1] * step)
    b1 = (b[0] + b_out[0] * step, b[1] + b_out[1] * step)
    points = [a, a1]
    if abs(a_out[0]) > 0.5 and abs(b_out[0]) > 0.5:
        mid = (a1[0] + b1[0]) / 2
        points += [(mid, a1[1]), (mid, b1[1])]
    elif abs(a_out[1]) > 0.5 and abs(b_out[1]) > 0.5:
        mid = (a1[1] + b1[1]) / 2
        points += [(a1[0], mid), (b1[0], mid)]
    else:
        points += [(b1[0], a1[1])]
    points += [b1, b]
    return points


def _partitions(doc: Schematic) -> set[frozenset[tuple[str, str]]]:
    return {frozenset((m["ref"], m["pin"]) for m in members)
            for members in doc.nets().values()}


def _lay_out(
    doc: Schematic,
    pin_net: dict[tuple[str, str], str],
    power: dict[tuple[str, str], str],
    label_w: float,
    *,
    scale: float,
    paper: str | None,
) -> dict[str, Any]:
    """One circuit-flow layout attempt at a given spacing scale. Leaves the document laid out."""
    # The netlist must be read *before* the drawing is cleared: connectivity comes from the old
    # wires and labels. Clearing first made every part look isolated and collapsed the sheet
    # into one 95-symbol column.
    comps, signal, rails = _graph(doc)
    islands, loose = _islands(comps, signal)
    islands.sort(key=len, reverse=True)
    main = islands[0] if islands else []
    hub = max(main, key=lambda ref: (len(signal[ref]), _natural_key(ref))) if main else None

    def layer_columns(group: list[str]) -> list[list[str]]:
        if not group:
            return []
        root = hub if group is main and hub else max(
            group, key=lambda ref: (len(signal[ref]), _natural_key(ref)))
        return _order_layers(_bfs_depth(root, signal), signal)

    # Clear the drawing but keep the symbols and their library geometry.
    for head in ("wire", "label", "global_label", "junction", "no_connect"):
        doc.tree[:] = [child for child in doc.tree
                       if not (isinstance(child, list) and child and child[0] == head)]

    placements = sorted(doc.placements(), key=lambda p: _natural_key(p.reference))
    boxes = symbol_boxes(doc)
    sizes = {ref: (box[2] - box[0], box[3] - box[1]) for ref, box in boxes.items()}

    horizontal = (STUB + label_w) * scale
    vertical = (STUB + TEXT_H / 2 + 0.6) * scale
    small = 1.6 * scale
    gap = GAP * scale
    extras: dict[str, dict[str, float]] = {}
    for placement in placements:
        symbol = doc.lookup(placement.lib_id)
        need = _pin_sides(doc, placement, symbol) if symbol else {}
        extras[placement.reference] = {
            "left": horizontal if need.get("left") else small,
            "right": horizontal if need.get("right") else small,
            "top": vertical if need.get("top") else small,
            "bottom": vertical if need.get("bottom") else small,
        }

    # ---- circuit flow: controller at the centre of the signal graph, then each stage outward
    supply_islands = [group for group in islands[1:]]
    columns: list[list[str]] = []
    # Power symbols only name nets; they are not parts on the signal path, so they go in a
    # compact legend strip under the drawing instead of a tall column that dominates the sheet.
    power_refs = sorted(
        (p.reference for p in placements if p.reference.startswith("#")),
        key=lambda ref: (next(p.value for p in placements if p.reference == ref),
                         _natural_key(ref)),
    )
    # Supply block, reading input to output: connectors and fuses first, then the regulator and
    # the parts around its switching node, then the bulk/decoupling capacitors. None of these
    # share a signal with the controller, so they cannot be placed by the flow itself.
    def is_input_end(ref: str) -> bool:
        return ref[:1] in ("J", "F", "B")  # connectors, fuses, bridges

    loose_input = [ref for ref in loose if is_input_end(ref)]
    loose_rest = [ref for ref in loose if not is_input_end(ref)]
    supply_columns: list[list[str]] = []
    if loose_input:
        supply_columns.append(loose_input)
    for group in sorted(supply_islands, key=lambda g: _natural_key(min(g, key=_natural_key))):
        supply_columns.extend(layer_columns(group))
    if loose_rest:
        supply_columns.append(loose_rest)
    columns.extend(supply_columns)
    if main:
        columns.extend(layer_columns(main))

    # Two-pin passives move into their owner's column, directly after it, so a pull-up sits by
    # the input it protects and a decoupling capacitor by the IC it decouples. Their labels still
    # carry the connection; the router can then wire them because they are adjacent.
    owner_of, _rails = _owners(doc, pin_net)
    passives = set(owner_of)
    if passives:
        for column in columns:
            column[:] = [ref for ref in column if ref not in passives]
        for column in columns:
            expanded: list[str] = []
            for ref in column:
                expanded.append(ref)
                expanded.extend(sorted((passive for passive, owner in owner_of.items()
                                        if owner == ref), key=_natural_key))
            column[:] = expanded
        placed_refs = {ref for column in columns for ref in column}
        for passive in sorted(passives - placed_refs, key=_natural_key):
            (columns[0] if columns else columns.append([])) and columns[0].append(passive)

    total_cols = len(columns)

    def slot_width(ref: str) -> float:
        return extras[ref]["left"] + sizes[ref][0] + extras[ref]["right"]

    def slot_height(ref: str) -> float:
        return extras[ref]["top"] + sizes[ref][1] + extras[ref]["bottom"]

    flow_width = sum(
        max(slot_width(ref) for ref in col) + gap for col in columns
    ) - gap if columns else 0.0
    flow_height = max(
        sum(slot_height(ref) + gap for ref in col) - gap for col in columns
    ) if columns else 0.0

    # Power-symbol legend: rows under the flow, as many per row as the flow is wide.
    per_row = max(1, min(12, int((flow_width + gap) // (min(
        (slot_width(ref) for ref in power_refs), default=10.0) + gap)) if flow_width else 8))
    power_rows: list[list[str]] = [
        power_refs[i:i + per_row] for i in range(0, len(power_refs), per_row)
    ]
    legend_width = max(
        (sum(slot_width(ref) + gap for ref in row) - gap for row in power_rows), default=0.0
    )
    legend_height = sum(
        (max(slot_height(ref) for ref in row) + gap if row else 0.0) for row in power_rows
    )

    width = max(flow_width, legend_width) + 2 * MARGIN
    height = flow_height + (gap + legend_height if power_rows else 0.0) + 2 * MARGIN

    # Prefer a standard sheet. When the drawing would leave a standard sheet mostly empty,
    # crop the page to the drawing instead: a custom "User" sheet the size of the content, with
    # a band at the foot reserved for KiCad's title block.
    chosen: str | None = None
    node = find_one(doc.tree, "paper")
    if paper:
        chosen = paper
        if node is not None and node[1] != chosen:
            node[:] = ["paper", chosen]
    else:
        # Crop the page to the drawing. A standard A1/A0 left three-quarters of the sheet
        # blank for this design; a custom sheet ends where the circuit ends.
        chosen = "User"
        if node is not None:
            node[:] = ["paper", "User", num(width), num(height + TITLE_BAND)]

    # Lay the columns out left to right, each stacked from the same top edge so the stages line
    # up row for row.
    x = MARGIN
    for column in columns:
        y = MARGIN
        col_width = max(extras[ref]["left"] + sizes[ref][0] + extras[ref]["right"]
                        for ref in column)
        for ref in column:
            box = boxes[ref]
            width_, height_ = sizes[ref]
            extra = extras[ref]
            target_x = x + extra["left"] + width_ / 2
            target_y = y + extra["top"] + height_ / 2
            placement = next(p for p in placements if p.reference == ref)
            doc.move_symbol(
                ref,
                placement.x + target_x - (box[0] + box[2]) / 2,
                placement.y + target_y - (box[1] + box[3]) / 2,
                rotation=0,
            )
            y += extra["top"] + height_ + extra["bottom"] + gap
        x += col_width + gap

    # Power-symbol legend under the flow.
    legend_y = MARGIN + flow_height + gap
    for row in power_rows:
        row_height = max(slot_height(ref) for ref in row)
        legend_x = MARGIN
        for ref in row:
            box = boxes[ref]
            width_, height_ = sizes[ref]
            extra = extras[ref]
            placement = next(p for p in placements if p.reference == ref)
            doc.move_symbol(
                ref,
                placement.x + (legend_x + extra["left"] + width_ / 2) - (box[0] + box[2]) / 2,
                placement.y + (legend_y + extra["top"] + height_ / 2) - (box[1] + box[3]) / 2,
                rotation=0,
            )
            legend_x += slot_width(ref) + gap
        legend_y += row_height + gap

    # Line the pins of point-to-point nets up before wires exist, so the router can draw
    # straight connections between stages (and between a passive and its owner).
    aligned = _align_symbols(doc, pin_net)

    # Re-read the placements: ``move_symbol`` changed the file, and pin positions must be
    # measured from where the symbols are *now*, not from the pre-move snapshot.
    placements = sorted(doc.placements(), key=lambda p: _natural_key(p.reference))
    placed_boxes = symbol_boxes(doc)
    page_size = _paper(doc)[1]
    placed_labels: list[tuple[float, float, float, float]] = []
    stubs = labels = 0
    for placement in placements:
        symbol = doc.lookup(placement.lib_id)
        if symbol is None:
            continue
        for number in symbol.pins:
            pin = doc.pin_position(placement, number)
            if pin is None:
                continue
            name = pin_net.get((placement.reference, number))
            if name is None:
                name = power.get((placement.reference, number))
            if name is None:
                doc.add_no_connect(*pin)
                continue
            # KiCad pin angles are measured Y-up while sheet Y grows down, so the outward
            # vector in sheet coordinates is (-cos(angle), +sin(angle)); it then rotates with
            # the placement exactly as ``pin_position`` rotates the pin itself.
            pin_angle = math.radians(symbol.pins[number].angle)
            local_x, local_y = -math.cos(pin_angle), math.sin(pin_angle)
            theta = math.radians(placement.rotation)
            ox = local_x * math.cos(theta) - local_y * math.sin(theta)
            oy = local_x * math.sin(theta) + local_y * math.cos(theta)

            # Try the natural direction first, then the other way, then longer stubs: a label is
            # placed where it covers nothing, which is what makes the sheet legible instead of
            # merely non-overlapping by luck.
            directions = [(ox, oy)]
            if abs(oy) > abs(ox):
                directions += [(1.0, 0.0), (-1.0, 0.0)]
            else:
                directions += [(0.0, 1.0), (0.0, -1.0)]
            candidate: tuple[tuple[float, float], float, str] | None = None
            for length_scale in (1.0, 1.6, 2.4):
                for dx, dy in directions:
                    for anchor_side in ("left", "right") if abs(dy) > abs(dx) else (
                        "left" if dx >= 0 else "right",
                    ):
                        end = (pin[0] + STUB * scale * length_scale * dx,
                               pin[1] + STUB * scale * length_scale * dy)
                        box = _box_at(name, end[0], end[1], anchor_side)
                        if _free(box, placed_labels, placed_boxes, page_size):
                            candidate = ((dx, dy), length_scale, anchor_side)
                            break
                    if candidate:
                        break
                if candidate:
                    break
            if candidate is None:
                dx, dy = directions[0]
                candidate = ((dx, dy), 1.0, "left" if dx >= 0 else "right")
            (dx, dy), length_scale, anchor_side = candidate
            end = (pin[0] + STUB * scale * length_scale * dx,
                   pin[1] + STUB * scale * length_scale * dy)
            doc.add_wire([pin, end])
            doc.add_label(name, *end, anchor=anchor_side)
            placed_labels.append(_box_at(name, end[0], end[1], anchor_side))
            stubs += 1
            labels += 1

    if not paper:
        boxes_after = symbol_boxes(doc)
        page_w = max((box[2] for box in boxes_after.values()), default=MARGIN) + MARGIN
        page_h = max((box[3] for box in boxes_after.values()), default=MARGIN) + MARGIN
        node_after = find_one(doc.tree, "paper")
        if node_after is not None:
            node_after[:] = ["paper", "User", num(page_w), num(page_h + TITLE_BAND)]

    return {
        "paper": chosen,
        "columns": total_cols,
        "stages": [len(col) for col in columns],
        "spacing_scale": scale,
        "sheet_mm": [round(_paper(doc)[1][0], 1), round(_paper(doc)[1][1], 1)],
        "symbols": len(placements),
        "stubs": stubs,
        "labels": labels,
        "aligned_pins": aligned,
    }

def findings(doc: Schematic) -> list[str]:
    """Actionable arrangement problems, in the agent's language.

    These are the things a placer cannot decide for itself: a power symbol that contradicts its
    net, a passive that belongs to a part it is nowhere near, a point-to-point net left as two
    labels at opposite ends of the sheet.
    """
    pin_net = _alias_nets(doc)
    owner_of, _rails = _owners(doc, pin_net)
    boxes = symbol_boxes(doc)
    out: list[str] = []

    # Power symbols whose value and net disagree. No placer can honour both.
    for placement in doc.placements():
        if not placement.reference.startswith("#"):
            continue
        symbol = doc.lookup(placement.lib_id)
        if symbol is None or not symbol.pins:
            continue
        number = next(iter(symbol.pins))
        net = doc.pin_net(placement, number) or placement.value
        if (placement.value != "PWR_FLAG" and net
                and net.strip().upper() != placement.value.strip().upper()):
            out.append(
                f"{placement.reference} is a {placement.value} symbol wired to net {net}: "
                "fix the symbol or the net before grounds can be placed at their part"
            )

    def centre(ref: str) -> tuple[float, float]:
        box = boxes[ref]
        return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)

    for passive, owner in sorted(owner_of.items(), key=lambda item: _natural_key(item[0])):
        if passive in boxes and owner in boxes:
            distance = math.dist(centre(passive), centre(owner))
            if distance > 30:
                out.append(f"{passive} is {distance:.0f} mm from {owner}, the part it serves "
                           "(pcb-placement#Priority Order calls for 3-5 mm)")

    net_pins: dict[str, list[tuple[str, str]]] = {}
    for (ref, number), name in pin_net.items():
        net_pins.setdefault(name, []).append((ref, number))
    for name, pins in sorted(net_pins.items()):
        if len(pins) != 2:
            continue
        (ref_a, pin_a), (ref_b, pin_b) = pins
        a = doc.pin_position(doc._placement(ref_a), pin_a)
        b = doc.pin_position(doc._placement(ref_b), pin_b)
        if a and b and math.dist(a, b) > 30:
            out.append(f"net {name} joins {ref_a}.{pin_a} and {ref_b}.{pin_b} "
                       f"{math.dist(a, b):.0f} mm apart; route_schematic or place them together")
    return out[:40]


def route(doc: Schematic, *, max_mm: float = 80.0) -> dict[str, Any]:
    """Draw direct wires for point-to-point nets, refusing any wire that changes connectivity.

    Labels stay where they are, so net names are preserved; the wire only makes the connection
    visible. Each candidate is added, the pin partition recomputed, and the wire removed again if
    two nets merged or a net split. The caller should still run ERC: the engine's partition model
    is necessary but not sufficient (KiCad's power-symbol rules are stricter).
    """
    pin_net = _alias_nets(doc)
    net_pins: dict[str, list[tuple[str, str]]] = {}
    for (ref, number), name in pin_net.items():
        net_pins.setdefault(name, []).append((ref, number))
    target = _partitions(doc)
    wired: list[str] = []
    skipped: list[dict[str, str]] = []
    for name, pins in sorted(net_pins.items()):
        if len(pins) != 2:
            continue
        (ref_a, pin_a), (ref_b, pin_b) = pins
        a = doc.pin_position(doc._placement(ref_a), pin_a)
        b = doc.pin_position(doc._placement(ref_b), pin_b)
        if a is None or b is None:
            continue
        if math.dist(a, b) > max_mm:
            skipped.append({"net": name, "reason": f"pins are {math.dist(a, b):.0f} mm apart"})
            continue
        points = _route_between(a, _pin_outward(doc, ref_a, pin_a),
                                b, _pin_outward(doc, ref_b, pin_b), STUB)
        added = doc.add_wire(points)
        if _partitions(doc) != target:
            doc.tree[:] = [child for child in doc.tree
                           if not any(child is node for node in added)]
            skipped.append({"net": name, "reason": "the wire would have joined another net"})
            continue
        wired.append(name)
    return {"wired": wired, "skipped": skipped,
            "partitions_ok": _partitions(doc) == target}

def _align_symbols(doc: Schematic, pin_net: dict[tuple[str, str], str], *,
                   gap: float = 4.0, passes: int = 3) -> int:
    """Place each right-hand part of a point-to-point net on the row of the pin it connects to.

    Targets must be recomputed on every pass: aligning the relays to their drivers moves the
    relays, so aligning the terminals in the same pass against the *old* relay rows is what made
    the terminal pitch 2.3x the relay pitch and left every channel skew-whiff.
    """
    net_pins: dict[str, list[tuple[str, str]]] = {}
    for (ref, number), name in pin_net.items():
        net_pins.setdefault(name, []).append((ref, number))

    aligned = 0
    for _pass in range(passes):
        wanted: dict[float, list[tuple[float, str, str, float]]] = {}
        for _name, pins in net_pins.items():
            if len(pins) != 2:
                continue
            (ref_a, pin_a), (ref_b, pin_b) = pins
            a = doc.pin_position(doc._placement(ref_a), pin_a)
            b = doc.pin_position(doc._placement(ref_b), pin_b)
            if a is None or b is None or abs(a[0] - b[0]) < 1.0:
                continue
            follower, follower_pin, target = (
                (ref_b, pin_b, a[1]) if b[0] > a[0] else (ref_a, pin_a, b[1]))
            follower_x = doc._placement(follower).x
            wanted.setdefault(round(follower_x, 1), []).append(
                (target, follower, follower_pin,
                 doc.pin_position(doc._placement(follower), follower_pin)[1])
            )
        moved_this_pass = 0
        for column_x in sorted(wanted):
            entries = sorted(wanted[column_x], key=lambda item: (item[0], _natural_key(item[1])))
            cursor: float | None = None
            for target_y, follower, follower_pin, current_pin_y in entries:
                placement = doc._placement(follower)
                if placement is None:
                    continue
                box = symbol_boxes(doc)[follower]
                desired = placement.y + (target_y - current_pin_y)
                if cursor is not None:
                    top = box[1] + (desired - placement.y)
                    if top < cursor:
                        desired += cursor - top
                if abs(desired - placement.y) < 0.05:
                    cursor = box[3] + gap
                    continue
                doc.move_symbol(follower, placement.x, desired)
                new_box = symbol_boxes(doc)[follower]
                cursor = new_box[3] + gap
                moved_this_pass += 1
                aligned += 1
        if not moved_this_pass:
            break
    return aligned

def metrics(doc: Schematic) -> dict[str, Any]:
    """Numbers behind the picture: ownership distance, channel alignment, wire length, sheet use."""
    pin_net = _alias_nets(doc)
    owner_of, _rails = _owners(doc, pin_net)
    boxes = symbol_boxes(doc)

    def centre(ref: str) -> tuple[float, float]:
        box = boxes[ref]
        return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)

    distances = [math.dist(centre(passive), centre(owner))
                 for passive, owner in owner_of.items()
                 if passive in boxes and owner in boxes]
    distances.sort()

    segments = _segments(doc)
    wire_length = sum(math.dist(a, b) for a, b in segments)

    net_pins: dict[str, list[tuple[str, str]]] = {}
    for (ref, number), name in pin_net.items():
        net_pins.setdefault(name, []).append((ref, number))
    pairs = 0
    aligned = 0
    for _name, pins in net_pins.items():
        if len(pins) != 2:
            continue
        (ref_a, pin_a), (ref_b, pin_b) = pins
        a = doc.pin_position(doc._placement(ref_a), pin_a)
        b = doc.pin_position(doc._placement(ref_b), pin_b)
        if a is None or b is None or abs(a[0] - b[0]) < 5:
            continue
        pairs += 1
        if abs(a[1] - b[1]) <= 1.0:
            aligned += 1

    page_w, page_h = _paper(doc)[1]
    xs = [box[0] for box in boxes.values()] + [box[2] for box in boxes.values()]
    ys = [box[1] for box in boxes.values()] + [box[3] for box in boxes.values()]
    content_area = (max(xs) - min(xs)) * (max(ys) - min(ys)) if xs else 0.0

    return {
        "owner_distance_median_mm": round(distances[len(distances) // 2], 1) if distances else None,
        "owner_distance_max_mm": round(distances[-1], 1) if distances else None,
        "pairs": pairs,
        "aligned_pairs": aligned,
        "alignment_ratio": round(aligned / pairs, 2) if pairs else None,
        "wire_length_mm": round(wire_length, 0),
        "wire_crossings": len([1 for i, (a, b) in enumerate(segments)
                               for c, d in segments[i + 1:] if _segments_cross(a, b, c, d)]),
        "sheet_utilisation": round(content_area / (page_w * page_h), 2) if page_w and page_h else None,
    }

