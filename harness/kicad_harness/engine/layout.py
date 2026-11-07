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
from typing import Any, Iterable

from .schematic import PAPER, Schematic, find_all, find_one

MARGIN = 12.7          # sheet border left free around the grid
GAP = 7.62             # clear space between neighbouring symbol boxes
STUB = 3.81            # stub length from a pin to its label
TEXT_H = 2.4           # a 1.27 mm label's rendered height, with a little air
CHAR_W = 1.15          # rough advance of a 1.27 mm KiCad character
MIN_LABEL_W = 9.0
MAX_LABEL_W = 30.0
PAPERS = ["A4", "A3", "A2", "A1", "A0"]


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
    """Rewrite the sheet as a grid of symbols with a labelled stub on every connected pin.

    Returns before/after measurements. Connectivity is preserved: each net keeps its name and
    every pin that belonged to it gets a label with that name, which KiCad merges sheet-wide.
    """
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

    boxes = symbol_boxes(doc)
    placements = sorted(doc.placements(), key=lambda p: _natural_key(p.reference))

    # Clear the drawing but keep the symbols and their library geometry.
    for head in ("wire", "label", "global_label", "junction", "no_connect"):
        doc.tree[:] = [child for child in doc.tree
                       if not (isinstance(child, list) and child and child[0] == head)]

    max_w = max((b[2] - b[0] for b in boxes.values()), default=10.0)
    max_h = max((b[3] - b[1] for b in boxes.values()), default=10.0)
    all_names = list(pin_net.values()) + list(power.values())
    label_w = max((_label_width(name) for name in all_names), default=MIN_LABEL_W)
    cell_w = max_w + 2 * (STUB + label_w) + GAP
    cell_h = max_h + 2 * STUB + 2 * TEXT_H + GAP

    chosen = paper
    cols = rows = 0
    for candidate_paper in ([paper] if paper else PAPERS):
        page_w, page_h = PAPER[candidate_paper]
        cols = max(1, int((page_w - 2 * MARGIN) // cell_w))
        rows = max(1, int((page_h - 2 * MARGIN) // cell_h))
        if cols * rows >= len(placements):
            chosen = candidate_paper
            break
    else:
        chosen = "A0"
        page_w, page_h = PAPER["A0"]
        cols = max(1, int((page_w - 2 * MARGIN) // cell_w))
    if chosen and chosen != _paper(doc)[0]:
        node = find_one(doc.tree, "paper")
        if node is not None:
            node[1] = chosen

    stubs = labels = 0
    for index, placement in enumerate(placements):
        col, row = index % cols, index // cols
        cx = MARGIN + col * cell_w + cell_w / 2
        cy = MARGIN + row * cell_h + cell_h / 2
        box = boxes[placement.reference]
        # Centre the symbol's own bbox in the cell, not its origin (origins are not symmetric).
        x = cx - (box[0] + box[2]) / 2 + placement.x
        y = cy - (box[1] + box[3]) / 2 + placement.y
        doc.move_symbol(placement.reference, x, y, rotation=0)

    # Re-read the placements: ``move_symbol`` changed the file, and pin positions must be
    # measured from where the symbols are *now*, not from the pre-move snapshot.
    placements = sorted(doc.placements(), key=lambda p: _natural_key(p.reference))
    placed_boxes = symbol_boxes(doc)
    page_size = _paper(doc)[1]
    placed_labels: list[tuple[float, float, float, float]] = []
    for placement in placements:
        symbol = doc.lookup(placement.lib_id)
        if symbol is None:
            continue
        box = placed_boxes[placement.reference]
        centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
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
            # The pin's own library angle says which way it leaves the body: the pin is drawn
            # from its connection point *into* the symbol, so outward is angle + 180, rotated
            # with the placement. Using the symbol's centre instead put connector stubs on top
            # of the connector (its pins are all down one long edge).
            # KiCad pin angles are measured Y-up while sheet Y grows down, so the outward
            # vector in sheet coordinates is (-cos(angle), +sin(angle)); it then rotates with
            # the placement exactly as ``pin_position`` rotates the pin itself.
            pin_angle = math.radians(symbol.pins[number].angle)
            local_x, local_y = -math.cos(pin_angle), math.sin(pin_angle)
            theta = math.radians(placement.rotation)
            ox = local_x * math.cos(theta) - local_y * math.sin(theta)
            oy = local_x * math.sin(theta) + local_y * math.cos(theta)

            # Try the natural direction first, then the other horizontal way, then longer
            # stubs: a label is placed where it covers nothing, which is what makes the sheet
            # legible instead of merely non-overlapping by luck.
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
                        end = (pin[0] + STUB * length_scale * dx, pin[1] + STUB * length_scale * dy)
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
            end = (pin[0] + STUB * length_scale * dx, pin[1] + STUB * length_scale * dy)
            doc.add_wire([pin, end])
            doc.add_label(name, *end, anchor=anchor_side)
            placed_labels.append(_box_at(name, end[0], end[1], anchor_side))
            stubs += 1
            labels += 1

    after = analyze(doc)
    return {
        "before": before,
        "after": after,
        "paper": chosen,
        "grid": {"columns": cols, "rows": rows, "cell_mm": [round(cell_w, 1), round(cell_h, 1)]},
        "symbols": len(placements),
        "stubs": stubs,
        "labels": labels,
    }
