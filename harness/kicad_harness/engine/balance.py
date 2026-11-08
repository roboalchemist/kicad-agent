"""Copper balance: how the routed copper is distributed between the outer layers.

An unbalanced stack warps during reflow. This is an estimate from the pick index — track area
(length x width) plus filled zone polygons, without pad copper — which is enough to tell a
one-sided board from a balanced one.
"""

from __future__ import annotations

import math
from typing import Any


def _area(points: list[list[float]]) -> float:
    """Shoelace area of a polygon in mm^2."""
    if len(points) < 3:
        return 0.0
    total = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        total += float(x1) * float(y2) - float(x2) * float(y1)
    return abs(total) / 2


def report(index: dict[str, Any]) -> dict[str, Any]:
    area: dict[str, float] = {}
    for track in index.get("tracks", []):
        layer = str(track.get("layer") or "")
        length = math.dist((float(track.get("x1") or 0), float(track.get("y1") or 0)),
                           (float(track.get("x2") or 0), float(track.get("y2") or 0)))
        area[layer] = area.get(layer, 0.0) + length * float(track.get("width") or 0)
    for zone in index.get("zones", []):
        if zone.get("keepout"):
            continue
        polygons = zone.get("filled") or [zone.get("outline") or []]
        size = sum(_area(poly) for poly in polygons)
        for layer in zone.get("layers") or [zone.get("layer")]:
            if layer:
                area[str(layer)] = area.get(str(layer), 0.0) + size
    front = round(area.get("F.Cu", 0.0), 1)
    back = round(area.get("B.Cu", 0.0), 1)
    total = front + back
    ratio = round(front / total, 3) if total else None
    return {
        "front_mm2": front,
        "back_mm2": back,
        "ratio": ratio,
        "balanced": None if ratio is None else abs(ratio - 0.5) <= 0.2,
    }
