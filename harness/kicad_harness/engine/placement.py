"""Board placement metrics: how much of the sheet the parts actually use.

The schematic has legibility numbers; the board needs the same kind of measurement so "compact"
is not a matter of taste. This is deliberately about component footprints, not copper: copper
coverage is reported separately as copper balance.
"""

from __future__ import annotations

from typing import Any

from shapely.geometry import box
from shapely.ops import unary_union


def report(index: dict[str, Any]) -> dict[str, Any]:
    ex, ey, ew, eh = (float(v) for v in index["board"]["edge_bbox"])
    board = box(ex, ey, ex + ew, ey + eh)
    boxes = []
    for fp in index.get("footprints", []):
        b = fp.get("bbox")
        if not b or len(b) < 4:
            continue
        x, y, w, h = (float(v) for v in b[:4])
        if w > 0 and h > 0:
            boxes.append(box(x, y, x + w, y + h))
    if not boxes:
        return {"ok": False, "error": "no footprints", "board_area_mm2": round(board.area, 1)}
    union = unary_union(boxes)
    content = unary_union(boxes).bounds
    intervals = sorted((b.bounds[1], b.bounds[3]) for b in boxes)
    merged: list[list[float]] = []
    for low, high in intervals:
        if merged and low <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], high)
        else:
            merged.append([low, high])
    gaps: list[dict[str, float]] = []
    cursor = ey
    for low, high in merged:
        if low > cursor:
            gaps.append({"y0": round(cursor, 2), "y1": round(low, 2),
                         "height_mm": round(low - cursor, 2)})
        cursor = max(cursor, high)
    if cursor < ey + eh:
        gaps.append({"y0": round(cursor, 2), "y1": round(ey + eh, 2),
                     "height_mm": round(ey + eh - cursor, 2)})
    largest = max(gaps, key=lambda g: g["height_mm"], default={"height_mm": 0.0})
    return {
        "ok": True,
        "board_area_mm2": round(board.area, 1),
        "component_area_mm2": round(union.area, 1),
        "component_utilisation": round(union.area / board.area, 3) if board.area else None,
        "content_bbox": [round(v, 2) for v in content],
        "content_width_mm": round(content[2] - content[0], 2),
        "content_height_mm": round(content[3] - content[1], 2),
        "largest_horizontal_gap_mm": largest["height_mm"],
        "largest_gap_y": [largest.get("y0"), largest.get("y1")],
        "horizontal_gaps": gaps,
    }
