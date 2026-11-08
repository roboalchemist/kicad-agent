"""Board-level visual QA that the pick index can answer exactly.

Silkscreen and courtyard checks are geometry, not OCR or pixel diffs: text boxes, pad polygons and
courtyard outlines come from the board itself, so a finding names the offending reference and the
rule. ``diff_indexes`` turns two revisions into added/removed/moved items for a visual overlay.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

from shapely.geometry import Polygon, box

from . import geometry

SILK_MIN_HEIGHT = 0.89       # pcb-silkscreen#Text Sizing: 0.035"
SILK_CLEARANCE = 0.15        # pcb-silkscreen#Clearance Rules: 6 mil to pads/vias
SILK_EDGE = 2.54             # pcb-silkscreen#Clearance Rules: 0.1" for important text
SILK_RULE = "pcb-silkscreen#Placement Rules"
COURTYARD_RULE = "pcb-placement#Spacing"


def _rect(raw: Iterable[float] | None) -> Polygon | None:
    if not raw or len(raw) < 4:
        return None
    x, y, w, h = (float(v) for v in raw[:4])
    return box(x, y, x + w, y + h)


def _in_region(shape, region: Iterable[float] | None) -> bool:
    if not region:
        return True
    x1, y1, x2, y2 = (float(v) for v in region)
    return shape.intersects(box(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))


def silkscreen(index: dict[str, Any], region: Iterable[float] | None = None) -> dict[str, Any]:
    """Reference text over pads, text over text, undersized text and text in the edge band."""
    edge = index.get("board", {}).get("edge_bbox") or (0, 0, 0, 0)
    edge_box = box(edge[0], edge[1], edge[0] + edge[2], edge[1] + edge[3])
    inner = edge_box.buffer(-SILK_EDGE)
    texts: list[dict[str, Any]] = []
    for fp in index.get("footprints", []):
        raw = fp.get("ref_text") or {}
        shape = _rect(raw.get("bbox"))
        layer = raw.get("layer", "")
        if not raw.get("visible") or shape is None or not (layer.endswith("SilkS") or "Silkscreen" in layer):
            continue
        if not _in_region(shape, region):
            continue
        texts.append({"ref": fp.get("ref"), "layer": raw["layer"], "box": shape,
                      "height": float(raw.get("height") or 0.0)})

    pads = [p for p in index.get("pads", []) if p.get("net")]
    vias = [v for v in index.get("vias", []) if v.get("net")]
    collisions: list[dict[str, Any]] = []
    undersized: list[dict[str, Any]] = []
    edge_texts: list[dict[str, Any]] = []
    for text in texts:
        side = "F" if text["layer"].startswith("F.") else "B"
        copper_layer = f"{side}.Cu"
        expanded = text["box"].buffer(SILK_CLEARANCE)
        for pad in pads:
            if copper_layer not in (pad.get("layers") or []):
                continue
            try:
                pad_shape = geometry.entity_shape(dict(pad, kind="pad"))
            except Exception:
                continue
            if expanded.intersects(pad_shape) and _in_region(text["box"], region):
                collisions.append({
                    "type": "text_over_pad",
                    "ref": text["ref"],
                    "pad": f"{pad.get('ref')}.{pad.get('pad')}",
                    "net": pad.get("net"),
                    "detail": f"{text['ref']} reference text is within {SILK_CLEARANCE} mm of pad "
                              f"{pad.get('ref')}.{pad.get('pad')}",
                })
        for via in vias:
            dx, dy = float(via["x"]) - text["box"].centroid.x, float(via["y"]) - text["box"].centroid.y
            if math.hypot(dx, dy) <= float(via.get("size") or 0) / 2 + SILK_CLEARANCE:
                collisions.append({
                    "type": "text_over_via",
                    "ref": text["ref"],
                    "via": via.get("uuid"),
                    "detail": f"{text['ref']} reference text is near a via",
                })
        if text["height"] and text["height"] + 1e-6 < SILK_MIN_HEIGHT:
            undersized.append({"ref": text["ref"], "height_mm": text["height"],
                               "min_mm": SILK_MIN_HEIGHT})
        if not text["box"].within(inner) and _in_region(text["box"], region):
            edge_texts.append({"ref": text["ref"],
                               "detail": f"{text['ref']} is within {SILK_EDGE} mm of the board edge"})

    for i, left in enumerate(texts):
        for right in texts[i + 1:]:
            if left["layer"] != right["layer"]:
                continue
            if left["box"].buffer(SILK_CLEARANCE).intersects(right["box"]):
                collisions.append({
                    "type": "text_over_text",
                    "ref": left["ref"],
                    "other": right["ref"],
                    "detail": f"{left['ref']} and {right['ref']} reference text overlap",
                })
    return {
        "passed": not (collisions or undersized or edge_texts),
        "checked": len(texts),
        "collisions": collisions[:50],
        "undersized": undersized[:50],
        "edge": edge_texts[:50],
        "rule": SILK_RULE,
        "limits": {"min_height_mm": SILK_MIN_HEIGHT, "clearance_mm": SILK_CLEARANCE,
                   "edge_mm": SILK_EDGE},
    }


def courtyards(index: dict[str, Any], region: Iterable[float] | None = None) -> dict[str, Any]:
    """Overlapping footprint courtyards (assembly/rework clearance), and footprints without one."""
    shapes: dict[str, Polygon] = {}
    missing: list[str] = []
    for fp in index.get("footprints", []):
        points = fp.get("courtyard")
        if not points or len(points) < 3:
            missing.append(str(fp.get("ref")))
            continue
        poly = Polygon([(float(p[0]), float(p[1])) for p in points])
        if not poly.is_valid or poly.area <= 0:
            missing.append(str(fp.get("ref")))
            continue
        if _in_region(poly, region):
            shapes[str(fp.get("ref"))] = poly
    overlaps: list[dict[str, Any]] = []
    refs = sorted(shapes)
    for i, left in enumerate(refs):
        for right in refs[i + 1:]:
            area = shapes[left].intersection(shapes[right]).area
            if area > 0.01:
                overlaps.append({"a": left, "b": right, "area_mm2": round(area, 3),
                                 "detail": f"{left} and {right} courtyards overlap by {area:.2f} mm2"})
    return {"passed": not overlaps, "checked": len(shapes), "overlaps": overlaps[:50],
            "missing": missing[:50], "rule": COURTYARD_RULE}


def _signature(kind: str, entry: dict[str, Any]) -> tuple:
    if kind == "footprints":
        return (entry.get("ref"), round(float(entry.get("x") or 0), 3),
                round(float(entry.get("y") or 0), 3), round(float(entry.get("rotation") or 0), 2),
                entry.get("side"))
    if kind == "pads":
        return (entry.get("ref"), entry.get("pad"), entry.get("net"),
                round(float(entry.get("x") or 0), 3), round(float(entry.get("y") or 0), 3))
    if kind == "tracks":
        return (entry.get("net"), entry.get("layer"),
                round(float(entry.get("x1") or 0), 3), round(float(entry.get("y1") or 0), 3),
                round(float(entry.get("x2") or 0), 3), round(float(entry.get("y2") or 0), 3),
                round(float(entry.get("width") or 0), 3))
    if kind == "vias":
        return (entry.get("net"), round(float(entry.get("x") or 0), 3),
                round(float(entry.get("y") or 0), 3), entry.get("type") or "through")
    return (entry.get("net"), entry.get("layer"), len(entry.get("outline") or []))


def _centre(kind: str, entry: dict[str, Any]) -> tuple[float, float] | None:
    if kind == "footprints":
        bx, by, bw, bh = entry.get("bbox") or (0, 0, 0, 0)
        return bx + bw / 2, by + bh / 2
    if kind in ("pads", "vias"):
        return float(entry.get("x") or 0), float(entry.get("y") or 0)
    if kind == "tracks":
        return ((float(entry.get("x1") or 0) + float(entry.get("x2") or 0)) / 2,
                (float(entry.get("y1") or 0) + float(entry.get("y2") or 0)) / 2)
    if kind == "zones":
        points = entry.get("outline") or []
        if points:
            return (sum(float(p[0]) for p in points) / len(points),
                    sum(float(p[1]) for p in points) / len(points))
    return None


def _bounds(kind: str, entry: dict[str, Any]) -> list[float] | None:
    if kind == "footprints":
        bx, by, bw, bh = entry.get("bbox") or (0, 0, 0, 0)
        return [bx, by, bx + bw, by + bh]
    if kind in ("pads", "vias"):
        x, y = float(entry.get("x") or 0), float(entry.get("y") or 0)
        size = max(float(entry.get("w") or entry.get("size") or 0.5),
                   float(entry.get("h") or entry.get("size") or 0.5)) / 2
        return [x - size, y - size, x + size, y + size]
    if kind == "tracks":
        xs = [float(entry.get("x1") or 0), float(entry.get("x2") or 0)]
        ys = [float(entry.get("y1") or 0), float(entry.get("y2") or 0)]
        return [min(xs), min(ys), max(xs), max(ys)]
    points = entry.get("outline") or []
    if points:
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        return [min(xs), min(ys), max(xs), max(ys)]
    return None


def _inside(centre: tuple[float, float] | None, region: Iterable[float] | None) -> bool:
    if not region or centre is None:
        return True
    x1, y1, x2, y2 = (float(v) for v in region)
    return min(x1, x2) <= centre[0] <= max(x1, x2) and min(y1, y2) <= centre[1] <= max(y1, y2)


def diff_indexes(old: dict[str, Any], new: dict[str, Any],
                 region: Iterable[float] | None = None) -> dict[str, Any]:
    """Added, removed and changed board items between two pick indexes, filtered by region."""
    result: dict[str, Any] = {"added": [], "removed": [], "changed": []}
    for key in ("footprints", "pads", "tracks", "vias", "zones"):
        before = {e.get("uuid"): e for e in old.get(key, []) if e.get("uuid")}
        after = {e.get("uuid"): e for e in new.get(key, []) if e.get("uuid")}
        for uuid in after.keys() - before.keys():
            entry = after[uuid]
            if _inside(_centre(key, entry), region):
                result["added"].append({"kind": key[:-1], "uuid": uuid,
                                        "ref": entry.get("ref"), "net": entry.get("net"),
                                        "bbox": _bounds(key, entry)})
        for uuid in before.keys() - after.keys():
            entry = before[uuid]
            if _inside(_centre(key, entry), region):
                result["removed"].append({"kind": key[:-1], "uuid": uuid,
                                          "ref": entry.get("ref"), "net": entry.get("net"),
                                          "bbox": _bounds(key, entry)})
        for uuid in before.keys() & after.keys():
            if _signature(key, before[uuid]) != _signature(key, after[uuid]):
                if _inside(_centre(key, after[uuid]), region):
                    result["changed"].append({"kind": key[:-1], "uuid": uuid,
                                              "ref": after[uuid].get("ref"),
                                              "net": after[uuid].get("net"),
                                              "bbox": _bounds(key, after[uuid]),
                                              "before": _signature(key, before[uuid]),
                                              "after": _signature(key, after[uuid])})
    result["summary"] = {name: len(result[name]) for name in ("added", "removed", "changed")}
    for name in ("added", "removed", "changed"):
        result[name] = result[name][:50]
    return result
