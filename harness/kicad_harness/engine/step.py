"""Minimal STEP (ISO-10303-21) outline reader.

A STEP file is a text database of entities. For a board outline we need only the wireframe:
``EDGE_CURVE`` records join two ``VERTEX_POINT``s, which point at ``CARTESIAN_POINT``s. This module
parses those, samples circular edges, and chains the edges into a closed polygon. It is not a CAD
kernel: exact B-splines are approximated by their endpoints, and the longest closed loop is taken
as the outline (inner cutouts are ignored).
"""

from __future__ import annotations

import math
import re

_ENTITY = re.compile(r"#(\d+)\s*=\s*([A-Z0-9_]+)\s*\((.*)\)\s*$", re.DOTALL)
_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


class Ref(int):
    """A ``#123`` entity reference."""


def _split_statements(text: str) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    quoted = False
    for char in text:
        if char == "'":
            quoted = not quoted
            buf.append(char)
        elif char == ";" and not quoted:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(char)
    if "".join(buf).strip():
        out.append("".join(buf))
    return out


def _split_args(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    quoted = False
    buf: list[str] = []
    for char in text:
        if char == "'":
            quoted = not quoted
            buf.append(char)
        elif quoted:
            buf.append(char)
        elif char == "(":
            depth += 1
            buf.append(char)
        elif char == ")":
            depth -= 1
            buf.append(char)
        elif char == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(char)
    parts.append("".join(buf).strip())
    return parts


def _value(token: str):
    token = token.strip()
    if not token:
        return None
    if token.startswith("#"):
        return Ref(token[1:])
    if token.startswith("'") and token.endswith("'"):
        return token[1:-1].replace("''", "'")
    if token.startswith("(") and token.endswith(")"):
        return tuple(_value(part) for part in _split_args(token[1:-1]))
    upper = token.upper()
    if upper in (".T.", ".TRUE."):
        return True
    if upper in (".F.", ".FALSE."):
        return False
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        return token


def entities(text: str) -> dict[int, tuple[str, tuple]]:
    """``{#id: (TYPE, args)}`` for every entity in the DATA section."""
    text = _COMMENT.sub("", text)
    out: dict[int, tuple[str, tuple]] = {}
    for statement in _split_statements(text):
        match = _ENTITY.search(statement.strip())
        if not match:
            continue
        number, kind, body = int(match.group(1)), match.group(2).upper(), match.group(3)
        out[number] = (kind, tuple(_value(part) for part in _split_args(body)))
    return out


def _point(table: dict[int, tuple[str, tuple]], ref) -> tuple[float, float] | None:
    entity = table.get(int(ref)) if isinstance(ref, int) else None
    if not entity or entity[0] != "CARTESIAN_POINT":
        return None
    coords = entity[1][1]
    if not isinstance(coords, tuple) or len(coords) < 2:
        return None
    return float(coords[0]), float(coords[1])


def _vertex_point(table: dict[int, tuple[str, tuple]], ref) -> tuple[float, float] | None:
    entity = table.get(int(ref)) if isinstance(ref, int) else None
    if not entity or entity[0] != "VERTEX_POINT":
        return None
    return _point(table, entity[1][1])


def _circle(table: dict[int, tuple[str, tuple]], ref) -> tuple[tuple[float, float], float] | None:
    entity = table.get(int(ref)) if isinstance(ref, int) else None
    if not entity or entity[0] != "CIRCLE":
        return None
    placement, radius = entity[1][0], entity[1][1]
    axis = table.get(int(placement)) if isinstance(placement, int) else None
    if not axis or axis[0] != "AXIS2_PLACEMENT_3D":
        return None
    centre = _point(table, axis[1][1])
    if centre is None:
        return None
    return centre, float(radius)


def _arc(centre: tuple[float, float], radius: float, start: tuple[float, float],
         end: tuple[float, float]) -> list[tuple[float, float]]:
    """Sample the minor arc between two endpoints of a circle."""
    a0 = math.atan2(start[1] - centre[1], start[0] - centre[0])
    a1 = math.atan2(end[1] - centre[1], end[0] - centre[0])
    delta = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
    steps = max(2, int(math.ceil(abs(delta) / (math.pi / 18))))
    return [(centre[0] + radius * math.cos(a0 + delta * i / steps),
             centre[1] + radius * math.sin(a0 + delta * i / steps)) for i in range(steps + 1)]


def _edges(table: dict[int, tuple[str, tuple]]) -> list[list[tuple[float, float]]]:
    out: list[list[tuple[float, float]]] = []
    for kind, args in table.values():
        if kind != "EDGE_CURVE" or len(args) < 3:
            continue
        start = _vertex_point(table, args[1])
        end = _vertex_point(table, args[2])
        if start is None or end is None:
            continue
        circle = _circle(table, args[3])
        if circle and circle[1] > 0:
            out.append(_arc(circle[0], circle[1], start, end))
        else:
            out.append([start, end])
    return out


def _close(a: tuple[float, float], b: tuple[float, float], tol: float = 1e-6) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def _chain(edges: list[list[tuple[float, float]]], first: int) -> tuple[list[tuple[float, float]], bool]:
    points = list(edges[first])
    remaining = set(range(len(edges))) - {first}
    while remaining:
        current = points[-1]
        for index in sorted(remaining):
            candidate = edges[index]
            if _close(candidate[0], current):
                points.extend(candidate[1:])
            elif _close(candidate[-1], current):
                points.extend(list(reversed(candidate))[1:])
            else:
                continue
            remaining.discard(index)
            break
        else:
            return points, False
        if _close(points[-1], points[0]):
            return points, True
    return points, _close(points[-1], points[0])


def polygon(text: str) -> list[list[float]]:
    """The longest closed loop of the file, as ``[[x, y], ...]`` in millimetres."""
    table = entities(text)
    edges = _edges(table)
    if not edges:
        return []
    best: list[tuple[float, float]] = []
    for first in range(len(edges)):
        points, closed = _chain(edges, first)
        if closed and len(points) > len(best):
            best = points
    if not best:
        return []
    if _close(best[0], best[-1]):
        best = best[:-1]
    return [[round(x, 6), round(y, 6)] for x, y in best]
