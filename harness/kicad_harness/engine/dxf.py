"""Minimal ASCII DXF outline import (LWPOLYLINE, or LINE segments forming a loop).

Mechanical drawings usually arrive as DXF. Full DXF support is a project of its own; this reads
the one thing a board outline needs: a closed polyline in millimetres.
"""

from __future__ import annotations

import re


def _pairs(text: str) -> list[tuple[str, str]]:
    lines = text.splitlines()
    out: list[tuple[str, str]] = []
    for i in range(0, len(lines) - 1, 2):
        out.append((lines[i].strip(), lines[i + 1].strip()))
    return out


def polygon(text: str) -> list[tuple[float, float]]:
    """Vertices of the first LWPOLYLINE, else the endpoints of LINE entities (in file order)."""
    pairs = _pairs(text)
    polylines: list[list[tuple[float, float]]] = []
    lines: list[tuple[tuple[float, float], tuple[float, float]]] = []
    entity = ""
    xs: list[float] = []
    ys: list[float] = []
    start = end = None

    def flush() -> None:
        nonlocal xs, ys
        if entity == "LWPOLYLINE" and len(xs) == len(ys) and len(xs) >= 3:
            polylines.append(list(zip(xs, ys)))
        xs, ys = [], []

    for code, value in pairs:
        if code == "0":
            flush()
            entity = value.upper()
            start = end = None
            continue
        if entity == "LWPOLYLINE":
            if code == "10":
                xs.append(float(value))
            elif code == "20":
                ys.append(float(value))
        elif entity == "LINE":
            if code == "10":
                start = (float(value), start[1] if start else 0.0)
            elif code == "20":
                start = (start[0] if start else 0.0, float(value))
            elif code == "11":
                end = (float(value), end[1] if end else 0.0)
            elif code == "21":
                end = (end[0] if end else 0.0, float(value))
                if start and end:
                    lines.append((start, end))
                    start = end = None
    flush()
    if polylines:
        return polylines[0]
    if lines:
        points = [lines[0][0]]
        for _a, b in lines:
            points.append(b)
        return points
    return []


def units_mm(text: str) -> float:
    """DXF header unit factor to millimetres (``$INSUNITS`` 4 = mm, 1 = inches)."""
    match = re.search(r"\$INSUNITS\s*\n\s*(\d+)", text)
    if not match:
        return 1.0
    return {1: 25.4, 4: 1.0, 5: 10.0, 6: 1000.0}.get(int(match.group(1)), 1.0)
